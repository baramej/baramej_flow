# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class FlowInboundMail(models.Model):
    """Technical model that mail.alias routes inbound email into. Each workflow that
    uses the 'Incoming Email' trigger gets its own mail.alias (created via
    FlowWorkflow.action_generate_email_alias), with alias_defaults pinning
    workflow_id so message_new() below knows which workflow to run.

    Every inbound email is kept as a record here (not deleted after processing) —
    that's a deliberate design choice, not an oversight: it gives you an audit trail
    of exactly what was received and when, which matters for a government tender
    where "show me every message that triggered this workflow" is a reasonable ask.
    """
    _name = 'baramej.flow.inbound.mail'
    _inherit = ['mail.thread']
    _description = 'Baramej Flow - Inbound Email Log'
    _order = 'create_date desc'

    name = fields.Char(default='Inbound Email')
    workflow_id = fields.Many2one('baramej.flow.workflow', ondelete='cascade', index=True)
    execution_id = fields.Many2one('baramej.flow.execution', readonly=True)
    email_from = fields.Char(readonly=True)
    subject = fields.Char(readonly=True)
    body = fields.Html(readonly=True)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Called by Odoo's mail gateway when a new email arrives at this model's
        alias. custom_values carries whatever was set in the alias's alias_defaults —
        in our case, {'workflow_id': <id>} — so this record already knows which
        workflow to trigger before it's even saved."""
        record = super().message_new(msg_dict, custom_values=custom_values)
        record.write({
            'name': msg_dict.get('subject') or 'Inbound Email',
            'email_from': msg_dict.get('email_from'),
            'subject': msg_dict.get('subject'),
            'body': msg_dict.get('body'),
        })
        record._trigger_workflow()
        return record

    def _trigger_workflow(self):
        for rec in self:
            if not rec.workflow_id:
                _logger.warning('Baramej Flow: inbound email %s has no linked workflow — check the alias '
                                 'configuration.', rec.id)
                continue
            if rec.workflow_id.state != 'published':
                _logger.info('Baramej Flow: inbound email %s arrived for unpublished workflow "%s" — '
                             'skipping execution.', rec.id, rec.workflow_id.name)
                continue

            from ..engine.executor import WorkflowEngine
            engine = WorkflowEngine(self.env)
            context = {
                'email': {
                    'from': rec.email_from,
                    'subject': rec.subject,
                    'body': rec.body,
                },
            }
            execution = engine.run(rec.workflow_id, trigger_source='email', initial_context=context)
            rec.execution_id = execution.id
