# -*- coding: utf-8 -*-
"""
account.payment.register override — Auto-push Odoo payments to Cloudbeds.

When the user registers a payment via the native Odoo "Register Payment" button
on an invoice that is linked to a cloudbeds.reservation, this override
automatically calls POST /postPayment on Cloudbeds so both systems stay in sync.

To prevent double-push (e.g. when cloudbeds.payment.wizard already pushed),
pass ``skip_cloudbeds_push=True`` in the context.
"""
import logging
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class AccountPaymentRegisterCloudbeds(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        """After creating native Odoo payments, push them to Cloudbeds when applicable."""
        payments = super()._create_payments()

        # Explicit opt-out (e.g. cloudbeds.payment.wizard already pushed)
        if self.env.context.get('skip_cloudbeds_push'):
            return payments

        # Find invoices that are being reconciled by this wizard
        invoices = self.line_ids.move_id.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted'
        )
        if not invoices:
            return payments

        for invoice in invoices:
            reservation = self.env['cloudbeds.reservation'].search(
                [('invoice_id', '=', invoice.id)], limit=1
            )
            if not reservation:
                continue
            for payment in payments:
                self._push_to_cloudbeds(payment, reservation)

        return payments

    def _push_to_cloudbeds(self, payment, reservation):
        """Push a single Odoo payment to Cloudbeds via POST /postPayment."""
        backend = reservation.backend_id
        if not backend:
            return

        cb_type = self.env['cloudbeds.payment.method'].get_type_for_journal(
            backend, payment.journal_id
        )
        client = backend._get_api_client()

        try:
            result = client.post_payment(
                reservation_id=reservation.cb_reservation_id,
                payment_type=cb_type,
                amount=payment.amount,
                description=payment.ref or f'Odoo/{reservation.cb_reservation_id}',
            )
            if isinstance(result, dict) and result.get('success') is False:
                _logger.warning(
                    'Cloudbeds rejected auto-pushed payment for reservation %s: %s',
                    reservation.cb_reservation_id,
                    result.get('message', ''),
                )
                return

            _logger.info(
                'Auto-pushed Odoo payment to Cloudbeds: reservation=%s type=%s amount=%s',
                reservation.cb_reservation_id,
                cb_type,
                payment.amount,
            )

            # Link payment to reservation record
            reservation.write({'payment_ids': [(4, payment.id)]})

            # Refresh balanceDetailed on the reservation
            self._refresh_reservation_balance(reservation, client)

        except Exception as exc:
            _logger.warning(
                'Failed to auto-push payment to Cloudbeds for reservation %s: %s',
                reservation.cb_reservation_id,
                exc,
            )

    def _refresh_reservation_balance(self, reservation, client):
        """Re-fetch getReservation and update balance fields on the reservation."""
        try:
            result = client.get_reservation(reservation.cb_reservation_id)
            inv_data = (
                result.get('data') or result
                if isinstance(result, dict)
                else {}
            )
            bd = inv_data.get('balanceDetailed') or {}
            reservation.write({
                'cb_total_amount': float(
                    bd.get('grandTotal')
                    or inv_data.get('total')
                    or reservation.cb_total_amount
                ),
                'cb_total_paid': float(
                    bd.get('paid') or reservation.cb_total_paid
                ),
                'cb_balance': float(
                    inv_data.get('balance') or reservation.cb_balance
                ),
                'last_sync': fields.Datetime.now(),
            })
        except Exception as exc:
            _logger.warning(
                'Could not refresh reservation balance after auto-push: %s', exc
            )
