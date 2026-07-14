# Document Intelligence V2 Operations Runbook

## Deployment profiles

### Development

```text
ANALYSIS_PIPELINE_V2_ENABLED=true
ANALYSIS_PIPELINE_V2_SHADOW=false
LEGACY_SMART_UPLOAD_ENABLED=true
SMART_UPLOAD_ALLOW_REAL_UPLOAD=false
ALLOW_PARTIAL_PRIMARY=false
```

### Shadow pilot

```text
ANALYSIS_PIPELINE_V2_ENABLED=true
ANALYSIS_PIPELINE_V2_SHADOW=true
LEGACY_SMART_UPLOAD_ENABLED=true
SMART_UPLOAD_ALLOW_REAL_UPLOAD=false
ANALYSIS_REQUIRE_REVIEWER_IDENTITY=true
ANALYSIS_REQUIRE_REVIEWER_ROLE=true
ANALYSIS_REVIEWER_IDENTITY_HEADER=X-Reviewer-Identity
ANALYSIS_REVIEWER_ROLE_HEADER=X-Reviewer-Roles
ANALYSIS_ROLLOUT_STAGE=shadow
ANALYSIS_CANARY_PERCENTAGE=0
```

In shadow mode, V1 remains authoritative. Each legacy recommendation returns a `v2_shadow.job_id`; compare it to the terminal V2 run through `/api/analysis-runs/shadow-comparison`.

### Controlled canary

Keep legacy enabled and shadow disabled. Enable real upload only after rule, verification, reviewer identity, and expert eval gates pass. Run one backend replica while using the SQLite job queue.

Set `ANALYSIS_QUEUE_BACKEND=sqlite` and `ANALYSIS_EXPECTED_REPLICAS=1`. The worker refuses to start when SQLite is configured with more than one expected replica. Migration V21 menambahkan singleton worker-leader lease di database; manager kedua yang salah konfigurasi tetap ditahan walaupun keduanya mengaku sebagai replica tunggal. Atomic `BEGIN IMMEDIATE` claim, WAL, leader lease, job lease, heartbeat, dan attempt fencing melindungi beberapa thread/koneksi/proses pada deployment tersebut. `ANALYSIS_WORKER_LEADER_LEASE_SECONDS=30` dan `ANALYSIS_WORKER_LEADER_HEARTBEAT_SECONDS=10` adalah default; lease yang hilang menghentikan worker fail-closed dan lease crash dapat direbut kembali setelah kedaluwarsa. Setiap claim menaikkan `attempt_count`; worker lama tidak dapat renew, attach run, supersede, requeue, complete, atau fail bila attempt telah berubah.

Nama adapter `postgresql` dan `redis` kini dikenali, tetapi tidak mengubah SQLite menjadi shared persistence. Adapter PostgreSQL hanya aktif setelah repository melaporkan `backend_name=postgresql`, `shared_across_replicas=true`, `atomic_distributed_claims=true`, dan `shared_payload_storage=true`. Redis bukan sumber kebenaran: ia hanya FIFO wake-up signal di atas canonical PostgreSQL, sedangkan claim/lease tetap ditransaksikan pada canonical store dan fallback polling menangani signal hilang atau duplikat. Set `ANALYSIS_QUEUE_REDIS_URL`, namespace, dan timeout hanya setelah capability PostgreSQL tersebut nyata; produksi default mewajibkan `rediss://` melalui `ANALYSIS_QUEUE_REDIS_REQUIRE_TLS=true`. URL/credential tidak pernah dikembalikan oleh config, readiness, metric, atau structured log. Selama `Database` masih SQLite, factory bahkan tidak membuat koneksi Redis dan `ANALYSIS_EXPECTED_REPLICAS>1` tetap 503/fail-closed.

Pada graceful shutdown, manager memasang state `stopping` secara atomik sebelum menunggu worker, menghentikan pengambilan job, dan menolak enqueue baru. `/api/health/ready` langsung mengembalikan 503 dengan reason code content-free, sementara `/api/health/live` tetap 200 agar orchestrator membedakan instance yang harus dikeluarkan dari traffic dari proses yang mati. Callback Visual/OCR memeriksa sinyal shutdown sebelum render batch berikutnya, retry resolusi tinggi, atau external-vision fallback. Job aktif dikembalikan ke `queued` tanpa menghapus payload/run lineage. Bila worker belum keluar dalam timeout shutdown, status menjadi `draining`, `spip_analysis_workers{state="draining"}=1`, dan singleton leader lease terus diperbarui; manager lain tetap ditolak. `spip_analysis_worker_drain_seconds` mengukur durasi tanpa isi dokumen. Alarm worker-down mengabaikan state `stopping`/`draining` yang sah, sedangkan `SpipAnalysisWorkerDrainStuck` menjadi kritis bila drain melewati sepuluh menit selama satu menit. Drain finalizer baru melepas leader setelah worker benar-benar berhenti. Jangan memaksa menghapus row leader atau job untuk mempercepat shutdown; periksa engine/subprocess, budget, dan event job ketika alarm drain aktif.

Operator dapat membatalkan melalui `POST /api/analysis-runs/jobs/{job_id}/cancel` atau alias roadmap `POST /api/analysis-runs/{run_id}/cancel`. Gunakan run ID saat bekerja dari workspace reviewer. Job running menjadi `cancel_requested` dan berhenti kooperatif; jangan menghapus lease secara manual. Job yang sedang queued setelah recovery ditutup atomik bersama run sebelum payload dilepas. Jika worker mati setelah cancel-request, startup recovery menutup run dan shadow pair secara fail-closed. Respons cancel run yang sudah cancelled bersifat idempotent; run terminal lain mengembalikan 409.

