# -*- coding: utf-8 -*-
"""Small templating helper so node config values can reference prior context, e.g.
{"name": "{{form.name}}"}. Deliberately minimal for Phase 1 — no filters, no
expressions, just dotted-path lookup into the execution context. Phase 3's AI
structured-output nodes and Phase 4's HTTP Request node will lean on this same
helper rather than inventing a second templating syntax.
"""
import re

_VAR_PATTERN = re.compile(r"\{\{\s*([\w\.]+)\s*\}\}")


def render_template(value, context):
    """Replace {{dotted.path}} placeholders in `value` using `context`. Non-string
    values pass through unchanged. If a path can't be resolved, the placeholder is
    left as-is rather than raising, so a missing optional field doesn't break the
    whole node — the gap is visible in the node's logged output for debugging."""
    if not isinstance(value, str):
        return value

    def _replace(match):
        path = match.group(1).split('.')
        data = context
        for part in path:
            if isinstance(data, dict) and part in data:
                data = data[part]
            else:
                return match.group(0)
        return str(data)

    return _VAR_PATTERN.sub(_replace, value)


def render_dict(values, context):
    """Apply render_template to every value in a dict. Used for node config blocks
    like {"values": {"name": "{{form.name}}", "email": "{{form.email}}"}}."""
    return {key: render_template(val, context) for key, val in (values or {}).items()}


def get_nested(context, dotted_path):
    """Resolve a dotted path like 'ai_result.confidence' against a context dict.
    Returns None if any segment is missing rather than raising — a missing optional
    field should surface as a false condition, not a crash, matching the
    forgiving-by-design approach the rest of this module already takes.

    This is what lets Phase 3's AI nodes feed a classification straight into the
    existing Condition node without a new node type: an AI node writes
    context['ai_result'] = {'category': ..., 'confidence': ...}, and a Condition
    node right after it can branch on field="ai_result.confidence", exactly as
    condition.py's docstring already anticipated back in Phase 1.
    """
    data = context
    for part in (dotted_path or '').split('.'):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data
