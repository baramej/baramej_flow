# -*- coding: utf-8 -*-
import uuid

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FlowWorkflow(models.Model):
    _name = 'baramej.flow.workflow'
    _description = 'Baramej Flow - Workflow Definition'
    _order = 'name'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    description = fields.Text(help='What this workflow does, in plain language, for the next person who opens it.')
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('published', 'Published'),
            ('archived', 'Archived'),
        ],
        default='draft', required=True, tracking=True,
        help='Draft workflows can be freely edited. Publishing locks in a version that is safe to '
             'reference from automated triggers in later phases.',
    )
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    version = fields.Integer(default=1, readonly=True, help='Incremented every time this workflow is published.')

    node_ids = fields.One2many('baramej.flow.node', 'workflow_id', string='Nodes')
    edge_ids = fields.One2many('baramej.flow.edge', 'workflow_id', string='Connections')
    execution_ids = fields.One2many('baramej.flow.execution', 'workflow_id', string='Executions')
    execution_count = fields.Integer(compute='_compute_execution_count')

    # Trigger configuration. 'manual', 'model_event', 'webhook', 'email', 'whatsapp',
    # and 'form' are all functional as of Phase 2. 'cron' remains reserved.
    trigger_type = fields.Selection(
        [
            ('manual', 'Manual'),
            ('model_event', 'Odoo Record Event'),
            ('webhook', 'Webhook'),
            ('email', 'Incoming Email'),
            ('whatsapp', 'WhatsApp'),
            ('form', 'Web Form'),
            ('cron', 'Scheduled (Phase 5+)'),
        ],
        default='manual', required=True, string='Trigger Type',
    )
    model_id = fields.Many2one(
        'ir.model', string='Trigger Model',
        help='Only used when Trigger Type is "Odoo Record Event".',
    )
    trigger_on = fields.Selection(
        [
            ('create', 'On Create'),
            ('write', 'On Update'),
            ('create_or_write', 'On Create or Update'),
        ],
        default='create', string='Trigger On',
    )

    # --- Webhook trigger (generic + WhatsApp share the same uuid/secret pair) ---
    webhook_uuid = fields.Char(
        default=lambda self: str(uuid.uuid4()), readonly=True, copy=False,
        help='Unique per-workflow identifier used in both the generic webhook URL and the '
             'WhatsApp webhook URL.',
    )
    webhook_secret = fields.Char(
        default=lambda self: uuid.uuid4().hex, readonly=True, copy=False,
        help='Used to HMAC-sign generic webhook requests. The caller must send a '
             'X-Flow-Signature header = hex(HMAC-SHA256(body, this secret)).',
    )
    webhook_url = fields.Char(compute='_compute_endpoint_urls', string='Webhook URL')
    whatsapp_verify_token = fields.Char(
        default=lambda self: uuid.uuid4().hex, readonly=True, copy=False,
        help='Enter this exact value as the "Verify Token" in the Meta Developer Console '
             'when configuring the WhatsApp webhook.',
    )
    whatsapp_webhook_url = fields.Char(compute='_compute_endpoint_urls', string='WhatsApp Webhook URL')

    # --- Email trigger ---
    email_alias_id = fields.Many2one('mail.alias', readonly=True, copy=False, ondelete='restrict')
    email_alias_full = fields.Char(compute='_compute_email_alias_full', string='Trigger Email Address')

    # --- Form trigger ---
    form_ids = fields.One2many('baramej.flow.form', 'workflow_id', string='Web Forms')

    @api.depends('execution_ids')
    def _compute_execution_count(self):
        for rec in self:
            rec.execution_count = len(rec.execution_ids)

    def _compute_endpoint_urls(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for rec in self:
            if rec.webhook_uuid:
                rec.webhook_url = '%s/flow/webhook/%s' % (base_url, rec.webhook_uuid)
                rec.whatsapp_webhook_url = '%s/flow/whatsapp/webhook/%s' % (base_url, rec.webhook_uuid)
            else:
                rec.webhook_url = False
                rec.whatsapp_webhook_url = False

    def _compute_email_alias_full(self):
        for rec in self:
            rec.email_alias_full = rec.email_alias_id.display_name if rec.email_alias_id else False

    def action_publish(self):
        for rec in self:
            if not rec.node_ids:
                raise UserError(_('"%s" has no nodes yet — add at least a trigger node before publishing.') % rec.name)
            if not rec.edge_ids and len(rec.node_ids) > 1:
                raise UserError(_('"%s" has multiple nodes but no connections between them — the workflow would have '
                                   'nothing to execute past the first node.') % rec.name)
            rec.write({'state': 'published', 'version': rec.version + 1})

    def action_set_draft(self):
        self.write({'state': 'draft'})

    def action_run_test(self):
        """Runs the workflow immediately with an empty context, purely for build-time
        testing. Automated triggering (from a record event, webhook, etc.) is wired up
        in later phases; this action exists so a workflow can be validated end-to-end
        as soon as it's built."""
        self.ensure_one()
        from ..engine.executor import WorkflowEngine
        engine = WorkflowEngine(self.env)
        execution = engine.run(self, trigger_source='manual_test', initial_context={})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Test Execution Result'),
            'res_model': 'baramej.flow.execution',
            'res_id': execution.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_regenerate_webhook_secret(self):
        """Rotates the HMAC secret. Any external system already sending signed requests
        will start failing signature verification until it's updated with the new
        secret — this is deliberate (rotation should break old credentials), but worth
        surfacing to whoever clicks this."""
        for rec in self:
            rec.webhook_secret = uuid.uuid4().hex

    def action_generate_email_alias(self):
        """Creates the mail.alias that routes inbound email to this workflow. Uses the
        same pattern Odoo's own Helpdesk/Project apps use: a technical model
        (baramej.flow.inbound.mail) implementing mail.thread, with alias_defaults
        pinning each created record to this specific workflow_id."""
        self.ensure_one()
        if self.email_alias_id:
            return
        alias_model = self.env['ir.model']._get('baramej.flow.inbound.mail')
        alias = self.env['mail.alias'].sudo().create({
            'alias_name': 'flow-%s' % self.id,
            'alias_model_id': alias_model.id,
            'alias_defaults': repr({'workflow_id': self.id}),
            'alias_contact': 'everyone',
        })
        self.email_alias_id = alias.id

    def action_open_canvas(self):
        """Opens the Phase 1b visual canvas as a client action, passing this
        workflow's id as a param. The canvas (static/src/js/flow_canvas.js) reads
        and writes baramej.flow.node / baramej.flow.edge directly via the ORM
        service using the logged-in user's own permissions — no new server-side
        endpoint was needed, since the existing Flow User/Flow Manager access
        rights already govern those models correctly."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'baramej_flow_canvas',
            'name': _('Canvas: %s') % self.name,
            'params': {'workflow_id': self.id},
        }

    def action_view_executions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Executions'),
            'res_model': 'baramej.flow.execution',
            'view_mode': 'list,form',
            'domain': [('workflow_id', '=', self.id)],
            'context': {'default_workflow_id': self.id},
        }