Jalankan backend container dengan init process (`init: true` pada `docker-compose.yml`). LibreOffice/Tesseract dapat membuat helper subprocess; init process wajib mereap child yang selesai agar render Office berulang tidak menumpuk zombie process.

Set `ANALYSIS_ROLLOUT_STAGE=canary` and a bounded `ANALYSIS_CANARY_PERCENTAGE` only after `/api/analysis-runs/readiness-dashboard` reports the rollout guard ready. A requested stage that is not eligible is reduced to effective stage `development`.

## Required preflight

1. Back up the database and retain the manifest.
2. Run backend tests, synthetic regression eval, frontend build, and the expert gold validator.
3. Confirm all production rule checksums are approved through `/api/analysis-runs/rule-catalog`.
4. Gunakan **Governance V2 → Bukti Rilis** untuk memuat ulang ledger shadow, memastikan minimal 50 pasangan terminal, membuat server-derived report hanya dari partisi expert-gold **Evaluasi**, memeriksa retrieval/mapping/source/role/template/grade/abstention serta coverage/latency/cost, memastikan `partition_overlap_count=0`, dan memastikan `/api/analysis-runs/promotion-readiness` membuka tahap yang dimaksud. Partisi **Learning** tidak dihitung dalam target 50/200. Import manual melalui `/api/analysis-runs/evaluation-reports` tetap tersedia sebagai informasi, tetapi tidak mempunyai `release_authority` dan tidak boleh dipilih untuk `passed`. Keputusan passed membutuhkan report `server_derived_v2_partitioned` yang direkomputasi, checksum shadow aktif, serta dataset checksum Evaluasi yang cocok.
5. Configure the reverse proxy to authenticate users and inject trusted identity and application-role headers.
6. Confirm `pdftoppm` exists for PDF rendering and `tesseract --list-langs` includes `ind` and `eng` when local OCR is enabled.
7. Confirm metrics show no high-severity security finding, parser failure spike, stale controlled-upload reservation, atau ambiguity upload terbuka. Production-profile validator v7 harus membuktikan identity+role proxy boundary, runtime RBAC enforcement, exact Migration V30, schema/index evidence-role dan expected-template, index idempotensi, trigger ledger append-only, `integrity_check=ok`, `stale_controlled_upload_reservation_count=0`, dan `unresolved_controlled_upload_ambiguity_count=0`.
8. Keep `ALLOW_PARTIAL_PRIMARY=false`.

Untuk boundary reviewer produksi, gunakan `ops/reverse-proxy/nginx.conf` sebagai referensi. Nginx menjalankan `auth_request` ke oauth2-proxy, menghapus `X-Reviewer-Identity` dan `X-Reviewer-Roles` yang dikirim klien, lalu menggantinya dengan `X-Auth-Request-Email` dan `X-Auth-Request-Groups` dari SSO. Backend tidak boleh dipublikasikan langsung ke jaringan pengguna; hanya reverse proxy yang boleh mencapai port backend. Set `ANALYSIS_REQUIRE_REVIEWER_IDENTITY=true`, `ANALYSIS_REQUIRE_REVIEWER_ROLE=true`, `ANALYSIS_REVIEWER_IDENTITY_HEADER=X-Reviewer-Identity`, dan `ANALYSIS_REVIEWER_ROLE_HEADER=X-Reviewer-Roles`.

SSO harus menerbitkan application group yang namanya persis salah satu role berikut: `evidence_reviewer`, `domain_owner`, `evaluation_owner`, `vision_owner`, `release_owner`, atau `operations_owner`. Role `analysis_admin` hanya untuk akun break-glass yang diaudit. Header roles dipisahkan koma; missing, unknown, atau wrong-scope role ditolak 403. Scope dipisahkan agar reviewer evidence tidak dapat mengesahkan rule/release, domain owner tidak dapat melakukan controlled upload, dan release owner tidak dapat mengubah label Evaluasi. Header identitas dibatasi 2–120 karakter aman dan payload reviewer yang berbeda ditolak 403.

Kontrak `analysis-rbac-v1` mengikat 55 operasi role-secured dan 6 operasi read-only pada boundary proxy/internal. Handover validator membandingkan registry dengan OpenAPI; production-profile validator v7 memeriksa versi, jumlah klasifikasi, dan invariant seluruh mutation role-secured. Jangan menambahkan route V2 dengan menonaktifkan test coverage atau memindahkannya ke proxy-only list.

Commands:

```bash
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
PYTHONPATH=backend python evals/run_evals.py --enforce --output eval-report.json
PYTHONPATH=backend python evals/build_bootstrap_cases.py --count 50 --output bootstrap-cases.jsonl
PYTHONPATH=backend python evals/run_evals.py --cases bootstrap-cases.jsonl --enforce --output bootstrap-eval-report.json
PYTHONPATH=backend python evals/run_synthetic_pilot.py --enforce --output synthetic-pilot-report.json
PYTHONPATH=backend python evals/validate_gold.py evals/gold/spip_expert_gold.jsonl --minimum-cases 50
cd frontend && npm ci && npm run build
python scripts/analysis_db_backup.py data/evidence.db backups/evidence-$(date +%Y%m%d-%H%M%S).db \
  --payload-source data/analysis-payloads \
  --payload-destination backups/analysis-payloads-$(date +%Y%m%d-%H%M%S) \
  --manifest backups/latest.json \
  --restore-to /tmp/evidence-restore-drill.db \
  --restore-payload-to /tmp/analysis-payloads-restore-drill
python scripts/run_incident_drill.py --output outputs/rollout-readiness/incident-drill.json
```

## Monitoring

