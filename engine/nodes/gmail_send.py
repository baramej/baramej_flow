# -*- coding: utf-8 -*-
import base64
import logging
from email.mime.text import MIMEText

import requests

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_template
from .base import BaseNodeExecutor
from .google_oauth_base import get_google_credential, refresh_google_access_token

_logger = logging.getLogger(__name__)
_TIMEOUT = 30


@register_node('gmail_send')
class GmailSendExecutor(BaseNodeExecutor):
    """Sends an email via the Gmail API (users.messages.send), authenticated as
    whichever Google account the OAuth2 credential's refresh token belongs to. Note
    this sends AS that Gmail account, not through Odoo's own outgoing mail server —
    use this specifically when the workflow needs to send *from* a particular
    Gmail/Workspace address rather than Odoo's configured sender.

    Expected config_json:
        {
            "credential_id": 9,
            "to": "{{form.email}}",
            "subject": "Your ticket {{ticket_partner_id}} was received",
            "body": "Hi {{form.name}}, we've logged your request."
        }
    """

    def execute(self):
        config = self.node.get_config()
        credential = get_google_credential(self.env, config.get('credential_id'))
        access_token = refresh_google_access_token(credential)

        to_addr = render_template(config.get('to', ''), self.context)
        subject = render_template(config.get('subject', ''), self.context)
        body = render_template(config.get('body', ''), self.context)

        if not to_addr:
            raise UserError(_('Gmail Send node "%s" is missing "to" in its configuration.') % self.node.name)

        mime_message = MIMEText(body)
        mime_message['to'] = to_addr
        mime_message['subject'] = subject
        raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()

        url = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
        try:
            response = requests.post(
                url, json={'raw': raw},
                headers={'Authorization': 'Bearer %s' % access_token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Gmail: %s') % exc)

        if response.status_code != 200:
            raise UserError(_('Gmail rejected the send (HTTP %s): %s') % (
                response.status_code, response.text[:400]))

        output_key = config.get('output_key', 'gmail_result')
        new_context = dict(self.context)
        new_context[output_key] = response.json()
        return {'output': new_context}
