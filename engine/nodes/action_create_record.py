# -*- coding: utf-8 -*-
from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_dict
from .base import BaseNodeExecutor


@register_node('action_create_record')
class ActionCreateRecordExecutor(BaseNodeExecutor):
    """Creates an Odoo record from the node's configured model/values.

    Expected config_json:
        {
            "model": "res.partner",
            "values": {"name": "{{form.name}}", "email": "{{form.email}}"},
            "output_key": "created_partner_id"
        }

    "values" supports {{dotted.path}} placeholders resolved against the running
    context (see engine/utils.py). The new record's id is written back into the
    context under "output_key" (default: "record_id") so a later node — e.g. an
    Update Record node, or a Phase 2 notification node — can reference it.

    Runs with sudo() deliberately: workflows are meant to act with the automation's
    own authority (like a service account), not the permissions of whichever user
    happened to trigger it. Phase 6's audit trail will log every sudo() write made
    this way, since that's exactly the kind of action a government-tender security
    review will ask about.
    """

    def execute(self):
        config = self.node.get_config()
        model_name = config.get('model')
        if not model_name:
            raise UserError(_('Create Record node "%s" is missing "model" in its configuration.') % self.node.name)
        if model_name not in self.env:
            raise UserError(_('Create Record node "%s" references unknown model "%s".') % (self.node.name, model_name))

        values = render_dict(config.get('values', {}), self.context)
        record = self.env[model_name].sudo().create(values)

        output_key = config.get('output_key', 'record_id')
        new_context = dict(self.context)
        new_context[output_key] = record.id

        return {'output': new_context}
