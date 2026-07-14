# Roadmap Document Intelligence SPIP Evidence Dashboard

Status: implementasi berjalan; rollout tetap gated
Baseline: worktree aktif per 13 Juli 2026
Ruang lingkup: ingestion, parsing, mapping SPIP, grading, verification, human review, upload, evaluasi, keamanan, dan operasi produksi

## Status implementasi aktual

Pembaruan 14 Juli 2026:

Peningkatan F3 terbaru menerapkan `advanced-rag-v1`: filename-aware BM25, cosine-IDF, semantic vector istilah administrasi/SPIP, reciprocal-rank fusion, candidate diversity, pencarian katalog penuh KK–subunsur–parameter oleh DeepSeek V4 Pro, query expansion sebagai fallback, dan constrained reranking maksimal sepuluh kandidat. Migration V31 menyimpan `rag_rank`, `rag_relevance`, serta `rag_method`. Grade text tidak masuk corpus retrieval; DeepSeek tidak dapat membuat parameter, mengubah `mapping_score`, menentukan Grade, atau membuka upload. Catatan V1–V30 dan deskripsi retrieval pada tabel di bawah adalah baseline sebelum peningkatan V31 ini.

| Fase | Status | Yang sudah tersedia | Sisa utama |
|---|---|---|---|
| F0 Baseline | Lulus lokal | Feature flags, compatibility boundary, roadmap, pipeline version, ADR-001, serta manifest snapshot UI formal dengan build/capture SHA-256 dan browser QA | Product acceptance terhadap baseline pada pilot nyata |
| F1 Persistence/Security | Implementasi kuat | Migration V1-V30, immutable evaluation report dan release authority, evaluation dataset provenance, mutually-exclusive Evaluation/Learning partition, fact evidence-role/provenance, expected template status, audit tables, append-only rule/release/visual/OCR-rescue review events, retrieval-feedback snapshot, controlled-upload reservation dan rekonsiliasi dua-reviewer, content-free telemetry, versioned vision consent, secure ZIP intake, content-addressed payload storage, private SQLite/WAL/SHM, signed storage attestation, expert label history, engine/unit checkpoints, security findings, SSRF/file guards, singleton worker-leader lease, trusted identity+role binding dengan least-privilege scope, dan legacy backfill fail-closed | Membuktikan SSO/group mapping pada environment nyata, menerbitkan attestation encrypted-volume dari platform produksi, dan storage terdistribusi bila deployment multi-host |
| F2 Document Map/Coverage | Implementasi kuat, teruji korpus nyata + Office fixture valid | Native units PDF/DOCX/XLSX/PPTX/image/text, selective local-first OCR, Tesseract `ind+eng` multi-layout dan tiled OCR, hierarchical attempt/unit/document wall-clock budget, render→OCR batch interleaving, content-free exhaustion reason, bounded single-document probe, DOCX page + XLSX visible-sheet + PPTX visual-slide routing, isolated LibreOffice Writer/Calc/Impress→PDF→PNG, single-conversion page inventory, configurable page budget dengan excess `pending`, hidden-sheet exclusion, relasi eksternal/ActiveX/embedded/VBA sanitization, exact-page/sheet/slide preview/checksum, alpha-mask/autocontrast preprocessing, adaptive high-DPI retry dan low-DPI over-tile fitting, confidence/timeout/cost/visual-semantics gates, normalized OCR regions, typed OOXML chart/diagram/picture/stamp regions, manual normalized region marking untuk raster/PDF, sparse-PDF gate, tabel DOCX per baris, structured OOXML 7 chart/30 anchored shape, document map/source location, hard coverage gate, transkripsi manual ketika OCR kosong, serta stale-checksum/semantic-region rejection. Probe outlier 68 halaman membuktikan hard stop 30/90/240 detik dan base attempt lengkap dalam tier 240 detik tanpa membuka hard gate | Menyelesaikan review manusia dan membuktikan typed region pada pilot PPTX/raster nyata |
| F3 Parameter Retrieval | Implementasi kuat | Parameter-first hybrid BM25 + cosine-IDF retrieval, metadata/context bonus, abstention, reviewer-triggered candidate expansion, serta registry fingerprint feedback hanya dari expert-gold partisi Learning yang immutable/checksum-bound, minimum tiga dokumen, precision ≥80%, catalog-safe, stale/overlap fail-closed, dan bonus retrieval terbatas tanpa otoritas grade. Gold Evaluasi tidak pernah menjadi sumber learning | Mengisi dan mengkalibrasi korpus Learning nyata yang terpisah dari 50/200 holdout Evaluasi; optional embedding hanya bila evaluasi membuktikan perlu |
| F4 Grade Rules | Kontrak dan guided governance lengkap, approval pending | Rule `2026.2-draft` mencakup stage, source type, periode, organisasi, prerequisite, disqualifier, effective date; 920 kontrak diuji; wizard lima-grade, atomic checksum decision, reject/stale handling, trusted identity, dan append-only history tersedia | Domain owner menjalankan pengesahan nyata untuk 184 parameter |
| F5 Structured Pipeline | Text, adaptive compute routing, dan local OCR kuat; vision eksternal fail-closed | Deterministic facts, Chat Completions dan Responses adapters, schema validation, exact source-quote gate, retry/fallback policy, local-first Tesseract `ind+eng`, confidence-based fallback, serta Compute Routing Engine `compute-routing-v1`. Setiap fakta menyimpan evidence role beserta provenance; structured role tetap advisory dan tidak mempunyai otoritas grade. Structured extraction hanya menerima unit eligible tanpa fakta lokal; constrained mapping reasoning Sumopod `deepseek-v4-pro` hanya diroute untuk margin/skor ambigu dan hanya dapat demote ke `needs_review`. Complexity/risk/factor/reason code disimpan tanpa otoritas grade. Synthetic vision probe, capability approval, expiring restricted-data consent, dan runtime revoke gate tersedia | DeepSeek V4 Pro Sumopod gagal probe vision `unit_key`; validasi provider vision alternatif hanya bila semantics visual masih dibutuhkan |
| F6 Verification | Implementasi kuat | Deterministic verifier termasuk quote-to-unit match, mapping-status guard, dan anti-overgrade; model second pass hanya diroute untuk mapping berisiko yang telah lebih dahulu lulus deterministik dan tetap bersifat veto-only. Cross-document graph, strict context key, contradiction gate, dan package human review tersedia | Validasi kalibrasi risk threshold pada holdout/domain pilot nyata |
| F7 API/UI | Implementasi kuat, single-replica efektif; adapter scale-out siap diaktifkan setelah shared persistence | Durable SQLite jobs, capability-driven queue contract, atomic claim by FIFO/ID, singleton worker-leader lease lintas manager, heartbeat/reclaim, dan hard guard replica >1 tersedia. Adapter `postgresql` hanya aktif bila repository membuktikan shared canonical state, atomic distributed claim, dan shared payload. Adapter `redis` memakai FIFO wake-up non-authoritative di atas PostgreSQL, fallback ke canonical polling saat signal hilang, TLS default, namespace guard, serta tidak pernah disentuh ketika canonical state masih SQLite. Readiness/API/Prometheus membedakan adapter dikenal dari multi-instance aman tanpa mengekspos URL. Secure ZIP intake, progress/resume/cancel melalui job ID maupun run ID, atomic cancel-vs-claim, unit checkpoints, artifact reuse, resume lineage, lease/cancel recovery, polling + SSE, review/reverify, candidate expansion, Governance, Guided Review, Visual Review/OCR Rescue, Operational Readiness, Shadow Comparison, dan Bukti Rilis feature modules/endpoint ownership checks tersedia. Smart Upload root hanya memiliki state/polling/job orchestration; ZIP intake, controls/progress, link crawl, hasil V2, controlled-upload state, panel batch, dan hasil legacy terpisah dengan endpoint ownership, presentational no-API/no-hook guard, serta line-budget CI. Two-person Dataset Ahli, no-code preview/checksum/history/run turunan, dan controlled upload melalui satu legacy bridge tersedia. Endpoint roadmap `approve-upload` dan alias kompatibel `controlled-upload` memakai reservation atomik sebelum side effect, hasil sukses idempotent, terminal ambiguity lock, serta rekonsiliasi no-code dua reviewer dengan optimistic concurrency tanpa force retry/reset | Implementasi shared PostgreSQL repository/payload aktual hanya ketika deployment benar-benar memerlukan >1 replica; sampai itu terjadi profil SQLite tetap satu replica. Acceptance UI manusia tetap bagian pilot |
| F8 Tests/Evals | Implementasi kuat | 274 backend tests dengan `ResourceWarning` sebagai error. Functional Acceptance v1 menjalankan tujuh case lintas enam format, 16 engine per case, exact source quote/location, partial fail-closed, 80 regression terfokus, local-only OCR review samples, serta mocked controlled-upload tanpa external write; automation lulus dan status final sengaja `pending_human`. Deterministic content-free evaluation mengukur retrieval recall, mapping precision, source accuracy, evidence-role accuracy/label coverage, template accuracy/empty-template recall/label coverage, overgrade, abstention, latency, dan cost. Promotion menahan recall template kosong di bawah 95%. Regression mencakup legacy parity/boundary, DeepSeek transport/routing, parser/OCR/typed region, durable queue/checkpoint/recovery, PostgreSQL/Redis adapter contract, structured lifecycle logging, controlled-upload/reconciliation, expert partition/authority/learning, trusted-role RBAC, `analysis-rbac-v1` OpenAPI coverage, storage/security/observability, Migration V30, serta coverage handover terhadap OpenAPI/DDL. Runtime JSON log memakai allowlist event/field, mengikat lifecycle terminal ke run ID, dan tidak menerima nama/isi dokumen, URL, prompt, source location, atau exception text. Frontend boundary/build, Nginx syntax, browser QA formal V30, actual V7→V30 copy, Docker Python 3.12 schema/health/runtime smoke, audit 110 dokumen, backup/restore, rollout guard, dan provider-outage drill lulus | Menyelesaikan review manusia, holdout Evaluasi 50/200 dan korpus Learning terpisah; hardening SSO/encrypted volume/alert receiver ditunda sementara tanpa menonaktifkan integrity gate |
| F9-F10 Rollout/Handover | Paket teknis lengkap lokal; rollout belum disahkan | Automatic V1-review↔V2-job/run ledger, content-free checksum comparison report, minimum-50 hard gate, separate expert-gold quality gate, no-code Bukti Rilis, server-derived-only evaluation authority, release-time SHA recomputation, manual/legacy report exclusion, pre-V26 stable-cycle invalidation, stale/passed/stable gate enforcement, append-only release evidence ledger, Migration V23 daily V1 usage telemetry, Prometheus counters, observation coverage, zero-call deprecation hard gate, rollback flags, ADR, referensi API/schema, panduan rule/eval/reviewer/governance, backup verification, incident/provider-outage/rollback drill, UI baseline manifest, technical-debt register, checklist KT/sign-off, serta validator CI yang mencakup 57 path/61 operasi OpenAPI, 55 role-secured/6 proxy-boundary authorization classifications, dan 46 tabel DDL | Menjalankan 50 shadow pair nyata, pengesahan/consent, pilot/canary, knowledge transfer, sign-off owner, dua release stabil V26 dengan zero V1 call, dan menetapkan tanggal deprecation V1 |

