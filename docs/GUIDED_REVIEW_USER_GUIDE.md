# Panduan Review Terpandu untuk Pengguna Nonteknis

Panduan ini dipakai untuk mengubah hasil analisis dokumen menjadi **kandidat label ahli** tanpa menulis kode atau mengedit JSON. Review ini membantu aplikasi belajar mengevaluasi kecocokan parameter dan lokasi sumber.

## Memasukkan banyak dokumen dari ZIP

1. Buka **Upload Pintar**.
2. Pada bagian **Masukkan ZIP menjadi antrean review**, pilih ZIP korpus.
3. Pertahankan jumlah awal **50** dan centang **Proses lokal saja** untuk dokumen sensitif.
4. Tekan **Periksa & buat antrean**. ZIP yang tidak aman ditolak sebelum satu pun dokumen dianalisis.
5. Tunggu progress selesai. Halaman boleh ditutup atau direload; batch terakhir akan dimuat kembali otomatis.
6. Tekan **Mulai Review Terpandu**.

Sistem memilih dokumen beragam, melewati format yang belum didukung, tidak menghitung salinan identik dua kali, dan menampilkan file yang ditolak beserta alasannya. Mode lokal memastikan isi dokumen tidak dikirim ke DeepSeek/Sumopod. Matikan mode lokal hanya bila penggunaan provider eksternal untuk batch tersebut memang telah disetujui.

## Sebelum mulai

- Dokumen sudah dianalisis melalui intake ZIP di atas atau **Upload Evidence V2** dengan mode **Full Audit**.
- Reviewer memahami isi dokumen atau mengetahui parameter SPIP yang seharusnya.
- Gunakan nama/identitas reviewer yang konsisten. Pada produksi, identitas akan berasal dari login/SSO.

Review terpandu **tidak** mengesahkan rule domain, tidak mengaktifkan OCR/vision, dan tidak membuka upload produksi. Semua gate tersebut tetap terpisah dan fail-closed.

## Cara paling mudah

1. Buka aplikasi, lalu tekan **Review Terpandu** pada bagian atas halaman.
2. Lihat indikator kemajuan dan pilih antrean **Belum direview**.
3. Tekan **Buka dokumen** untuk membaca dokumen asli. Cocokkan isi dokumen dengan saran parameter dari sistem.
4. Pilih satu jawaban:
   - **Saran sudah benar**: parameter yang disarankan sistem memang sesuai.
   - **Perlu ganti parameter**: dokumen relevan, tetapi parameter sistem keliru.
   - **Bukan evidence**: dokumen tidak layak menjadi evidence untuk saran tersebut.
   - **Belum yakin**: informasi belum cukup atau perlu ditanyakan kepada orang lain.
5. Jika memilih **Saran sudah benar**, pilih satu saran parameter dan centang fakta/kutipan yang benar-benar mendukungnya.
6. Jika memilih **Perlu ganti parameter**, cari parameter resmi dengan kata sederhana, pilih parameter yang benar, lalu centang fakta/kutipan pendukung.
7. Untuk jawaban positif, pilih **Peran evidence** yang sudah diperiksa: `primary` bila bukti langsung menunjukkan pelaksanaan/hasil/perbaikan, `supporting` bila berupa kebijakan atau dukungan, `context` bila hanya memberi latar, dan `contradictory` bila justru menyangkal klaim. Nilai dari sistem hanya saran dan bukan keputusan grade.
8. Untuk semua jawaban selain **Belum yakin**, pilih apakah dokumen **memiliki isi substantif/aktivitas nyata** atau **hanya template, instruksi, atau kolom kosong**. Dokumen yang berisi form sekaligus data terisi dipilih sebagai substantif.
9. Isi **Nama reviewer**. Tambahkan alasan singkat, terutama untuk koreksi, penolakan, atau kondisi belum yakin.
10. Tekan **Simpan & lanjut**. Aplikasi menyimpan label dan membuka dokumen berikutnya.
11. Jika harus berhenti, cukup tutup halaman. Pilihan yang belum dikirim tersimpan otomatis di browser dan dokumen terakhir akan dibuka kembali.
12. Setelah selesai, tekan **Unduh Hasil Review** untuk mendapatkan berkas JSONL yang dapat divalidasi oleh pengelola evaluasi.

