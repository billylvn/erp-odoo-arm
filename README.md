# erp-odoo-arm

Koleksi custom module Odoo 18 milik Creativin.

---

## Aturan Repo

- Setiap folder di root = satu Odoo module (wajib ada `__manifest__.py` dan `__init__.py`)
- Jangan taruh folder non-module di root — Odoo akan error saat scan addons path
- Semua module menggunakan prefix `cl_` dan versi `18.0.x.x.x`

---

## Modules

| Module | Deskripsi |
|--------|-----------|
| [`cl_cloudbeds_integration`](./cl_cloudbeds_integration) | Integrasi Odoo 18 ↔ Cloudbeds (reservasi, invoice, payment, guest, produk) |

---

## Menambah Module Baru

1. Buat folder di root repo dengan struktur minimal:
```
cl_nama_module/
├── __init__.py
├── __manifest__.py   ← version: '18.0.1.0.0', depends: [...]
├── models/
├── views/
└── security/
```

2. Restart Odoo, lalu install via **Apps → Update Apps List**

---

## Development & Deployment

Docker setup ada di repo terpisah: **`erp-odoo-docker`**

Repo ini di-mount langsung ke container sebagai `/mnt/extra-addons` — tidak perlu rebuild Docker image saat tambah/edit module, cukup restart Odoo.

### Local

```bash
cd erp-odoo-docker
docker compose up -d
docker compose restart web   # setelah edit module
```

### Deploy ke Server

```bash
# Di server — pertama kali
git clone <erp-odoo-arm-url>
git clone <erp-odoo-docker-url>

cd erp-odoo-docker
cp .env.example .env
nano .env   # sesuaikan ADDONS_PATH dan ENTERPRISE_PATH ke path di server

docker compose build
docker compose up -d
```

### Update module di server

```bash
cd erp-odoo-arm && git pull
cd ../erp-odoo-docker && docker compose restart web
# Jika ada perubahan model (field baru/hapus):
docker compose exec web odoo -c /etc/odoo/odoo.conf -d <db> -u <module> --stop-after-init
```
