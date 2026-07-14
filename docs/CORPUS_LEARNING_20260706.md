# Pembelajaran Korpus Dokumen 2026-07-06

Tanggal analisis: 12 Juli 2026
Sumber: satu ZIP operasional yang diberikan pengguna
Kebijakan data: analisis lokal, sensitivitas `restricted`, status `pilot_unlabelled`
AI eksternal: tidak digunakan; isi dokumen tidak dikirim ke DeepSeek, Sumopod, atau layanan lain

## Hasil utama

Arsip lolos pemeriksaan sebelum ekstraksi: 110 file, 192.573.240 byte setelah dekompresi, rasio kompresi 1,163, tanpa entry terenkripsi, symlink, path traversal, collision, atau rasio kompresi berbahaya.

| Format | Dokumen | Unit struktur utama |
|---|---:|---:|
| PDF | 68 | 1.376 halaman |
| XLSX | 24 | 174 sheet, termasuk 64 hidden sheet |
| DOCX | 6 | 574 blok/baris tabel |
| JPEG | 12 | 12 foto, masing-masing 1.599×1.200 atau 1.600×900 |
| Total | 110 | 2.162 unit pipeline |

Pipeline mengekstrak 4.147.874 karakter native tanpa menulis isi teks mentah ke ringkasan. Intake memblokir nol file. Baseline text-centric awal memiliki 41 dokumen lengkap; audit v4/v5 sempat mencapai 78. Audit full-document v10 sekarang meroute dan menjadwalkan seluruh 170 halaman/sheet Office visible, sehingga 14 dokumen Office yang dahulu tampak lengkap sengaja kembali `partial` sampai makna visualnya direview. Baseline fidelity baru adalah 64 complete dan 46 partial; perubahan ini merupakan hardening coverage, bukan regresi parser native.

## Failure mode yang ditemukan

1. **PDF scan dan text layer palsu-tipis.** Ada 239 halaman tanpa text layer dan 61 halaman lain yang memiliki kurang dari 200 karakter native tetapi mengandung image XObject. Sebelumnya 61 halaman terakhir berisiko dianggap selesai walaupun konten visualnya belum terbaca.
2. **Visual XLSX belum identik dengan nilai cell.** Korpus memiliki 151 drawing part, 7 chart, 3 drawing bermuatan teks, 16 embedded image, 12 external-link part, 9 table definition, dan 787 defined name. Hanya 2 sheet mempunyai print area eksplisit. Cell parser membaca formula, komentar, merged range, dan hyperlink, tetapi chart/shape tetap membutuhkan renderer atau vision untuk memahami relasi visual.
   Audit v6 membuktikan LibreOffice mengekspor seluruh 174 sheet, termasuk 64 hidden sheet. Routing v7 kini mempertahankan page index PDF tetapi hanya membuat unit untuk 110 sheet visible; hidden sheet tetap tersedia melalui parser native. Sebagian raster visible mencapai 99–204 juta pixel. Tiled OCR v8 memproses maksimal 16 tile berukuran 16 juta pixel, memetakan bounding box kembali ke halaman penuh, dan memulihkan lima timeout tanpa mengubah hard gate. Audit v10 menutup outlier 204 juta pixel dengan rerender adaptif 144→115 DPI: kebutuhan turun dari 20 menjadi 12 tile, batas produksi tetap 16, dan locator/checksum memakai raster aktual.
3. **Provenance tabel DOCX terlalu kasar.** Visual QA atas 6 DOCX/60 halaman menunjukkan tabel panjang dan dokumen mixed orientation. Unit tabel yang hanya menunjuk satu block tidak cukup presisi untuk source review; locator harus mencapai tabel dan baris.
4. **Foto rapat bukan evidence teks biasa.** Visual review seluruh 12 JPEG menunjukkan beberapa rangkaian rapat, sebagian dengan watermark waktu/tanggal/lokasi yang tertanam sebagai pixel. OCR dapat membantu membaca watermark, tetapi vision dan package context tetap diperlukan untuk menghubungkan foto dengan kegiatan tanpa mengandalkan identifikasi orang.
5. **Duplikasi dapat membiasakan evaluasi.** Ditemukan 5 grup duplikat identik yang mencakup 10 dokumen. Split evaluasi dan retrieval harus dikelompokkan berdasarkan SHA-256 agar salinan tidak dihitung sebagai bukti independen.
6. **Data sensitif perlu tetap lokal.** Detektor agregat menemukan 18 pola nomor telepon dan 7 pola yang mungkin NIK. Nilai yang cocok tidak ditulis ke laporan. Batch tetap `restricted` dan tidak boleh masuk Git.
7. **Korpus belum otomatis menjadi label ahli.** Tidak ada unit yang boleh dinaikkan menjadi `expert_gold` tanpa expected mapping, lokasi sumber, identitas reviewer, dan waktu review.

