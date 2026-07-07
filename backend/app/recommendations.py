from __future__ import annotations

import re
from typing import Any


KK_CONTEXTS = {
    "KK3.1": {
        "label": "tata kelola umum, SPIP, manajemen risiko, dan koordinasi Ditjen PDP",
        "keywords": [
            "SPIP",
            "manajemen risiko",
            "SOP",
            "SK tim",
            "struktur organisasi",
            "koordinasi",
            "Renstra",
            "IKU",
            "laporan kinerja",
            "APIP",
            "BPKP",
        ],
    },
    "KK3.2": {
        "label": "keuangan Ditjen PDP",
        "keywords": [
            "anggaran",
            "RKA-K/L",
            "DIPA",
            "POK",
            "SPM",
            "SP2D",
            "realisasi anggaran",
            "laporan keuangan",
            "rekonsiliasi keuangan",
            "pengendalian belanja",
        ],
    },
    "KK3.3": {
        "label": "aset dan BMN Ditjen PDP",
        "keywords": [
            "BMN",
            "aset",
            "inventarisasi",
            "KIB",
            "DBR",
            "SIMAK-BMN",
            "stock opname",
            "pemeliharaan aset",
            "rekonsiliasi BMN",
        ],
    },
    "KK3.4": {
        "label": "ketaatan terhadap peraturan perundang-undangan",
        "keywords": [
            "kepatuhan",
            "peraturan",
            "regulasi",
            "SOP kepatuhan",
            "reviu kepatuhan",
            "tindak lanjut temuan",
        ],
    },
}


GRADE_RULES = {
    "E": {
        "meaning": "baru ada kebijakan",
        "stage": "kebijakan",
        "chain": ["Bukti kebijakan"],
        "warning": "Dokumen kebijakan hanya cukup untuk membuktikan Grade E. Jangan menaikkan grade tanpa bukti sosialisasi, implementasi, evaluasi, atau perbaikan.",
        "not_sufficient": [
            "Judul dokumen yang terlihat formal tetapi tidak memuat kebijakan yang berlaku",
            "Draft kebijakan yang belum ditetapkan",
            "Dokumentasi kegiatan tanpa dasar kebijakan",
        ],
    },
    "D": {
        "meaning": "kebijakan telah disosialisasikan",
        "stage": "sosialisasi",
        "chain": ["Bukti kebijakan", "Bukti sosialisasi"],
        "warning": "Grade D belum kuat jika hanya ada kebijakan tanpa bukti bahwa kebijakan sudah disampaikan dan dipahami pihak terkait.",
        "not_sufficient": [
            "SK/SOP saja tanpa bukti penyampaian",
            "Undangan rapat tanpa daftar hadir, bahan, atau notulen",
            "Dokumentasi kegiatan yang tidak menunjukkan sosialisasi kebijakan",
        ],
    },
    "C": {
        "meaning": "kebijakan telah diimplementasikan",
        "stage": "implementasi",
        "chain": ["Bukti kebijakan", "Bukti implementasi"],
        "warning": "Grade C harus menunjukkan praktik berjalan. Dokumen rencana, pedoman, atau undangan saja belum cukup.",
        "not_sufficient": [
            "Rencana kerja tanpa output pelaksanaan",
            "Bahan sosialisasi tanpa bukti kegiatan berjalan",
            "Matriks kosong atau belum terisi",
        ],
    },
    "B": {
        "meaning": "kebijakan dan pelaksanaan sudah dievaluasi berkala",
        "stage": "evaluasi",
        "chain": ["Bukti kebijakan", "Bukti implementasi", "Bukti evaluasi berkala"],
        "warning": "Grade B membutuhkan bukti kebijakan, implementasi, dan evaluasi berkala. Evaluasi tanpa bukti implementasi perlu dilengkapi.",
        "not_sufficient": [
            "Laporan evaluasi tanpa bukti kegiatan yang dievaluasi",
            "Rekap capaian satu kali tanpa pola berkala",
            "Notulen rapat umum tanpa hasil evaluasi terdokumentasi",
        ],
    },
    "A": {
        "meaning": "hasil evaluasi digunakan untuk perbaikan organisasi",
        "stage": "perbaikan",
        "chain": [
            "Bukti kebijakan",
            "Bukti implementasi",
            "Bukti evaluasi",
            "Bukti tindak lanjut/perbaikan organisasi",
        ],
        "warning": "Grade A membutuhkan hubungan jelas antara hasil evaluasi dan tindakan perbaikan. Tindak lanjut yang tidak merujuk hasil evaluasi belum cukup.",
        "not_sufficient": [
            "Tindak lanjut yang tidak terkait hasil evaluasi",
            "Rencana perbaikan tanpa bukti pelaksanaan",
            "Revisi dokumen yang tidak menunjukkan alasan evaluasi",
        ],
    },
}


