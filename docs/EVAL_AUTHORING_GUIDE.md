# Panduan Eval Authoring Document Intelligence V2

Evaluasi dipakai untuk mengukur kemampuan pipeline pada data yang belum dipakai untuk pembelajaran. Korpus Learning dipakai secara konservatif untuk retrieval vocabulary. Kedua partisi wajib terpisah berdasarkan checksum dokumen agar hasil evaluasi tidak bocor.

## Jalur termudah tanpa pemrograman

1. Masukkan ZIP melalui Upload Pintar dengan mode **Proses lokal saja** bila izin external model/vision belum tersedia.
2. Reviewer pertama membuka **Review Terpandu**, memilih mapping/grade, fakta sumber, evidence role, status `substantive` atau `template_only`, lalu memberi alasan.
3. Domain owner kedua membuka **Governance V2 → Dataset Ahli**, memeriksa dokumen dan lokasi sumber, lalu memilih partisi **Evaluasi rilis** atau **Learning retrieval**.
4. Pada tab **Bukti Rilis**, server menghitung evaluation report dan checksum. Jangan mengetik metrik secara manual untuk membuka gate.

Panduan klik rinci tersedia di `GUIDED_REVIEW_USER_GUIDE.md`, `GOVERNANCE_USER_GUIDE.md`, dan `VISUAL_REVIEW_USER_GUIDE.md`.

## Pemisahan Evaluasi dan Learning

| Partisi | Tujuan | Boleh memengaruhi retrieval | Berwenang untuk release |
|---|---|---:|---:|
| `evaluation` | Holdout untuk mengukur retrieval, mapping, source, grade, role, template, abstention, latency, dan cost | Tidak | Ya, bila report dihitung server dan seluruh gate lulus |
| `learning` | Korpus terpisah untuk vocabulary retrieval konservatif | Ya, setelah two-person expert gold dan consistency threshold | Tidak |

Satu checksum dokumen tidak boleh aktif pada kedua partisi. Salinan file yang sama dengan nama berbeda tetap dianggap overlap.

## Desain kasus

Dataset harus mencakup seluruh `case_type` berikut:

- `positive`: ada evidence dan mapping/grade/lokasi sumber yang dapat ditentukan.
- `negative`: dokumen bukan evidence untuk parameter yang diuji atau sistem seharusnya abstain.
- `edge`: format/struktur sulit tetapi sah, misalnya tabel besar, periode di header, atau source tersebar.
- `adversarial`: template kosong, instruksi, rencana tanpa hasil, teks menyesatkan, scan buruk, atau metadata salah.
- `historical_failure`: kasus yang pernah menghasilkan salah mapping, salah source, overgrade, timeout, atau resume error.

Variasikan PDF text/scan, DOCX tabel/gambar, XLSX formula/hidden sheet, PPTX diagram, foto, bahasa, organisasi, periode, dokumen panjang, dan bukti saling bertentangan. Jangan membuat 50 duplikat ringan dari template yang sama.

## Label minimum per kasus

- ID pseudonim dan checksum dokumen.
- Outcome: confirmed/corrected/not_evidence/unsure.
- Mapping yang diharapkan: `kk_id`, `kode`, `detail_kode`, dan grade bila dapat dinilai.
- Source location dan fakta sumber yang benar.
- Evidence role untuk mapping positif: `primary`, `supporting`, `context`, atau `contradictory`.
- Status dokumen: `substantive` atau `template_only`; `not_assessed` belum layak expert gold.
- Reviewer, waktu, alasan, dan keputusan reviewer kedua.
- Partisi `evaluation` atau `learning` dan consent scope yang sesuai.

Jawaban `unsure` berguna untuk antrean perbaikan, tetapi tidak boleh dipromosikan menjadi expert gold.

## Target release

| Gate | Target |
|---|---:|
| Shadow/pilot awal | Minimal 50 kasus Evaluasi expert gold |
| General release | Minimal 200 kasus Evaluasi expert gold |
| `retrieval_recall_at_5` | ≥ 0,95 |
| `source_accuracy` | ≥ 0,95 |
| `overgrade_rate` | ≤ 0,02 |
| `grade_label_coverage` | ≥ 0,95 |
| `grade_assessment_coverage` | ≥ 0,95 |
| `evidence_role_label_coverage` | ≥ 0,95 |
| `template_label_coverage` | ≥ 0,95 |
| `template_detection_recall` | ≥ 0,95 dan harus mempunyai contoh template kosong |

