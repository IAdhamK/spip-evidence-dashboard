# Referensi API Document Intelligence V2

Status dokumen: kontrak teknis pengembangan/shadow, 15 Juli 2026. Spesifikasi request dan response yang paling otoritatif tetap `GET /openapi.json`; dokumen ini menjelaskan batas operasi dan memastikan seluruh route V2 mempunyai pintu masuk handover yang dapat ditelusuri.

Kontrak authorization `analysis-rbac-v1` mengklasifikasikan seluruh 62 operasi aktif: 55 operasi role-secured dan 7 operasi read-only pada authenticated-proxy/internal-network boundary. Regression dan handover validator menolak route baru yang belum diklasifikasikan, policy stale, overlap klasifikasi, atau mutation yang hanya mengandalkan network boundary.

## Konvensi dan batas keamanan

- Prefix utama adalah `/api/analysis-runs`; package synthesis memakai `/api/analysis-packages`.
- Intake asinkron mengembalikan HTTP `202`; pembuatan keputusan/ledger umumnya `201`; baca sukses `200`.
- Kesalahan validasi memakai `422`, resource hilang `404`, konflik/stale state `409`, identitas hilang `401`, dan identitas payload yang tidak cocok `403`.
- `run_id`, `job_id`, `batch_id`, `package_id`, dan `action_id` adalah ID teknis. Klien tidak boleh menebak urutannya atau menganggapnya sebagai bukti otorisasi.
- Produksi wajib mengambil identitas dan role reviewer dari trusted header yang sudah diganti reverse proxy/SSO. Field `reviewer_id` pada body hanya pembanding/audit dan tidak boleh menjadi sumber autentikasi.
- Saat `ANALYSIS_REQUIRE_REVIEWER_ROLE=true`, backend menerapkan least-privilege scope: `evidence_reviewer`, `domain_owner`, `evaluation_owner`, `vision_owner`, `release_owner`, atau `operations_owner`. `analysis_admin` adalah role lintas-scope yang harus dibatasi sebagai break-glass. Missing/unknown/wrong-scope role ditolak 403; konfigurasi role tanpa trusted identity ditolak 503.
- Semua approval, rekonsiliasi, governance, dan release evidence bersifat fail-closed. `attested=true` berarti pengguna menyatakan telah melakukan pemeriksaan yang disebutkan UI; itu bukan pengganti kontrol identitas.
- Checksum SHA-256 mengikat rule, dokumen, preview, label, report, atau keputusan ke versi yang diperiksa. Konflik checksum harus ditinjau ulang, bukan dipaksa.
- Real upload tetap ditahan oleh feature flag, coverage, rule approval, verifier, human approval, idempotency reservation, dan duplicate/security check. Tidak ada endpoint force-upload.
- Stream event memakai Server-Sent Events. Klien harus dapat kembali ke polling job/run bila koneksi stream putus dan tidak boleh menganggap event yang hilang sebagai sukses.
- Isi dokumen, token, URL bercredential, atau prompt tidak boleh dimasukkan ke label metrics maupun structured runtime log.

## Intake, job, dan batch

