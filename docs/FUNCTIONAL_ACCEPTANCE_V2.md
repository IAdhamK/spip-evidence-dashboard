# V2 Functional Acceptance

Status: automation lulus; keputusan manusia pending
Contract: `document-intelligence-functional-acceptance-v1`
Scope: fungsi V2 pada sandbox lokal, tanpa write eksternal dan tanpa klaim production security

## Tujuan

Functional acceptance membuktikan alur V2 berfungsi dari intake hingga batas controlled upload tanpa menjadikan fixture atau hasil mesin sebagai expert truth. Hardening produksi seperti SSO aktual, attestation encrypted volume, external alert receiver, dan multi-replica deployment berada di luar scope sementara. Validasi file, exact source quote, fail-closed coverage, anti-overgrade, audit trail, dan human gate tetap wajib karena menentukan kebenaran fungsi.

Runner `scripts/run_functional_acceptance.py`:

- memilih sampel operasional bounded untuk PDF, DOCX, XLSX, dan gambar dari audit korpus;
- menjalankan fixture teks dan PPTX agar enam format route teruji;
- menjalankan seluruh 16 tahap engine per dokumen, termasuk retrieval, mapping, grade, verification, dan explainability;
- memeriksa setiap fact source mempunyai quote yang terverifikasi dan lokasi sumber;
- memastikan coverage tidak lengkap tetap memblokir primary upload;
- menjalankan regression terfokus untuk ZIP intake, retry/checkpoint, local OCR, visual review, cross-document synthesis, evaluasi, dan sandbox controlled-upload dengan mocked legacy bridge;
- tidak mengisi expected mapping, expected source location, grade, keputusan visual, atau approval rule.

## Cara menjalankan

Gunakan database dan output baru pada setiap run agar bukti tidak tercampur:

```bash
.venv/bin/python scripts/run_functional_acceptance.py \
  --archive /path/to/corpus.zip \
  --corpus-dir outputs/corpus-audit/<latest> \
  --database /private/tmp/spip-v2-functional-acceptance.db \
  --output outputs/functional-acceptance/<run>/report.json \
  --markdown outputs/functional-acceptance/<run>/REPORT.md
```

Runner menolak database yang sudah ada. Laporan menyimpan hitungan/status dan case ID netral; nama serta isi dokumen operasional tidak ditulis ke report acceptance.

## Bukti 14 Juli 2026

Run `20260714-v3` menghasilkan:

- `automated_status=passed` dan `functional_acceptance_status=pending_human`;
- tujuh kasus end-to-end: empat format operasional, satu OCR Rescue operasional tambahan, serta fixture teks dan PPTX;
- seluruh case melewati 16 engine wajib;
- source location dan exact quote valid untuk seluruh fakta yang dihasilkan;
- partial PDF/Office/image tetap fail-closed;
- 80 regression terfokus lulus;
- controlled upload mencapai mocked sandbox boundary tanpa external write;
- database sandbox menyediakan enam guided-review item, satu visual-semantics item, dan satu OCR Rescue item untuk QA no-code.

Acceptance pertama menemukan quote panjang PDF/XLSX yang tidak lagi identik karena whitespace normalization dan punctuation tambahan saat bounding. Fact Extraction sekarang mempertahankan quote sebagai substring tepat dari unit; regression khusus mencegah masalah itu kembali.

## Gate manusia yang tersisa

- 193 visual-semantics review;
- 13 OCR Rescue;
- 5 transkripsi manual tanpa kandidat;
- 50 expected mapping/source/role/template/grade untuk Evaluasi awal, lalu 200 sebelum general release;
- korpus Learning yang checksum-nya terpisah;
- pengesahan 920 kontrak rule untuk 184 parameter.

Status `pending_human` adalah hasil yang benar sampai keputusan tersebut dibuat melalui UI oleh reviewer yang memahami dokumen. Runner tidak boleh mengubahnya menjadi passed dengan data sintetik atau label buatan mesin.