Durabilitas unit kini memakai `unit-checkpoint-v2`: Visual/OCR menulis manifest seluruh unit serta checkpoint sukses setelah setiap batch render→OCR, lalu lease recovery/retry memakai ulang hanya snapshot lengkap yang checksum kanonisnya masih identik. Unit `ocr_required`, unit yang belum sempat diproses, serta checkpoint stale/tampered dijalankan kembali atau ditolak fail-closed. Checksum mengikat key, tipe, urutan, heading, status, source location, teks, warning, dan metadata. Transisi lease recovery atomik mengikat job ke run sumber sebelum run retry dibuat sehingga crash berulang tidak mengubah run gagal menjadi job selesai palsu. Graceful shutdown memasang `stopping` sebelum join sehingga enqueue baru ditolak dan traffic readiness menjadi 503, lalu berhenti pada batas batch, mempertahankan leader selama `draining`, dan requeue tanpa menghapus payload; attempt fencing menolak semua mutasi job dari worker stale. Cancellation menerima job ID atau run ID, diserialisasi terhadap claim, dan menutup run recovery/lease-expired sebelum payload dilepas. Metric durasi drain serta alarm kritis sepuluh menit memisahkan shutdown sah dari kondisi macet. Kegagalan persistensi checkpoint menggagalkan run secara eksplisit. Event, config surface, endpoint audit checkpoint, liveness/readiness probe, metric/alert Prometheus content-free, serta regression dua simulated process death dan shutdown PDF empat halaman tersedia; hanya unit belum selesai yang di-OCR.

Compute Routing kini dijalankan setelah fact extraction, setelah mapping, dan setelah deterministic verification, termasuk pada candidate expansion dan re-verification. Keputusan tidak diturunkan hanya dari `screening/full_audit`: engine memakai format, jumlah/depth unit, text length, visual/OCR pending, coverage gap, unit tanpa fakta lokal, margin/score mapping, konteks periode/organisasi, candidate grade, dan hasil verifier. Execution Trace UI menampilkan target route beserta **skor kompleksitas** dan **skor risiko**—keduanya bukan probabilitas/confidence. Prometheus hanya mengekspor hitungan pilihan dan skor rata-rata per fase tanpa isi dokumen. Mapping model tidak dapat menambah parameter, menaikkan skor/status, atau menentukan grade; response hilang/gagal justru menahan kandidat ambigu untuk review manusia.