Scope produksi: `evidence_reviewer` membuat intake; pembacaan/pembatalan job atau run menerima `evidence_reviewer` dan `operations_owner`.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/analysis-runs/config` | Feature flags, capability, checkpoint, queue, dan batas efektif yang aman ditampilkan. |
| POST | `/api/analysis-runs` | Membuat job analisis satu dokumen. |
| POST | `/api/analysis-runs/batch-intakes` | Memvalidasi ZIP dan membuat batch job per dokumen. |
| GET | `/api/analysis-runs/batch-intakes/recent` | Daftar batch terbaru untuk pemulihan UI. |
| GET | `/api/analysis-runs/batch-intakes/{batch_id}` | Status dan anggota satu batch. |
| POST | `/api/analysis-runs/batch-intakes/{batch_id}/cancel` | Meminta pembatalan batch dan job yang masih dapat dibatalkan. |
| GET | `/api/analysis-runs/jobs/{job_id}` | Status antrean, attempt, run terikat, dan terminal result. |
| POST | `/api/analysis-runs/jobs/{job_id}/cancel` | Membatalkan queued job atau meminta cancellation job berjalan. |

`POST /api/analysis-runs` menerima upload multipart. Batas format, ukuran, jumlah anggota ZIP, mode model, dan storage ditentukan konfigurasi server; klien harus membaca `/config`, bukan menyalin konstanta backend.

## Run dan artefak engine

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/analysis-runs/{run_id}` | Ringkasan lifecycle, coverage, gate, dan lineage run. |
| GET | `/api/analysis-runs/{run_id}/events` | Ledger event run. |
| GET | `/api/analysis-runs/{run_id}/events/stream` | SSE progress yang merefleksikan event backend. |
| GET | `/api/analysis-runs/{run_id}/units` | Unit dokumen dan status parser/OCR. |
| GET | `/api/analysis-runs/{run_id}/checkpoints` | Checkpoint per-stage/per-batch untuk audit resume. |
| GET | `/api/analysis-runs/{run_id}/document-map` | Struktur dokumen dan coverage ledger. |
| GET | `/api/analysis-runs/{run_id}/facts` | Fakta terstruktur, provenance, dan evidence role. |
| GET | `/api/analysis-runs/{run_id}/mappings` | Kandidat parameter dan assessment grade. |
| POST | `/api/analysis-runs/{run_id}/expand-candidates` | Menambah kandidat retrieval tanpa melewati rule/verifier. |
| POST | `/api/analysis-runs/{run_id}/reverify` | Mengulang verification setelah approval rule berubah. |
| POST | `/api/analysis-runs/{run_id}/retry` | Membuat job turunan dengan payload/checkpoint yang masih valid. |
| POST | `/api/analysis-runs/{run_id}/cancel` | Membatalkan melalui run ID secara idempotent bila state mengizinkan. |

Run sumber tidak ditimpa oleh retry, OCR rescue, atau visual correction. Hubungan turunan disimpan sebagai lineage; checkpoint stale/tampered ditolak.

Response run menambahkan `document_family` secara additive. Contract memuat `family`, `family_confidence`, `evidence_role`, `grade_eligible`, `grade_status`, `grade_block_reasons`, primary/secondary parameter keys, reasons, warnings, structural features, relevant coverage, dan relationship hints. Item `mappings` memisahkan `raw_retrieval_score`, `mapping_score`, `calibrated_decision_confidence`, `confidence_components`, `decision_status`, serta family/Grade gate. Item `grade_assessments` memuat `grade_status`: `not_applicable`, `blocked`, `direction_only`, atau `supported`; `supported` hanya diterbitkan setelah rule disahkan, requirement terpenuhi, dan seluruh Independent Verification terkait berstatus `verified`. Klien tidak boleh menurunkan Grade sendiri dari rule trace ketika `candidate_grade` kosong.

`GET /api/analysis-runs/{run_id}` juga menambahkan field `workbook_evidence` secara additive. Field lama seperti `run`, `facts`, `mappings`, `grade_assessments`, dan `verification_results` tetap dipertahankan. Untuk XLSX yang sudah mencapai status analisis terminal yang dapat diatribusikan, nilai ini dihitung saat dibaca dari `document_units`, source location fakta, `supporting_fact_ids`, mapping, assessment, dan verification canonical:

