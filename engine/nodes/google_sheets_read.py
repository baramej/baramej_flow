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


@register_node('google_sheets_read')
class GoogleSheetsReadExecutor(BaseNodeExecutor):
    """Reads a range from a Google Sheet via the Sheets API v4 values:get endpoint
    — e.g. pulling a reference/lookup table into context for an AI node to reason
    over, similar in spirit to read_excel.py but for a live Google Sheet instead of
    an uploaded file.

    Expected config_json:
        {
            "credential_id": 9,
            "spreadsheet_id": "1AbC...xyz",
            "range": "Sheet1!A1:D100",
            "has_header_row": true,
            "output_key": "sheet_rows"
        }
    """

    def execute(self):
        config = self.node.get_config()
        credential = get_google_credential(self.env, config.get('credential_id'))
        access_token = refresh_google_access_token(credential)

        spreadsheet_id = config.get('spreadsheet_id')
        range_ = render_template(config.get('range', 'Sheet1'), self.context)
        if not spreadsheet_id:
            raise UserError(_('Google Sheets Read node "%s" is missing "spreadsheet_id".') % self.node.name)

        url = 'https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s' % (spreadsheet_id, range_)

        try:
            response = requests.get(
                url, headers={'Authorization': 'Bearer %s' % access_token}, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Google Sheets: %s') % exc)

        if response.status_code != 200:
            raise UserError(_('Google Sheets rejected the read (HTTP %s): %s') % (
                response.status_code, response.text[:400]))

        raw_values = response.json().get('values', [])
        has_header_row = config.get('has_header_row', True)

        if has_header_row and raw_values:
            header, *data_rows = raw_values
            rows = [dict(zip(header, row)) for row in data_rows]
        else:
            rows = raw_values

        output_key = config.get('output_key', 'sheet_rows')
        new_context = dict(self.context)
        new_context[output_key] = rows
        return {'output': new_context}
