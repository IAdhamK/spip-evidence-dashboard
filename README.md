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

Provider AI default menggunakan endpoint OpenAI-compatible Sumopod dengan model `deepseek-v4-pro`. Simpan key hanya pada file environment yang diabaikan Git:

```text
AI_PROVIDER=sumopod
SUMOPOD_API_KEY=...
DEEPSEEK_BASE_URL=https://ai.sumopod.com/v1
DEEPSEEK_CHAT_PATH=/chat/completions
DEEPSEEK_RESPONSES_PATH=/responses
DEEPSEEK_MODEL=deepseek-v4-pro
ANALYSIS_API_SURFACE=responses
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
- `GET /api/health/live`
- `GET /api/health/ready`
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

## Document Intelligence Pipeline V2

Pipeline V2 dibangun sebagai rangkaian engine terpisah: Intake/Security, File Router, Native Parsing, selective Visual/OCR, Document Structure, Coverage, Fact Extraction, **Compute Routing**, **Advanced RAG** (BM25 + cosine-IDF + semantic vector + RRF + pencarian katalog penuh KK–subunsur–parameter oleh DeepSeek), SPIP Mapping, constrained DeepSeek reranking, Domain Rule/Grade, Cross-document Synthesis, Independent Verification, Output/Explainability, Human Review, dan Evaluation/Learning. Nama dokumen ikut menjadi query, kandidat didiversifikasi agar satu subunsur tidak menutup alternatif lain, dan query expansion hanya menjadi fallback bila catalog search gagal. DeepSeek V4 Pro hanya membantu recall, pencarian, urutan, dan demotion kandidat; Grade tetap menjadi kewenangan rule engine.

Compute Routing memakai skor kompleksitas dan risiko deterministik yang berasal dari format, jumlah/struktur unit, visual/OCR pending, coverage, unit tanpa fakta deterministik, margin kandidat mapping, mapping score, grade risk, serta hasil verifier sebelumnya. Mode `screening/full_audit` hanya dicatat sebagai input provenance dan bukan satu-satunya penentu. Structured extraction hanya menerima unit eligible yang belum menghasilkan fakta lokal; constrained mapping model hanya boleh menurunkan kandidat menjadi `needs_review`; model verifier hanya dipakai sebagai veto second-pass pada mapping yang lebih dahulu lulus verifier deterministik. Ketiganya tidak mempunyai otoritas grade atau upload.

Aktifkan hanya pada environment pengembangan:

```text
ANALYSIS_PIPELINE_V2_ENABLED=true
ANALYSIS_PIPELINE_V2_SHADOW=false
LEGACY_SMART_UPLOAD_ENABLED=true
ALLOW_PARTIAL_PRIMARY=false
ANALYSIS_STRUCTURED_MODEL_ENABLED=false
VISION_ANALYSIS_ENABLED=false
ANALYSIS_MODEL_VERIFIER_ENABLED=false
ANALYSIS_LOCAL_OCR_ENABLED=true
ANALYSIS_LOCAL_OCR_PROVIDER=auto
ANALYSIS_LOCAL_OCR_LANGUAGES=ind+eng
ANALYSIS_LOCAL_OCR_MIN_CONFIDENCE=0.45
ANALYSIS_LOCAL_OCR_TESSERACT_PSM_MODES=6,3,11
ANALYSIS_LOCAL_OCR_PREPROCESSING_ENABLED=true
ANALYSIS_LOCAL_OCR_MAX_IMAGE_PIXELS=16000000
ANALYSIS_LOCAL_OCR_MAX_TILES=16
ANALYSIS_LOCAL_OCR_TIMEOUT_SECONDS=30
ANALYSIS_LOCAL_OCR_UNIT_BUDGET_SECONDS=180
ANALYSIS_LOCAL_OCR_DOCUMENT_BUDGET_SECONDS=900
ANALYSIS_LOCAL_OCR_MAX_ATTEMPTS_PER_UNIT=24
ANALYSIS_LOCAL_OCR_RENDER_BATCH_UNITS=4
ANALYSIS_PDF_RENDER_DPI=144
ANALYSIS_PDF_RETRY_RENDER_DPI=288
ANALYSIS_PDF_RETRY_MAX_UNITS=8
ANALYSIS_OFFICE_RENDERING_ENABLED=true
ANALYSIS_OFFICE_RENDER_MAX_PAGES=24
ANALYSIS_QUEUE_BACKEND=sqlite
ANALYSIS_EXPECTED_REPLICAS=1
ANALYSIS_WORKER_LEADER_LEASE_SECONDS=30
ANALYSIS_WORKER_LEADER_HEARTBEAT_SECONDS=10
ANALYSIS_PROMETHEUS_METRICS_ENABLED=true
ANALYSIS_REQUIRE_REVIEWER_IDENTITY=false
ANALYSIS_REQUIRE_REVIEWER_ROLE=false
ANALYSIS_REVIEWER_ROLE_HEADER=X-Reviewer-Roles
ANALYSIS_API_SURFACE=responses
ANALYSIS_ADVANCED_RAG_ENABLED=true
ANALYSIS_ADVANCED_RAG_DEEPSEEK_ENABLED=true
ANALYSIS_ADVANCED_RAG_MIN_CONFIDENCE=0.68
ANALYSIS_ADVANCED_RAG_AMBIGUITY_MARGIN=0.08
ANALYSIS_VISION_PROVIDER_VALIDATED=false
ANALYSIS_ROLLOUT_STAGE=development
ANALYSIS_CANARY_PERCENTAGE=0
```

Production memakai kontrak `analysis-rbac-v1`: 55 operasi V2 role-secured dan 6 operasi read-only pada authenticated-proxy/internal-network boundary. Seluruh mutation harus role-secured. Handover validator serta regression OpenAPI gagal bila route baru tidak diklasifikasikan, policy overlap/stale, atau mutation hanya mengandalkan network boundary.

Untuk memprioritaskan validasi fungsi sebelum hardening produksi, jalankan `scripts/run_functional_acceptance.py`. Runner memproses sampel korpus operasional dan fixture enam format pada database baru, memeriksa 16 engine, exact source quote/location, fail-closed coverage, resume/regression, dan mocked controlled-upload tanpa external write. Kontrak serta interpretasi hasil dijelaskan di `docs/FUNCTIONAL_ACCEPTANCE_V2.md`; hasil otomatis tidak pernah mengisi label ahli atau approval domain.

Pada environment pengembangan Sumopod, `ANALYSIS_STRUCTURED_MODEL_ENABLED` dan `ANALYSIS_MODEL_VERIFIER_ENABLED` dapat diaktifkan agar DeepSeek V4 Pro menjalankan structured extraction serta verifier kedua. OCR berjalan **lokal lebih dahulu** melalui Tesseract (`ind+eng`) pada image container; teks ber-confidence rendah tetap ditahan dari Fact Extraction tetapi disimpan sebagai kandidat **OCR Rescue** untuk diperiksa manusia tanpa kode. DOCX dirender per halaman, XLSX per sheet visible (`SinglePageSheets`), dan slide PPTX visual dirender penuh melalui LibreOffice headless → PDF → PNG. Relasi eksternal, ActiveX, embedded object, dan VBA dibuang dari salinan render; maksimal 24 halaman/sheet dijadwalkan per full audit dan sisanya tetap `pending`. Sumopod vision hanya menjadi fallback terpisah setelah capability dan consent tervalidasi.

Endpoint awal:

- `GET /api/analysis-runs/config`
- `POST /api/analysis-runs`
- `GET /api/analysis-runs/jobs/{job_id}`
- `POST /api/analysis-runs/jobs/{job_id}/cancel`
- `GET /api/analysis-runs/{id}`
- `GET /api/analysis-runs/{id}/events`
- `GET /api/analysis-runs/{id}/events/stream`
- `GET /api/analysis-runs/{id}/units`
- `GET /api/analysis-runs/{id}/document-map`
- `GET /api/analysis-runs/{id}/facts`
- `GET /api/analysis-runs/{id}/mappings`
- `POST /api/analysis-runs/{id}/reverify`
- `GET/POST /api/analysis-runs/{id}/review-decisions`
- `POST /api/analysis-runs/{id}/approve-upload`
- `POST /api/analysis-runs/{id}/controlled-upload`
- `GET /api/analysis-runs/metrics`
- `GET /api/analysis-runs/metrics/prometheus`
- `GET /api/analysis-runs/rule-catalog`
- `POST /api/analysis-runs/rule-approvals`
- `GET /api/analysis-runs/governance/rules`
- `GET /api/analysis-runs/governance/rules/history`
- `POST /api/analysis-runs/governance/rules/decisions`
- `GET /api/analysis-runs/governance/expert-dataset`
- `POST /api/analysis-runs/governance/expert-dataset/{id}/decision`
- `GET /api/analysis-runs/governance/vision`
- `POST /api/analysis-runs/governance/vision/probe`
- `POST /api/analysis-runs/governance/vision/decisions`
- `GET /api/analysis-runs/shadow-comparison`
- `POST /api/analysis-runs/shadow-comparisons/refresh`
- `GET /api/analysis-runs/shadow-comparison-report`
- `POST /api/analysis-runs/evaluation-reports`
- `GET /api/analysis-runs/evaluation-reports`
- `POST /api/analysis-runs/evaluation-reports/from-expert-gold`
- `GET /api/analysis-runs/release-evidence`
- `POST /api/analysis-runs/release-evidence`
- `GET /api/analysis-runs/promotion-readiness`
- `GET /api/analysis-runs/readiness-dashboard`
- `POST /api/analysis-runs/batch-intakes`
- `GET /api/analysis-runs/batch-intakes/recent`
- `GET /api/analysis-runs/batch-intakes/{batch_id}`
- `POST /api/analysis-runs/batch-intakes/{batch_id}/cancel`
- `GET /api/analysis-runs/guided-review/parameters`
- `GET /api/analysis-runs/guided-review/queue`
- `GET /api/analysis-runs/guided-review/export`
- `GET /api/analysis-runs/guided-review/{id}`
- `GET /api/analysis-runs/guided-review/{id}/document`
- `POST /api/analysis-runs/guided-review/{id}`
- `GET /api/analysis-runs/visual-review/queue`
- `GET /api/analysis-runs/visual-review/{run_id}/{unit_key}`
- `GET /api/analysis-runs/visual-review/{run_id}/{unit_key}/preview`
- `POST /api/analysis-runs/visual-review/{run_id}/{unit_key}/decision`
- `POST /api/analysis-runs/visual-review/{run_id}/apply`
- `POST /api/analysis-runs/{id}/expand-candidates`
- `POST /api/analysis-runs/{id}/retry`
- `POST /api/analysis-runs/{id}/cancel`
- `GET /api/analysis-runs/{id}/checkpoints`
- `POST /api/analysis-packages`
- `GET /api/analysis-packages/{id}`
- `POST /api/analysis-packages/{id}/review-decisions`

Visual/OCR memakai checkpoint durable per batch (`unit-checkpoint-v2`). Setelah setiap batch lokal, manifest seluruh unit disimpan pada stage `visual_ocr_manifest`, sedangkan unit yang sudah `processed` atau `partial` dicatat pada `visual_ocr_batch`; unit yang masih `ocr_required` tidak dianggap selesai. Checksum mengikat himpunan key beserta tipe, urutan, heading, status, lokasi sumber, teks, warning, dan metadata OCR, sehingga penambahan/penghapusan unit juga membatalkan resume. Bila worker mati atau lease kedaluwarsa, job dipulihkan dengan transisi atomik yang lebih dahulu menyimpan `resume_from_run_id`, mempertahankan payload, dan mengosongkan pointer run lama sebelum run retry dibuat. Graceful shutdown memasang state `stopping` sebelum menunggu thread sehingga enqueue baru langsung ditolak, lalu memeriksa cancellation di batas antar-batch, mengembalikan job ke antrean, dan tetap memperbarui leader lease sampai worker lama benar-benar keluar. Setiap write worker dipagari `attempt_count`; claim lama tidak dapat renew, attach, supersede, requeue, complete, atau fail job claim baru. Retry hanya memakai ulang snapshot yang manifest-nya lengkap dan identik lalu menjalankan kembali unit sisanya. Kegagalan menulis checkpoint menggagalkan run secara eksplisit, sedangkan checkpoint stale/tampered ditolak fail-closed. Statusnya dapat diaudit melalui `GET /api/analysis-runs/{id}/checkpoints`; konfigurasi efektif tersedia pada field `checkpointing` di `GET /api/analysis-runs/config`. Metric worker memisahkan `stopping`, `draining`, dan `accepting_jobs` tanpa membawa nama atau isi dokumen. `/api/health/live` tetap hidup selama drain, sedangkan `/api/health/ready` menjadi 503 segera saat worker tidak dapat menerima job; Docker menggunakan probe readiness ini. Worker-down alarm menoleransi shutdown yang sah, sedangkan drain lebih dari sepuluh menit mempunyai alarm kritis tersendiri.

Cancellation tersedia melalui job ID maupun run ID. Cancel job queued yang belum mempunyai run langsung melepas payload. Cancel run yang sedang menunggu recovery menutup job dan run dalam satu transaksi sebelum payload dilepas; cancel-vs-claim diserialisasi dengan write lock. Untuk job `running`, status menjadi `cancel_requested` dan orchestrator berhenti pada batas aman antar-engine. Bila worker mati sebelum memproses request tersebut, lease recovery tetap menutup run, mencatat event `run_cancelled`, membersihkan payload, dan memperbarui shadow ledger. Pemanggilan ulang endpoint run yang sudah cancelled bersifat idempotent.

Scale-out adapter F7 sudah disiapkan tanpa membuat false readiness. `postgresql` hanya aktif bila repository membuktikan shared canonical state, atomic distributed claim, dan shared payload storage. `redis` hanya menjadi FIFO wake-up di atas PostgreSQL; claim/lease tetap canonical dan polling PostgreSQL menjadi fallback bila signal hilang atau duplikat. Redis production default wajib `rediss://`, URL tidak pernah diekspos, dan factory tidak menyentuh Redis selama canonical backend masih SQLite. Karena repository aplikasi saat ini masih SQLite, profil resmi tetap `ANALYSIS_QUEUE_BACKEND=sqlite` dengan satu replica.