Pipeline V2 tetap default nonaktif pada konfigurasi distribusi. Environment pengembangan lokal memakai Sumopod `deepseek-v4-pro` melalui Responses API; real upload dan vision eksternal tetap nonaktif. Local OCR aktif terpisah dan tidak mengirim dokumen keluar. Rule berstatus `draft` dan Independent Verification sengaja memblokir primary upload sampai domain validation selesai. Mitigasi sementara tidak mempunyai otoritas promosi dan rollout guard menurunkan stage tidak-eligible ke `development`.

Audit korpus nyata 12–13 Juli 2026 memproses 110/110 dokumen secara lokal. Audit full-document v10 memeriksa 500 target visual/OCR, menyelesaikan 482, mempertahankan 18 unit fail-closed, dan mencatat 27.439 region. Dari 234 halaman hasil render Office, 170 halaman visible diroute dan seluruhnya dijadwalkan; 64 hidden sheet dikecualikan, tidak ada halaman deferred, dan tidak ada kegagalan render. Tiled OCR memproses 51 halaman; outlier yang semula membutuhkan 20 tile dirender ulang 144→115 DPI lalu berhasil dalam 12 tile tanpa menaikkan batas produksi 16. Baseline fidelity tetap 64 complete dan 46 partial: angka complete turun dari baseline text-centric 78 karena 14 dokumen Office kini benar-benar menunggu pemeriksaan visual. Antrean terdiri dari 193 review visual, 13 kandidat OCR Rescue, dan 5 unit tanpa kandidat yang sekarang dapat ditranskripsi manual dari preview checksum-bound. Kandidat maupun transkripsi tidak masuk Fact Extraction sebelum keputusan manusia diterapkan pada run turunan. Detail pembelajaran dijelaskan dalam `docs/CORPUS_LEARNING_20260706.md`.

Intake ZIP satu-klik kini memeriksa keamanan archive secara atomik sebelum enqueue, memilih sampai 50 dokumen beragam, memvalidasi signature per file, menghindari duplikat identik, mencatat batch/member, menyediakan cancel dan resume setelah reload, serta default ke pemrosesan lokal per-job. Wizard **Review Terpandu** menyediakan antrean dan expert-candidate workflow, petunjuk tiga tahap, draft otomatis, serta checklist “Siap disimpan” untuk pengguna nonteknis. **Governance V2 → Dataset Ahli** menyediakan second-review no-code, pengesahan/return, partisi mutually-exclusive Evaluasi/Learning, target 50/200 khusus Evaluasi, overlap guard, serta checksum dataset otomatis. **Bukti Rilis** menghasilkan evaluation report content-free hanya dari holdout Evaluasi, mengikatnya ke checksum dataset aktif, dan menahan passed/stable cycle yang belum memenuhi gate. Governance juga menyediakan review lima grade per parameter, keputusan checksum atomik, history, status local OCR, synthetic vision probe, capability approval, restricted-data consent, expiry, dan revoke runtime. Tesseract multi-layout/adaptif/tiled serta structured chart/shape telah diregresikan pada seluruh korpus; probe Sumopod `deepseek-v4-pro` tetap gagal kontrak `unit_key`, sehingga vision eksternal fail-closed. Critical path manusia berikutnya adalah 193 review visual, 13 OCR Rescue, 5 transkripsi manual tanpa kandidat, 50 label Evaluasi beserta grade, korpus Learning terpisah, dan pengesahan 184 parameter.

Kontrak evaluasi V29–V30 menutup metrik roadmap yang sebelumnya belum mempunyai label ahli eksplisit. Review mapping positif wajib memilih evidence role, dan seluruh jawaban pasti wajib memilih `substantive` atau `template_only`. Label lama tetap `not_assessed` sehingga tidak dapat dipromosikan tanpa review ulang. Evaluation report menyimpan hanya ID, key mapping, role, status template, counter, latency, cost, dan checksum—bukan isi dokumen—serta promotion readiness mewajibkan cakupan label evidence role dan template minimal 95%.

Halaman **Review Visual** kini menyediakan jalur manual no-code untuk 193 unit visual-pending, 13 kandidat **OCR Rescue** di bawah confidence 0,45, dan 5 unit OCR kosong yang harus ditranskripsi dari gambar. Preview halaman PDF/Office dirender persis pada locator/DPI sumber dan terikat checksum. Bila tidak ada kandidat OCR, opsi **Teks dan makna benar** dinonaktifkan; reviewer harus memilih **Perlu koreksi** lalu mengetik yang terlihat, **Bukan evidence**, atau **Belum yakin**. Keputusan dicatat append-only beserta jenis review, reviewer, alasan, teks, kandidat OCR, dan hash sumber. Keputusan final diterapkan hanya pada run turunan yang menyimpan lineage serta checksum snapshot, lalu Fact Extraction dan verification dijalankan kembali. Mesin tetap tidak boleh menaikkan teks kandidat rendah menjadi fakta dengan sendirinya.

Functional Acceptance v1 pada 14 Juli 2026 memisahkan kematangan fungsi dari hardening produksi. Tujuh case end-to-end mencakup PDF/DOCX/XLSX/gambar operasional, satu OCR Rescue operasional tambahan, dan fixture teks/PPTX; seluruhnya melewati 16 engine wajib. Run pertama menemukan provenance defect pada klaim panjang: whitespace normalization dan titik tambahan membuat quote PDF/XLSX bukan substring tepat. Fact Extraction diperbaiki agar quote bounded tetap exact; run kedua lulus source-location/quote, fail-closed, 80 regression terfokus, dan mocked sandbox upload. Database acceptance menyediakan guided-review serta satu visual-semantics dan satu OCR Rescue item, tetapi status tetap `pending_human` karena label/approval tidak boleh dibuat mesin.

## 1. Sasaran akhir

Aplikasi harus berubah dari recommender berbasis cuplikan menjadi sistem audit evidence yang:

1. Mengetahui dan menampilkan cakupan pembacaan setiap dokumen.
2. Menyertakan halaman, sheet, cell, slide, atau bagian sumber untuk setiap klaim.
3. Mencari parameter terlebih dahulu, kemudian menghitung grade berdasarkan rule parameter.
4. Memisahkan ekstraksi fakta, interpretasi, mapping, grading, dan verification.
5. Menggabungkan beberapa dokumen hanya bila KK, parameter, organisasi, dan periodenya kompatibel.
6. Menahan upload utama ketika coverage, sumber, atau verification belum memenuhi syarat.
7. Menyimpan seluruh jejak keputusan dan koreksi reviewer.
8. Mempunyai eval dataset, regression test, observability, rollback, dan prosedur operasi.

## 2. Asumsi perencanaan

