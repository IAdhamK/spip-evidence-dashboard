from __future__ import annotations

import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from app.evidence_structure import (
    SPECIAL_KK32_310_PARAMETER,
    SPECIAL_KK32_310_ROOT,
    SPECIAL_KK32_310_SUBUNSUR,
    canonical_folder_path,
)
from app.routes import current_folder_record, current_public_folder_link
from app.webdav_client import canonical_public_folder_url, public_folder_link
from scripts.export_static_snapshot import inject_public_urls, refresh_nested_public_urls


class LumbungPublicLinkTests(unittest.TestCase):
    def test_long_parameter_segment_is_aligned_with_physical_folder(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade C"
        )

        canonical = canonical_folder_path(full_path)
        parameter_segment = canonical.split("/")[2]
        self.assertEqual(len(parameter_segment), 118)
        self.assertTrue(parameter_segment.endswith("mend_"))

        public_url = public_folder_link(
            "https://lumbungfile.kemendesa.go.id",
            "CiJYTHFxZaJ83YF",
            full_path,
        )
        query = parse_qs(urlparse(public_url).query)
        self.assertEqual(query["dir"], [f"/{canonical}"])

    def test_existing_canonical_path_remains_stable(self) -> None:
        path = "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/1.5 Pendelegasian Wewenang/Grade C"
        self.assertEqual(canonical_folder_path(path), path)

    def test_cached_database_url_is_rebuilt_from_folder_path(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade B"
        )
        item = {
            "folder_path": full_path,
            "public_url": "https://lumbungfile.kemendesa.go.id/stale-full-path",
        }
        settings = SimpleNamespace(
            has_share_token=True,
            lumbung_host="https://lumbungfile.kemendesa.go.id",
            lumbung_share_token="CiJYTHFxZaJ83YF",
        )

        resolved = current_public_folder_link(settings, item)
        query = parse_qs(urlparse(resolved).query)

        self.assertNotEqual(resolved, item["public_url"])
        self.assertEqual(query["dir"], [f"/{canonical_folder_path(full_path)}"])
        self.assertTrue(query["dir"][0].split("/")[3].endswith("mend_"))

    def test_api_record_never_exposes_stale_long_folder_path(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade E"
        )

        current = current_folder_record(
            {
                "folder_path": full_path,
                "public_url": "https://lumbungfile.kemendesa.go.id/stale-full-path",
            }
        )

        self.assertEqual(current["folder_path"], canonical_folder_path(full_path))
        self.assertTrue(current["folder_path"].split("/")[2].endswith("mend_"))
        query = parse_qs(urlparse(current["public_url"]).query)
        self.assertEqual(query["dir"], [f"/{canonical_folder_path(full_path)}"])

    def test_cached_public_url_is_canonicalized_without_share_token(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade A"
        )
        stale_url = (
            "https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/"
            + "/".join(part.replace(" ", "%20") for part in full_path.split("/"))
        )
        settings = SimpleNamespace(has_share_token=False)

        resolved = current_public_folder_link(
            settings,
            {"folder_path": full_path, "public_url": stale_url},
        )
        query = parse_qs(urlparse(resolved).query)

        self.assertNotEqual(resolved, stale_url)
        self.assertEqual(query["dir"], [f"/{canonical_folder_path(full_path)}"])

    def test_stale_lumbung_url_can_be_repaired_from_its_own_dir_query(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade E"
        )
        stale_url = (
            "https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/"
            + "/".join(part.replace(" ", "%20") for part in full_path.split("/"))
        )

        resolved = canonical_public_folder_url(stale_url)
        query = parse_qs(urlparse(resolved).query)

        self.assertEqual(query["dir"], [f"/{canonical_folder_path(full_path)}"])

    def test_kk32_310_uses_full_parameter_folder_for_every_grade(self) -> None:
        stale_parameter = SPECIAL_KK32_310_PARAMETER[:117] + "_"

        for grade in "ABCDE":
            with self.subTest(grade=grade):
                stale_path = "/".join(
                    [
                        SPECIAL_KK32_310_ROOT,
                        SPECIAL_KK32_310_SUBUNSUR,
                        stale_parameter,
                        f"Grade {grade}",
                    ]
                )
                expected_path = "/".join(
                    [
                        SPECIAL_KK32_310_ROOT,
                        SPECIAL_KK32_310_SUBUNSUR,
                        SPECIAL_KK32_310_PARAMETER,
                        f"Grade {grade}",
                    ]
                )

                self.assertGreater(len(SPECIAL_KK32_310_PARAMETER), 118)
                self.assertEqual(canonical_folder_path(stale_path), expected_path)

                public_url = public_folder_link(
                    "https://lumbungfile.kemendesa.go.id",
                    "CiJYTHFxZaJ83YF",
                    stale_path,
                )
                query = parse_qs(urlparse(public_url).query)
                self.assertEqual(query["dir"], [f"/{expected_path}"])

    def test_snapshot_refresh_replaces_stale_nested_public_urls(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade C"
        )
        payload = {
            "result": {
                "folder_path": full_path,
                "public_url": "https://example.invalid/stale",
            }
        }
        settings = SimpleNamespace(
            lumbung_host="https://lumbungfile.kemendesa.go.id",
            lumbung_share_token="CiJYTHFxZaJ83YF",
        )

        refresh_nested_public_urls(payload, settings)

        query = parse_qs(urlparse(payload["result"]["public_url"]).query)
        self.assertEqual(query["dir"], [f"/{canonical_folder_path(full_path)}"])

    def test_snapshot_seed_public_urls_are_canonicalized_without_share_token(self) -> None:
        full_path = (
            "KK 3.3 PENGAMANAN ASET NEGARA DAERAH/"
            "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat/"
            "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat "
            "sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan "
            "memperhatikan benturan kepentingan/Grade A"
        )
        stale_url = (
            "https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/"
            + "/".join(part.replace(" ", "%20") for part in full_path.split("/"))
        )
        payload = {"subunsur_details": {"x": {"folder_path": full_path, "public_url": stale_url}}}
        settings = SimpleNamespace(has_share_token=False)

        inject_public_urls(payload, settings)

        query = parse_qs(urlparse(payload["subunsur_details"]["x"]["public_url"]).query)
        self.assertEqual(query["dir"], [f"/{canonical_folder_path(full_path)}"])


if __name__ == "__main__":
    unittest.main()
