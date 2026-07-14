from pathlib import Path

from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles


NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class NoStoreStaticFiles(StaticFiles):
    """Serve rebuilt frontend assets without reusing a stale browser response."""

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers.update(NO_STORE_HEADERS)
        return response


def no_store_file_response(path: Path) -> FileResponse:
    return FileResponse(path, headers=NO_STORE_HEADERS)