- Implementasi mempertahankan FastAPI, React/Vite, SQLite, dan WebDAV pada tahap awal.
- Pipeline AI harus provider-neutral. DeepSeek-compatible Chat Completions tetap dapat dipakai, tetapi adapter disiapkan untuk Responses API atau provider lain.
- Grade final selalu membutuhkan persetujuan manusia.
- Kriteria resmi pada `spip_parameters.json` menjadi sumber rule, bukan keyword umum semata.
- Worktree saat ini memiliki perubahan aktif. Fase pertama wajib menginventarisasi dan mengamankan perubahan tersebut sebelum refactor.
- Estimasi menggunakan person-day, bukan janji tanggal. Dengan satu engineer penuh dan reviewer domain paruh waktu, keseluruhan pekerjaan diperkirakan 55-80 person-day. Dengan backend dan frontend dikerjakan paralel, target realistis 8-12 minggu kalender.

## 3. Indikator keberhasilan

### Indikator wajib sebelum produksi

- 100% rekomendasi utama mempunyai minimal satu `source_location` valid.
- 100% analysis run melaporkan `total_units`, `processed_units`, dan status coverage.
- Coverage parsial, OCR gagal, parser gagal, atau verifier menolak harus memblokir upload utama secara default.
- Tidak ada grade di atas hasil parameter rule engine.
- Paket multi-file tidak boleh menggabungkan evidence lintas parameter, unit organisasi, atau periode yang tidak kompatibel.
- Semua keputusan reviewer mencatat identitas, waktu, mapping awal, mapping final, alasan, dan versi rule/model.
- Tidak ada high-severity finding pada pemeriksaan SSRF, file upload, dan secret exposure.

### Target kualitas awal

- Parameter retrieval Recall@5 minimal 95% pada gold dataset.
- Akurasi lokasi sumber minimal 95%.
- False-positive overgrade maksimal 2%.
- Deteksi template kosong minimal 95% recall.
- Parser success rate minimal 98% untuk format yang didukung.
- Regression eval tidak boleh turun lebih dari ambang yang ditentukan domain owner.

Persentase di UI harus disebut `skor kecocokan` sampai confidence benar-benar dikalibrasi terhadap data evaluasi.

## 4. Arsitektur target

```text
File Intake
  -> MIME/Checksum/Security Validation
  -> Format Router
  -> Native Parser + Selective OCR/Vision
  -> Document Map + Coverage Ledger
  -> Unit-level Fact Extraction
  -> Compute Routing (complexity/risk/capability)
  -> Parameter Retrieval
  -> Evidence-to-Parameter Mapping
  -> Constrained Mapping Reasoning bila ambigu
  -> Parameter-specific Grade Rule Engine
  -> Independent Verification
  -> Risk-routed Model Verification bila diperlukan
  -> Cross-document Synthesis
  -> Human Review
  -> Approved Upload/Reference Action
```

### 4.1 Prinsip utama: aplikasi adalah rangkaian engine

Pipeline V2 bukan satu fungsi AI besar dan bukan satu `smart_upload.py` yang terus diperpanjang. Runtime dibangun sebagai rangkaian engine independen dengan kontrak data yang sama, status yang dapat dilacak, serta kemampuan retry per engine.

Katalog engine yang wajib dibangun:

| No. | Engine | Tanggung jawab | Implementasi utama | Output wajib |
|---|---|---|---|---|
| 0 | Analysis Orchestration Engine | Menentukan DAG, menjalankan engine, retry, resume, timeout, dan cancellation | Kode lokal/job worker | `AnalysisRun`, event, status engine |
| 1 | File Intake & Security Engine | Checksum, MIME/magic bytes, ukuran, duplikat, keamanan archive, dan retensi | Kode lokal | `DocumentIdentity`, security findings |
| 2 | File Router Engine | Memilih processor berdasarkan tipe dan karakter dokumen | Kode lokal | processing route dan capability flags |
| 3 | Native Parsing Engine | Mengambil teks, heading, tabel, formula, komentar, hyperlink, dan metadata | Parser per format | native document units |
| 4 | Visual/OCR Engine | Membaca scan, diagram, chart, foto, tanda tangan, stempel, dan layout | OCR + vision model | visual facts, OCR text, bounding/source regions |
| 5 | Document Structure Engine | Membentuk peta bab, subbab, tabel, lampiran, sheet, slide, dan hubungan antarbagiannya | Kode lokal + model bila ambigu | `DocumentMap` |
| 6 | Unitization & Coverage Engine | Membagi dokumen mengikuti struktur dan mencatat semua unit yang wajib diproses | Kode lokal | `DocumentUnit[]`, `CoverageLedger` |
| 7 | Fact Extraction Engine | Mengubah isi unit menjadi fakta atomik tanpa menentukan grade | Structured model output + rules | `ExtractedFact[]` dengan source IDs |
| 8 | Compute Routing Engine | Menghitung complexity/risk dari format, coverage, ambiguity, mapping, grade, dan hasil verifier lalu memilih compute/provider secara auditable | Policy deterministik | route decision, factor/reason code, skor non-probabilistik |
| 9 | Retrieval Engine | Mengambil parameter dan dokumen referensi yang relevan | BM25/hybrid retrieval + metadata filter | ranked parameter candidates |
| 10 | SPIP Mapping Engine | Menghubungkan fakta dengan KK, subunsur, dan parameter | Deterministik + constrained demotion-only reasoning | `MappingCandidate[]` |
| 11 | Domain Rule & Grade Engine | Menghitung grade ceiling dan kekurangan berdasarkan rule resmi per parameter | Kode deterministik | `GradeAssessment`, `rule_trace` |
| 12 | Cross-document Synthesis Engine | Menggabungkan evidence kompatibel dan membentuk evidence chain | Evidence graph + reasoning | package assessment dan contradictions |
| 13 | Independent Verification Engine | Mencari unsupported claim, salah periode, salah sumber, pencampuran konteks, dan overgrade | Deterministik + risk-routed model kedua | `VerificationResult[]` |
| 14 | Output & Explainability Engine | Menyusun payload UI, ringkasan, sitasi, alasan, dan warning tanpa mengubah keputusan inti | Kode lokal + model naratif opsional | explainable API response |
| 15 | Human Review Engine | Menangani koreksi, approval, rejection, override, identitas reviewer, dan audit trail | Workflow aplikasi | `HumanReviewDecision` |
| 16 | Evaluation & Learning Engine | Mengukur kualitas, membaca feedback reviewer, menjalankan regression eval, dan mengkalibrasi skor | Eval runner + metrics | eval report dan promotion decision |

Setiap engine harus dapat diuji tanpa harus menjalankan seluruh pipeline. AI tidak boleh menggantikan File Router, Coverage, Domain Rule, Human Review, atau Evaluation Engine.

### 4.2 Kontrak bersama antar-engine

Semua engine menerima dan menghasilkan envelope yang seragam:

