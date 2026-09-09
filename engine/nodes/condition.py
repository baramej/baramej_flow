# -*- coding: utf-8 -*-
import operator as op

from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import get_nested
from .base import BaseNodeExecutor

_OPERATORS = {
    '==': op.eq,
    '!=': op.ne,
    '>': op.gt,
    '<': op.lt,
    '>=': op.ge,
    '<=': op.le,
    'contains': lambda a, b: b in (a or ''),
    'not_contains': lambda a, b: b not in (a or ''),
}


@register_node('condition')
class ConditionExecutor(BaseNodeExecutor):
    """Evaluates one field from the context against a value and routes down the
    'true' or 'false' outgoing edge accordingly. This is intentionally a single flat
    comparison for Phase 1 — Phase 3's AI structured-output nodes will feed a
    classification result straight into this same node type (e.g. field:
    "ai_result.priority", value: "high"), and Phase 4 will add an AND/OR condition
    group builder for multi-field logic without changing this node's core contract.

    Expected config_json:
        {"field": "priority", "operator": "==", "value": "high"}

    "field" supports dotted paths into nested context, e.g. "ai_result.confidence" —
    see engine/utils.py get_nested() for the resolution rule.
    """

    def execute(self):
        config = self.node.get_config()
        field_key = config.get('field')
        operator_key = config.get('operator', '==')
        expected = config.get('value')

        if not field_key:
            raise UserError(_('Condition node "%s" is missing "field" in its configuration.') % self.node.name)

        compare_fn = _OPERATORS.get(operator_key)
        if not compare_fn:
            raise UserError(_('Condition node "%s" uses unsupported operator "%s". Supported: %s') % (
                self.node.name, operator_key, ', '.join(_OPERATORS.keys())))

        actual = get_nested(self.context, field_key)
        result = bool(compare_fn(actual, expected))
        branch = 'true' if result else 'false'

        return {'output': self.context, 'branch': branch}
