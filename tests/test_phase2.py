# -*- coding: utf-8 -*-
import hashlib
import hmac

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.baramej_flow.controllers.webhook_controller import FlowWebhookController
from odoo.addons.baramej_flow.engine.node_registry import get_registered_node_types


@tagged('post_install', '-at_install')
class TestPhase2NodeRegistry(TransactionCase):

    def test_all_node_types_registered(self):
        """The Selection list on baramej.flow.node and the engine's node registry
        must never drift apart — a node type in one but not the other fails silently
        at runtime instead of being caught by the UI."""
        model_node_types = set(
            key for key, _label in
            self.env['baramej.flow.node']._fields['node_type'].selection
        )
        registered_types = set(get_registered_node_types())
        self.assertEqual(
            model_node_types, registered_types,
            'node_type Selection values and engine node_registry entries must match exactly.'
        )


@tagged('post_install', '-at_install')
class TestWebhookSignature(TransactionCase):

    def test_valid_signature_accepted(self):
        secret = 'test-secret-123'
        body = b'{"hello": "world"}'
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(FlowWebhookController._verify_signature(body, signature, secret))

    def test_tampered_body_rejected(self):
        secret = 'test-secret-123'
        original_body = b'{"hello": "world"}'
        signature = hmac.new(secret.encode(), original_body, hashlib.sha256).hexdigest()
        tampered_body = b'{"hello": "tampered"}'
        self.assertFalse(FlowWebhookController._verify_signature(tampered_body, signature, secret))

    def test_missing_secret_always_rejected(self):
        body = b'{"hello": "world"}'
        self.assertFalse(FlowWebhookController._verify_signature(body, 'anything', ''))

    def test_missing_signature_always_rejected(self):
        body = b'{"hello": "world"}'
        self.assertFalse(FlowWebhookController._verify_signature(body, '', 'some-secret'))


@tagged('post_install', '-at_install')
class TestWhatsappPayloadParsing(TransactionCase):

    def test_extracts_message_from_standard_payload(self):
        payload = {
            'entry': [{
                'changes': [{
                    'value': {
                        'messages': [{
                            'from': '96891234567',
                            'id': 'wamid.abc123',
                            'timestamp': '1699999999',
                            'type': 'text',
                            'text': {'body': 'Hello, I need help with my ticket'},
                        }]
                    }
                }]
            }]
        }
        messages = FlowWebhookController._extract_whatsapp_messages(payload)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['from'], '96891234567')
        self.assertEqual(messages[0]['text'], 'Hello, I need help with my ticket')

    def test_status_callback_payload_yields_no_messages(self):
        """Meta sends delivery/read receipts to the same endpoint - these have no
        'messages' key and must not be mistaken for an incoming message."""
        payload = {
            'entry': [{
                'changes': [{
                    'value': {'statuses': [{'id': 'wamid.abc123', 'status': 'delivered'}]}
                }]
            }]
        }
        messages = FlowWebhookController._extract_whatsapp_messages(payload)
        self.assertEqual(messages, [])

    def test_malformed_payload_does_not_raise(self):
        self.assertEqual(FlowWebhookController._extract_whatsapp_messages({}), [])
        self.assertEqual(FlowWebhookController._extract_whatsapp_messages({'entry': []}), [])


@tagged('post_install', '-at_install')
class TestFormFieldValidation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Form Test Workflow',
            'trigger_type': 'form',
        })
        cls.form = cls.env['baramej.flow.form'].create({
            'name': 'Test Intake Form',
            'workflow_id': cls.workflow.id,
        })

    def test_valid_field_name_accepted(self):
        field = self.env['baramej.flow.form.field'].create({
            'form_id': self.form.id,
            'name': 'customer_email',
            'label': 'Customer Email',
            'field_type': 'email',
        })
        self.assertTrue(field.id)

    def test_invalid_field_name_rejected(self):
        with self.assertRaises(ValidationError):
            self.env['baramej.flow.form.field'].create({
                'form_id': self.form.id,
                'name': 'Customer Email',  # spaces/capitals not allowed
                'label': 'Customer Email',
                'field_type': 'email',
            })

    def test_duplicate_field_name_on_same_form_rejected(self):
        self.env['baramej.flow.form.field'].create({
            'form_id': self.form.id,
            'name': 'priority',
            'label': 'Priority',
            'field_type': 'char',
        })
        with self.assertRaises(ValidationError):
            self.env['baramej.flow.form.field'].create({
                'form_id': self.form.id,
                'name': 'priority',
                'label': 'Priority Level (duplicate)',
                'field_type': 'char',
            })

    def test_form_publish_requires_published_workflow(self):
        from odoo.exceptions import UserError
        self.env['baramej.flow.form.field'].create({
            'form_id': self.form.id,
            'name': 'subject',
            'label': 'Subject',
            'field_type': 'char',
        })
        # workflow is still draft at this point
        with self.assertRaises(UserError):
            self.form.action_publish()

    def test_form_to_workflow_end_to_end(self):
        """A form submission's field values should reach the workflow as
        context['form'][<field name>], exactly like a real public submission would."""
        self.env['baramej.flow.form.field'].create({
            'form_id': self.form.id,
            'name': 'contact_name',
            'label': 'Name',
            'field_type': 'char',
        })
        trigger_node = self.env['baramej.flow.node'].create({
            'workflow_id': self.workflow.id,
            'name': 'Start',
            'node_type': 'trigger_form',
            'sequence': 10,
        })
        create_node = self.env['baramej.flow.node'].create({
            'workflow_id': self.workflow.id,
            'name': 'Create Contact',
            'node_type': 'action_create_record',
            'sequence': 20,
            'config_json': '{"model": "res.partner", "values": {"name": "{{form.contact_name}}"}, '
                            '"output_key": "partner_id"}',
        })
        self.env['baramej.flow.edge'].create({
            'workflow_id': self.workflow.id,
            'source_node_id': trigger_node.id,
            'target_node_id': create_node.id,
        })
        self.workflow.action_publish()
        self.form.action_publish()
        self.assertTrue(self.form.is_published)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(
            self.workflow, trigger_source='form',
            initial_context={'form': {'contact_name': 'Jane From The Form'}},
        )
        self.assertEqual(execution.state, 'success')
        partner = self.env['res.partner'].search([('name', '=', 'Jane From The Form')])
        self.assertTrue(partner)
