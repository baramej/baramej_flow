# -*- coding: utf-8 -*-
import json
from unittest.mock import Mock, patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.baramej_flow.engine.utils import get_nested


@tagged('post_install', '-at_install')
class TestGetNested(TransactionCase):

    def test_resolves_nested_path(self):
        context = {'ai_result': {'category': 'network', 'confidence': 0.92}}
        self.assertEqual(get_nested(context, 'ai_result.confidence'), 0.92)
        self.assertEqual(get_nested(context, 'ai_result.category'), 'network')

    def test_flat_path_still_works_unchanged(self):
        """Phase 1/2 condition configs using a plain field name (no dots) must keep
        working exactly as before - this is a backward-compatibility guarantee."""
        context = {'priority': 'high'}
        self.assertEqual(get_nested(context, 'priority'), 'high')

    def test_missing_path_returns_none_not_raises(self):
        context = {'ai_result': {'category': 'network'}}
        self.assertIsNone(get_nested(context, 'ai_result.confidence'))
        self.assertIsNone(get_nested(context, 'completely.missing.path'))
        self.assertIsNone(get_nested({}, 'anything'))


@tagged('post_install', '-at_install')
class TestConditionOnNestedAIOutput(TransactionCase):
    """Proves the architectural claim in condition.py's Phase 1 docstring: an AI
    node's structured output can drive branching through the *existing* Condition
    node, with no new node type needed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Confidence Routing Test', 'trigger_type': 'manual',
        })
        trigger = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Start',
            'node_type': 'trigger_manual', 'sequence': 10,
        })
        condition = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Check Confidence',
            'node_type': 'condition', 'sequence': 20,
            'config_json': json.dumps({'field': 'ai_result.confidence', 'operator': '>=', 'value': 0.8}),
        })
        auto_route_node = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Auto Route',
            'node_type': 'action_create_record', 'sequence': 30,
            'config_json': json.dumps({'model': 'res.partner', 'values': {'name': 'Auto Routed High Confidence'}}),
        })
        cls.env['baramej.flow.edge'].create({
            'workflow_id': cls.workflow.id, 'source_node_id': trigger.id, 'target_node_id': condition.id,
        })
        cls.env['baramej.flow.edge'].create({
            'workflow_id': cls.workflow.id, 'source_node_id': condition.id,
            'target_node_id': auto_route_node.id, 'condition_branch': 'true',
        })

    def test_high_confidence_routes_true_and_acts(self):
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'ai_result': {'confidence': 0.95}})
        self.assertEqual(execution.state, 'success')
        self.assertTrue(self.env['res.partner'].search([('name', '=', 'Auto Routed High Confidence')]))

    def test_low_confidence_routes_false_dead_end(self):
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'ai_result': {'confidence': 0.3}})
        self.assertEqual(execution.state, 'success')
        self.assertEqual(len(execution.log_ids), 2)  # trigger + condition only, false branch is a dead end


@tagged('post_install', '-at_install')
class TestAIOpenAINode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.credential = cls.env['baramej.flow.credential'].create({
            'name': 'Test OpenAI Key', 'provider': 'openai', 'api_key': 'sk-fake-test-key-not-real',
        })
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'AI Classify Test', 'trigger_type': 'manual',
        })
        trigger = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Start', 'node_type': 'trigger_manual', 'sequence': 10,
        })
        cls.ai_node = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Classify Ticket', 'node_type': 'ai_openai', 'sequence': 20,
            'config_json': json.dumps({
                'credential_id': cls.credential.id,
                'model': 'gpt-4o-mini',
                'system_prompt': 'Classify IT support tickets by category and confidence.',
                'user_prompt_template': 'Ticket: {{form.description}}',
                'output_key': 'ai_result',
                'json_schema': {'category': 'string', 'confidence': 'number'},
            }),
        })
        cls.env['baramej.flow.edge'].create({
            'workflow_id': cls.workflow.id, 'source_node_id': trigger.id, 'target_node_id': cls.ai_node.id,
        })

    @staticmethod
    def _mock_openai_response(category='network', confidence=0.91):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': json.dumps({'category': category, 'confidence': confidence})}}],
            'usage': {'prompt_tokens': 50, 'completion_tokens': 12, 'total_tokens': 62},
        }
        return mock_response

    @patch('odoo.addons.baramej_flow.engine.nodes.ai_openai.requests.post')
    def test_ai_node_parses_structured_output_and_interpolates_prompt(self, mock_post):
        mock_post.return_value = self._mock_openai_response()

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'form': {'description': 'Cannot connect to VPN'}})

        self.assertEqual(execution.state, 'success')
        ai_log = execution.log_ids.filtered(lambda l: l.node_type == 'ai_openai')
        self.assertTrue(ai_log)
        output = json.loads(ai_log.output_json)
        self.assertEqual(output['ai_result']['category'], 'network')
        self.assertAlmostEqual(output['ai_result']['confidence'], 0.91)

        # Confirm the {{form.description}} placeholder was actually interpolated
        # before being sent, not forwarded to OpenAI literally.
        sent_payload = mock_post.call_args.kwargs['json']
        self.assertIn('Cannot connect to VPN', sent_payload['messages'][1]['content'])
        self.assertEqual(sent_payload['response_format'], {'type': 'json_object'})

    @patch('odoo.addons.baramej_flow.engine.nodes.ai_openai.requests.post')
    def test_provider_error_fails_the_run_with_readable_message(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = 'Invalid API key provided'
        mock_post.return_value = mock_response

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'form': {'description': 'test'}})

        self.assertEqual(execution.state, 'failed')
        self.assertIn('401', execution.error_message)

    def test_missing_credential_fails_clearly_before_any_api_call(self):
        self.credential.active = False
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'form': {'description': 'test'}})

        self.assertEqual(execution.state, 'failed')
        self.assertIn('credential', execution.error_message.lower())

    def test_missing_api_key_fails_clearly(self):
        self.credential.active = True
        self.credential.api_key = False
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'form': {'description': 'test'}})

        self.assertEqual(execution.state, 'failed')
        self.assertIn('api key', execution.error_message.lower())


@tagged('post_install', '-at_install')
class TestAIGeminiNode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.credential = cls.env['baramej.flow.credential'].create({
            'name': 'Test Gemini Key', 'provider': 'gemini', 'api_key': 'fake-gemini-key-not-real',
        })
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Gemini Classify Test', 'trigger_type': 'manual',
        })
        trigger = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Start', 'node_type': 'trigger_manual', 'sequence': 10,
        })
        cls.ai_node = cls.env['baramej.flow.node'].create({
            'workflow_id': cls.workflow.id, 'name': 'Classify with Gemini', 'node_type': 'ai_gemini', 'sequence': 20,
            'config_json': json.dumps({
                'credential_id': cls.credential.id,
                'model': 'gemini-1.5-pro',
                'system_prompt': 'Classify IT support tickets.',
                'user_prompt_template': 'Ticket: {{form.description}}',
                'output_key': 'ai_result',
                'json_schema': {'category': 'string', 'confidence': 'number'},
            }),
        })
        cls.env['baramej.flow.edge'].create({
            'workflow_id': cls.workflow.id, 'source_node_id': trigger.id, 'target_node_id': cls.ai_node.id,
        })

    @patch('odoo.addons.baramej_flow.engine.nodes.ai_gemini.requests.post')
    def test_gemini_node_parses_structured_output(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'candidates': [{'content': {'parts': [
                {'text': json.dumps({'category': 'hardware', 'confidence': 0.88})}
            ]}}],
            'usageMetadata': {'promptTokenCount': 40, 'candidatesTokenCount': 10, 'totalTokenCount': 50},
        }
        mock_post.return_value = mock_response

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self.workflow, initial_context={'form': {'description': 'Laptop wont turn on'}})

        self.assertEqual(execution.state, 'success')
        ai_log = execution.log_ids.filtered(lambda l: l.node_type == 'ai_gemini')
        output = json.loads(ai_log.output_json)
        self.assertEqual(output['ai_result']['category'], 'hardware')


@tagged('post_install', '-at_install')
class TestCredentialTestConnection(TransactionCase):

    def test_test_connection_without_api_key_raises(self):
        from odoo.exceptions import UserError
        credential = self.env['baramej.flow.credential'].create({
            'name': 'Empty Credential', 'provider': 'openai',
        })
        with self.assertRaises(UserError):
            credential.action_test_connection()

    def test_test_connection_unsupported_provider_raises(self):
        from odoo.exceptions import UserError
        credential = self.env['baramej.flow.credential'].create({
            'name': 'Generic Credential', 'provider': 'generic', 'api_key': 'some-key',
        })
        with self.assertRaises(UserError):
            credential.action_test_connection()
