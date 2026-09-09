# -*- coding: utf-8 -*-
import uuid

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FlowForm(models.Model):
    _name = 'baramej.flow.form'
    _description = 'Baramej Flow - Web Form'
    _order = 'name'

    name = fields.Char(required=True)
    workflow_id = fields.Many2one(
        'baramej.flow.workflow', required=True, ondelete='cascade',
        domain=[('trigger_type', '=', 'form')],
        help='Only workflows with Trigger Type = "Web Form" are eligible — a form always '
             'triggers exactly one workflow.',
    )
    access_uuid = fields.Char(
        default=lambda self: str(uuid.uuid4()), readonly=True, copy=False, required=True,
        help='Used in the public URL. Not the same as the workflow\'s webhook_uuid, so a '
             'form and a generic webhook on the same workflow never collide.',
    )
    is_published = fields.Boolean(default=False)
    require_login = fields.Boolean(
        default=True,
        help='If enabled (default for the E-Service pattern), a public visitor is redirected to log in '
             'before they can submit. On submission, a baramej.flow.ticket is created — linked to their '
             'portal account — so they can track status, chat with staff, and resubmit if asked for more '
             'info. If disabled, the form works exactly as it did before this feature: any prior '
             'non-ticketed form keeps working unchanged.',
    )
    description = fields.Text(help='Shown to the public above the form fields.')
    submit_button_label = fields.Char(default='Submit')
    thank_you_message = fields.Text(default='Thank you — your submission has been received.')
    field_ids = fields.One2many('baramej.flow.form.field', 'form_id', string='Fields')
    public_url = fields.Char(compute='_compute_public_url')
    submission_count = fields.Integer(compute='_compute_submission_count')

    def _compute_public_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for rec in self:
            rec.public_url = '%s/flow/form/%s' % (base_url, rec.access_uuid) if rec.access_uuid else False

    def _compute_submission_count(self):
        Execution = self.env['baramej.flow.execution']
        for rec in self:
            rec.submission_count = Execution.search_count([
                ('workflow_id', '=', rec.workflow_id.id),
                ('trigger_source', '=', 'form'),
            ]) if rec.workflow_id else 0

    def action_publish(self):
        for rec in self:
            if not rec.field_ids:
                raise UserError(_('Add at least one field to "%s" before publishing it.') % rec.name)
            if rec.workflow_id.state != 'published':
                raise UserError(_('"%s" is linked to workflow "%s", which is not Published yet. '
                                   'Publish the workflow first — a live public form should never point at a '
                                   'draft workflow.') % (rec.name, rec.workflow_id.name))
            rec.is_published = True

    def action_unpublish(self):
        self.write({'is_published': False})

    def action_view_submissions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Submissions'),
            'res_model': 'baramej.flow.execution',
            'view_mode': 'list,form',
            'domain': [('workflow_id', '=', self.workflow_id.id), ('trigger_source', '=', 'form')],
        }
