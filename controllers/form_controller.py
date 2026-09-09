# -*- coding: utf-8 -*-
"""Renders and processes the public-facing forms defined via baramej.flow.form.
Deliberately does NOT depend on the 'website' module — it returns plain, hand-built
HTML rather than a QWeb website template, keeping this module's dependency footprint
to base + mail. That's a reasonable trade for Phase 2; if a client wants the form to
visually match a themed Odoo website later, that's a straightforward follow-up to
swap this for a QWeb template without touching the data model.
"""
import json
import logging
from markupsafe import Markup
from werkzeug.exceptions import NotFound
from werkzeug.utils import redirect

from odoo import http
from odoo.http import request

from .attachment_utils import save_uploaded_file

_logger = logging.getLogger(__name__)

_BRAND_COLOR = '#714B67'  # matches Baramej's Odoo UI styling convention

_HONEYPOT_FIELD = 'flow_hp'  # hidden field; real users never fill this in, bots often do


class FlowFormController(http.Controller):

    @http.route('/flow/form/<string:uuid>', type='http', auth='public', methods=['GET'], csrf=True, website=False)
    def render_form(self, uuid, **kwargs):
        env = request.env
        form = env['baramej.flow.form'].sudo().search([
            ('access_uuid', '=', uuid),
            ('is_published', '=', True),
        ], limit=1)

        if not form:
            raise NotFound()

        if form.require_login and request.env.user._is_public():
            return redirect('/web/login?redirect=%s' % request.httprequest.path)

        html = self._render_form_html(form)
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

    @http.route('/flow/form/<string:uuid>/submit', type='http', auth='public', methods=['POST'], csrf=False)
    def submit_form(self, uuid, **kwargs):
        env = request.env
        form = env['baramej.flow.form'].sudo().search([
            ('access_uuid', '=', uuid),
            ('is_published', '=', True),
        ], limit=1)

        if not form:
            raise NotFound()

        if form.require_login and request.env.user._is_public():
            return redirect('/web/login?redirect=/flow/form/%s' % uuid)

        # Honeypot: if the hidden field is filled, silently pretend success without
        # running the workflow. Basic bot mitigation until Phase 6 adds real rate
        # limiting — not a substitute for it.
        if kwargs.get(_HONEYPOT_FIELD):
            _logger.info('Baramej Flow: form %s submission rejected (honeypot triggered)', form.id)
            return request.make_response(
                self._render_thankyou_html(form),
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )

        missing_required = [
            f.label for f in form.field_ids
            if f.field_type != 'file' and f.required and not (kwargs.get(f.name) or '').strip()
        ]
        missing_required += [
            f.label for f in form.field_ids
            if f.field_type == 'file' and f.required and not (
                    request.httprequest.files.get(f.name) and request.httprequest.files.get(f.name).filename
            )
        ]
        if missing_required:
            html = self._render_form_html(
                form,
                error='Please fill in: %s' % ', '.join(missing_required),
                submitted_values=kwargs,
            )
            return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

        file_fields = form.field_ids.filtered(lambda f: f.field_type == 'file')
        non_file_fields = form.field_ids - file_fields
        submitted_values = {f.name: kwargs.get(f.name) for f in non_file_fields}

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(env)

        if form.require_login:
            # E-Service pattern: create a tracked ticket owned by the logged-in
            # user's partner, then run the workflow against it. This is the branch
            # that unlocks portal status tracking, staff approve/reject/request-
            # info, and chatter — see models/eservice_ticket.py.
            #
            # The ticket is created BEFORE file uploads are processed, on purpose:
            # ir.attachment needs a real res_id to link to, and the ticket is that
            # anchor. submitted_data_json gets written twice (once without file
            # data, once after) rather than trying to attach files to a record
            # that doesn't exist yet.
            ticket = env['baramej.flow.ticket'].sudo().create({
                'form_id': form.id,
                'partner_id': request.env.user.partner_id.id,
                'submitted_data_json': json.dumps(submitted_values),
                'state': 'submitted',
            })

            for f in file_fields:
                file_storage = request.httprequest.files.get(f.name)
                if file_storage and file_storage.filename:
                    submitted_values[f.name] = save_uploaded_file(
                        env, file_storage, 'baramej.flow.ticket', ticket.id)
            if file_fields:
                ticket.write({'submitted_data_json': json.dumps(submitted_values)})

            execution = engine.run(
                form.workflow_id, trigger_source='form',
                initial_context={'form': submitted_values, 'ticket_id': ticket.id},
            )
            ticket.execution_id = execution.id
            return redirect('/my/eservices/%s' % ticket.id)

        # Backward-compatible path: forms built before this feature (or explicitly
        # set to require_login=False) keep working exactly as they did in Phase 2 —
        # no ticket, no portal tracking, just run the workflow and show the plain
        # thank-you page. File fields are still accepted here, but attach to the
        # form itself (res_model='baramej.flow.form') rather than a ticket, since
        # there isn't one on this path — a real limitation if you need staff to
        # browse these files later; the E-Service (require_login=True) path is
        # the one built for that.
        for f in file_fields:
            file_storage = request.httprequest.files.get(f.name)
            if file_storage and file_storage.filename:
                submitted_values[f.name] = save_uploaded_file(env, file_storage, 'baramej.flow.form', form.id)

        engine.run(form.workflow_id, trigger_source='form', initial_context={'form': submitted_values})
        html = self._render_thankyou_html(form)
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

    # ------------------------------------------------------------------
    # HTML rendering helpers
    # ------------------------------------------------------------------
    def _render_form_html(self, form, error=None, submitted_values=None):
        submitted_values = submitted_values or {}
        fields_html = ''.join(
            self._render_field_html(f, submitted_values.get(f.name, ''))
            for f in form.field_ids
        )
        error_html = (
                '<div style="background:#fdecea;color:#b3261e;padding:12px 16px;'
                'border-radius:8px;margin-bottom:16px;font-size:14px;">%s</div>' % Markup.escape(error)
        ) if error else ''

        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>%(title)s</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background:#f5f5f7;
         margin:0; padding:40px 16px; color:#1f1f1f; }
  .card { max-width:560px; margin:0 auto; background:#fff; border-radius:12px; padding:32px;
          box-shadow:0 2px 12px rgba(0,0,0,0.08); }
  h1 { font-size:22px; margin:0 0 8px; color:%(brand)s; }
  .desc { color:#5a5a5a; font-size:14px; margin-bottom:24px; }
  label { display:block; font-size:13px; font-weight:600; margin:16px 0 6px; }
  input, select, textarea {
    width:100%%; box-sizing:border-box; padding:10px 12px; border:1px solid #d7d7db;
    border-radius:8px; font-size:14px; font-family:inherit;
  }
  input:focus, select:focus, textarea:focus { outline:none; border-color:%(brand)s; }
  button {
    margin-top:24px; background:%(brand)s; color:#fff; border:none; padding:12px 20px;
    border-radius:8px; font-size:15px; font-weight:600; cursor:pointer; width:100%%;
  }
  button:hover { opacity:0.92; }
  .req { color:#b3261e; }
</style>
</head>
<body>
  <div class="card">
    <h1>%(title)s</h1>
    %(desc)s
    %(error)s
        <form method="post" action="/flow/form/%(uuid)s/submit" enctype="multipart/form-data">
      %(fields)s
      <input type="text" name="%(honeypot)s" value="" style="position:absolute;left:-9999px;" tabindex="-1" autocomplete="off"/>
      <button type="submit">%(submit_label)s</button>
    </form>
  </div>
</body>
</html>""" % {
            'title': Markup.escape(form.name),
            'brand': _BRAND_COLOR,
            'desc': ('<div class="desc">%s</div>' % Markup.escape(form.description)) if form.description else '',
            'error': error_html,
            'uuid': form.access_uuid,
            'fields': fields_html,
            'honeypot': _HONEYPOT_FIELD,
            'submit_label': Markup.escape(form.submit_button_label or 'Submit'),
        }

    @staticmethod
    def _render_field_html(field, value):
        required_mark = ' <span class="req">*</span>' if field.required else ''
        required_attr = ' required' if field.required else ''
        placeholder_attr = ' placeholder="%s"' % Markup.escape(field.placeholder) if field.placeholder else ''
        label = '<label>%s%s</label>' % (Markup.escape(field.label), required_mark)
        value = Markup.escape(value or '')

        if field.field_type == 'text':
            input_html = '<textarea name="%s" rows="4"%s%s>%s</textarea>' % (
                field.name, required_attr, placeholder_attr, value)
        elif field.field_type == 'boolean':
            checked = ' checked' if value else ''
            input_html = '<input type="checkbox" name="%s" value="1"%s/>' % (field.name, checked)
        elif field.field_type == 'file':
            # Browsers never let a file input's value be pre-filled, so we ignore
            # `value` here entirely even when re-rendering after a validation error.
            input_html = '<input type="file" name="%s"%s/>' % (field.name, required_attr)
        elif field.field_type == 'selection':
            options = [o.strip() for o in (field.selection_options or '').split(',') if o.strip()]
            opts_html = ''.join(
                '<option value="%s"%s>%s</option>' % (
                    Markup.escape(o), ' selected' if o == value else '', Markup.escape(o)
                ) for o in options
            )
            input_html = '<select name="%s"%s><option value="">-- Select --</option>%s</select>' % (
                field.name, required_attr, opts_html)
        else:
            html_type = {
                'email': 'email', 'integer': 'number', 'float': 'number',
                'date': 'date', 'datetime': 'datetime-local',
            }.get(field.field_type, 'text')
            input_html = '<input type="%s" name="%s" value="%s"%s%s/>' % (
                html_type, field.name, value, required_attr, placeholder_attr)

        return label + input_html

    def _render_thankyou_html(self, form):
        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>%(title)s</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background:#f5f5f7;
         margin:0; padding:40px 16px; color:#1f1f1f; }
  .card { max-width:560px; margin:80px auto 0; background:#fff; border-radius:12px; padding:40px;
          text-align:center; box-shadow:0 2px 12px rgba(0,0,0,0.08); }
  .check { font-size:40px; color:%(brand)s; margin-bottom:16px; }
  h1 { font-size:20px; margin:0 0 8px; }
  p { color:#5a5a5a; font-size:14px; }
</style>
</head>
<body>
  <div class="card">
    <div class="check">&#10003;</div>
    <h1>Submitted</h1>
    <p>%(message)s</p>
  </div>
</body>
</html>""" % {
            'title': Markup.escape(form.name),
            'brand': _BRAND_COLOR,
            'message': Markup.escape(form.thank_you_message or 'Thank you.'),
        }
