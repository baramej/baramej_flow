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


@register_node('google_calendar_create_event')
class GoogleCalendarCreateEventExecutor(BaseNodeExecutor):
    """Creates an event via the Google Calendar API (events.insert) — e.g.
    scheduling a follow-up call or on-site visit automatically from a ticket
    workflow.

    Expected config_json:
        {
            "credential_id": 9,
            "calendar_id": "primary",
            "summary": "Follow-up: {{form.subject}}",
            "description": "Auto-created from ticket {{ticket_partner_id}}",
            "start_datetime": "2026-09-01T10:00:00+04:00",
            "end_datetime": "2026-09-01T10:30:00+04:00",
            "timezone": "Asia/Muscat",
            "attendee_emails": ["{{form.email}}"]
        }

    Datetimes must be ISO 8601 with a UTC offset (Google's API requirement) — this
    node does not attempt to parse/reformat other date formats, since silently
    "fixing" a malformed datetime is exactly the kind of thing that should fail
    loudly instead.
    """

    def execute(self):
        config = self.node.get_config()
        credential = get_google_credential(self.env, config.get('credential_id'))
        access_token = refresh_google_access_token(credential)

        calendar_id = config.get('calendar_id', 'primary')
        start_dt = render_template(config.get('start_datetime', ''), self.context)
        end_dt = render_template(config.get('end_datetime', ''), self.context)
        if not start_dt or not end_dt:
            raise UserError(_('Google Calendar node "%s" needs both "start_datetime" and "end_datetime".') %
                             self.node.name)

        timezone = config.get('timezone', 'UTC')
        attendees = [
            {'email': render_template(str(e), self.context)}
            for e in (config.get('attendee_emails') or [])
        ]

        event_body = {
            'summary': render_template(config.get('summary', ''), self.context),
            'description': render_template(config.get('description', ''), self.context),
            'start': {'dateTime': start_dt, 'timeZone': timezone},
            'end': {'dateTime': end_dt, 'timeZone': timezone},
            'attendees': attendees,
        }

        url = 'https://www.googleapis.com/calendar/v3/calendars/%s/events' % calendar_id
        try:
            response = requests.post(
                url, json=event_body,
                headers={'Authorization': 'Bearer %s' % access_token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Google Calendar: %s') % exc)

        if response.status_code not in (200, 201):
            raise UserError(_('Google Calendar rejected the event (HTTP %s): %s') % (
                response.status_code, response.text[:400]))

        output_key = config.get('output_key', 'calendar_result')
        new_context = dict(self.context)
        new_context[output_key] = response.json()
        return {'output': new_context}
