# -*- coding: utf-8 -*-
"""Portal pages for citizens to track their E-Service applications: a list at
/my/eservices and a detail page per ticket at /my/eservices/<id> with chatter and
a resubmission form when staff have asked for more information.

Follows Odoo's standard CustomerPortal extension pattern. Access control is
enforced explicitly (ticket.partner_id must match the logged-in user's partner)
rather than granting portal users direct ORM access to baramej.flow.ticket — this
keeps the security model simple: portal users never get a direct access-rights
grant on this model at all, everything goes through this sudo'd controller with an
explicit ownership check on every route.
"""
import json
import logging

from werkzeug.exceptions import Forbidden, NotFound

from odoo import fields, http
from odoo.http import request

from .attachment_utils import save_uploaded_file

try:
    from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
except ImportError:  # pragma: no cover - only happens if 'portal' isn't installed yet
    CustomerPortal = http.Controller
    portal_pager = None

_logger = logging.getLogger(__name__)

_PAGE_SIZE = 20


class FlowPortalController(CustomerPortal):

    def _flow_get_ticket_or_403(self, ticket_id):
        ticket = request.env['baramej.flow.ticket'].sudo().browse(ticket_id)
        if not ticket.exists():
            raise NotFound()
        if ticket.partner_id.id != request.env.user.partner_id.id:
            raise Forbidden()
        return ticket

    @http.route('/my/eservices', type='http', auth='user', website=True)
    def portal_my_eservices(self, page=1, **kwargs):
        partner = request.env.user.partner_id
        Ticket = request.env['baramej.flow.ticket'].sudo()
        domain = [('partner_id', '=', partner.id)]

        total = Ticket.search_count(domain)
        page = int(page)
        offset = (page - 1) * _PAGE_SIZE

        tickets = Ticket.search(domain, order='create_date desc', limit=_PAGE_SIZE, offset=offset)

        values = {
            'tickets': tickets,
            'page_name': 'eservices',
            'default_url': '/my/eservices',
            'total': total,
            'page': page,
            'page_size': _PAGE_SIZE,
        }
        if portal_pager:
            values['pager'] = portal_pager(url='/my/eservices', total=total, page=page, step=_PAGE_SIZE)

        return request.render('baramej_flow.portal_my_eservices', values)

    @http.route('/my/eservices/<int:ticket_id>', type='http', auth='user', website=True)
    def portal_eservice_detail(self, ticket_id, **kwargs):
        ticket = self._flow_get_ticket_or_403(ticket_id)
        try:
            raw_data = json.loads(ticket.submitted_data_json or '{}')
        except ValueError:
            raw_data = {}

        # Built here (not with isinstance()/dict logic inline in the QWeb
        # template) since QWeb's restricted eval context for inline expressions
        # isn't guaranteed to expose isinstance() — same reasoning as
        # eservice_ticket.py's _compute_submitted_data_html on the backend side,
        # kept consistent between the two.
        label_by_name = {f.name: f.label for f in ticket.form_id.field_ids} if ticket.form_id else {}
        submitted_rows = []
        for key, value in raw_data.items():
            row = {'label': label_by_name.get(key, key)}
            if isinstance(value, dict) and value.get('attachment_id'):
                row['is_file'] = True
                row['attachment_id'] = value['attachment_id']
                row['filename'] = value.get('filename') or 'Download'
            else:
                row['is_file'] = False
                row['value'] = value
            submitted_rows.append(row)

        # Rendered server-side from data the controller already holds under sudo(),
        # rather than relying on portal.message_thread's own client-side widget —
        # that widget makes its own RPC calls as the logged-in portal user, whose
        # exact access-rule chain for a custom model isn't something verifiable
        # from this environment. This guarantees every customer-facing message is
        # shown, since it doesn't depend on that separate code path at all.
        comment_subtype = request.env.ref('mail.mt_comment', raise_if_not_found=False)
        messages = ticket.message_ids.filtered(
            lambda m: comment_subtype and m.subtype_id == comment_subtype
        ).sorted(key=lambda m: m.date)

        payment_link_expired = False
        if ticket.payment_link_expires_at:
            payment_link_expired = fields.Datetime.now() > ticket.payment_link_expires_at

        values = {
            'ticket': ticket,
            'submitted_rows': submitted_rows,
            'messages': messages,
            'payment_link_expired': payment_link_expired,
            'request_lines': ticket.info_request_line_ids,
            'page_name': 'eservice_detail',
        }
        return request.render('baramej_flow.portal_eservice_detail', values)

    @http.route('/my/eservices/<int:ticket_id>/resubmit', type='http', auth='user', methods=['POST'], csrf=True)
    def portal_eservice_resubmit(self, ticket_id, **kwargs):
        ticket = self._flow_get_ticket_or_403(ticket_id)
        if ticket.state != 'waiting_info':
            raise Forbidden()

        message = kwargs.pop('resubmit_message', None)
        extra_data = {}

        request_lines = ticket.info_request_line_ids
        if request_lines:
            # Staff requested specific fields via the wizard — only process
            # exactly those, matching by their technical name. Each line is
            # either a brand-new key or an existing one being corrected; either
            # way, action_portal_resubmit below just overwrites that key.
            missing_required = []
            for line in request_lines:
                if line.field_type == 'file':
                    file_storage = request.httprequest.files.get(line.name)
                    if file_storage and file_storage.filename:
                        extra_data[line.name] = save_uploaded_file(
                            request.env, file_storage, 'baramej.flow.ticket', ticket.id)
                    elif line.required:
                        missing_required.append(line.label)
                else:
                    value = kwargs.get(line.name)
                    if value:
                        extra_data[line.name] = value
                    elif line.required:
                        missing_required.append(line.label)

            if missing_required:
                # Re-render rather than silently dropping the submission — same
                # principle as the initial form's own required-field check.
                ticket_fresh = self._flow_get_ticket_or_403(ticket_id)
                return request.render('baramej_flow.portal_eservice_detail', {
                    'ticket': ticket_fresh,
                    'submitted_rows': [],
                    'messages': ticket_fresh.message_ids,
                    'payment_link_expired': False,
                    'request_lines': request_lines,
                    'resubmit_error': 'Please fill in: %s' % ', '.join(missing_required),
                    'page_name': 'eservice_detail',
                })
        else:
            # No specific fields were requested — fall back to accepting whatever
            # extra form fields were posted (the original, pre-this-feature
            # behavior, still useful for a plain "just tell me more" request with
            # no defined fields).
            extra_data = {key: value for key, value in kwargs.items() if key not in ('csrf_token',)}

        ticket.action_portal_resubmit(extra_data, message=message)

        return request.redirect('/my/eservices/%s' % ticket_id)

    @http.route('/my/eservices/<int:ticket_id>/confirm-payment-test', type='http', auth='user',
                methods=['POST'], csrf=True)
    def portal_eservice_confirm_payment_test(self, ticket_id, **kwargs):
        """Dummy payment confirmation for testing without a real payment provider
        configured — see engine/nodes/send_payment_link.py 'dummy' mode. Not meant
        to survive into a production deployment with a real provider; it's a
        stand-in specifically so the rest of the flow can be tested end-to-end
        right now."""
        ticket = self._flow_get_ticket_or_403(ticket_id)
        ticket.action_confirm_payment_test()
        return request.redirect('/my/eservices/%s' % ticket_id)
