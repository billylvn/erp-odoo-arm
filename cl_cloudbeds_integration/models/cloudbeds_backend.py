# -*- coding: utf-8 -*-
"""
cloudbeds.backend — Central configuration and orchestration hub.

One record = one Cloudbeds property connection.
Covers: OAuth2, guest sync, product sync, reservations → invoices + payments.
"""
import logging
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloudbedsBackend(models.Model):
    _name = 'cloudbeds.backend'
    _description = 'Cloudbeds Backend'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(
        string='Backend Name',
        required=True,
        tracking=True,
        help='Friendly name, e.g. "Grand Hotel Jakarta".',
    )
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Connection / OAuth
    # ------------------------------------------------------------------
    property_id = fields.Char(
        string='Cloudbeds Property ID',
        required=True,
        tracking=True,
        help='The propertyID value from your Cloudbeds account.',
    )
    auth_method = fields.Selection(
        selection=[
            ('api_key', 'API Key'),
            ('oauth', 'OAuth 2.0'),
        ],
        string='Auth Method',
        default='api_key',
        required=True,
        help=(
            'API Key: paste the cbat_... key from your Cloudbeds developer app — '
            'simplest and recommended.\n'
            'OAuth 2.0: redirect-based flow — requires your redirect URI to be '
            'registered in your Cloudbeds app settings.'
        ),
    )
    api_key = fields.Char(
        string='Cloudbeds API Key',
        groups='base.group_system',
        help='The cbat_... API key from your Cloudbeds developer app.',
    )
    client_id = fields.Char(string='OAuth Client ID')
    client_secret = fields.Char(string='OAuth Client Secret')
    access_token = fields.Char(
        string='Access Token',
        readonly=True,
        groups='base.group_system',
    )
    refresh_token = fields.Char(
        string='Refresh Token',
        readonly=True,
        groups='base.group_system',
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Not Connected'),
            ('connected', 'Connected'),
            ('error', 'Error'),
        ],
        string='Status',
        default='draft',
        readonly=True,
        tracking=True,
    )
    last_sync = fields.Datetime(string='Last Sync', readonly=True)
    last_error = fields.Char(string='Last Error', readonly=True)

    # ------------------------------------------------------------------
    # Sync settings
    # ------------------------------------------------------------------
    sync_guests = fields.Boolean(
        string='Sync Guests',
        default=True,
        help='Pull guest records from Cloudbeds and create/update Odoo contacts.',
    )
    sync_products = fields.Boolean(
        string='Sync Products',
        default=True,
        help='Pull catalog items from Cloudbeds and create/update Odoo products.',
    )
    sync_reservations = fields.Boolean(
        string='Sync Reservations',
        default=True,
        help='Import reservations from Cloudbeds.',
    )

    # ------------------------------------------------------------------
    # Accounting settings
    # ------------------------------------------------------------------
    auto_invoice_on_checkout = fields.Boolean(
        string='Auto-Invoice on Checkout',
        default=True,
        help='Automatically create and post an invoice when a reservation is checked out.',
    )
    auto_confirm_invoices = fields.Boolean(
        string='Auto-Confirm Invoices',
        default=True,
        help='Post (confirm) invoices automatically after creation.',
    )
    auto_register_payments = fields.Boolean(
        string='Auto-Register Payments',
        default=True,
        help='Register account.payment entries for each Cloudbeds payment automatically.',
    )
    revenue_journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Revenue Journal',
        domain=[('type', '=', 'sale')],
        help='Journal used for room revenue invoices.',
    )
    service_charge_product_id = fields.Many2one(
        comodel_name='product.product',
        string='Service Charge Product',
        help='Product used for service charge.',
    )
    currency_id = fields.Many2one('res.currency', string='Currency')

    # ------------------------------------------------------------------
    # Inline relation tables
    # ------------------------------------------------------------------
    tax_mapping_ids = fields.One2many(
        comodel_name='cloudbeds.tax.mapping',
        inverse_name='backend_id',
        string='Tax Mappings',
    )
    payment_method_ids = fields.One2many(
        comodel_name='cloudbeds.payment.method',
        inverse_name='backend_id',
        string='Payment Method Mappings',
    )

    # ------------------------------------------------------------------
    # Linked records
    # ------------------------------------------------------------------
    reservation_ids = fields.One2many(
        comodel_name='cloudbeds.reservation',
        inverse_name='backend_id',
        string='Reservations',
    )
    sync_log_ids = fields.One2many(
        comodel_name='cloudbeds.sync.log',
        inverse_name='backend_id',
        string='Sync Logs',
    )

    # ------------------------------------------------------------------
    # Computed stat buttons
    # ------------------------------------------------------------------
    reservation_count = fields.Integer(
        string='Reservations',
        compute='_compute_reservation_count',
    )
    sync_log_count = fields.Integer(
        string='Sync Logs',
        compute='_compute_sync_log_count',
    )

    def _compute_reservation_count(self):
        for rec in self:
            rec.reservation_count = len(rec.reservation_ids)

    def _compute_sync_log_count(self):
        for rec in self:
            rec.sync_log_count = len(rec.sync_log_ids)

    # ------------------------------------------------------------------
    # API client factory
    # ------------------------------------------------------------------

    def _get_api_client(self):
        self.ensure_one()
        # API key acts as permanent access token; no refresh needed.
        token = self.api_key if self.auth_method == 'api_key' else self.access_token
        if not token:
            raise UserError(
                _('Backend "%s" is not authenticated. Connect to Cloudbeds first.') % self.name
            )
        from ..services import CloudbedsApiClient
        return CloudbedsApiClient(
            property_id=self.property_id,
            access_token=token,
            refresh_token=self.refresh_token if self.auth_method == 'oauth' else None,
            client_id=self.client_id or None,
            client_secret=self.client_secret or None,
            on_token_refresh=self._on_token_refresh if self.auth_method == 'oauth' else None,
        )

    def _on_token_refresh(self, new_access_token, new_refresh_token):
        """Callback: save refreshed tokens and ensure state = connected."""
        self.sudo().write({
            'access_token': new_access_token,
            'refresh_token': new_refresh_token,
            'state': 'connected',
            'last_error': False,
        })

    # ------------------------------------------------------------------
    # OAuth / API Key connection
    # ------------------------------------------------------------------

    def action_connect_api_key(self):
        """Validate the entered API key, set state to connected, and sync lookups."""
        self.ensure_one()
        if not self.api_key:
            raise UserError(_('Please enter a Cloudbeds API Key (cbat_...) before connecting.'))
        try:
            from ..services import CloudbedsApiClient
            client = CloudbedsApiClient(
                property_id=self.property_id,
                access_token=self.api_key,
            )
            client.check_access_token()
        except Exception as exc:
            self.write({'state': 'error', 'last_error': str(exc)})
            raise UserError(_('API Key validation failed: %s') % exc) from exc
        self.write({'state': 'connected', 'last_error': False})
        self._sync_payment_methods_from_api()
        self._sync_taxes_from_api()
        self._sync_currencies_from_api()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connected'),
                'message': _(
                    'Cloudbeds API Key validated. Payment methods and taxes imported.'
                ),
                'type': 'success',
            },
        }

    def action_connect(self):
        """Build the Cloudbeds OAuth URL and redirect the browser."""
        self.ensure_one()
        from ..services import CloudbedsApiClient
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f'{base_url}/cloudbeds/oauth/callback'
        # Use backend ID as state to identify the backend on callback
        url = CloudbedsApiClient.build_authorize_url(
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            state=str(self.id),
        )
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'self'}

    def _finalise_oauth(self, code):
        """Exchange the authorisation code for tokens and persist them."""
        from ..services import CloudbedsApiClient
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f'{base_url}/cloudbeds/oauth/callback'
        tokens = CloudbedsApiClient.exchange_code_for_tokens(
            code=code,
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        self.write({
            'access_token': tokens.get('access_token'),
            'refresh_token': tokens.get('refresh_token'),
            'state': 'connected',
            'last_error': False,
        })
        self._sync_payment_methods_from_api()
        self._sync_taxes_from_api()
        self._sync_currencies_from_api()

    # ------------------------------------------------------------------
    # Lookup sync — payment methods & taxes
    # ------------------------------------------------------------------

    def _get_or_create_service_charge_product(self, name=False, percentage=False):
        """Return (or create) a 'Service Charge' service product."""
        product = self.env['product.template'].search(
            [('cb_item_id', '=', '__cb_service_charge__')],
            limit=1,
        )
        if product:
            return product.product_variant_id
        new_product = self.env['product.template'].create({
            'name': name or 'Service Charge',
            'type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
            'taxes_id': False,
            'cb_service_charge_percentage': percentage or 0.0,
            'cb_item_id': '__cb_service_charge__',
        })
        return new_product.product_variant_id

    def _sync_payment_methods_from_api(self):
        """
        Fetch payment methods from Cloudbeds getPaymentMethods and upsert
        cloudbeds.payment.method records with auto-assigned journals.
        """
        self.ensure_one()
        try:
            client = self._get_api_client()
            result = client.get_payment_methods()
            # Response: {"success": true, "data": [...]} or nested dict
            raw = result.get('data') or result
            if isinstance(raw, dict):
                # Some responses nest under a key like "paymentMethods"
                methods = raw.get('methods') or []
            else:
                methods = raw or []

            PaymentMethod = self.env['cloudbeds.payment.method']

            from ..models.cloudbeds_payment_method import _JOURNAL_TYPE_MAP

            for method in methods:
                if not isinstance(method, dict):
                    continue

                cb_id = str(method.get('code') or '')
                cb_name = (
                    method.get('name')
                    or ''
                )
                cb_type = method.get('method')

                if not cb_type or not cb_id:
                    continue

                existing = PaymentMethod.search(
                    [('backend_id', '=', self.id), ('cb_payment_method_id', '=', cb_id)],
                    limit=1,
                )
                if existing:
                    existing.write({
                        'cb_payment_method_name': cb_name,
                        'cb_payment_type': cb_type,
                    })
                    continue

                # Auto-assign journal by type
                journal_type = _JOURNAL_TYPE_MAP.get(cb_type, 'bank')
                journal = self.env['account.journal'].search(
                    [('type', '=', journal_type),
                     ('company_id', '=', self.env.company.id)],
                    limit=1,
                )
                if not journal:
                    journal = self.env['account.journal'].search(
                        [('type', 'in', ['bank', 'cash']),
                         ('company_id', '=', self.env.company.id)],
                        limit=1,
                    )
                if journal:
                    PaymentMethod.create({
                        'backend_id': self.id,
                        'cb_payment_method_id': cb_id,
                        'cb_payment_method_name': cb_name,
                        'cb_payment_type': cb_type,
                        'journal_id': journal.id,
                    })

            _logger.info(
                'Cloudbeds: synced payment methods for backend "%s".', self.name
            )
        except Exception as exc:
            _logger.warning(
                'Could not sync Cloudbeds payment methods for "%s": %s', self.name, exc
            )

    def _map_tax_from_cloudbeds(self):
        """
        Map taxes from Cloudbeds to Odoo
        """
        cb_taxes = self.tax_mapping_ids
        if cb_taxes:
            cb_tax = cb_taxes[0]
            return cb_tax.tax_id
        return None

    def _map_odoo_taxes(self, percentage, included):
        """
        Map taxes from Cloudbeds to Odoo
        """
        AccountTax = self.env['account.tax']
        tax = AccountTax.search([
            ('amount', '=', percentage),
            ('amount_type', '=', 'percent'),
            ('price_include_override', '=', 'tax_excluded' if included else 'tax_included'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not tax:
            tax = AccountTax.create({
                'name': f"Cloudbeds Tax {percentage}%",
                'amount': percentage,
                'amount_type': 'percent',
                'type_tax_use': 'sale',
                'invoice_label': f"{percentage}%",
                'price_include_override': 'tax_excluded' if included else 'tax_included',
                'company_id': self.env.company.id,
            })
        self.ensure_one()
        return tax
        
    def _sync_currencies_from_api(self):
        """
        Fetch currencies from Cloudbeds getCurrency and upsert
        cloudbeds.currency.mapping records (CB side only; user maps Odoo currency manually).
        """
        self.ensure_one()
        try:
            client = self._get_api_client()
            result = client.get_currencies()
            currencies = result.get('data') or result

            self.currency_id = self.env['res.currency'].search([('name','=',currencies.get('default'))], limit=1).id
            _logger.info(
                'Cloudbeds: synced currencies for backend "%s".', self.name
            )
        except Exception as exc:
            _logger.warning(
                'Could not sync Cloudbeds currencies for "%s": %s', self.name, exc
            )

    def _sync_taxes_from_api(self):
        """
        Fetch taxes from Cloudbeds getTaxesAndFees and upsert
        cloudbeds.tax.mapping records (CB side only; user maps Odoo tax manually).
        """
        self.ensure_one()
        try:
            client = self._get_api_client()
            result = client.get_taxes_and_fees()
            raw = result.get('data') or result
            taxes = raw if isinstance(raw, list) else list(raw.values()) if isinstance(raw, dict) else []

            TaxMapping = self.env['cloudbeds.tax.mapping']

            for tax in taxes:
                if not isinstance(tax, dict):
                    continue

                cb_tax_id = str(tax.get('taxID') or '')
                cb_tax_name = tax.get('taxName') or tax.get('name') or ''
                cb_tax_type = tax.get('type') or ''
                cb_tax_inclded = tax.get('inclusiveOrExclusive') or ''
                cb_percentage = float(tax.get('amount') or tax.get('taxamount') or 0.0)

                if not cb_tax_id:
                    self.service_charge_product_id = self._get_or_create_service_charge_product(tax.get('name'), (cb_percentage/100)).id
                    continue

                existing = TaxMapping.search(
                    [('backend_id', '=', self.id), ('cb_tax_id', '=', cb_tax_id)],
                    limit=1,
                )
                cb_tax_record = False
                if existing:
                    existing.write({
                        'cb_tax_name': cb_tax_name,
                        'cb_tax_type': cb_tax_type,
                        'cb_tax_percentage': cb_percentage,
                    })
                    cb_tax_record = existing
                else:
                    cb_tax_record = TaxMapping.create({
                        'backend_id': self.id,
                        'cb_tax_id': cb_tax_id,
                        'cb_tax_name': cb_tax_name,
                        'cb_tax_type': cb_tax_type,
                        'cb_tax_percentage': cb_percentage,
                    })

                # Map Odoo tax
                odoo_tax = self._map_odoo_taxes(cb_percentage, cb_tax_inclded == 'inclusive')
                if odoo_tax:
                    cb_tax_record.write({
                        'tax_id': odoo_tax.id,
                    })

            _logger.info(
                'Cloudbeds: synced taxes for backend "%s".', self.name
            )
        except Exception as exc:
            _logger.warning(
                'Could not sync Cloudbeds taxes for "%s": %s', self.name, exc
            )

    def action_sync_lookups(self):
        """Manually re-sync payment methods and taxes from Cloudbeds."""
        self.ensure_one()
        self._sync_payment_methods_from_api()
        self._sync_taxes_from_api()
        self._sync_currencies_from_api()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Lookups Synced'),
                'message': _('Payment methods and taxes refreshed from Cloudbeds.'),
                'type': 'success',
            },
        }

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def action_test_connection(self):
        self.ensure_one()
        try:
            client = self._get_api_client()
            result = client.check_access_token()
            self.write({'state': 'connected', 'last_error': False})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': _(
                        'Cloudbeds token is valid for property %s.'
                    ) % self.property_id,
                    'type': 'success',
                },
            }
        except Exception as exc:
            self.write({'state': 'error', 'last_error': str(exc)})
            raise UserError(_('Connection failed: %s') % exc) from exc

    # ------------------------------------------------------------------
    # Webhook registration
    # ------------------------------------------------------------------

    def action_register_webhooks(self):
        self.ensure_one()
        client = self._get_api_client()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        webhook_url = f'{base_url}/cloudbeds/webhook/{self.id}'

        events = [
            ('reservation', 'created'),
            ('reservation', 'status_changed'),
        ]
        registered = []
        errors = []
        for obj, action in events:
            try:
                client.post_webhook(
                    endpoint_url=webhook_url,
                    object_name=obj,
                    action=action,
                )
                registered.append(f'{obj}/{action}')
                _logger.info(
                    'Cloudbeds webhook "%s/%s" registered for backend "%s".',
                    obj, action, self.name,
                )
            except Exception as exc:
                errors.append(f'{obj}/{action}: {exc}')
                _logger.warning(
                    'Could not register webhook %s/%s: %s', obj, action, exc
                )

        from ..models.cloudbeds_sync_log import CloudbedsSyncLog
        state = 'success' if not errors else ('partial' if registered else 'error')
        self.env['cloudbeds.sync.log']._log(
            self.env, self, 'webhook', 'odoo_to_cb', state,
            processed=len(registered),
            failed=len(errors),
            message='\n'.join(errors) if errors else None,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhooks Registration'),
                'message': (
                    _('Registered: %s') % ', '.join(registered)
                    if registered
                    else _('No webhooks could be registered. Check errors.')
                ),
                'type': 'success' if registered else 'warning',
            },
        }

    # ------------------------------------------------------------------
    # High-level sync orchestration
    # ------------------------------------------------------------------

    def action_sync_all(self):
        """Run all enabled sync operations."""
        self.ensure_one()
        _logger.info('Cloudbeds: starting full sync for backend "%s".', self.name)

        if self.sync_guests:
            try:
                self.env['res.partner']._cloudbeds_pull_guests(self)
            except Exception as exc:
                _logger.error('Guest sync failed: %s', exc, exc_info=True)

        if self.sync_products:
            try:
                self.env['product.template']._cloudbeds_pull_items(self)
            except Exception as exc:
                _logger.error('Product sync failed: %s', exc, exc_info=True)

        if self.sync_reservations:
            try:
                self.env['cloudbeds.reservation']._import_reservations(self)
            except Exception as exc:
                _logger.error('Reservation sync failed: %s', exc, exc_info=True)

        self.write({'last_sync': fields.Datetime.now()})

    # ------------------------------------------------------------------
    # Stat button actions
    # ------------------------------------------------------------------

    def action_view_reservations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reservations'),
            'res_model': 'cloudbeds.reservation',
            'view_mode': 'list,form',
            'domain': [('backend_id', '=', self.id)],
            'context': {'default_backend_id': self.id},
        }

    def action_view_sync_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync Logs'),
            'res_model': 'cloudbeds.sync.log',
            'view_mode': 'list,form',
            'domain': [('backend_id', '=', self.id)],
            'context': {'default_backend_id': self.id},
        }

    # ------------------------------------------------------------------
    # Cron entry point
    # ------------------------------------------------------------------

    @api.model
    def _cron_sync_all(self):
        """Called by the scheduled action every 15 minutes."""
        backends = self.search([('state', '=', 'connected'), ('active', '=', True)])
        for backend in backends:
            try:
                backend.action_sync_all()
            except Exception as exc:
                _logger.error(
                    'Cloudbeds auto-sync failed for "%s": %s',
                    backend.name, exc, exc_info=True,
                )
                backend.write({'state': 'error', 'last_error': str(exc)})
