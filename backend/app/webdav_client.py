from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from app.evidence_structure import canonical_folder_path


@dataclass(frozen=True)
class WebDavItem:
    name: str
    href: str
    is_folder: bool
    size_bytes: int | None
    mime_type: str | None
    modified_at: str | None


class WebDavError(RuntimeError):
    pass


class PublicShareWebDavClient:
    def __init__(self, host: str, share_token: str, timeout_seconds: int = 30):
        self.host = host.rstrip("/")
        self.share_token = share_token.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def base_dav_url(self) -> str:
        return f"{self.host}/public.php/dav/files/{self.share_token}/"

    def upload_file(self, folder_path: str, file_name: str, payload: bytes, content_type: str | None = None) -> str:
        if not self.share_token:
            raise WebDavError("LUMBUNG_SHARE_TOKEN belum diisi.")

        remote_path = "/".join([folder_path.strip("/"), file_name.strip("/")])
        url = self.base_dav_url + encode_path(remote_path)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(url, data=payload, method="PUT", headers=headers)

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status not in {200, 201, 204}:
                    raise WebDavError(f"WebDAV upload gagal: HTTP {response.status}.")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")[:500]
            raise WebDavError(f"WebDAV upload gagal: HTTP {exc.code} {exc.reason}. {details}") from exc
        except URLError as exc:
            raise WebDavError(f"WebDAV upload gagal tersambung: {exc.reason}") from exc

        return remote_path

    def list_folder(self, folder_path: str) -> list[WebDavItem]:
        if not self.share_token:
            raise WebDavError("LUMBUNG_SHARE_TOKEN belum diisi.")

        url = self.base_dav_url + encode_path(folder_path).rstrip("/") + "/"
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontentlength/>
    <d:getcontenttype/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>
"""
        request = Request(
            url,
            data=body,
            method="PROPFIND",
            headers={
                "Depth": "1",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/xml",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                xml_data = response.read()
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")[:500]
            raise WebDavError(f"WebDAV gagal: HTTP {exc.code} {exc.reason}. {details}") from exc
        except URLError as exc:
            raise WebDavError(f"WebDAV gagal tersambung: {exc.reason}") from exc

        return parse_propfind_response(xml_data, folder_path)

    def list_files_recursive(self, folder_path: str, max_depth: int = 4) -> list[WebDavItem]:
        root_path = folder_path.strip("/")
        files: list[WebDavItem] = []
        queue: list[tuple[str, int]] = [(root_path, 0)]

        while queue:
            current_path, depth = queue.pop(0)
            for item in self.list_folder(current_path):
                item_path = "/".join([current_path, item.name]).strip("/")
                if item.is_folder:
                    if depth < max_depth:
                        queue.append((item_path, depth + 1))
                    continue

                relative_name = item_path.removeprefix(root_path).lstrip("/")
                files.append(
                    WebDavItem(
                        name=relative_name or item.name,
                        href=item.href,
                        is_folder=False,
                        size_bytes=item.size_bytes,
                        mime_type=item.mime_type,
                        modified_at=item.modified_at,
                    )
                )

        return files


def encode_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.strip("/").split("/"))


def public_folder_link(host: str, share_token: str, folder_path: str) -> str:
    encoded_dir = "/" + encode_path(canonical_folder_path(folder_path))
    return f"{host.rstrip('/')}/s/{share_token}?dir={encoded_dir}"


def parse_propfind_response(xml_data: bytes, folder_path: str) -> list[WebDavItem]:
    ns = {"d": "DAV:"}
    root = ET.fromstring(xml_data)
    normalized_folder = folder_path.strip("/")
    items: list[WebDavItem] = []

    for response in root.findall("d:response", ns):
        href = response.findtext("d:href", default="", namespaces=ns)
        decoded_href = unquote(href).rstrip("/")
        prop = response.find("d:propstat/d:prop", ns)
        if prop is None:
            continue

        is_folder = prop.find("d:resourcetype/d:collection", ns) is not None
        name = prop.findtext("d:displayname", default="", namespaces=ns)
        if not name:
            name = decoded_href.split("/")[-1]

        if decoded_href.endswith(normalized_folder):
            continue

        size_raw = prop.findtext("d:getcontentlength", default="", namespaces=ns)
        modified_raw = prop.findtext("d:getlastmodified", default="", namespaces=ns)
        mime_type = prop.findtext("d:getcontenttype", default="", namespaces=ns) or None
        size_bytes = int(size_raw) if size_raw.isdigit() else None
        modified_at = normalize_http_date(modified_raw)

        items.append(
            WebDavItem(
                name=name,
                href=decoded_href,
                is_folder=is_folder,
                size_bytes=size_bytes,
                mime_type=mime_type,
                modified_at=modified_at,
            )
        )

    return items


def normalize_http_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value
