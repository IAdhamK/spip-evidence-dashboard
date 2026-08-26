export const FILE_SEARCH_SUGGESTIONS = [
  "IKPA",
  "KPPN",
  "Petris",
  "RTP",
  "SAKIP",
  "Manajemen Risiko",
  "Pelaksanaan Anggaran",
  "465",
];

export const FILE_TYPE_OPTIONS = [
  ["all", "Semua jenis"],
  ["pdf", "PDF"],
  ["word", "Word"],
  ["spreadsheet", "Spreadsheet"],
  ["presentation", "Presentasi"],
  ["image", "Gambar"],
  ["text", "Teks"],
];

export function buildFileSearchPath({ query, kkId = "all", fileType = "all", grade = "all", limit = 50 }) {
  const params = new URLSearchParams({ q: String(query ?? "").trim(), limit: String(limit) });
  if (kkId && kkId !== "all") params.set("kk_id", kkId);
  if (fileType && fileType !== "all") params.set("file_type", fileType);
  if (grade && grade !== "all") params.set("grade", grade);
  return `/api/files/search?${params.toString()}`;
}

export function fileTypeLabel(value) {
  return FILE_TYPE_OPTIONS.find(([key]) => key === value)?.[1] ?? "File";
}

export function evidenceLocationLabel(result = {}) {
  const parts = [result.kk_id, result.kode];
  if (result.detail_kode) parts.push(`Parameter ${result.detail_kode}`);
  if (result.grade) parts.push(`Grade ${result.grade}`);
  return parts.filter(Boolean).join(" · ");
}

export function fileSearchPromptState(query, response, loading = false) {
  const normalized = String(query ?? "").trim();
  if (loading) return { kind: "loading", text: "Mencari pada indeks hasil sinkronisasi..." };
  if (!normalized) return { kind: "idle", text: "Ketik nama file, nomor surat, kegiatan, atau kata kunci." };
  if (normalized.length < 2) return { kind: "invalid", text: "Masukkan minimal dua karakter." };
  if (response && response.total === 0) {
    return { kind: "empty", text: `Belum ada nama atau lokasi file yang cocok dengan “${normalized}”.` };
  }
  if (response) return { kind: "ready", text: `${response.total} file ditemukan pada indeks sinkronisasi.` };
  return { kind: "idle", text: "Tekan Cari untuk memulai pencarian." };
}

export function secondaryFileName(result = {}) {
  const remotePath = String(result.remote_path ?? "");
  const baseName = String(result.base_name ?? "");
  if (!remotePath || remotePath === baseName) return "";
  return remotePath.endsWith(`/${baseName}`)
    ? remotePath.slice(0, -(baseName.length + 1))
    : remotePath;
}
