export async function getRecord(env, key, seedPath, requestUrl) {
  const row = await env.LIVE_DB.prepare(
    "SELECT payload FROM edge_state WHERE key = ?1",
  ).bind(key).first();
  if (row?.payload) return JSON.parse(row.payload);
  if (!seedPath) return null;
  const url = new URL(seedPath, requestUrl);
  const response = await env.ASSETS.fetch(new Request(url));
  if (!response.ok) throw new Error(`Seed edge tidak tersedia: ${seedPath}`);
  return response.json();
}

export async function putRecord(env, key, payload) {
  const serialized = JSON.stringify(payload);
  const checksum = await sha256(serialized);
  const existing = await env.LIVE_DB.prepare(
    "SELECT checksum FROM edge_state WHERE key = ?1",
  ).bind(key).first();
  if (existing?.checksum === checksum) return false;
  await env.LIVE_DB.prepare(
    `INSERT INTO edge_state(key, payload, checksum, updated_at)
     VALUES(?1, ?2, ?3, ?4)
     ON CONFLICT(key) DO UPDATE SET
       payload = excluded.payload,
       checksum = excluded.checksum,
       updated_at = excluded.updated_at`,
  ).bind(key, serialized, checksum, new Date().toISOString()).run();
  return true;
}

export async function deleteRecord(env, key) {
  await env.LIVE_DB.prepare("DELETE FROM edge_state WHERE key = ?1").bind(key).run();
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
