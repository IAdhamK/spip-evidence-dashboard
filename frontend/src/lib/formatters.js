export function formatNumber(value) {
  return new Intl.NumberFormat("id-ID").format(Math.round(Number(value) || 0));
}

export function formatUsdRange(value) {
  if (!value?.low && !value?.high) return "Belum tersedia";
  const low = Number(value.low || 0);
  const high = Number(value.high || low);
  if (low === high) return `$${low.toFixed(7)}`;
  return `$${low.toFixed(7)}-${high.toFixed(7)}`;
}

export function formatBytes(value) {
  if (!value) return "0 KB";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}

export function formatDate(value) {
  if (!value) return "Belum ada tanggal";
  try {
    return new Intl.DateTimeFormat("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}
