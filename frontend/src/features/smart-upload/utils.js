export function noticeToneForAi(status) {
  if (status === "ok") return "info";
  if (status === "unavailable" || status === "skipped") return "neutral";
  return "danger";
}

export function clampCandidateLimit(value) {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return 1;
  return Math.max(1, Math.min(100, parsed));
}
