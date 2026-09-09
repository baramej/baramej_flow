# -*- coding: utf-8 -*-
import base64
import io
import json
from unittest.mock import MagicMock, Mock, patch

from odoo.tests.common import TransactionCase, tagged


def _make_node(env, workflow, node_type, config, name=None, sequence=20):
    return env['baramej.flow.node'].create({
        'workflow_id': workflow.id,
        'name': name or node_type,
        'node_type': node_type,
        'sequence': sequence,
        'config_json': json.dumps(config),
    })


def _wire_trigger_to(env, workflow, target_node):
    trigger = env['baramej.flow.node'].create({
        'workflow_id': workflow.id, 'name': 'Start', 'node_type': 'trigger_manual', 'sequence': 10,
    })
    env['baramej.flow.edge'].create({
        'workflow_id': workflow.id, 'source_node_id': trigger.id, 'target_node_id': target_node.id,
    })
    return trigger


@tagged('post_install', '-at_install')
class TestHTTPRequestNode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'HTTP Test', 'trigger_type': 'manual',
        })

    @patch('odoo.addons.baramej_flow.engine.nodes.http_request.requests.request')
    def test_no_auth_get_request(self, mock_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'application/json'}
        mock_response.json.return_value = {'ok': True}
        mock_request.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'http_request', {
            'method': 'GET', 'url': 'https://api.example.com/status', 'auth_type': 'none',
            'output_key': 'http_result',
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')
        log = execution.log_ids.filtered(lambda l: l.node_type == 'http_request')
        output = json.loads(log.output_json)
        self.assertEqual(output['http_result']['data'], {'ok': True})

    @patch('odoo.addons.baramej_flow.engine.nodes.http_request.requests.request')
    def test_bearer_auth_uses_credential(self, mock_request):
        credential = self.env['baramej.flow.credential'].create({
            'name': 'Test Bearer', 'provider': 'generic', 'api_key': 'secret-token-123',
        })
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'application/json'}
        mock_response.json.return_value = {'ok': True}
        mock_request.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'http_request', {
            'method': 'POST', 'url': 'https://api.example.com/things',
            'auth_type': 'bearer', 'credential_id': credential.id,
            'body': {'name': '{{form.name}}'},
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow, initial_context={'form': {'name': 'Test Item'}})
        self.assertEqual(execution.state, 'success')

        sent_headers = mock_request.call_args.kwargs['headers']
        self.assertEqual(sent_headers['Authorization'], 'Bearer secret-token-123')
        sent_body = mock_request.call_args.kwargs['json']
        self.assertEqual(sent_body['name'], 'Test Item')

    @patch('odoo.addons.baramej_flow.engine.nodes.http_request.requests.request')
    def test_non_2xx_raises_by_default(self, mock_request):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {'Content-Type': 'text/plain'}
        mock_response.text = 'Internal Server Error'
        mock_request.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'http_request', {
            'method': 'GET', 'url': 'https://api.example.com/broken', 'auth_type': 'none',
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'failed')

    def test_missing_url_fails_clearly(self):
        node = _make_node(self.env, self.workflow, 'http_request', {'method': 'GET'})
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'failed')
        self.assertIn('url', execution.error_message.lower())


@tagged('post_install', '-at_install')
class TestDatabaseQueryNode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'DB Query Test', 'trigger_type': 'manual',
        })
        cls.credential = cls.env['baramej.flow.credential'].create({
            'name': 'Test PG', 'provider': 'postgres',
            'db_host': 'localhost', 'db_name': 'testdb', 'db_user': 'testuser',
        })

    def test_write_query_blocked_by_default(self):
        node = _make_node(self.env, self.workflow, 'db_query', {
            'credential_id': self.credential.id,
            'query': 'DELETE FROM tickets WHERE id = 1',
        })
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'failed')
        self.assertIn('allow_write', execution.error_message)

    @patch('odoo.addons.baramej_flow.engine.nodes.db_query.psycopg2')
    def test_select_query_returns_rows(self, mock_psycopg2):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [('id',), ('name',)]
        mock_cursor.fetchmany.return_value = [{'id': 1, 'name': 'Ticket A'}]
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        mock_psycopg2.connect.return_value = mock_conn
        mock_psycopg2.extras.RealDictCursor = MagicMock()

        node = _make_node(self.env, self.workflow, 'db_query', {
            'credential_id': self.credential.id,
            'query': 'SELECT id, name FROM tickets LIMIT 10',
        })
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')

    def test_missing_credential_fails_clearly(self):
        node = _make_node(self.env, self.workflow, 'db_query', {
            'query': 'SELECT 1',
        })
        self.credential.active = False
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'failed')


