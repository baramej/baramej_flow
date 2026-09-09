# -*- coding: utf-8 -*-
"""Generic outbound HTTP call node. This is the integration backbone: Google
Sheets/Gmail/Calendar/Drive, Supabase's REST API, or literally any other REST
service can be called from this one node type without writing a bespoke connector
for each. The convenience wrappers (google_sheets_append.py, gmail_send.py, etc.)
exist purely to save you from hand-writing the URL/payload shape for common
operations — they're thin, and anything they don't cover, this node covers directly.
"""
import json
import logging

import requests

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_dict, render_template
from .base import BaseNodeExecutor
from .google_oauth_base import get_google_credential, refresh_google_access_token

_logger = logging.getLogger(__name__)
_TIMEOUT = 30

_AUTH_TYPES = ('none', 'api_key_header', 'bearer', 'basic', 'google_oauth2')


@register_node('http_request')
class HTTPRequestExecutor(BaseNodeExecutor):
    """Expected config_json:
        {
            "method": "POST",
            "url": "https://api.example.com/v1/things/{{form.thing_id}}",
            "auth_type": "bearer",            # none | api_key_header | bearer | basic | google_oauth2
            "credential_id": 4,               # required for bearer/api_key_header/basic/google_oauth2
            "api_key_header_name": "X-API-Key",  # only used when auth_type = api_key_header
            "headers": {"Content-Type": "application/json"},
            "query_params": {"limit": "10"},
            "body": {"name": "{{form.name}}"},
            "output_key": "http_result"
        }

    URL, headers, query_params, and body values all support {{dotted.path}}
    interpolation, same as every other node. The full response is parsed as JSON
    when the Content-Type says so (falls back to raw text otherwise) and written to
    context[output_key] as {"status_code": ..., "data": ..., "headers": {...}}.

    For auth_type "bearer"/"api_key_header"/"basic", credential_id points at a
    baramej.flow.credential with provider="generic" (api_key holds the token/key;
    for basic auth, store "username:password" in api_key). For "google_oauth2",
    credential_id points at a provider="google_oauth2" credential and the node
    handles token refresh automatically via google_oauth_base.py.
    """

    def execute(self):
        config = self.node.get_config()
        method = (config.get('method') or 'GET').upper()
        url = render_template(config.get('url', ''), self.context)
        if not url:
            raise UserError(_('HTTP Request node "%s" is missing "url" in its configuration.') % self.node.name)

        auth_type = config.get('auth_type', 'none')
        if auth_type not in _AUTH_TYPES:
            raise UserError(_('HTTP Request node "%s" has unsupported auth_type "%s". Supported: %s') % (
                self.node.name, auth_type, ', '.join(_AUTH_TYPES)))

        headers = render_dict(config.get('headers', {}), self.context)
        params = render_dict(config.get('query_params', {}), self.context)
        body_config = config.get('body')
        body = render_dict(body_config, self.context) if isinstance(body_config, dict) else None

        self._apply_auth(auth_type, config, headers)

        try:
            response = requests.request(
                method, url, headers=headers, params=params,
                json=body if body is not None else None,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('HTTP Request node "%s" could not reach "%s": %s') % (self.node.name, url, exc))

        content_type = response.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            try:
                data = response.json()
            except ValueError:
                data = response.text
        else:
            data = response.text

        result = {
            'status_code': response.status_code,
            'data': data,
            'headers': dict(response.headers),
        }

        if config.get('raise_on_error', True) and not (200 <= response.status_code < 300):
            raise UserError(_('HTTP Request node "%s" got a non-2xx response (HTTP %s) from %s: %s') % (
                self.node.name, response.status_code, url, str(data)[:500]))

        output_key = config.get('output_key', 'http_result')
        new_context = dict(self.context)
        new_context[output_key] = result
        return {'output': new_context}

    def _apply_auth(self, auth_type, config, headers):
        if auth_type == 'none':
            return

        credential_id = config.get('credential_id')

        if auth_type == 'google_oauth2':
            credential = get_google_credential(self.env, credential_id)
            access_token = refresh_google_access_token(credential)
            headers['Authorization'] = 'Bearer %s' % access_token
            return

        Credential = self.env['baramej.flow.credential'].sudo()
        if not credential_id:
            raise UserError(_('HTTP Request node "%s" has auth_type "%s" but no credential_id set.') % (
                self.node.name, auth_type))
        credential = Credential.browse(int(credential_id))
        if not credential.exists() or not credential.api_key:
            raise UserError(_('HTTP Request node "%s" references a credential with no API key set.') %
                             self.node.name)

        if auth_type == 'bearer':
            headers['Authorization'] = 'Bearer %s' % credential.api_key
        elif auth_type == 'api_key_header':
            header_name = config.get('api_key_header_name', 'X-API-Key')
            headers[header_name] = credential.api_key
        elif auth_type == 'basic':
            import base64
            token = base64.b64encode(credential.api_key.encode()).decode()
            headers['Authorization'] = 'Basic %s' % token
