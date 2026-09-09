# -*- coding: utf-8 -*-
from ..node_registry import register_node
from .base import BaseNodeExecutor


@register_node('trigger_manual')
class TriggerManualExecutor(BaseNodeExecutor):
    """Entry point for a manually-run workflow (the 'Run Test' button, or Phase 1's
    only supported trigger). Passes the initial context straight through unchanged —
    it exists mainly so every workflow has an explicit, visible starting node on the
    canvas, matching the pattern every later trigger type (webhook, email, WhatsApp,
    form, cron) will follow."""

    def execute(self):
        return {'output': self.context}