```json
{
  "workbook_evidence": {
    "schema_version": "workbook-multi-evidence-v1",
    "status": "ready",
    "applicable": true,
    "is_multi_evidence": true,
    "sheet_count": 5,
    "substantive_sheet_count": 3,
    "unique_substantive_sheet_count": 3,
    "sheet_results": [
      {
        "sheet_key": "sheet:register-risiko",
        "sheet_name": "Register Risiko",
        "status": "mapped",
        "primary_eligible": true,
        "coverage_status": "complete",
        "source_fact_ids": [101, 102],
        "primary_parameter": {
          "mapping_candidate_id": 20,
          "kk_id": "KK3.1",
          "kode": "2.1",
          "detail_kode": "2.1.2",
          "mapping_score": 0.86,
          "supporting_fact_ids": [101, 102],
          "source_locations": [
            {"unit_key": "sheet-1", "sheet": "Register Risiko", "sheet_index": 1}
          ],
          "verification_status": "verified",
          "selection_status": "selected"
        },
        "secondary_parameters": [],
        "warnings": []
      }
    ],
    "workbook_summary": {
      "primary_parameter": null,
      "secondary_parameters": [],
      "parameter_sources": [],
      "detection_reasons": [],
      "warning_codes": [],
      "warnings": []
    }
  }
}
```

### Arti multi-evidence, parameter utama, dan parameter sekunder

Workbook multi-evidence adalah XLSX dengan lebih dari satu sheet substantif yang mendukung parameter berbeda, memperlihatkan tahapan evidence berbeda (misalnya register, analisis, RTP, monitoring, atau evaluasi), atau mempunyai parameter berbeda yang didukung fakta kuat pada sheet lain. Banyaknya sheet saja tidak cukup: sheet kosong, hidden, template, petunjuk, duplikat, atau tidak terbaca tidak membuat workbook otomatis menjadi multi-evidence.

`primary_parameter` pada satu `sheet_result` adalah rekomendasi parameter dengan dukungan fakta sumber paling kuat pada sheet tersebut. Nama/jenis sheet hanya membantu mengurutkan kandidat yang sudah mempunyai `supporting_fact_ids` dari sheet yang sama; nama sheet saja tidak dapat menciptakan kandidat. `secondary_parameters` berisi maksimal tiga parameter berbeda yang juga mempunyai fakta pendukung pada sheet tersebut. Primary/secondary di sini adalah urutan rekomendasi pemeriksaan, bukan keputusan final workbook, bukan keputusan reviewer, bukan Grade, dan bukan izin upload. `workbook_summary` mengagregasi rekomendasi tanpa menghilangkan `source_sheets`; parameter yang sama pada dua sheet tetap mencatat kedua sumber.

`is_multi_evidence=true` diterbitkan bila setidaknya salah satu kondisi berikut terbukti dari sheet substantif unik:

1. dua sheet mempunyai rekomendasi primary berbeda;
2. sheet lain mempunyai parameter sekunder berbeda dengan fakta sumber pada sheet itu; atau
3. sheet berbeda menunjukkan tahapan evidence berbeda.

### Penentuan source sheet dan batas authority

Source sheet ditentukan dari artefak canonical yang sudah ada. Sistem mencocokkan `fact.sources[].unit_key` ke document unit sheet; bila unit key tidak tersedia, sistem memakai kecocokan tepat `source_location.sheet` dan, jika tersedia, `sheet_index`. Mapping hanya dapat masuk ke suatu sheet jika `supporting_fact_ids` mapping beririsan dengan fakta substantif yang sumbernya cocok ke sheet tersebut. Nama sheet yang hilang menghasilkan `sheet_name=null` dan warning—sistem tidak menebaknya.

View ini derived-on-read dan tidak memerlukan tabel/kolom database baru. Pembacaan endpoint tidak menulis mapping, Grade, verification, event, engine result, human review, atau controlled-upload action baru. `primary_eligible` hanya menyatakan apakah rekomendasi parameter sheet cukup lengkap untuk ditampilkan sebagai kandidat utama; field ini bukan `primary_allowed` Grade dan bukan upload authority. `existing_grade_assessment`, bila ada, hanya salinan informasi assessment canonical existing dengan label belum resmi; view tidak membuat atau menaikkan Grade.