```json
{
  "run_id": "run-20260712-001",
  "document_id": "doc-sha256",
  "engine": "fact_extraction",
  "engine_version": "2.0.0",
  "status": "completed",
  "input_refs": ["unit-page-17"],
  "output_refs": ["fact-120", "fact-121"],
  "coverage": {
    "required": 1,
    "processed": 1,
    "failed": 0
  },
  "warnings": [],
  "metrics": {
    "duration_ms": 1250,
    "input_tokens": 0,
    "output_tokens": 0,
    "estimated_cost": 0
  },
  "started_at": "...",
  "completed_at": "..."
}
```

Aturan kontrak:

- Engine hanya membaca artefak engine sebelumnya melalui ID yang tersimpan.
- Engine tidak boleh menghapus artefak sebelumnya; hasil revisi membuat versi baru.
- Setiap output mempunyai `engine_version`, `source_ids`, dan checksum input.
- Status yang diizinkan: `queued`, `running`, `completed`, `partial`, `failed`, `skipped`, `blocked`.
- `partial` tidak pernah otomatis diperlakukan sebagai `completed`.
- Retry harus idempotent dan tidak menduplikasi fakta atau mapping.
- Keputusan domain tidak boleh berasal dari narrative text.

### 4.3 DAG orkestrasi menurut mode

#### Mode Screening

Tujuan: triase cepat, bukan keputusan upload.

```text
Intake
 -> Router
 -> Native Parser (sample terkontrol)
 -> Structure Inventory
 -> Parameter Retrieval
 -> Preliminary Mapping
 -> Output
```

Ketentuan:

- Tidak menghasilkan grade final.
- Tidak mengaktifkan upload utama.
- UI wajib menampilkan `screening only` dan coverage parsial.
- Dapat dipromosikan menjadi Full Audit dengan document ID yang sama.

#### Mode Full Audit

Tujuan: rekomendasi yang dapat direview dan disetujui.

```text
Intake/Security
 -> Router
 -> Native Parser
 -> Visual/OCR Router
 -> Document Structure
 -> Unitization/Coverage
 -> Fact Extraction per unit
 -> Parameter Retrieval
 -> SPIP Mapping
 -> Domain Rule/Grade
 -> Independent Verification
 -> Cross-document Synthesis (bila paket)
 -> Output/Explainability
 -> Human Review
 -> Controlled Upload
 -> Evaluation Feedback
```

Ketentuan:

- Seluruh unit wajib mempunyai status terminal.
- Unit `failed` atau `partial` menurunkan run menjadi `review_required` dan memblokir primary upload.
- Cross-document synthesis dijalankan setelah hasil tiap dokumen diverifikasi, bukan sebelum itu.
- Human approval tidak mengubah fakta asli; koreksi disimpan sebagai keputusan versi baru.

#### Mode Re-analysis

Tujuan: menjalankan ulang hanya bagian yang berubah.

```text
Detect changed checksum/version
 -> invalidate downstream artifacts
 -> rerun affected engines
 -> compare old/new mappings and grades
 -> require review when decision changed
```

Contoh: perubahan prompt Fact Extraction tidak perlu mengulang Native Parsing, tetapi wajib mengulang Fact Extraction, Mapping, Grade, Verification, dan Output.

### 4.4 Model dan compute routing

Tidak semua engine menggunakan model yang sama:

| Kondisi | Route |
|---|---|
| MIME, checksum, duplicate, archive validation | Kode lokal |
| PDF/DOCX/XLSX/PPTX dengan struktur normal | Native parser |
| PDF scan atau image dengan text density rendah | OCR engine |
| Diagram, chart, stempel, atau layout kompleks | Vision engine selektif |
| Fakta eksplisit dan tabel sederhana | Parser/rule extractor lokal |
| Fakta ambigu atau relasi lintas paragraf | Model structured extraction |
| Parameter shortlist | Retrieval lokal/hybrid |
| Mapping ambigu | Reasoning model dengan candidate constraints |
| Grade | Domain rule engine deterministik |
| Verification risiko rendah | Deterministic verifier |
| Verification risiko tinggi/kontradiktif | Model verifier terpisah |
| Ringkasan untuk reviewer | Model cepat, hanya dari hasil terstruktur |

Routing harus menggunakan `complexity_score`, `risk_score`, coverage, format, dan hasil engine sebelumnya. Pemilihan model tidak boleh hanya berdasarkan pilihan pengguna `fast/deep/full`.

### 4.5 Failure policy rangkaian engine

| Engine gagal | Perilaku pipeline |
|---|---|
| Intake/Security | Hentikan run dan tolak file |
| Native Parser | Coba fallback parser; bila gagal tandai unit gagal |
| Visual/OCR | Pertahankan native result, tandai visual coverage incomplete |
| Structure | Gunakan unit per halaman/sheet sebagai fallback |
| Fact Extraction | Retry unit; jangan membuat fakta berdasarkan asumsi |
| Retrieval | Perluas kandidat; bila tetap kosong hasilkan abstention |
| Mapping | Simpan `unmapped`, jangan memaksa parameter |
| Grade Rule | Tidak ada grade; minta rule/domain review |
| Verification | Blokir primary upload |
| Output Narrative | Tampilkan data terstruktur tanpa narrative |
| Evaluation | Jangan promosikan pipeline/model version baru |

### 4.6 Perbedaan peran Codex dan runtime engine

Codex dipakai untuk membangun, menguji, dan memperbaiki rangkaian engine tersebut. Codex bukan engine analisis dokumen yang berjalan di produksi. Runtime produksi tetap terdiri dari parser, OCR/vision, retrieval, model provider, rule engine, verifier, database, dan workflow reviewer yang diimplementasikan di aplikasi.

Jejak data wajib:

```text
Document
  -> Analysis Run
  -> Document Unit
  -> Extracted Fact
  -> Source Location
  -> Mapping Candidate
  -> Grade Assessment
  -> Verification Result
  -> Human Decision
  -> Upload Action
```

## 5. Struktur kode target

```text
backend/app/
  analysis/
    orchestrator.py
    jobs.py
    coverage.py
    document_map.py
    provider.py
    schemas.py
    security.py
    processors/
      base.py
      pdf.py
      docx.py
      xlsx.py
      pptx.py
      image.py
      text.py
    passes/
      inventory.py
      extraction.py
      retrieval.py
      mapping.py
      verification.py
      synthesis.py
    domain/
      parameter_index.py
      grade_rules.py
      evidence_chain.py
      rule_compiler.py
  migrations/
  routes/
    analysis.py
    review.py
    upload.py

backend/tests/
  unit/
  integration/
  fixtures/

evals/
  cases/
  expected/
  run_evals.py
  reports/
```

`smart_upload.py` tetap berjalan selama migrasi, kemudian diperkecil menjadi facade compatibility sebelum akhirnya dipensiunkan.

## 6. Fase implementasi

### Fase 0 - Baseline, proteksi perubahan aktif, dan keputusan arsitektur

