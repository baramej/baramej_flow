# -*- coding: utf-8 -*-
import json
import logging

import requests

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from .ai_base import BaseAINodeExecutor

_logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = 'https://api.openai.com/v1/chat/completions'
_TIMEOUT = 30


@register_node('ai_openai')
class AIOpenAIExecutor(BaseAINodeExecutor):
    """Calls OpenAI's Chat Completions API in JSON mode. Uses response_format=
    {"type": "json_object"} — OpenAI's own structured-output guarantee — rather than
    just asking nicely in the prompt, so a malformed response is a genuine API-level
    edge case, not the common case.
    """
    provider_code = 'openai'

    def _call_provider(self, model, system_prompt, user_prompt, json_schema, api_key):
        schema_hint = json.dumps(json_schema) if json_schema else '{}'
        full_system_prompt = (
            (system_prompt + '\n\n' if system_prompt else '') +
            'Respond ONLY with valid JSON matching this shape (no prose, no markdown fences): ' + schema_hint
        )

        payload = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': full_system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'response_format': {'type': 'json_object'},
            'temperature': 0,
        }
        headers = {
            'Authorization': 'Bearer %s' % api_key,
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(_OPENAI_CHAT_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise UserError(_('Could not reach OpenAI: %s') % exc)

        if response.status_code != 200:
            raise UserError(_('OpenAI returned an error (HTTP %s): %s') % (
                response.status_code, response.text[:500]))

        data = response.json()
        try:
            content = data['choices'][0]['message']['content']
            result_data = json.loads(content)
        except (KeyError, IndexError, ValueError) as exc:
            raise UserError(_('OpenAI response could not be parsed as JSON: %s') % exc)

        usage = data.get('usage', {}) or {}
        return result_data, {
            'prompt_tokens': usage.get('prompt_tokens'),
            'completion_tokens': usage.get('completion_tokens'),
            'total_tokens': usage.get('total_tokens'),
        }
