# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkflowEngine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Test Workflow',
            'trigger_type': 'manual',
        })
        cls.trigger_node = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id,
            'name': 'Start',
            'node_type': 'trigger_manual',
            'sequence': 10,
        })
        cls.create_node = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id,
            'name': 'Create Partner',
            'node_type': 'action_create_record',
            'sequence': 20,
            'config_json': '{"model": "res.partner", "values": {"name": "Engine Test Contact"}, '
                            '"output_key": "partner_id"}',
        })
        cls.env['baramej.flow.edge'].create({
            'workflow_id': cls.workflow.id,
            'source_node_id': cls.trigger_node.id,
            'target_node_id': cls.create_node.id,
        })

    def test_engine_runs_trigger_then_create_record(self):
        """A two-node workflow (trigger -> create record) should complete
        successfully and produce exactly two log entries."""
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine

        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, trigger_source='unit_test')

        self.assertEqual(execution.state, 'success')
        self.assertEqual(len(execution.log_ids), 2)

        partner = self.env['res.partner'].search([('name', '=', 'Engine Test Contact')])
        self.assertTrue(partner, 'The Create Record node should have created a res.partner.')

    def test_condition_node_routes_true_branch(self):
        """trigger -> condition -> create (on true branch only). Passing
        priority='high' in the initial context should take the true branch and
        reach the create-record node."""
        condition_node = self.env['baramej.flow.node'].create({
            'workflow_id': self.workflow.id,
            'name': 'Check Priority',
            'node_type': 'condition',
            'sequence': 15,
            'config_json': '{"field": "priority", "operator": "==", "value": "high"}',
        })

        # Rewire: trigger -> condition -> create_node (true branch only)
        self.env['baramej.flow.edge'].search([
            ('workflow_id', '=', self.workflow.id),
            ('source_node_id', '=', self.trigger_node.id),
        ]).unlink()
        self.env['baramej.flow.edge'].create({
            'workflow_id': self.workflow.id,
            'source_node_id': self.trigger_node.id,
            'target_node_id': condition_node.id,
        })
        self.env['baramej.flow.edge'].create({
            'workflow_id': self.workflow.id,
            'source_node_id': condition_node.id,
            'target_node_id': self.create_node.id,
            'condition_branch': 'true',
        })

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, trigger_source='unit_test', initial_context={'priority': 'high'})

        self.assertEqual(execution.state, 'success')
        self.assertEqual(len(execution.log_ids), 3)  # trigger, condition, create

    def test_condition_node_false_branch_has_no_outgoing_edge_stops_cleanly(self):
        """If the false branch has no outgoing edge, the run should end successfully
        after the condition node rather than erroring out."""
        condition_node = self.env['baramej.flow.node'].create({
            'workflow_id': self.workflow.id,
            'name': 'Check Priority',
            'node_type': 'condition',
            'sequence': 15,
            'config_json': '{"field": "priority", "operator": "==", "value": "high"}',
        })
        self.env['baramej.flow.edge'].search([
            ('workflow_id', '=', self.workflow.id),
            ('source_node_id', '=', self.trigger_node.id),
        ]).unlink()
        self.env['baramej.flow.edge'].create({
            'workflow_id': self.workflow.id,
            'source_node_id': self.trigger_node.id,
            'target_node_id': condition_node.id,
        })
        self.env['baramej.flow.edge'].create({
            'workflow_id': self.workflow.id,
            'source_node_id': condition_node.id,
            'target_node_id': self.create_node.id,
            'condition_branch': 'true',
        })

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, trigger_source='unit_test', initial_context={'priority': 'low'})

        self.assertEqual(execution.state, 'success')
        self.assertEqual(len(execution.log_ids), 2)  # trigger, condition (false -> dead end)

    def test_unknown_model_raises_clear_error(self):
        """A misconfigured Create Record node should fail the run with a readable
        error rather than a raw traceback."""
        bad_node = self.env['baramej.flow.node'].create({
            'workflow_id': self.workflow.id,
            'name': 'Bad Create',
            'node_type': 'action_create_record',
            'sequence': 20,
            'config_json': '{"model": "not.a.real.model", "values": {}}',
        })
        self.env['baramej.flow.edge'].search([
            ('workflow_id', '=', self.workflow.id),
        ]).unlink()
        self.env['baramej.flow.edge'].create({
            'workflow_id': self.workflow.id,
            'source_node_id': self.trigger_node.id,
            'target_node_id': bad_node.id,
        })

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, trigger_source='unit_test')

        self.assertEqual(execution.state, 'failed')
        self.assertIn('unknown model', execution.error_message)
