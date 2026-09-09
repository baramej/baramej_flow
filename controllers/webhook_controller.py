# -*- coding: utf-8 -*-
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class FlowWebhookController(http.Controller):
    """Two inbound HTTP integration points:

    1. A generic, HMAC-signed webhook for any external system to trigger a workflow.
    2. A WhatsApp Business Cloud API webhook (Meta's required GET verification
       handshake, plus the POST endpoint that receives actual messages).

    Both endpoints are `auth='public'` by design — the caller is an external system,
    not a logged-in Odoo user — so authenticity is established via signature/token
    verification instead of a session, not by trusting the network.
    """

    # ------------------------------------------------------------------
    # Generic webhook
    # ------------------------------------------------------------------
    @http.route('/flow/webhook/<string:uuid>', type='http', auth='public', methods=['POST'], csrf=False)
    def generic_webhook(self, uuid, **kwargs):
        env = request.env
        workflow = env['baramej.flow.workflow'].sudo().search([
            ('webhook_uuid', '=', uuid),
            ('trigger_type', '=', 'webhook'),
            ('state', '=', 'published'),
        ], limit=1)

        if not workflow:
            _logger.info('Baramej Flow: webhook call to unknown/unpublished uuid %s', uuid)
            return request.make_json_response({'error': 'Unknown or unpublished webhook'}, status=404)

        raw_body = request.httprequest.get_data()
        signature = request.httprequest.headers.get('X-Flow-Signature', '')
        if not self._verify_signature(raw_body, signature, workflow.webhook_secret):
            _logger.warning('Baramej Flow: webhook signature verification failed for workflow %s', workflow.id)
            return request.make_json_response({'error': 'Invalid or missing signature'}, status=401)

        try:
            payload = json.loads(raw_body or b'{}')
        except ValueError:
            return request.make_json_response({'error': 'Body must be valid JSON'}, status=400)

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(env)
        execution = engine.run(workflow, trigger_source='webhook', initial_context={'webhook': payload})

        status = 200 if execution.state == 'success' else 500
        return request.make_json_response(
            {'execution_id': execution.id, 'state': execution.state},
            status=status,
        )

    @staticmethod
    def _verify_signature(raw_body, signature, secret):
        """Constant-time HMAC-SHA256 comparison. Rejects outright if no secret is
        configured — a workflow should never accept unsigned traffic just because
        the secret field happens to be empty."""
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # WhatsApp Business Cloud API
    # ------------------------------------------------------------------
    @http.route('/flow/whatsapp/webhook/<string:uuid>', type='http', auth='public', methods=['GET'], csrf=False)
    def whatsapp_verify(self, uuid, **kwargs):
        """Meta's required verification handshake when you configure the webhook URL
        in the Developer Console. Meta sends hub.mode/hub.verify_token/hub.challenge
        as query params and expects the raw challenge echoed back if the token matches."""
        env = request.env
        workflow = env['baramej.flow.workflow'].sudo().search([
            ('webhook_uuid', '=', uuid),
            ('trigger_type', '=', 'whatsapp'),
        ], limit=1)

        if not workflow:
            return request.make_response('Not found', status=404)

        mode = kwargs.get('hub.mode')
        token = kwargs.get('hub.verify_token')
        challenge = kwargs.get('hub.challenge', '')

        if mode == 'subscribe' and token == workflow.whatsapp_verify_token:
            return request.make_response(challenge, status=200)

        _logger.warning('Baramej Flow: WhatsApp webhook verification failed for workflow %s', workflow.id)
        return request.make_response('Verification token mismatch', status=403)

    @http.route('/flow/whatsapp/webhook/<string:uuid>', type='http', auth='public', methods=['POST'], csrf=False)
    def whatsapp_incoming(self, uuid, **kwargs):
        env = request.env
        workflow = env['baramej.flow.workflow'].sudo().search([
            ('webhook_uuid', '=', uuid),
            ('trigger_type', '=', 'whatsapp'),
            ('state', '=', 'published'),
        ], limit=1)

        if not workflow:
            return request.make_json_response({'error': 'Unknown or unpublished workflow'}, status=404)

        raw_body = request.httprequest.get_data()
        try:
            payload = json.loads(raw_body or b'{}')
        except ValueError:
            return request.make_json_response({'error': 'Body must be valid JSON'}, status=400)

        messages = self._extract_whatsapp_messages(payload)
        if not messages:
            # Meta also sends status callbacks (delivered/read receipts) to the same
            # endpoint — these aren't messages, so acknowledge with 200 and do nothing,
            # rather than treating "no messages found" as an error.
            return request.make_json_response({'status': 'ignored', 'reason': 'no message payload'})

        from odoo.addons.baramej_flow.engine.executor import WorkflowEngine
        engine = WorkflowEngine(env)
        execution_ids = []
        for msg in messages:
            execution = engine.run(workflow, trigger_source='whatsapp', initial_context={'whatsapp': msg})
            execution_ids.append(execution.id)

        return request.make_json_response({'execution_ids': execution_ids})

    @staticmethod
    def _extract_whatsapp_messages(payload):
        """Flattens Meta's nested webhook payload (entry -> changes -> value ->
        messages) into a simple list of dicts the engine context can use directly.
        Only handles the 'messages' array for now — status/read-receipt callbacks
        are intentionally ignored (see caller)."""
        messages = []
        for entry in payload.get('entry', []) or []:
            for change in entry.get('changes', []) or []:
                value = change.get('value', {}) or {}
                for msg in value.get('messages', []) or []:
                    messages.append({
                        'from': msg.get('from'),
                        'message_id': msg.get('id'),
                        'timestamp': msg.get('timestamp'),
                        'type': msg.get('type'),
                        'text': (msg.get('text') or {}).get('body'),
                    })
        return messages
