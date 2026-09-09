# -*- coding: utf-8 -*-
from odoo import api, fields, models


class FlowExecution(models.Model):
    _name = 'baramej.flow.execution'
    _description = 'Baramej Flow - Execution Run'
    _order = 'create_date desc'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    workflow_id = fields.Many2one('baramej.flow.workflow', required=True, ondelete='cascade', index=True)
    state = fields.Selection(
        [
            ('running', 'Running'),
            ('waiting', 'Waiting'),
            ('success', 'Success'),
            ('failed', 'Failed'),
        ],
        default='running', required=True,
        help='"Waiting" means the execution is paused mid-graph — typically at a human_approval, '
             'wait_for_resubmission, or send_payment_link node — until something external (a staff '
             'decision, a resubmission, a payment) calls WorkflowEngine.resume() on it.',
    )
    waiting_node_id = fields.Many2one(
        'baramej.flow.node', readonly=True, copy=False,
        help='Set only while state=waiting. Identifies exactly which node the execution is paused at, '
             'so resume() knows where to continue from.',
    )
    live_context_json = fields.Text(
        default='{}', string='Live Context',
        help='The execution context as of the last pause or completion — NOT just the initial trigger '
             'context (see context_json for that). resume() reads from here and merges in whatever new '
             'data the resuming action provides.',
    )
    # Free-text now (e.g. 'manual_test'); becomes a proper selection once Phase 2 adds
    # webhook/email/WhatsApp/form/cron triggers with distinct identifiers.
    trigger_source = fields.Char(default='manual')
    context_json = fields.Text(default='{}', string='Initial Context')
    start_date = fields.Datetime(default=fields.Datetime.now)
    end_date = fields.Datetime()
    duration = fields.Float(compute='_compute_duration', store=True, help='Seconds')
    log_ids = fields.One2many('baramej.flow.execution.log', 'execution_id', string='Node Logs')
    error_message = fields.Text()

    @api.depends('start_date', 'end_date')
    def _compute_duration(self):
        for rec in self:
            if rec.start_date and rec.end_date:
                rec.duration = (rec.end_date - rec.start_date).total_seconds()
            else:
                rec.duration = 0.0