`mapping_precision_at_5`, `evidence_role_accuracy`, `abstention_accuracy`, `template_detection_accuracy`, average latency, cost, dan coverage pengukurannya wajib dilaporkan untuk diagnosis. Report manual tetap informational; promotion hanya membaca report `server_derived_v2_partitioned` yang dihitung ulang dari partisi Evaluasi aktif.

## Format JSONL untuk fallback teknis

UI adalah jalur utama. JSONL digunakan untuk fixture non-sensitif atau migrasi dataset yang mendapat izin. Satu baris adalah satu JSON object:

```json
{"id":"case-001","claim":"ringkasan non-sensitif","fact_type":"policy","expected_any_of":["KK3.1:1.1.1"],"source_location_expected":{"page":2},"case_type":"positive","labelled_by":"reviewer@example.go.id","labelled_at":"2026-07-13","notes":"dasar keputusan"}
```

Field wajib validator: `id`, `claim`, `expected_any_of`, `source_location_expected`, `case_type`, `labelled_by`, dan `labelled_at`. ID harus unik; seluruh lima case type harus hadir; reviewer otomatis/synthetic tidak diterima sebagai expert.

Validasi fallback:

```bash
PYTHONPATH=backend python evals/validate_gold.py evals/gold/spip_expert_gold.jsonl --minimum-cases 50
PYTHONPATH=backend python evals/run_evals.py --cases evals/gold/spip_expert_gold.jsonl --enforce --output eval-report.json
```

Sebelum general release, ubah minimum menjadi 200. `evals/gold` tidak boleh berisi dokumen/label sensitif yang tidak disetujui untuk Git.

```bash
PYTHONPATH=backend python evals/validate_gold.py evals/gold/spip_expert_gold.jsonl --minimum-cases 200
```

## Manifest korpus dokumen

File mentah bukan expert gold. Buat manifest mengikuti `evals/corpus/manifest.schema.json`, gunakan ID pseudonim, dan pilih status:

- `pilot_unlabelled`: untuk menemukan failure mode, tidak membuka gate.
- `expert_candidate`: expected mapping/source sudah diusulkan reviewer pertama.
- `expert_gold`: telah dikonfirmasi reviewer kedua dan consent `expert_labelling` tersedia.

Validasi checksum dan path:

```bash
PYTHONPATH=backend python evals/validate_corpus_manifest.py manifest.jsonl --document-root /path/to/documents
```

Jangan memasukkan password, token, NIK, atau data pribadi yang tidak diperlukan. Dokumen `internal`/`restricted` disimpan di lokasi yang disetujui, di luar Git.

## Quality control dan leakage check

1. Pisahkan dokumen berdasarkan SHA-256 sebelum menentukan partisi.
2. Pastikan reviewer kedua berbeda dari pembuat label pertama.
3. Sampling ulang source location dan grade oleh domain owner; jangan hanya menyetujui hasil mesin.
4. Catat disagreement sebagai koreksi atau `unsure`, bukan memilih jawaban yang meningkatkan metrik.
5. Jangan memakai report Evaluasi untuk membangun vocabulary Learning.
6. Setelah rule/parser berubah, jalankan seluruh holdout tanpa mengubah label kecuali label memang salah dan correction audit dicatat.
7. Simpan report/checksum dan release event; jangan menyimpan isi dokumen pada alert atau metrics.
8. Bandingkan failure bucket, bukan hanya skor agregat: format, OCR, retrieval miss, wrong source, role, template, grade, abstention, latency, dan cost.

## Kapan dataset belum siap

- Kurang dari 50/200 kasus atau salah satu tipe kasus tidak ada.
- Label dibuat otomatis, reviewer tidak dapat ditelusuri, atau two-person check belum selesai.
- Source location, evidence role, template status, grade, organisasi, atau periode masih ambigu.
- Evaluation/Learning overlap ditemukan.
- Dokumen tidak mempunyai consent/sensitivity handling yang sesuai.
- Metrik diimpor manual lalu dianggap setara dengan report server-derived.

Status tersebut adalah pekerjaan validasi, bukan kegagalan software. Gate harus tetap menahan rollout sampai bukti yang benar tersedia.
