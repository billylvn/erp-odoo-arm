# Cloudbeds Integration untuk Odoo 19

**Modul:** `cl_cloudbeds_integration`
**Versi:** 19.0.1.0.0
**Penulis:** Creativin Labs
**Lisensi:** LGPL-3
**Referensi API:** <https://hotels.cloudbeds.com/api/v1.3/docs>

---

## Deskripsi Singkat

Modul ini menghubungkan **Odoo 19** dengan **Cloudbeds** — sistem manajemen hotel terkemuka — secara penuh dan satu arah (Cloudbeds → Odoo). Tujuan utamanya adalah memastikan **seluruh transaksi keuangan dari Cloudbeds** (biaya kamar, biaya layanan, pembayaran, pajak, penyesuaian) muncul sebagai dokumen akuntansi yang tepat di Odoo: faktur, pembayaran, dan jurnal.

---

## Fitur Utama

### Akuntansi

| Fitur | Deskripsi |
| --- | --- |
| Faktur Otomatis | Setiap reservasi yang check-out dikonversi menjadi `account.move` (faktur) di Odoo |
| Biaya Kamar | Setiap tipe kamar menjadi baris faktur: produk = tipe kamar, jumlah = jumlah malam, harga = tarif per malam |
| Item Layanan | Item tambahan dari Cloudbeds (F&B, laundry, spa, dll.) menjadi baris faktur terpisah |
| Multi-Pajak | Pajak dari Cloudbeds dipetakan ke `account.tax` Odoo per baris, dengan jumlah yang akurat |
| Penyesuaian | Diskon dan penyesuaian folio menjadi baris negatif pada faktur |
| Multi-Pembayaran | Setiap entri pembayaran (tunai, kartu kredit, transfer bank, dll.) dicatat sebagai `account.payment` di jurnal yang sesuai |
| Rekonsiliasi | Pembayaran langsung direkonsiliasi dengan faktur setelah dibuat |
| Kredit Nota | Reservasi yang dibatalkan setelah invoice terbit dibuatkan kredit nota otomatis |

### Reservasi

| Fitur | Deskripsi |
| --- | --- |
| Import Folio Lengkap | Mengambil data lengkap via `getReservationInvoiceInformation`: kamar, item, pajak, pembayaran, penyesuaian |
| Semua Status | Menangani semua status: `not_confirmed`, `confirmed`, `checked_in`, `checked_out`, `canceled`, `no_show` |
| Pipeline Otomatis | Check-Out → Sale Order → Faktur → Post → Pembayaran, semua otomatis |
| Idempoten | Import aman dijalankan berulang kali — data tidak duplikat |
| Staging Record | Setiap reservasi tersimpan di `cloudbeds.reservation` untuk audit dan debugging |

### Produk

| Fitur | Deskripsi |
| --- | --- |
| Tipe Kamar | Tipe kamar dari reservasi menjadi produk layanan Odoo secara otomatis (ID: `room_<slug>`) |
| Katalog Item | Item dari `getItems` disinkronisasi sebagai produk layanan Odoo |
| Tidak Ada Stok | Semua produk bertipe `service` — tidak ada pergerakan stok untuk hotel |

### Tamu

| Fitur | Deskripsi |
| --- | --- |
| Sinkronisasi Tamu | Tamu Cloudbeds disinkronisasi ke `res.partner` Odoo (nama, email, telepon, alamat, negara) |
| Resolve Otomatis | Saat memproses reservasi, tamu dicari otomatis; jika tidak ada, dibuat dari API Cloudbeds |

### Infrastruktur

| Fitur | Deskripsi |
| --- | --- |
| OAuth2 | Flow autentikasi penuh (Authorization Code) dengan refresh token otomatis |
| Webhook | Receiver real-time untuk event `reservation/created` dan `reservation/status_changed` |
| Keamanan Webhook | Verifikasi tanda tangan HMAC-SHA256 menggunakan `client_secret` |
| Cron Job | Scheduled action berjalan setiap 15 menit untuk semua backend aktif |
| Log Sinkronisasi | Setiap operasi tercatat di `cloudbeds.sync.log` |
| Multi-Backend | Mendukung beberapa properti Cloudbeds sekaligus dalam satu instalasi Odoo |
| Rate Limiting | Jeda otomatis 250 ms antar permintaan untuk mematuhi batas 5 req/detik Cloudbeds |

---

## Struktur Modul