GENERIC_TOPIC = {
    "label": "parameter ini",
    "kebijakan": [
        "SK, SOP, pedoman, atau kebijakan yang mengatur parameter terkait",
        "Surat edaran atau panduan internal yang menjadi dasar pelaksanaan",
        "Standar kerja, mekanisme, atau aturan internal yang sudah ditetapkan",
    ],
    "sosialisasi": [
        "Undangan sosialisasi kebijakan atau mekanisme terkait parameter",
        "Daftar hadir, bahan paparan, notulen, atau dokumentasi penyampaian kebijakan",
        "Nota dinas/surat penyampaian kebijakan kepada unit atau pegawai terkait",
    ],
    "implementasi": [
        "Laporan pelaksanaan, output kegiatan, atau dokumen hasil penerapan parameter",
        "Matriks/rekap yang sudah terisi dan menunjukkan kegiatan berjalan",
        "Bukti penggunaan SOP, mekanisme, atau prosedur dalam pelaksanaan tugas",
    ],
    "evaluasi": [
        "Laporan monitoring/evaluasi berkala atas pelaksanaan parameter",
        "Rekap hasil pemantauan, reviu APIP/BPKP, atau notisi hasil evaluasi",
        "Notulen rapat evaluasi yang memuat hasil, masalah, dan rekomendasi",
    ],
    "perbaikan": [
        "Matriks tindak lanjut hasil evaluasi",
        "Revisi kebijakan/SOP, perubahan proses, atau keputusan pimpinan berdasarkan evaluasi",
        "Bukti perbaikan sistem, mekanisme, atau tata kelola organisasi",
    ],
    "examples": {
        "kebijakan": "SK atau SOP {topic} Ditjen PDP 2026.pdf",
        "sosialisasi": "Daftar Hadir Sosialisasi {topic} 2026.pdf",
        "implementasi": "Laporan Pelaksanaan {topic} Semester I 2026.pdf",
        "evaluasi": "Laporan Evaluasi {topic} Triwulan II 2026.pdf",
        "perbaikan": "Matriks Tindak Lanjut Evaluasi {topic} 2026.xlsx",
    },
}