V2 masih berstatus pengembangan/shadow. Grade rule yang dikompilasi dari matriks berstatus `draft`, sehingga primary upload tetap diblokir sampai checksum rule disahkan domain owner, seluruh verification berstatus `verified`, dan keputusan reviewer tersimpan. Controlled upload memakai duplicate check, flag real-upload legacy, dan reservation Migration V27 sebelum side effect WebDAV. `approve-upload` adalah alias roadmap dari `controlled-upload`; keduanya memakai kunci idempotensi yang sama. Retry sesudah sukses mengembalikan action yang sama tanpa upload ulang, sedangkan hasil eksternal yang ambigu dikunci sebagai `blocked_ambiguous`. Migration V28 menyediakan rekonsiliasi append-only dari dua reviewer independen tanpa membuka retry/reset action. Roadmap lengkap tersedia di `docs/ROADMAP_DOCUMENT_INTELLIGENCE.md`; operasi, backup, rollback, dan release gate tersedia di `docs/OPERATIONS_DOCUMENT_INTELLIGENCE.md`.

Paket handover teknis tersedia pada `docs/API_DOCUMENT_INTELLIGENCE_V2.md`, `docs/SCHEMA_DOCUMENT_INTELLIGENCE_V2.md`, `docs/RULE_AUTHORING_GUIDE.md`, `docs/EVAL_AUTHORING_GUIDE.md`, dan `docs/HANDOVER_CHECKLIST_DOCUMENT_INTELLIGENCE.md`. `PYTHONPATH=backend python scripts/validate_handover_docs.py` membandingkan referensi tersebut dengan OpenAPI dan DDL aktif; knowledge transfer serta sign-off manusia tetap harus dicatat terpisah dan tidak dapat digantikan validator.

