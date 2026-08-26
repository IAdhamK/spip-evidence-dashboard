import { applyDetailToDashboard, applyDetailToKk, recalculateDetail } from "./evidence-sync.js";
import { scanDetail } from "./webdav.js";
import { getRecord, putRecord } from "./state.js";

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};
const SYNC_STATUS_KEY = "sync:status";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
    try {
      if (env.BACKEND_ORIGIN_URL) {
        return await proxyBackendRequest(request, env.BACKEND_ORIGIN_URL);
      }
      return await routeApi(request, env, url);
    } catch (error) {
      return json({ detail: error instanceof Error ? error.message : String(error) }, 500);
    }
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(runScheduledSync(env));
  },
};

export async function proxyBackendRequest(request, originUrl, fetchImpl = fetch) {
  const source = new URL(request.url);
  const origin = new URL(originUrl);
  const target = new URL(`${source.pathname}${source.search}`, origin);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("cf-connecting-ip");
  headers.delete("cf-ray");
  headers.delete("cf-visitor");
  return fetchImpl(target, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method.toUpperCase()) ? undefined : request.body,
    redirect: "manual",
  });
}

async function routeApi(request, env, url) {
  const method = request.method.toUpperCase();
  if (method === "GET" && url.pathname === "/api/health") {
    return json({
      ok: true,
      service: "spip-evidence-dashboard-edge-live",
      webdav_configured: Boolean(env.LUMBUNG_SHARE_TOKEN),
      live_sync: true,
    });
  }
  if (method === "GET" && url.pathname === "/api/meta") {
    return json(await record(env, "meta", "/edge-seed/meta.json", request.url));
  }
  if (method === "GET" && url.pathname === "/api/dashboard") {
    return json(await record(env, "dashboard", "/edge-seed/dashboard.json", request.url));
  }
  if (method === "GET" && url.pathname === "/api/operational-progress") {
    const dashboard = await record(env, "dashboard", "/edge-seed/dashboard.json", request.url);
    const summary = dashboard?.operational_summary ?? {};
    return json({
      count: summary.latest_documents?.length ?? 0,
      limit: summary.latest_documents?.length ?? 0,
      last_checked_at: summary.last_checked_at ?? null,
      latest_document_modified_at: summary.latest_document_modified_at ?? null,
      latest_documents: summary.latest_documents ?? [],
      source: summary.source ?? "edge_snapshot_metadata",
    });
  }
  if (method === "GET" && url.pathname === "/api/kk") {
    return json(await record(env, "kk-list", "/edge-seed/kk-list.json", request.url));
  }
  if (method === "GET" && url.pathname === "/api/smart-upload/config") {
    return json({ enabled: false, analysis_pipeline_v2_enabled: false, online_sync_only: true });
  }
  if (method === "GET" && url.pathname === "/api/sync/status") {
    return json(await syncStatus(env, request.url));
  }
  if (method === "POST" && url.pathname === "/api/sync/background") {
    return json(await startFullSync(env, request.url));
  }
  if (method === "POST" && url.pathname === "/api/sync/background/advance") {
    return json(await advanceFullSync(env, request.url));
  }

  const syncMatch = url.pathname.match(/^\/api\/sync\/background\/([^/]+)\/([^/]+)$/);
  if (method === "POST" && syncMatch) {
    const kkId = decodeURIComponent(syncMatch[1]);
    const kode = decodeURIComponent(syncMatch[2]);
    const result = await syncOne(env, request.url, kkId, kode);
    return json({
      is_running: false,
      scope: `${kkId}/${kode}`,
      total: 1,
      synced: result.ok ? 1 : 0,
      failed: result.ok ? 0 : 1,
      current: null,
      errors: result.ok ? [] : [{ kk_id: kkId, kode, message: result.error }],
      message: result.ok ? `Sinkronisasi ${kkId}/${kode} selesai.` : `Sinkronisasi ${kkId}/${kode} gagal.`,
      finished_at: new Date().toISOString(),
      live_sync: true,
    });
  }

  const filesMatch = url.pathname.match(/^\/api\/subunsur\/([^/]+)\/([^/]+)\/files$/);
  if (method === "GET" && filesMatch) {
    const detail = await loadDetail(env, request.url, decodeURIComponent(filesMatch[1]), decodeURIComponent(filesMatch[2]));
    return json(detail.files ?? []);
  }
  const detailMatch = url.pathname.match(/^\/api\/subunsur\/([^/]+)\/([^/]+)$/);
  if (method === "GET" && detailMatch) {
    return json(await loadDetail(env, request.url, decodeURIComponent(detailMatch[1]), decodeURIComponent(detailMatch[2])));
  }
  const kkMatch = url.pathname.match(/^\/api\/kk\/([^/]+)$/);
  if (method === "GET" && kkMatch) {
    const kkId = decodeURIComponent(kkMatch[1]);
    return json(await record(env, `kk:${kkId}`, `/edge-seed/kk/${safeName(kkId)}.json`, request.url));
  }
  return json({ detail: `Endpoint Worker tidak tersedia: ${method} ${url.pathname}` }, 404);
}

