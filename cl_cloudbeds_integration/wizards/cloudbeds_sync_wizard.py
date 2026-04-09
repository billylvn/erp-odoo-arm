# -*- coding: utf-8 -*-
"""
cloudbeds.sync.wizard — On-demand sync dialog.

Lets users pick which sync types to run and optionally filter by date range.
"""
import logging
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CloudbedsSyncWizard(models.TransientModel):
    _name = 'cloudbeds.sync.wizard'
    _description = 'Cloudbeds Manual Sync Wizard'

    backend_id = fields.Many2one(
        comodel_name='cloudbeds.backend',
        string='Backend',
        required=True,
        domain=[('state', '=', 'connected')],
    )
    sync_guests = fields.Boolean(string='Sync Guests', default=True)
    sync_products = fields.Boolean(string='Sync Products', default=True)
    sync_reservations = fields.Boolean(string='Sync Reservations', default=True)
    reservation_status = fields.Selection(
        selection=[
            ('checked_out', 'Checked-Out Only'),
            ('all', 'All Statuses'),
        ],
        string='Reservation Filter',
        default='checked_out',
    )
    results_from = fields.Date(string='Date From')
    results_to = fields.Date(string='Date To')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Pre-select active backend if launched from its form
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')
        if active_id and active_model == 'cloudbeds.backend':
            backend = self.env['cloudbeds.backend'].browse(active_id)
            if backend.exists() and backend.state == 'connected':
                res['backend_id'] = backend.id
        return res

    def action_run_sync(self):
        self.ensure_one()
        backend = self.backend_id
        results = []

        if self.sync_guests:
            try:
                self.env['res.partner']._cloudbeds_pull_guests(backend)
                results.append('Guests synced.')
            except Exception as exc:
                results.append('Guests FAILED: %s' % exc)
                _logger.error('Manual guest sync failed: %s', exc, exc_info=True)

        if self.sync_products:
            try:
                self.env['product.template']._cloudbeds_pull_items(backend)
                results.append('Products synced.')
            except Exception as exc:
                results.append('Products FAILED: %s' % exc)
                _logger.error('Manual product sync failed: %s', exc, exc_info=True)

        if self.sync_reservations:
            try:
                status_filter = None if self.reservation_status == 'all' else 'checked_out'
                from_date = self.results_from.strftime('%Y-%m-%d') if self.results_from else None
                to_date = self.results_to.strftime('%Y-%m-%d') if self.results_to else None
                count, _ = self.env['cloudbeds.reservation']._import_reservations(
                    backend, status=status_filter, results_from=from_date, results_to=to_date
                )
                results.append('%s reservation(s) staged.' % str(count))
            except Exception as exc:
                results.append('Reservations FAILED: %s' % exc)
                _logger.error('Manual reservation sync failed: %s', exc, exc_info=True)

        backend.write({'last_sync': fields.Datetime.now()})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Complete',
                'message': '\n'.join(results),
                'type': 'success',
                'sticky': False,
            },
        }
