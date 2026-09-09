# -*- coding: utf-8 -*-
"""Direct SQL query node against an external Postgres or Supabase database — e.g.
reading a client's existing asset register, CMDB, or knowledge-base table as
context an AI node can then reason over. Deliberately NOT the same connection as
Odoo's own database; this always connects to whatever host/db is configured on the
credential.
"""
import logging

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_template
from .base import BaseNodeExecutor

_logger = logging.getLogger(__name__)
_TIMEOUT = 20
_MAX_ROWS = 1000  # hard cap so a runaway query can't return an unbounded result into context


@register_node('db_query')
class DatabaseQueryExecutor(BaseNodeExecutor):
    """Expected config_json:
        {
            "credential_id": 7,
            "query": "SELECT id, subject, status FROM tickets WHERE customer_email = %(email)s LIMIT 20",
            "params": {"email": "{{form.email}}"},
            "output_key": "db_result",
            "allow_write": false
        }

    "query" uses psycopg2's native %(name)s parameter binding (NOT this module's
    {{...}} templating) for the actual SQL substitution — that's a deliberate
    security choice: template-substituting raw values directly into a SQL string
    would be a textbook SQL-injection vector. Instead, "params" values ARE resolved
    via {{dotted.path}} template interpolation first (so you can pull
    {{form.email}} out of the workflow context), and the *results* of that
    resolution are then passed to psycopg2 as bound parameters, never string-
    concatenated into the query.

    allow_write defaults to false and blocks anything that isn't a SELECT/WITH
    statement — flip it only if this node genuinely needs to INSERT/UPDATE/DELETE,
    and even then, use a database role scoped to exactly what it needs. This
    module does not (yet) support connection pooling; each run opens and closes
    its own connection, which is fine for the traffic volumes discussed for OTA/
    Omran ITSM, but would need revisiting for very high-frequency triggers.
    """

    def execute(self):
        config = self.node.get_config()
        credential = self._get_credential(config)
        query = config.get('query')
        if not query:
            raise UserError(_('Database Query node "%s" is missing "query" in its configuration.') % self.node.name)

        allow_write = bool(config.get('allow_write', False))
        if not allow_write and not query.strip().upper().lstrip('(').startswith(('SELECT', 'WITH')):
            raise UserError(_('Database Query node "%s" only allows SELECT/WITH statements unless '
                               '"allow_write": true is explicitly set in its configuration.') % self.node.name)

        raw_params = config.get('params', {}) or {}
        bound_params = {key: render_template(val, self.context) for key, val in raw_params.items()}

        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise UserError(_('psycopg2 is not installed in this Odoo environment. Run: '
                               'pip install psycopg2-binary'))

        try:
            conn = psycopg2.connect(
                host=credential.db_host, port=credential.db_port or 5432, dbname=credential.db_name,
                user=credential.db_user, password=credential.db_password or '',
                sslmode=credential.db_sslmode or 'require', connect_timeout=_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - surface the driver's own message verbatim
            raise UserError(_('Database Query node "%s" could not connect: %s') % (self.node.name, exc))

        try:
            with conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(query, bound_params)
                    if cur.description:  # SELECT-like statement
                        rows = cur.fetchmany(_MAX_ROWS)
                        result_rows = [dict(row) for row in rows]
                    else:  # write statement
                        result_rows = []
                    row_count = cur.rowcount
        except Exception as exc:  # noqa: BLE001 - surface the driver's own message verbatim
            raise UserError(_('Database Query node "%s" query failed: %s') % (self.node.name, exc))
        finally:
            conn.close()

        output_key = config.get('output_key', 'db_result')
        new_context = dict(self.context)
        new_context[output_key] = {'rows': result_rows, 'row_count': row_count}
        return {'output': new_context}

    def _get_credential(self, config):
        Credential = self.env['baramej.flow.credential'].sudo()
        credential_id = config.get('credential_id')
        if credential_id:
            credential = Credential.browse(int(credential_id))
            if not credential.exists():
                raise UserError(_('Database Query node "%s" references credential id %s, which no longer '
                                   'exists.') % (self.node.name, credential_id))
        else:
            credential = Credential.search(
                [('provider', 'in', ('postgres', 'supabase')), ('active', '=', True)], limit=1)
            if not credential:
                raise UserError(_('Database Query node "%s" has no credential_id set and no active database '
                                   'credential exists.') % self.node.name)
        if not credential.db_host:
            raise UserError(_('Credential "%s" has no database host configured.') % credential.name)
        return credential
