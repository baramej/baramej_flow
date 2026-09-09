# -*- coding: utf-8 -*-
"""Shared OAuth2 access-token handling for every Google API node (Sheets, Gmail,
Calendar, Drive). This module does NOT implement the interactive consent-screen
flow — that has to happen once, outside Odoo (e.g. via Google's OAuth Playground
at https://developers.google.com/oauthplayground, or a one-time script run by
whoever sets this up), to obtain a long-lived refresh_token. What this module DOES
do is the refresh-token grant: exchanging that refresh_token for a short-lived
access_token automatically, every time a node needs one, caching it on the
credential record so most calls don't need a fresh token round-trip.

This is standard OAuth2 (RFC 6749 §6) — same flow whether the caller is this module
or Google's own client libraries. It has not been exercised against Google's live
token endpoint from within this build environment (no network egress to
oauth2.googleapis.com here), so treat the very first real run against your actual
Google Cloud OAuth app as the true test of this code path, the same way the email
gateway needed a live mail server to fully confirm.
"""
import logging
from datetime import datetime, timedelta

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_TIMEOUT = 15
# Refresh a little before actual expiry so a slow node execution doesn't start with
# a token that expires mid-call.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


def refresh_google_access_token(credential):
    """Returns a valid access token for `credential`, refreshing it first if the
    cached one is missing or close to expiry. Writes the new token + expiry back
    onto the credential record so the next call can reuse it without another round
    trip. `credential` must be a baramej.flow.credential recordset with provider =
    'google_oauth2'."""
    now = datetime.now()
    if credential.oauth_access_token and credential.oauth_token_expiry:
        if credential.oauth_token_expiry - timedelta(seconds=_EXPIRY_SAFETY_MARGIN_SECONDS) > now:
            return credential.oauth_access_token

    if not all([credential.oauth_client_id, credential.oauth_client_secret, credential.oauth_refresh_token]):
        raise UserError(_('Google OAuth2 credential "%s" is missing Client ID, Client Secret, or Refresh '
                           'Token.') % credential.name)

    token_uri = credential.oauth_token_uri or 'https://oauth2.googleapis.com/token'
    payload = {
        'client_id': credential.oauth_client_id,
        'client_secret': credential.oauth_client_secret,
        'refresh_token': credential.oauth_refresh_token,
        'grant_type': 'refresh_token',
    }

    try:
        response = requests.post(token_uri, data=payload, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise UserError(_('Could not reach Google\'s token endpoint: %s') % exc)

    if response.status_code != 200:
        raise UserError(_('Google rejected the refresh token for credential "%s" (HTTP %s): %s. The refresh '
                           'token may have been revoked — obtain a new one.') % (
            credential.name, response.status_code, response.text[:300]))

    data = response.json()
    access_token = data.get('access_token')
    expires_in = data.get('expires_in', 3600)
    if not access_token:
        raise UserError(_('Google\'s token response for credential "%s" did not include an access_token.') %
                         credential.name)

    credential.sudo().write({
        'oauth_access_token': access_token,
        'oauth_token_expiry': now + timedelta(seconds=expires_in),
    })
    return access_token


def get_google_credential(env, credential_id=None, company_id=None):
    """Looks up a google_oauth2 credential by id, or falls back to the first
    active one for the current company — same lookup pattern as ai_base.py's
    _get_credential, kept consistent across the whole node library."""
    Credential = env['baramej.flow.credential'].sudo()
    if credential_id:
        credential = Credential.browse(int(credential_id))
        if not credential.exists():
            raise UserError(_('Referenced credential id %s no longer exists.') % credential_id)
    else:
        domain = [('provider', '=', 'google_oauth2'), ('active', '=', True)]
        if company_id:
            domain.append(('company_id', '=', company_id))
        credential = Credential.search(domain, limit=1)
        if not credential:
            raise UserError(_('No active Google OAuth2 credential found. Add one under Baramej Flow → '
                               'Configuration → Credentials.'))
    return credential
