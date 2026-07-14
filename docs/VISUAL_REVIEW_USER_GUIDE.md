# Panduan Review Visual Tanpa Pemrograman

Halaman **Review Visual** dipakai untuk dua pekerjaan: memeriksa makna foto, halaman DOCX, snapshot sheet XLSX, screenshot slide PPTX, dan **menyelamatkan teks OCR** yang terbaca tetapi kepercayaannya masih di bawah ambang aman. Anda tidak perlu menulis kode, JSON, checksum, atau menjalankan perintah terminal.

## Yang perlu disiapkan

- Gunakan laptop/monitor yang cukup jelas untuk melihat gambar.
- Siapkan nama atau email dinas reviewer.
- Bila gambar memerlukan pengetahuan bidang SPIP yang tidak Anda kuasai, pilih **Belum yakin** dan minta domain owner memeriksanya.

## Langkah paling mudah

1. Buka aplikasi, lalu tekan **Review Visual** di bagian atas.
2. Pilih **Belum direview**. Aplikasi menampilkan satu visual pada satu waktu.
3. Lihat label jenis pekerjaan. **Verifikasi makna visual** meminta Anda menjelaskan isi gambar/halaman/sheet/slide lengkap; **OCR rescue** meminta Anda membandingkan gambar dengan kandidat teks OCR berkepercayaan rendah. Jika tertulis **OCR tidak menghasilkan teks**, tidak ada kandidat untuk disetujui: baca langsung dari preview dan transkripsikan secara manual. Preview berasal dari nomor halaman, nama sheet, atau nomor slide yang tercatat. Gunakan **Buka Gambar Penuh** bila tulisan kecil. Bila dokumen Office menyediakan koordinat terstruktur, kotak bernomor menandai region seperti chart, diagram, gambar, tanda tangan, atau stempel; daftar di bawah gambar menjelaskan jenis dan labelnya. Kotak ini hanya petunjuk lokasi, bukan keputusan bahwa objek pasti merupakan evidence.
4. Pilih satu hasil pemeriksaan:

   | Pilihan | Gunakan ketika |
   |---|---|
   | **Teks dan makna benar** | Teks OCR cocok dengan gambar dan, bila diminta, Anda memahami konteksnya sebagai evidence. Pada OCR rescue, kandidat yang tadinya ditahan baru dipakai setelah keputusan ini. Opsi ini nonaktif jika OCR tidak menghasilkan kandidat. |
   | **Perlu koreksi** | Ada teks atau makna yang salah/kurang; ketik ulang hanya bagian yang benar-benar terlihat pada gambar. |
   | **Bukan evidence** | Gambar sudah jelas, tetapi tidak membuktikan parameter SPIP, misalnya logo atau ornamen. |
   | **Belum yakin** | Gambar buram, konteks kurang, atau Anda membutuhkan pemeriksa lain. |

5. Untuk **Teks dan makna benar** atau **Perlu koreksi**, isi teks/ringkasan dengan kalimat faktual. Pada **OCR rescue**, fokus utama adalah transkripsi yang terlihat; jangan memperbaiki isi berdasarkan dugaan.
6. Bila lokasi visual penting, pilih jenis pada **Region visual**, isi label singkat, tekan **Tandai region pada gambar**, lalu tarik kotak pada gambar sumber. Langkah ini opsional; gunakan terutama untuk diagram, chart, tanda tangan, stempel, atau tabel visual. Kotak yang salah dapat dihapus sebelum disimpan.
7. Isi **Nama/identitas reviewer** serta **Alasan pemeriksaan**. Centang pernyataan bahwa Anda sudah melihat gambar sumber.
8. Tekan **Simpan & Lanjut**. Keputusan tersimpan dan item berikutnya akan terbuka.
9. Ulangi sampai semua unit pada run tersebut selesai. Item **Belum yakin** masih dianggap tertahan dan perlu diperiksa kembali.
10. Setelah tertulis `0 masih tertahan`, isi alasan penerapan, centang pernyataan, lalu tekan **Buat Run Turunan**.
11. Aplikasi membuat analisis baru dan menghitung ulang fakta, mapping, serta verification. Run lama tetap utuh sebagai jejak audit. Untuk OCR rescue yang final, unit turunan keluar dari status `ocr_required`; keputusan ini tidak otomatis mengesahkan grade atau rule SPIP.

## Cara tercepat untuk 13 kandidat OCR

