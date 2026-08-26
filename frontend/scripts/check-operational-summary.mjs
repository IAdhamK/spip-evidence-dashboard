import assert from "node:assert/strict";
import {
  operationalFolderLabel,
  operationalSummaryView,
} from "../src/features/operational-summary/operational-summary.js";
import {
  filterRecentProgress,
  recentProgressCounts,
} from "../src/features/operational-summary/recent-progress.js";

const dashboard = {
  status_counts: { Kosong: 1, "Terisi Sebagian": 1, "Terisi Penuh": 1 },
  folders: [
    { kk_id: "KK3.1", kode: "1.1", subunsur_name: "Integritas", status: "Kosong", last_scanned_at: "2026-08-25T00:00:00Z" },
    { kk_id: "KK3.1", kode: "1.2", subunsur_name: "Kompetensi", status: "Terisi Sebagian", last_scanned_at: "2026-08-26T00:00:00Z", error_message: "Timeout" },
    { kk_id: "KK3.1", kode: "1.3", subunsur_name: "Kepemimpinan", status: "Terisi Penuh", last_scanned_at: "2026-08-24T00:00:00Z" },
  ],
};

const fallback = operationalSummaryView(dashboard);
assert.equal(fallback.available, true);
assert.equal(fallback.lastCheckedAt, "2026-08-26T00:00:00Z");

const supplied = operationalSummaryView({
  ...dashboard,
  operational_summary: {
    last_checked_at: "2026-08-27T00:00:00Z",
    latest_documents: [{ id: 1, kk_id: "KK3.1", kode: "1.3", base_name: "Laporan.pdf" }],
  },
});
assert.equal(supplied.lastCheckedAt, "2026-08-27T00:00:00Z");
assert.equal(supplied.latestDocuments[0].base_name, "Laporan.pdf");
assert.equal(operationalFolderLabel({ kk_id: "KK3.2", kode: "3.10" }), "KK3.2 / 3.10");

const progress = [
  { id: 1, kk_id: "KK3.1", kode: "1.1", base_name: "Laporan Lama.pdf", subunsur_name: "Integritas", modified_at: "2026-08-20T00:00:00Z" },
  { id: 2, kk_id: "KK3.2", kode: "3.10", base_name: "Laporan IKPA.pdf", subunsur_name: "Akuntabilitas", modified_at: "2026-08-25T00:00:00Z" },
  { id: 3, kk_id: "KK3.2", kode: "1.3", base_name: "RTP.xlsx", subunsur_name: "Kepemimpinan", modified_at: "2026-08-24T00:00:00Z" },
];
assert.deepEqual(
  filterRecentProgress(progress, { query: "laporan", kkId: "KK3.2" }).map((item) => item.id),
  [2],
);
assert.deepEqual(filterRecentProgress(progress).map((item) => item.id), [2, 3, 1]);
assert.equal(recentProgressCounts(progress)["KK3.2"], 2);
assert.equal(recentProgressCounts(progress)["KK3.4"], 0);

console.log("Operational summary helper checks passed.");