Estimasi: 2-3 person-day

Pekerjaan:

- Inventarisasi seluruh perubahan worktree dan pisahkan perubahan yang sudah valid dari eksperimen.
- Simpan baseline perilaku endpoint, response payload, dan screenshot UI saat ini.
- Buat salinan database pengujian yang sudah dianonimkan.
- Tambahkan feature flags:
  - `ANALYSIS_PIPELINE_V2_ENABLED`
  - `ANALYSIS_PIPELINE_V2_SHADOW`
  - `VISION_ANALYSIS_ENABLED`
  - `VERIFICATION_PASS_ENABLED`
  - `ALLOW_PARTIAL_PRIMARY=false`
  - `LEGACY_SMART_UPLOAD_ENABLED`
- Catat architecture decision records untuk job execution, provider AI, file retention, dan database migration.
- Tetapkan domain owner yang menyetujui rule grade.

Exit criteria:

- Baseline dapat dijalankan ulang.
- Perubahan aktif tidak hilang atau tertimpa.
- Feature flag dan strategi rollback disepakati.

### Fase 1 - Fondasi persistence, audit trail, dan keamanan

Estimasi: 5-7 person-day

Pekerjaan database:

- Buat mekanisme migration berurutan dan idempotent.
- Tambahkan tabel:
  - `documents`
  - `analysis_runs`
  - `document_units`
  - `document_structures`
  - `extracted_facts`
  - `fact_sources`
  - `mapping_candidates`
  - `grade_assessments`
  - `verification_results`
  - `human_review_decisions`
  - `analysis_events`
- Simpan `parser_version`, `rule_version`, `prompt_version`, `provider`, `model`, token/cost, dan checksum konfigurasi pada setiap run.
- Pertahankan tabel lama dan buat backfill minimal dari `smart_upload_reviews` tanpa menghapus data lama.
- Terapkan status run: `queued`, `inventory`, `extracting`, `mapping`, `grading`, `verifying`, `review_required`, `approved`, `rejected`, `failed`, `cancelled`.

Pekerjaan keamanan:

- Validasi magic bytes dan MIME, bukan ekstensi saja.
- Tambahkan batas ukuran terkompresi dan hasil dekompresi untuk Office ZIP.
- Lindungi crawler dari SSRF: allowlist, blok private/link-local/loopback IP, validasi DNS ulang, dan periksa redirect.
- Terapkan timeout, batas response, batas redirect, dan content-type allowlist.
- Hapus `file_bytes` setelah aksi selesai atau setelah TTL; simpan file pending di storage terpisah bila volumenya meningkat.
- Jangan menyimpan secret, full URL token, atau isi sensitif dalam log.

Exit criteria:

- Migration dapat dijalankan pada database salinan dan rollback tervalidasi.
- Satu analysis run dapat dilacak dari file sampai keputusan reviewer.
- Pengujian SSRF dasar dan file bomb lulus.

### Fase 2 - Document map, parser per format, dan coverage ledger

Estimasi: 9-13 person-day

Kontrak `DocumentUnit` minimal:

```json
{
  "unit_id": "doc-12:page-17",
  "unit_type": "page",
  "ordinal": 17,
  "heading_path": ["BAB III", "Evaluasi"],
  "text": "...",
  "source_location": {"page": 17},
  "status": "processed",
  "warnings": []
}
```

Pekerjaan parser:

- PDF:
  - inventaris seluruh halaman;
  - ekstrak text layer per halaman;
  - deteksi halaman scan berdasarkan density;
  - OCR/vision hanya pada halaman yang membutuhkan;
  - simpan page image reference dan koordinat sumber bila tersedia.
- DOCX:
  - pertahankan heading, paragraf, tabel, header/footer, komentar, hyperlink, dan embedded object metadata;
  - render salinan ke PDF untuk fidelity visual bila converter tersedia;
  - gabungkan hasil native dan visual tanpa menduplikasi fakta.
- XLSX:
  - inventaris seluruh sheet termasuk hidden/very hidden;
  - baca cell value, formula, cached value, comment, hyperlink, merged range, date/number format, dan row/column coordinate;
  - deteksi header bertingkat serta baris pemisah;
  - jangan menghitung header/instruksi sebagai aktivitas nyata.
- PPTX:
  - baca slide, notes, shape text, table, chart metadata, dan embedded image;
  - route slide bergambar/chart/diagram/konektor menjadi unit `slide_visual` dengan locator slide;
  - render salinan tersanitasi melalui LibreOffice Impress → PDF → PNG dan pertahankan checksum preview;
  - OCR lokal tidak menyamakan teks slide dengan makna layout; hasil tetap menunggu vision/human review.
- Gambar:
  - OCR dan vision dengan confidence serta bounding box jika tersedia.
- Teks/CSV:
  - deteksi encoding dan pertahankan nomor baris.

Coverage ledger:

- Hitung `total_units`, `processed_units`, `failed_units`, `ocr_required_units`, dan `coverage_percentage`.
- Bedakan `processed` dari `understood`; coverage 100% tidak berarti akurasi 100%.
- Blok upload utama bila unit wajib belum diproses.
- Ganti label `Mode Penuh` menjadi `Audit Terarah` sampai full coverage benar-benar tersedia.

Exit criteria:

- Setiap format menghasilkan document map dan source locator konsisten.
- Dokumen lebih dari 40 halaman dan workbook besar dapat dilanjutkan dalam beberapa batch tanpa kehilangan unit.
- Coverage gate terbukti memblokir hasil parsial.

### Fase 3 - Redesign retrieval: parameter terlebih dahulu

Estimasi: 5-7 person-day

Pekerjaan:

- Ubah indeks dari 920 kombinasi parameter-grade menjadi 184 parameter sebagai unit retrieval utama.
- Buat indeks berisi KK, subunsur, detail parameter, uraian, cara pengujian, sinonim domain, dan metadata organisasi.
- Gunakan retrieval bertahap:
  1. deteksi domain KK;
  2. shortlist subunsur;
  3. retrieve parameter;
  4. rerank memakai fakta bersumber;
  5. hitung grade setelah mapping.
- Implementasikan lexical/BM25 terlebih dahulu; embedding bersifat tambahan dan harus melalui eval.
- Pastikan AI dapat meminta perluasan kandidat bila kandidat benar tidak ada dalam top-k awal.
- Simpan alasan retrieval dan matched facts, bukan matched keyword saja.

Exit criteria:

- Recall@5 parameter mencapai target gold dataset.
- Tidak ada grade dalam tahap retrieval.
- Kandidat yang tepat dapat ditemukan tanpa bergantung pada nama file.

### Fase 4 - Parameter-specific grade rule engine

Estimasi: 7-10 person-day termasuk validasi domain

Pekerjaan:

