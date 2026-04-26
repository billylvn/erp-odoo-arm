# -*- coding: utf-8 -*-
{
    'name': 'Cloudbeds Integration',
    'version': '18.0.1.1.0',
    'category': 'Accounting/Accounting',
    'summary': (
        'Full Odoo 18 integration with Cloudbeds: reservations, '
        'invoices, payments, guests, products, and financial reporting.'
    ),
    'description': """
Cloudbeds Integration
=====================
Connect Odoo 18 with Cloudbeds hotel management system end-to-end.

**Accounting**
* Import Cloudbeds reservations → Odoo invoices automatically on checkout
* Payment mapping mode (manual / automatic):
    - Manual: assign journal via the Map Payment wizard (multi-select from list view)
    - Automatic: payment auto-created and reconciled using a configured journal on get reservation
* Create credit notes when reservations are cancelled after invoicing
* Map Cloudbeds taxes → Odoo account.tax
* Map payment methods → accounting journals
* Adjustments as negative invoice lines

**Reservations**
* Full folio import: room charges, service items, taxes, payments, adjustments
* All statuses handled: not_confirmed, confirmed, checked_in, checked_out, canceled, no_show
* Sale Order → Invoice pipeline automatic on checkout; payment assigned manually via wizard or automatically per backend configuration
* Idempotent imports — safe to run repeatedly

**Products**
* Sync Cloudbeds room types as Odoo service products
* Sync Cloudbeds catalog items as Odoo service products
* Item categories mapped automatically

**Guests**
* Sync Cloudbeds guests → Odoo contacts (res.partner)
* Resolve guest on import, create if not found

**Infrastructure**
* OAuth2 Authorization Code Flow with automatic token refresh
* Real-time webhooks: reservation/created, reservation/status_changed
* HMAC-SHA256 webhook signature verification
* Scheduled Action (cron) every 15 minutes
* Full audit log for every sync operation
* Multi-backend: multiple properties in one Odoo instance
    """,
    'author': 'Creativin Labs',
    'website': 'https://www.creativin-labs.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'product',
        'stock',
        'account',
        'sale',
        'sale_management',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/cloudbeds_backend_views.xml',
        'views/cloudbeds_reservation_views.xml',
        'views/cloudbeds_product_views.xml',
        'views/cloudbeds_guest_views.xml',
        'views/cloudbeds_log_views.xml',
        'views/cloudbeds_wizard_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
