# -*- coding: utf-8 -*-
import re

from odoo import fields, models, api
from odoo.exceptions import ValidationError

_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')


class FlowFormField(models.Model):
    _name = 'baramej.flow.form.field'
    _description = 'Baramej Flow - Form Field'
    _order = 'sequence, id'

    form_id = fields.Many2one('baramej.flow.form', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(
        required=True,
        help='Technical name. This becomes the context key the workflow reads, e.g. '
             '{{form.<name>}}. Lowercase letters, numbers, and underscores only.',
    )
    label = fields.Char(required=True, help='Shown to the person filling out the form.')
    field_type = fields.Selection(
        [
            ('char', 'Single Line Text'),
            ('text', 'Multi-line Text'),
            ('email', 'Email'),
            ('integer', 'Number'),
            ('float', 'Decimal Number'),
            ('date', 'Date'),
            ('datetime', 'Date & Time'),
            ('boolean', 'Yes / No'),
            ('selection', 'Dropdown'),
            ('file', 'File Upload'),
        ],
        required=True, default='char',
    )
    required = fields.Boolean(default=False)
    selection_options = fields.Char(
        help='Only used when Field Type is Dropdown. Comma-separated, e.g. "Low,Medium,High".',
    )
    placeholder = fields.Char()

    @api.constrains('name')
    def _check_name_format(self):
        for rec in self:
            if not _NAME_RE.match(rec.name or ''):
                raise ValidationError(
                    'Field technical name "%s" is invalid — use lowercase letters, numbers, and '
                    'underscores only, starting with a letter (e.g. "customer_email").' % rec.name
                )

    @api.constrains('form_id', 'name')
    def _check_name_unique_per_form(self):
        for rec in self:
            duplicate = self.search_count([
                ('form_id', '=', rec.form_id.id),
                ('name', '=', rec.name),
                ('id', '!=', rec.id),
            ])
            if duplicate:
                raise ValidationError(
                    'Field name "%s" is already used elsewhere on this form — technical names must be '
                    'unique per form.' % rec.name
                )
