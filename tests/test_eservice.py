# -*- coding: utf-8 -*-
import json
import uuid

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


def _build_taxi_style_workflow(env):
    """trigger_form -> assign_employee -> human_approval
                                              |- approved -> action_create_record (stand-in for send_payment_link,
                                                              so tests don't depend on the 'payment' module being
                                                              configured)
                                              |- rejected -> (dead end)
                                              |- more_info -> wait_for_resubmission -> human_approval (loop back)
    """
    workflow = env['baramej.flow.workflow'].create({'name': 'Taxi-Style Test Workflow', 'trigger_type': 'form'})
    trigger = env['baramej.flow.node'].create({
        'workflow_id': workflow.id, 'name': 'Form Trigger', 'node_type': 'trigger_form', 'sequence': 10,
    })
    employee = env['res.users'].create({
        'name': 'Test Reviewer', 'login': 'test_reviewer_%s' % uuid.uuid4().hex[:8],
    })
    assign = env['baramej.flow.node'].create({
        'workflow_id': workflow.id, 'name': 'Assign', 'node_type': 'assign_employee', 'sequence': 20,
        'config_json': json.dumps({'user_id': employee.id}),
    })
    approval = env['baramej.flow.node'].create({
        'workflow_id': workflow.id, 'name': 'Approval', 'node_type': 'human_approval', 'sequence': 30,
    })
    approved_action = env['baramej.flow.node'].create({
        'workflow_id': workflow.id, 'name': 'On Approved', 'node_type': 'action_create_record', 'sequence': 40,
        'config_json': json.dumps({'model': 'res.partner', 'values': {'name': 'Approved Side Effect'}}),
    })
    wait_node = env['baramej.flow.node'].create({
        'workflow_id': workflow.id, 'name': 'Wait For Info', 'node_type': 'wait_for_resubmission', 'sequence': 50,
    })

    env['baramej.flow.edge'].create({
        'workflow_id': workflow.id, 'source_node_id': trigger.id, 'target_node_id': assign.id,
    })
    env['baramej.flow.edge'].create({
        'workflow_id': workflow.id, 'source_node_id': assign.id, 'target_node_id': approval.id,
    })
    env['baramej.flow.edge'].create({
        'workflow_id': workflow.id, 'source_node_id': approval.id, 'target_node_id': approved_action.id,
        'condition_branch': 'approved',
    })
    env['baramej.flow.edge'].create({
        'workflow_id': workflow.id, 'source_node_id': approval.id, 'target_node_id': wait_node.id,
        'condition_branch': 'more_info',
    })
    # 'rejected' branch deliberately has no outgoing edge - dead end
    env['baramej.flow.edge'].create({
        'workflow_id': workflow.id, 'source_node_id': wait_node.id, 'target_node_id': approval.id,
    })

    workflow.action_publish()
    return workflow, employee