Mitigasi sementara tersedia tanpa melemahkan release gate: dashboard readiness lokal, bootstrap regression 50 kasus non-expert, reviewer-triggered candidate expansion, retry run dari payload yang belum dipurge, capability gate vision, serta rollout guard yang otomatis kembali ke `development` ketika syarat shadow/canary belum terpenuhi.

Payload job dan dokumen V2 mempunyai storage abstraction backward-compatible. Default proses Python lokal tetap `database`; profile Docker Compose memakai `filesystem` content-addressed pada `/app/data/analysis-payloads`. File ditulis atomik dengan permission privat, diverifikasi ulang memakai SHA-256 dan ukuran pada setiap read, dibagi aman oleh job/dokumen identik, dihapus setelah TTL hanya ketika tidak lagi direferensikan, dan orphan dibersihkan saat worker mulai. Kerusakan, symlink, permission terbuka, file hilang, atau checksum mismatch tidak pernah fallback diam-diam ke BLOB—job/preview tetap fail-closed. Volume produksi harus terenkripsi oleh platform karena aplikasi belum menerapkan encryption layer sendiri. Gate canary kini memerlukan attestation HMAC yang private, belum kedaluwarsa, dan terikat ke path/device database serta payload runtime; `ANALYSIS_PAYLOAD_STORAGE_ENCRYPTION_VALIDATED=true` tanpa evidence tidak lagi cukup.

