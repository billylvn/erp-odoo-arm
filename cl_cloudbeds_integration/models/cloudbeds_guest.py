# -*- coding: utf-8 -*-
"""
cloudbeds_guest — Extends res.partner to link with Cloudbeds guests.
"""
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Cloudbeds country code → Odoo res.country name mapping (ISO 2-letter codes)
COUNTRY_CODE_FIELD = 'code'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    cb_guest_id = fields.Char(
        string='Cloudbeds Guest ID',
        index=True,
        copy=False,
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

    # ------------------------------------------------------------------
    # Pull guests from Cloudbeds
    # ------------------------------------------------------------------

    @api.model
    def _cloudbeds_pull_guests(self, backend):
        """
        Fetch all guests from Cloudbeds and create/update Odoo partners.
        Called from backend.action_sync_all().
        """
        client = backend._get_api_client()
        Log = self.env['cloudbeds.sync.log']
        processed = failed = 0
        errors = []

        try:
            guests = list(client.get_all_guests())
        except Exception as exc:
            Log._log(self.env, backend, 'guest', 'cb_to_odoo', 'error', message=str(exc))
            raise

        for guest_data in guests:
            try:
                self._upsert_from_cloudbeds(self.env, backend, guest_data)
                processed += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{guest_data.get('guestID', '?')}: {exc}")
                _logger.warning(
                    'Failed to sync CB guest %s: %s', guest_data.get('guestID'), exc
                )

        state = 'success' if not failed else ('partial' if processed else 'error')
        Log._log(
            self.env, backend, 'guest', 'cb_to_odoo', state,
            processed=processed, failed=failed,
            message='\n'.join(errors) if errors else None,
        )
        _logger.info(
            'Cloudbeds guest sync: %d processed, %d failed for backend "%s".',
            processed, failed, backend.name,
        )

    @api.model
    def _upsert_from_cloudbeds(self, env, backend, guest_data):
        """
        Create or update a res.partner from a Cloudbeds guest dict.

        Cloudbeds guest fields used:
          guestID, gestName, guestEmail,
          guestPhone, guestAddress1, guestCity, guestState,
          guestCountry (ISO code), guestZip
        """
        guest_id = str(guest_data.get('guestID') or '')
        if not guest_id:
            return None

        guest_name = guest_data.get('guestName') or ''
        name = guest_name.strip()
        vals = {
            'name': name,
            'customer_rank': 1,
            'cb_guest_id': guest_id,
            'cb_backend_id': backend.id,
            'cb_sync_date': fields.Datetime.now(),
        }

        email = guest_data.get('guestEmail') or ''
        if email:
            vals['email'] = email

        phone = guest_data.get('guestPhone') or ''
        if phone:
            vals['phone'] = phone

        street = guest_data.get('guestAddress1') or ''
        if street:
            vals['street'] = street

        city = guest_data.get('guestCity') or ''
        if city:
            vals['city'] = city

        zip_code = guest_data.get('guestZip') or ''
        if zip_code:
            vals['zip'] = zip_code

        # Resolve country
        country_code = guest_data.get('guestCountry') or ''
        if country_code:
            country = env['res.country'].search(
                [('code', '=', country_code.upper())], limit=1
            )
            if country:
                vals['country_id'] = country.id

        # Resolve state/province
        state_name = guest_data.get('guestState') or ''
        if state_name and vals.get('country_id'):
            state = env['res.country.state'].search(
                [
                    ('country_id', '=', vals['country_id']),
                    ('name', 'ilike', state_name),
                ],
                limit=1,
            )
            if state:
                vals['state_id'] = state.id

        existing = env['res.partner'].search(
            [('cb_guest_id', '=', guest_id), ('cb_backend_id', '=', backend.id)],
            limit=1,
        )
        if existing:
            existing.write(vals)
            return existing
        return env['res.partner'].create(vals)

    @api.model
    def _resolve_guest(self, env, backend, guest_id, api_client=None):
        """
        Find an existing partner by cb_guest_id, or fetch + create from Cloudbeds.

        :param guest_id: Cloudbeds guestID string
        :param api_client: optional CloudbedsApiClient (avoid extra factory calls)
        :returns: res.partner record or None
        """
        if not guest_id:
            return None

        partner = env['res.partner'].search(
            [('cb_guest_id', '=', str(guest_id)), ('cb_backend_id', '=', backend.id)],
            limit=1,
        )
        if partner:
            return partner

        # Attempt to pull from API
        try:
            client = api_client or backend._get_api_client()
            result = client.get_guest(guest_id)
            guest_data = result.get('data') or result
            if isinstance(guest_data, dict) and guest_data.get('guestID'):
                return env['res.partner']._upsert_from_cloudbeds(env, backend, guest_data)
        except Exception as exc:
            _logger.warning(
                'Could not resolve CB guest %s: %s', guest_id, exc
            )
        return None
