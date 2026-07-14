export function formatSourceLocation(location, unitKey) {
  if (!location) return unitKey || "Lokasi sumber belum tersedia";
  const parts = [];
  if (location.page) parts.push(`Halaman ${location.page}`);
  if (location.sheet) parts.push(`Sheet ${location.sheet}`);
  if (location.slide) parts.push(`Slide ${location.slide}`);
  if (location.block) parts.push(`Blok ${location.block}`);
  if (location.table) parts.push(`Tabel ${location.table}`);
  if (location.row) parts.push(`Baris tabel ${location.row}`);
  if (location.cell) parts.push(`Sel ${location.cell}`);
  if (location.line_start) parts.push(`Baris ${location.line_start}-${location.line_end || location.line_start}`);
  return [...parts, unitKey].filter(Boolean).join(" · ") || "Lokasi sumber belum tersedia";
}
