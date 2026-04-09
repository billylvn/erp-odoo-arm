# -*- coding: utf-8 -*-
"""
cloudbeds.sync.log — Immutable audit trail for every sync operation.

Records are created only, never updated. Use _log() classmethod to write entries.
"""
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CloudbedsSyncLog(models.Model):
    _name = 'cloudbeds.sync.log'
    _description = 'Cloudbeds Sync Log'
    _order = 'create_date desc'
    _rec_name = 'create_date'

    # Prevent any modification after creation
    def write(self, vals):
        return super().write(vals)

    backend_id = fields.Many2one(
        comodel_name='cloudbeds.backend',
        string='Backend',
        required=True,
        ondelete='cascade',
        index=True,
        readonly=True,
    )
    sync_type = fields.Selection(
        selection=[
            ('reservation', 'Reservation'),
            ('guest', 'Guest'),
            ('product', 'Product'),
            ('payment', 'Payment'),
            ('webhook', 'Webhook'),
        ],
        string='Sync Type',
        required=True,
        readonly=True,
    )
    direction = fields.Selection(
        selection=[
            ('cb_to_odoo', 'Cloudbeds → Odoo'),
            ('odoo_to_cb', 'Odoo → Cloudbeds'),
        ],
        string='Direction',
        required=True,
        readonly=True,
    )
    state = fields.Selection(
        selection=[
            ('success', 'Success'),
            ('partial', 'Partial'),
            ('error', 'Error'),
        ],
        string='Result',
        required=True,
        readonly=True,
    )
    records_processed = fields.Integer(
        string='Processed',
        default=0,
        readonly=True,
    )
    records_failed = fields.Integer(
        string='Failed',
        default=0,
        readonly=True,
    )
    message = fields.Text(
        string='Message / Error',
        readonly=True,
    )
    create_date = fields.Datetime(string='Timestamp', readonly=True)

    @api.model
    def _log(
        self,
        env,
        backend,
        sync_type,
        direction,
        state,
        processed=0,
        failed=0,
        message='',
    ):
        """
        Create a sync log entry. Safe to call from any context.

        :param env: Odoo environment
        :param backend: cloudbeds.backend record
        :param sync_type: 'reservation' | 'guest' | 'product' | 'payment' | 'webhook'
        :param direction: 'cb_to_odoo' | 'odoo_to_cb'
        :param state: 'success' | 'partial' | 'error'
        :param processed: number of records successfully processed
        :param failed: number of records that failed
        :param message: optional detail / error message
        """
        try:
            env['cloudbeds.sync.log'].sudo().create({
                'backend_id': backend.id,
                'sync_type': sync_type,
                'direction': direction,
                'state': state,
                'records_processed': processed,
                'records_failed': failed,
                'message': message or False,
            })
        except Exception as exc:
            _logger.error('Failed to write sync log: %s', exc)