@tagged('post_install', '-at_install')
class TestEServicePauseResume(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow, cls.employee = _build_taxi_style_workflow(cls.env)
        cls.partner = cls.env['res.partner'].create({'name': 'Test Applicant'})
        cls.form = cls.env['baramej.flow.form'].create({
            'name': 'Taxi Test Form', 'workflow_id': cls.workflow.id, 'require_login': True,
        })
        cls.env['baramej.flow.form.field'].create({
            'form_id': cls.form.id, 'name': 'vehicle_plate', 'label': 'Vehicle Plate', 'field_type': 'char',
        })

    def _create_ticket_and_run(self):
        ticket = self.env['baramej.flow.ticket'].create({
            'form_id': self.form.id,
            'partner_id': self.partner.id,
            'submitted_data_json': json.dumps({'vehicle_plate': 'AB-1234'}),
            'state': 'submitted',
        })
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(
            self.workflow, trigger_source='form',
            initial_context={'form': {'vehicle_plate': 'AB-1234'}, 'ticket_id': ticket.id},
        )
        ticket.execution_id = execution.id
        return ticket

    def test_workflow_pauses_at_human_approval_with_ticket_in_review(self):
        ticket = self._create_ticket_and_run()
        self.assertEqual(ticket.execution_id.state, 'waiting')
        self.assertEqual(ticket.state, 'in_review')
        self.assertEqual(ticket.user_id, self.employee)

    def test_approve_resumes_and_runs_downstream_action(self):
        ticket = self._create_ticket_and_run()
        ticket.action_approve()

        self.assertEqual(ticket.execution_id.state, 'success')
        self.assertEqual(ticket.state, 'approved')
        self.assertTrue(self.env['res.partner'].search([('name', '=', 'Approved Side Effect')]))

    def test_reject_resumes_to_dead_end_and_records_reason(self):
        ticket = self._create_ticket_and_run()
        ticket.action_reject('Vehicle not eligible for commercial use')

        self.assertEqual(ticket.execution_id.state, 'success')
        self.assertEqual(ticket.state, 'rejected')
        self.assertEqual(ticket.rejection_reason, 'Vehicle not eligible for commercial use')
        # The 'approved' side effect must NOT have run down the rejected branch
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'Approved Side Effect')]))

    def test_reject_without_reason_raises(self):
        ticket = self._create_ticket_and_run()
        with self.assertRaises(UserError):
            ticket.action_reject('')

    def test_request_info_pauses_at_wait_node_then_resubmission_loops_back(self):
        ticket = self._create_ticket_and_run()
        ticket.action_request_info('Please provide proof of non-conviction certificate')

        self.assertEqual(ticket.state, 'waiting_info')
        self.assertEqual(ticket.execution_id.state, 'waiting')  # now paused at wait_for_resubmission

        ticket.action_portal_resubmit({'vehicle_plate': 'AB-1234', 'non_conviction_certificate': 'attached'})

        # Resubmission should route back to human_approval, which pauses again -
        # proving the loop-back wiring works, not just a straight-line resume.
        self.assertEqual(ticket.execution_id.state, 'waiting')
        self.assertEqual(ticket.state, 'in_review')

        submitted = json.loads(ticket.submitted_data_json)
        self.assertEqual(submitted['non_conviction_certificate'], 'attached')
        self.assertEqual(submitted['vehicle_plate'], 'AB-1234')  # original data preserved

    def test_resuming_ticket_with_no_paused_execution_raises(self):
        ticket = self._create_ticket_and_run()
        ticket.action_approve()  # execution now 'success', nothing left to resume
        with self.assertRaises(UserError):
            ticket.action_approve()


@tagged('post_install', '-at_install')
class TestAssignEmployeeNode(TransactionCase):

    def test_missing_user_id_fails_clearly(self):
        workflow = self.env['baramej.flow.workflow'].create({'name': 'Assign Test', 'trigger_type': 'manual'})
        trigger = self.env['baramej.flow.node'].create({
            'workflow_id': workflow.id, 'name': 'Start', 'node_type': 'trigger_manual', 'sequence': 10,
        })
        node = self.env['baramej.flow.node'].create({
            'workflow_id': workflow.id, 'name': 'Assign', 'node_type': 'assign_employee', 'sequence': 20,
            'config_json': '{}',
        })
        self.env['baramej.flow.edge'].create({
            'workflow_id': workflow.id, 'source_node_id': trigger.id, 'target_node_id': node.id,
        })
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(workflow)
        self.assertEqual(execution.state, 'failed')
        self.assertIn('user_id', execution.error_message)


@tagged('post_install', '-at_install')
class TestFormRequireLogin(TransactionCase):

    def test_require_login_defaults_true(self):
        workflow = self.env['baramej.flow.workflow'].create({'name': 'Login Test WF', 'trigger_type': 'form'})
        form = self.env['baramej.flow.form'].create({'name': 'Login Test Form', 'workflow_id': workflow.id})
        self.assertTrue(form.require_login)
