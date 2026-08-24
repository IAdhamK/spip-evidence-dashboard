import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createLatestRequestGuard } from "../src/lib/latest-request-guard.js";

const guard = createLatestRequestGuard();

const firstDetail = guard.begin();
assert.equal(guard.isCurrent(firstDetail), true, "Permintaan detail aktif harus dapat memperbarui layar.");

guard.invalidate();
assert.equal(
  guard.isCurrent(firstDetail),
  false,
  "Respons detail yang selesai setelah pengguna kembali ke daftar harus diabaikan.",
);

const olderDetail = guard.begin();
const newerDetail = guard.begin();
assert.equal(guard.isCurrent(olderDetail), false, "Detail lama tidak boleh menimpa detail yang lebih baru.");
assert.equal(guard.isCurrent(newerDetail), true, "Hanya permintaan detail terbaru yang boleh digunakan.");

const mainSource = await readFile(new URL("../src/main.jsx", import.meta.url), "utf8");
assert.match(mainSource, /function clearDetailView\(\)[\s\S]*detailRequestGuard\.invalidate\(\)/);
assert.match(
  mainSource,
  /async function refreshDetail[\s\S]*detailRequestGuard\.begin\(\)[\s\S]*detailRequestGuard\.isCurrent\(requestRevision\)[\s\S]*setSelected\(detail\)/,
);
assert.match(mainSource, /<DetailPage[\s\S]*onBack=\{\(\) => \{[\s\S]*clearDetailView\(\)/);

console.log("Navigation request guard checks passed.");