- Poll `/api/analysis-runs/metrics` for queue depth, engine status, run duration, OCR usage, verification rejection, review correction/override ratio, security findings, rolling-hour cost, token estimates, and controlled upload results. `alerting.derived` memuat budget biaya runtime yang dipakai alert engine lokal.
- Gunakan `/api/analysis-runs/readiness-dashboard` atau panel Operational Readiness pada UI untuk melihat alert, approval rule, expert-eval gate, capability vision, dan stage rollout efektif dalam satu tampilan.
- Consume `/api/analysis-runs/{id}/events/stream` for a live run trace.
- Alert when parser failures exceed 2%, any high-severity security finding appears, queue depth grows continuously, verification rejection changes abruptly, or controlled upload fails repeatedly.
- Logs and metrics must contain IDs and aggregates, not document content or API keys.
- Lifecycle worker/job/run dicatat sebagai JSON `analysis-runtime-log-v1` pada logger `uvicorn.error.spip.analysis`. Field dibatasi ke event allowlist, `job_id`, `run_id`, stage/status/reason code slug, attempt, dan counter numerik finite. Jangan menambahkan formatter/call site yang membawa nama file, isi dokumen, URL, prompt, source location, reviewer, provider response, atau exception message. Event terminal `job_completed` wajib mempunyai `run_id`; korelasikan insiden melalui ID tersebut dan ledger database, bukan dengan menyalin konten ke log.
- Prometheus dapat melakukan scrape pada `/api/analysis-runs/metrics/prometheus` ketika `ANALYSIS_PROMETHEUS_METRICS_ENABLED=true`. Endpoint hanya berisi counter/gauge agregat dan label status, tanpa nama file atau isi dokumen. `spip_analysis_human_reviews_total{outcome="approved|corrected|rejected"}` dan `spip_analysis_human_review_override_ratio` mengukur hasil serta proporsi koreksi/penolakan tanpa reviewer atau evidence text. `spip_analysis_estimated_cost_usd_total` dibandingkan dengan `spip_analysis_cost_budget_usd_per_hour`; atur budget positif melalui `ANALYSIS_COST_ALERT_USD_PER_HOUR` (default `10.0`) agar `SpipAnalysisCostAnomaly` menyala bila kenaikan rolling satu jam melewati batas. Token provider dicatat dari field `usage` untuk Responses API maupun Chat Completions. Isi `ANALYSIS_MODEL_INPUT_COST_PER_MILLION_USD` dan `ANALYSIS_MODEL_OUTPUT_COST_PER_MILLION_USD` sesuai tarif kontrak Sumopod; default `0` sengaja hanya menghitung token agar sistem tidak mengarang biaya. Gauge `spip_compute_routing_decisions{phase,selected}` serta `spip_compute_routing_average_score{phase,score}` mengukur snapshot pilihan dan skor rata-rata complexity/risk tanpa reason text atau isi bukti; skor tersebut bukan confidence. `spip_analysis_ocr_resource_events_total{event="attempt|timeout|budget_exhausted|checkpoint_batch"}`, `spip_analysis_ocr_budget_exhaustions_total{reason}`, dan `spip_analysis_ocr_document_elapsed_seconds_total` mengukur resource OCR memakai reason code content-free. `checkpoint_batch` menghitung batch Visual/OCR yang berhasil dipersistenkan, bukan jumlah halaman atau isi dokumen. `spip_analysis_job_recovery_total{event="recovered_job|lease_retry_attempt|resume_lineage"}` mencatat recovery agregat; `spip_analysis_job_recovery_active_loops` hanya menghitung job aktif yang mencapai claim ketiga dan menyalakan alert `SpipAnalysisJobRecoveryLoop`. `spip_analysis_workers{state="draining"}` dan `spip_analysis_worker_drain_seconds` memisahkan shutdown normal dari worker-down serta menyalakan alarm drain macet tanpa label dokumen. `spip_analysis_controlled_upload_reservations` menghitung reservation aktif dan `spip_analysis_controlled_upload_reservations_stale` menyalakan alert kritis bila reservation belum terminal setelah sepuluh menit. `spip_legacy_pipeline_calls_total{usage_kind,source}` mengukur pemakaian V1 dan bridge V2→legacy; setiap nilai nonnol setelah observation window stable-cycle dimulai menahan deprecation. `spip_retrieval_feedback_registry_active`, `spip_retrieval_feedback_terms`, dan `spip_retrieval_feedback_source_labels` hanya menampilkan status/hitungan learning; fingerprint maupun vocabulary dokumen tidak diekspos. `spip_evaluation_reports_total{authority="release|informational"}` menunjukkan komposisi authority tanpa mengekspos nama dataset atau checksum.

Contoh scrape config:

```yaml
scrape_configs:
  - job_name: spip-document-intelligence
    metrics_path: /api/analysis-runs/metrics/prometheus
    static_configs:
      - targets: ["spip-backend:8000"]
```

Atur alert rule Prometheus/Alertmanager untuk `spip_analysis_alerts == 1`, queue depth, parser failure, security finding, latency, override review, anomali biaya, dan worker health. Endpoint aplikasi membuktikan format/surface metrics; koneksi Prometheus/Alertmanager produksi tetap harus dibuktikan pada environment deployment.

Profile lokal yang sudah dipin dan divalidasi dengan `promtool`/`amtool`:

```bash
docker compose --profile observability up -d
```

Prometheus tersedia pada port `${PROMETHEUS_PORT:-9090}` dan Alertmanager pada `${ALERTMANAGER_PORT:-9093}`. Receiver default `local-observability` tidak memiliki email/Slack/webhook agar instalasi development tidak mengirim data keluar secara tak sengaja.

Untuk receiver webhook organisasi, buat secret file lokal berizin minimum yang hanya berisi satu URL HTTPS, lalu jalankan profile opt-in:

