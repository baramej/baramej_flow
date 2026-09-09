# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FlowEdge(models.Model):
    _name = 'baramej.flow.edge'
    _description = 'Baramej Flow - Connection'
    _order = 'id'

    workflow_id = fields.Many2one('baramej.flow.workflow', required=True, ondelete='cascade', index=True)
    source_node_id = fields.Many2one('baramej.flow.node', required=True, ondelete='cascade', string='From Node')
    target_node_id = fields.Many2one('baramej.flow.node', required=True, ondelete='cascade', string='To Node')

    # 'default' is used for every node type except Condition, which uses true/false to
    # pick which outgoing edge to follow.
    condition_branch = fields.Selection(
        [
            ('default', 'Default'),
            ('true', 'True Branch'),
            ('false', 'False Branch'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('more_info', 'More Info Needed'),
        ],
        default='default', required=True,
    )

    # Canvas routing. A single bend point per connection — not an arbitrary
    # multi-point polyline, which would need a much larger canvas editor (add/
    # remove waypoints, drag each independently). One draggable point already
    # covers the common case (untangling a straight overlap into an L-shape or
    # gentle curve); has_custom_bend distinguishes "user dragged this" from
    # "still auto-routed" — 0.0 is a valid dragged coordinate, so a boolean
    # flag is used instead of treating 0.0 itself as "unset".
    has_custom_bend = fields.Boolean(
        default=False,
        help='True once the user has dragged this connection\'s bend point on the canvas. False = '
             'auto-curved based on node positions, recomputed on every render.',
    )
    waypoint_x = fields.Float(default=0.0)
    waypoint_y = fields.Float(default=0.0)

    @api.constrains('source_node_id', 'target_node_id')
    def _check_not_self_loop(self):
        for rec in self:
            if rec.source_node_id.id == rec.target_node_id.id:
                raise ValidationError('A node cannot connect to itself.')

    @api.constrains('source_node_id', 'target_node_id', 'workflow_id')
    def _check_same_workflow(self):
        for rec in self:
            if rec.source_node_id.workflow_id != rec.workflow_id or rec.target_node_id.workflow_id != rec.workflow_id:
                raise ValidationError('Connections can only link nodes that belong to the same workflow.')
