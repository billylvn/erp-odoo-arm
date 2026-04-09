# -*- coding: utf-8 -*-
"""
cloudbeds.payment.method — Maps Cloudbeds payment methods to Odoo account.journal.

Records are auto-populated from getPaymentMethods on connect.
A default journal is assigned automatically; the user can remap as needed.

Cloudbeds paymentMethodType values: cash, credit, debit, bank_transfer,
check, on_account, ota_collect, comp, custom, and any property-specific types.
"""
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Auto-assign journal type by Cloudbeds payment method type
_JOURNAL_TYPE_MAP = {
    'cash': 'cash',
    'comp': 'cash',
    'credit': 'bank',
    'debit': 'bank',
    'bank_transfer': 'bank',
    'check': 'bank',
    'on_account': 'bank',
    'ota_collect': 'bank',
    'custom': 'bank',
}


class CloudbedsPaymentMethod(models.Model):
    _name = 'cloudbeds.payment.method'
    _description = 'Cloudbeds Payment Method Mapping'
    _order = 'cb_payment_method_name'

    backend_id = fields.Many2one(
        comodel_name='cloudbeds.backend',
        string='Backend',
        required=True,
        ondelete='cascade',
        index=True,
    )
    cb_payment_method_id = fields.Char(
        string='Cloudbeds Method ID',
        help='The paymentMethodID from Cloudbeds getPaymentMethods.',
        index=True,
    )
    cb_payment_method_name = fields.Char(
        string='Cloudbeds Method Name',
        help='Display name from Cloudbeds, e.g. "Visa Credit Card".',
    )
    cb_payment_type = fields.Char(
        string='Cloudbeds Payment Type',
        required=True,
        help=(
            'The paymentMethodType from Cloudbeds, e.g.: '
            'cash, credit, debit, bank_transfer, check, on_account, '
            'ota_collect, comp, custom.'
        ),
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Odoo Journal',
        required=True,
        domain=[('type', 'in', ['cash', 'bank'])],
    )

    _sql_constraints = [
        (
            'cb_payment_method_backend_unique',
            'UNIQUE(cb_payment_method_id, backend_id)',
            'Each Cloudbeds payment method ID can only be mapped once per backend.',
        )
    ]

    @api.model
    def get_journal(self, backend, cb_payment_type):
        """
        Return the mapped journal for a Cloudbeds payment type.
        Falls back to the first bank/cash journal if no mapping is found.

        :param backend: cloudbeds.backend record
        :param cb_payment_type: string, e.g. 'cash', 'credit', 'debit'
        :returns: account.journal record
        """
        if cb_payment_type:
            mapping = self.search(
                [
                    ('backend_id', '=', backend.id),
                    ('cb_payment_type', '=', cb_payment_type.lower()),
                ],
                limit=1,
            )
            if mapping:
                return mapping.journal_id

        _logger.debug(
            'No payment method mapping for CB type "%s" on backend "%s" — using fallback.',
            cb_payment_type,
            backend.name,
        )
        fallback = self.env['account.journal'].search(
            [
                ('type', 'in', ['bank', 'cash']),
                ('company_id', '=', self.env.company.id),
            ],
            limit=1,
        )
        return fallback

    @api.model
    def get_type_for_journal(self, backend, journal):
        """
        Reverse lookup: return the Cloudbeds payment type for an Odoo journal.
        Falls back to 'cash' for cash journals, 'bank_transfer' otherwise.

        :param backend: cloudbeds.backend record
        :param journal: account.journal record
        :returns: string CB payment type, e.g. 'cash', 'credit', 'bank_transfer'
        """
        if journal:
            mapping = self.search(
                [
                    ('backend_id', '=', backend.id),
                    ('journal_id', '=', journal.id),
                ],
                limit=1,
            )
            if mapping:
                return mapping.cb_payment_type
        # Fallback based on journal type
        if journal and journal.type == 'cash':
            return 'cash'
        return 'bank_transfer'
