const FOLDER_SEGMENT_MAX_LENGTH = 118;
export const SPECIAL_KK31_310_ROOT = "KK 3.1 EFEKTIVITAS DAN EFISIENSI PENCAPAIAN TUJUAN ORGANISASI";
export const SPECIAL_KK31_310_SUBUNSUR = "3.10 Akuntabilitas terhadap Sumber Daya dan Pencatatannya";
export const SPECIAL_KK31_310_PARAMETER = "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola sumber daya yang diberikan-dikuasak_";
export const SPECIAL_KK32_310_ROOT = "KK 3.2 KEANDALAN PELAPORAN KEUANGAN";
export const SPECIAL_KK32_310_SUBUNSUR = "3.10 Akuntabilitas terhadap Sumber Daya dan Pencatatannya";
export const SPECIAL_KK32_310_PARAMETER = "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola sumber daya keuangan yang diberikan atau dikuasakan kepadanya dalam rangka pencapaian tujuan organisasi";

export function canonicalLumbungUrl(publicUrl, folderPath = "") {
  if (!publicUrl) return null;

  try {
    const url = new URL(publicUrl);
    const sourcePath = String(folderPath || url.searchParams.get("dir") || "").trim();
    if (!sourcePath) return publicUrl;

    const canonicalPath = canonicalFolderPath(sourcePath);
    const queryParts = [];
    for (const [key, value] of url.searchParams.entries()) {
      if (key !== "dir") {
        queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
      }
    }
    queryParts.push(`dir=/${canonicalPath.split("/").map(encodeURIComponent).join("/")}`);
    return `${url.origin}${url.pathname}?${queryParts.join("&")}${url.hash}`;
  } catch {
    return publicUrl;
  }
}

export function normalizeLumbungLinks(value) {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeLumbungLinks(item));
  }
  if (!value || typeof value !== "object") {
    return value;
  }

  const normalized = {};
  for (const [key, item] of Object.entries(value)) {
    normalized[key] = normalizeLumbungLinks(item);
  }

  if (normalized.public_url) {
    normalized.public_url = canonicalLumbungUrl(normalized.public_url, normalized.folder_path);
  }
  if (normalized.parameter_entry_public_url) {
    normalized.parameter_entry_public_url = canonicalLumbungUrl(
      normalized.parameter_entry_public_url,
      normalized.parameter_entry_folder_path,
    );
  }
  if (normalized.folder_path) {
    normalized.folder_path = canonicalFolderPath(normalized.folder_path);
  }
  if (normalized.parameter_entry_folder_path) {
    normalized.parameter_entry_folder_path = canonicalFolderPath(normalized.parameter_entry_folder_path);
  }

  return normalized;
}

export function canonicalFolderPath(folderPath) {
  const parts = String(folderPath || "")
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter((part) => part.trim());
  const canonicalParts = parts.map(canonicalFolderSegment);

  // Pengecualian sesuai nama folder fisik LumbungFile KK3.1/3.10/3.10.1.
  // Folder lama memakai tanda hubung dan terpotong tepat pada 118 karakter.
  if (
    parts.length >= 3
    && parts[0].trim().toLocaleLowerCase("id-ID") === SPECIAL_KK31_310_ROOT.toLocaleLowerCase("id-ID")
    && parts[1].trim().toLocaleLowerCase("id-ID") === SPECIAL_KK31_310_SUBUNSUR.toLocaleLowerCase("id-ID")
    && parts[2].trim().toLocaleLowerCase("id-ID").startsWith("3.10.1 ")
  ) {
    canonicalParts[0] = SPECIAL_KK31_310_ROOT;
    canonicalParts[1] = SPECIAL_KK31_310_SUBUNSUR;
    canonicalParts[2] = SPECIAL_KK31_310_PARAMETER;
  }

  // Pengecualian sesuai struktur folder fisik LumbungFile KK3.2/3.10/3.10.1.
  // Nama parameter ini memang tidak dipotong; hanya segmen Grade A-E yang berubah.
  if (
    parts.length >= 3
    && parts[0].trim().toLocaleLowerCase("id-ID") === SPECIAL_KK32_310_ROOT.toLocaleLowerCase("id-ID")
    && parts[1].trim().toLocaleLowerCase("id-ID") === SPECIAL_KK32_310_SUBUNSUR.toLocaleLowerCase("id-ID")
    && parts[2].trim().toLocaleLowerCase("id-ID").startsWith("3.10.1 ")
  ) {
    canonicalParts[0] = SPECIAL_KK32_310_ROOT;
    canonicalParts[1] = SPECIAL_KK32_310_SUBUNSUR;
    canonicalParts[2] = SPECIAL_KK32_310_PARAMETER;
  }

  return canonicalParts.join("/");
}

export function canonicalFolderSegment(value) {
  let text = String(value || "")
    .replace(/[\u0000-\u001f:<>"|?*]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[. ]+|[. ]+$/g, "");
  const characters = Array.from(text);
  if (characters.length <= FOLDER_SEGMENT_MAX_LENGTH) return text;
  text = characters.slice(0, FOLDER_SEGMENT_MAX_LENGTH - 1).join("").replace(/[. ]+$/g, "");
  return `${text}_`;
}
