# -*- coding: utf-8 -*-
import logging

import requests

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_template
from .base import BaseNodeExecutor
from .google_oauth_base import get_google_credential, refresh_google_access_token

_logger = logging.getLogger(__name__)
_TIMEOUT = 30


@register_node('google_sheets_append')
class GoogleSheetsAppendExecutor(BaseNodeExecutor):
    """Appends one row to a Google Sheet via the Sheets API v4 values:append
    endpoint. A thin convenience wrapper — the same result is achievable with a
    generic http_request node pointed at this same URL; this exists purely so you
    don't have to hand-write the Sheets API payload shape.

    Expected config_json:
        {
            "credential_id": 9,
            "spreadsheet_id": "1AbC...xyz",
            "range": "Sheet1!A1",
            "values": ["{{form.name}}", "{{form.email}}", "{{ai_result.category}}"]
        }

    "spreadsheet_id" is the long id in the sheet's URL between /d/ and /edit.
    "values" is a single row; each entry supports {{dotted.path}} interpolation.
    """

    def execute(self):
        config = self.node.get_config()
        credential = get_google_credential(self.env, config.get('credential_id'))
        access_token = refresh_google_access_token(credential)

        spreadsheet_id = config.get('spreadsheet_id')
        range_ = config.get('range', 'Sheet1!A1')
        if not spreadsheet_id:
            raise UserError(_('Google Sheets Append node "%s" is missing "spreadsheet_id".') % self.node.name)

        values = [render_template(str(v), self.context) for v in (config.get('values') or [])]

        url = (
            'https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s:append'
            '?valueInputOption=USER_ENTERED' % (spreadsheet_id, range_)
        )
        payload = {'values': [values]}

        try:
            response = requests.post(
                url, json=payload,
                headers={'Authorization': 'Bearer %s' % access_token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Google Sheets: %s') % exc)

        if response.status_code != 200:
            raise UserError(_('Google Sheets rejected the append (HTTP %s): %s') % (
                response.status_code, response.text[:400]))

        output_key = config.get('output_key', 'sheets_result')
        new_context = dict(self.context)
        new_context[output_key] = response.json()
        return {'output': new_context}
