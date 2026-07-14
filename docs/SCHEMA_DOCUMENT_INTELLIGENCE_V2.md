# Referensi Schema Document Intelligence V2

Status dokumen: Migration V1–V32, 15 Juli 2026. DDL otoritatif berada di `backend/app/database.py` dan `backend/app/migrations.py`. Schema bersifat forward-only dan idempotent; rollback produksi dilakukan melalui feature flag dan restore backup terverifikasi, bukan menurunkan versi tabel secara destruktif.

## Prinsip penyimpanan

- SQLite adalah canonical store profil resmi saat ini dan hanya didukung untuk satu replica backend dengan singleton worker lease.
- Dokumen/job payload dapat berada pada database atau filesystem content-addressed. Metadata checksum, ukuran, reference, dan TTL tetap canonical di database.
- Adapter PostgreSQL/Redis belum mengubah canonical store resmi. Multi-instance baru boleh aktif setelah shared PostgreSQL state, atomic distributed claim, dan shared payload storage terbukti.
- Foreign key dinyalakan; write kritis memakai transaksi pendek. Claim/reservation memakai atomic transaction dan fencing attempt/version.
- Ledger approval, review, release, dan rekonsiliasi dipertahankan append-only. Koreksi menghasilkan event/row baru atau run turunan.
- Hash SHA-256 adalah pengikat integritas/version, bukan enkripsi. Volume database/payload produksi tetap memerlukan encryption-at-rest dari platform.
- Backup produksi harus mencakup database dan external payload manifest yang cocok. Restore harus lulus `integrity_check=ok`, schema version, checksum, dan critical-table counts.

## Tabel baseline/legacy yang dipertahankan

| Tabel | Peran |
|---|---|
| `folders` | Hierarki folder evidence legacy. |
| `files` | Metadata file legacy/WebDAV. |
| `parameters` | Indeks parameter dan matriks grade resmi. |
| `evidence_slots` | Slot parameter-grade legacy. |
| `smart_upload_reviews` | Hasil review pipeline V1. |
| `smart_upload_actions` | Action upload V1. |
| `evidence_link_cache` | Cache link evidence yang dibatasi kebijakan SSRF/retensi. |

Tabel legacy tidak dihapus selama V1 masih menjadi fallback. Data lama tidak boleh diubah menjadi fakta V2 yang tidak pernah direkam.

## Dokumen, run, queue, dan lifecycle

| Tabel | Peran dan invariant utama |
|---|---|
| `documents` | Identitas konten, MIME, checksum, payload reference/size, retention, dan integrity metadata. |
| `analysis_runs` | Lifecycle V2, pipeline version, parent/resume lineage, coverage/gate, biaya, dan terminal state. |
| `analysis_events` | Event progress/audit per run; sumber SSE. |
| `analysis_jobs` | Durable queue, payload reference, attempt fencing, lease, cancellation, dan run attachment. |
| `analysis_worker_leases` | Singleton leader untuk profil SQLite; lease kedaluwarsa dapat direbut fail-closed. |
| `analysis_unit_checkpoints` | Artefak resume per unit/stage dengan manifest/checksum. |
| `analysis_batch_intakes` | Status intake ZIP, limit, dan counter batch. |
| `analysis_batch_members` | Relasi batch ke dokumen/job/run dengan status per anggota. |

Status job/run hanya bergerak melalui repository service. Worker lama tidak boleh menulis setelah `attempt_count` berubah. Run terminal tidak dibuka kembali; retry membuat job/run baru.

## Artefak rangkaian engine

| Tabel | Engine/artefak |
|---|---|
| `document_units` | Unit normalized beserta locator, text, checksum, parser/OCR status, dan metadata. |
| `document_structures` | Document Map, coverage ledger, kontrak `document_family`, dan registry `parameter_scope` yang digunakan setiap run. |
| `extracted_facts` | Claim terstruktur, fact type, status, evidence role/provenance, dan confidence score. |
| `fact_sources` | Kutipan/locator sumber yang harus cocok dengan unit. |
| `mapping_candidates` | Kandidat parameter-first retrieval dan ranking trace. Migration V31 menambah ranking Advanced RAG. Migration V32 memisahkan raw retrieval, mapping score, calibrated decision confidence beserta komponennya, family/role, compatibility, decision status, dan hasil grade eligibility gate. |
| `grade_assessments` | Hasil Domain Rule Grade Engine, `grade_eligible`, `grade_status` (`not_applicable`, `blocked`, `direction_only`, `supported`), block reasons, dan missing requirements. |
| `verification_results` | Independent source/rule/cross-document verification. |
| `engine_results` | Artefak ter-version untuk engine per run. |
| `security_findings` | Temuan file/archive/network dengan severity dan status. |