```text
cl_cloudbeds_integration/
├── __manifest__.py
├── __init__.py
├── README.md
├── i18n/
│   └── id.po                              # Terjemahan Bahasa Indonesia
├── services/
│   ├── __init__.py
│   └── cloudbeds_api_client.py            # HTTP client murni (tanpa dependensi Odoo)
├── models/
│   ├── __init__.py
│   ├── cloudbeds_backend.py               # Konfigurasi & orkestrasi utama
│   ├── cloudbeds_sync_log.py              # Audit trail setiap sinkronisasi
│   ├── cloudbeds_tax_mapping.py           # Pemetaan pajak CB → Odoo
│   ├── cloudbeds_payment_method.py        # Pemetaan metode pembayaran CB → jurnal
│   ├── cloudbeds_product.py               # Extends product.template
│   ├── cloudbeds_guest.py                 # Extends res.partner
│   └── cloudbeds_reservation.py          # Model staging reservasi + pipeline akuntansi
├── wizards/
│   ├── __init__.py
│   └── cloudbeds_sync_wizard.py           # Dialog sinkronisasi manual
├── controllers/
│   ├── __init__.py
│   ├── cloudbeds_oauth.py                 # OAuth2 callback (/cloudbeds/oauth/callback)
│   └── cloudbeds_webhook.py               # Webhook receiver (/cloudbeds/webhook/<id>)
├── views/
│   ├── cloudbeds_backend_views.xml
│   ├── cloudbeds_reservation_views.xml
│   ├── cloudbeds_product_views.xml
│   ├── cloudbeds_guest_views.xml
│   ├── cloudbeds_log_views.xml
│   ├── cloudbeds_wizard_views.xml
│   └── menus.xml
├── security/
│   └── ir.model.access.csv
└── data/
    └── ir_cron.xml
```

---

## Alur Integrasi

### 1. Pipeline Reservasi Check-Out (Alur Utama)

```text
Tamu Check-Out di Cloudbeds
        │
        ▼
Webhook reservation/status_changed  (atau cron 15 menit)
        │
        ▼
cloudbeds.reservation (staging record) — cb_status = checked_out
        │
        ▼
getReservationInvoiceInformation  ← Ambil folio lengkap dari API
        │
        ▼
Resolve Guest → res.partner  (cari / buat dari guestID)
        │
        ▼
sale.order (baris kamar + item + penyesuaian)
        │
        ▼
action_confirm()
        │
        ▼
account.move (faktur out_invoice)  ← dari sale order
        │
        ▼
action_post()  (posting otomatis jika auto_confirm_invoices)
        │
        ▼
account.payment (per entri pembayaran CB)  ← mapped ke jurnal
        │
        ▼
Rekonsiliasi pembayaran ↔ faktur
        │
        ▼
cloudbeds.reservation.state = imported
```

### 2. Pipeline Pembatalan

```text
Reservasi dibatalkan di Cloudbeds
        │
        ▼
Webhook reservation/status_changed  (status = canceled)
        │
        ▼
Cek apakah invoice sudah terbit (state = posted)?
        │
        ├── Ya → account.move.reversal → Kredit Nota → action_post()
        │
        └── Tidak → cloudbeds.reservation.state = imported (selesai)
```

### 3. OAuth2 Flow

```text
Klik "Connect to Cloudbeds" di form backend
        │
        ▼
Redirect ke https://hotels.cloudbeds.com/oauth/authorize?...&state=<backend_id>
        │
        ▼
Pengguna login dan setujui izin akses
        │
        ▼
Cloudbeds redirect ke /cloudbeds/oauth/callback?code=...&state=<backend_id>
        │
        ▼
Exchange code → access_token + refresh_token
        │
        ▼
Token disimpan di cloudbeds.backend → Status: Connected
```

### 4. Webhook Real-Time

```text
Event di Cloudbeds (reservasi dibuat / status berubah)
        │
        ▼
POST ke /cloudbeds/webhook/<backend_id>
        │
        ▼
Verifikasi HMAC-SHA256 (X-Cloudbeds-Signature vs client_secret)
        │
        ▼
Dispatch ke handler:
  reservation/created        → buat staging record
  reservation/status_changed → update status, proses jika checked_out/canceled
```

### 5. Sinkronisasi Otomatis (Cron)

```text
Setiap 15 menit:
  Untuk setiap backend (state=connected, active=True):
    1. Sync Guests  (jika sync_guests=True)
    2. Sync Products (jika sync_products=True)
    3. Import Reservations (jika sync_reservations=True)
       └── Proses semua staging records yang pending+checked_out
```

