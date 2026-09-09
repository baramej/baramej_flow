# -*- coding: utf-8 -*-
from odoo import _
from odoo.exceptions import UserError

from ..node_registry import register_node
from ..utils import render_dict
from .base import BaseNodeExecutor


@register_node('action_update_record')
class ActionUpdateRecordExecutor(BaseNodeExecutor):
    """Updates an existing Odoo record whose id is already sitting in the context
    (typically written there by an earlier Create Record node, or, from Phase 2
    onward, by a trigger such as an incoming ticket record).

    Expected config_json:
        {
            "model": "res.partner",
            "record_id_key": "created_partner_id",
            "values": {"comment": "Updated by Baramej Flow"}
        }
    """

    def execute(self):
        config = self.node.get_config()
        model_name = config.get('model')
        record_id_key = config.get('record_id_key')

        if not model_name or not record_id_key:
            raise UserError(_('Update Record node "%s" needs both "model" and "record_id_key" in its '
                               'configuration.') % self.node.name)
        if model_name not in self.env:
            raise UserError(_('Update Record node "%s" references unknown model "%s".') % (self.node.name, model_name))

        record_id = self.context.get(record_id_key)
        if not record_id:
            raise UserError(_('Update Record node "%s" found no value under context key "%s" — check the node '
                               'that runs before this one is writing that key.') % (self.node.name, record_id_key))

        values = render_dict(config.get('values', {}), self.context)
        self.env[model_name].sudo().browse(int(record_id)).write(values)

        return {'output': self.context}