@tagged('post_install', '-at_install')
class TestReadExcelNode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Excel Test', 'trigger_type': 'manual',
        })

    def _make_xlsx_attachment(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Ticket ID', 'Priority'])
        ws.append([101, 'High'])
        ws.append([102, 'Low'])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return self.env['ir.attachment'].create({
            'name': 'test.xlsx',
            'datas': base64.b64encode(buf.read()),
        })

    def test_reads_rows_with_header(self):
        attachment = self._make_xlsx_attachment()
        node = _make_node(self.env, self.workflow, 'read_excel', {
            'attachment_id': attachment.id,
            'has_header_row': True,
            'output_key': 'excel_rows',
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')
        log = execution.log_ids.filtered(lambda l: l.node_type == 'read_excel')
        output = json.loads(log.output_json)
        self.assertEqual(len(output['excel_rows']), 2)
        self.assertEqual(output['excel_rows'][0]['Ticket ID'], 101)
        self.assertEqual(output['excel_rows'][0]['Priority'], 'High')

    def test_missing_attachment_fails_clearly(self):
        node = _make_node(self.env, self.workflow, 'read_excel', {'attachment_id': 999999})
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'failed')


@tagged('post_install', '-at_install')
class TestGoogleOAuthRefresh(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.credential = cls.env['baramej.flow.credential'].create({
            'name': 'Test Google OAuth', 'provider': 'google_oauth2',
            'oauth_client_id': 'client-id', 'oauth_client_secret': 'client-secret',
            'oauth_refresh_token': 'refresh-token-abc',
        })

    @patch('odoo.addons.baramej_flow.engine.nodes.google_oauth_base.requests.post')
    def test_refreshes_and_caches_token(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'access_token': 'new-access-token', 'expires_in': 3600}
        mock_post.return_value = mock_response

        from odoo.addons.baramej_flow.engine.nodes.google_oauth_base import refresh_google_access_token
        token = refresh_google_access_token(self.credential)
        self.assertEqual(token, 'new-access-token')
        self.assertEqual(self.credential.oauth_access_token, 'new-access-token')
        self.assertTrue(self.credential.oauth_token_expiry)

    @patch('odoo.addons.baramej_flow.engine.nodes.google_oauth_base.requests.post')
    def test_cached_token_not_refreshed_if_still_valid(self, mock_post):
        from datetime import datetime, timedelta
        self.credential.write({
            'oauth_access_token': 'still-valid-token',
            'oauth_token_expiry': datetime.now() + timedelta(hours=1),
        })
        from odoo.addons.baramej_flow.engine.nodes.google_oauth_base import refresh_google_access_token
        token = refresh_google_access_token(self.credential)
        self.assertEqual(token, 'still-valid-token')
        mock_post.assert_not_called()

    @patch('odoo.addons.baramej_flow.engine.nodes.google_oauth_base.requests.post')
    def test_revoked_refresh_token_raises_clear_error(self, mock_post):
        from odoo.exceptions import UserError
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = 'invalid_grant'
        mock_post.return_value = mock_response

        from odoo.addons.baramej_flow.engine.nodes.google_oauth_base import refresh_google_access_token
        with self.assertRaises(UserError):
            refresh_google_access_token(self.credential)


@tagged('post_install', '-at_install')
class TestGoogleSheetsNodes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.credential = cls.env['baramej.flow.credential'].create({
            'name': 'Test Google OAuth', 'provider': 'google_oauth2',
            'oauth_client_id': 'x', 'oauth_client_secret': 'y', 'oauth_refresh_token': 'z',
            'oauth_access_token': 'cached-token',
        })
        from datetime import datetime, timedelta
        cls.credential.oauth_token_expiry = datetime.now() + timedelta(hours=1)
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Sheets Test', 'trigger_type': 'manual',
        })

    @patch('odoo.addons.baramej_flow.engine.nodes.google_sheets_append.requests.post')
    def test_append_row(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'updates': {'updatedRows': 1}}
        mock_post.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'google_sheets_append', {
            'credential_id': self.credential.id,
            'spreadsheet_id': 'sheet123',
            'range': 'Sheet1!A1',
            'values': ['{{form.name}}', '{{form.email}}'],
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(
            self.workflow, initial_context={'form': {'name': 'Jane', 'email': 'jane@example.com'}})
        self.assertEqual(execution.state, 'success')

        sent_payload = mock_post.call_args.kwargs['json']
        self.assertEqual(sent_payload['values'], [['Jane', 'jane@example.com']])

    @patch('odoo.addons.baramej_flow.engine.nodes.google_sheets_read.requests.get')
    def test_read_range_with_header(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'values': [['Ticket ID', 'Priority'], ['101', 'High'], ['102', 'Low']]
        }
        mock_get.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'google_sheets_read', {
            'credential_id': self.credential.id,
            'spreadsheet_id': 'sheet123',
            'range': 'Sheet1!A1:B100',
            'has_header_row': True,
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')
        log = execution.log_ids.filtered(lambda l: l.node_type == 'google_sheets_read')
        output = json.loads(log.output_json)
        self.assertEqual(output['sheet_rows'][0]['Ticket ID'], '101')


@tagged('post_install', '-at_install')
class TestGmailAndCalendarAndDriveNodes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.credential = cls.env['baramej.flow.credential'].create({
            'name': 'Test Google OAuth', 'provider': 'google_oauth2',
            'oauth_client_id': 'x', 'oauth_client_secret': 'y', 'oauth_refresh_token': 'z',
            'oauth_access_token': 'cached-token',
        })
        from datetime import datetime, timedelta
        cls.credential.oauth_token_expiry = datetime.now() + timedelta(hours=1)
        cls.workflow = cls.env['baramej.flow.workflow'].create({
            'name': 'Google APIs Test', 'trigger_type': 'manual',
        })

    @patch('odoo.addons.baramej_flow.engine.nodes.gmail_send.requests.post')
    def test_gmail_send_encodes_message(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'msg123'}
        mock_post.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'gmail_send', {
            'credential_id': self.credential.id,
            'to': 'citizen@example.com',
            'subject': 'Ticket received',
            'body': 'We received your request.',
        })
        _wire_trigger_to(self.env, self.workflow, node)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')
        sent_payload = mock_post.call_args.kwargs['json']
        self.assertIn('raw', sent_payload)

    @patch('odoo.addons.baramej_flow.engine.nodes.google_calendar_create_event.requests.post')
    def test_calendar_create_event_requires_datetimes(self, mock_post):
        node = _make_node(self.env, self.workflow, 'google_calendar_create_event', {
            'credential_id': self.credential.id,
            'summary': 'Follow-up call',
        })
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'failed')
        mock_post.assert_not_called()

    @patch('odoo.addons.baramej_flow.engine.nodes.google_calendar_create_event.requests.post')
    def test_calendar_create_event_success(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'evt123', 'htmlLink': 'https://calendar.google.com/x'}
        mock_post.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'google_calendar_create_event', {
            'credential_id': self.credential.id,
            'summary': 'Follow-up call',
            'start_datetime': '2026-09-01T10:00:00+04:00',
            'end_datetime': '2026-09-01T10:30:00+04:00',
        })
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')

    @patch('odoo.addons.baramej_flow.engine.nodes.google_drive_list.requests.get')
    def test_drive_list_files(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'files': [{'id': 'f1', 'name': 'doc.pdf'}]}
        mock_get.return_value = mock_response

        node = _make_node(self.env, self.workflow, 'google_drive_list', {
            'credential_id': self.credential.id,
            'query': "name contains 'ticket'",
        })
        _wire_trigger_to(self.env, self.workflow, node)
        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        execution = WorkflowEngine(self.env).run(self.workflow)
        self.assertEqual(execution.state, 'success')
        log = execution.log_ids.filtered(lambda l: l.node_type == 'google_drive_list')
        output = json.loads(log.output_json)
        self.assertEqual(output['drive_files'][0]['name'], 'doc.pdf')