async function syncOne(env, requestUrl, kkId, kode) {
  try {
    const markerKey = `sync-marker:${kkId}:${kode}`;
    const marker = await getRecord(env, markerKey, null, requestUrl);
    const minimumInterval = Math.max(Number(env.SYNC_MIN_INTERVAL_SECONDS) || 10, 5) * 1000;
    if (marker?.completed_at && Date.now() - Date.parse(marker.completed_at) < minimumInterval) {
      return { ok: true, skipped: true, request_count: 0 };
    }
    const detail = await loadDetail(env, requestUrl, kkId, kode);
    const scanned = await scanDetail(detail, env);
    const budgetError = scanned.slots.find((slot) =>
      String(slot.error_message ?? "").startsWith("Batas aman"),
    );
    if (budgetError) {
      return { ok: false, error: budgetError.error_message, request_count: scanned.requestCount };
    }
    const updatedDetail = recalculateDetail(detail, scanned);
    const dashboard = await record(env, "dashboard", "/edge-seed/dashboard.json", requestUrl);
    const kk = await record(env, `kk:${kkId}`, `/edge-seed/kk/${safeName(kkId)}.json`, requestUrl);
    await Promise.all([
      putRecord(env, detailKey(kkId, kode), updatedDetail),
      putRecord(env, "dashboard", applyDetailToDashboard(dashboard, updatedDetail)),
      putRecord(env, `kk:${kkId}`, applyDetailToKk(kk, updatedDetail)),
      putRecord(env, markerKey, { completed_at: new Date().toISOString() }),
    ]);
    return { ok: true, request_count: scanned.requestCount };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

async function startFullSync(env, requestUrl, { scheduled = false } = {}) {
  const current = await syncStatus(env, requestUrl);
  if (current.is_running) return { ...current, started: false, already_running: true };
  const catalog = await record(env, "catalog", "/edge-seed/catalog.json", requestUrl);
  const now = new Date().toISOString();
  const status = {
    is_running: true,
    scope: "full",
    started_at: now,
    finished_at: null,
    total: catalog.length,
    synced: 0,
    failed: 0,
    cursor: 0,
    current: catalog[0] ? `${catalog[0].kk_id}/${catalog[0].kode}` : null,
    errors: [],
    message: scheduled
      ? "Sinkronisasi penuh otomatis Cloudflare berjalan bertahap."
      : "Sinkronisasi penuh Cloudflare berjalan bertahap.",
    client_driven: !scheduled,
    scheduled,
    live_sync: true,
  };
  await putRecord(env, SYNC_STATUS_KEY, status);
  return { ...status, started: true, already_running: false };
}

async function advanceFullSync(env, requestUrl) {
  const status = await syncStatus(env, requestUrl);
  if (!status.is_running) return status;
  const catalog = await record(env, "catalog", "/edge-seed/catalog.json", requestUrl);
  const target = catalog[status.cursor];
  if (!target) return finishStatus(env, status);

  const result = await syncOne(env, requestUrl, target.kk_id, target.kode);
  const errors = [...(status.errors ?? [])];
  if (!result.ok) errors.push({ kk_id: target.kk_id, kode: target.kode, message: result.error });
  const nextCursor = status.cursor + 1;
  const next = catalog[nextCursor];
  const updated = {
    ...status,
    cursor: nextCursor,
    synced: status.synced + (result.ok ? 1 : 0),
    failed: status.failed + (result.ok ? 0 : 1),
    errors: errors.slice(-100),
    current: next ? `${next.kk_id}/${next.kode}` : null,
  };
  if (!next) return finishStatus(env, updated);
  await putRecord(env, SYNC_STATUS_KEY, updated);
  return updated;
}

async function finishStatus(env, status) {
  const updated = {
    ...status,
    is_running: false,
    current: null,
    finished_at: new Date().toISOString(),
    message: status.failed
      ? `Sinkronisasi penuh selesai dengan ${status.failed} kegagalan; data lama dipertahankan untuk folder tersebut.`
      : "Sinkronisasi penuh selesai.",
  };
  await putRecord(env, SYNC_STATUS_KEY, updated);
  return updated;
}

async function syncStatus(env, requestUrl) {
  return (await getRecord(env, SYNC_STATUS_KEY, null, requestUrl)) ?? {
    is_running: false,
    scope: null,
    started_at: null,
    finished_at: null,
    total: 0,
    synced: 0,
    failed: 0,
    current: null,
    errors: [],
    message: "Belum ada sinkronisasi Worker berjalan.",
    client_driven: false,
    live_sync: true,
  };
}

async function runScheduledSync(env) {
  const requestUrl = "https://spip-evidence-pdp.m-i-adhamkarim.workers.dev/";
  let status = await syncStatus(env, requestUrl);
  if (!status.is_running) {
    const intervalMinutes = Math.max(Number(env.EDGE_FULL_SYNC_INTERVAL_MINUTES) || 360, 60);
    const finishedAt = Date.parse(status.finished_at || 0);
    if (Number.isFinite(finishedAt) && Date.now() - finishedAt < intervalMinutes * 60_000) return;
    status = await startFullSync(env, requestUrl, { scheduled: true });
  }
  if (status.is_running && status.scheduled) await advanceFullSync(env, requestUrl);
}

async function loadDetail(env, requestUrl, kkId, kode) {
  const detail = await record(
    env,
    detailKey(kkId, kode),
    `/edge-seed/subunsur/${safeName(`${kkId}__${kode}`)}.json`,
    requestUrl,
  );
  if (!detail) throw new Error("Subunsur tidak ditemukan.");
  return detail;
}

function detailKey(kkId, kode) {
  return `detail:${kkId}:${kode}`;
}

function safeName(value) {
  return encodeURIComponent(value).replaceAll("%", "_");
}

async function record(env, key, seedPath, requestUrl) {
  return getRecord(env, key, seedPath, requestUrl);
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS });
}
