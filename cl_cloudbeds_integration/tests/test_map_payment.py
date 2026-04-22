# -*- coding: utf-8 -*-
"""
Tests untuk payment mapping feature di cloudbeds.reservation.

Jalankan:
  ./odoo-bin -t cl_cloudbeds_integration.tests.test_map_payment -d <db>
"""
from unittest.mock import patch
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestMapPayment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Journal
        cls.journal_cash = cls.env['account.journal'].search(
            [('type', '=', 'cash'), ('company_id', '=', cls.env.company.id)],
            limit=1,
        )
        cls.journal_bank = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)],
            limit=1,
        )
        # Partner
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Guest CB',
            'customer_rank': 1,
        })
        # Backend (minimal — tidak butuh OAuth untuk test ini)
        cls.backend = cls.env['cloudbeds.backend'].create({
            'name': 'Test Backend',
            'property_id': 'TEST001',
            'client_id': 'fake_client',
            'client_secret': 'fake_secret',
            'currency_id': cls.env.company.currency_id.id,
        })
        # Product
        cls.product = cls.env['product.product'].create({
            'name': 'Room Test',
            'type': 'service',
        })
        # Account revenue
        cls.account_revenue = cls.env['account.account'].search(
            [
                ('account_type', '=', 'income'),
                ('company_id', '=', cls.env.company.id),
            ],
            limit=1,
        )

    def _create_posted_invoice(self, amount=500000.0):
        """Helper: buat invoice posted untuk testing."""
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': amount,
                'account_id': self.account_revenue.id,
            })],
        })
        invoice.action_post()
        return invoice

    def _create_reservation(self, invoice=None, paid=500000.0):
        """Helper: buat staging reservation."""
        return self.env['cloudbeds.reservation'].create({
            'name': 'TEST-001',
            'backend_id': self.backend.id,
            'cb_reservation_id': 'TEST-001',
            'cb_status': 'checked_out',
            'cb_total_amount': paid,
            'cb_total_paid': paid,
            'cb_balance': 0.0,
            'partner_id': self.partner.id,
            'invoice_id': invoice.id if invoice else False,
            'state': 'imported',
            'payment_mapping_status': 'not_mapped',
        })

    # ── Tests ─────────────────────────────────────────────────────────────

    def test_map_payment_creates_payment_and_sets_mapped(self):
        """_map_payment harus buat account.payment dan set status 'mapped'."""
        invoice = self._create_posted_invoice()
        reservation = self._create_reservation(invoice)

        reservation._map_payment(self.journal_cash)

        self.assertEqual(reservation.payment_mapping_status, 'mapped')
        self.assertTrue(reservation.payment_ids)
        payment = reservation.payment_ids[0]
        self.assertEqual(payment.journal_id, self.journal_cash)
        self.assertEqual(payment.state, 'posted')

    def test_map_payment_reconciles_with_invoice(self):
        """_map_payment harus reconcile payment dengan invoice."""
        invoice = self._create_posted_invoice(amount=300000.0)
        reservation = self._create_reservation(invoice, paid=300000.0)

        reservation._map_payment(self.journal_cash)

        # Invoice harus ter-reconcile (amount_residual mendekati 0)
        self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)

    def test_map_payment_raises_without_invoice(self):
        """_map_payment harus raise UserError jika tidak ada invoice posted."""
        reservation = self._create_reservation(invoice=None)

        with self.assertRaises(UserError):
            reservation._map_payment(self.journal_cash)

    def test_map_payment_raises_without_journal(self):
        """_map_payment harus raise UserError jika journal None/empty."""
        invoice = self._create_posted_invoice()
        reservation = self._create_reservation(invoice)
        empty_journal = self.env['account.journal']

        with self.assertRaises(UserError):
            reservation._map_payment(empty_journal)

    def test_remap_payment_replaces_old_payment(self):
        """Re-mapping harus cancel+delete payment lama dan buat yang baru."""
        invoice = self._create_posted_invoice(amount=400000.0)
        reservation = self._create_reservation(invoice, paid=400000.0)

        # Map pertama dengan cash
        reservation._map_payment(self.journal_cash)
        old_payment_id = reservation.payment_ids[0].id

        # Remap dengan bank
        reservation._map_payment(self.journal_bank)

        # Payment lama tidak ada lagi
        old_payment = self.env['account.payment'].search(
            [('id', '=', old_payment_id)]
        )
        self.assertFalse(old_payment, 'Payment lama harus dihapus saat remap')

        # Payment baru pakai journal bank
        self.assertEqual(reservation.payment_ids[0].journal_id, self.journal_bank)
        self.assertEqual(reservation.payment_mapping_status, 'mapped')

    def test_payment_mapping_status_default_not_mapped(self):
        """Reservasi baru harus punya payment_mapping_status = 'not_mapped'."""
        reservation = self._create_reservation()
        self.assertEqual(reservation.payment_mapping_status, 'not_mapped')

    def test_wizard_default_get_populates_reservation_ids(self):
        """Wizard harus populate reservation_ids dari active_ids context."""
        invoice = self._create_posted_invoice()
        reservation = self._create_reservation(invoice)

        wizard = self.env['cloudbeds.map.payment.wizard'].with_context(
            active_ids=[reservation.id],
            active_model='cloudbeds.reservation',
        ).create({'journal_id': self.journal_cash.id})

        self.assertIn(reservation, wizard.reservation_ids)

    def test_wizard_action_confirm_maps_all_reservations(self):
        """Wizard action_confirm harus map semua reservasi yang punya invoice posted."""
        invoice1 = self._create_posted_invoice(amount=100000.0)
        invoice2 = self._create_posted_invoice(amount=200000.0)

        res1 = self.env['cloudbeds.reservation'].create({
            'name': 'TEST-W1', 'backend_id': self.backend.id,
            'cb_reservation_id': 'TEST-W1', 'cb_status': 'checked_out',
            'cb_total_amount': 100000.0, 'cb_total_paid': 100000.0,
            'cb_balance': 0.0, 'partner_id': self.partner.id,
            'invoice_id': invoice1.id, 'state': 'imported',
            'payment_mapping_status': 'not_mapped',
        })
        res2 = self.env['cloudbeds.reservation'].create({
            'name': 'TEST-W2', 'backend_id': self.backend.id,
            'cb_reservation_id': 'TEST-W2', 'cb_status': 'checked_out',
            'cb_total_amount': 200000.0, 'cb_total_paid': 200000.0,
            'cb_balance': 0.0, 'partner_id': self.partner.id,
            'invoice_id': invoice2.id, 'state': 'imported',
            'payment_mapping_status': 'not_mapped',
        })

        wizard = self.env['cloudbeds.map.payment.wizard'].create({
            'reservation_ids': [(6, 0, [res1.id, res2.id])],
            'journal_id': self.journal_cash.id,
        })
        wizard.action_confirm()

        self.assertEqual(res1.payment_mapping_status, 'mapped')
        self.assertEqual(res2.payment_mapping_status, 'mapped')

    def test_wizard_skips_reservation_without_invoice(self):
        """Wizard harus skip (tidak error) reservasi tanpa invoice posted."""
        res_no_invoice = self._create_reservation(invoice=None)

        wizard = self.env['cloudbeds.map.payment.wizard'].create({
            'reservation_ids': [(6, 0, [res_no_invoice.id])],
            'journal_id': self.journal_cash.id,
        })
        result = wizard.action_confirm()

        self.assertEqual(res_no_invoice.payment_mapping_status, 'not_mapped')
        self.assertEqual(result.get('tag'), 'display_notification')

    def test_build_cache_no_longer_contains_payment_journal(self):
        """_build_cache tidak boleh lagi memuat payment_journal."""
        cache = self.env['cloudbeds.reservation']._build_cache(self.backend)
        self.assertNotIn('payment_journal', cache)
