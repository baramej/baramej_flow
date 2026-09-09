# -*- coding: utf-8 -*-
"""The E-Service ticket: the case object created when a logged-in portal user
submits a require_login form (see models/workflow_form.py, controllers/
form_controller.py). This is deliberately a first-party model, not a Helpdesk
inheritance — Helpdesk's data model is shaped around IT support tickets (SLAs,
teams, stages tuned for that use case) and bending it to fit generic government
e-services (approve/reject/request-info against a citizen-submitted application)
would mean fighting its assumptions more than reusing them. This model borrows the
same portal/chatter *pattern* Helpdesk uses (mail.thread + portal.mixin), which is
the actually-reusable part, without inheriting Helpdesk's ticket-specific shape.
"""
import json
import logging
from datetime import timedelta

from markupsafe import escape
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class FlowTicket(models.Model):
    _name = 'baramej.flow.ticket'
    _description = 'Baramej Flow - E-Service Ticket'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        default=lambda self: self.env['ir.sequence'].next_by_code('baramej.flow.ticket') or _('New'),
        readonly=True, copy=False, required=True,
    )
    form_id = fields.Many2one('baramej.flow.form', readonly=True, ondelete='restrict')
    workflow_id = fields.Many2one(
        'baramej.flow.workflow', related='form_id.workflow_id', store=True, readonly=True)
    execution_id = fields.Many2one('baramej.flow.execution', readonly=True, copy=False)

    partner_id = fields.Many2one(
        'res.partner', string='Submitted By', required=True, index=True, tracking=True,
        help='The logged-in portal user who submitted the form. Required because require_login=True '
             'forms are the only ones that create a ticket — see workflow_form.py.',
    )
    user_id = fields.Many2one('res.users', string='Assigned To', tracking=True)

    submitted_data_json = fields.Text(string='Submitted Data')
    submitted_data_pretty = fields.Text(compute='_compute_submitted_data_pretty', string='Submitted Data (Formatted)')
    submitted_data_html = fields.Html(
        compute='_compute_submitted_data_html', string='Submitted Data', sanitize=False,
        help='Same data as submitted_data_json, rendered as a proper table with field labels (not '
             'technical names) and clickable download links for any uploaded files. sanitize=False is '
             'safe here because every value going into this HTML is built and escaped by our own compute '
             'method below, never inserted from user input directly.',
    )
    info_request_line_ids = fields.One2many(
        'baramej.flow.ticket.info.request.line', 'ticket_id', string='Pending Info Requests',
        help='Fields staff have specifically asked the applicant to fill in or correct — set via the '
             'Request More Info wizard. Cleared automatically once the applicant resubmits.',
    )

    state = fields.Selection(
        [
            ('submitted', 'Submitted'),
            ('in_review', 'In Review'),
            ('waiting_info', 'Waiting on Applicant'),
            ('waiting_payment', 'Waiting on Payment'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('done', 'Done'),
        ],
        default='submitted', tracking=True, required=True,
    )
    rejection_reason = fields.Text(readonly=True)
    payment_link = fields.Char(readonly=True)
    payment_link_expires_at = fields.Datetime(readonly=True, copy=False)

    @api.depends('submitted_data_json')
    def _compute_submitted_data_pretty(self):
        for rec in self:
            try:
                data = json.loads(rec.submitted_data_json or '{}')
                rec.submitted_data_pretty = '\n'.join('%s: %s' % (k, v) for k, v in data.items())
            except ValueError:
                rec.submitted_data_pretty = rec.submitted_data_json

    @api.depends('submitted_data_json', 'form_id.field_ids.name', 'form_id.field_ids.label')
    def _compute_submitted_data_html(self):
        for rec in self:
            try:
                data = json.loads(rec.submitted_data_json or '{}')
            except ValueError:
                rec.submitted_data_html = '<pre>%s</pre>' % escape(rec.submitted_data_json or '')
                continue

            if not data:
                rec.submitted_data_html = '<p style="color:#888;">No data submitted.</p>'
                continue

            label_by_name = {f.name: f.label for f in rec.form_id.field_ids} if rec.form_id else {}
            rows = []
            for key, value in data.items():
                label = label_by_name.get(key, key)
                if isinstance(value, dict) and value.get('attachment_id'):
                    cell = '<a href="/web/content/%s?download=true" target="_blank">%s &#8595;</a>' % (
                        int(value['attachment_id']), escape(value.get('filename') or 'Download'))
                else:
                    cell = escape('' if value is None else str(value))
                rows.append(
                    '<tr>'
                    '<th style="text-align:left;vertical-align:top;padding:6px 16px 6px 0;'
                    'white-space:nowrap;color:#555;">%s</th>'
                    '<td style="padding:6px 0;">%s</td>'
                    '</tr>' % (escape(label), cell)
                )
            rec.submitted_data_html = '<table style="border-collapse:collapse;width:100%%;">%s</table>' % ''.join(rows)

    def _compute_access_url(self):
        super()._compute_access_url()
        for rec in self:
            rec.access_url = '/my/eservices/%s' % rec.id

    current_approver_id = fields.Many2one(
        'res.users', string='Waiting On', compute='_compute_current_approver_id',
        help='If the paused step has a specific Approver configured, this shows who — otherwise blank, '
             'meaning any Flow User can act on it.',
    )
    current_approve_mode = fields.Selection(
        [('approve', 'Approve'), ('forward', 'Forward')],
        compute='_compute_current_approver_id', default='approve',
        help='Drives the button label on the ticket form: "approve" shows "Approve", "forward" shows '
             '"Send for Approval" — set per-node via human_approval config {"approve_mode": "forward"}. '
             'Both call the same action_approve() underneath; this only changes what the wording implies '
             'to whoever is looking at the button.',
    )

    @api.depends('execution_id.state', 'execution_id.waiting_node_id')
    def _compute_current_approver_id(self):
        for rec in self:
            node = rec.execution_id.waiting_node_id if rec.execution_id and rec.execution_id.state == 'waiting' else None
            config = node.get_config() if node and node.node_type == 'human_approval' else {}
            rec.current_approver_id = config.get('approver_user_id') or False
            mode = config.get('approve_mode', 'approve')
            rec.current_approve_mode = mode if mode in ('approve', 'forward') else 'approve'

    def _check_approver_or_raise(self):
        """If the node currently paused at is a human_approval with a specific
        approver_user_id configured, ONLY that exact user may act — no group-based
        override, including Flow Manager. That bypass existed in an earlier pass
        and was the actual cause of "the restriction did nothing": a tester with
        Flow Manager access could always click through regardless of who was
        configured as the approver. Removed deliberately; if you need a genuine
        admin-override path later, it should be a separate, explicit action, not a
        silent exception to this check. No approver configured at all = any Flow
        User can act, unchanged from before."""
        self.ensure_one()
        if self.current_approver_id and self.current_approver_id != self.env.user:
            raise UserError(_('Only %s can act on this approval step.') % self.current_approver_id.name)

    # ------------------------------------------------------------------
    # Staff actions — these resume the paused workflow execution
    # ------------------------------------------------------------------
    def action_approve(self):
        self.ensure_one()
        self._check_approver_or_raise()
        self._resume_execution(branch='approved')
        # Only default to 'approved' if the resume ran all the way to a genuine
        # dead end (execution finished 'success') without landing on another
        # paused node. Checking execution state here instead of just re-reading
        # ticket.state matters once workflows chain multiple Human Approval nodes:
        # a second node pausing correctly sets ticket.state back to 'in_review'
        # for ITS OWN wait, and the old version of this check (which just asked
        # "is state still in_review?") would wrongly stomp that back to
        # 'approved', treating a legitimate second-stage pause as if nothing
        # had happened.
        self.invalidate_recordset(['state'])
        if self.execution_id.state == 'success' and self.state == 'in_review':
            self.write({'state': 'approved'})

    def action_reject(self, reason):
        self.ensure_one()
        self._check_approver_or_raise()
        if not reason:
            raise UserError(_('A rejection reason is required.'))
        self.write({'rejection_reason': reason, 'state': 'rejected'})
        self.message_post(body=_('Application rejected: %s') % reason, subtype_xmlid='mail.mt_comment')
        self._resume_execution(branch='rejected', context_update={'rejection_reason': reason})

    def action_request_info(self, message, lines_data=None):
        self.ensure_one()
        self._check_approver_or_raise()
        if not message:
            raise UserError(_('A message explaining what\'s needed is required.'))
        self.write({'state': 'waiting_info'})
        self.message_post(body=message, subtype_xmlid='mail.mt_comment')
        # Clear any stale, previously-unresolved requests before recording the new
        # ones — a fresh Request More Info always replaces what was being asked
        # for, rather than accumulating old asks alongside new ones.
        self.info_request_line_ids.unlink()
        if lines_data:
            self.env['baramej.flow.ticket.info.request.line'].sudo().create(
                [dict(line, ticket_id=self.id) for line in lines_data]
            )
            self._resume_execution(branch='more_info', context_update={'info_requested_message': message})

    def action_request_info_internal(self, message):
            """Used when an approver needs clarification from the assigned reviewer,
            not the customer — e.g. Ministry Approval asking Reviewer Screening for
            more detail. Posted as an internal note (mail.mt_note), never
            mail.mt_comment, so it can never appear in the customer-facing portal
            message list (see controllers/portal_controller.py, which only ever
            pulls mail.mt_comment messages) — this is the actual mechanism that
            keeps the reviewer/approver conversation hidden from the applicant, not
            a separate visibility flag.

            Deliberately does NOT touch ticket.state or info_request_line_ids —
            those are customer-facing concerns. Whichever node picks up the
            'more_info' branch (per your wiring, Ministry Approval's more_info edge
            routes to Assign to Reviewer, which reassigns and lands back on Reviewer
            Screening) sets its own state on pause, same as any other resume.
            """
            self.ensure_one()
            self._check_approver_or_raise()
            if not message:
                raise UserError(_('A message explaining what you need from the reviewer is required.'))
            self.message_post(body=message, subtype_xmlid='mail.mt_note')
            self._resume_execution(branch='more_info', context_update={'info_requested_message': message})

    def action_open_reject_wizard(self):
            self.ensure_one()
            return {
                'type': 'ir.actions.act_window',
                'name': _('Reject Application'),
                'res_model': 'baramej.flow.ticket.reject.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_ticket_id': self.id},
            }

    def action_open_request_info_wizard(self):
            self.ensure_one()
            return {
                'type': 'ir.actions.act_window',
                'name': _('Request More Information'),
                'res_model': 'baramej.flow.ticket.request.info.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_ticket_id': self.id},
            }

    def action_open_request_info_from_reviewer_wizard(self):
            self.ensure_one()
            return {
                'type': 'ir.actions.act_window',
                'name': _('Request Info from Reviewer'),
                'res_model': 'baramej.flow.ticket.request.info.from.reviewer.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_ticket_id': self.id},
            }

    # ------------------------------------------------------------------
    # Portal action — citizen resubmitting after a "more info" request
    # ------------------------------------------------------------------
    def action_portal_resubmit(self, extra_data, message=None):
        """Called from the portal controller when the applicant adds the requested
        information and resubmits. Merges the new fields into submitted_data_json
        (existing keys are overwritten, everything else preserved) and resumes the
        execution that's waiting at a wait_for_resubmission node."""
        self.ensure_one()
        data = json.loads(self.submitted_data_json or '{}')
        data.update(extra_data or {})
        self.write({'submitted_data_json': json.dumps(data)})
        if message:
            self.message_post(body=message, author_id=self.partner_id.id, subtype_xmlid='mail.mt_comment')
        # The requested fields have now been provided — clear them so the portal
        # stops prompting for them and the backend "Pending Info Requests" list
        # goes empty until the next Request More Info.
        self.info_request_line_ids.unlink()
        self._resume_execution(context_update={'form': data})

    # ------------------------------------------------------------------
    def _resume_execution(self, branch=None, context_update=None):
        self.ensure_one()
        if not self.execution_id or self.execution_id.state != 'waiting':
            raise UserError(_('This ticket has no paused workflow step to resume — it may already be '
                               'past this stage, or the workflow may have finished/failed already.'))
        from ..engine.executor import WorkflowEngine
        WorkflowEngine(self.env).resume(self.execution_id, branch=branch, context_update=context_update)

    # ------------------------------------------------------------------
    # Manual payment trigger — decoupled from the workflow graph entirely, so
    # either the reviewer or the approver (or anyone with Flow User access) can
    # send payment whenever they're ready after approval, rather than it firing
    # automatically the instant someone clicks Approve. If you'd rather have it
    # fire automatically as part of the approval step, wire a send_payment_link
    # node onto the relevant human_approval node's 'approved' branch instead of
    # using this button — both paths are supported, pick whichever fits.
    # ------------------------------------------------------------------
    def action_send_payment_link_manual(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_('Payment can only be sent once the application is approved.'))
        expiry_seconds = 30
        self.write({
            'state': 'waiting_payment',
            'payment_link': False,
            'payment_link_expires_at': fields.Datetime.now() + timedelta(seconds=expiry_seconds),
        })
        self.message_post(
            body=_('[Test Mode] A payment link has been sent manually. The applicant can confirm it from '
                   'their portal page within %s seconds.') % expiry_seconds,
            subtype_xmlid='mail.mt_comment',
        )

    # ------------------------------------------------------------------
    # Test/dummy payment confirmation — see engine/nodes/send_payment_link.py
    # "dummy" mode. Not a real payment integration; a stand-in so the rest of the
    # flow (staff decisions, resubmission, portal chat) can be exercised end to
    # end without a configured payment provider.
    # ------------------------------------------------------------------
    def action_confirm_payment_test(self):
        self.ensure_one()
        if self.state != 'waiting_payment':
            raise UserError(_('This ticket is not waiting on a (test) payment.'))
        if self.payment_link_expires_at and fields.Datetime.now() > self.payment_link_expires_at:
            raise UserError(_('This test payment link has expired.'))
        self.message_post(body=_('[Test Mode] Payment confirmed by applicant.'), subtype_xmlid='mail.mt_comment')
        # Only resume if a graph node is actually paused waiting on this payment
        # (the automatic, in-graph send_payment_link path). If payment was
        # triggered manually via action_send_payment_link_manual() after the
        # workflow had already finished (execution.state == 'success'), there's
        # nothing to resume — just mark the ticket done directly.
        if self.execution_id and self.execution_id.state == 'waiting':
            self._resume_execution()
        self.write({'state': 'done'})

    # ------------------------------------------------------------------
    # Payment confirmation polling (see engine/nodes/send_payment_link.py docstring
    # for why this is a poll rather than a direct webhook hook)
    # ------------------------------------------------------------------
    @api.model
    def _cron_check_pending_payments(self):
        tickets = self.sudo().search([('state', '=', 'waiting_payment')])
        for ticket in tickets:
            if not ticket.execution_id or ticket.execution_id.state != 'waiting':
                continue
            # Heuristic match: most recent 'done' transaction for this partner.
            # This is an MVP simplification, not an exact link — a hardened version
            # should have send_payment_link.py store the specific
            # payment.transaction id (or reference) on the ticket and match on
            # that exactly instead of partner + recency.
            transaction = self.env['payment.transaction'].sudo().search(
                [('partner_id', '=', ticket.partner_id.id), ('state', '=', 'done')],
                order='create_date desc', limit=1,
            )
            if transaction and transaction.create_date >= ticket.write_date:
                from ..engine.executor import WorkflowEngine
                WorkflowEngine(self.env).resume(ticket.execution_id)
                ticket.write({'state': 'done'})
                ticket.message_post(body=_('Payment confirmed.'), subtype_xmlid='mail.mt_comment')