Saat shadow mode aktif, setiap legacy review otomatis dicatat sebagai pasangan dengan job/run V2 pada ledger Migration V22. Worker menghitung overlap, top-1 match, exact-set match, legacy coverage, dan Jaccard setelah run terminal; report agregat hanya berisi kode parameter, ID teknis, metrik, dan SHA-256—tanpa nama atau isi dokumen. Agreement V1/V2 bukan label kebenaran. Keputusan rilis `passed` tetap memerlukan minimal 50 pasangan shadow terminal sekaligus evaluation expert-gold yang memenuhi threshold.

Migration V23 mencatat jumlah pemanggilan harian seluruh endpoint pipeline V1 dan bridge controlled-upload V2→legacy tanpa nama file, isi dokumen, payload, atau identitas reviewer. **Bukti Rilis** menampilkan panggilan V1 sejak stable-cycle observation dimulai. Deprecation tetap terkunci sampai dua siklus stabil tervalidasi, rollback pernah diuji, telemetry mencakup observation window, dan jumlah panggilan V1 pada window tersebut nol. Counter yang sama tersedia sebagai `spip_legacy_pipeline_calls_total{usage_kind,source}`.

Migration V24 menambahkan learning vocabulary yang konservatif untuk Retrieval Engine. Registry hanya dibangun ulang dari label `expert_gold` aktif yang telah melewati pemeriksaan dua orang dan hanya dari fakta sumber yang mereka pilih. Sebuah istilah baru harus muncul pada minimal tiga dokumen berbeda dan konsisten ke parameter yang sama dengan precision minimal 80%; candidate, jawaban `unsure`, prediksi mesin, organization/period token, dan istilah ambigu diabaikan. Snapshot terikat checksum dataset serta katalog parameter, append-only, dan otomatis tidak dipakai ketika stale. Database hanya menyimpan fingerprint SHA-256 istilah—bukan klaim atau teks istilah—sementara Prometheus hanya menampilkan status dan jumlah agregat. Feedback memberi bonus retrieval maksimal 0,18 dan tidak pernah menentukan grade atau melewati rule/verification.