Untuk non-XLSX, `applicable=false` dan `status=not_applicable`, sementara field response existing tetap tersedia. Untuk XLSX yang run-nya belum selesai, `status=pending`, `sheet_results` kosong, dan hasil parsial tidak diterbitkan sebagai final. Run `failed` atau `cancelled` mengembalikan status terminal yang sama, `sheet_results` kosong, dan warning `RUN_FAILED` atau `RUN_CANCELLED`. Workbook terminal tanpa unit sheet tetap aman dengan `NO_WORKBOOK_SHEETS`. Sheet coverage `partial` selalu `needs_review`, tidak `primary_eligible`, dan tidak menjalankan fallback retrieval; sheet gagal/tidak terbaca tidak mempunyai primary.

### Keterbatasan format dan workbook campuran

- Hidden sheet tidak boleh menjadi primary evidence. Kandidat bersumber hidden sheet dapat tetap terlihat sebagai informasi pendukung agar sumber tidak hilang, tetapi wajib diperiksa manusia.
- Parser menerbitkan `XLSX_FORMULA_REF_ERROR` bila formula atau cached value memuat `#REF!`. Detektor ini tidak menghitung ulang formula dan tidak menjamin formula lain benar.
- Merged cell dipertahankan pada metadata parser, tetapi atribusi multi-evidence saat ini berada pada tingkat sheet dan source cell/unit; sistem belum menafsirkan seluruh merged range sebagai satu semantic region baru.
- Workbook dengan lebih dari satu periode atau organisasi menghasilkan `MIXED_PERIODS_ACROSS_SHEETS` atau `MIXED_ORGANIZATIONS_ACROSS_SHEETS`. Sistem tidak memilih periode/unit final secara otomatis dan tidak menganggap seluruh sheet selalu milik satu unit kerja.
- Sheet duplikat tidak menambah alasan multi-evidence. Kecocokan yang terlalu dekat, coverage parsial, source sheet hilang, atau konteks campuran tetap fail-closed dan membutuhkan review.

### Alur reviewer

Panel **Workbook Multi-Evidence** pada Upload Pintar menampilkan jumlah sheet, sheet substantif, parameter, warning, dan source sheet. Reviewer membuka setiap card sheet, memeriksa parameter utama/sekunder serta fakta sumber melalui **Detail Pemeriksaan**, lalu menggunakan **Review Terpandu** untuk memilih parameter resmi, Grade yang diizinkan rule, evidence role, fakta sumber, dan alasan. Sheet ambigu tidak boleh disahkan sekaligus sebagai satu workbook. Reviewer memilih **Belum yakin** bila source, periode, organisasi, atau efektivitas penanganan belum dapat dibuktikan.

Sheet yang berstatus `needs_sheet_retrieval` menjalankan fallback `sheet-retrieval-v1` secara terisolasi. Query deterministik hanya memakai nama/heading sheet, fakta dan source location milik sheet tersebut, document role, periode, serta organisasi yang telah terdeteksi—bukan seluruh isi workbook. Fallback memakai `ParameterRetrievalEngine` dan `SPIPMappingEngine` existing dengan maksimal 10 kandidat; `supporting_fact_ids` wajib berasal dari sheet yang sama. Advanced RAG lokal mengikuti feature flag existing. Catalog search atau query expansion provider hanya dapat membantu recall/rerank ketika hasil deterministik masih lemah atau ambigu, run mengizinkan external AI, dan konfigurasi provider existing aktif. Query expansion tidak dipanggil ketika hasil lokal sudah cukup.

Rekomendasi fallback memiliki `provenance.origin=sheet_retrieval`, `mapping_candidate_id=null`, `recommendation_status=derived_recommendation`, `verification_status=not_run`, `primary_allowed=false`, `grade_eligible=false`, dan `grade_status=not_assessed`. Query hanya direpresentasikan oleh `query_sha256`; isi query tidak dipersistenkan. Rekomendasi yang kuat memakai status sheet `sheet_retrieval_recommended`, sedangkan hasil lemah/ambigu tetap `needs_review`. Kegagalan provider tetap fail-closed. Rekomendasi ini tidak ditulis ke `mapping_candidates`, bukan keputusan manusia, bukan Grade resmi, dan bukan controlled-upload authority.

