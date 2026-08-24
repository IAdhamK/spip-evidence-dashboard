import { XMLParser } from "fast-xml-parser";

const PROPFIND_BODY = `<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:getcontentlength/><d:getcontenttype/><d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>`;

export class WebDavSyncError extends Error {}

export async function scanDetail(detail, env, fetchImpl = fetch) {
  const client = createClient(env, fetchImpl);
  const scannedAt = new Date().toISOString();
  const rootItems = await client.listFolder(detail.folder_path);
  const slots = [];
  const slotFiles = [];

  for (const slot of detail.evidence_slots ?? []) {
    try {
      const files = await client.listFilesRecursive(slot.folder_path);
      const slotRelativePath = relativePath(detail.folder_path, slot.folder_path);
      const normalizedFiles = files.map((item) => ({
        ...item,
        name: [slotRelativePath, item.name].filter(Boolean).join("/"),
      }));
      slotFiles.push(...normalizedFiles);
      slots.push({
        ...slot,
        file_count: files.length,
        total_size_bytes: files.reduce(
          (total, item) => total + Math.max(Number(item.size_bytes) || 0, 0),
          0,
        ),
        last_scanned_at: scannedAt,
        error_message: null,
      });
    } catch (error) {
      slots.push({
        ...slot,
        file_count: 0,
        total_size_bytes: 0,
        last_scanned_at: scannedAt,
        error_message: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return { scannedAt, rootItems, slots, slotFiles, requestCount: client.requestCount() };
}

export function createClient(env, fetchImpl = fetch) {
  const host = String(env.LUMBUNG_HOST || "https://lumbungfile.kemendesa.go.id").replace(/\/$/, "");
  const token = String(env.LUMBUNG_SHARE_TOKEN || "").trim();
  const maxDepth = Math.max(Number(env.SCAN_MAX_DEPTH) || 4, 0);
  const maxRequests = Math.max(Number(env.SCAN_MAX_REQUESTS_PER_SYNC) || 45, 1);
  let requests = 0;

  if (!token) throw new WebDavSyncError("Konfigurasi Lumbung File pada Worker belum tersedia.");

  async function listFolder(folderPath) {
    requests += 1;
    if (requests > maxRequests) {
      throw new WebDavSyncError(
        `Batas aman ${maxRequests} permintaan WebDAV tercapai; hasil lama dipertahankan.`,
      );
    }
    const url = `${host}/public.php/dav/files/${encodeURIComponent(token)}/${encodePath(folderPath)}/`;
    const response = await fetchImpl(url, {
      method: "PROPFIND",
      headers: {
        Depth: "1",
        "Content-Type": "application/xml",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: PROPFIND_BODY,
    });
    if (!response.ok && response.status !== 207) {
      throw new WebDavSyncError(`WebDAV gagal: HTTP ${response.status}.`);
    }
    return parsePropfind(await response.text(), folderPath);
  }

  async function listFilesRecursive(folderPath) {
    const rootPath = String(folderPath).replace(/^\/+|\/+$/g, "");
    const files = [];
    const queue = [[rootPath, 0]];
    while (queue.length) {
      const [currentPath, depth] = queue.shift();
      const items = await listFolder(currentPath);
      for (const item of items) {
        const itemPath = [currentPath, item.name].filter(Boolean).join("/");
        if (item.is_folder) {
          if (depth < maxDepth) queue.push([itemPath, depth + 1]);
          continue;
        }
        files.push({ ...item, name: relativePath(rootPath, itemPath) || item.name });
      }
    }
    return files;
  }

  return { listFolder, listFilesRecursive, requestCount: () => requests };
}

export function parsePropfind(xml, folderPath) {
  const parser = new XMLParser({
    removeNSPrefix: true,
    ignoreAttributes: false,
    parseTagValue: false,
    trimValues: true,
  });
  let parsed;
  try {
    parsed = parser.parse(xml);
  } catch (error) {
    throw new WebDavSyncError(`Respons WebDAV tidak valid: ${error.message}`);
  }
  const responses = asArray(parsed?.multistatus?.response);
  const normalizedFolder = String(folderPath).replace(/^\/+|\/+$/g, "");
  return responses.flatMap((entry) => {
    const propstats = asArray(entry?.propstat);
    const successful = propstats.find((item) => String(item?.status ?? "").includes(" 200 ")) ?? propstats[0];
    const prop = successful?.prop;
    if (!prop) return [];
    const href = safeDecode(String(entry?.href ?? "")).replace(/\/$/, "");
    const name = String(prop.displayname ?? href.split("/").at(-1) ?? "").trim();
    if (!name || safeDecode(href).endsWith(normalizedFolder)) return [];
    const contentLength = Number(prop.getcontentlength);
    return [{
      name,
      href: "",
      is_folder: Object.prototype.hasOwnProperty.call(prop.resourcetype ?? {}, "collection") ? 1 : 0,
      size_bytes: Number.isFinite(contentLength) ? contentLength : null,
      mime_type: String(prop.getcontenttype ?? "").trim() || null,
      modified_at: normalizeDate(prop.getlastmodified),
    }];
  });
}

function asArray(value) {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function encodePath(path) {
  return String(path)
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function relativePath(root, child) {
  const prefix = String(root).replace(/^\/+|\/+$/g, "");
  const value = String(child).replace(/^\/+|\/+$/g, "");
  return value.startsWith(prefix) ? value.slice(prefix.length).replace(/^\/+/, "") : value;
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function normalizeDate(value) {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const timestamp = Date.parse(text);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : text;
}