TOPIC_RULES = [
    {
        "keys": ("integritas", "etika", "pakta", "disiplin", "reward", "punishment"),
        "label": "integritas dan nilai etika",
        "kebijakan": [
            "SK/SOP/kebijakan kode etik, pakta integritas, atau penegakan disiplin",
            "Pedoman perilaku pegawai atau mekanisme penanganan pelanggaran integritas",
            "Surat edaran nilai etika, reward and punishment, atau pembangunan integritas",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi kode etik",
            "Dokumentasi penyampaian pakta integritas atau nilai etika kepada pegawai",
            "Nota dinas penyampaian kebijakan integritas kepada unit kerja",
        ],
        "implementasi": [
            "Pakta integritas yang sudah ditandatangani atau rekap penerapannya",
            "Bukti penegakan disiplin, penanganan pelanggaran, reward, atau punishment",
            "Laporan pelaksanaan pembinaan integritas dan nilai etika",
        ],
        "evaluasi": [
            "Laporan evaluasi penerapan kode etik atau pakta integritas",
            "Rekap monitoring pelanggaran integritas dan tindak lanjutnya",
            "Notulen rapat evaluasi nilai etika atau hasil reviu APIP/BPKP",
        ],
        "perbaikan": [
            "Matriks tindak lanjut evaluasi integritas",
            "Revisi SOP/kebijakan kode etik berdasarkan hasil evaluasi",
            "Keputusan pimpinan atau bukti perubahan proses penanganan pelanggaran",
        ],
    },
    {
        "keys": ("risiko", "manajemen risiko", "peta risiko", "rtp", "pengendalian risiko", "register risiko"),
        "label": "manajemen risiko",
        "kebijakan": [
            "SK Tim Manajemen Risiko, pedoman MR, atau SOP identifikasi dan analisis risiko",
            "Kebijakan selera risiko, penetapan konteks risiko, atau rencana pengendalian risiko",
            "Surat edaran/panduan pengisian register risiko dan RTP",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi manajemen risiko",
            "Dokumentasi bimbingan teknis pengisian peta risiko/register risiko",
            "Nota dinas penyampaian pedoman MR kepada UKE/unit kerja",
        ],
        "implementasi": [
            "Register risiko, peta risiko, matriks analisis risiko, atau RTP yang sudah terisi",
            "Dokumen penetapan konteks risiko, pemilik risiko, pengendalian yang ada, dan risiko residual",
            "Laporan pelaksanaan mitigasi atau pengendalian risiko",
        ],
        "evaluasi": [
            "Laporan monitoring/evaluasi manajemen risiko berkala",
            "Laporan MR semesteran/triwulanan atau rekap progres RTP",
            "Hasil reviu APIP/BPKP atas penerapan manajemen risiko",
        ],
        "perbaikan": [
            "Matriks tindak lanjut hasil evaluasi MR atau pemutakhiran RTP",
            "Revisi peta risiko/register risiko berdasarkan hasil evaluasi",
            "Keputusan pimpinan tentang perbaikan pengendalian risiko",
        ],
    },
    {
        "keys": ("aset", "bmn", "inventaris", "kib", "dbr", "simak", "stock opname", "pemeliharaan", "kendaraan", "gedung"),
        "label": "pengelolaan aset/BMN",
        "kebijakan": [
            "SOP pengelolaan, pengamanan, inventarisasi, atau pemeliharaan BMN",
            "SK pengelola BMN atau petugas inventarisasi aset",
            "Pedoman penggunaan kendaraan, gedung, peralatan, atau aset lainnya",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi pengelolaan BMN",
            "Dokumentasi bimtek SIMAK-BMN, inventarisasi, atau pengamanan aset",
            "Nota dinas penyampaian kebijakan penggunaan/pemeliharaan aset",
        ],
        "implementasi": [
            "KIB, DBR, laporan inventarisasi BMN, BA stock opname, atau rekap SIMAK-BMN",
            "Bukti pemeliharaan, penggunaan, pengamanan, penghapusan, atau pemindahtanganan aset",
            "Dokumen rekonsiliasi atau pemutakhiran data aset/BMN",
        ],
        "evaluasi": [
            "Laporan evaluasi pengamanan aset atau monitoring BMN berkala",
            "Laporan rekonsiliasi BMN, hasil stock opname, atau evaluasi pemeliharaan aset",
            "Notulen evaluasi pengelolaan BMN dan rekomendasi tindak lanjut",
        ],
        "perbaikan": [
            "Tindak lanjut hasil rekonsiliasi BMN atau stock opname",
            "Perbaikan data aset, revisi SOP pengamanan aset, atau keputusan penghapusan/pemindahtanganan",
            "Bukti perbaikan sistem pencatatan, pengamanan, atau pemeliharaan aset",
        ],
    },
    {
        "keys": ("kinerja", "capaian", "tolok ukur", "indikator", "iku", "renstra", "reviu kinerja", "laporan kinerja"),
        "label": "reviu dan capaian kinerja",
        "kebijakan": [
            "Pedoman reviu kinerja, SOP pemantauan kinerja, atau kebijakan pengukuran capaian",
            "Dokumen IKU, Renstra, perjanjian kinerja, atau target kinerja yang ditetapkan",
            "SK/tim atau mekanisme pelaporan dan reviu kinerja",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi mekanisme reviu kinerja",
            "Dokumentasi penyampaian target/indikator kinerja kepada unit kerja",
            "Nota dinas arahan pengukuran, pelaporan, atau reviu capaian kinerja",
        ],
        "implementasi": [
            "Laporan kinerja, rekap capaian indikator, atau matriks monitoring kinerja yang sudah terisi",
            "Dokumen pembandingan target dan realisasi kinerja secara periodik",
            "Notulen rapat reviu kinerja yang memuat hambatan, strategi, dan tindak lanjut awal",
        ],
        "evaluasi": [
            "Laporan evaluasi/reviu kinerja berkala",
            "Rekap hasil pemantauan capaian kinerja triwulan/semesteran",
            "Hasil reviu APIP/BPKP atau notulen evaluasi atas capaian kinerja",
        ],
        "perbaikan": [
            "Matriks tindak lanjut hasil evaluasi kinerja",
            "Perubahan strategi, target, proses bisnis, atau keputusan pimpinan berdasarkan reviu kinerja",
            "Bukti perbaikan kinerja periode berikutnya yang merujuk hasil evaluasi",
        ],
    },
    {
        "keys": ("anggaran", "keuangan", "rka", "dipa", "pok", "spm", "sp2d", "belanja", "laporan keuangan", "realisasi"),
        "label": "pengelolaan keuangan",
        "kebijakan": [
            "SOP penyusunan anggaran, pelaksanaan anggaran, atau pertanggungjawaban keuangan",
            "SK pengelola keuangan, PPK, PPSPM, bendahara, atau tim pelaksana anggaran",
            "Pedoman rekonsiliasi, pengendalian belanja, RKA-K/L, DIPA, atau POK",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi pengelolaan keuangan",
            "Dokumentasi bimtek penyusunan RKA-K/L, DIPA/POK, atau pertanggungjawaban belanja",
            "Nota dinas penyampaian kebijakan pengendalian anggaran",
        ],
        "implementasi": [
            "RKA-K/L, DIPA, POK, laporan realisasi anggaran, SPM, SP2D, atau dokumen belanja",
            "Bukti rekonsiliasi keuangan, pertanggungjawaban belanja, kontrak, atau BAP",
            "Rekap pelaksanaan anggaran dan pengendalian belanja",
        ],
        "evaluasi": [
            "Laporan evaluasi realisasi anggaran atau monitoring belanja berkala",
            "Hasil reviu RKA-K/L, laporan keuangan, atau rekonsiliasi berkala",
            "Notulen evaluasi pelaksanaan anggaran dan rekomendasi tindak lanjut",
        ],
        "perbaikan": [
            "Tindak lanjut hasil evaluasi belanja atau laporan keuangan",
            "Revisi POK, perbaikan SOP, atau keputusan pimpinan berdasarkan evaluasi keuangan",
            "Bukti perbaikan proses pertanggungjawaban atau pengendalian anggaran",
        ],
    },
    {
        "keys": ("kompetensi", "sdm", "pegawai", "jabatan", "pelatihan", "diklat", "pembinaan"),
        "label": "kompetensi dan pembinaan SDM",
        "kebijakan": [
            "SK/SOP pengelolaan kompetensi, analisis kebutuhan pelatihan, atau pembinaan SDM",
            "Standar kompetensi jabatan, peta jabatan, atau uraian tugas",
            "Pedoman pengembangan kompetensi pegawai",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi pengembangan kompetensi",
            "Dokumentasi penyampaian standar kompetensi atau uraian jabatan",
            "Nota dinas pembinaan SDM atau arahan pemenuhan kompetensi",
        ],
        "implementasi": [
            "Matriks kompetensi, rekap pelatihan, sertifikat, atau laporan pelaksanaan pembinaan SDM",
            "Dokumen pengisian jabatan, uraian tugas, atau bukti pelaksanaan pengembangan kompetensi",
            "Output kegiatan peningkatan kompetensi pegawai",
        ],
        "evaluasi": [
            "Laporan evaluasi kompetensi atau monitoring pengembangan SDM berkala",
            "Rekap gap kompetensi, hasil evaluasi pelatihan, atau reviu pemenuhan jabatan",
            "Notulen evaluasi pembinaan SDM dan rekomendasi tindak lanjut",
        ],
        "perbaikan": [
            "Tindak lanjut hasil evaluasi kompetensi atau gap SDM",
            "Revisi rencana pelatihan, penyesuaian uraian tugas, atau keputusan pimpinan atas evaluasi SDM",
            "Bukti perbaikan proses pembinaan dan pengembangan kompetensi",
        ],
    },
    {
        "keys": ("peraturan", "regulasi", "kepatuhan", "ketaatan", "temuan", "perundang", "fraud", "kecurangan", "pengaduan", "informasi relevan"),
        "label": "ketaatan regulasi dan pencegahan fraud",
        "kebijakan": [
            "Daftar regulasi, SOP kepatuhan, pedoman pemantauan peraturan, atau kebijakan internal terkait",
            "SK/tim kepatuhan atau mekanisme pengendalian ketaatan peraturan",
            "Surat edaran penerapan regulasi baru kepada unit terkait",
        ],
        "sosialisasi": [
            "Undangan, daftar hadir, bahan paparan, dan notulen sosialisasi regulasi",
            "Dokumentasi penyampaian peraturan atau kebijakan kepatuhan kepada pegawai/unit kerja",
            "Nota dinas tindak lanjut penerapan peraturan perundang-undangan",
        ],
        "implementasi": [
            "Matriks kepatuhan, register peraturan, bukti pelaksanaan SOP kepatuhan, atau dokumen penerapan regulasi",
            "Laporan pelaksanaan pemenuhan kewajiban peraturan",
            "Rekap tindak lanjut ketentuan atau persyaratan regulasi",
        ],
        "evaluasi": [
            "Laporan reviu kepatuhan atau evaluasi penerapan peraturan berkala",
            "Rekap temuan kepatuhan, hasil pemantauan regulasi, atau reviu APIP/BPKP",
            "Notulen evaluasi kepatuhan dan rekomendasi tindak lanjut",
        ],
        "perbaikan": [
            "Matriks tindak lanjut temuan kepatuhan",
            "Revisi SOP/kebijakan berdasarkan hasil reviu kepatuhan",
            "Bukti perbaikan proses agar selaras dengan peraturan",
        ],
    },
]