---

## Cara Pemasangan

### Prasyarat

Modul-modul Odoo yang dibutuhkan: `base`, `mail`, `product`, `stock`, `account`, `sale`, `sale_management`

### Langkah Instalasi

1. Salin folder `cl_cloudbeds_integration` ke direktori addons Odoo
2. Restart server Odoo
3. Aktifkan mode developer: **Settings → Activate Developer Mode**
4. Pergi ke **Apps → Update Apps List**
5. Cari `Cloudbeds Integration` dan klik **Install**

---

## Konfigurasi Awal

### Langkah 1 — Buat Aplikasi di Cloudbeds Marketplace

1. Login ke Cloudbeds Developer Portal
2. Buat aplikasi OAuth2 baru
3. Catat **Client ID** dan **Client Secret**
4. Set Redirect URI ke:

```text
https://domain-odoo-anda.com/cloudbeds/oauth/callback
```

### Langkah 2 — Buat Backend di Odoo

1. Buka menu **Cloudbeds → Configuration → Backends → New**
2. Isi form:
   - **Backend Name**: nama bebas, misal "Grand Hotel Jakarta"
   - **Cloudbeds Property ID**: ID properti dari dashboard Cloudbeds
   - **OAuth Client ID** dan **OAuth Client Secret**: dari Developer Portal
   - **Revenue Journal**: jurnal untuk faktur pendapatan kamar
3. Simpan record

### Langkah 3 — Hubungkan ke Cloudbeds

1. Klik tombol **Connect to Cloudbeds**
2. Browser membuka halaman OAuth Cloudbeds
3. Login dan setujui izin akses
4. Status backend berubah menjadi **Connected**
5. Klik **Test Connection** untuk memverifikasi

### Langkah 4 — Konfigurasi Pemetaan Pembayaran (tab Accounting)

Tambahkan baris di tabel **Payment Method Mapping**:

| Tipe Pembayaran CB | Jurnal Odoo |
| --- | --- |
| `cash` | Kas |
| `credit` | Bank / EDC Kartu Kredit |
| `debit` | Bank / EDC Kartu Debit |
| `bank_transfer` | Transfer Bank |
| `check` | Cek / Giro |
| `on_account` | Piutang / Kredit Hotel |

### Langkah 5 — Konfigurasi Pemetaan Pajak (tab Accounting)

Tambahkan baris di tabel **Tax Mapping**:

| CB Tax ID | Nama CB | Pajak Odoo |
| --- | --- | --- |
| `tax_001` | PPN 11% | PPN 11% |
| `tax_002` | Service Charge 5% | Service Charge 5% |

CB Tax ID dapat dilihat dari respons `getTaxesAndFees` API Cloudbeds.

### Langkah 6 — Daftarkan Webhook (untuk real-time)

Klik **Register Webhooks** — modul mendaftarkan event:

- `reservation/created`
- `reservation/status_changed`

### Langkah 7 — Jalankan Sinkronisasi Pertama

Klik **Sync Now** atau gunakan **Operations → Manual Sync** untuk menjalankan import pertama.

---

## Navigasi Menu

Setelah instalasi, modul menambahkan aplikasi **Cloudbeds** di app switcher:

```text
Cloudbeds
├── Operations
│   ├── Reservations           ← Daftar reservasi yang diimpor dari Cloudbeds
│   ├── Guests                 ← Tamu yang tersinkronisasi dari Cloudbeds
│   ├── Linked Products        ← Produk yang terhubung ke Cloudbeds (kamar + item)
│   └── Manual Sync            ← Dialog sinkronisasi on-demand
├── Reports
│   └── Sync Logs              ← Audit trail setiap operasi sinkronisasi
└── Configuration
    └── Backends               ← Konfigurasi koneksi per properti
```

---

## Penggunaan Harian

### Sinkronisasi Otomatis

Berjalan setiap **15 menit** via scheduled action. Ubah interval di:
**Settings → Technical → Scheduled Actions → Cloudbeds: Auto Sync All Backends**

### Sinkronisasi Manual

**Cloudbeds → Operations → Manual Sync**

1. Pilih backend
2. Centang modul yang ingin disinkronkan (Guests, Products, Reservations)
3. Opsional: filter berdasarkan status reservasi dan rentang tanggal
4. Klik **Run Sync**

### Melihat Reservasi

