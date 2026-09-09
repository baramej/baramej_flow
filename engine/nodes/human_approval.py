# -*- coding: utf-8 -*-
from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from .base import BaseNodeExecutor


@register_node('human_approval')
class HumanApprovalExecutor(BaseNodeExecutor):
    """Pauses the workflow, waiting for a staff member to click Approve, Reject, or
    Request More Info on the linked baramej.flow.ticket. Those three actions (see
    models/eservice_ticket.py action_approve/action_reject/action_request_info)
    are what call WorkflowEngine.resume() — this node itself does nothing but pause
    and set the ticket's visible status.

    Requires context['ticket_id'] — meaning this node only makes sense downstream
    of a ticket already existing, which in the E-Service pattern means downstream
    of a trigger_form node on a require_login=True form (see workflow_form.py /
    form_controller.py, which create the ticket automatically on submission).

        Expected config_json:
        {"ticket_state_while_waiting": "in_review", "approver_user_id": 7}

    "approver_user_id" is optional. If set, only that user (or a Flow Manager) can
    act on this approval step — enforced in models/eservice_ticket.py
    _check_approver_or_raise() — and the ticket's "Assigned To" is set to them, so
    it's immediately visible who's expected to review. Leave unset to allow any
    Flow User to act, as before.

    For multi-level approval, chain multiple Human Approval nodes in sequence,
    each with a different approver_user_id, wired together via the 'approved'
    branch (Level 1 approved -> Level 2 node -> Level 2 approved -> next step).
    There's no separate "number of levels" setting — the chain length on the
    canvas *is* the number of levels, which keeps this consistent with how every
    other multi-step pattern in this module works (build it as nodes+edges, not a
    hidden counter).

    Wire this node's outgoing edges with condition_branch = 'approved' / 'rejected'
    / 'more_info' to route each staff decision to a different next step.
    """

    def execute(self):
        config = self.node.get_config()
        ticket_id = self.context.get('ticket_id')
        if not ticket_id:
            raise UserError(_('Human Approval node "%s" requires context["ticket_id"] — this workflow needs '
                               'to run against a baramej.flow.ticket. See the E-Service pattern in the '
                               'module README.') % self.node.name)

        ticket = self.env['baramej.flow.ticket'].sudo().browse(int(ticket_id))
        if not ticket.exists():
            raise UserError(_('Human Approval node "%s" references ticket id %s, which does not exist.') % (
                self.node.name, ticket_id))

        new_state = config.get('ticket_state_while_waiting', 'in_review')
        approver_user_id = config.get('approver_user_id')
        write_vals = {'state': new_state}
        if approver_user_id:
            write_vals['user_id'] = int(approver_user_id)
        ticket.write(write_vals)

        return {'output': self.context, 'pause': True}
