export function filterRecentProgress(documents = [], { query = "", kkId = "Semua" } = {}) {
  const needle = String(query ?? "").trim().toLocaleLowerCase("id-ID");
  return [...documents]
    .filter((item) => {
      const matchesKk = kkId === "Semua" || item.kk_id === kkId;
      if (!matchesKk) return false;
      if (!needle) return true;
      const haystack = [
        item.base_name,
        item.kk_id,
        item.kode,
        item.subunsur_name,
      ].join(" ").toLocaleLowerCase("id-ID");
      return haystack.includes(needle);
    })
    .sort((left, right) => {
      const leftTime = Date.parse(left.modified_at || 0) || 0;
      const rightTime = Date.parse(right.modified_at || 0) || 0;
      return rightTime - leftTime || String(left.base_name || "").localeCompare(String(right.base_name || ""), "id-ID");
    });
}

export function recentProgressCounts(documents = []) {
  return Object.fromEntries(
    ["KK3.1", "KK3.2", "KK3.3", "KK3.4"].map((kkId) => [
      kkId,
      documents.filter((item) => item.kk_id === kkId).length,
    ]),
  );
}
