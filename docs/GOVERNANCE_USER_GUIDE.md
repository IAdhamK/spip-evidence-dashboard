# Panduan Governance V2 Tanpa Pemrograman

Halaman **Governance V2** dipakai oleh domain owner, technical reviewer, dan data owner. Keputusan di halaman ini tidak mengubah source code atau environment server. Semua keputusan mencatat identitas, alasan, checksum, versi, masa berlaku, dan riwayat revisi.

Untuk memeriksa foto/gambar visual-pending atau kandidat teks **OCR Rescue**, gunakan halaman **Review Visual** dan panduan `docs/VISUAL_REVIEW_USER_GUIDE.md`. Review tersebut merupakan verifikasi manusia atas artefak tertentu; bukan pengesahan capability provider vision dan bukan consent transfer data eksternal.

## Mengesahkan kandidat menjadi dataset ahli

Tahap ini tidak memerlukan pemrograman, file JSON, atau perhitungan checksum manual.

1. Reviewer pertama menyelesaikan kasus pada halaman **Review Terpandu** sampai statusnya menjadi kandidat label ahli.
2. Domain owner yang berbeda membuka **Governance V2 → Dataset Ahli**.
3. Lihat angka **Menunggu pengesahan**, **Target shadow 50**, dan **Target general release 200**.
4. Pilih kasus, lalu tekan **Buka Dokumen**. Bandingkan isi dokumen dengan mapping, grade, peran evidence, status substantif/template, dan lokasi sumber yang ditampilkan.
5. Pilih **Tujuan kasus**: **Evaluasi rilis (holdout)** untuk target/metrik 50/200, atau **Learning retrieval** untuk mengasah vocabulary. Jangan memasukkan dokumen yang sama ke kedua tujuan.
6. Isi nama domain owner dan catatan singkat pemeriksaan.
7. Centang pernyataan pemeriksaan.
8. Pilih **Sahkan sebagai Expert Gold** bila benar, atau **Kembalikan untuk Diperbaiki** bila masih keliru.

Reviewer pengesahan harus berbeda dari reviewer pertama. Saat disahkan, aplikasi menghitung checksum dataset otomatis; hanya tujuan Evaluasi yang memperbarui progres 50/200, sedangkan Learning mempunyai hitungan sendiri. Saat dikembalikan, kasus muncul lagi pada **Review Terpandu** dengan status belum yakin. Tidak satu pun keputusan ini membuka upload produksi dengan sendirinya.

Hanya expert gold dengan tujuan **Learning retrieval** yang menyegarkan registry. Gold **Evaluasi rilis** tidak pernah dipakai untuk belajar dan hanya partisi Evaluasi yang dihitung dalam Recall@5, mapping precision, source accuracy, evidence-role accuracy, template detection, abstention, overgrade, latency/cost, serta target 50/200. Learning baru aktif bila checksum korpus learning dan katalog parameter sama dengan snapshot. Aplikasi hanya menerima pola istilah yang konsisten pada minimal tiga dokumen; kandidat belum disahkan dan prediksi mesin tidak ikut belajar. Database menyimpan fingerprint istilah, bukan teks istilah. Status dan jumlah fingerprint terlihat pada tab **Dataset Ahli**, tetapi learning ini hanya membantu pencarian parameter dan tidak mengubah grade.

## Membuat evaluation report dan bukti rilis

1. Buka **Governance V2 → Bukti Rilis**.
2. Pada langkah 1, periksa jumlah expert-gold dan status checksum dataset.
3. Isi nama dataset serta evaluator, centang pernyataan, lalu tekan **Buat Report Otomatis**.
4. Periksa Recall@5, mapping precision, akurasi sumber, evidence-role accuracy, template detection, abstention, overgrade, latency/cost, serta seluruh cakupan label/assessment. Report otomatis akan berlabel **berwenang untuk rilis**. Report manual/legacy berlabel **informasional** dan tidak dapat dipilih untuk keputusan `passed`. Report tidak menyimpan isi dokumen atau kutipan sumber.
5. Pada langkah 2, isi ID siklus, versi, tahap, keputusan, product owner, incident, rollback, dan alasan.
6. Untuk keputusan `passed`, pilih report server-derived yang berwenang. Server menghitung ulang report saat penyimpanan dan menolak perbedaan SHA-256, dataset, case count, generation method, atau authority. Checksum dataset dan comparison report diisi server; pengguna tidak mengetik hash.
7. Tandai **siklus stabil** hanya untuk canary/general yang lulus, tanpa critical incident, dan rollback sudah diuji.
8. Tekan **Simpan Event Rilis**. Event lama tidak dapat diedit atau dihapus; koreksi harus menjadi event baru.

Aplikasi menonaktifkan keputusan `passed` ketika gate tahap belum siap. Shadow/pilot membutuhkan minimal 50 expert-gold partisi Evaluasi dengan Recall@5, akurasi sumber, dan recall deteksi template kosong ≥95%, overgrade ≤2%, serta cakupan label/assessment grade, evidence role, dan template masing-masing ≥95%. Canary juga membutuhkan seluruh rule aktif disahkan, tanpa high/critical security finding, dan OCR/vision efektif bila dibutuhkan. General release membutuhkan minimal 200 Evaluasi. Dua stable cycle tervalidasi dengan authority/recompute V26 tetap diperlukan sebelum deprecation V1.

