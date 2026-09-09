# -*- coding: utf-8 -*-
from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from .base import BaseNodeExecutor


@register_node('assign_employee')
class AssignEmployeeExecutor(BaseNodeExecutor):
    """Assigns a specific employee to the ticket linked in context['ticket_id'].
    Static assignment only for this pass — config points at one user_id. Rotating/
    load-based/role-based assignment (e.g. "whoever on the Licensing team has the
    fewest open tickets") is a natural next increment once there's a real team
    structure to route against; flagged rather than guessed at here.

    Expected config_json:
        {"user_id": 7, "output_key": "assigned_user_id"}
    """

    def execute(self):
        config = self.node.get_config()
        user_id = config.get('user_id')
        if not user_id:
            raise UserError(_('Assign Employee node "%s" is missing "user_id" in its configuration.') %
                             self.node.name)

        user = self.env['res.users'].sudo().browse(int(user_id))
        if not user.exists():
            raise UserError(_('Assign Employee node "%s" references user id %s, which does not exist.') % (
                self.node.name, user_id))

        ticket_id = self.context.get('ticket_id')
        if ticket_id:
            ticket = self.env['baramej.flow.ticket'].sudo().browse(int(ticket_id))
            if ticket.exists():
                ticket.write({'user_id': user.id})

        output_key = config.get('output_key', 'assigned_user_id')
        new_context = dict(self.context)
        new_context[output_key] = user.id
        return {'output': new_context}