```bash
export ALERTMANAGER_CONFIG_FILE=/etc/alertmanager/alertmanager.webhook.yml
export ALERTMANAGER_WEBHOOK_URL_FILE=/absolute/secure/path/alertmanager_webhook_url
.venv/bin/python scripts/validate_production_profile.py \
  --webhook-url-file "$ALERTMANAGER_WEBHOOK_URL_FILE" \
  --database-path /secure/encrypted/evidence.db \
  --payload-backend filesystem \
  --payload-root /secure/encrypted/analysis-payloads \
  --storage-encryption-evidence-file /secure/runtime-secrets/storage-encryption-attestation.json \
  --storage-encryption-key-file /secure/runtime-secrets/storage-encryption-attestation.key \
  --output outputs/rollout-readiness/production-profile.json
docker compose --profile observability up -d
docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.webhook.yml
```

Validator v7 menolak identity/role enforcement yang nonaktif, proxy yang meneruskan header klien, HTTP, credential di URL, fragment, multiline secret, symlink, permission group/other, payload key non-content-addressed, file storage non-regular, attestation enkripsi yang hilang/rusak/kedaluwarsa/tidak terikat, database SQLite yang tidak private/owned/integrity-ok, schema yang belum tepat pada Migration V30, schema/index evidence-role atau expected-template yang hilang, index idempotensi atau trigger rekonsiliasi yang hilang, reservation `uploading` di atas sepuluh menit, serta `blocked_ambiguous` yang belum diselesaikan dua reviewer. Report hanya menyimpan SHA-256 URL, fingerprint binding, status pemeriksaan, schema version, hitungan reservation/ambiguity resolved/unresolved, dan ukuran agregat—bukan endpoint, token, path, signature, reviewer/role aktual, alasan pemeriksaan, atau isi evidence. Setelah change ticket/izin pengiriman tersedia, operator dapat mengirim alert synthetic dengan `amtool alert add` dan mencatat acknowledgement receiver. Jangan menjalankan delivery test ke endpoint organisasi tanpa otorisasi. Payload alert hanya berasal dari metrics/labels/annotations agregat; nama file dan isi dokumen tidak boleh menjadi label atau annotation.

## Payload storage

Default library tetap `ANALYSIS_PAYLOAD_STORAGE_BACKEND=database` agar instalasi lama kompatibel. Docker Compose mengaktifkan profile berikut secara default:

```bash
ANALYSIS_PAYLOAD_STORAGE_BACKEND=filesystem
ANALYSIS_PAYLOAD_STORAGE_PATH=/app/data/analysis-payloads
ANALYSIS_PAYLOAD_STORAGE_FSYNC=true
ANALYSIS_PAYLOAD_STORAGE_ENCRYPTION_VALIDATED=false
ANALYSIS_STORAGE_ENCRYPTION_EVIDENCE_PATH=
ANALYSIS_STORAGE_ENCRYPTION_KEY_PATH=
```

Profile filesystem menyimpan job aktif dan dokumen tertahan sebagai key `aa/bb/<sha256>.blob`. SQLite hanya menyimpan metadata backend, key, SHA-256, ukuran, dan waktu penyimpanan. Write memakai temporary file + `fsync` + atomic replace; payload memakai mode `0600`, direktori `0700`, dan read memverifikasi regular-file, permission, ukuran, key, serta checksum. Inisialisasi database membuat/mengoreksi file SQLite dan sidecar WAL/SHM menjadi `0600`, menolak symlink, non-regular file, dan owner yang berbeda. Tidak ada fallback ke BLOB ketika file eksternal rusak atau hilang. `/api/analysis-runs/config` dan `/api/analysis-runs/readiness-dashboard` menampilkan backend efektif, jumlah reference, status scan, serta requirement encrypted volume tanpa menampilkan path. Hitungan orphan diperoleh pada startup cleanup, validator produksi, atau backup/restore verification yang memang melakukan scan filesystem eksplisit.

Ketentuan produksi:

1. Mount path database dan payload pada volume terenkripsi dan private untuk UID proses backend. Application-layer encryption masih `false`; permission file saja bukan bukti enkripsi at-rest.
2. Platform/storage owner memeriksa kontrol enkripsi aktual (`luks2`, `filevault`, `cloud_kms`, `encrypted_block_volume`, `managed_database_encryption`, atau kontrol managed lain), mencatat change ticket, lalu menyediakan key HMAC acak 32–4096 byte melalui secret manager. Key dan evidence harus regular file, bukan symlink, dimiliki UID runtime, dan tanpa permission group/other. Jangan simpan keduanya di Git atau output backup aplikasi.
3. Terbitkan evidence dari namespace runtime yang sama dengan backend agar binding absolute path dan device ID benar. CLI menolak overwrite; renewal memakai file baru dan pergantian secret secara atomik. Contoh host/non-container:

```bash
.venv/bin/python scripts/issue_storage_encryption_attestation.py \
  --database-path /secure/encrypted/evidence.db \
  --payload-backend filesystem \
  --payload-root /secure/encrypted/analysis-payloads \
  --key-file /secure/runtime-secrets/storage-encryption-attestation.key \
  --output /secure/runtime-secrets/storage-encryption-attestation.json \
  --control encrypted_block_volume \
  --reviewer platform-owner@example.go.id \
  --change-ticket CHG-2026-0713 \
  --expires-days 90
```

Untuk container, jalankan issuer sebagai UID backend melalui image yang sama dan mount secret directory read-write hanya selama penerbitan:

```bash
docker compose run --rm --no-deps \
  -v /secure/runtime-secrets:/run/secrets/storage \
  backend python -m app.analysis.storage_attestation_cli \
  --database-path /app/data/evidence.db \
  --payload-backend filesystem \
  --payload-root /app/data/analysis-payloads \
  --key-file /run/secrets/storage/storage-encryption-attestation.key \
  --output /run/secrets/storage/storage-encryption-attestation.json \
  --control encrypted_block_volume \
  --reviewer platform-owner@example.go.id \
  --change-ticket CHG-2026-0713 \
  --expires-days 90
```