## Pengesahan rule parameter-grade

1. Buka **Governance V2 → Rule Parameter**.
2. Cari parameter berdasarkan kode atau uraian.
3. Baca lima kartu Grade A–E. Periksa criterion, tahap wajib, jenis sumber, effective date, prerequisite, dan disqualifier.
4. Pilih **Setujui**, **Tolak**, atau **Lewati** pada setiap grade. Tombol **Pilih setujui untuk 5 grade** hanya membantu memilih; reviewer tetap harus membaca seluruh kartu.
5. Isi identitas reviewer dan alasan.
6. Centang pernyataan pemeriksaan, lalu tekan **Simpan Keputusan Rule**.

Jika checksum berubah sebelum penyimpanan, seluruh keputusan dalam request ditolak secara atomik. Keputusan lama tidak dihapus: state terbaru disimpan pada katalog, sedangkan setiap revisi masuk ke event history append-only.

Rule yang ditolak atau belum diperiksa tetap `draft` dan tidak dapat membuka primary upload. Pengesahan 184 parameter berarti memeriksa 920 kombinasi parameter-grade, bukan satu persetujuan global.

## Uji dan consent OCR/Vision

OCR teks dasar sekarang memakai urutan **lokal lebih dahulu**:

1. Tesseract `ind+eng` membaca scan/image di server dan menyimpan confidence serta bounding region. Isi dokumen tidak dikirim ke Sumopod.
2. Bila OCR lokal tidak tersedia, timeout, atau confidence di bawah ambang, unit tetap `ocr_required`.
3. Sumopod vision hanya boleh menjadi fallback bila seluruh gate capability dan consent di bawah ini lulus.

Status OCR lokal terlihat langsung pada bagian atas tab ini. OCR lokal tidak memerlukan persetujuan transfer data eksternal, tetapi tetap mengikuti limit unit, timeout, confidence minimum, dan hard coverage gate.

1. Buka **Governance V2 → OCR/Vision**.
2. Pastikan identitas reviewer terisi.
3. Tekan **Jalankan Uji Synthetic**. Sistem mengirim gambar buatan bertuliskan `SPIP 2026`; tidak ada dokumen pengguna yang dikirim.
4. Capability hanya dapat disetujui jika provider mengembalikan schema, `unit_key`, dan token OCR yang benar.
5. Technical reviewer mengisi alasan, mencentang pernyataan, lalu memilih **Setujui Capability**.
6. Setelah capability disahkan, data owner dapat menyetujui **Consent data restricted** dengan masa berlaku 30, 90, atau 365 hari.
7. Capability maupun consent dapat dicabut. Pencabutan langsung membuat provider vision tidak tersedia untuk analysis run baru.

Fallback vision eksternal baru efektif bila seluruh kondisi berikut benar:

- `VISION_ANALYSIS_ENABLED=true`;
- `ANALYSIS_VISION_PROVIDER_VALIDATED=true`;
- API key tersedia;
- renderer PDF tersedia;
- synthetic capability probe lulus dan disahkan;
- consent pemrosesan data `restricted` aktif dan belum kedaluwarsa.

Approval UI tidak menyalakan feature flag. Sebaliknya, feature flag tanpa approval dan consent juga tidak cukup. Kombinasi dua lapis ini disengaja agar rollout tetap fail-closed.

## Status OCR lokal saat ini

Image backend memasang Tesseract beserta bahasa `ind` dan `eng`. Audit full-document v10 pada 110 dokumen memakai fallback layout PSM 6→3→11, preprocessing transparansi, adaptive high/low-DPI, dan tiled OCR maksimal 16 tile. Sebanyak 482/500 target visual/OCR diproses dan 27.439 bounding region dicatat; 18 unit tetap fail-closed dan tidak ada halaman Office deferred atau raster over-budget. Antrean manusia berisi 193 review visual, 13 kandidat OCR Rescue, dan 5 unit tanpa kandidat yang harus ditranskripsi dari preview. Apple Vision pada host macOS berhasil pada browser smoke terbatas, tetapi runtime deployment teruji tetap Tesseract di container. Konfigurasi `auto` memakai provider lokal yang lulus self-test dan tetap fail-closed bila tidak ada provider sehat.

## Hasil validasi DeepSeek V4 Pro saat ini

Probe synthetic pada Sumopod `deepseek-v4-pro` tanggal 12 Juli 2026 gagal karena provider tidak mengembalikan `unit_key` synthetic yang diwajibkan. Karena itu capability tidak disahkan dan consent tetap terkunci. DeepSeek V4 Pro tetap dipakai untuk structured text/reasoning yang sudah tervalidasi, tetapi belum boleh diperlakukan sebagai vision provider. Kegagalan ini tidak mematikan OCR lokal.