- Buat `rule_compiler` untuk mengubah kriteria pada `spip_parameters.json` menjadi rule yang dapat dieksekusi.
- Rule minimal mencakup:
  - required evidence types;
  - required source types;
  - periode berlaku;
  - organisasi/unit yang sesuai;
  - prerequisite grade;
  - disqualifier seperti template kosong atau rencana tanpa hasil;
  - kebutuhan evaluasi dan tindak lanjut;
  - rule version dan effective date.
- Pertahankan maturity chain umum hanya sebagai fallback eksplisit.
- Sediakan halaman/artefak review rule agar domain owner dapat menyetujui setiap parameter-grade.
- Buat unit test untuk seluruh kombinasi parameter-grade yang memiliki rule.
- Keluarkan `grade_candidate`, `grade_ceiling`, `missing_requirements`, dan `rule_trace`.

Exit criteria:

- Semua rule yang dipakai produksi telah disetujui domain owner.
- Grade dapat dijelaskan dengan rule trace dan supporting fact IDs.
- Rule engine tidak bergantung pada output naratif AI.

### Fase 5 - Structured extraction dan provider adapter

Estimasi: 6-9 person-day

Pekerjaan:

- Buat interface provider untuk Chat Completions-compatible dan Responses-style APIs.
- Ubah output AI internal dari narasi bebas menjadi schema tervalidasi:
  - extracted facts;
  - evidence role;
  - source unit IDs;
  - period and organization;
  - contradictions;
  - mapping support;
  - missing evidence.
- Gunakan Pydantic/JSON Schema dan retry terbatas saat schema invalid.
- Perlakukan isi dokumen sebagai untrusted data; instruksi di dokumen tidak boleh mengubah system task.
- Batasi AI pada fakta yang tersedia dan paksa abstain ketika source tidak cukup.
- Buat narrative summary hanya setelah data terstruktur valid.
- Gunakan model mahal hanya untuk unit ambigu, cross-section synthesis, dan verification; parsing serta aturan tetap lokal.

Exit criteria:

- Tidak ada parsing heading naratif untuk keputusan inti.
- Semua output model lolos schema atau run ditandai gagal/partial.
- Provider dapat diganti melalui konfigurasi tanpa mengubah domain pipeline.

### Fase 6 - Independent verification dan cross-document evidence graph

Estimasi: 6-9 person-day

Pekerjaan verification:

- Jalankan deterministic verifier terlebih dahulu:
  - source exists;
  - kutipan sesuai source;
  - periode cocok;
  - organisasi cocok;
  - template tidak dianggap aktivitas;
  - grade tidak melampaui rule;
  - coverage lengkap.
- Jalankan model verifier terpisah hanya untuk kasus berisiko/ambigu.
- Verifier harus mencari kesalahan, bukan mengonfirmasi hasil pertama.
- Status: `verified`, `partially_verified`, `rejected`, `needs_human_review`.

Pekerjaan cross-document:

- Bangun evidence graph berdasarkan `KK + kode + detail_kode + organization + period`.
- Hubungkan kebijakan, sosialisasi, implementasi, evaluasi, dan perbaikan antarfile.
- Deteksi kontradiksi tanggal, versi, organisasi, dan hasil.
- Jangan menggabungkan rantai lintas parameter secara global.
- Aktifkan analisis paket backend hanya setelah grouping dan verification lulus.

Exit criteria:

- Overgrade yang disengaja pada fixture ditolak verifier.
- Paket evidence tidak dapat memperoleh grade dari dokumen yang tidak kompatibel.
- Semua rejection mempunyai alasan dan sumber.

### Fase 7 - Background jobs, API V2, dan frontend review workspace

Estimasi: 8-11 person-day

API target:

- `POST /api/analysis-runs`
- `GET /api/analysis-runs/{id}`
- `GET /api/analysis-runs/{id}/events`
- `GET /api/analysis-runs/{id}/units`
- `GET /api/analysis-runs/{id}/facts`
- `GET /api/analysis-runs/{id}/mappings`
- `POST /api/analysis-runs/{id}/review-decisions`
- `POST /api/analysis-runs/{id}/approve-upload`
- `POST /api/analysis-runs/{id}/cancel`

Pekerjaan backend:

- Pindahkan analisis panjang dari request sinkron ke durable job.
- Mulai dengan database-backed worker yang aman untuk deployment tunggal; siapkan adapter queue untuk deployment multi-instance.
- Terapkan idempotency berdasarkan checksum file + pipeline version + configuration hash.
- Sediakan retry per unit, cancellation, timeout, dan resume.
- Gunakan polling atau SSE untuk progress aktual.

Pekerjaan frontend:

- Hapus progress timer simulasi.
- Tampilkan tahapan aktual, processed/total units, warning, dan error per unit.
- Tambahkan Document Map viewer.
- Setiap fakta dan kandidat harus dapat membuka source page/sheet/cell.
- Tampilkan evidence chain dan missing requirements.
- Pisahkan `skor kecocokan`, coverage, verification, dan grade ceiling.
- Tambahkan workspace reviewer untuk approve, correct mapping, reject, dan memberi alasan.
- Tampilkan riwayat perubahan dan versi analysis.
- Hubungkan batch UI ke analysis package yang sudah dikelompokkan dengan aman.
- Upload utama hanya aktif setelah coverage, rule, verifier, dan human approval lulus.

Exit criteria:

- Progress UI sama dengan event backend.
- Refresh browser tidak menghilangkan status job.
- Reviewer dapat menelusuri rekomendasi sampai source dan rule trace.
- Legacy endpoint tetap tersedia di balik feature flag selama masa transisi.

### Fase 8 - Testing, evals, observability, dan operasional

Estimasi: 7-10 person-day

Testing:

- Unit test parser, locator, coverage, retrieval, rule, verifier, dan security.
- Integration test upload -> analysis -> review -> upload/reference.
- Fixture untuk PDF scan, PDF text, DOCX bertabel, workbook besar, hidden sheet, formula, PPTX, gambar, template kosong, dokumen salah periode, dan file rusak.
- Contract test untuk setiap provider AI.
- Browser test untuk alur reviewer utama.

Eval dataset:

- Mulai dengan 50 kasus expert-labelled pada partisi Evaluasi; naikkan menjadi minimal 200 Evaluasi sebelum general release. Gunakan dokumen berbeda untuk partisi Learning.
- Sertakan positive, negative, edge, adversarial, dan historical failure cases.
- Ukur retrieval recall, mapping precision, source accuracy, evidence-role accuracy, template detection, overgrade rate, abstention quality, latency, dan cost.
- Jalankan eval setiap perubahan parser, prompt, model, retrieval, atau rule.

Observability:

- Log terstruktur menggunakan run ID tanpa membocorkan isi sensitif.
- Metrics: queue depth, run duration, parser failure, OCR rate, model/schema failure, verification rejection, override rate, cost, dan upload success.
- Dashboard operasional serta alert untuk failure spike dan cost anomaly.
- Backup dan restore drill untuk database dan artefak analysis.

