# -*- coding: utf-8 -*-
"""
cloudbeds.map.payment.wizard — Assign Odoo payment journal ke reservasi terpilih.

Flow:
  1. User pilih satu atau beberapa reservasi di list view.
  2. Klik Action > Map Payment Journal.
  3. Wizard terbuka: tampilkan summary reservasi + selector journal.
  4. Konfirmasi → loop tiap reservasi → panggil _map_payment(journal).
  5. Tampilkan ringkasan hasil (sukses / gagal).
"""
import logging
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloudbedsMapPaymentWizard(models.TransientModel):
    _name = 'cloudbeds.map.payment.wizard'
    _description = 'Map Payment Journal to Reservations'

    reservation_ids = fields.Many2many(
        comodel_name='cloudbeds.reservation',
        string='Reservations',
        readonly=True,
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Payment Journal',
        domain=[('type', 'in', ['cash', 'bank'])],
        required=True,
    )

    # Computed summary fields untuk display di wizard
    reservation_count = fields.Integer(
        compute='_compute_summary',
        string='Total Reservations',
    )
    total_amount = fields.Float(
        compute='_compute_summary',
        string='Total Paid (CB)',
        digits=(16, 2),
    )
    already_mapped_count = fields.Integer(
        compute='_compute_summary',
        string='Already Mapped (will be re-mapped)',
    )
    invoiced_count = fields.Integer(
        compute='_compute_summary',
        string='Has Posted Invoice',
    )

    @api.depends('reservation_ids')
    def _compute_summary(self):
        for wizard in self:
            recs = wizard.reservation_ids
            wizard.reservation_count = len(recs)
            wizard.total_amount = sum(recs.mapped('cb_total_paid'))
            wizard.already_mapped_count = len(
                recs.filtered(lambda r: r.payment_mapping_status == 'mapped')
            )
            wizard.invoiced_count = len(
                recs.filtered(
                    lambda r: r.invoice_id and r.invoice_id.state == 'posted'
                )
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids') or []
        active_model = self.env.context.get('active_model')
        if active_ids and active_model == 'cloudbeds.reservation':
            res['reservation_ids'] = [(6, 0, active_ids)]
        return res

    def action_confirm(self):
        """Map payment journal ke semua reservasi terpilih."""
        self.ensure_one()
        journal = self.journal_id
        success = 0
        skipped = 0
        errors = []

        for rec in self.reservation_ids:
            if not rec.invoice_id or rec.invoice_id.state != 'posted':
                skipped += 1
                _logger.info(
                    'Skipped reservation %s: no posted invoice.', rec.cb_reservation_id
                )
                continue
            try:
                rec._map_payment(journal)
                success += 1
            except Exception as exc:
                errors.append(f'{rec.cb_reservation_id}: {exc}')
                _logger.warning(
                    'Failed to map payment for reservation %s: %s',
                    rec.cb_reservation_id, exc,
                )

        # Susun pesan hasil
        parts = []
        if success:
            parts.append(_('%d payment(s) mapped successfully.') % success)
        if skipped:
            parts.append(_('%d reservation(s) skipped (no posted invoice).') % skipped)
        if errors:
            parts.append(_('%d failed:\n') % len(errors) + '\n'.join(errors))

        msg_type = 'success' if not errors else ('warning' if success else 'danger')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Payment Mapping Complete'),
                'message': '\n'.join(parts),
                'type': msg_type,
                'sticky': bool(errors),
            },
        }