Di bagian akhir setiap dokumen terdapat daftar centang **Siap disimpan**. Tombol **Simpan & Lanjut** baru aktif setelah seluruh isian wajib lengkap. Anda tidak perlu memahami JSON, checksum, API, atau menjalankan perintah terminal.

## Pembagian tugas yang paling sederhana

- **Anda/reviewer pertama:** selesaikan sedikitnya 50 dokumen untuk holdout Evaluasi di **Review Terpandu**; siapkan dokumen berbeda bila juga ingin mengasah Learning. Bila ragu, pilih **Belum yakin**.
- **Domain owner/reviewer kedua:** buka **Governance V2 → Dataset Ahli**, buka dokumen, pilih tujuan **Evaluasi rilis** atau **Learning retrieval**, lalu pilih **Sahkan sebagai Expert Gold** atau **Kembalikan untuk Diperbaiki**. Orang kedua harus berbeda dari reviewer pertama; dokumen yang sama tidak boleh masuk kedua tujuan.
- **Aplikasi:** menyimpan riwayat, membuat lokasi sumber, menghitung checksum, lalu menghitung Recall@5, mapping precision, akurasi sumber, evidence-role accuracy, template detection, overgrade, abstention, latency, dan estimasi cost secara otomatis setelah label disahkan.

Dengan alur ini, pengguna nonteknis cukup membaca, memilih, mencentang, dan menyimpan. File hasil unduhan hanya cadangan/audit; Anda tidak perlu mengeditnya.

## Cara memilih sumber yang baik

Pilih fakta yang menyatakan bukti secara langsung, misalnya nama kebijakan, kegiatan yang dilakukan, periode, unit organisasi, atau hasil evaluasi. Lokasi halaman, sheet, baris, cell, atau slide dibuat otomatis oleh aplikasi dari fakta yang dicentang; reviewer tidak perlu mengetiknya.

Jangan memilih kutipan hanya karena memakai kata yang sama dengan parameter. Pastikan maknanya benar dan konteks organisasi serta periodenya sesuai. Periksa juga apakah bukti itu utama atau hanya pendukung, serta apakah dokumen benar-benar terisi dan bukan template kosong. Bila dokumen scan tidak terbaca, chart belum terurai, atau sumber tidak cukup jelas, pilih **Belum yakin** alih-alih menebak.

## Arti status hasil

| Status | Arti |
|---|---|
| `pilot_unlabelled` | Belum menjadi label ahli; termasuk jawaban **Belum yakin**. |
| `expert_candidate` | Sudah direview dan dapat diperiksa untuk dataset evaluasi. |
| `expert_gold` | Sudah melalui pemeriksaan/sign-off tambahan; wizard tidak menaikkan status ini secara otomatis. |

Riwayat perubahan tidak ditimpa. Jika jawaban diperbaiki, aplikasi menonaktifkan versi lama dan menyimpan versi baru beserta reviewer dan waktunya.

## Apa yang masih memerlukan pemilik domain

1. Memeriksa kandidat label dan menyetujui dataset `expert_gold`.
2. Mengesahkan checksum rule parameter-grade pada alur approval rule yang terpisah.
3. Menyetujui kebijakan pemrosesan OCR/vision, terutama untuk dokumen sensitif.
4. Menyetujui hasil pilot, canary, dan rilis produksi.

Untuk tahap pertama, targetkan 50 dokumen Evaluasi yang beragam. Dokumen Learning harus tambahan dan berbeda checksum; jangan mengurangi 50 Evaluasi untuk memenuhi kebutuhan learning. Lebih baik memilih jawaban **Belum yakin** daripada membuat label yang dipaksakan.
