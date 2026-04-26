# Payment Mapping Mode (Manual / Automatic) — Design

**Module:** `cl_cloudbeds_integration`
**Odoo:** 18 Enterprise
**Date:** 2026-04-26

## Problem

Payment mapping di Cloudbeds reservation saat ini 100% manual: user harus
membuka wizard "Map Payment Journal" dan memilih journal untuk tiap
reservation yang sudah punya posted invoice. Untuk property yang konsisten
memakai satu journal saja (mis. semua transaksi via satu rekening bank), proses
manual ini repetitif dan rawan kelewat.

Solusi: tambahkan konfigurasi *payment mapping behaviour* di backend dengan
dua mode — **manual** (perilaku sekarang) dan **automatic** (sistem auto-create
payment + reconcile pakai journal yang dikonfigurasi pada saat get reservation).

## Goals

- User bisa pilih per-backend: `manual` atau `automatic`.
- Mode `manual` tidak mengubah perilaku apapun (zero regression untuk user existing).
- Mode `automatic` auto-trigger `_map_payment` saat invoice posted, pakai journal yang ditentukan user.
- Idempoten: re-process reservation yang sudah `mapped` tidak menduplikasi payment.
- Validasi: `automatic` tanpa journal harus dicegah saat save.
- Reuse `_map_payment(journal)` yang sudah ada — no logic duplication.

## Non-Goals

- Tidak mengubah behavior wizard manual.
- Tidak menggunakan tabel `payment_method_ids` (per-CB-type mapping) untuk routing journal — sengaja simple pakai single global journal.
- Tidak menambah migration script untuk data lama (field deprecated dihapus apa adanya — boolean `auto_register_payments` sudah tidak aktif).

## Design

### 1. Model — `cloudbeds.backend`

#### Field changes

**Hapus** field deprecated:

```python
auto_register_payments = fields.Boolean(...)  # REMOVED
```

**Tambah** dua field baru:

```python
payment_mapping_mode = fields.Selection(
    selection=[('manual', 'Manual'), ('automatic', 'Automatic')],
    string='Payment Mapping Mode',
    default='manual',
    required=True,
    help=(
        'Manual: payments are mapped via the Map Payment wizard.\n'
        'Automatic: payment is auto-created and reconciled with the invoice '
        'using the configured journal when a reservation is fetched.'
    ),
)
auto_payment_journal_id = fields.Many2one(
    comodel_name='account.journal',
    string='Auto Payment Journal',
    domain="[('type', 'in', ['cash', 'bank'])]",
    help='Journal used when payment_mapping_mode = automatic.',
)
```

#### Validation

```python
@api.constrains('payment_mapping_mode', 'auto_payment_journal_id')
def _check_auto_payment_journal(self):
    for rec in self:
        if rec.payment_mapping_mode == 'automatic' and not rec.auto_payment_journal_id:
            raise ValidationError(_(
                'Auto Payment Journal is required when Payment Mapping Mode is Automatic.'
            ))
```

### 2. Logic — `cloudbeds.reservation`

#### New helper

```python
def _auto_map_payment_if_enabled(self):
    """
    Auto-map payment when backend is in automatic mode.
    Idempotent: skips when already mapped, no payment to register,
    or invoice not posted.
    """
    self.ensure_one()
    backend = self.backend_id
    if backend.payment_mapping_mode != 'automatic':
        return
    if self.payment_mapping_status == 'mapped':
        return
    if not self.invoice_id or self.invoice_id.state != 'posted':
        return
    if (self.cb_total_paid or 0) <= 0:
        return
    if not backend.auto_payment_journal_id:
        return  # constraint guards this; defensive only
    self._map_payment(backend.auto_payment_journal_id)
```

#### Integration points

Dipanggil dari dua tempat di `_process` flow:

**A. `_process_checkout`** — setelah `invoice.action_post()`, sebelum
`self.write({'state': 'imported', ...})`. Ini handle first-time checkout flow.

**B. `_sync_payments_if_needed`** — saat ini stub no-op. Replace dengan call ke
`_auto_map_payment_if_enabled()`. Ini handle re-process flow + cb_status
transitions (not_confirmed/confirmed/checked_in juga aman karena guard
`invoice.state == 'posted'` akan skip kalau invoice belum ada).

```python
def _sync_payments_if_needed(self, invoice_data, cache):
    """
    Trigger auto payment mapping when backend is in automatic mode.
    No-op when in manual mode — user maps via wizard.
    """
    self._auto_map_payment_if_enabled()
```

