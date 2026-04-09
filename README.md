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

```
cl_nama_module/
├── __init__.py
├── __manifest__.py   ← version: '18.0.1.0.0'
├── models/
├── views/
└── security/
```

Setelah push: CI/CD otomatis pull & restart. Install manual via **Apps → Update Apps List**.

---

## CI/CD

Push ke `main` → GitHub Actions otomatis:

1. Detect modul mana yang berubah
2. SSH ke server → `git pull`
3. `docker compose restart web`
4. Jika ada perubahan `.py` → jalankan upgrade modul (`-u <module>`)

### Setup (sekali saja)

Tambahkan secrets berikut di **GitHub → Settings → Secrets → Actions**:

| Secret | Nilai |
|--------|-------|
| `SSH_HOST` | IP atau domain server |
| `SSH_USER` | Username SSH (misal `ubuntu`) |
| `SSH_PRIVATE_KEY` | Private key untuk SSH ke server |
| `SSH_PORT` | Port SSH (default `22`) |
| `ADDONS_PATH` | Path repo ini di server (misal `/opt/erp-odoo-arm`) |
| `DOCKER_PATH` | Path docker repo di server (misal `/opt/arm-odoo-docker`) |
| `ODOO_DB` | Nama database Odoo di server |

### Setup server (pertama kali)

```bash
# Di server
git clone <erp-odoo-arm-url> /opt/erp-odoo-arm
git clone <docker-repo-url>  /opt/arm-odoo-docker

cd /opt/arm-odoo-docker
cp .env.example .env && nano .env
docker compose up -d
```

Tambahkan public key GitHub Actions ke `~/.ssh/authorized_keys` di server.

---

## Development Lokal

```bash
cd /path/to/arm-odoo-docker
docker compose up -d
docker compose restart web        # setelah edit module
```

Upgrade manual jika ada perubahan model:
```bash
docker compose exec web odoo -c /etc/odoo/odoo.conf \
  -d <database> -u <module> --stop-after-init
```
