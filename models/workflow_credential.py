# -*- coding: utf-8 -*-
import logging

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_TIMEOUT = 15


class FlowCredential(models.Model):
    """Credential vault. Live as of Phase 3 — the ai_openai and ai_gemini node
    executors read from this model (see engine/nodes/ai_base.py). Encryption at
    rest and per-read audit logging remain on the Phase 6 governance list; what
    Phase 3 adds is the "Test Connection" action below, so a bad key is caught at
    configuration time rather than surfacing as a cryptic failure deep in a
    workflow run.

    api_key is restricted to the Flow Manager group at the field level for UI/export
    purposes. This does NOT block the engine from reading it during execution — the
    engine always runs as a superuser environment (see engine/executor.py), and
    Odoo's field-level group restriction is bypassed for superuser access by design,
    the same way action_create_record.py's sudo() is meant to act with the
    automation's own authority rather than the triggering user's.
    """

    _name = 'baramej.flow.credential'
    _description = 'Baramej Flow - Credential Vault'
    _order = 'name'

    name = fields.Char(required=True)
    provider = fields.Selection(
        [
            ('openai', 'OpenAI'),
            ('gemini', 'Google Gemini'),
            ('whatsapp', 'WhatsApp Cloud API'),
            ('generic', 'Generic API Key'),
            ('postgres', 'PostgreSQL Database'),
            ('supabase', 'Supabase (Postgres)'),
            ('google_oauth2', 'Google OAuth2 (Sheets/Gmail/Calendar/Drive)'),
        ],
        required=True,
    )
    api_key = fields.Char(string='API Key / Token', groups='baramej_flow.group_flow_manager')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    active = fields.Boolean(default=True)
    note = fields.Text()

    # --- Database credentials (postgres / supabase) ---
    db_host = fields.Char(string='Host', groups='baramej_flow.group_flow_manager')
    db_port = fields.Integer(string='Port', default=5432, groups='baramej_flow.group_flow_manager')
    db_name = fields.Char(string='Database Name', groups='baramej_flow.group_flow_manager')
    db_user = fields.Char(string='Username', groups='baramej_flow.group_flow_manager')
    db_password = fields.Char(string='Password', groups='baramej_flow.group_flow_manager')
    db_sslmode = fields.Selection(
        [('disable', 'Disable'), ('require', 'Require'), ('verify-full', 'Verify Full')],
        default='require', string='SSL Mode',
        help='Supabase and most managed Postgres providers require SSL — "require" is the safe default.',
    )

    # --- Google OAuth2 credentials (Sheets / Gmail / Calendar / Drive) ---
    # A refresh_token must be obtained OUTSIDE Odoo first (e.g. via Google's OAuth
    # Playground, or a one-time consent flow run elsewhere) — this module does not
    # implement the interactive consent-screen redirect itself. Once you have a
    # refresh_token, the engine handles renewing the short-lived access_token
    # automatically on every use (see engine/nodes/google_oauth_base.py).
    oauth_client_id = fields.Char(string='OAuth Client ID', groups='baramej_flow.group_flow_manager')
    oauth_client_secret = fields.Char(string='OAuth Client Secret', groups='baramej_flow.group_flow_manager')
    oauth_refresh_token = fields.Char(
        string='OAuth Refresh Token', groups='baramej_flow.group_flow_manager',
        help='Obtained externally via Google\'s consent flow (e.g. OAuth Playground). This module renews '
             'the short-lived access token automatically from this on every use.',
    )
    oauth_access_token = fields.Char(
        string='Cached Access Token', groups='baramej_flow.group_flow_manager', readonly=True, copy=False,
        help='Auto-refreshed by the engine. Do not edit directly.',
    )
    oauth_token_expiry = fields.Datetime(
        string='Access Token Expires', groups='baramej_flow.group_flow_manager', readonly=True, copy=False,
    )
    oauth_token_uri = fields.Char(
        string='Token Endpoint', default='https://oauth2.googleapis.com/token',
        groups='baramej_flow.group_flow_manager',
    )

    def action_test_connection(self):
        self.ensure_one()
        if self.provider in ('postgres', 'supabase'):
            self._test_database()
        elif self.provider == 'google_oauth2':
            self._test_google_oauth2()
        else:
            if not self.api_key:
                raise UserError(_('Enter an API key before testing.'))
            if self.provider == 'openai':
                self._test_openai()
            elif self.provider == 'gemini':
                self._test_gemini()
            else:
                raise UserError(_('Test Connection is only implemented for OpenAI, Gemini, PostgreSQL/Supabase, '
                                   'and Google OAuth2 credentials so far. WhatsApp and Generic credentials can '
                                   'still be used by nodes — just not pre-validated here yet.'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('The connection was validated successfully.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def _test_openai(self):
        try:
            response = requests.get(
                'https://api.openai.com/v1/models',
                headers={'Authorization': 'Bearer %s' % self.api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('Could not reach OpenAI: %s') % exc)
        if response.status_code != 200:
            raise UserError(_('OpenAI rejected this key (HTTP %s): %s') % (
                response.status_code, response.text[:300]))

    def _test_gemini(self):
        try:
            response = requests.get(
                'https://generativelanguage.googleapis.com/v1beta/models?key=%s' % self.api_key,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Gemini: %s') % exc)
        if response.status_code != 200:
            raise UserError(_('Gemini rejected this key (HTTP %s): %s') % (
                response.status_code, response.text[:300]))

    def _test_database(self):
        if not all([self.db_host, self.db_name, self.db_user]):
            raise UserError(_('Host, Database Name, and Username are required.'))
        try:
            import psycopg2
        except ImportError:
            raise UserError(_('psycopg2 is not installed in this Odoo environment. Run: '
                               'pip install psycopg2-binary'))
        try:
            conn = psycopg2.connect(
                host=self.db_host, port=self.db_port or 5432, dbname=self.db_name,
                user=self.db_user, password=self.db_password or '', sslmode=self.db_sslmode or 'require',
                connect_timeout=_TIMEOUT,
            )
            conn.close()
        except Exception as exc:  # noqa: BLE001 - surface the driver's own error message verbatim
            raise UserError(_('Could not connect: %s') % exc)

    def _test_google_oauth2(self):
        if not all([self.oauth_client_id, self.oauth_client_secret, self.oauth_refresh_token]):
            raise UserError(_('OAuth Client ID, Client Secret, and Refresh Token are all required.'))
        from ..engine.nodes.google_oauth_base import refresh_google_access_token
        # This actually exercises the refresh-token grant against Google's real
        # token endpoint and writes the refreshed token back onto this record —
        # a genuine end-to-end test, not just a field-presence check.
        refresh_google_access_token(self)