def attach_recommendations(parameters: list[dict[str, Any]]) -> None:
    for parameter in parameters:
        for grade in parameter.get("grades", []):
            grade["recommendation"] = build_recommendation(parameter, grade)


def build_recommendation(parameter: dict[str, Any], grade: dict[str, Any]) -> dict[str, Any]:
    grade_value = str(grade.get("grade") or parameter.get("grade_sample") or "").upper()
    grade_rule = GRADE_RULES.get(grade_value, GRADE_RULES["E"])
    kk_id = str(parameter.get("kk_id") or "")
    kk_context = KK_CONTEXTS.get(kk_id, KK_CONTEXTS["KK3.1"])
    topic = detect_topic(parameter, grade)
    stage = grade_rule["stage"]

    primary_files = unique(topic.get(stage, []) + stage_context_files(kk_id, stage, kk_context))
    supporting_files = unique(build_supporting_files(topic, grade_value, kk_context))
    example_filenames = build_example_filenames(topic["label"], stage, grade_value)
    not_sufficient = unique(grade_rule["not_sufficient"] + generic_not_sufficient(stage))

    return {
        "title": f"Rekomendasi Grade {grade_value}",
        "summary": (
            f"Upload bukti untuk menunjukkan bahwa {topic['label']} pada konteks "
            f"{kk_context['label']} sudah berada pada tahap {grade_rule['meaning']}."
        ),
        "primary_files": primary_files[:5],
        "supporting_files": supporting_files[:5],
        "example_filenames": example_filenames,
        "evidence_chain": grade_rule["chain"],
        "warning": grade_rule["warning"],
        "not_sufficient": not_sufficient[:4],
        "source": "hybrid-rule-context",
    }


