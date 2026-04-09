# -*- coding: utf-8 -*-
import logging
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    cloudbeds_reservation_ids = fields.One2many(
        'cloudbeds.reservation', 'sale_order_id', string='Cloudbeds Reservations'
    )

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            for reservation in order.cloudbeds_reservation_ids.filtered(lambda x: x.cb_status != 'confirmed'):
                order._push_confirmed_status_to_cloudbeds(reservation)
        return res

    def _push_confirmed_status_to_cloudbeds(self, reservation):
        """Push reservationStatus='confirmed' to Cloudbeds via PUT /putReservation."""
        backend = reservation.backend_id
        if not backend:
            return
        try:
            client = backend._get_api_client()
            result = client.put_reservation({
                'reservationID': reservation.cb_reservation_id,
                'status': 'confirmed',
            })
            if isinstance(result, dict) and result.get('success') is False:
                _logger.warning(
                    'Cloudbeds rejected status update for reservation %s: %s',
                    reservation.cb_reservation_id,
                    result.get('message', ''),
                )
                return

            _logger.info(
                'Cloudbeds reservation %s set to confirmed (sale order %s).',
                reservation.cb_reservation_id,
                self.name,
            )
            # Keep local staging record in sync
            reservation.write({'cb_status': 'confirmed'})

        except Exception as exc:
            _logger.warning(
                'Failed to push confirmed status to Cloudbeds for reservation %s: %s',
                reservation.cb_reservation_id,
                exc,
            )