Validasi container dari namespace dan mount yang sama:

```bash
docker compose run --rm --no-deps \
  -v "$PWD":/workspace \
  -w /workspace \
  -v "$ALERTMANAGER_WEBHOOK_URL_FILE":/run/secrets/alertmanager_webhook_url:ro \
  -v /secure/runtime-secrets:/run/secrets/storage:ro \
  backend python scripts/validate_production_profile.py \
  --webhook-url-file /run/secrets/alertmanager_webhook_url \
  --database-path /app/data/evidence.db \
  --payload-backend filesystem \
  --payload-root /app/data/analysis-payloads \
  --storage-encryption-evidence-file /run/secrets/storage/storage-encryption-attestation.json \
  --storage-encryption-key-file /run/secrets/storage/storage-encryption-attestation.key \
  --output /workspace/outputs/rollout-readiness/production-profile.json
```

4. Mount secret directory read-only pada backend dan isi `ANALYSIS_PAYLOAD_STORAGE_ENCRYPTION_VALIDATED=true`, `ANALYSIS_STORAGE_ENCRYPTION_EVIDENCE_PATH`, serta `ANALYSIS_STORAGE_ENCRYPTION_KEY_PATH` dengan path **di dalam runtime**. Flag tanpa kedua file tersebut tetap ditolak.
5. Jalankan production-profile validator v7 sebelum start, setelah perubahan SSO/group mapping, restore/move storage, perubahan mount/backend/path, migration, rekonsiliasi upload, dan sebelum expiry. Pastikan pemeriksaan RBAC/database/schema/reservation/unresolved ambiguity juga lulus. Untuk Docker, jalankan validator melalui `docker compose run` dengan repository di-mount pada `/workspace`, secret mount yang sama, serta path runtime `/app/data/...`; ini memastikan pemeriksaan device/path dilakukan dari namespace yang sama.
6. Pantau `spip_storage_encryption_attestation_valid`, `spip_storage_encryption_validation_claimed`, `spip_storage_encryption_attestation_failed_checks`, dan `spip_storage_encryption_attestation_seconds_until_expiry`. Alert `SpipStorageEncryptionAttestationInvalid` aktif bila deployment mengklaim validasi tetapi bukti tidak efektif; `SpipStorageEncryptionAttestationExpiring` memberi warning 14 hari sebelum expiry agar evidence dapat dirotasi tanpa membuka jeda fail-closed.
7. Jangan berbagi direktori payload dengan aplikasi lain atau mengeksposnya melalui static server. Jangan menghapus file manual. TTL purge hanya menghapus content-addressed file setelah tidak ada document/job reference aktif; startup cleanup menghapus orphan.
8. Jika checksum, permission, symlink, file, signature, masa berlaku, atau binding berubah, pertahankan fail-closed, restore pasangan database+payload dari backup yang sama, atau unggah ulang dokumen. Perpindahan ke volume baru selalu memerlukan attestation baru.

## Operasi Review Visual dan OCR Rescue

Reviewer nonteknis memakai halaman **Review Visual**, bukan endpoint secara manual. Preview raster harus tetap dilayani dari endpoint aplikasi agar checksum gambar, batas member Office, `Content-Type`, CSP, dan header `nosniff` diperiksa server.

1. Jalankan analysis lokal sampai visual-pending atau `ocr_required` dengan kandidat OCR rendah muncul.
2. Reviewer membandingkan preview dengan teks OCR. Untuk `review_kind=ocr_rescue`, reviewer menyetujui kandidat atau memberikan transkripsi koreksi; kandidat mesin tidak boleh dipromosikan otomatis.
3. Keputusan bersifat append-only. Koreksi dibuat sebagai event baru; jangan mengubah tabel `visual_review_decisions` secara langsung.
4. Setelah semua unit satu run final, reviewer menekan **Buat Run Turunan**. Job baru memakai `resume_from_run_id`, snapshot checksum, dan artefak yang masih valid.
5. Pastikan run turunan menyimpan `visual_review_checksum`, lineage run sumber, provenance `visual_review` atau `ocr_rescue`, dan status coverage yang diharapkan. OCR Rescue final harus menghasilkan `human_ocr_rescue_transcription_v1`, sedangkan run sumber harus tetap tidak berubah.

Ketika identity+role enforcement aktif, reverse proxy/SSO wajib mengirim kedua trusted header. Identitas dari payload hanya boleh dipakai pada development yang secara eksplisit mengizinkannya. Request tanpa identity ditolak 401; role hilang/salah scope ditolak 403; konfigurasi role tanpa identity requirement ditolak 503; payload yang tidak sama dengan identitas terautentikasi ditolak 403. Payload sumber harus belum dipurge agar preview dan derived re-verification dapat berjalan. Preview halaman PDF harus dirender dari locator halaman dan DPI yang tercatat. Jika checksum teks/gambar/kandidat OCR stale, minta reviewer membuka unit terbaru dan membuat keputusan baru; jangan melewati pemeriksaan checksum.

Panduan klik demi klik tersedia di `docs/VISUAL_REVIEW_USER_GUIDE.md`.

## Pemulihan checkpoint Visual/OCR

Kebijakan checkpoint efektif adalah `unit-checkpoint-v2`. Engine menyimpan snapshot durable setelah setiap batch render→OCR, bukan menunggu seluruh dokumen selesai. Hanya unit `processed` atau `partial` yang dicatat sebagai hasil reusable; kandidat rendah, timeout, budget exhaustion, dan unit `ocr_required` tetap harus dicoba ulang atau masuk review manusia.

