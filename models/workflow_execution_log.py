# -*- coding: utf-8 -*-
from odoo import fields, models


class FlowExecutionLog(models.Model):
    _name = 'baramej.flow.execution.log'
    _description = 'Baramej Flow - Node Execution Log'
    _order = 'execution_id, sequence, id'

    execution_id = fields.Many2one('baramej.flow.execution', required=True, ondelete='cascade', index=True)
    node_id = fields.Many2one('baramej.flow.node', ondelete='set null')
    sequence = fields.Integer()

    # node_name/node_type are stored as plain text (not just via node_id) so the log
    # stays readable even if the node is later renamed or deleted from the workflow.
    node_name = fields.Char()
    node_type = fields.Char()

    state = fields.Selection(
        [
            ('success', 'Success'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped'),
            ('waiting', 'Waiting'),
        ],
    )
    input_json = fields.Text(string='Input')
    output_json = fields.Text(string='Output')
    error_message = fields.Text()
    duration = fields.Float(help='Seconds')
