# -*- coding: utf-8 -*-
"""
cloudbeds.payment.wizard — Register a payment from Odoo to Cloudbeds.

Flow:
  1. User opens wizard from the reservation form.
  2. Selects amount, payment type, description (and card type if credit).
  3. On confirm:
       a. POST /postPayment to Cloudbeds.
       b. Create + post an Odoo account.payment reconciled against the invoice.
       c. Re-fetch getReservation to refresh balanceDetailed on the staging record.
"""
import logging
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Cloudbeds accepted payment types (from postPayment Postman collection)
CB_PAYMENT_TYPES = [
    ('cash', 'Cash'),
    ('credit', 'Credit Card'),
    ('debit', 'Debit Card'),
    ('bank_transfer', 'Bank Transfer'),
    ('check', 'Check'),
    ('on_account', 'On Account'),
]

CB_CARD_TYPES = [
    ('visa', 'Visa'),
    ('mastercard', 'Mastercard'),
    ('amex', 'American Express'),
    ('discover', 'Discover'),
    ('diners', 'Diners Club'),
    ('jcb', 'JCB'),
    ('unionpay', 'UnionPay'),
]


class CloudbedsPaymentWizard(models.TransientModel):
    _name = 'cloudbeds.payment.wizard'
    _description = 'Register Payment to Cloudbeds'

    reservation_id = fields.Many2one(
        comodel_name='cloudbeds.reservation',
        string='Reservation',
        required=True,
        readonly=True,
    )
    # Read-only info fields
    cb_reservation_id = fields.Char(
        related='reservation_id.cb_reservation_id',
        string='CB Reservation ID',
        readonly=True,
    )
    cb_guest_name = fields.Char(
        related='reservation_id.cb_guest_name',
        string='Guest',
        readonly=True,
    )
    cb_total_amount = fields.Float(
        related='reservation_id.cb_total_amount',
        string='Grand Total',
        readonly=True,
        digits=(16, 2),
    )
    cb_total_paid = fields.Float(
        related='reservation_id.cb_total_paid',
        string='Already Paid',
        readonly=True,
        digits=(16, 2),
    )
    cb_balance = fields.Float(
        related='reservation_id.cb_balance',
        string='Balance Due',
        readonly=True,
        digits=(16, 2),
    )

    # Payment fields
    amount = fields.Float(
        string='Amount',
        required=True,
        digits=(16, 2),
    )
    payment_type = fields.Selection(
        selection=CB_PAYMENT_TYPES,
        string='Payment Type',
        required=True,
        default='cash',
    )
    card_type = fields.Selection(
        selection=CB_CARD_TYPES,
        string='Card Type',
        help='Required when Payment Type is Credit Card.',
    )
    description = fields.Char(
        string='Description / Reference',
        help='Visible in Cloudbeds as the payment note.',
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Odoo Journal',
        domain=[('type', 'in', ['cash', 'bank'])],
        help='Journal used for the Odoo account.payment record.',
    )
    register_in_odoo = fields.Boolean(
        string='Also Create Odoo Payment',
        default=True,
        help='Create and post an account.payment in Odoo and reconcile with the invoice.',
    )

    @api.onchange('payment_type')
    def _onchange_payment_type(self):
        """Auto-fill journal from mapped payment methods."""
        res = self.reservation_id
        if res and res.backend_id:
            journal = self.env['cloudbeds.payment.method'].get_journal(
                res.backend_id, self.payment_type
            )
            self.journal_id = journal

    @api.onchange('reservation_id')
    def _onchange_reservation_id(self):
        if self.reservation_id and self.reservation_id.cb_balance > 0:
            self.amount = self.reservation_id.cb_balance

    @api.constrains('amount')
    def _check_amount(self):
        for wizard in self:
            if wizard.amount <= 0:
                raise ValidationError(_('Payment amount must be greater than zero.'))

    @api.constrains('payment_type', 'card_type')
    def _check_card_type(self):
        for wizard in self:
            if wizard.payment_type == 'credit' and not wizard.card_type:
                raise ValidationError(
                    _('Card Type is required when Payment Type is Credit Card.')
                )

    def action_confirm(self):
        """Push payment to Cloudbeds and optionally create Odoo payment."""
        self.ensure_one()
        reservation = self.reservation_id
        backend = reservation.backend_id

        if not backend:
            raise UserError(_('No backend linked to this reservation.'))

        client = backend._get_api_client()

        # ── 1. Push payment to Cloudbeds ─────────────────────────────
        try:
            result = client.post_payment(
                reservation_id=reservation.cb_reservation_id,
                payment_type=self.payment_type,
                amount=self.amount,
                description=self.description or f'Odoo/{reservation.cb_reservation_id}',
                card_type=self.card_type if self.payment_type == 'credit' else None,
            )
            if isinstance(result, dict) and result.get('success') is False:
                raise UserError(
                    _('Cloudbeds rejected the payment: %s') % result.get('message', '')
                )
            _logger.info(
                'Cloudbeds payment posted for reservation %s: %s %s',
                reservation.cb_reservation_id, self.payment_type, self.amount,
            )
        except UserError:
            raise
        except Exception as exc:
            raise UserError(_('Failed to post payment to Cloudbeds: %s') % exc) from exc

        # ── 2. Create Odoo account.payment (optional) ─────────────────
        if self.register_in_odoo:
            invoice = reservation.invoice_id
            journal = self.journal_id
            if not journal:
                journal = self.env['cloudbeds.payment.method'].get_journal(
                    backend, self.payment_type
                )
            if invoice and invoice.state == 'posted' and journal:
                try:
                    pay_vals = {
                        'payment_type': 'inbound',
                        'partner_type': 'customer',
                        'partner_id': invoice.partner_id.id,
                        'amount': self.amount,
                        'journal_id': journal.id,
                        'date': fields.Date.today(),
                        'ref': (
                            self.description
                            or f'CB/{reservation.cb_reservation_id}'
                        ),
                        'currency_id': invoice.currency_id.id,
                    }
                    payment = self.env['account.payment'].create(pay_vals)
                    payment.action_post()

                    # Reconcile with invoice
                    inv_receivable = invoice.line_ids.filtered(
                        lambda l: l.account_id.account_type == 'asset_receivable'
                        and not l.reconciled
                    )
                    pay_receivable = payment.move_id.line_ids.filtered(
                        lambda l: l.account_id.account_type == 'asset_receivable'
                        and not l.reconciled
                    )
                    if inv_receivable and pay_receivable:
                        (inv_receivable + pay_receivable).reconcile()

                    # Link payment to reservation
                    reservation.write({
                        'payment_ids': [(4, payment.id)],
                    })
                except Exception as exc:
                    _logger.warning(
                        'Cloudbeds payment pushed to CB but Odoo payment failed: %s', exc
                    )

        # ── 3. Re-sync reservation to refresh balanceDetailed ─────────
        try:
            result = client.get_reservation(reservation.cb_reservation_id)
            inv_data = result.get('data') or result if isinstance(result, dict) else {}
            bd = inv_data.get('balanceDetailed') or {}
            reservation.write({
                'cb_total_amount': float(bd.get('grandTotal') or inv_data.get('total') or reservation.cb_total_amount),
                'cb_total_paid': float(bd.get('paid') or reservation.cb_total_paid),
                'cb_balance': float(inv_data.get('balance') or reservation.cb_balance),
                'last_sync': fields.Datetime.now(),
            })
        except Exception as exc:
            _logger.warning('Could not refresh reservation after payment: %s', exc)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Payment Registered'),
                'message': _(
                    'Payment of %s posted to Cloudbeds. Balance updated.'
                ) % f'{self.amount:,.2f}',
                'type': 'success',
                'sticky': False,
            },
        }
