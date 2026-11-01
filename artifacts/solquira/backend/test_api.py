"""HTTP contract regression tests for Solquira demo (requires httpx)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import main


class APIContractTests(unittest.TestCase):
    def setUp(self):
        self._saved_api_key = os.environ.pop("SOLQUIRA_API_KEY", None)

    def tearDown(self):
        if self._saved_api_key is not None:
            os.environ["SOLQUIRA_API_KEY"] = self._saved_api_key

    def test_main_endpoints_and_writable_demo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(main, "DB_PATH", Path(temp_dir) / "sample.sqlite3"):
                client = TestClient(main.app)
                prefix = main.API_PREFIX
                self.assertEqual(client.get(prefix + "/healthz").status_code, 200)
                profiles = client.get(prefix + "/perfiles")
                self.assertEqual(profiles.status_code, 200)
                self.assertEqual(len(profiles.json()), 3)
                parcel = client.get(prefix + "/parcela").json()
                self.assertEqual(parcel["profileId"], "valle")
                self.assertIsNone(parcel["latitude"])
                self.assertIsNone(parcel["longitude"])
                parcel["latitude"], parcel["longitude"] = 45.0, -120.0
                parcel["waterM3Ha"] = 1800
                self.assertEqual(
                    client.put(prefix + "/parcela", json=parcel).status_code, 200
                )
                self.assertEqual(
                    client.get(prefix + "/parcela").json()["waterM3Ha"], 1800
                )
                r = client.post(
                    prefix + "/evaluacion", json={"parcel": parcel, "waterPercent": 70}
                )
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["status"], "ok")
                self.assertEqual(len(r.json()["rotations"]), 3)
                self.assertEqual(len(client.get(prefix + "/fuentes").json()), 8)
                self.assertEqual(len(client.get(prefix + "/escenarios").json()), 4)

    def test_global_point_valid_and_invalid_bounds_at_http_boundary(self):
        client = TestClient(main.app)
        p = main.default_parcel().model_dump()
        for latitude, longitude in ((45.0, -120.0), (-25.0, 135.0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                p["latitude"], p["longitude"] = latitude, longitude
                response = client.post(
                    main.API_PREFIX + "/evaluacion",
                    json={"parcel": p, "waterPercent": 100},
                )
                self.assertEqual(response.status_code, 200)
        p["latitude"], p["longitude"] = 90.1, 0
        self.assertEqual(
            client.post(
                main.API_PREFIX + "/evaluacion", json={"parcel": p, "waterPercent": 100}
            ).status_code,
            422,
        )

    def test_missing_data_and_bad_json(self):
        client = TestClient(main.app)
        p = main.default_parcel().model_dump()
        p.update({"waterM3Ha": None, "previousCrops": [], "soil": "desconocido"})
        result = client.post(
            main.API_PREFIX + "/evaluacion", json={"parcel": p, "waterPercent": 40}
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "insufficient_data")
        self.assertEqual(result.json()["rotations"], [])
        wrong = client.post(main.API_PREFIX + "/evaluacion", json={"parcela": p})
        self.assertEqual(wrong.status_code, 422)


class OptionalApiKeyTests(unittest.TestCase):
    """Exposure gate: SOLQUIRA_API_KEY set => X-Solquira-Api-Key required everywhere but healthz."""

    def setUp(self):
        self._saved = os.environ.get("SOLQUIRA_API_KEY")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SOLQUIRA_API_KEY", None)
        else:
            os.environ["SOLQUIRA_API_KEY"] = self._saved

    def test_open_mode_when_key_unset(self):
        os.environ.pop("SOLQUIRA_API_KEY", None)
        client = TestClient(main.app)
        self.assertEqual(client.get(main.API_PREFIX + "/healthz").status_code, 200)
        self.assertEqual(client.get(main.API_PREFIX + "/perfiles").status_code, 200)

    def test_key_required_when_configured(self):
        os.environ["SOLQUIRA_API_KEY"] = "test-key-123"
        client = TestClient(main.app)
        self.assertEqual(client.get(main.API_PREFIX + "/healthz").status_code, 200)
        self.assertEqual(client.get(main.API_PREFIX + "/perfiles").status_code, 401)
        self.assertEqual(
            client.get(
                main.API_PREFIX + "/perfiles", headers={"X-Solquira-Api-Key": "wrong"}
            ).status_code,
            401,
        )
        self.assertEqual(
            client.get(
                main.API_PREFIX + "/perfiles",
                headers={"X-Solquira-Api-Key": "test-key-123"},
            ).status_code,
            200,
        )

    def test_assistant_routes_also_gated(self):
        os.environ["SOLQUIRA_API_KEY"] = "test-key-123"
        client = TestClient(main.app)
        self.assertEqual(client.get("/api/v1/geospatial/layers").status_code, 401)
        self.assertEqual(
            client.get(
                "/api/v1/geospatial/layers",
                headers={"X-Solquira-Api-Key": "test-key-123"},
            ).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