def detect_topic(parameter: dict[str, Any], grade: dict[str, Any]) -> dict[str, Any]:
    haystack = " ".join(
        str(value or "")
        for value in (
            parameter.get("matrix_subunsur_name"),
            parameter.get("uraian"),
            grade.get("kriteria"),
            grade.get("penjelasan"),
        )
    ).lower()
    best_topic: dict[str, Any] | None = None
    best_score = 0
    for topic in TOPIC_RULES:
        score = sum(1 for key in topic["keys"] if keyword_matches(haystack, key))
        if score > best_score:
            best_topic = topic
            best_score = score
    return best_topic or GENERIC_TOPIC


def keyword_matches(haystack: str, keyword: str) -> bool:
    key = keyword.lower().strip()
    if not key:
        return False
    if len(key) <= 4 and key.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack) is not None
    return key in haystack


def stage_context_files(kk_id: str, stage: str, kk_context: dict[str, Any]) -> list[str]:
    if kk_id == "KK3.2" and stage in {"implementasi", "evaluasi", "perbaikan"}:
        return [
            "Rekap pendukung keuangan sesuai konteks parameter, seperti anggaran, realisasi, rekonsiliasi, atau pertanggungjawaban",
        ]
    if kk_id == "KK3.3" and stage in {"implementasi", "evaluasi", "perbaikan"}:
        return [
            "Rekap pendukung aset/BMN sesuai konteks parameter, seperti inventarisasi, pemeliharaan, rekonsiliasi, atau pengamanan",
        ]
    if kk_id == "KK3.4" and stage in {"implementasi", "evaluasi", "perbaikan"}:
        return [
            "Rekap pemenuhan ketentuan atau tindak lanjut kepatuhan sesuai peraturan yang menjadi acuan",
        ]
    if stage == "kebijakan":
        keywords = ", ".join(kk_context.get("keywords", [])[:4])
        return [f"Dokumen dasar yang relevan dengan konteks {keywords}"]
    return []