## Perbaikan yang langsung diterapkan

- `scripts/audit_document_corpus.py` memeriksa ZIP sebelum ekstraksi, menolak traversal/enkripsi/symlink/collision/bomb, mengekstrak secara aman, menghitung checksum/duplikat, menjalankan seluruh parser lokal, dan menghasilkan manifest serta laporan tanpa isi teks mentah. Checkpoint ditulis per dokumen; resume nyata dari record 78/110 lulus tanpa duplikasi, dan baseline reuse terikat SHA-256 + nama file + audit profile.
- PDF dengan text layer sangat tipis dan image XObject sekarang berstatus `ocr_required`, bukan `processed`.
- XLSX sekarang menginventarisasi drawing, chart, external link, dan table definition. Teks shape dan cache chart diekstrak sebagai unit `partial` yang tetap memblokir primary upload sampai visual verification tersedia.
- XLSX juga mencatat used-range `dimension_ref`, defined name/print area, serta konteks sheet untuk drawing/chart agar renderer berikutnya dapat memilih region yang relevan.
- Tabel DOCX sekarang diunitisasi per baris dengan locator `table` dan `row`, serta cell text berlabel `C1`, `C2`, dan seterusnya.
- Parser image mencatat dimensi PNG/GIF/BMP/JPEG tanpa menambah library decoder produksi.
- Antrean 50 dokumen unik disiapkan untuk expert review: 20 XLSX, 18 PDF, 6 DOCX, dan 6 JPEG. Semua item berstatus `pending_human_expert`.
- Regression test auditor korpus, parser termasuk structured/unanchored chart serta full-document Office, guided/two-person expert review, secure ZIP batch intake, governance rule/vision/release, adaptive/preprocessed/tiled local OCR, retained low-confidence candidate, exact-page/sheet/slide preview, hidden-sheet routing, OOXML sanitization, page/checkpoint/tile budget, literal-TSV parser, visual-semantics fact gate, typed checksum-bound human review, queue guard, deterministic expert evaluation, release ledger, dan Prometheus/Alertmanager profile tersedia. DOCX/XLSX/PPTX fixture valid dan container smoke telah lulus. ZIP operasional ini tidak mengandung PPTX sehingga pilot PPTX tetap diperlukan.
- Halaman **Review Terpandu** memungkinkan pengguna nonteknis memilih hasil, parameter resmi, serta fakta sumber tanpa menulis JSON. Hasil disimpan append-only sebagai `expert_candidate` dan tidak membuka upload produksi.
- Halaman **Upload Pintar** sekarang menerima ZIP secara satu-klik, mempertahankan mode lokal, memilih sampel beragam, menghindari duplikat, dan memulihkan progress batch setelah reload.
- Probe synthetic menunjukkan Sumopod `deepseek-v4-pro` belum memenuhi kontrak vision `unit_key`. Local-first Tesseract `ind+eng` mencoba PSM 6 lalu 3/11, alpha-mask/autocontrast untuk gambar lemah, adaptive high/low-DPI, dan tiled OCR tanpa menurunkan confidence 0,45. Parser TSV memakai mode literal agar glyph kutip tidak menelan baris metadata dan menghasilkan confidence palsu. Audit v10 menargetkan 500 unit visual/OCR, memproses 482, mencatat 27.439 bounding region, mempertahankan 18 `ocr_required`, dan tidak memiliki halaman deferred atau raster over-budget. Lima puluh satu halaman menggunakan tiled OCR. Structured OOXML tetap memproses seluruh 7 chart dan 30 anchored shape. Sebanyak 193 foto/embedded/page/sheet visual mempunyai teks tetapi sengaja tetap parsial; Fact Extraction dan structured model menolaknya sampai makna visual diverifikasi. Tidak ada AI eksternal yang digunakan.
- Probe resource v11 pada outlier PDF 68 halaman menemukan bahwa render-all-before-OCR menghabiskan deadline tanpa progres. Engine kini menginterleave render→OCR per empat unit dengan hard budget bertingkat: 30 detik/attempt, 180 detik dan 24 attempt/unit, 900 detik/dokumen. Ladder container lokal tanpa jaringan membuktikan deadline 30/90/240 detik berhenti pada 29,923/90,058/240,069 detik dan meningkatkan progres 13→32→51 halaman accepted; pada 240 detik seluruh halaman telah mendapat base attempt dan 17 kandidat rendah tetap diblokir untuk retry/review. Confidence 0,45 tidak diturunkan dan tidak ada AI eksternal yang digunakan.
- Halaman **Review Visual** menyediakan preview sumber, perbandingan teks OCR, empat keputusan manusia, riwayat append-only, dan penerapan ke run turunan. Audit v10 menghasilkan 193 visual-pending, 13 kandidat **OCR Rescue**, serta 5 unit tanpa kandidat yang sekarang juga dapat ditranskripsi manual dari exact preview. Checksum teks/gambar/kandidat mencegah keputusan lama diterapkan pada artefak yang berubah. Seluruh unit tetap tidak masuk Fact Extraction sebelum reviewer manusia menyetujui, mengoreksi, atau menandainya bukan evidence.