Exit criteria:

- CI memblokir merge bila test atau quality gate gagal.
- Eval report tersimpan per pipeline version.
- Restore dan rollback diuji, bukan hanya didokumentasikan.

### Fase 9 - Rollout bertahap dan penghentian pipeline lama

Estimasi: 5-8 person-day tersebar selama pilot

Tahap rollout:

1. Shadow mode: V1 tetap melayani pengguna; V2 berjalan tanpa memengaruhi keputusan.
2. Internal review: bandingkan V1/V2 pada dataset dan dokumen nyata.
3. Pilot terbatas: V2 untuk reviewer terpilih, upload utama tetap manual.
4. Canary: sebagian analysis run memakai V2 sebagai default.
5. General release: V2 default dengan legacy fallback.
6. Deprecation: hentikan V1 setelah dua release stabil dan data migration tervalidasi.

Rollback trigger:

- overgrade melebihi target;
- source-location mismatch meningkat;
- parser failure melewati ambang;
- SSRF/security finding;
- upload salah tujuan;
- latency/cost tidak terkendali.

Exit criteria:

- Dua siklus rilis tanpa regression kritis.
- Domain owner dan product owner menyetujui hasil pilot.
- Legacy pipeline tidak lagi dipanggil dan dapat dihapus dengan aman.

### Fase 10 - Penutupan dan handover

Estimasi: 2-4 person-day

- Hapus compatibility code yang sudah tidak dipakai.
- Finalisasi runbook deployment, incident, backup, restore, model outage, dan manual fallback.
- Dokumentasikan schema, API, rule authoring, eval authoring, serta onboarding reviewer.
- Lakukan knowledge transfer dan simulasi incident.
- Buat daftar technical debt tersisa dengan owner dan target.

## 7. Critical path dan pekerjaan paralel

Critical path:

```text
F0 Baseline
 -> F1 Persistence/Security
 -> F2 Document Map/Coverage
 -> F3 Parameter Retrieval
 -> F4 Rule Engine
 -> F5 Structured Pipeline
 -> F6 Verification
 -> F7 UI/API
 -> F8 Evals
 -> F9 Rollout
 -> F10 Handover
```

Pekerjaan yang dapat paralel:

- Setelah schema F1 stabil, frontend dapat membangun mock Review Workspace sambil backend mengerjakan F2-F4.
- Domain owner dapat menyusun rule F4 sejak F2 berjalan.
- Fixture dan gold dataset F8 harus mulai dikumpulkan sejak F0, bukan menunggu akhir.
- Security test dapat berjalan paralel dengan parser development.
- Observability dapat dibangun segera setelah lifecycle analysis run tersedia.

## 8. Migration dan kompatibilitas

- Jangan menghapus atau mengubah data lama secara destruktif.
- Selalu backup database sebelum migration produksi.
- Migration harus forward-only dan idempotent; rollback dilakukan melalui restore snapshot dan feature flag.
- Backfill review lama sebagai `legacy_import`, tanpa mengarang unit/fakta yang tidak pernah disimpan.
- Pertahankan response legacy selama frontend lama masih dipakai.
- Jalankan V1 dan V2 bersamaan pada shadow mode untuk membandingkan hasil.
- Pertimbangkan PostgreSQL hanya ketika concurrency multi-instance atau volume analysis sudah melampaui kemampuan SQLite; jangan jadikan migrasi database sebagai prasyarat fase awal.

## 9. Review gates

Setiap fase hanya boleh ditutup bila:

- kode dan migration direview;
- automated tests lulus;
- security implications diperiksa;
- dokumentasi diperbarui;
- demo menggunakan fixture representatif berhasil;
- acceptance criteria fase dipenuhi;
- keputusan domain yang relevan disetujui dan dicatat.

## 10. Definition of Done keseluruhan

Pengembangan dianggap selesai bila seluruh kondisi berikut terpenuhi:

### Fungsional

- Semua format yang dijanjikan mempunyai parser dan coverage ledger.
- Rekomendasi dapat ditelusuri sampai source unit.
- Retrieval parameter dan grading terpisah.
- Rule parameter serta verifier aktif.
- Multi-file synthesis aman terhadap pencampuran konteks.
- Reviewer dapat mengoreksi, menyetujui, menolak, dan memberi alasan.
- Upload hanya terjadi setelah approval dan duplicate/security checks.

### Kualitas

- Target eval tercapai dan regression gate aktif di CI.
- Tidak ada known critical parsing, overgrading, atau source attribution bug.
- Confidence yang ditampilkan sudah dikalibrasi atau tetap diberi label skor.

### Keamanan dan privasi

- SSRF, file bomb, malicious Office archive, path traversal, dan secret logging telah diuji.
- Retensi file dan hasil analisis terdokumentasi dan diterapkan.
- Reviewer identity dan audit trail tersedia.

### Operasional

- Background job dapat retry, resume, cancel, dan dipantau.
- Backup, restore, rollback, model outage, dan manual fallback telah diuji.
- Dashboard metrics dan alert aktif.

### Handover

- API, schema, rule authoring, eval authoring, deployment, dan incident runbook lengkap.
- Domain owner dan product owner menandatangani hasil pilot.
- Legacy pipeline dihentikan atau memiliki tanggal penghentian yang disepakati.

## 11. Risiko utama dan mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Rule grade tidak dapat diturunkan otomatis dari teks matriks | Grade tidak konsisten | Domain review dan rule versioning wajib |
| OCR/vision mahal dan lambat | Cost/latency tinggi | Gunakan selective routing berdasarkan text density |
| Kandidat benar hilang saat retrieval | Mapping salah | Recall@k eval, expansion, dan abstention |
| Evidence lintas dokumen tercampur | Overgrade | Group key ketat dan verifier paket |
| SQLite terkunci saat job paralel | Run gagal | WAL, transaksi pendek, worker limit, opsi PostgreSQL |
| File BLOB membesarkan database | Storage/backup bermasalah | TTL, external pending storage, purge job |
| Link evidence melakukan SSRF | Insiden keamanan | Allowlist, IP blocking, redirect validation |
| Model/provider berubah | Output tidak stabil | Provider adapter, schema validation, model pinning, eval |
| Reviewer mengabaikan warning | Salah upload | Hard gate, explicit override reason, audit log |

## 12. Urutan implementasi yang tidak boleh dibalik

1. Audit trail dan security foundation.
2. Document map dan coverage gate.
3. Parameter retrieval tanpa grade.
4. Parameter-specific rule engine.
5. Structured model output.
6. Independent verification.
7. Cross-document synthesis.
8. Reviewer UI dan controlled upload.
9. Evals, pilot, dan rollout.

Model yang lebih kuat, embedding, atau vision tidak boleh dipakai sebagai jalan pintas sebelum provenance, coverage, rule, dan evaluation tersedia.
