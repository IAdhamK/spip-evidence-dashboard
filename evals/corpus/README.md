# Document Corpus Intake

Dokumen yang dikumpulkan pengguna dapat dipakai untuk regression corpus, pilot, dan kandidat expert gold. File mentah tidak otomatis menjadi expert gold.

Untuk setiap batch, simpan dokumen di luar Git bila sensitif dan buat manifest JSONL mengikuti `manifest.schema.json`. Gunakan ID pseudonim; jangan menaruh token, password, NIK, atau data pribadi yang tidak diperlukan.

Tahapan status:

1. `pilot_unlabelled`: dokumen boleh diproses untuk menemukan failure mode, tetapi tidak membuka promotion gate.
2. `expert_candidate`: organisasi, periode, expected mapping, dan source location sudah diusulkan.
3. `expert_gold`: label sudah dikonfirmasi reviewer ahli dengan identitas dan waktu.

Validasi:

```bash
PYTHONPATH=backend python evals/validate_corpus_manifest.py manifest.jsonl --document-root /path/to/documents
```

Dokumen dapat dikirim dalam beberapa batch. Variasi yang paling berguna: PDF text/scan, DOCX tabel dan gambar, XLSX besar/hidden/formula, PPTX diagram, foto, template kosong, periode campuran, unit organisasi berbeda, bukti negatif, dan historical failure.