## Artefak lokal

Baseline berada di `outputs/corpus-audit/20260706/`, hasil intermediate di direktori `20260706-local-ocr-v3` hingga `v9`, dan baseline full-document/adaptive terbaru di `outputs/corpus-audit/20260706-local-ocr-v10-adaptive-office/`. Semuanya sengaja diabaikan Git:

- `archive_audit.json`: keputusan keamanan sebelum ekstraksi.
- `document_results.jsonl`: hasil per dokumen tanpa text payload.
- `document_results.checkpoint.jsonl`: checkpoint resumable dengan audit profile.
- `corpus_summary.json`: metrik agregat.
- `manifest.jsonl`: manifest 110 dokumen `pilot_unlabelled` yang lolos validator.
- `expert_review_queue_50.jsonl`: antrean awal 50 dokumen unik untuk reviewer.
- `CORPUS_AUDIT.md`: ringkasan yang dapat dibaca manusia.

## Dampak terhadap roadmap

Batch ini menutup kekurangan “belum diuji pada dokumen operasional nyata” untuk tahap **discovery/failure-mode analysis**, tetapi belum menutup pilot domain atau promotion gate. Bukti yang sekarang tersedia adalah bahwa pipeline membaca seluruh batch, mempertahankan fail-closed pada coverage visual, serta menghasilkan kandidat review yang dapat ditindaklanjuti.

Urutan lanjutan yang paling bernilai:

1. Masukkan ZIP melalui intake batch satu-klik, lalu reviewer ahli mengisi expected mapping dan source location untuk 50 kasus holdout Evaluasi melalui **Review Terpandu**; gunakan dokumen tambahan yang berbeda untuk korpus Learning dan validator harus tetap menolak item yang belum lengkap.
2. Domain owner mengesahkan rule parameter-grade yang dipakai untuk 50 kasus tersebut.
3. Reviewer nonteknis menyelesaikan 13 **OCR Rescue**, 5 transkripsi manual tanpa kandidat, dan 193 unit visual-semantics melalui halaman **Review Visual**. Ambang confidence tetap 0,45; sistem menyimpan kandidat rendah hanya untuk dibandingkan dengan gambar dan menerapkannya lewat run turunan setelah keputusan manusia.
4. Jalankan retrieval, mapping, source-accuracy, dan overgrade eval pada hasil label; jangan mencampur duplikat SHA-256 antar split.
5. Jalankan shadow comparison V1/V2 pada batch yang sama, lalu pilih pilot/canary hanya setelah metrik memenuhi gate.

Menambah dokumen tetap berguna bila memperluas unit organisasi, periode, format scan, atau failure mode. Namun setelah batch ini, nilai terbesar bukan dari volume mentah tambahan, melainkan dari pelabelan ahli yang presisi pada sampel beragam dan perbaikan OCR/visual untuk unit yang sudah teridentifikasi.
