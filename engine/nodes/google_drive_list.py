# -*- coding: utf-8 -*-
"""Lists files in Google Drive matching a query — e.g. checking whether a document
already exists before creating a duplicate, or pulling a folder's contents into
context. Upload/download are NOT implemented in this pass (see module README) —
this node covers the read side only; write it up as the next increment if/when
upload is actually needed.
"""
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


@register_node('google_drive_list')
class GoogleDriveListExecutor(BaseNodeExecutor):
    """Expected config_json:
        {
            "credential_id": 9,
            "query": "name contains '{{form.ticket_ref}}' and trashed = false",
            "page_size": 10,
            "output_key": "drive_files"
        }

    "query" follows Google Drive's own query syntax (https://developers.google.com/
    drive/api/guides/search-files) and supports {{dotted.path}} interpolation
    before being sent.
    """

    def execute(self):
        config = self.node.get_config()
        credential = get_google_credential(self.env, config.get('credential_id'))
        access_token = refresh_google_access_token(credential)

        query = render_template(config.get('query', ''), self.context)
        page_size = config.get('page_size', 10)

        params = {
            'pageSize': page_size,
            'fields': 'files(id, name, mimeType, webViewLink, modifiedTime)',
        }
        if query:
            params['q'] = query

        url = 'https://www.googleapis.com/drive/v3/files'
        try:
            response = requests.get(
                url, params=params,
                headers={'Authorization': 'Bearer %s' % access_token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Google Drive: %s') % exc)

        if response.status_code != 200:
            raise UserError(_('Google Drive rejected the request (HTTP %s): %s') % (
                response.status_code, response.text[:400]))

        files = response.json().get('files', [])
        output_key = config.get('output_key', 'drive_files')
        new_context = dict(self.context)
        new_context[output_key] = files
        return {'output': new_context}