## Human review

Scope produksi: `evidence_reviewer`.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/analysis-runs/{run_id}/review-decisions` | Riwayat keputusan mapping. |
| POST | `/api/analysis-runs/{run_id}/review-decisions` | Approve, correct, atau reject dengan alasan. |
| GET | `/api/analysis-runs/guided-review/parameters` | Indeks parameter resmi untuk reviewer. |
| GET | `/api/analysis-runs/guided-review/queue` | Antrean review nonteknis. |
| GET | `/api/analysis-runs/guided-review/export` | Ekspor metadata review yang aman untuk handoff. |
| GET | `/api/analysis-runs/guided-review/{run_id}` | Detail kandidat, fakta sumber, dan keputusan aktif. |
| GET | `/api/analysis-runs/guided-review/{run_id}/document` | Dokumen yang berhak dilihat reviewer. |
| POST | `/api/analysis-runs/guided-review/{run_id}` | Menyimpan label kandidat, mapping, source, role, template status, dan alasan. |
| GET | `/api/analysis-runs/visual-review/queue` | Unit visual/OCR yang masih ambigu. |
| GET | `/api/analysis-runs/visual-review/{run_id}/{unit_key}` | Detail unit dan history keputusan visual. |
| GET | `/api/analysis-runs/visual-review/{run_id}/{unit_key}/preview` | Preview exact source image dengan checksum. |
| POST | `/api/analysis-runs/visual-review/{run_id}/{unit_key}/decision` | Keputusan append-only OCR/semantic region. |
| POST | `/api/analysis-runs/visual-review/{run_id}/apply` | Membuat run turunan dari satu set keputusan ber-checksum. |

Review mapping positif wajib menentukan evidence role (`primary`, `supporting`, `context`, atau `contradictory`). Jawaban pasti juga wajib menentukan `substantive` atau `template_only`; `not_assessed` tidak memenuhi gate expert gold.

## Rule, governance, dan vision

Scope produksi: rule memakai `domain_owner`; expert dataset memakai `domain_owner` atau `evaluation_owner`; capability/consent vision memakai `vision_owner`.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/analysis-runs/parameter-catalog` | Katalog lengkap KK, subunsur, parameter, dan Grade yang tersedia untuk koreksi hasil pemeriksaan. Mendukung filter `kk_id`, `kode`, `offset`, dan `limit`; tidak menetapkan Grade secara otomatis. |
| GET | `/api/analysis-runs/rule-catalog` | Seluruh compiled rule, checksum, dan approval state. |
| POST | `/api/analysis-runs/rule-approvals` | Endpoint kompatibel untuk satu keputusan rule. |
| GET | `/api/analysis-runs/governance/rules` | Ringkasan rule per parameter untuk UI governance. |
| GET | `/api/analysis-runs/governance/rules/history` | Ledger keputusan rule. |
| POST | `/api/analysis-runs/governance/rules/decisions` | Keputusan atomik maksimal 25 rule ber-checksum. |
| GET | `/api/analysis-runs/governance/expert-dataset` | Kandidat expert dataset dan partisinya. |
| POST | `/api/analysis-runs/governance/expert-dataset/{run_id}/decision` | Approve/return oleh reviewer kedua ke Evaluasi atau Learning. |
| GET | `/api/analysis-runs/governance/vision` | Capability, consent, dan approval vision. |
| POST | `/api/analysis-runs/governance/vision/probe` | Probe synthetic tanpa dokumen pengguna. |
| POST | `/api/analysis-runs/governance/vision/decisions` | Approval/revocation capability atau external data processing. |

Approval rule melekat pada checksum. Perubahan criterion atau kontrak compiler menghasilkan checksum baru dan approval lama tidak boleh dipakai.

## Evaluasi, rollout, dan operasi

