# Panduan Rule Authoring Document Intelligence V2

Panduan ini ditujukan kepada domain owner dan pengembang yang memelihara matriks SPIP. Rule grade bukan jawaban model AI: rule dikompilasi deterministik dari sumber parameter resmi, memiliki checksum, lalu wajib disahkan manusia sebelum dapat dipakai sebagai gate produksi.

## Sumber dan keluaran

- Sumber katalog resmi: `backend/app/spip_parameters.json`.
- Unit keputusan: kombinasi `kk_id` + `kode` + `detail_kode` + `grade`.
- Field naratif grade yang dibaca compiler: `kriteria`, `penjelasan`, dan `cara_pengujian`.
- Field tanggal opsional: `effective_date` atau alias `tanggal_berlaku` dengan format ISO `YYYY-MM-DD`.
- Keluaran compiler: `required_stages`, `required_source_types`, `period_policy`, `organization_policy`, `prerequisite_grade`, `disqualifiers`, `effective_date`, `criterion`, `source`, dan `rule_checksum`.
- Katalog saat ini berisi 920 kombinasi parameter-grade. Angka ini adalah regression invariant untuk sumber saat ini, bukan alasan untuk menerima perubahan katalog tanpa review.

`GET /api/analysis-runs/rule-catalog` dan tab Governance adalah tampilan hasil compiler. Jangan mengedit tabel approval langsung.

## Makna kontrak rule

| Field | Makna |
|---|---|
| `required_stages` | Tahap kematangan minimum yang harus terbukti, misalnya policy, implementation, evaluation, atau improvement. |
| `required_source_types` | Jenis sumber minimum yang diturunkan dari tahap/criterion, misalnya policy document atau evaluation report. |
| `period_policy` | Saat ini `required_single`: fakta lintas periode ambigu tidak boleh digabung. |
| `organization_policy` | Saat ini `required_single`: fakta lintas unit organisasi ambigu tidak boleh digabung. |
| `prerequisite_grade` | Grade sebelumnya yang harus lolos; grade tinggi tidak boleh melompati rantai kematangan. |
| `disqualifiers` | `template_only` dan `plan_without_result` menahan grade walaupun ada kata kunci yang tampak cocok. |
| `effective_date` | Evidence sebelum tanggal berlaku tidak memenuhi rule. Tanggal kosong tidak menambahkan batas waktu. |
| `criterion` | Gabungan narasi sumber yang dibatasi panjangnya untuk audit. |
| `rule_checksum` | SHA-256 kontrak stabil; perubahan material membuat approval lama stale. |

Compiler masih menginfer tahap/jenis sumber dari istilah pada narasi. Karena itu domain owner harus membaca keluaran, bukan hanya teks sumber.

## Cara menulis criterion yang dapat diaudit

1. Sebutkan artefak yang harus ada: kebijakan, bukti penerapan, laporan evaluasi, atau tindak lanjut.
2. Sebutkan tindakan dan hasilnya. Hindari criterion grade tinggi yang hanya berkata “direncanakan”.
3. Jelaskan periode dan unit organisasi bila bukti harus spesifik.
4. Pada `cara_pengujian`, nyatakan apa yang diperiksa dan lokasi/jenis sumber yang diharapkan.
5. Gunakan `effective_date` hanya jika ada dasar kebijakan yang dapat ditunjukkan.
6. Jangan menulis prompt, instruksi untuk model, confidence, atau pengecualian ad-hoc di criterion.
7. Jangan melemahkan `template_only`, `plan_without_result`, prerequisite, period, atau organization gate untuk membuat fixture lulus.

Contoh sumber grade:

```json
{
  "grade": "B",
  "kriteria": "Penerapan kebijakan telah dievaluasi secara berkala pada unit dan periode yang dinilai.",
  "penjelasan": "Laporan evaluasi memuat hasil, bukan hanya rencana atau format kosong.",
  "cara_pengujian": "Periksa laporan evaluasi bertanggal dan keterkaitannya dengan kebijakan serta bukti penerapan.",
  "effective_date": "2026-01-01"
}
```

Contoh keluaran yang harus direview (nilai persis ditentukan compiler):

```json
{
  "grade": "B",
  "required_stages": ["evaluation", "implementation", "policy"],
  "required_source_types": ["evaluation_report", "implementation_record", "policy_document"],
  "period_policy": "required_single",
  "organization_policy": "required_single",
  "prerequisite_grade": "C",
  "disqualifiers": ["plan_without_result", "template_only"],
  "effective_date": "2026-01-01",
  "source": "parameter_criterion_draft"
}
```

Keluaran tetap `draft` sampai checksum-nya disetujui domain owner.

## Alur perubahan yang aman

1. Catat alasan perubahan dan dasar kebijakan pada change ticket.
2. Ubah hanya entry yang dimaksud di `backend/app/spip_parameters.json`; pertahankan ID dan grade lain kecuali scope memang mencakupnya.
3. Jalankan validasi JSON dan regression rule:

   ```bash
   python -m json.tool backend/app/spip_parameters.json >/dev/null
   PYTHONPATH=backend python -m unittest backend.tests.test_decision_engines backend.tests.test_analysis_api -v
   PYTHONPATH=backend python scripts/validate_handover_docs.py
   ```

4. Buka Governance V2 dan bandingkan `required_stages`, `required_source_types`, prerequisite, disqualifier, tanggal berlaku, dan checksum sebelum/sesudah.
5. Uji minimal satu evidence positif, satu negatif, satu template kosong, satu plan-without-result, dan satu periode sebelum tanggal berlaku untuk rule yang berubah.
6. Domain owner memilih approve/reject dengan alasan. Approval harus memakai checksum yang sedang tampil.
7. Jalankan `reverify` untuk run yang relevan; jangan mengubah hasil historis secara manual.
8. Masukkan kasus perubahan ke regression corpus dan, setelah review dua orang, ke partisi Evaluasi atau Learning yang tepat.

## Kapan perubahan harus ditolak

- Criterion hanya menambah kata kunci tanpa artefak/hasil yang dapat diverifikasi.
- Grade A–B dapat lolos dari rencana, template, atau klaim tanpa source location.
- Rule menggabungkan periode atau organisasi berbeda untuk menutup kekurangan evidence.
- `effective_date` tidak memiliki dasar atau formatnya ambigu.
- Output compiler tidak sesuai maksud domain tetapi perubahan tetap hendak “dipaksa” melalui approval.
- Reviewer yang sama membuat label kasus dan memberikan approval kedua.

Jika compiler tidak dapat mengekspresikan rule domain secara benar, hentikan approval dan buat perubahan compiler beserta test terlebih dahulu. Model DeepSeek tidak boleh dipakai untuk menambal rule yang tidak eksplisit.

## Bukti handover rule

Satu sesi knowledge transfer dianggap teknis-selesai bila peserta dapat: menemukan entry sumber, menjelaskan seluruh field compiler, menjalankan regression, membaca perubahan checksum, menolak rule bermasalah, menyimpan keputusan di Governance, dan membuktikan upload tetap tertahan sebelum seluruh gate lulus. Kehadiran/persetujuan manusia dicatat terpisah pada `docs/HANDOVER_CHECKLIST_DOCUMENT_INTELLIGENCE.md`.
