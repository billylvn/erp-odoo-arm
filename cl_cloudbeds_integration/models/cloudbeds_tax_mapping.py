# -*- coding: utf-8 -*-
"""
cloudbeds.tax.mapping — Maps Cloudbeds tax IDs to Odoo account.tax records.

Records are auto-populated from getTaxesAndFees on connect.
The user then maps each CB tax to an Odoo account.tax manually.
"""
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CloudbedsTaxMapping(models.Model):
    _name = 'cloudbeds.tax.mapping'
    _description = 'Cloudbeds Tax Mapping'
    _order = 'cb_tax_name'

    backend_id = fields.Many2one(
        comodel_name='cloudbeds.backend',
        string='Backend',
        required=True,
        ondelete='cascade',
        index=True,
    )
    cb_tax_id = fields.Char(
        string='Cloudbeds Tax ID',
        required=True,
        help='The taxID value returned by Cloudbeds getTaxesAndFees.',
    )
    cb_tax_name = fields.Char(
        string='Cloudbeds Tax Name',
        help='Display name from Cloudbeds. Populated automatically on connect.',
    )
    cb_tax_type = fields.Char(
        string='CB Tax Type',
        help='Type from Cloudbeds (e.g. percent, flat).',
    )
    cb_tax_percentage = fields.Float(
        string='CB Percentage',
        digits=(5, 4),
        help='Percentage value from Cloudbeds, for reference when mapping.',
    )
    # Not required — user maps manually after auto-import
    tax_id = fields.Many2one(
        comodel_name='account.tax',
        string='Odoo Tax',
        required=False,
        domain=[('type_tax_use', '=', 'sale')],
        help='Leave empty until you have matched this CB tax to an Odoo tax.',
    )

    _sql_constraints = [
        (
            'cb_tax_id_backend_unique',
            'UNIQUE(cb_tax_id, backend_id)',
            'Each Cloudbeds tax ID can only be mapped once per backend.',
        )
    ]

    @api.model
    def get_odoo_tax(self, backend, cb_tax_id):
        """
        Return the mapped account.tax for a given Cloudbeds tax ID.
        Returns False if no mapping is found or mapping has no Odoo tax set.
        """
        if not cb_tax_id:
            return False
        mapping = self.search(
            [('backend_id', '=', backend.id), ('cb_tax_id', '=', str(cb_tax_id))],
            limit=1,
        )
        if mapping and mapping.tax_id:
            return mapping.tax_id
        _logger.debug(
            'No tax mapping for CB tax ID "%s" on backend "%s".',
            cb_tax_id,
            backend.name,
        )
        return False