`extracted_facts` tidak boleh berdiri tanpa provenance yang dapat diverifikasi untuk primary evidence. Structured model bersifat advisory; rule dan verifier tetap authority untuk grade/gate.

## Review dan governance

| Tabel | Peran dan invariant utama |
|---|---|
| `human_review_decisions` | Approve/correct/reject mapping dengan reviewer, alasan, dan final mapping. |
| `domain_rule_approvals` | Approval aktif yang terikat key rule, version, dan checksum. |
| `domain_rule_approval_events` | History keputusan rule append-only. |
| `expert_review_labels` | Label kandidat/gold, source fact, evidence role, template status, partisi, reviewer, dan checksum. |
| `visual_review_decisions` | Keputusan OCR/visual region append-only yang mengikat source/text/image checksum. |
| `vision_capability_probes` | Hasil probe synthetic provider/capability tanpa dokumen pengguna. |
| `vision_governance_decisions` | Approval/revocation capability dan consent external processing. |

Expert gold mensyaratkan dua identitas reviewer berbeda. Checksum dokumen yang sama tidak boleh aktif pada partisi `evaluation` dan `learning` sekaligus.

## Package dan controlled upload

| Tabel | Peran dan invariant utama |
|---|---|
| `analysis_packages` | Header synthesis lintas dokumen dengan organization/period group key. |
| `analysis_package_members` | Run anggota package. |
| `package_assessments` | Assessment gabungan dan conflict status. |
| `package_engine_results` | Artefak engine package ter-version. |
| `package_review_decisions` | Keputusan reviewer package. |
| `controlled_upload_actions` | Reservation idempotent sebelum side effect; status sukses atau ambiguous. |
| `controlled_upload_reconciliation_events` | Pemeriksaan append-only dua reviewer untuk action ambiguous. |

Unique idempotency key mencegah dua upload untuk pasangan run/mapping. `blocked_ambiguous` tidak boleh dihapus/reset agar retry otomatis tampak aman.

## Evaluasi, rollout, dan learning

| Tabel | Peran dan invariant utama |
|---|---|
| `evaluation_reports` | Metrik/checksum content-free; release authority hanya untuk report server-derived yang valid. |
| `analysis_shadow_pairs` | Pasangan V1/V2 dan agreement metrics; agreement bukan gold truth. |
| `analysis_release_events` | Planned/started/passed/failed/rolled_back append-only. |
| `legacy_pipeline_usage_daily` | Telemetry pemakaian V1 untuk deprecation gate. |
| `legacy_analysis_imports` | Hubungan data legacy yang diimpor fail-closed. |
| `retrieval_feedback_snapshots` | Snapshot learning vocabulary dengan dataset/catalog checksum. |
| `retrieval_feedback_terms` | Fingerprint istilah dan statistik agregat; bukan teks dokumen mentah. |

Report manual/informational tidak boleh membuka promotion. Release `passed` memerlukan report otoritatif yang dihitung ulang dan checksum-nya tetap sama.

## Metadata migration

| Tabel | Peran |
|---|---|
| `schema_migrations` | Version/name/checksum migration yang sudah diterapkan. |

Migration V1–V32 harus diterapkan berurutan. Deployment menolak schema yang bukan exact Migration V32 pada production preflight saat ini. Menambah migration baru wajib memperbarui validator produksi, backup/restore drill, dokumen ini, dan test upgrade fixture.

## Retensi dan data sensitif

- File mentah dan payload mengikuti TTL konfigurasi; purge hanya menghapus object yang tidak lagi direferensikan.
- Ledger audit, checksum, status, dan metrik agregat dapat memiliki retensi berbeda dari file mentah sesuai kebijakan organisasi.
- Nama file, isi dokumen, source quote, alasan reviewer, token, dan endpoint privat tidak boleh masuk metrics/alert/log terstruktur.
- Ekspor expert dataset untuk repository harus memakai ID pseudonim. Dokumen restricted tetap di storage yang disetujui, bukan di Git.
- Deletion/retention job harus menghormati active job, batch member, preview, retry lineage, dan payload reference bersama.

## Pemeriksaan perubahan schema

```bash
PYTHONPATH=backend python scripts/validate_handover_docs.py
PYTHONPATH=backend python -W error::ResourceWarning -m unittest discover -s backend/tests -v
python scripts/run_incident_drill.py --output incident-drill-report.json
```

Untuk produksi, ikuti backup, exact-version preflight, storage attestation, restore, dan rollback di `docs/OPERATIONS_DOCUMENT_INTELLIGENCE.md`. Jangan menjalankan DDL manual pada database produksi.