Scope produksi: evaluation report memakai `evaluation_owner`; shadow/release evidence memakai `release_owner`. Metrics Prometheus tetap diamankan oleh boundary jaringan internal dan tidak membawa isi dokumen.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/analysis-runs/metrics` | Snapshot metrics content-free untuk dashboard. |
| GET | `/api/analysis-runs/metrics/prometheus` | Exposition format Prometheus. |
| GET | `/api/analysis-runs/shadow-comparison` | Pasangan V1/V2 dan status perbandingan. |
| POST | `/api/analysis-runs/shadow-comparisons/refresh` | Menghitung ulang pasangan terminal. |
| GET | `/api/analysis-runs/shadow-comparison-report` | Agregat content-free dan checksum report. |
| GET | `/api/analysis-runs/evaluation-reports` | Riwayat report expert-gold/informational. |
| POST | `/api/analysis-runs/evaluation-reports` | Import report manual; tidak memberi release authority. |
| POST | `/api/analysis-runs/evaluation-reports/from-expert-gold` | Report otoritatif yang dihitung server dari holdout Evaluasi aktif. |
| GET | `/api/analysis-runs/release-evidence` | Ledger release event. |
| POST | `/api/analysis-runs/release-evidence` | Planned/started/passed/failed/rolled_back dengan attestation. |
| GET | `/api/analysis-runs/promotion-readiness` | Hard gate development, shadow, pilot, canary, dan general. |
| GET | `/api/analysis-runs/readiness-dashboard` | Tampilan gabungan kualitas, governance, security, storage, queue, dan rollout. |

Report manual selalu informational. Keputusan `passed` harus mengacu pada report `server_derived_v2_partitioned` yang dihitung ulang dan cocok checksum pada saat keputusan.

## Controlled upload dan rekonsiliasi

Scope produksi: controlled upload memakai `release_owner`; rekonsiliasi hasil ambigu memakai `operations_owner` dan tetap membutuhkan dua reviewer berbeda pada ledger.

| Method | Path | Fungsi |
|---|---|---|
| POST | `/api/analysis-runs/{run_id}/controlled-upload` | Reservation idempotent lalu legacy WebDAV bridge bila semua gate lulus. |
| POST | `/api/analysis-runs/{run_id}/approve-upload` | Alias roadmap dengan idempotency key yang sama. |
| POST | `/api/analysis-runs/{run_id}/controlled-upload-actions/{action_id}/reconciliation` | Ledger dua-reviewer untuk hasil eksternal ambigu. |

Status `blocked_ambiguous` tidak boleh di-retry otomatis. Dua reviewer berbeda harus memeriksa folder tujuan dan legacy review lalu menghasilkan outcome terminal yang sama; action asli tetap immutable.

## Package synthesis

| Method | Path | Fungsi |
|---|---|---|
| POST | `/api/analysis-packages` | Membuat paket lintas dokumen dengan group key ketat. |
| GET | `/api/analysis-packages/{package_id}` | Assessment, conflict, dan provenance package. |
| POST | `/api/analysis-packages/{package_id}/review-decisions` | Keputusan manusia untuk synthesis package. |

Package tidak boleh mencampur organisasi/periode ambigu. Konflik atau provenance yang tidak cukup harus abstain dan tetap memblokir primary upload.

## Checklist integrasi klien

1. Baca `/config`, lalu tampilkan gate dan mode efektif kepada pengguna.
2. Simpan `job_id`; polling `/jobs/{job_id}` adalah jalur pemulihan utama.
3. Bila stream dipakai, perlakukan SSE sebagai optimasi tampilan, bukan sumber state tunggal.
4. Selalu kirim checksum/version yang terakhir dibaca pada keputusan yang mendukung optimistic concurrency.
5. Tangani `409` dengan reload dan review ulang; jangan mengulang side effect upload secara buta.
6. Jangan membuka tombol produksi hanya dari status UI lokal—gunakan promotion/readiness dan response server.
7. Jangan log body dokumen, reviewer reason, source quote, secret, atau URL privat pada klien/telemetry.
