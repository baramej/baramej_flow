# -*- coding: utf-8 -*-
"""Shared logic for every AI provider node (OpenAI, Gemini, and whatever's added
next). Subclasses implement only `_call_provider(...)` — credential lookup, prompt
templating, output-key placement, and token-usage logging all live here once, so
adding a third provider later is a ~40-line subclass, not a re-implementation.
"""
from odoo import _
from odoo.exceptions import UserError

from ..utils import render_template
from .base import BaseNodeExecutor


class BaseAINodeExecutor(BaseNodeExecutor):
    """Expected config_json (same shape for every provider):
        {
            "credential_id": 4,
            "model": "gpt-4o-mini",
            "system_prompt": "You classify IT support tickets by category and priority.",
            "user_prompt_template": "Ticket description: {{form.description}}",
            "output_key": "ai_result",
            "json_schema": {"category": "string", "priority": "string", "confidence": "number"}
        }

    The node always instructs the provider to return JSON matching json_schema —
    that's what makes "AI reads the form then decides the path" reliable rather
    than free-text guessing that a downstream Condition node can't safely parse.
    The parsed result lands in context[output_key], so a Condition node placed
    right after this one can branch on e.g. field="ai_result.confidence".

    credential_id is optional: if omitted, the node looks up the first active
    credential for this provider on the current company. Explicit credential_id is
    recommended once more than one key per provider exists (e.g. separate OTA vs.
    Omran keys sharing one Odoo instance).
    """

    provider_code = None  # set by subclass, e.g. 'openai' or 'gemini'

    def execute(self):
        config = self.node.get_config()
        credential = self._get_credential(config)

        model = config.get('model')
        if not model:
            raise UserError(_('AI node "%s" is missing "model" in its configuration.') % self.node.name)

        system_prompt = render_template(config.get('system_prompt', ''), self.context)
        user_prompt = render_template(config.get('user_prompt_template', ''), self.context)
        json_schema = config.get('json_schema') or {}

        result_data, usage = self._call_provider(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_schema=json_schema,
            api_key=credential.api_key,
        )

        output_key = config.get('output_key', 'ai_result')
        new_context = dict(self.context)
        new_context[output_key] = result_data

        # Running usage log, kept in-context so a downstream node (or, in Phase 6,
        # the analytics dashboard reading the final context) can see total AI spend
        # for the whole run, not just this one call.
        usage_log = list(new_context.get('_ai_usage', []))
        usage_log.append({
            'node': self.node.name,
            'provider': self.provider_code,
            'model': model,
            **(usage or {}),
        })
        new_context['_ai_usage'] = usage_log

        return {'output': new_context}

    def _get_credential(self, config):
        Credential = self.env['baramej.flow.credential'].sudo()
        credential_id = config.get('credential_id')

        if credential_id:
            credential = Credential.browse(int(credential_id))
            if not credential.exists():
                raise UserError(_('AI node "%s" references credential id %s, which no longer exists.') % (
                    self.node.name, credential_id))
        else:
            credential = Credential.search(
                [('provider', '=', self.provider_code), ('active', '=', True)], limit=1)
            if not credential:
                raise UserError(_('AI node "%s" has no credential_id set and no active "%s" credential exists. '
                                   'Add one under Baramej Flow → Configuration → Credentials.') % (
                    self.node.name, self.provider_code))

        if not credential.api_key:
            raise UserError(_('Credential "%s" has no API key set.') % credential.name)

        return credential

    def _call_provider(self, model, system_prompt, user_prompt, json_schema, api_key):
        """Subclasses implement this. Must return (result_dict, usage_dict).
        usage_dict should have prompt_tokens/completion_tokens/total_tokens keys
        where the provider makes them available; missing keys are fine."""
        raise NotImplementedError