**Cloudbeds → Operations → Reservations**

Setiap record menampilkan:

- ID dan nama tamu dari Cloudbeds
- Tanggal check-in dan check-out
- Status dari Cloudbeds dan status pemrosesan Odoo
- Total, total dibayar, dan saldo
- Link ke Sale Order dan Invoice di Odoo

Untuk memproses ulang reservasi yang error: buka form reservasi → klik **Process**.

### Melihat Log Sinkronisasi

**Cloudbeds → Reports → Sync Logs**

Setiap operasi sinkronisasi tercatat dengan:

- Jenis sinkronisasi (reservation / guest / product / payment / webhook)
- Arah (Cloudbeds → Odoo)
- Jumlah record berhasil dan gagal
- Detail error jika ada

---

## Endpoint API yang Digunakan

| Endpoint | Metode | Kegunaan |
| --- | --- | --- |
| `/access_token` | POST | Exchange code / refresh token |
| `/access_token_check` | GET | Verifikasi validitas token |
| `/getHotelDetails` | GET | Detail properti |
| `/getReservations` | GET | Daftar reservasi (paginasi) |
| `/getReservation` | GET | Detail satu reservasi |
| `/getReservationInvoiceInformation` | GET | Folio lengkap: kamar, item, pajak, pembayaran, penyesuaian |
| `/getGuestList` | GET | Daftar tamu (paginasi) |
| `/getGuest` | GET | Detail satu tamu |
| `/getItems` | GET | Katalog item/produk |
| `/getItemCategories` | GET | Kategori item |
| `/getPaymentMethods` | GET | Metode pembayaran yang tersedia |
| `/getTaxesAndFees` | GET | Pajak dan biaya yang dikonfigurasi |
| `/getWebhooks` | GET | Daftar webhook terdaftar |
| `/postWebhook` | POST | Daftarkan webhook baru |
| `/deleteWebhook` | DELETE | Hapus webhook |

**Base URL:** `https://hotels.cloudbeds.com/api/v1.3/`
**Autentikasi:** Bearer Token (OAuth2)
**Content-Type POST/PUT:** `application/x-www-form-urlencoded`

---

## Troubleshooting

| Masalah | Solusi |
| --- | --- |
| Status **Error** | Cek field **Last Error** di form backend → klik **Test Connection** |
| Reservasi tidak masuk | Pastikan backend **Connected**, cek **Sync Logs**, jalankan **Manual Sync** |
| Faktur tidak dibuat | Pastikan **Auto-Invoice on Checkout** diaktifkan dan **Revenue Journal** diisi |
| Pajak tidak sesuai | Periksa **Tax Mapping** di tab **Accounting** — CB Tax ID harus cocok persis |
| Pembayaran tidak terrekonsiliasi | Pastikan jurnal di **Payment Method Mapping** bertipe `cash` atau `bank` |
| Webhook tidak berfungsi | Pastikan URL Odoo dapat diakses publik, klik **Register Webhooks** ulang |
| Error 401 dari API | Token kedaluwarsa — klik **Connect to Cloudbeds** untuk autentikasi ulang |
| Error rate limit (429) | Kurangi frekuensi cron atau gunakan webhook real-time |
| Produk kamar tidak ditemukan | Produk dibuat otomatis saat reservasi pertama diproses |

---

## Catatan Teknis

- **Versi API Cloudbeds:** v1.3
- **URL Dasar:** `https://hotels.cloudbeds.com/api/v1.3/`
- **Autentikasi:** OAuth2 Authorization Code Flow
- **Token Refresh:** Otomatis saat API mengembalikan status 401
- **Retry Logic:** 3x retry untuk status 429, 500, 502, 503, 504
- **Rate Limiting:** Jeda 250 ms antar permintaan (batas Cloudbeds: 5 req/detik)
- **Pagination:** Otomatis via `pageNumber` + `pageSize` (default 100 per halaman)
- **Webhook Security:** Verifikasi HMAC-SHA256 menggunakan `X-Cloudbeds-Signature` header
- **Produk Hotel:** Semua produk bertipe `service` — tidak ada pergerakan stok fisik
- **Idempoten:** Reservasi diidentifikasi oleh `(cb_reservation_id, backend_id)` — aman diimpor ulang
- **Pemrosesan Error:** Setiap error pada level reservasi dicatat di `error_message` dan `cloudbeds.sync.log`

---

## Pengembang

**Creativin Labs**
Website: <https://www.creativin-labs.com>
