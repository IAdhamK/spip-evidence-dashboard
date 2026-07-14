const FOLDER_SEGMENT_MAX_LENGTH = 118;

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

export function canonicalFolderPath(folderPath) {
  return String(folderPath || "")
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter((part) => part.trim())
    .map(canonicalFolderSegment)
    .join("/");
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
