from dataclasses import dataclass


@dataclass(frozen=True)
class Subunsur:
    kode: str
    nama: str
    unsur: str
    evidence_hint: str


@dataclass(frozen=True)
class Kk:
    id: str
    title: str
    folder_name: str
    description: str


KK_LIST: list[Kk] = [
    Kk(
        id="KK3.1",
        title="KK 3.1 Efektivitas dan Efisiensi Pencapaian Tujuan Organisasi",
        folder_name="KK 3.1 EFEKTIVITAS DAN EFISIENSI PENCAPAIAN TUJUAN ORGANISASI",
        description="Evidence untuk kecukupan pengendalian atas pencapaian tujuan organisasi.",
    ),
    Kk(
        id="KK3.2",
        title="KK 3.2 Keandalan Pelaporan Keuangan",
        folder_name="KK 3.2 KEANDALAN PELAPORAN KEUANGAN",
        description="Evidence untuk kecukupan pengendalian atas pelaporan keuangan.",
    ),
    Kk(
        id="KK3.3",
        title="KK 3.3 Pengamanan Aset Negara/Daerah",
        folder_name="KK 3.3 PENGAMANAN ASET NEGARA DAERAH",
        description="Evidence untuk kecukupan pengendalian atas pengamanan aset.",
    ),
    Kk(
        id="KK3.4",
        title="KK 3.4 Ketaatan pada Peraturan Perundang-undangan",
        folder_name="KK 3.4 KETAATAN PADA PERATURAN PERUNDANG UNDANGAN",
        description="Evidence untuk kecukupan pengendalian atas ketaatan pada peraturan perundang-undangan.",
    ),
]