1. Periksa `GET /api/analysis-runs/{id}/checkpoints`. Stage `visual_ocr_manifest` mengikat snapshot seluruh unit pada batch terakhir, `visual_ocr_batch` menunjukkan unit sukses yang reusable, sedangkan `unit_preparation` menunjukkan tahap persiapan unit dan template lengkap.
2. Setelah worker/host mati atau lease kedaluwarsa, biarkan lease recovery menjadwalkan ulang job yang sama. Transisi atomik menandai run lama `failed`, menyimpan `resume_from_run_id`, dan mengosongkan `run_id` job sebelum run retry dibuat; payload tetap tersedia. Tombol retry manual membuat job baru dengan lineage yang sama. Jangan mengubah row job/checkpoint secara manual.
3. Resume hanya menerima manifest lengkap dengan himpunan key dan checksum kanonis tipe, urutan, heading, status, source location, teks, warning, dan metadata yang masih cocok. Checkpoint lama dengan versi konfigurasi berbeda, unit ditambah/dihapus, atau data berubah ditolak dan tahap tersebut dihitung ulang.
4. Unit sukses yang lolos validasi dilewati; unit `ocr_required` atau yang belum mempunyai checkpoint dijalankan kembali. Event `visual_ocr_partial_resume` dan `visual_ocr_batch_checkpoint` menyediakan jejak content-free.
5. Bila penyimpanan checkpoint gagal, run harus gagal dan lease recovery membuat retry baru. Jangan menurunkan error menjadi warning karena itu dapat memberikan kesan progres sudah durable padahal belum.

Field `checkpointing` pada `/api/analysis-runs/config` harus menunjukkan `policy_version=unit-checkpoint-v2`, `visual_ocr_batch_durable=true`, dan `partial_resume_checksum_bound=true`. Metric `spip_analysis_ocr_resource_events_total{event="checkpoint_batch"}` dapat dibandingkan dengan timeout/budget exhaustion untuk mendeteksi dokumen panjang yang berulang tanpa progres durable.

Regression crash recovery membuat PDF dua halaman, menyimpan hanya halaman pertama sebagai checkpoint sukses, lalu mensimulasikan proses mati dua kali—termasuk tepat setelah transisi recovery dan sebelum run retry dibuat. Claim ketiga tetap memakai payload lama, menghasilkan run baru, memanggil OCR hanya untuk halaman kedua, mempertahankan halaman pertama, dan menutup active-loop gauge setelah job terminal. Jika `spip_analysis_job_recovery_active_loops` tetap nonnol, periksa crash worker dan resource budget; jangan menghapus lineage atau menaikkan worker count sebelum penyebabnya diketahui.

Regression graceful shutdown memakai PDF empat halaman dan OCR batch dua unit. Shutdown saat batch pertama masih berjalan mempertahankan leader, menolak manager kedua, menyimpan halaman 1–2, lalu mengembalikan job ke antrean. Setelah drain selesai, manager berikutnya menerima claim kedua dan OCR hanya memproses halaman 3–4. Regression fencing terpisah membuktikan attempt lama ditolak pada seluruh operasi mutasi job dan tidak dapat menghapus payload claim terbaru.

## Pemulihan controlled upload

`POST /api/analysis-runs/{id}/approve-upload` adalah endpoint roadmap; `controlled-upload` tetap tersedia sebagai alias kompatibel. Keduanya memakai satu idempotency key Migration V27 untuk pasangan run/mapping dan menulis reservation `uploading` dalam transaksi atomik sebelum memanggil legacy WebDAV bridge.

1. Jika respons sukses hilang di jaringan, ulangi endpoint dengan run/mapping yang sama. Action `uploaded_primary` dikembalikan sebagai `idempotent=true` tanpa memanggil WebDAV lagi.
2. Jika permintaan kedua datang ketika action masih `uploading`, server mengembalikan 409. Jangan menjalankan upload legacy atau WebDAV manual secara paralel.
3. Jika bridge mengembalikan galat setelah legacy review dibuat, action menjadi `blocked_ambiguous`. Galat dapat terjadi sebelum atau sesudah side effect eksternal, sehingga retry otomatis sengaja ditolak.
4. Saat `SpipAnalysisControlledUploadReservationStale` aktif atau action tetap `uploading` lebih dari sepuluh menit, cocokkan `legacy_review_id`, tujuan pada action audit, nama/hash file pada ledger legacy, dan isi folder WebDAV. Jangan menghapus row reservation.
5. Untuk action terminal `blocked_ambiguous`, buka Upload Pintar, pilih hasil pemeriksaan, isi alasan, centang attestation, lalu kirim. API ekuivalennya adalah `POST /api/analysis-runs/{run_id}/controlled-upload-actions/{action_id}/reconciliation` dengan `expected_latest_event_id` dari snapshot terbaru.
6. Reviewer kedua harus login dengan identitas berbeda, memeriksa folder tujuan dan legacy review secara independen, memuat ulang snapshot, lalu mengirim outcome yang sama. Satu reviewer tidak pernah dihitung dua kali; update stale ditolak 409.
7. `needs_investigation`, outcome yang berlawanan, atau baru satu reviewer tetap fail-closed. Setelah dua outcome terminal cocok, ledger menjadi final dan tidak dapat diubah. Action asli tetap `blocked_ambiguous` untuk menjaga audit.
8. `confirmed_uploaded` menyelaraskan status run menjadi `uploaded`; `confirmed_not_uploaded` tidak membuka retry, reset, atau side effect baru. Jika upload tetap dibutuhkan, perlakukan sebagai perubahan operasional terpisah dengan otorisasi baru—jangan mengubah ledger lama.

