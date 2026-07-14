from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_static import NO_STORE_HEADERS, NoStoreStaticFiles, no_store_file_response


class FrontendStaticCacheTests(unittest.TestCase):
    def test_assets_ignore_conditional_cache_after_rebuild(self):
        with TemporaryDirectory() as directory:
            asset = Path(directory) / "app.js"
            asset.write_text("console.log('current build')", encoding="utf-8")
            app = FastAPI()
            app.mount("/assets", NoStoreStaticFiles(directory=directory), name="assets")

            with TestClient(app) as client:
                first = client.get("/assets/app.js")
                second = client.get(
                    "/assets/app.js",
                    headers={"If-None-Match": first.headers["etag"]},
                )

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.text, "console.log('current build')")
            self.assertEqual(second.headers["cache-control"], NO_STORE_HEADERS["Cache-Control"])

    def test_spa_file_response_is_not_cached(self):
        with TemporaryDirectory() as directory:
            index = Path(directory) / "index.html"
            index.write_text("<div id='root'></div>", encoding="utf-8")
            response = no_store_file_response(index)

        self.assertEqual(response.headers["cache-control"], NO_STORE_HEADERS["Cache-Control"])
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(response.headers["expires"], "0")


if __name__ == "__main__":
    unittest.main()