Migration V25 memisahkan `expert_gold` menjadi partisi **Evaluasi** dan **Learning**. Existing label selalu dimigrasikan ke Evaluasi dan snapshot V24 lama dinonaktifkan. Target release 50/200, Recall@5, source accuracy, serta overgrade hanya dihitung dari Evaluasi; compiler vocabulary hanya membaca Learning. Dokumen dengan checksum yang sama dilarang berada pada kedua partisi, evaluation report menolak overlap, dan learning fail-closed bila overlap ditemukan. Domain owner memilih tujuan kasus melalui **Governance V2 → Dataset Ahli** tanpa mengedit JSON.

Migration V26 memberi `release_authority` immutable pada evaluation report. Semua report lama dan import manual tetap `informational`; hanya `server_derived_v2_partitioned` yang dibuat dari holdout Evaluasi aktif yang berwenang. Promotion readiness mengabaikan report informasional. Saat menyimpan keputusan `passed`, server menghitung ulang report dan mensyaratkan report SHA-256, recomputed SHA-256, dataset checksum, case count, serta authority semuanya sama. Stable-cycle lama tanpa bukti authority/recompute V26 tetap disimpan tetapi tidak dihitung untuk deprecation V1.

Migration V27 menambahkan reservation idempotent untuk controlled upload. Satu run/mapping hanya boleh mempunyai satu action berkunci; status `uploading` dibuat atomik sebelum memanggil legacy WebDAV bridge dan difinalisasi menjadi `uploaded_primary` atau `blocked_ambiguous`. Reservation di atas sepuluh menit memicu metric dan alert kritis agar operator memeriksa folder tujuan serta legacy review tanpa retry otomatis yang berisiko menggandakan file.

Migration V28 menambahkan ledger rekonsiliasi controlled-upload append-only. Hanya action terminal `blocked_ambiguous` yang dapat diperiksa melalui `POST /api/analysis-runs/{run_id}/controlled-upload-actions/{action_id}/reconciliation`; reservation `uploading` tidak dapat diubah dari endpoint tersebut. Outcome baru efektif setelah dua identitas reviewer berbeda mengirim keputusan terminal yang sama dengan optimistic-concurrency ID dan attestation pemeriksaan folder tujuan serta legacy review. Outcome final tidak dapat ditimpa, action asli tidak dihapus, dan `confirmed_not_uploaded` tidak mengaktifkan upload ulang otomatis.

Migration V29 menambahkan `evidence_role` dan provenance method pada setiap fakta. Peran `primary`, `supporting`, `context`, atau `contradictory` diturunkan deterministik lebih dahulu; structured model hanya boleh memberi saran peran yang tersimpan sebagai advisory dan tidak mempunyai otoritas grade. Review Terpandu mewajibkan reviewer memeriksa peran untuk mapping positif, sedangkan evaluation report mengukur akurasi serta cakupan label peran secara content-free.

Migration V30 menambahkan expected template status pada label ahli. Reviewer memilih apakah dokumen mempunyai isi substantif atau hanya template/instruksi/kolom kosong; label lama tetap `not_assessed` dan tidak dapat disahkan sebagai expert gold sebelum ditinjau ulang. Report server-derived membandingkan label tersebut dengan ledger `template_completeness`, mengukur akurasi, recall kelas template kosong, serta label coverage. Promotion readiness menahan report bila coverage atau recall template kosong berada di bawah 95%; korpus tanpa contoh template kosong tidak dianggap lulus.

