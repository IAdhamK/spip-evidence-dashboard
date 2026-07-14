# Expert Gold Dataset

Folder ini sengaja tidak berisi label buatan sistem. Gold dataset harus disusun dan disahkan oleh reviewer domain agar metrik tidak mengevaluasi model terhadap jawabannya sendiri.

Format satu kasus per baris (`.jsonl`):

```json
{"id":"case-001","claim":"...","fact_type":"policy","expected_any_of":["KK3.1:1.1.1"],"source_location_expected":{"page":2},"case_type":"positive","labelled_by":"reviewer@example.go.id","labelled_at":"2026-07-12","notes":"..."}
```

Validasi sebelum pilot:

```bash
PYTHONPATH=backend python evals/validate_gold.py evals/gold/spip_expert_gold.jsonl --minimum-cases 50
PYTHONPATH=backend python evals/run_evals.py --cases evals/gold/spip_expert_gold.jsonl --enforce --output eval-report.json
```

Sebelum general release, naikkan minimum menjadi 200. Dataset wajib memuat kasus `positive`, `negative`, `edge`, `adversarial`, dan `historical_failure`; isi dokumen sensitif tidak boleh dikomit ke repository.
