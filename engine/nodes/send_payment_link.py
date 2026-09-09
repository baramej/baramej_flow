# -*- coding: utf-8 -*-
"""Generates an Odoo payment link and pauses the workflow until payment is
confirmed. This is genuinely the least-verified node in the whole module — I
cannot test it against a live `payment` module + provider from this environment,
and payment.link.wizard's exact field names have shifted across Odoo versions.
Treat this as a solid best-effort starting point that needs real verification
against your actual Odoo 19 payment setup, not a "done" integration.
"""
from datetime import timedelta

from odoo import _, fields
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_template
from .base import BaseNodeExecutor


@register_node('send_payment_link')
class SendPaymentLinkExecutor(BaseNodeExecutor):
    """Uses payment.link.wizard — the same mechanism behind Sales/Invoicing's own
    "Send Payment Link" feature — to generate a payment link for the ticket's
    partner, posts it to the ticket's chatter, and pauses. Resuming happens via a
    scheduled cron (see models/eservice_ticket.py _cron_check_pending_payments)
    that polls for a matching 'done' payment.transaction, rather than a direct
    webhook hook — deliberately chosen because provider webhook payloads vary
    enough between Odoo versions/providers that hand-writing that integration
    without being able to test it felt like the wrong kind of confident. The cron
    interval determines how quickly a resume happens after actual payment — near-
    real-time, not instantaneous.

    Expected config_json:
        {
            "amount": 25.0,
            "currency": "OMR",
            "description": "Taxi plate approval fee",
            "dummy": true,
            "dummy_expiry_seconds": 30
        }
    "amount" and "description" both support {{dotted.path}} interpolation (e.g. a
    computed fee carried in context from an earlier node).

    "dummy": true switches this node into test mode — no payment.link.wizard call
    at all, no dependency on a configured provider. Instead it posts a plain
    message to the ticket's chat explaining a (fake) payment step, sets
    payment_link_expires_at to now + dummy_expiry_seconds (default 30), and pauses
    the same way the real path does. The applicant confirms via a "Confirm Payment
    (Test)" button on the portal ticket page — see
    models/eservice_ticket.py action_confirm_payment_test() and
    controllers/portal_controller.py portal_eservice_confirm_payment_test. This
    exists purely so the rest of the flow (approval, resubmission, portal chat)
    can be tested end to end without a real payment provider configured — remove
    "dummy": true (or set it false) once you have one.

    Requires the 'payment' module installed and at least one payment provider
    configured for the REAL (non-dummy) path — this node will raise a clear error
    if payment.link.wizard isn't available rather than failing silently.
    """

    def execute(self):
        config = self.node.get_config()
        ticket_id = self.context.get('ticket_id')
        if not ticket_id:
            raise UserError(_('Send Payment Link node "%s" requires context["ticket_id"].') % self.node.name)

        ticket = self.env['baramej.flow.ticket'].sudo().browse(int(ticket_id))
        if not ticket.exists():
            raise UserError(_('Send Payment Link node "%s" references ticket id %s, which does not exist.') %
                             (self.node.name, ticket_id))
        if not ticket.partner_id:
            raise UserError(_('Ticket "%s" has no linked partner to bill — Send Payment Link node "%s" '
                               'cannot proceed.') % (ticket.name, self.node.name))

        if config.get('dummy'):
            return self._execute_dummy(config, ticket)
        return self._execute_real(config, ticket)

    def _execute_dummy(self, config, ticket):
        expiry_seconds = int(config.get('dummy_expiry_seconds', 30))
        expires_at = fields.Datetime.now() + timedelta(seconds=expiry_seconds)

        ticket.write({
            'state': 'waiting_payment',
            'payment_link': False,  # deliberately blank - the portal template shows the test-confirm
                                     # button instead of a "Pay Now" link when payment_link is empty
            'payment_link_expires_at': expires_at,
        })
        ticket.message_post(
            body=_('[Test Mode] A payment step has been reached. This is a simulated payment — no real '
                   'provider is configured. The applicant can confirm it from their portal page within '
                   '%s seconds.') % expiry_seconds,
            subtype_xmlid='mail.mt_comment',
        )
        return {'output': self.context, 'pause': True}

    def _execute_real(self, config, ticket):
        if 'payment.link.wizard' not in self.env:
            raise UserError(_('Send Payment Link node "%s" needs the "payment" module installed with at '
                               'least one payment provider configured.') % self.node.name)

        amount_raw = str(config.get('amount', '0'))
        try:
            amount = float(render_template(amount_raw, self.context))
        except ValueError:
            raise UserError(_('Send Payment Link node "%s" resolved "amount" to a non-numeric value.') %
                             self.node.name)

        currency_code = config.get('currency', 'OMR')
        currency = self.env['res.currency'].sudo().search([('name', '=', currency_code)], limit=1)
        if not currency:
            raise UserError(_('Currency "%s" not found or not active in this Odoo instance.') % currency_code)

        description = render_template(config.get('description', ticket.name), self.context)

        try:
            wizard = self.env['payment.link.wizard'].sudo().with_context(
                active_model='res.partner', active_id=ticket.partner_id.id,
            ).create({
                'amount': amount,
                'currency_id': currency.id,
                'partner_id': ticket.partner_id.id,
                'description': description,
            })
            payment_link = wizard.link
        except Exception as exc:  # noqa: BLE001 - surface whatever payment.link.wizard raised verbatim;
            # this is exactly the kind of failure that needs the real message, not a generic one, since
            # it's most likely a payment-module configuration issue on the Odoo side.
            raise UserError(_('Send Payment Link node "%s" could not generate a payment link: %s') % (
                self.node.name, exc))

        ticket.write({'state': 'waiting_payment', 'payment_link': payment_link})
        ticket.message_post(body=_('A payment link has been generated: %s') % payment_link,
                             subtype_xmlid='mail.mt_comment')

        output_key = config.get('output_key', 'payment_link')
        new_context = dict(self.context)
        new_context[output_key] = payment_link
        return {'output': new_context, 'pause': True}