1. Pilih filter **Belum direview**, kemudian utamakan item berlabel **OCR rescue**.
2. Besarkan gambar dan baca baris demi baris.
3. Jika kandidat OCR persis atau hanya berbeda spasi kecil, pilih **Teks dan makna benar**.
4. Jika ada huruf, angka, atau kata yang keliru, pilih **Perlu koreksi** lalu ketik ulang sesuai gambar.
5. Jika tulisan tidak dapat dibaca, pilih **Belum yakin**. Jangan menebak agar pipeline tetap aman.

Audit korpus terbaru menyediakan 13 kandidat tersebut langsung di antrean: 11 halaman PDF dan 2 foto. Kandidat mesin sengaja tidak dipakai sebagai fakta sebelum seorang reviewer menyetujuinya atau memberikan koreksi.

## Cara menangani 5 unit tanpa kandidat OCR

1. Buka item berlabel **OCR tidak menghasilkan teks**, lalu besarkan preview.
2. Jika teks terbaca, pilih **Perlu koreksi** dan ketik persis isi yang relevan dari gambar. Anda tidak perlu mengisi JSON, hash, atau data teknis lain.
3. Jika halaman tidak mengandung evidence, pilih **Bukan evidence**.
4. Jika terlalu buram atau ragu, pilih **Belum yakin** dan serahkan kepada reviewer lain. Jangan memilih berdasarkan tebakan.

Sistem sengaja menonaktifkan **Teks dan makna benar** pada kelima unit ini karena tidak ada hasil mesin yang dapat dibandingkan. Preview dan keputusan tetap terikat checksum sumber.

## Contoh penulisan yang aman

- Baik: `Gambar menampilkan judul Identifikasi Risiko dan tabel daftar risiko.`
- Baik: `Foto menunjukkan papan kegiatan bertanggal 12 Mei 2026; identitas peserta tidak diverifikasi.`
- Hindari: `Kegiatan pasti dilaksanakan dengan baik.`
- Hindari: `Semua orang pada foto adalah pejabat unit X.`

Tuliskan hanya yang benar-benar tampak. Review Visual mengesahkan makna sumber, bukan memberi kesimpulan grade SPIP.

## Jika salah memilih

Pilih item melalui daftar visual, buat keputusan baru, lalu simpan lagi. Riwayat lama tidak dihapus atau ditimpa; keputusan terbaru menjadi revisi yang aktif. Mekanisme ini sengaja dibuat append-only agar perubahan dapat diaudit.

Jika gambar atau teks sumber berubah setelah keputusan dibuat, aplikasi menolak keputusan lama karena checksum tidak cocok. Periksa versi terbaru dan simpan keputusan baru.

## Mengapa tombol Buat Run Turunan belum aktif?

Tombol baru aktif jika:

- semua visual dalam run sudah berstatus final;
- tidak ada keputusan **Belum yakin**;
- minimal satu keputusan dapat diterapkan; dan
- snapshot keputusan masih cocok dengan teks serta gambar sumber.

Gunakan filter **Belum yakin** untuk menemukan item yang masih menahan run.

## Hubungannya dengan pekerjaan 50 kasus

**Review Visual** dan **Review Terpandu** mempunyai tujuan berbeda:

1. **Review Visual** memastikan makna gambar/foto atau transkripsi OCR berkepercayaan rendah dapat dipakai secara aman oleh pipeline.
2. **Review Terpandu** memilih mapping parameter, grade, dan fakta sumber untuk kandidat dataset ahli.
3. **Governance V2 → Dataset Ahli** meminta domain owner kedua mengesahkan kandidat menjadi `expert_gold`.

Urutan praktisnya: selesaikan visual yang relevan, buat run turunan, lalu gunakan hasil run tersebut ketika mengisi Review Terpandu. Pengesahan 50/200 kasus partisi Evaluasi tetap memerlukan reviewer manusia dan tidak terjadi otomatis; dokumen Learning harus dipisahkan.

## Batas keamanan

- Preview hanya dibuka jika checksum raster cocok dengan catatan OCR.
- Keputusan terikat pada checksum teks, gambar, dan snapshot region semantik serta identitas reviewer.
- Dokumen tidak dikirim ke Sumopod oleh workflow ini; OCR Rescue memakai kandidat OCR lokal dan keputusan manusia.
- Keputusan **Belum yakin** tetap fail-closed.
- Review Visual tidak mengaktifkan vision eksternal, tidak mengesahkan rule, dan tidak membuka upload produksi.
