"""Offline inventory admission and source-isolation regressions."""
import asyncio
import copy
import json
import os
import socket
import time
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
import main
import studio_api
from land_evidence import evidence_inventory, query_point

PATH = "/solquira-api/studio/evidence"
POINT = {"latitude": 52.520008, "longitude": 13.404954}


class LandEvidenceApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"SOLQUIRA_STUDIO_MODE": "1", "SOLQUIRA_STUDIO_GEODATA": "1",
                                          "SOLQUIRA_STUDIO_ALLOWED_HOSTS": "testserver,studio.example"}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        studio_api.ADMISSION.reset(); self.addCleanup(studio_api.ADMISSION.reset)
        original = socket.socket.connect
        def deny(sock, address):
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original(sock, address)
            raise AssertionError("External sockets forbidden")
        for target, kwargs in (("socket.socket.connect", {"new": deny}),
                               ("httpx.HTTPTransport.handle_request", {"side_effect": AssertionError("Source network forbidden")}),
                               ("studio_api.current_provider", {"side_effect": AssertionError("Inference forbidden")}),
                               ("environmental_history.historical_context", {"side_effect": AssertionError("History forbidden")}),
                               ("studio_geo._fetch", {"side_effect": AssertionError("Forecast acquisition forbidden")})):
            guard = patch(target, **kwargs); guard.start(); self.addCleanup(guard.stop)
        self.client = TestClient(main.app); self.addCleanup(self.client.close)

    def headers(self, client=None):
        client = client or self.client
        response = client.get("/solquira-api/studio/bootstrap")
        self.assertEqual(response.status_code, 200)
        return {studio_api.CSRF_HEADER: response.json()["csrf_token"]}

    def bound_forecast(self, headers):
        now = datetime.now(timezone.utc)
        fixture = {"schema_version": "solquira.location/1", "location": query_point(POINT) | {"precision_degrees": .001},
                   "retrieved_at": now.isoformat(), "weather": {"state": "ready", "source": "Open-Meteo",
                   "kind": "model_forecast", "time_zone": "UTC", "days": [
                       {"date": (now.date() + timedelta(days=index)).isoformat()} for index in range(7)]}}
        with patch("studio_geo.location_context", return_value=copy.deepcopy(fixture)):
            response = self.client.post("/solquira-api/studio/location", headers=headers, json=query_point(POINT))
        self.assertEqual(response.status_code, 200)
        reference = response.json()["context_ref"]
        self.assertTrue(reference)
        return reference

    def test_session_csrf_and_origin_are_required_before_computation(self):
        with patch("land_evidence.evidence_inventory") as inventory:
            self.assertEqual(self.client.post(PATH, json=POINT).status_code, 401)
            headers = self.headers()
            self.assertEqual(self.client.post(PATH, json=POINT).status_code, 403)
            self.assertEqual(self.client.post(PATH, headers=headers | {"Origin": "https://untrusted.invalid"}, json=POINT).status_code, 403)
            self.assertEqual(self.client.post(PATH, headers=headers | {"Sec-Fetch-Site": "cross-site"}, json=POINT).status_code, 403)
            inventory.assert_not_called()

    def test_wrong_host_rejected_and_https_cookie_preserved(self):
        headers = self.headers()
        self.assertEqual(self.client.post(PATH, headers=headers | {"Host": "untrusted.invalid"}, json=POINT).status_code, 403)
        with TestClient(main.app, base_url="https://studio.example") as https:
            response = https.get("/solquira-api/studio/bootstrap")
            cookie = response.headers["set-cookie"]
            for flag in ("Secure", "HttpOnly", "SameSite=strict"):
                self.assertIn(flag, cookie)

    def test_no_reference_is_seven_missing_slots_and_never_fetches(self):
        response = self.client.post(PATH, headers=self.headers(), json=POINT)
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value["location"], POINT)
        self.assertEqual(len(value["items"]), 7)
        self.assertTrue(all(row["state"] == "missing" for row in value["items"]))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_only_session_bound_matching_forecast_can_fill_context_slot(self):
        headers = self.headers(); reference = self.bound_forecast(headers)
        response = self.client.post(PATH, headers=headers, json=POINT | {"context_ref": reference})
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value["location"], POINT)
        self.assertEqual(value["items"][0]["state"], "available")
        self.assertTrue(all(row["state"] == "missing" for row in value["items"][1:]))
        self.assertNotIn(reference, json.dumps(value))
        self.assertIsNone(value["items"][0]["coverage"])
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_unknown_cross_session_mismatched_and_expired_refs_rejected(self):
        headers = self.headers(); reference = self.bound_forecast(headers)
        self.assertEqual(self.client.post(PATH, headers=headers, json=POINT | {"context_ref": "slq-context-" + "B" * 32}).status_code, 409)
        self.assertEqual(self.client.post(PATH, headers=headers, json={"latitude": -33.45, "longitude": -70.66, "context_ref": reference}).status_code, 409)
        with TestClient(main.app) as second:
            second_headers = self.headers(second)
            self.assertEqual(second.post(PATH, headers=second_headers, json=POINT | {"context_ref": reference}).status_code, 409)
        with patch.object(studio_api.ADMISSION, "clock", return_value=time.monotonic() + 601):
            self.assertEqual(self.client.post(PATH, headers=headers, json=POINT | {"context_ref": reference}).status_code, 409)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_new_location_invalidates_previous_inventory_reference(self):
        headers = self.headers(); reference = self.bound_forecast(headers)
        with patch("studio_geo.location_context", return_value={"retrieved_at": datetime.now(timezone.utc).isoformat(), "weather": {"state": "unavailable"}}):
            response = self.client.post("/solquira-api/studio/location", headers=headers, json={"latitude": 0., "longitude": 0.})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(PATH, headers=headers, json=POINT | {"context_ref": reference}).status_code, 409)

    def test_invalid_coordinates_refs_and_extra_metadata_never_compute(self):
        headers = self.headers()
        payloads = [{}, {"latitude": 1}, POINT | {"latitude": True}, POINT | {"longitude": "1"},
                    POINT | {"latitude": 91}, POINT | {"longitude": -181}, POINT | {"context_ref": ""},
                    POINT | {"context_ref": "https://untrusted.invalid"}, POINT | {"source": {"state": "available"}},
                    POINT | {"url": "http://127.0.0.1/private"}, POINT | {"soil_carbon": 10}]
        with patch("land_evidence.evidence_inventory") as inventory:
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assertEqual(self.client.post(PATH, headers=headers, json=payload).status_code, 422)
            inventory.assert_not_called()

    def test_nonfinite_duplicate_keys_and_body_limits(self):
        headers = self.headers() | {"Content-Type": "application/json"}
        for raw in ('{"latitude":NaN,"longitude":0}', '{"latitude":Infinity,"longitude":0}',
                    '{"latitude":1,"latitude":2,"longitude":0}'):
            self.assertEqual(self.client.post(PATH, headers=headers, content=raw).status_code, 422)
        self.assertEqual(self.client.post(PATH, headers=headers, content=" " * (studio_api.MAX_BODY_BYTES + 1)).status_code, 413)
        self.assertEqual(self.client.post(PATH, headers={studio_api.CSRF_HEADER: headers[studio_api.CSRF_HEADER]}, content="text").status_code, 415)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_request_rate_and_busy_limits_are_shared_not_bypassed(self):
        headers = self.headers()
        with patch("land_evidence.evidence_inventory") as inventory:
            with patch.object(studio_api.ADMISSION, "active", studio_api.MAX_ACTIVE):
                self.assertEqual(self.client.post(PATH, headers=headers, json=POINT).status_code, 429)
            inventory.assert_not_called()
        for _ in range(studio_api.SESSION_REQUESTS):
            self.assertEqual(self.client.post(PATH, headers=headers, json=POINT).status_code, 200)
        limited = self.client.post(PATH, headers=headers, json=POINT)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_cancelled_request_retains_slot_until_inventory_worker_finishes(self):
        import httpx
        entered, release, ended = threading.Event(), threading.Event(), threading.Event()
        def blocked_inventory(*args):
            entered.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("Test did not release inventory")
                return evidence_inventory(*args)
            finally:
                ended.set()

        async def exercise():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                boot = await client.get("/solquira-api/studio/bootstrap")
                headers = {studio_api.CSRF_HEADER: boot.json()["csrf_token"]}
                pending = asyncio.create_task(client.post(PATH, json=POINT, headers=headers))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    self.assertEqual(studio_api.ADMISSION.active, 1)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    self.assertFalse(ended.is_set())
                    self.assertEqual(studio_api.ADMISSION.active, 1)
                    denied = await client.post(PATH, json=POINT, headers=headers)
                    self.assertEqual(denied.status_code, 429)
                    self.assertEqual(worker.call_count, 1)
                finally:
                    release.set()
                    if not pending.done():
                        pending.cancel()
                        try:
                            await pending
                        except asyncio.CancelledError:
                            pass
                for _ in range(100):
                    if ended.is_set() and studio_api.ADMISSION.active == 0:
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(ended.is_set())
                self.assertEqual(studio_api.ADMISSION.active, 0)
                resumed = await client.post(PATH, json=POINT, headers=headers)
                self.assertEqual(resumed.status_code, 200)
                self.assertEqual(resumed.json()["status"], "inventory_only")
        with patch.object(studio_api, "MAX_ACTIVE", 1), patch("land_evidence.evidence_inventory", side_effect=blocked_inventory) as worker:
            asyncio.run(exercise())
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_route_stays_closed_outside_studio_mode(self):
        with patch.dict(os.environ, {"SOLQUIRA_STUDIO_MODE": "0"}), patch("land_evidence.evidence_inventory") as inventory:
            self.assertEqual(self.client.post(PATH, json=POINT).status_code, 404)
            inventory.assert_not_called()
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_worker_error_releases_admission_without_refetch(self):
        headers = self.headers()
        with TestClient(main.app, raise_server_exceptions=False) as client:
            client.cookies.update(self.client.cookies)
            with patch("land_evidence.evidence_inventory", side_effect=RuntimeError("Synthetic test worker failure")):
                self.assertEqual(client.post(PATH, headers=headers, json=POINT).status_code, 500)
        self.assertEqual(studio_api.ADMISSION.active, 0)


if __name__ == "__main__":
    unittest.main()
