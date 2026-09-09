# -*- coding: utf-8 -*-
from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from .base import BaseNodeExecutor


@register_node('wait_for_resubmission')
class WaitForResubmissionExecutor(BaseNodeExecutor):
    """Pauses until the applicant resubmits the requested additional information
    through the portal (see models/eservice_ticket.py action_portal_resubmit,
    which merges the new data into context and calls WorkflowEngine.resume() with
    no branch — this node typically has a single 'default' outgoing edge, commonly
    wired straight back to the human_approval node that requested the info, so
    staff can review again).

    Sets the ticket to 'waiting_info' so the portal UI shows the right status and
    prompts the applicant for what's missing.

    Requires context['ticket_id'], same as human_approval.
    """

    def execute(self):
        ticket_id = self.context.get('ticket_id')
        if not ticket_id:
            raise UserError(_('Wait For Resubmission node "%s" requires context["ticket_id"].') % self.node.name)

        ticket = self.env['baramej.flow.ticket'].sudo().browse(int(ticket_id))
        if not ticket.exists():
            raise UserError(_('Wait For Resubmission node "%s" references ticket id %s, which does not '
                               'exist.') % (self.node.name, ticket_id))

        ticket.write({'state': 'waiting_info'})
        return {'output': self.context, 'pause': True}