### 3. View — `cloudbeds_backend_views.xml`

Di tab Accounting, group "Invoice Settings", ganti:

```xml
<field name="auto_register_payments"
       class="text-muted"
       string="Auto-Register Payments (Deprecated — use Map Payment wizard)"/>
```

dengan:

```xml
<field name="payment_mapping_mode" widget="radio"/>
<field name="auto_payment_journal_id"
       invisible="payment_mapping_mode != 'automatic'"
       required="payment_mapping_mode == 'automatic'"/>
```

### 4. Manifest

Bump version: `'18.0.1.0.0'` → `'18.0.1.1.0'` (fitur baru + breaking field rename).

Update description string yang reference `auto_register_payments` deprecated.

### 5. Tests — `tests/test_map_payment.py`

Tambah lima test cases di class `TestMapPayment`:

| Test | Setup | Assertion |
|------|-------|-----------|
| `test_auto_map_runs_in_automatic_mode` | mode=automatic + journal, posted invoice, cb_total_paid>0 | `_auto_map_payment_if_enabled()` membuat payment, `payment_mapping_status='mapped'` |
| `test_auto_map_skipped_in_manual_mode` | mode=manual | tidak ada payment dibuat, status tetap `not_mapped` |
| `test_auto_map_skipped_when_already_mapped` | mode=automatic, status sudah `mapped` | payment tidak duplicate, count tetap 1 |
| `test_auto_map_skipped_when_total_paid_zero` | mode=automatic, `cb_total_paid=0` | tidak ada payment dibuat |
| `test_constraint_automatic_requires_journal` | set mode=automatic tanpa journal | `ValidationError` saat save |

## Data Flow

```
get_reservation (cron / manual)
    ↓
_process(client, cache)
    ↓
[cb_status routing]
    ├─ checked_out + invoice belum ada
    │     → _process_checkout
    │           → create SO → confirm → create invoice → post invoice
    │           → _auto_map_payment_if_enabled()  ← integration point A
    │           → write state='imported'
    │
    └─ checked_out + invoice sudah ada
          → _sync_payments_if_needed
                → _auto_map_payment_if_enabled()  ← integration point B
          → write state='imported'

_auto_map_payment_if_enabled checks:
    backend.payment_mapping_mode == 'automatic'
    AND payment_mapping_status == 'not_mapped'
    AND invoice posted
    AND cb_total_paid > 0
    AND backend.auto_payment_journal_id set
        → _map_payment(backend.auto_payment_journal_id)
```

## Migration / Backward Compatibility

- Field `auto_register_payments` dihapus. Field ini sudah deprecated dan
  `_register_payments` method yang mengonsumsinya juga sudah deprecated (no
  longer called from sync pipeline).
- Default mode = `manual` → existing user tidak terpengaruh.
- Tidak butuh migration script: ORM auto-drop column saat module update.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| User set mode=automatic, lupa set journal → constraint melakukan validate saat save | `@api.constrains` raise saat save record |
| Auto-map dipanggil saat invoice belum post (race) | Guard `invoice.state == 'posted'` di `_auto_map_payment_if_enabled` |
| Re-process menduplikasi payment | Guard `payment_mapping_status == 'mapped'` skip |
| User switch manual → automatic mid-flight, reservasi lama belum mapped | Next re-process akan auto-map (sesuai harapan) — user bisa pakai "Re-Process" button untuk batch backfill |
| Reservasi belum bayar (deposit, checked-in tanpa bayar) | Guard `cb_total_paid > 0` skip |

## File Inventory

| File | Action |
|------|--------|
| `cl_cloudbeds_integration/models/cloudbeds_backend.py` | Hapus `auto_register_payments`, tambah `payment_mapping_mode` + `auto_payment_journal_id` + `_check_auto_payment_journal` constraint |
| `cl_cloudbeds_integration/models/cloudbeds_reservation.py` | Tambah `_auto_map_payment_if_enabled`, panggil di `_process_checkout` setelah invoice post, replace stub `_sync_payments_if_needed` |
| `cl_cloudbeds_integration/views/cloudbeds_backend_views.xml` | Replace deprecated field di Accounting tab dengan dua field baru + visibility |
| `cl_cloudbeds_integration/__manifest__.py` | Version bump + description update |
| `cl_cloudbeds_integration/tests/test_map_payment.py` | 5 test cases baru |