Metric dan event tidak membawa isi dokumen. Event sukses adalah `controlled_upload_completed`; hasil tak pasti adalah `controlled_upload_ambiguous`; keputusan dua-reviewer menghasilkan `controlled_upload_reconciled`. Alert `SpipAnalysisControlledUploadAmbiguityUnresolved` tetap kritis sampai dua keputusan cocok tersedia. Status `blocked_ambiguous` merupakan hard gate, bukan kegagalan sementara.

## Backup and restore drill

`scripts/analysis_db_backup.py` memakai SQLite online backup, `PRAGMA integrity_check`, dan manifest payload content-addressed. Database yang mempunyai reference filesystem sengaja ditolak bila command tidak menyertakan source/destination payload. Untuk drill restore:

1. Stop workers or set `ANALYSIS_PIPELINE_V2_ENABLED=false`.
2. Back up database dan payload root sebagai satu set; jangan mencampur timestamp/snapshot berbeda.
3. Pastikan `payload_storage.manifest_sha256` tercatat, `orphan_count=0`, dan `PRAGMA integrity_check=ok`.
4. Restore database dan payload ke path baru; tool menolak overwrite target yang sudah ada.
5. Start aplikasi dengan `DATABASE_PATH`, `ANALYSIS_PAYLOAD_STORAGE_BACKEND=filesystem`, dan `ANALYSIS_PAYLOAD_STORAGE_PATH` menunjuk pasangan restore.
6. Periksa `/api/health/live` dan `/api/health/ready`, migration V1–V30, schema/index evidence-role dan expected-template, readiness payload storage, worker-leader lease, shadow ledger, legacy usage telemetry, controlled-upload reservation/reconciliation ledger, run counts, preview historical run, dan satu retry terkontrol. Endpoint `/api/health` lama tetap tersedia untuk kompatibilitas, tetapi tidak boleh dipakai sebagai traffic-readiness probe.
7. Sebelum menetapkan tanggal deprecation, buka **Governance V2 → Bukti Rilis** dan pastikan dua stable cycle ber-authority/recompute V26 tercatat setelah telemetry V23 aktif, observation coverage valid, serta **Panggilan V1 sejak observasi = 0**. Jangan menghapus V1 bila controlled-upload bridge masih menambah telemetry.
8. Re-enable workers hanya setelah checksum payload dan pemeriksaan tersebut lulus.

## Provider or OCR outage

- Disable `VISION_ANALYSIS_ENABLED`, `ANALYSIS_STRUCTURED_MODEL_ENABLED`, `ANALYSIS_ADVANCED_RAG_DEEPSEEK_ENABLED`, `ANALYSIS_MAPPING_REASONING_ENABLED`, or `ANALYSIS_MODEL_VERIFIER_ENABLED` as appropriate. Advanced RAG lokal (BM25, cosine-IDF, semantic vector, RRF) tetap berjalan ketika DeepSeek dimatikan. DeepSeek `deepseek-v4-pro` hanya melakukan query expansion dan constrained reranking ketika kandidat ambigu serta job mengizinkan AI eksternal. Kegagalan provider kembali ke retrieval lokal dan menahan kandidat meragukan sebagai `needs_review`; tidak ada fallback yang mempromosikan mapping atau Grade.
- Threshold `ANALYSIS_ROUTING_STRUCTURED_MIN_COMPLEXITY`, `ANALYSIS_ROUTING_MAPPING_MARGIN`, dan `ANALYSIS_ROUTING_VERIFIER_MIN_RISK` harus tetap pada default 0,25/0,08/0,45 sampai holdout Evaluasi berlabel ahli membuktikan perubahan lebih baik. Jangan menurunkan threshold untuk mengejar jumlah call model atau menganggap routing score sebagai confidence.
- OCR lokal dikendalikan terpisah oleh `ANALYSIS_LOCAL_OCR_ENABLED`, `ANALYSIS_LOCAL_OCR_PROVIDER`, `ANALYSIS_LOCAL_OCR_LANGUAGES`, `ANALYSIS_LOCAL_OCR_MIN_CONFIDENCE`, `ANALYSIS_LOCAL_OCR_TIMEOUT_SECONDS`, `ANALYSIS_LOCAL_OCR_UNIT_BUDGET_SECONDS`, `ANALYSIS_LOCAL_OCR_DOCUMENT_BUDGET_SECONDS`, `ANALYSIS_LOCAL_OCR_MAX_ATTEMPTS_PER_UNIT`, `ANALYSIS_LOCAL_OCR_RENDER_BATCH_UNITS`, `ANALYSIS_LOCAL_OCR_MAX_UNITS`, `ANALYSIS_LOCAL_OCR_TESSERACT_PSM_MODES`, `ANALYSIS_LOCAL_OCR_PREPROCESSING_ENABLED`, `ANALYSIS_LOCAL_OCR_MAX_IMAGE_PIXELS`, `ANALYSIS_LOCAL_OCR_MAX_TILES`, `ANALYSIS_PDF_RENDER_DPI`, `ANALYSIS_PDF_RETRY_RENDER_DPI`, `ANALYSIS_PDF_RETRY_MAX_UNITS`, `ANALYSIS_OFFICE_RENDERING_ENABLED`, dan `ANALYSIS_OFFICE_RENDER_MAX_PAGES`. Default safety envelope adalah 30 detik per subprocess, 180 detik dan 24 attempt per unit, 900 detik kumulatif per dokumen, serta render→OCR setiap empat unit. Deadline dokumen mencakup rendering, base OCR, preprocessing/tile, dan retry. Exhaustion tidak mengubah confidence atau status menjadi sukses: unit tetap `ocr_required`, reason code tersimpan, dan primary upload diblokir. Default PSM `6,3,11` mencoba mode 3/11 hanya bila hasil mode 6 masih lemah. Raster di atas 16 juta pixel dipotong menjadi maksimal 16 tile; bounding box tile dinormalisasi kembali ke halaman penuh dan kegagalan tile tetap fail-closed. PDF/halaman Office yang tetap lemah dapat dirender ulang pada DPI lebih tinggi dengan ambang confidence sama. Bila halaman Office justru membutuhkan lebih dari 16 tile, engine merender ulang pada DPI terendah yang masih ≥72 dan muat dalam budget; DPI aktual dan checksum raster disimpan agar preview review tetap persis. Halaman biasa tidak diturunkan resolusinya. Full audit menjadwalkan maksimal 24 halaman/sheet Office; unit di atas budget tetap `pending`, bukan dihilangkan.