SUBUNSUR_LIST: list[Subunsur] = [
    Subunsur("1.1", "Penegakan Integritas dan Nilai Etika", "Lingkungan Pengendalian", "Pakta integritas, kode etik, sosialisasi nilai, bukti penanganan pelanggaran."),
    Subunsur("1.2", "Komitmen terhadap Kompetensi", "Lingkungan Pengendalian", "Standar kompetensi, sertifikat diklat, rencana pengembangan pegawai, evaluasi kompetensi."),
    Subunsur("1.3", "Kepemimpinan yang Kondusif", "Lingkungan Pengendalian", "Arahan pimpinan, notulen rapat, disposisi, tindak lanjut arahan strategis."),
    Subunsur("1.4", "Struktur Organisasi Sesuai Kebutuhan", "Lingkungan Pengendalian", "OTK, struktur organisasi, peta jabatan, uraian tugas, evaluasi kelembagaan."),
    Subunsur("1.5", "Pendelegasian Wewenang dan Tanggung Jawab yang Tepat", "Lingkungan Pengendalian", "SK penugasan, SOP, matriks peran, disposisi, bukti pelaksanaan delegasi."),
    Subunsur("1.6", "Penyusunan dan Penerapan Kebijakan yang Sehat tentang Pembinaan SDM", "Lingkungan Pengendalian", "Kebijakan SDM, ABK, pembinaan pegawai, evaluasi kinerja, tindak lanjut pembinaan."),
    Subunsur("1.7", "Perwujudan Peran APIP yang Efektf", "Lingkungan Pengendalian", "Surat reviu, laporan APIP, rekomendasi, matriks tindak lanjut pengawasan."),
    Subunsur("1.8", "Hubungan Kerja yang Baik dengan Instansi Pemerintah Terkait", "Lingkungan Pengendalian", "MoU/PKS, surat koordinasi, notulen lintas instansi, laporan kerja sama."),
    Subunsur("2.1", "Identifikasi Risiko", "Penilaian Risiko", "Register risiko, profil risiko, undangan pembahasan risiko, notulen identifikasi risiko."),
    Subunsur("2.2", "Analisis Risiko", "Penilaian Risiko", "Matriks risiko, peta risiko, analisis dampak/kemungkinan, rencana respons risiko."),
    Subunsur("3.1", "Reviu atas Kinerja", "Kegiatan Pengendalian", "Laporan kinerja, evaluasi IKU, notulen reviu capaian, matriks tindak lanjut kinerja."),
    Subunsur("3.2", "Pembinaan SDM", "Kegiatan Pengendalian", "Dokumen pembinaan, coaching, mentoring, diklat, penilaian kinerja pegawai."),
    Subunsur("3.3", "Pengendalian atas Pengelolaan Sistem Informasi", "Kegiatan Pengendalian", "SOP sistem informasi, daftar akses, backup, log perubahan, dokumentasi aplikasi."),
    Subunsur("3.4", "Pengendalian Fisik atas Aset", "Kegiatan Pengendalian", "KIB/BMN, label aset, BA pemeriksaan, dokumentasi pengamanan aset."),
    Subunsur("3.5", "Penetapan dan Reviu atas Indikator dan Ukuran Kinerja", "Kegiatan Pengendalian", "Renja, PK, IKU, laporan evaluasi indikator, bahan reviu capaian."),
    Subunsur("3.6", "Pemisahan Fungsi", "Kegiatan Pengendalian", "SK tim, uraian tugas, SOP proses bisnis, matriks pembagian kewenangan."),
    Subunsur("3.7", "Otorisasi atas Transaksi dan Kejadian yang Penting", "Kegiatan Pengendalian", "Nota persetujuan, surat tugas, disposisi, BA kegiatan, dokumen otorisasi."),
    Subunsur("3.8", "Pencatatan yang Akurat dan Tepat Waktu atas Transaksi dan Kejadian", "Kegiatan Pengendalian", "Logbook, rekap data, laporan bulanan, BA validasi, bukti input aplikasi."),
    Subunsur("3.9", "Pembatasan Akses atas Sumber Daya dan Pencatatannya", "Kegiatan Pengendalian", "Daftar user, hak akses aplikasi, SOP akses, dokumentasi pengamanan."),
    Subunsur("3.10", "Akuntabilitas terhadap Sumber Daya dan Pencatatannya", "Kegiatan Pengendalian", "Laporan BMN, laporan kegiatan, pertanggungjawaban anggaran, BA serah terima."),
    Subunsur("3.11", "Dokumentasi yang Baik atas SPI serta Transaksi dan Kejadian Penting", "Kegiatan Pengendalian", "SOP dokumentasi, arsip transaksi penting, register dokumen, bukti penyimpanan."),
    Subunsur("4.1", "Informasi yang Relevan", "Informasi dan Komunikasi", "Dashboard data, laporan informasi, SOP pengelolaan data, validasi informasi."),
    Subunsur("4.2", "Komunikasi yang Efektif", "Informasi dan Komunikasi", "Notulen, surat edaran, kanal komunikasi, dokumentasi koordinasi, tindak lanjut komunikasi."),
    Subunsur("5.1", "Pemantauan Berkelanjutan", "Pemantauan", "Laporan monitoring rutin, dashboard pemantauan, rapat evaluasi, tindak lanjut hasil pemantauan."),
    Subunsur("5.2", "Evaluasi Terpisah", "Pemantauan", "Laporan evaluasi terpisah, hasil audit/reviu, rekomendasi, matriks tindak lanjut evaluasi."),
]


STATUS_EXPLANATIONS = {
    "Kosong": "Belum ada file evidence pada folder ini.",
    "Terisi Sebagian": "Sudah ada 1-3 file, tetapi belum memenuhi acuan minimal empat kategori evidence.",
    "Terisi": "Sudah ada minimal 4 file evidence. Substansinya tetap perlu dicek koordinator.",
    "Perlu Kurasi": "Ada file dengan nama terlalu umum, ambigu, atau berpotensi tidak mudah ditelusuri.",
    "Final": "Sudah diverifikasi manual oleh koordinator.",
}


EVIDENCE_CATEGORIES = [
    {
        "name": "Dasar Kebijakan",
        "description": "SK, SOP, pedoman, peraturan, surat edaran, atau dasar formal lainnya.",
    },
    {
        "name": "Bukti Pelaksanaan",
        "description": "Undangan, notulen, daftar hadir, berita acara, dokumen kerja, atau dokumentasi kegiatan.",
    },
    {
        "name": "Monitoring/Reviu",
        "description": "Laporan evaluasi, laporan reviu, hasil pemantauan, rekap capaian, atau bahan pembahasan.",
    },
    {
        "name": "Tindak Lanjut",
        "description": "Matriks tindak lanjut, bukti perbaikan, surat tindak lanjut, atau laporan penyelesaian.",
    },
]


def kk_by_id(kk_id: str) -> Kk | None:
    return next((item for item in KK_LIST if item.id == kk_id), None)


def subunsur_by_kode(kode: str) -> Subunsur | None:
    return next((item for item in SUBUNSUR_LIST if item.kode == kode), None)
