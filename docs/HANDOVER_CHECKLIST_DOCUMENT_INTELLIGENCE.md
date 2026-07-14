# Checklist Handover Document Intelligence V2

Dokumen ini memisahkan artefak yang dapat diverifikasi otomatis dari knowledge transfer dan sign-off yang harus dilakukan manusia. Kolom bukti manusia sengaja kosong sampai kegiatan benar-benar terjadi.

## Paket teknis

- [x] Roadmap dan Definition of Done: `ROADMAP_DOCUMENT_INTELLIGENCE.md`
- [x] Referensi API: `API_DOCUMENT_INTELLIGENCE_V2.md`
- [x] Referensi schema: `SCHEMA_DOCUMENT_INTELLIGENCE_V2.md`
- [x] Rule authoring: `RULE_AUTHORING_GUIDE.md`
- [x] Eval authoring: `EVAL_AUTHORING_GUIDE.md`
- [x] Onboarding reviewer no-code: `GUIDED_REVIEW_USER_GUIDE.md`, `GOVERNANCE_USER_GUIDE.md`, `VISUAL_REVIEW_USER_GUIDE.md`
- [x] Deployment, backup, restore, outage, fallback, alert, dan rollback: `OPERATIONS_DOCUMENT_INTELLIGENCE.md`
- [x] Technical-debt register dengan status/mitigasi: `TECHNICAL_DEBT_DOCUMENT_INTELLIGENCE.md`
- [x] Incident simulation lokal: `scripts/run_incident_drill.py`
- [x] Validator coverage handover: `scripts/validate_handover_docs.py`

Jalankan pemeriksaan paket:

```bash
PYTHONPATH=backend python scripts/validate_handover_docs.py
python scripts/run_incident_drill.py --output incident-drill-report.json
PYTHONPATH=backend python -W error::ResourceWarning -m unittest discover -s backend/tests -v
```

## Skenario knowledge transfer

| Peserta/role | Skenario yang harus didemonstrasikan | Bukti yang dicatat | Status |
|---|---|---|---|
| Reviewer pertama | Intake ZIP lokal, Guided Review, source, role, template status, save/resume | Tanggal, peserta, run ID fixture, hasil checklist | Belum diisi |
| Domain owner kedua | Approve/return expert candidate dan memisahkan Evaluasi/Learning | Tanggal, peserta, dataset checksum | Belum diisi |
| Domain owner rule | Membaca compiled rule/checksum, reject lalu approve fixture | Tanggal, peserta, rule key/checksum | Belum diisi |
| Operator | Deploy preflight, readiness, backup/restore, model outage, manual fallback | Tanggal, peserta, report checksum | Belum diisi |
| Incident commander | Menjalankan incident drill dan rollback ke development/legacy | Tanggal, peserta, drill/release event checksum | Belum diisi |
| Product owner | Membaca shadow/evaluation report dan menjelaskan alasan gate tertahan | Tanggal, peserta, keputusan | Belum diisi |

Kegiatan dianggap selesai hanya jika peserta menjalankan skenario, bukan sekadar menerima dokumen.

## Gate eksternal sebelum pilot/canary/general

- [ ] Seluruh 920 rule aktif disahkan domain owner terhadap checksum terkini.
- [ ] Minimal 50 kasus holdout Evaluasi expert gold untuk shadow/pilot; 200 untuk general release.
- [ ] Korpus Learning terpisah, tidak overlap checksum dengan Evaluasi.
- [ ] Dokumen operasional nyata memiliki consent dan selesai direview dua orang.
- [ ] SSO/reverse proxy aktual membuktikan trusted reviewer identity, application-role mapping, wrong-scope denial, dan spoof protection.
- [ ] Encryption-at-rest database/payload aktual mempunyai attestation yang valid.
- [ ] Alert receiver organisasi sudah diizinkan, diuji, dan acknowledgement dicatat.
- [ ] Capability/consent OCR/vision eksternal disetujui bila fitur tersebut diperlukan.
- [ ] Shadow minimum, pilot, canary, rollback rehearsal, dan dua stable release cycle mempunyai evidence append-only.
- [ ] Domain owner dan product owner menandatangani hasil pilot.
- [ ] Tanggal penghentian V1 disepakati setelah telemetry window menunjukkan pemakaian nol.

Tidak satu pun checkbox di atas boleh diisi oleh sistem berdasarkan fixture synthetic, bootstrap, atau asumsi.

## Formulir bukti sesi

Salin bagian berikut untuk setiap sesi. Jangan menaruh secret, nama/isi dokumen restricted, atau source quote di repository.

```text
Sesi/tujuan       :
Tanggal/zona waktu:
Peran peserta     :
Lingkungan        :
Fixture/run ID    :
Langkah berhasil  :
Langkah gagal     :
Report SHA-256    :
Tindak lanjut     :
Disetujui oleh    :
```

## Sign-off akhir

| Otoritas | Nama/identitas terverifikasi | Tanggal | Evidence/reference | Keputusan |
|---|---|---|---|---|
| Domain owner | — | — | — | Pending |
| Product owner | — | — | — | Pending |
| Platform/security owner | — | — | — | Pending |

Tabel pending ini adalah hard gate yang disengaja. Dokumentasi lengkap tidak dengan sendirinya menyatakan V2 siap general release.
