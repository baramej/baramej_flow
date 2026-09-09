# -*- coding: utf-8 -*-
import json

from odoo import fields, models


class FlowNode(models.Model):
    _name = 'baramej.flow.node'
    _description = 'Baramej Flow - Node'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    workflow_id = fields.Many2one('baramej.flow.workflow', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)

    # Registered node types. New node types are added here AND registered in
    # engine/nodes/ — this selection list and the executor registry are kept in sync
    # deliberately so an invalid config is caught by the UI, not just at runtime.
    node_type = fields.Selection(
        [
            ('trigger_manual', 'Trigger: Manual'),
            ('trigger_webhook', 'Trigger: Webhook'),
            ('trigger_email', 'Trigger: Incoming Email'),
            ('trigger_whatsapp', 'Trigger: WhatsApp'),
            ('trigger_form', 'Trigger: Web Form'),
            ('action_create_record', 'Action: Create Record'),
            ('action_update_record', 'Action: Update Record'),
            ('condition', 'Condition (IF Branch)'),
            ('ai_openai', 'AI: OpenAI'),
            ('ai_gemini', 'AI: Google Gemini'),
            ('http_request', 'Integration: HTTP Request'),
            ('db_query', 'Integration: Database Query'),
            ('read_excel', 'Integration: Read Excel'),
            ('google_sheets_append', 'Integration: Google Sheets - Append Row'),
            ('google_sheets_read', 'Integration: Google Sheets - Read Range'),
            ('gmail_send', 'Integration: Gmail - Send Email'),
            ('google_calendar_create_event', 'Integration: Google Calendar - Create Event'),
            ('google_drive_list', 'Integration: Google Drive - List Files'),
            ('assign_employee', 'E-Service: Assign Employee'),
            ('human_approval', 'E-Service: Human Approval'),
            ('wait_for_resubmission', 'E-Service: Wait For Resubmission'),
            ('send_payment_link', 'E-Service: Send Payment Link'),
        ],
        required=True, string='Node Type',
    )

    # Reserved for the Phase 1b visual canvas — stored now so early workflows don't
    # need to be rebuilt once the canvas ships, they just render at (0,0) until
    # someone drags them.
    pos_x = fields.Float(default=0.0, string='Canvas X')
    pos_y = fields.Float(default=0.0, string='Canvas Y')

    config_json = fields.Text(
        string='Configuration (JSON)', default='{}',
        help='Node-specific configuration. Structure depends on Node Type — see the '
             'module README for the schema each node type expects.',
    )
    note = fields.Text(help='Free-text notes for whoever maintains this workflow next.')

    outgoing_edge_ids = fields.One2many('baramej.flow.edge', 'source_node_id', string='Outgoing Connections')
    incoming_edge_ids = fields.One2many('baramej.flow.edge', 'target_node_id', string='Incoming Connections')

    def get_config(self):
        """Safely parse config_json. Returns {} on malformed JSON rather than raising,
        so a typo in one node's config surfaces as a clear execution-log error on that
        node instead of crashing form rendering elsewhere."""
        self.ensure_one()
        try:
            return json.loads(self.config_json or '{}')
        except ValueError:
            return {}
