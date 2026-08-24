import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const snapshot = JSON.parse(await readFile(path.join(root, "public", "snapshot.json"), "utf8"));
const output = path.join(root, "dist", "edge-seed");
await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, "kk"), { recursive: true });
await mkdir(path.join(output, "subunsur"), { recursive: true });

await writeJson("health.json", snapshot.health);
await writeJson("meta.json", snapshot.meta);
await writeJson("dashboard.json", snapshot.dashboard);
await writeJson("kk-list.json", snapshot.kk);

const catalog = [];
for (const kk of snapshot.kk ?? []) {
  await writeJson(path.join("kk", `${safeName(kk.id)}.json`), {
    kk_id: kk.id,
    title: kk.title,
    folders: kk.folders ?? [],
  });
}
for (const [key, detail] of Object.entries(snapshot.subunsur_details ?? {})) {
  catalog.push({ kk_id: detail.kk_id, kode: detail.kode });
  await writeJson(path.join("subunsur", `${safeName(key.replace("::", "__"))}.json`), detail);
}
await writeJson("catalog.json", catalog);
console.log(`Edge seed generated: ${catalog.length} subunsur.`);

async function writeJson(relativePath, value) {
  const target = path.join(output, relativePath);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, JSON.stringify(value), "utf8");
}

function safeName(value) {
  return encodeURIComponent(value).replaceAll("%", "_");
}
