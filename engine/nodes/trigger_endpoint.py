# -*- coding: utf-8 -*-
"""Trigger nodes for the Phase 2 entry points. All four are deliberately simple
passthroughs — the real work (parsing a webhook payload, an email, a WhatsApp
message, or a form submission) happens in controllers/ or models/workflow_inbound_mail.py
*before* the engine is invoked, and lands in the context under a predictable key
('webhook', 'email', 'whatsapp', or 'form'). These nodes exist purely so every
workflow has a visible, correctly-labeled starting node on the canvas that matches
its actual trigger type, the same as trigger_manual in Phase 1.
"""
from ..node_registry import register_node
from .base import BaseNodeExecutor


class _PassthroughTrigger(BaseNodeExecutor):
    def execute(self):
        return {'output': self.context}


@register_node('trigger_webhook')
class TriggerWebhookExecutor(_PassthroughTrigger):
    """Context is pre-populated by controllers/webhook_controller.py under the
    'webhook' key, e.g. context['webhook'] = <parsed JSON body>."""


@register_node('trigger_email')
class TriggerEmailExecutor(_PassthroughTrigger):
    """Context is pre-populated by models/workflow_inbound_mail.py under the 'email'
    key: context['email'] = {'from': ..., 'subject': ..., 'body': ...}."""


@register_node('trigger_whatsapp')
class TriggerWhatsappExecutor(_PassthroughTrigger):
    """Context is pre-populated by controllers/webhook_controller.py under the
    'whatsapp' key: context['whatsapp'] = {'from': ..., 'text': ..., 'type': ...}."""


@register_node('trigger_form')
class TriggerFormExecutor(_PassthroughTrigger):
    """Context is pre-populated by controllers/form_controller.py under the 'form'
    key: context['form'] = {field_name: submitted_value, ...}."""
