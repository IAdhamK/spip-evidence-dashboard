const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const STATIC_SNAPSHOT = import.meta.env.VITE_STATIC_SNAPSHOT === "true";
let snapshotPromise;

export async function apiGet(path) {
  return request(path, { method: "GET" });
}

export async function apiPost(path, body) {
  return request(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function isStaticSnapshot() {
  return STATIC_SNAPSHOT;
}

async function request(path, options) {
  if (STATIC_SNAPSHOT) {
    return staticRequest(path, options);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, options);
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || `Request gagal: ${response.status}`);
  }
  return data;
}

async function staticRequest(path, options) {
  const method = options?.method ?? "GET";
  if (method !== "GET") {
    throw new Error("Mode online GitHub Pages memakai snapshot read-only. Jalankan aplikasi lokal untuk sinkronisasi live.");
  }

  const snapshot = await loadSnapshot();
  if (path === "/api/health") return snapshot.health;
  if (path === "/api/meta") return snapshot.meta;
  if (path === "/api/dashboard") return snapshot.dashboard;
  if (path === "/api/kk") return snapshot.kk;

  const kkMatch = path.match(/^\/api\/kk\/([^/]+)$/);
  if (kkMatch) {
    const kkId = decodeURIComponent(kkMatch[1]);
    const kk = snapshot.kk.find((item) => item.id === kkId);
    if (!kk) throw new Error("KK tidak ditemukan di snapshot.");
    return { kk_id: kk.id, title: kk.title, folders: kk.folders };
  }

  const filesMatch = path.match(/^\/api\/subunsur\/([^/]+)\/([^/]+)\/files$/);
  if (filesMatch) {
    const key = detailKey(decodeURIComponent(filesMatch[1]), decodeURIComponent(filesMatch[2]));
    const detail = snapshot.subunsur_details[key];
    if (!detail) throw new Error("Subunsur tidak ditemukan di snapshot.");
    return detail.files ?? [];
  }

  const detailMatch = path.match(/^\/api\/subunsur\/([^/]+)\/([^/]+)$/);
  if (detailMatch) {
    const key = detailKey(decodeURIComponent(detailMatch[1]), decodeURIComponent(detailMatch[2]));
    const detail = snapshot.subunsur_details[key];
    if (!detail) throw new Error("Subunsur tidak ditemukan di snapshot.");
    return detail;
  }

  throw new Error(`Endpoint snapshot tidak tersedia: ${path}`);
}

async function loadSnapshot() {
  if (!snapshotPromise) {
    snapshotPromise = fetch(`${import.meta.env.BASE_URL}snapshot.json`).then(async (response) => {
      if (!response.ok) {
        throw new Error("Snapshot dashboard online belum tersedia.");
      }
      return response.json();
    });
  }
  return snapshotPromise;
}

function detailKey(kkId, kode) {
  return `${kkId}::${kode}`;
}