Frontend menjalankan `npm run check:boundaries` sebelum build. Smart Upload beserta ZIP intake, progress, V1/V2 result, package synthesis, readiness, dan upload action kini menjadi feature ownership mandiri dengan endpoint allowlist. Governance, Guided Review, Visual Review/OCR Rescue, Operational Readiness, Shadow Comparison, dan Bukti Rilis juga terpisah dari composition root. Formatter, lokasi sumber, feedback, dan status badge dipakai bersama tanpa duplikasi. Peta review perubahan tersedia di `docs/CHANGESET_REVIEW_MANIFEST.md` agar worktree besar dapat diperiksa per cluster tanpa men-stage perubahan pengguna secara membabi buta.

Internal Smart Upload juga telah dipecah: `SmartUploadPage.jsx` 478 baris hanya mengelola state, polling, dan job orchestration; `features/smart-upload/` memiliki ZIP intake, controls/progress, link crawl, Document Intelligence V2 result, batch panels, legacy result, dan utilitas bersama. Boundary verifier memeriksa endpoint seluruh submodule, melarang API/hooks pada panel presentasional, mencegah komponen pindahan kembali ke root, dan menegakkan line budget. Production build serta HTTP smoke root/bundle/config/readiness lulus; visual browser untuk refactor ini tidak dijadikan bukti karena akses localhost ditolak oleh policy browser.

Transport HTTP legacy untuk Sumopod/DeepSeek berada di `backend/app/legacy_ai_transport.py`: URL normalization, `deepseek-v4-pro` body/thinking contract, timeout/error mapping, serta credential redaction tidak lagi bercampur dengan extraction/recommendation domain. `backend/app/smart_upload.py` tetap mere-export API lama agar route V1 dan V2 `legacy_bridge` kompatibel selama migrasi.

Ekstraksi dokumen legacy kini berada di `backend/app/legacy_document_extraction.py`, dengan helper normalisasi/keyword tunggal di `backend/app/legacy_text_utils.py`. PDF, DOCX, XLSX, PPTX, text, image metadata, Office locator helper, mode analisis, dan error malformed archive tetap dire-export oleh `smart_upload.py`; parity serta architecture tests menjaga kontrak V1. Worker V2 sekarang dimiliki FastAPI lifespan melalui `backend/app/lifecycle.py`, sehingga start/stop dan cleanup saat exception tidak lagi memakai hook `on_event` yang deprecated. Image Python 3.12 lulus import aplikasi/parser dan health smoke dengan seluruh warning diperlakukan sebagai error.

Classification dan reasoning legacy kini berada di `backend/app/legacy_recommendation_domain.py` sebagai pure-domain module tanpa database, WebDAV, atau provider AI. Template/actuality guard, maturity E–A, anti-overgrade, XLSX evidence-reference interpretation, candidate reasoning, AI-placement demotion, dan package gate tetap dire-export oleh `smart_upload.py`. Regression contract membuktikan template kosong tetap maksimal Grade E/fail-closed, rantai bukti lengkap dapat mencapai Grade A, dan model tidak dapat mempertahankan penempatan utama ketika gate deterministik menolaknya.

Normalisasi naratif AI, placement, dan merge reranking berada di `backend/app/legacy_ai_normalization.py`; immutable candidate seed/ranking berada di `backend/app/legacy_candidate_ranking.py`; sedangkan checksum, duplicate block, upload action validation, filename sanitation, dan analysis summary berada di `backend/app/legacy_upload_support.py`. Seluruh nama lama tetap dire-export. `smart_upload.py` kini merupakan facade sekitar 1.008 baris yang hanya memiliki `SmartUploadService` dan dua fungsi orchestration naratif DeepSeek, sehingga patchability transport V1 tetap dipertahankan tanpa mencampur kembali domain helper.

Pengguna nonteknis dapat memilih satu ZIP pada **Upload Pintar → Masukkan ZIP menjadi antrean review**. Backend memeriksa traversal, collision, symlink, enkripsi, ukuran, rasio kompresi, signature setiap dokumen, duplikat isi, serta limit sebelum membuat job. Mode default **Proses lokal saja** menonaktifkan structured model, model verifier, dan vision eksternal per-job walaupun DeepSeek aktif secara global; OCR lokal tetap dapat berjalan tanpa mengirim dokumen keluar. Batch tersimpan, dapat dibatalkan, dipantau per file, dan otomatis dimuat kembali setelah halaman direload.