class FlowTicketInfoRequestLine(models.Model):
    """A single field staff have asked the applicant to provide or correct.
    Persistent (unlike the wizard lines below) — created when staff confirm the
    Request More Info wizard, read by the portal controller to render the right
    inputs, and deleted once the applicant resubmits (see
    action_portal_resubmit above)."""
    _name = 'baramej.flow.ticket.info.request.line'
    _description = 'Baramej Flow - Requested Info Field'
    _order = 'sequence, id'

    ticket_id = fields.Many2one('baramej.flow.ticket', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(
        required=True,
        help='Technical name — becomes the submitted_data_json key on resubmission. If this matches a '
             'key that already exists (an "update" request), resubmission overwrites that value in place '
             'rather than adding a new one.',
    )
    label = fields.Char(required=True, help='Shown to the applicant on the portal.')
    field_type = fields.Selection(
        [
            ('char', 'Single Line Text'),
            ('text', 'Multi-line Text'),
            ('email', 'Email'),
            ('integer', 'Number'),
            ('float', 'Decimal Number'),
            ('date', 'Date'),
            ('datetime', 'Date & Time'),
            ('boolean', 'Yes / No'),
            ('selection', 'Dropdown'),
            ('file', 'File Upload'),
        ],
        required=True, default='char',
    )
    selection_options = fields.Char(help='Comma-separated, only used when Field Type is Dropdown.')
    required = fields.Boolean(default=True)


class FlowTicketRejectWizard(models.TransientModel):
    _name = 'baramej.flow.ticket.reject.wizard'
    _description = 'Baramej Flow - Reject Ticket Wizard'

    ticket_id = fields.Many2one('baramej.flow.ticket', required=True)
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.ticket_id.action_reject(self.reason)


class FlowTicketRequestInfoWizardLine(models.TransientModel):
    """One row in the Request More Info wizard's field list. Two modes:
    - 'new': staff manually defines a brand-new field (name/label/type) that
      isn't part of the original form — e.g. "Passport Scanned Copy".
    - 'update': staff picks an EXISTING field from the original form via
      existing_field_id; name/label/type auto-fill from it (onchange below), so
      resubmission overwrites that field's value in place rather than adding a
      new key.
    """
    _name = 'baramej.flow.ticket.request.info.wizard.line'
    _description = 'Baramej Flow - Request Info Wizard Line'

    wizard_id = fields.Many2one('baramej.flow.ticket.request.info.wizard', required=True, ondelete='cascade')
    mode = fields.Selection(
        [('new', 'New Field'), ('update', 'Update Existing Field')], default='new', required=True)
    existing_field_id = fields.Many2one(
        'baramej.flow.form.field', string='Existing Field',
        domain="[('form_id', '=', parent.form_id)]",
        help='Only used when Mode is "Update Existing Field" — pick from the fields already on this '
             'ticket\'s original form.',
    )
    name = fields.Char(string='Technical Name')
    label = fields.Char(string='Label')
    field_type = fields.Selection(
        [
            ('char', 'Single Line Text'),
            ('text', 'Multi-line Text'),
            ('email', 'Email'),
            ('integer', 'Number'),
            ('float', 'Decimal Number'),
            ('date', 'Date'),
            ('datetime', 'Date & Time'),
            ('boolean', 'Yes / No'),
            ('selection', 'Dropdown'),
            ('file', 'File Upload'),
        ],
        default='char',
    )
    selection_options = fields.Char()
    required = fields.Boolean(default=True)

    @api.onchange('existing_field_id')
    def _onchange_existing_field_id(self):
        for rec in self:
            if rec.existing_field_id:
                rec.name = rec.existing_field_id.name
                rec.label = rec.existing_field_id.label
                rec.field_type = rec.existing_field_id.field_type
                rec.selection_options = rec.existing_field_id.selection_options

    @api.constrains('mode', 'name', 'existing_field_id')
    def _check_mode_consistency(self):
        for rec in self:
            if rec.mode == 'update' and not rec.existing_field_id:
                raise ValidationError(_('Select an Existing Field for any row set to "Update Existing '
                                         'Field".'))
            if rec.mode == 'new' and not rec.name:
                raise ValidationError(_('Enter a Technical Name for any row set to "New Field".'))


class FlowTicketRequestInfoWizard(models.TransientModel):
    _name = 'baramej.flow.ticket.request.info.wizard'
    _description = 'Baramej Flow - Request More Info Wizard'

    ticket_id = fields.Many2one('baramej.flow.ticket', required=True)
    form_id = fields.Many2one(
        related='ticket_id.form_id', readonly=True,
        help='Exposed so line_ids can restrict the "Existing Field" picker to this ticket\'s own form.',
    )
    message = fields.Text(required=True, help='Shown to the applicant, explaining what\'s needed.')
    line_ids = fields.One2many(
        'baramej.flow.ticket.request.info.wizard.line', 'wizard_id', string='Requested Fields',
        help='Optional. Add specific fields you need — new ones not on the original form, or existing '
             'ones you want corrected. Leave empty and the applicant just sees your message with a '
             'free-text reply box.',
    )

    def action_confirm(self):
        self.ensure_one()
        lines_data = [{
            'name': line.name,
            'label': line.label,
            'field_type': line.field_type,
            'selection_options': line.selection_options,
            'required': line.required,
        } for line in self.line_ids]
        self.ticket_id.action_request_info(self.message, lines_data)


class FlowTicketRequestInfoFromReviewerWizard(models.TransientModel):
    """Simple by design — unlike the customer-facing wizard, this has no
    add-a-field mechanism, since asking an internal colleague for clarification
    doesn't need a structured resubmission form the way asking a citizen does.
    Just a message, posted as an internal note (see
    FlowTicket.action_request_info_internal)."""
    _name = 'baramej.flow.ticket.request.info.from.reviewer.wizard'
    _description = 'Baramej Flow - Request Info From Reviewer Wizard'

    ticket_id = fields.Many2one('baramej.flow.ticket', required=True)
    message = fields.Text(
        required=True,
        help='Internal note to the reviewer — never shown to the applicant, regardless of portal access.',
    )

    def action_confirm(self):
        self.ensure_one()
        self.ticket_id.action_request_info_internal(self.message)