Kalibrasi outlier `Kepdirjen PDP 465 Tahun 2025 Tentang Peta Risiko 2025-2029.pdf` (68 halaman, SHA-256 `cb40ada9…11b0`) dilakukan lokal di container tanpa jaringan. Probe 30 detik memproses 13 halaman dan berhenti 29,923 detik; probe 90 detik memproses 32 halaman/43 attempt/1 timeout dan berhenti 90,058 detik; probe 240 detik menyelesaikan base attempt seluruh halaman, menerima 51 halaman, mempertahankan 17 kandidat rendah, 112 attempt/1 timeout, dan berhenti 240,069 detik. Semua tahap tetap `partial`, `primary_blocked=true`, dan tidak memakai AI eksternal. Gunakan `scripts/probe_ocr_resource_budget.py` untuk reproduksi satu dokumen. Jangan menaikkan hard ceiling 900 detik sebelum telemetry menunjukkan budget exhaustion berulang dan kapasitas worker telah disetujui.
- Full-document Office membutuhkan LibreOffice Writer/Calc/Impress headless dan `pdftoppm`; status efektif tersedia pada `office_renderer` di `/api/analysis-runs/config` dan `/api/analysis-runs/readiness-dashboard` (`office_slide_renderer` dipertahankan sebagai alias kompatibilitas). DOCX dirender per halaman, XLSX memakai `SinglePageSheets=true` untuk satu snapshot per sheet visible, dan PPTX hanya meroute slide visual. Renderer memakai conversion tunggal per run, direktori/profile sementara, environment minimal tanpa API key, batas entry/ukuran/rasio/waktu, serta salinan OOXML yang menghapus relasi eksternal, ActiveX, embedded object, dan VBA. Jika converter/filter tidak tersedia atau gagal, `office_visual_document`/`slide_visual` tetap memblokir coverage.
- Jangan menyamakan OCR teks dengan pemahaman visual. Standalone photo, embedded image, `office_visual_page`, dan `slide_visual` tetap `partial` dengan `visual_semantics_status=pending_review_or_vision` walaupun teksnya terbaca; Fact Extraction dan structured model tidak boleh menerima teks tersebut. Hanya external vision yang sudah lolos governance atau human review yang dapat menutup gap tersebut.
- Pada image backend, Tesseract `ind+eng` adalah jalur produksi lokal. Periksa status efektif melalui field `ocr` pada `/api/analysis-runs/readiness-dashboard`; jangan hanya memeriksa keberadaan binary.
- Native parsing and deterministic engines continue; affected units/results remain partial and primary upload stays blocked.
- Do not bypass OCR or verification warnings. Ask the reviewer to retry after recovery or use documented manual handling outside automated primary upload.
- `VISION_ANALYSIS_ENABLED=true` saja tidak cukup untuk fallback eksternal. Runtime juga membutuhkan `ANALYSIS_VISION_PROVIDER_VALIDATED=true`, API key, renderer, synthetic probe yang lulus, capability approval aktif, dan consent data `restricted` yang belum kedaluwarsa pada **Governance V2**. Mencabut consent langsung menutup provider eksternal untuk run baru tanpa mematikan OCR lokal. Probe live Sumopod `deepseek-v4-pro` per 12 Juli 2026 gagal kontrak `unit_key`, sehingga model tetap text/reasoning provider dan fallback vision harus nonaktif.

## Rollback

Immediate rollback:

```text
ANALYSIS_PIPELINE_V2_SHADOW=false
ANALYSIS_PIPELINE_V2_ENABLED=false
LEGACY_SMART_UPLOAD_ENABLED=true
SMART_UPLOAD_ALLOW_REAL_UPLOAD=false
```

Trigger rollback for overgrade above 2%, source-location accuracy below 95%, parser success below 98%, a high-severity security issue, wrong upload destination, uncontrolled cost/latency, or data integrity failure. Migrations are forward-only; restore a verified snapshot when data rollback is required.

`scripts/run_incident_drill.py` menyediakan rehearsal lokal yang dapat diulang. Drill membuat database sementara, memaksa outage seluruh provider OCR/AI pada gambar synthetic, membuktikan coverage tetap partial dan primary upload blocked, menjalankan online backup/restore beserta critical-table counts, memeriksa konfigurasi rollback ke legacy, lalu memastikan canary dengan gate tertutup turun ke `development`. Output JSON mempunyai SHA-256 report dan tidak mengandung isi dokumen atau memanggil AI eksternal. Drill ini membuktikan mekanisme teknis; product owner tetap harus mencatat rehearsal rollout nyata pada release ledger.

## Release gates

- Shadow: at least 50 expert-labelled cases, Recall@5 at least 95%, source accuracy at least 95%, overgrade at most 2%.
- Canary: domain owner approves all active rule checksums and product owner signs the comparison report.
- General release: at least 200 expert-labelled cases and two release cycles without critical regression.
- Deprecate V1 only after audit migration is validated and rollback has been rehearsed.

Record sign-off outside this repository with owner, date, dataset checksum, eval artifact, pipeline/rule/model versions, and decision. Until those records exist, V2 remains development/shadow only.