def build_supporting_files(topic: dict[str, Any], grade_value: str, kk_context: dict[str, Any]) -> list[str]:
    if grade_value == "E":
        return [
            "Lampiran, daftar distribusi, atau dokumen pendukung penetapan kebijakan",
            f"Dokumen rujukan yang menunjukkan konteks {kk_context['label']}",
        ]
    if grade_value == "D":
        return topic.get("kebijakan", [])[:2]
    if grade_value == "C":
        return unique(topic.get("kebijakan", [])[:2] + topic.get("sosialisasi", [])[:2])
    if grade_value == "B":
        return unique(topic.get("kebijakan", [])[:2] + topic.get("implementasi", [])[:3])
    if grade_value == "A":
        return unique(topic.get("kebijakan", [])[:1] + topic.get("implementasi", [])[:2] + topic.get("evaluasi", [])[:3])
    return []


def build_example_filenames(topic_label: str, stage: str, grade_value: str) -> list[str]:
    label = title_case(topic_label)
    examples = {
        "kebijakan": [
            f"SK atau SOP {label} Ditjen PDP 2026.pdf",
            f"Pedoman {label} Ditjen PDP 2026.pdf",
        ],
        "sosialisasi": [
            f"Daftar Hadir Sosialisasi {label} 2026.pdf",
            f"Materi Sosialisasi {label} Ditjen PDP 2026.pptx",
        ],
        "implementasi": [
            f"Laporan Pelaksanaan {label} Semester I 2026.pdf",
            f"Rekap Implementasi {label} 2026.xlsx",
        ],
        "evaluasi": [
            f"Laporan Evaluasi {label} Triwulan II 2026.pdf",
            f"Rekap Monitoring {label} Semester I 2026.xlsx",
        ],
        "perbaikan": [
            f"Matriks Tindak Lanjut Evaluasi {label} 2026.xlsx",
            f"Nota Dinas Perbaikan {label} Berdasarkan Evaluasi 2026.pdf",
        ],
    }
    return examples.get(stage, [f"Evidence Grade {grade_value} {label} 2026.pdf"])


def generic_not_sufficient(stage: str) -> list[str]:
    if stage == "evaluasi":
        return ["Dokumen implementasi tanpa hasil monitoring/evaluasi berkala"]
    if stage == "perbaikan":
        return ["Bukti perbaikan yang tidak merujuk pada hasil evaluasi sebelumnya"]
    if stage == "implementasi":
        return ["Kebijakan atau sosialisasi yang belum menunjukkan pelaksanaan nyata"]
    if stage == "sosialisasi":
        return ["Dokumen kebijakan yang belum terbukti disampaikan kepada pihak terkait"]
    return ["Nama file yang terlihat relevan tetapi isi dokumen tidak mendukung parameter"]


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = " ".join(str(item).split())
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result


def title_case(value: str) -> str:
    return " ".join(part[:1].upper() + part[1:] for part in value.split())