Setelah batch selesai, pengguna membuka **Review Terpandu** untuk menilai satu dokumen pada satu waktu, memilih parameter resmi, mencentang fakta sumber, memeriksa peran evidence, menandai dokumen substantif atau template kosong, menyimpan koreksi, melanjutkan pekerjaan yang tertunda, dan mengunduh hasil review. Petunjuk tiga tahap dan checklist **Siap disimpan** membuat alur ini cukup dilakukan dengan membaca, memilih, mencentang, dan menekan **Simpan & Lanjut**—tanpa JSON atau pemrograman. Label awal berstatus `expert_candidate` atau `pilot_unlabelled`; label tersebut tidak otomatis menjadi `expert_gold`, mengesahkan rule, mengaktifkan OCR/vision, atau membuka upload produksi. Domain owner kedua kemudian memakai **Governance V2 → Dataset Ahli** untuk membuka dokumen, memeriksa mapping/grade, peran evidence, status template, serta lokasi sumber, memilih tujuan **Evaluasi rilis** atau **Learning retrieval**, lalu mengesahkan atau mengembalikan kasus. Aplikasi menegakkan two-person review, melarang checksum dokumen berada di kedua partisi, dan menghitung checksum dataset otomatis. Panduan klik demi klik tersedia di `docs/GUIDED_REVIEW_USER_GUIDE.md` dan `docs/GOVERNANCE_USER_GUIDE.md`.

Unit foto/gambar, halaman visual DOCX, snapshot sheet XLSX, screenshot slide PPTX, kandidat OCR di bawah ambang aman, dan unit tanpa hasil OCR muncul pada halaman **Review Visual** bila maknanya belum pasti. Pengguna membandingkan preview sumber dengan teks, lalu memilih **Sesuai**, **Perbaiki**, **Bukan Bukti**, atau **Belum Yakin**. Untuk unit tanpa kandidat, pilihan **Sesuai** sengaja nonaktif dan pengguna cukup memilih **Perbaiki** lalu mengetik teks yang terlihat. Keputusan disimpan append-only dan terikat checksum teks, kandidat OCR, serta gambar. Setelah seluruh unit diputuskan, tombol **Buat Run Turunan** menghitung ulang analisis; run sumber tidak diubah. Panduan no-code tersedia di `docs/VISUAL_REVIEW_USER_GUIDE.md`.

Domain owner dapat memakai **Governance V2** untuk memeriksa lima grade per parameter, mengesahkan kandidat dataset ahli, menyimpan keputusan atomik berbasis checksum, serta mempertahankan history. Tab **Bukti Rilis** menghitung evaluation report dan checksum dataset otomatis, mencatat planned/started/passed/failed/rollback sebagai event append-only, menolak report stale, serta menahan `passed`/stable cycle ketika gate 50/200, grade coverage, rule, security, OCR, incident, atau rollback belum terpenuhi. Tab OCR/Vision menampilkan status OCR lokal dan menjalankan probe synthetic untuk fallback eksternal tanpa dokumen pengguna, kemudian memisahkan capability approval dari consent data `restricted`. Runtime vision eksternal membutuhkan approval, consent, feature flag, provider-validation flag, API key, dan renderer sekaligus. Probe live Sumopod `deepseek-v4-pro` pada 12 Juli 2026 belum lolos kontrak `unit_key`, sehingga fallback vision tetap fail-closed. Tesseract `ind+eng` memakai fallback layout PSM 6→3→11, preprocessing gambar transparan, retry resolusi tinggi PDF/Office, low-DPI fitting untuk raster Office over-tile, serta tiled OCR maksimal 16 tile tanpa menurunkan confidence 0,45. Hard budget default membatasi 30 detik per subprocess, 180 detik/24 attempt per unit, 900 detik per dokumen, dan menginterleave render→OCR per empat unit. Audit korpus v10 memproses 482/500 unit visual/OCR, mencatat 27.439 region, mengecualikan 64 hidden sheet dari OCR visual, menjadwalkan seluruh 170 halaman Office visible tanpa deferred, dan mempertahankan 18 unit fail-closed. Sebanyak 51 halaman memakai tiled OCR; outlier 20 tile berhasil dirender 144→115 DPI dan diproses dalam 12 tile tanpa menaikkan batas. Resource probe v11 pada PDF 68 halaman membuktikan hard stop 30/90/240 detik, menyelesaikan base attempt seluruh halaman dalam tier 240 detik, dan tetap menahan 17 kandidat rendah untuk retry/review. Full-document gate menghasilkan 193 visual-pending dan 64 dokumen complete; angka complete turun dari baseline text-centric 78 karena 14 dokumen Office yang sebelumnya dianggap lengkap sekarang sengaja menunggu review visual. Tersisa 13 kandidat OCR Rescue dan 5 unit tanpa kandidat yang dapat ditranskripsi manual melalui UI; seluruh teks visual tetap dilarang menjadi fakta sebelum keputusan manusia yang checksum-bound. Panduan tersedia di `docs/VISUAL_REVIEW_USER_GUIDE.md` dan `docs/GOVERNANCE_USER_GUIDE.md`.

