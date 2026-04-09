# -*- coding: utf-8 -*-
"""
cloudbeds_product — Extends product.template to link with Cloudbeds catalog items.

Room types from reservations also become service products using the 'room_' prefix convention.
"""
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    cb_item_id = fields.Char(
        string='Cloudbeds Item ID',
        index=True,
        copy=False,
        help='Internal Cloudbeds itemID or room type slug (e.g. room_deluxe_room).',
    )
    cb_backend_id = fields.Many2one(
        comodel_name='cloudbeds.backend',
        string='Cloudbeds Backend',
        ondelete='set null',
    )
    cb_sync_date = fields.Datetime(
        string='Last CB Sync',
        readonly=True,
    )
    cb_service_charge_percentage = fields.Float(
        string='Service Charge Percentage'
    )

    # ------------------------------------------------------------------
    # Pull items from Cloudbeds
    # ------------------------------------------------------------------

    @api.model
    def _cloudbeds_pull_items(self, backend):
        """
        Fetch all catalog items from Cloudbeds and create/update Odoo products.
        Called from backend.action_sync_all().
        """
        client = backend._get_api_client()
        Log = self.env['cloudbeds.sync.log']
        processed = failed = 0
        errors = []

        try:
            result = client.get_items()
        except Exception as exc:
            Log._log(self.env, backend, 'product', 'cb_to_odoo', 'error', message=str(exc))
            raise

        items = result.get('data') or result.get('items') or []
        if isinstance(items, dict):
            items = list(items.values())

        for item in items:
            try:
                self._upsert_from_cloudbeds(self.env, backend, item)
                processed += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{item.get('itemID', '?')}: {exc}")
                _logger.warning(
                    'Failed to sync CB item %s: %s', item.get('itemID'), exc
                )

        state = 'success' if not failed else ('partial' if processed else 'error')
        Log._log(
            self.env, backend, 'product', 'cb_to_odoo', state,
            processed=processed, failed=failed,
            message='\n'.join(errors) if errors else None,
        )
        _logger.info(
            'Cloudbeds product sync: %d processed, %d failed for backend "%s".',
            processed, failed, backend.name,
        )

    @api.model
    def _upsert_from_cloudbeds(self, env, backend, item_data):
        """
        Create or update a product.template from a Cloudbeds item dict.

        Expected fields: itemID, itemName, itemDescription, itemPrice, itemCategoryID
        """
        item_id = str(item_data.get('itemID') or '')
        item_name = item_data.get('itemName') or item_data.get('name') or item_id
        if not item_id:
            return None

        existing = env['product.template'].search(
            [('cb_item_id', '=', item_id), ('cb_backend_id', '=', backend.id)],
            limit=1,
        )
        price = float(item_data.get('itemPrice') or item_data.get('price') or 0.0)

        vals = {
            'name': item_name,
            'type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
            'list_price': price,
            'cb_item_id': item_id,
            'cb_backend_id': backend.id,
            'cb_sync_date': fields.Datetime.now(),
        }
        description = item_data.get('itemDescription') or item_data.get('description') or ''
        if description:
            vals['description_sale'] = description

        if existing:
            existing.write(vals)
            return existing
        else:
            return env['product.template'].create(vals)
