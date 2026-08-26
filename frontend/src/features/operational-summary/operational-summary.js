export function operationalSummaryView(dashboard = {}) {
  const folders = Array.isArray(dashboard.folders) ? dashboard.folders : [];
  const provided = dashboard.operational_summary ?? {};
  const scannedDates = folders.map((folder) => folder.last_scanned_at).filter(Boolean).sort();

  return {
    available: folders.length > 0 || Boolean(dashboard.operational_summary),
    lastCheckedAt: provided.last_checked_at ?? scannedDates.at(-1) ?? null,
    latestDocuments: Array.isArray(provided.latest_documents) ? provided.latest_documents.slice(0, 8) : [],
  };
}

export function operationalFolderLabel(item = {}) {
  return [item.kk_id, item.kode].filter(Boolean).join(" / ");
}
