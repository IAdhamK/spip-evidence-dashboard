import assert from "node:assert/strict";
import {
  FILE_SEARCH_SUGGESTIONS,
  buildFileSearchPath,
  evidenceLocationLabel,
  fileSearchPromptState,
  fileTypeLabel,
  secondaryFileName,
} from "../src/features/file-search/file-search.js";

const filteredPath = buildFileSearchPath({
  query: "  pelaksanaan anggaran  ",
  kkId: "KK3.2",
  fileType: "pdf",
  grade: "C",
  limit: 25,
});
const filteredUrl = new URL(filteredPath, "http://localhost");
assert.equal(filteredUrl.pathname, "/api/files/search");
assert.equal(filteredUrl.searchParams.get("q"), "pelaksanaan anggaran");
assert.equal(filteredUrl.searchParams.get("kk_id"), "KK3.2");
assert.equal(filteredUrl.searchParams.get("file_type"), "pdf");
assert.equal(filteredUrl.searchParams.get("grade"), "C");
assert.equal(filteredUrl.searchParams.get("limit"), "25");

const defaultPath = new URL(
  buildFileSearchPath({ query: "IKPA", kkId: "all", fileType: "all", grade: "all" }),
  "http://localhost",
);
assert.equal(defaultPath.searchParams.has("kk_id"), false);
assert.equal(defaultPath.searchParams.has("file_type"), false);
assert.equal(defaultPath.searchParams.has("grade"), false);

assert.equal(
  evidenceLocationLabel({ kk_id: "KK3.1", kode: "3.10", detail_kode: "3.10.1", grade: "B" }),
  "KK3.1 · 3.10 · Parameter 3.10.1 · Grade B",
);
assert.equal(fileTypeLabel("spreadsheet"), "Spreadsheet");
assert.equal(fileTypeLabel("unknown"), "File");
assert.equal(
  secondaryFileName({
    remote_path: "3.10.1 Pertanggungjawaban/Grade B/Laporan IKPA.pdf",
    base_name: "Laporan IKPA.pdf",
  }),
  "3.10.1 Pertanggungjawaban/Grade B",
);

assert.deepEqual(fileSearchPromptState("", null), {
  kind: "idle",
  text: "Ketik nama file, nomor surat, kegiatan, atau kata kunci.",
});
assert.equal(fileSearchPromptState("I", null).kind, "invalid");
assert.equal(fileSearchPromptState("IKPA", null, true).kind, "loading");
assert.equal(fileSearchPromptState("IKPA", { total: 0 }).kind, "empty");
assert.equal(fileSearchPromptState("IKPA", { total: 4 }).kind, "ready");
assert.ok(FILE_SEARCH_SUGGESTIONS.includes("IKPA"));
assert.ok(FILE_SEARCH_SUGGESTIONS.includes("Manajemen Risiko"));

console.log("File search helper checks passed.");
