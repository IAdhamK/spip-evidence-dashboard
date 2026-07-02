# SPIP Evidence Dashboard

Aplikasi internal untuk membaca metadata file evidence dari Lumbung File Kemendesa melalui WebDAV public share, lalu menampilkan dashboard status evidence per KK dan subunsur SPIP.

## Fitur MVP

- Backend FastAPI read-only untuk WebDAV `PROPFIND`.
- SQLite untuk menyimpan mapping folder, status, dan metadata file.
- React + Vite SPA untuk dashboard monitoring.
- Status otomatis:
  - `Kosong`: 0 file.
  - `Terisi Sebagian`: 1-3 file.
  - `Terisi`: minimal 4 file.
  - `Perlu Kurasi`: ada nama file yang ambigu.
  - `Final`: disiapkan untuk verifikasi manual tahap berikutnya.
- Tooltip hover untuk penjelasan status, metrik, dan acuan parameter.
- Detail panel per subunsur dengan daftar file dan tombol buka folder Lumbung File.
- Acuan parameter matriks disimpan per kombinasi `KK + kode subunsur + nomor parameter + grade`, sehingga detail seperti `KK3.1/1.3.1/Grade A` sampai `Grade E` dapat ditampilkan sesuai workbook.
- Folder evidence detail dapat ditrack sampai level `detail parameter -> grade`, lengkap dengan link Lumbung File dan jumlah file per grade.

## Menjalankan Dengan Docker

```bash
cp .env.example .env
```

Isi `LUMBUNG_SHARE_TOKEN` di `.env`, lalu jalankan:

```bash
docker compose up --build
```

Buka:

```text
http://localhost:3000
```

Backend:

```text
http://localhost:8000/api/health
```

## Menjalankan Lokal Tanpa Docker

Backend:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Endpoint API

- `GET /api/health`
- `GET /api/meta`
- `GET /api/dashboard`
- `GET /api/kk`
- `GET /api/kk/{kk_id}`
- `GET /api/subunsur/{kk_id}/{kode}`
- `GET /api/subunsur/{kk_id}/{kode}/files`
- `POST /api/sync`
- `POST /api/sync/{kk_id}/{kode}`

## Deployment Online

Repository ini memiliki dua pola deployment:

### Full-stack live

```text
Frontend React + backend FastAPI berjalan dalam satu container.
```

Gunakan `Dockerfile` di root repository atau `render.yaml` untuk Render. Set environment variable berikut di platform hosting:

```text
LUMBUNG_HOST=https://lumbungfile.kemendesa.go.id
LUMBUNG_SHARE_TOKEN=isi-token-share
DATABASE_PATH=/app/data/evidence.db
SCAN_TIMEOUT_SECONDS=30
```

Mode ini mendukung tombol `Sinkronkan` langsung dari dashboard karena API backend tersedia online pada origin yang sama.

### GitHub Pages snapshot

```text
https://iadhamk.github.io/spip-evidence-dashboard/
```

Versi Pages hanya snapshot read-only dari data terakhir yang diekspor ke `frontend/public/snapshot.json`. GitHub Pages tidak cocok untuk kebutuhan upload lalu langsung terbaca, karena tidak menjalankan backend FastAPI.

## Catatan

Aplikasi tahap ini hanya membaca metadata file. Upload, rename, delete, login, role PIC/admin, dan verifikasi manual final belum dimasukkan ke MVP.

Data `Acuan Parameter Matriks` pada detail subunsur diekstrak dari workbook `Kertas Kerja PM SPIP 2026.xlsx` ke `backend/app/spip_parameters.json`. Setiap parameter menyimpan daftar grade A-E berisi kriteria, penjelasan, kode parameter, cara pengujian, serta slot folder evidence per grade. Panduan evidence umum tetap dipakai sebagai bantuan awal, bukan pengganti parameter resmi matriks.