Audit ZIP korpus lokal dapat dijalankan tanpa mengirim dokumen ke provider AI:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_document_corpus.py /path/to/corpus.zip \
  --extract-dir /private/tmp/spip-corpus \
  --output-dir outputs/corpus-audit/batch-id \
  --local-ocr --pdf-retry-max-units 24 --office-render-max-pages 24
```

Direktori ekstraksi harus kosong. Skrip menulis `document_results.checkpoint.jsonl` setelah setiap dokumen; ulangi command dengan `--resume` untuk melanjutkan profil audit yang sama. Reprocessing selektif dapat memakai `--reuse-results baseline/document_results.jsonl --reprocess-kinds docx,xlsx`; reuse tetap terikat SHA-256 dan nama file. Hasil mencakup pemeriksaan keamanan, manifest `pilot_unlabelled`, metrik per dokumen, deteksi duplikat, dan antrean 50 kandidat expert review. Output korpus diabaikan Git; file mentah dan laporan sensitif tidak boleh di-commit. Pembelajaran batch operasional pertama tersedia di `docs/CORPUS_LEARNING_20260706.md`.

Rehearsal outage/rollback lokal dapat dijalankan tanpa dokumen pengguna atau AI eksternal:

```bash
.venv/bin/python scripts/run_incident_drill.py \
  --output outputs/rollout-readiness/incident-drill.json
```

Drill memverifikasi provider outage tetap fail-closed, backup/restore SQLite, konfigurasi rollback ke legacy, serta rollout guard. Manifest baseline UI terdapat di `docs/UI_BASELINE_20260713.md` dan technical-debt register di `docs/TECHNICAL_DEBT_DOCUMENT_INTELLIGENCE.md`.

Stack observability lokal dapat dijalankan sebagai profile terpisah:

```bash
docker compose --profile observability up -d
```

Profile memakai Prometheus `v3.12.0` dan Alertmanager `v0.32.1`, men-scrape endpoint agregat aplikasi, dan memuat tujuh belas alert rule termasuk anomali estimasi biaya rolling satu jam terhadap `ANALYSIS_COST_ALERT_USD_PER_HOUR`, recovery loop pada claim ketiga, graceful drain macet, kehilangan singleton worker-leader lease, controlled-upload reservation macet/ambiguity yang belum direkonsiliasi, OCR budget exhaustion/timeout spike, klaim enkripsi tanpa attestation valid, serta warning 14 hari sebelum expiry. Endpoint juga mengekspor outcome dan rasio override review secara content-free. Receiver default tidak mengirim data keluar. Profile webhook opt-in tersedia di `ops/alertmanager/alertmanager.webhook.yml`; URL organisasi dibaca dari mounted secret file, bukan disimpan di Git. Referensi boundary SSO/trusted identity dan role header tersedia di `ops/reverse-proxy/nginx.conf`.

Worker V2 juga mengeluarkan JSON lifecycle `analysis-runtime-log-v1` melalui logger `uvicorn.error.spip.analysis`. Record hanya berisi event allowlist, job/run ID, status/reason code, attempt, dan counter numerik. Nama/isi dokumen, URL, source location, prompt, reviewer, respons provider, dan exception text tidak mempunyai field log. Event terminal dapat dikorelasikan ke ledger database menggunakan `run_id` tanpa menyalin evidence ke sistem log.

Sebelum profile produksi dinyalakan, platform owner menerbitkan bukti menggunakan `scripts/issue_storage_encryption_attestation.py`, lalu menjalankan `scripts/validate_production_profile.py` dengan `--database-path`, `--payload-root`, `--storage-encryption-evidence-file`, dan `--storage-encryption-key-file`. Validator v7 memeriksa HTTPS, permission secret, profile Alertmanager, spoof-protection identity+role proxy, enforcement RBAC runtime, ownership/permission/content-addressing storage, signature/expiry/binding storage, permission dan integrity SQLite, exact Migration V30, schema/index evidence-role dan expected-template, index idempotensi, trigger append-only rekonsiliasi, reservation `uploading` di atas sepuluh menit, dan jumlah ambiguity yang belum memperoleh keputusan cocok dari dua reviewer. Report tidak menampilkan URL, token, path, signature, reviewer, role aktual, alasan pemeriksaan, atau isi evidence. Perintah container harus dijalankan dari namespace/mount yang sama dengan backend; langkah lengkap tersedia di `docs/OPERATIONS_DOCUMENT_INTELLIGENCE.md`. Backup database dengan referensi filesystem wajib menyertakan `--payload-source`, `--payload-destination`, dan `--restore-payload-to`; manifest backup/restore mengikat seluruh artefak menggunakan checksum content-free.
