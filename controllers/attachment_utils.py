# -*- coding: utf-8 -*-
"""Tiny shared helper used by both form_controller.py (initial submission) and
portal_controller.py (resubmission) — both need to turn a werkzeug FileStorage
into a persisted ir.attachment the same way, so it lives here once rather than
being duplicated in two controllers.
"""
import base64


def save_uploaded_file(env, file_storage, res_model, res_id):
    """Reads a werkzeug FileStorage (from request.httprequest.files) and saves it
    as an ir.attachment linked to (res_model, res_id). Returns a small dict meant
    to be stored inline in a submitted_data_json blob — never the raw file bytes,
    just enough to look the attachment back up later (see
    models/eservice_ticket.py _compute_submitted_data_html for the download-link
    rendering that reads this same shape back out).

    No file-size limit is enforced here — a known gap, not an oversight. Add one
    (checking len(content) against a configured max) before relying on this for
    a public-facing deployment.
    """
    content = file_storage.read()
    attachment = env['ir.attachment'].sudo().create({
        'name': file_storage.filename,
        'datas': base64.b64encode(content),
        'res_model': res_model,
        'res_id': res_id,
        'mimetype': file_storage.mimetype,
    })
    return {'attachment_id': attachment.id, 'filename': file_storage.filename}