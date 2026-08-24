import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { applyDetailToDashboard, recalculateDetail } from "../worker/evidence-sync.js";
import { createClient, parsePropfind } from "../worker/webdav.js";

const xml = await readFile(new URL("./fixtures/propfind.xml", import.meta.url), "utf8");
const items = parsePropfind(xml, "Root/Grade E");
assert.equal(items.length, 2);
assert.equal(items[0].is_folder, 0);
assert.equal(items[0].size_bytes, 12);
assert.equal(items[1].is_folder, 1);

const limitedClient = createClient(
  {
    LUMBUNG_SHARE_TOKEN: "token",
    SCAN_MAX_DEPTH: "4",
    SCAN_MAX_REQUESTS_PER_SYNC: "2",
  },
  async () => new Response(xml, { status: 207 }),
);
await assert.rejects(
  limitedClient.listFilesRecursive("Root/Grade E"),
  /Batas aman 2 permintaan WebDAV/,
);

const detail = {
  kk_id: "KK3.1",
  kode: "1.1",
  folder_path: "Root",
  parameters: [{
    detail_kode: "1.1.1",
    kode_spip: "SPIP",
    kode_mri: "-",
    kode_iepk: "-",
    grades: ["A", "B", "C", "D", "E"].map((grade) => ({ grade, evidence_folders: [] })),
  }],
  evidence_slots: ["A", "B", "C", "D", "E"].map((grade) => ({
    detail_kode: "1.1.1",
    grade,
    category_name: "Evidence Grade",
    category_folder: "",
    folder_path: `Root/Grade ${grade}`,
  })),
};
const scanned = {
  scannedAt: "2026-08-24T00:00:00.000Z",
  rootItems: [],
  slots: detail.evidence_slots.map((slot) => ({
    ...slot,
    file_count: ["E", "D"].includes(slot.grade) ? 1 : 0,
    total_size_bytes: ["E", "D"].includes(slot.grade) ? 10 : 0,
    error_message: null,
  })),
  slotFiles: [
    { name: "Grade E/e.pdf", is_folder: 0, size_bytes: 10 },
    { name: "Grade D/d.pdf", is_folder: 0, size_bytes: 10 },
  ],
};
const updated = recalculateDetail(detail, scanned);
assert.equal(updated.status, "Terisi Penuh");
assert.equal(updated.file_count, 2);
assert.equal(updated.parameters[0].highest_filled_grade, "D");

const dashboard = applyDetailToDashboard({
  folders: [{ kk_id: "KK3.1", kode: "1.1", file_count: 0, total_size_bytes: 0, status: "Kosong" }],
  kk_summary: [{ kk_id: "KK3.1", file_count: 0, total_size_bytes: 0, status_counts: { Kosong: 1 } }],
}, updated);
assert.equal(dashboard.total_files, 2);
assert.deepEqual(dashboard.status_counts, { Kosong: 0, "Terisi Sebagian": 0, "Terisi Penuh": 1 });
console.log("Cloudflare edge sync checks passed.");
