# -*- coding: utf-8 -*-
import json
import logging

import requests

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from .ai_base import BaseAINodeExecutor

_logger = logging.getLogger(__name__)

_GEMINI_URL_TEMPLATE = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'
_TIMEOUT = 30


@register_node('ai_gemini')
class AIGeminiExecutor(BaseAINodeExecutor):
    """Calls Google's Gemini API with responseMimeType=application/json — Gemini's
    structured-output mode, the equivalent of OpenAI's response_format used in
    ai_openai.py. Same contract, different wire format.
    """
    provider_code = 'gemini'

    def _call_provider(self, model, system_prompt, user_prompt, json_schema, api_key):
        schema_hint = json.dumps(json_schema) if json_schema else '{}'
        full_prompt = (
            (system_prompt + '\n\n' if system_prompt else '') +
            user_prompt +
            '\n\nRespond ONLY with valid JSON matching this shape (no prose, no markdown fences): ' + schema_hint
        )

        url = _GEMINI_URL_TEMPLATE.format(model=model, api_key=api_key)
        payload = {
            'contents': [{'parts': [{'text': full_prompt}]}],
            'generationConfig': {
                'temperature': 0,
                'responseMimeType': 'application/json',
            },
        }

        try:
            response = requests.post(url, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise UserError(_('Could not reach Gemini: %s') % exc)

        if response.status_code != 200:
            raise UserError(_('Gemini returned an error (HTTP %s): %s') % (
                response.status_code, response.text[:500]))

        data = response.json()
        try:
            content = data['candidates'][0]['content']['parts'][0]['text']
            result_data = json.loads(content)
        except (KeyError, IndexError, ValueError) as exc:
            raise UserError(_('Gemini response could not be parsed as JSON: %s') % exc)

        usage = data.get('usageMetadata', {}) or {}
        return result_data, {
            'prompt_tokens': usage.get('promptTokenCount'),
            'completion_tokens': usage.get('candidatesTokenCount'),
            'total_tokens': usage.get('totalTokenCount'),
        }
