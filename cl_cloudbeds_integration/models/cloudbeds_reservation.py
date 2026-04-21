# -*- coding: utf-8 -*-
"""
cloudbeds.reservation — Staging model for Cloudbeds reservations.

Full accounting pipeline:
  1. Fetch reservation invoice information from Cloudbeds
  2. Resolve (or create) Odoo partner from CB guest
  3. Create sale.order with room charge lines + service item lines + adjustment lines
  4. Confirm sale order
  5. Create invoice from sale order; add any missing lines (taxes, etc.)
  6. Post (confirm) invoice
  7. Register account.payment per CB payment, reconcile against invoice
  8. On cancellation after invoicing: create credit note
"""
import json
import logging
from odoo import _, api, fields, models, Command
from odoo.exceptions import UserError
from decimal import Decimal, ROUND_DOWN

_logger = logging.getLogger(__name__)


class CloudbedsReservation(models.Model):
    _name = 'cloudbeds.reservation'
    _description = 'Cloudbeds Reservation'
    _order = 'cb_check_in desc'
    _rec_name = 'name'

    # ------------------------------------------------------------------
    # Cloudbeds data
    # ------------------------------------------------------------------
    name = fields.Char(
        string='Reservation ID',
        required=True,
        copy=False,
        help='Cloudbeds reservation ID.',
    )
    backend_id = fields.Many2one(
        comodel_name='cloudbeds.backend',
        string='Backend',
        required=True,
        ondelete='cascade',
        index=True,
    )
    cb_reservation_id = fields.Char(
        string='CB Reservation ID',
        required=True,
        readonly=True,
        index=True,
        copy=False,
    )
    cb_status = fields.Selection(
        selection=[
            ('not_confirmed', 'Not Confirmed'),
            ('confirmed', 'Confirmed'),
            ('checked_in', 'Checked In'),
            ('checked_out', 'Checked Out'),
            ('canceled', 'Cancelled'),
            ('no_show', 'No Show'),
        ],
        string='CB Status',
        readonly=True,
        index=True,
    )
    cb_check_in = fields.Date(string='Check-In', readonly=True)
    cb_check_out = fields.Date(string='Check-Out', readonly=True)
    cb_guest_id = fields.Char(string='CB Guest ID', readonly=True)
    cb_guest_name = fields.Char(string='Guest Name', readonly=True)
    cb_total_amount = fields.Float(string='Total Amount', readonly=True, digits=(16, 2))
    cb_total_paid = fields.Float(string='Total Paid', readonly=True, digits=(16, 2))
    cb_balance = fields.Float(string='Balance', readonly=True, digits=(16, 2))
    cb_raw_data = fields.Text(
        string='Raw Invoice Data (JSON)',
        readonly=True,
        help='Full getReservationInvoiceInformation response.',
    )

    # ------------------------------------------------------------------
    # Odoo processing state
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('imported', 'Imported'),
            ('error', 'Error'),
        ],
        string='Odoo Status',
        default='pending',
        readonly=True,
        index=True,
    )
    error_message = fields.Char(string='Error Message', readonly=True)
    last_sync = fields.Datetime(string='Last Sync', readonly=True)

    # ------------------------------------------------------------------
    # Odoo document links
    # ------------------------------------------------------------------
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Partner',
        readonly=True,
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string='Sale Order',
        readonly=True,
        copy=False,
    )
    invoice_id = fields.Many2one(
        comodel_name='account.move',
        string='Invoice',
        readonly=True,
        copy=False,
    )
    payment_ids = fields.Many2many(
        comodel_name='account.payment',
        relation='cloudbeds_reservation_payment_rel',
        column1='reservation_id',
        column2='payment_id',
        string='Payments',
        readonly=True,
        copy=False,
    )
    payment_mapping_status = fields.Selection(
        selection=[('not_mapped', 'Not Mapped'), ('mapped', 'Mapped')],
        string='Payment Mapping Status',
        default='not_mapped',
        index=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            'cb_reservation_backend_unique',
            'UNIQUE(cb_reservation_id, backend_id)',
            'A Cloudbeds reservation can only be imported once per backend.',
        )
    ]

    # ------------------------------------------------------------------
    # Import entry point
    # ------------------------------------------------------------------

    # Cloudbeds uses both 'cancelled' and 'canceled' spellings across endpoints.
    _CB_STATUS_MAP = {
        'not_confirmed': 'not_confirmed',
        'confirmed': 'confirmed',
        'checked_in': 'checked_in',
        'checked_out': 'checked_out',
        'canceled': 'canceled',
        'cancelled': 'canceled',
        'no_show': 'no_show',
        'noshow': 'no_show',
    }

    @api.model
    def _import_reservations(self, backend, status=None, results_from=None, results_to=None):
        """
        Fetch reservations from Cloudbeds, bulk-upsert staging records,
        then batch-process pending records with shared caches.

        :returns: tuple(processed, failed)
        """
        client = backend._get_api_client()
        Log = self.env['cloudbeds.sync.log']

        try:
            all_reservations = list(client.get_all_reservations(
                status=status,
                results_from=results_from,
                results_to=results_to,
            ))
        except Exception as exc:
            Log._log(self.env, backend, 'reservation', 'cb_to_odoo', 'error', message=str(exc))
            raise

        # Phase 1: Bulk upsert — one pre-fetch query + batch create
        processed, failed, errors = self._bulk_upsert(all_reservations, backend)

        # Phase 2: Process pending with shared caches (avoids per-record lookups)
        pending = self.search([
            ('backend_id', '=', backend.id),
            ('state', '=', 'pending'),
        ])
        if pending:
            pending._process_batch(client, backend)

        state = 'success' if not failed else ('partial' if processed else 'error')
        Log._log(
            self.env, backend, 'reservation', 'cb_to_odoo', state,
            processed=processed, failed=failed,
            message='\n'.join(errors) if errors else None,
        )
        _logger.info(
            'Cloudbeds reservation sync: %d staged, %d failed for backend "%s".',
            processed, failed, backend.name,
        )
        return processed, failed

    def _bulk_upsert(self, reservations, backend):
        """
        Bulk upsert staging records.
        One SQL pre-fetch for all existing records, batch-create for new ones.
        """
        if not reservations:
            return 0, 0, []

        # Collect all CB IDs from the API response
        cb_ids = [str(r.get('reservationID') or '') for r in reservations]
        cb_ids = [c for c in cb_ids if c]

        # Single query to fetch ALL existing staging records for this backend
        self.env.cr.execute(
            "SELECT id, cb_reservation_id, cb_status, state, cb_balance"
            " FROM cloudbeds_reservation"
            " WHERE cb_reservation_id = ANY(%s) AND backend_id = %s",
            (cb_ids, backend.id),
        )
        existing_map = {r['cb_reservation_id']: r for r in self.env.cr.dictfetchall()}

        now = fields.Datetime.now()
        to_create = []
        processed = failed = 0
        errors = []

        for res_data in reservations:
            cb_id = str(res_data.get('reservationID') or '')
            if not cb_id:
                continue

            try:
                raw_status = res_data.get('status')
                status = self._CB_STATUS_MAP.get(raw_status, raw_status)

                vals = {
                    'name': cb_id,
                    'backend_id': backend.id,
                    'cb_reservation_id': cb_id,
                    'cb_status': status,
                    'cb_check_in': res_data.get('startDate') or res_data.get('checkIn'),
                    'cb_check_out': res_data.get('endDate') or res_data.get('checkOut'),
                    'cb_guest_id': str(res_data.get('guestID') or ''),
                    'cb_guest_name': res_data.get('guestName') or '',
                    'last_sync': now,
                }

                existing = existing_map.get(cb_id)
                if not existing:
                    to_create.append(dict(vals, state='pending'))
                    processed += 1
                    continue

                status_changed = existing['cb_status'] != status
                list_balance = float(res_data.get('balance') or 0)
                balance_changed = (
                    existing['state'] == 'imported'
                    and abs(list_balance - float(existing['cb_balance'] or 0)) > 0.01
                )

                # Nothing actionable — skip entirely
                if existing['state'] == 'imported' and not status_changed and not balance_changed:
                    processed += 1
                    continue

                if (status_changed or balance_changed) and existing['state'] == 'imported':
                    vals['state'] = 'pending'
                    vals['cb_balance'] = list_balance

                self.browse(existing['id']).write(vals)
                processed += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{cb_id}: {exc}")
                _logger.warning('Failed to upsert CB reservation %s: %s', cb_id, exc)

        # Batch-create all new records in one ORM call
        if to_create:
            try:
                self.create(to_create)
            except Exception:
                # Fallback: create individually so one bad record doesn't block all
                for vals in to_create:
                    try:
                        self.create(vals)
                    except Exception as exc:
                        failed += 1
                        errors.append(f"{vals.get('cb_reservation_id', '?')}: {exc}")

        return processed, failed, errors

    # ------------------------------------------------------------------
    # Processing dispatch
    # ------------------------------------------------------------------

    def _process_batch(self, client, backend):
        """
        Process multiple pending reservations with shared caches.
        Avoids per-record product/journal/tax lookups.
        """
        cache = self._build_cache(backend)
        total = len(self)
        processed = 0
        for rec in self:
            _logger.info("Processing reservation %s/%s", processed + 1, total)
            try:
                rec._process(client, cache)
                processed += 1
                _logger.info("Processed reservation %s/%s", processed, total)
            except Exception as exc:
                rec.write({'state': 'error', 'error_message': str(exc)[:250]})
                _logger.warning(
                    'Failed to process CB reservation %s: %s',
                    rec.cb_reservation_id, exc,
                )
        _logger.info("Result: Processed %s/%s reservations", processed, total)

    def _build_cache(self, backend):
        """
        Pre-load lookups once for the entire batch to avoid N+1 ORM searches.
        """
        # Room products: slug → product.product
        room_products = {}
        room_tmpl = self.env['product.template'].search([
            ('cb_item_id', '=like', 'room_%'),
            ('cb_backend_id', '=', backend.id),
        ])
        for tmpl in room_tmpl:
            room_products[tmpl.cb_item_id] = tmpl.product_variant_id

        # Adjustment / taxes products (singletons)
        adj_product = self.env['product.template'].search(
            [('cb_item_id', '=', '__cb_adjustment__')], limit=1
        )
        # Tax from mapping
        tax = backend._map_tax_from_cloudbeds()

        # Guest cache: cb_guest_id → res.partner
        guest_ids = list(set(r.cb_guest_id for r in self if r.cb_guest_id))
        guest_map = {}
        if guest_ids:
            partners = self.env['res.partner'].search([
                ('cb_guest_id', 'in', guest_ids),
                ('cb_backend_id', '=', backend.id),
            ])
            for p in partners:
                guest_map[p.cb_guest_id] = p

        return {
            'room_products': room_products,
            'adj_product': adj_product.product_variant_id if adj_product else None,
            'service_charge_product': backend.service_charge_product_id,
            'tax': tax,
            'guest_map': guest_map,
            'backend': backend,
        }

    def _delete_transaction(self):
        """Delete existing transaction for this reservation."""
        # Delete payment
        self.payment_ids.move_id.button_cancel()
        self.payment_ids.move_id.unlink()
        self.payment_ids.unlink()

        # Delete Invoice
        self.invoice_id.button_cancel()
        self.invoice_id.unlink()

        # Delete Sale Order
        self.sale_order_id._action_cancel()
        self.sale_order_id.unlink()

        # Set transaction to pending
        self.state = 'pending'

    def _process(self, client=None, cache=None):
        """
        Route to the appropriate processing pipeline based on CB status.

        Financial data comes from data.balanceDetailed:
          grandTotal, paid, balance
        """
        self.ensure_one()
        backend = self.backend_id

        # When called standalone (e.g. action_process button), build ad-hoc client/cache
        if not client:
            client = backend._get_api_client()
        if not cache:
            cache = self._build_cache(backend)

        try:
            result = client.get_reservation(self.cb_reservation_id)
            invoice_data = (
                result.get('data') or result
                if isinstance(result, dict) else {}
            )

            # ── Financial totals ────────────────────────────────────────
            balance_detail = invoice_data.get('balanceDetailed') or {}
            grand_total = float(
                balance_detail.get('grandTotal') or invoice_data.get('total') or 0
            )
            paid = float(balance_detail.get('paid') or 0)
            balance = float(invoice_data.get('balance') or 0)

            self.write({
                'cb_raw_data': json.dumps(invoice_data, indent=2),
                'cb_total_amount': grand_total,
                'cb_total_paid': paid,
                'cb_balance': balance,
            })

            if self.cb_status == 'not_confirmed':
                self._process_order(invoice_data, cache, confirmed=False)
                # Payment registration is handled manually via the Map Payment wizard.
                self._sync_payments_if_needed(invoice_data, cache)
            elif self.cb_status == 'confirmed':
                self._process_order(invoice_data, cache, confirmed=True)
                # Payment registration is handled manually via the Map Payment wizard.
                self._sync_payments_if_needed(invoice_data, cache)
            elif self.cb_status == 'checked_in':
                # Payment registration is handled manually via the Map Payment wizard.
                self._sync_payments_if_needed(invoice_data, cache)
                self.write({'state': 'imported', 'error_message': False})
            elif self.cb_status == 'checked_out':
                if grand_total > 0 and backend.auto_invoice_on_checkout:
                    if self.invoice_id:
                        # Payment registration is handled manually via the Map Payment wizard.
                        self._sync_payments_if_needed(invoice_data, cache)
                        self.write({'state': 'imported', 'error_message': False})
                    else:
                        self._process_checkout(invoice_data, cache)
                else:
                    self.write({'state': 'imported', 'error_message': False})
            elif self.cb_status in ('canceled', 'no_show'):
                self._process_cancellation(invoice_data)
            else:
                self.write({'state': 'imported', 'error_message': False})

        except Exception as exc:
            _logger.error(
                'Error processing CB reservation %s: %s',
                self.cb_reservation_id, exc, exc_info=True,
            )
            self.write({'state': 'error', 'error_message': str(exc)[:250]})
            raise

    def _resolve_guest(self, invoice_data, backend, cache):
        """
        Resolve guest from cache first, then guestList data, then fallback create.
        No extra API call — getReservation already includes guestList.
        """
        guest_map = cache.get('guest_map', {})

        # Try cache by cb_guest_id
        if self.cb_guest_id and self.cb_guest_id in guest_map:
            return guest_map[self.cb_guest_id]

        # Extract main guest from guestList in the reservation response
        guest_list = invoice_data.get('guestList') or {}
        main_guest_data = next(
            (g for g in guest_list.values() if g.get('isMainGuest')),
            next(iter(guest_list.values()), None) if guest_list else None,
        )
        guest_id = str(
            (main_guest_data or {}).get('guestID')
            or self.cb_guest_id
            or ''
        )

        # Check cache again with resolved guest_id
        if guest_id and guest_id in guest_map:
            return guest_map[guest_id]

        # Upsert from guestList data (no API call needed)
        if main_guest_data:
            partner = self.env['res.partner']._upsert_from_cloudbeds(
                self.env, backend, main_guest_data
            )
            if partner:
                guest_map[guest_id] = partner
                return partner

        # Fallback: search by name or create
        guest_name = (
            invoice_data.get('guestName')
            or self.cb_guest_name
            or f'CB Guest {guest_id}'
        )
        partner = self.env['res.partner'].search(
            [('name', '=', guest_name)], limit=1
        )
        if not partner:
            partner = self.env['res.partner'].create({
                'name': guest_name,
                'customer_rank': 1,
                'cb_guest_id': guest_id,
                'cb_backend_id': backend.id,
            })
        guest_map[guest_id] = partner
        return partner

    def _sync_payments_if_needed(self, invoice_data, cache):
        """Payment registration is handled manually via the Map Payment wizard."""
        return

    def _process_order(self, invoice_data, cache, confirmed=False):
        """Process not_confirmed / confirmed: create SO, optionally confirm."""
        self.ensure_one()
        backend = self.backend_id

        partner = self.partner_id or self._resolve_guest(invoice_data, backend, cache)

        sale_order = self.sale_order_id or self._create_sale_order(invoice_data, partner, cache)
        if confirmed and sale_order.state != 'sale':
            sale_order.action_confirm()
        elif not confirmed and sale_order.state != 'draft':
            sale_order.action_cancel()
            sale_order.action_draft()
        self.write({
            'partner_id': partner.id,
            'sale_order_id': sale_order.id,
            'state': 'imported',
            'error_message': False,
        })

    def _process_checkout(self, invoice_data, cache):
        """
        Full pipeline for a checked-out reservation:
        guest → sale order → invoice → post.

        Payment registration is deferred to the Map Payment wizard.
        """
        self.ensure_one()
        backend = self.backend_id

        partner = self.partner_id or self._resolve_guest(invoice_data, backend, cache)

        sale_order = self.sale_order_id or self._create_sale_order(invoice_data, partner, cache)
        if sale_order.state != 'sale':
            sale_order.action_confirm()

        invoice = self.invoice_id or self._create_invoice_from_so(sale_order, invoice_data)

        if backend.auto_confirm_invoices and invoice.state == 'draft':
            invoice.action_post()

        self.write({
            'partner_id': partner.id,
            'sale_order_id': sale_order.id,
            'invoice_id': invoice.id,
            'payment_mapping_status': 'not_mapped',
            'state': 'imported',
            'error_message': False,
        })

    def _process_cancellation(self, invoice_data):
        """
        If a posted invoice exists, create a credit note (reversal).
        """
        self.ensure_one()
        if self.sale_order_id and self.sale_order_id.state != 'cancel':
            self.sale_order_id.action_cancel()
            
        if (
            self.invoice_id
            and self.invoice_id.state == 'posted'
            and not self.invoice_id.reversal_move_ids
        ):
            move_reversal = self.env['account.move.reversal'].with_context(
                active_ids=[self.invoice_id.id],
                active_model='account.move',
            ).create({
                'reason': _('Reservation %s cancelled') % self.cb_reservation_id,
                'journal_id': self.invoice_id.journal_id.id,
            })
            reversal_result = move_reversal.reverse_moves()
            credit_note_id = (
                reversal_result.get('res_id')
                if isinstance(reversal_result, dict)
                else False
            )
            if credit_note_id:
                credit_note = self.env['account.move'].browse(credit_note_id)
                credit_note.action_post()

        self.write({'state': 'imported', 'error_message': False})

    # ------------------------------------------------------------------
    # Sale Order creation
    # ------------------------------------------------------------------

    def _create_sale_order(self, invoice_data, partner, cache=None):
        """
        Build a sale.order from a getReservation response.
        Uses cache for product/tax lookups when available.
        """
        self.ensure_one()
        backend = self.backend_id
        balance_detail = invoice_data.get('balanceDetailed') or {}
        taxes_fees = float(balance_detail.get('taxesFees') or 0.0)
        service_charge_product = (
            cache.get('service_charge_product') if cache else None
        ) or backend.service_charge_product_id
        base_service_charge = 0
        order_lines = []

        # Resolve tax once for all room lines
        tax = None
        if taxes_fees:
            tax = (cache.get('tax') if cache else None) or backend._map_tax_from_cloudbeds()
            if not tax:
                raise UserError("No tax found for reservation %s" % self.cb_reservation_id)

        tax_ids = [(6, 0, [tax.id])] if tax else []

        for room in (invoice_data.get('assigned') or invoice_data.get('unassigned')):
            room_type_name = room.get('roomTypeName') or 'Room'
            daily_rates = room.get('dailyRates') or []
            nights = len(daily_rates)
            room_total = float(balance_detail.get('subTotal') or 0.0)

            if nights == 0:
                continue

            rate_per_night = room_total / nights
            product = self._resolve_room_product(backend, room_type_name, cache)

            base_service_charge += room_total

            order_lines += [
                Command.create({
                    'product_id': product.id,
                    'product_uom': product.uom_id.id,
                    'product_uom_qty': nights,
                    'price_unit': rate_per_night,
                    'tax_id': tax_ids,
                })
            ]

        # Additional items
        additional = float(balance_detail.get('additionalItems') or 0.0)
        if additional > 0:
            adj_product = (
                cache.get('adj_product') if cache else None
            ) or self._get_or_create_adjustment_product()
            line_vals = {
                'product_uom_qty': 1.0,
                'price_unit': additional,
                'tax_id': tax_ids,
            }
            if adj_product:
                line_vals['product_id'] = adj_product.id
                line_vals['product_uom'] = adj_product.uom_id.id
            order_lines.append(Command.create(line_vals))
            base_service_charge += additional

        # Service Charge
        decimal_srv_charge = Decimal(base_service_charge * service_charge_product.cb_service_charge_percentage)
        service_charge_amount = float(
            decimal_srv_charge.quantize(Decimal('0.00'), rounding=ROUND_DOWN)
        )
        if service_charge_amount > 0:
            line_vals = {
                'name': 'Service Charge',
                'product_id': service_charge_product.id,
                'product_uom': service_charge_product.uom_id.id, 
                'product_uom_qty': 1.0,
                'price_unit': service_charge_amount,
                'tax_id': [],
            }
            order_lines.append(Command.create(line_vals))

        if not order_lines:
            raise UserError('There is no order line to create a sale order for reservation %s' % self.cb_reservation_id)

        sale = self.env['sale.order'].create({
            'partner_id': partner.id,
            'date_order': fields.Datetime.now(),
            'client_order_ref': self.cb_reservation_id,
            'origin': f'CB/{self.cb_reservation_id}',
            'order_line': order_lines,
        })
        diff = self.cb_total_amount - sale.amount_total
        if abs(diff) >= 0.005:
            # Adjust on a tax-free line so the diff maps 1:1 to amount_total
            no_tax_line = sale.order_line.filtered(lambda l: not l.tax_id)[-1:]
            if no_tax_line:
                no_tax_line.write({'price_unit': no_tax_line.price_unit + diff})
            else:
                # All lines have tax; compute tax-inclusive factor to offset exactly
                taxed_line = sale.order_line[-1:]
                tax_factor = 1.0 + sum(taxed_line.tax_id.mapped('amount')) / 100.0
                taxed_line.write({'price_unit': taxed_line.price_unit + diff / tax_factor})
        return sale

    def _create_invoice_from_so(self, sale_order, invoice_data):
        """Create an invoice from the confirmed sale order."""
        if not sale_order.invoice_ids:
            sale_order._create_invoices()
        invoice = sale_order.invoice_ids[:1]
        if not invoice:
            raise UserError(
                _('Could not create invoice from sale order %s.') % sale_order.name
            )

        # Set invoice date to checkout date (getReservation uses endDate)
        check_out = (
            invoice_data.get('endDate')
            or invoice_data.get('checkOut')
            or self.cb_check_out
        )
        if check_out:
            if invoice.state == 'posted':
                invoice.button_draft()
            invoice.write({'invoice_date': check_out})
            if invoice.state != 'posted':
                invoice.action_post()

        return invoice

    # ------------------------------------------------------------------
    # Payment registration
    # ------------------------------------------------------------------

    def _register_payments(self, invoice, invoice_data, cache=None):
        """
        Register only the *delta* between what Cloudbeds says is paid and what
        Odoo has already registered via this reservation's payment_ids.

        Safe to call multiple times — skips when delta <= 0.
        """
        self.ensure_one()
        backend = self.backend_id
        empty = self.env['account.payment']

        if not invoice or invoice.state != 'posted':
            return empty

        balance_detail = invoice_data.get('balanceDetailed') or {}
        paid = float(balance_detail.get('paid') or self.cb_total_paid or 0.0)
        balance = float(invoice_data.get('balance') or self.cb_balance or 0.0)

        if paid <= 0:
            return empty

        already_registered = sum(
            p.amount for p in self.payment_ids.filtered(lambda p: p.state == 'paid')
        )

        delta = round(paid - already_registered, 2)
        if delta <= 0.01:
            return empty

        payment_amount = min(delta, invoice.amount_residual or 0.0)
        if payment_amount <= 0.01:
            return empty

        journal = self.env['cloudbeds.payment.method'].get_journal(backend, 'cash')
        payment_ref = f'CB/{self.cb_reservation_id}'
        if balance > 0:
            payment_ref += f' (partial — balance {balance:,.0f})'

        payment = self._create_single_payment(
            invoice, journal, payment_amount,
            pay_data={'description': payment_ref},
        )
        if payment:
            self.write({'payment_ids': [(4, payment.id)]})
            return payment

        return empty

    def _create_single_payment(self, invoice, journal, amount, pay_data=None):
        """Create and post a single account.payment, then reconcile with invoice."""
        if not journal or amount <= 0:
            return None

        pay_date = None
        if pay_data:
            raw_date = pay_data.get('date') or ''
            if raw_date:
                try:
                    from datetime import datetime
                    pay_date = datetime.strptime(raw_date[:10], '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    pass

        payment_vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': invoice.partner_id.id or self.partner_id.id,
            'amount': amount,
            'journal_id': journal.id,
            'date': pay_date or invoice.invoice_date or fields.Date.today(),
            'memo': (
                pay_data.get('description') or pay_data.get('paymentID') or invoice.name
            ) if pay_data else invoice.name,
            'currency_id': self.backend_id.currency_id.id ,
        }

        try:
            payment = self.env['account.payment'].create(payment_vals)
            payment.action_post()
            payment.action_validate()
            _logger.info(
                'Created payment for reservation %s: %s (%s)',
                self.cb_reservation_id, payment.name, amount,
            )
            # Reconcile payment with invoice
            if payment.move_id:
                pass
            else:
                payment.move_id = self.env['account.move'].create({
                    'journal_id': payment.journal_id.id,
                    'partner_id': payment.partner_id.id,
                    'date': payment.date,
                    'currency_id': payment.currency_id.id,
                    'line_ids': [Command.create({
                        'account_id': payment.journal_id.default_account_id.id,
                        'debit': payment.amount,
                        'credit': 0,
                    }),
                    Command.create({
                        'account_id': payment.partner_id.property_account_receivable_id.id,
                        'debit': 0,
                        'credit': payment.amount,
                    }),
                    ]
                })
                payment.move_id.action_post()
                _logger.info('Created payment move for reservation %s: %s', self.cb_reservation_id, payment.move_id.name)
                payment_line_to_reconcile = payment.move_id.line_ids[-1]
                invoice_line_to_reconcile = invoice.line_ids.filtered(lambda x: x.account_id.id == payment.partner_id.property_account_receivable_id.id)
                if payment_line_to_reconcile and invoice_line_to_reconcile:
                    (payment_line_to_reconcile + invoice_line_to_reconcile).reconcile()
                    _logger.info('Reconciled payment %s with invoice %s', payment.move_id.name, invoice.name)

            return payment
        except Exception as exc:
            _logger.warning(
                'Could not create/post payment for reservation %s: %s',
                self.cb_reservation_id, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Product resolution helpers
    # ------------------------------------------------------------------

    def _resolve_room_product(self, backend, room_type_name, cache=None):
        """
        Find or create a service product for a Cloudbeds room type.
        Uses cache['room_products'] when available.
        """
        if not room_type_name:
            return None
        slug = 'room_' + room_type_name.lower().replace(' ', '_').replace('/', '_')

        # Check cache first
        room_products = cache.get('room_products', {}) if cache else {}
        if slug in room_products:
            return room_products[slug]

        product = self.env['product.template'].search(
            [('cb_item_id', '=', slug), ('cb_backend_id', '=', backend.id)],
            limit=1,
        )
        if product:
            variant = product.product_variant_id
        else:
            new_product = self.env['product.template'].create({
                'name': room_type_name,
                'type': 'service',
                'sale_ok': True,
                'purchase_ok': False,
                'cb_item_id': slug,
                'cb_backend_id': backend.id,
                'cb_sync_date': fields.Datetime.now(),
            })
            variant = new_product.product_variant_id

        # Store in cache for subsequent records
        if cache is not None:
            cache.setdefault('room_products', {})[slug] = variant
        return variant

    def _resolve_item_product(self, backend, item_id, item_name):
        """
        Find or create a service product for a Cloudbeds catalog item.
        """
        if item_id:
            product = self.env['product.template'].search(
                [
                    ('cb_item_id', '=', item_id),
                    ('cb_backend_id', '=', backend.id),
                ],
                limit=1,
            )
            if product:
                return product.product_variant_id

        # Create
        display_name = item_name or f'CB Item {item_id}'
        new_product = self.env['product.template'].create({
            'name': display_name,
            'type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
            'cb_item_id': item_id or f'item_{display_name.lower().replace(" ", "_")}',
            'cb_backend_id': backend.id,
            'cb_sync_date': fields.Datetime.now(),
        })
        return new_product.product_variant_id

    def _get_or_create_adjustment_product(self):
        """Return (or create) a generic 'Hotel Additional Items' service product."""
        product = self.env['product.template'].search(
            [('cb_item_id', '=', '__cb_adjustment__')],
            limit=1,
        )
        if product:
            return product.product_variant_id
        new_product = self.env['product.template'].create({
            'name': 'Hotel Additional Items',
            'type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
            'cb_item_id': '__cb_adjustment__',
        })
        return new_product.product_variant_id

    def _resolve_taxes(self, backend, taxes_list):
        """
        Resolve a list of CB tax dicts to an account.tax recordset.

        Each dict: {"taxName": "...", "taxID": "...", "taxAmount": 45.00}
        """
        tax_records = self.env['account.tax']
        for tax in (taxes_list or []):
            cb_tax_id = str(tax.get('taxID') or '')
            if cb_tax_id:
                mapped = self.env['cloudbeds.tax.mapping'].get_odoo_tax(backend, cb_tax_id)
                if mapped:
                    tax_records |= mapped
        return tax_records

    # ------------------------------------------------------------------
    # Manual action buttons
    # ------------------------------------------------------------------

    def action_register_payment(self):
        """
        Open the Register Payment wizard to push a payment to Cloudbeds
        and create the corresponding Odoo account.payment.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Register Payment to Cloudbeds'),
            'res_model': 'cloudbeds.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_reservation_id': self.id,
            },
        }

    def action_process(self):
        """Re-process reservations manually."""
        for res in self:
            try:
                res._process()
            except Exception as exc:
                res.write({'state': 'error', 'error_message': str(exc)[:250]})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Processing Complete'),
                'message': _('Selected reservations have been processed.'),
                'type': 'success',
            },
        }
    
    def action_recreate_transaction(self):
        """Recreate transaction for reservations."""
        for res in self:
            try:
                res._delete_transaction()
                res._process()
            except Exception as exc:
                res.write({'state': 'error', 'error_message': str(exc)[:250]})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Processing Complete'),
                'message': _('Selected reservations have been processed.'),
                'type': 'success',
            },
        }

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_('No sale order linked yet.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
        }

    def action_view_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_('No invoice linked yet.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
        }
