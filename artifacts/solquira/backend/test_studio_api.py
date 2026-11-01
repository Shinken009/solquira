"""Offline Studio admission regression tests; no accounts, sockets or providers."""
import asyncio
import os
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from starlette.datastructures import Headers

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import studio_api

SCENARIO = {"profile_id": "valle", "water_percent": 100, "previous_crop": "cereal"}


class StudioApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"SOLQUIRA_STUDIO_MODE": "1", "SOLQUIRA_STUDIO_ALLOWED_HOSTS": "testserver,studio.example"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        studio_api.ADMISSION.reset()
        self.addCleanup(studio_api.ADMISSION.reset)
        self.evaluate = Mock(return_value={"comparison_id": "slq-example", "synthetic": True})
        self.explain = Mock(return_value={
            "report": {"comparison_id": "slq-example", "synthetic": True},
            "trace": [{"tool": "comparar_escenarios", "status": "executed"}],
            "inference_mode": "local_fallback", "outcome": "disabled", "model": None,
        })
        self.service = SimpleNamespace(
            studio_status=Mock(return_value={"available": False, "mode": "local_fallback", "model": None}),
            evaluate_studio=self.evaluate, explain_studio=self.explain,
        )
        self.service_patch = patch.object(studio_api, "_service", return_value=self.service)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)
        self.network = patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        original_connect = socket.socket.connect
        def deny_external_connect(sock, address):
            # Windows asyncio needs a loopback socketpair for its event loop.
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original_connect(sock, address)
            raise AssertionError("External socket network forbidden")
        self.socket_network = patch("socket.socket.connect", new=deny_external_connect)
        self.socket_network.start()
        self.addCleanup(self.socket_network.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def bootstrap(self, client=None):
        client = client or self.client
        response = client.get("/solquira-api/studio/bootstrap")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def post(self, path="evaluate", value=None, csrf=None, headers=None):
        return self.client.post("/solquira-api/studio/" + path, json=SCENARIO if value is None else value,
                                headers={studio_api.CSRF_HEADER: csrf or "", **(headers or {})})

    def test_bootstrap_contract_cookie_and_no_store(self):
        response = self.client.get("/solquira-api/studio/bootstrap")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["schema_version"], "solquira.studio/1")
        self.assertEqual(len(data["profiles"]), 3)
        self.assertEqual(len(data["history_options"]), 4)
        self.assertFalse(data["inference"]["available"])
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("Secure;", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("token", self.client.cookies.get(studio_api.COOKIE))

    def test_https_cookie_is_secure(self):
        with TestClient(main.app, base_url="https://studio.example") as client:
            response = client.get("/solquira-api/studio/bootstrap")
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_session_is_reused_and_csrf_not_rotated(self):
        first = self.bootstrap()
        cookie = self.client.cookies.get(studio_api.COOKIE)
        second = self.bootstrap()
        self.assertEqual(first, second)
        self.assertEqual(cookie, self.client.cookies.get(studio_api.COOKIE))

    def test_evaluation_returns_expected_envelope(self):
        csrf = self.bootstrap()
        response = self.post(csrf=csrf)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["scenario"], SCENARIO)
        self.assertEqual(data["run"]["inference_mode"], "deterministic")
        self.assertEqual(data["run"]["outcome"], "success")
        self.assertEqual(data["request_id"][:12], "slq-request-")
        self.evaluate.assert_called_once_with(SCENARIO)

    def test_agent_never_injects_live_provider(self):
        csrf = self.bootstrap()
        response = self.post("agent", {"scenario": SCENARIO, "query": "Explain the water constraint."}, csrf)
        self.assertEqual(response.status_code, 200)
        self.explain.assert_called_once_with(SCENARIO, "Explain the water constraint.", provider=None)
        self.assertEqual(response.json()["run"]["inference_mode"], "local_fallback")

    def test_proposal_preserved_without_replacing_original(self):
        proposal = {**SCENARIO, "water_percent": 40}
        self.explain.return_value["proposed_scenario"] = proposal
        csrf = self.bootstrap()
        response = self.post("agent", {"scenario": SCENARIO, "query": "What if there is less water?"}, csrf)
        self.assertEqual(response.json()["scenario"], SCENARIO)
        self.assertEqual(response.json()["proposed_scenario"], proposal)

    def test_unexpected_live_mode_is_rejected(self):
        self.explain.return_value["inference_mode"] = "nebius_live"
        csrf = self.bootstrap()
        self.assertEqual(self.post("agent", {"scenario": SCENARIO, "query": "Explain"}, csrf).status_code, 503)

    def test_location_uses_session_and_csrf_without_enabling_legacy(self):
        csrf=self.bootstrap()
        value={"latitude":45.2,"longitude":3.1}
        self.assertEqual(self.post("location",value=value).status_code,403)
        response=self.post("location",value=value,csrf=csrf)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["weather"]["state"],"disabled")
        self.assertEqual(self.client.get('/api/v1/geospatial/layers').status_code,404)

    def test_location_rejects_untrusted_urls_and_invalid_coordinates(self):
        csrf=self.bootstrap()
        for value in ({"latitude":True,"longitude":1.},{"latitude":float('inf'),"longitude":1.},{"latitude":91.,"longitude":1.},{"latitude":1.,"longitude":2.,"url":"https://untrusted.invalid"}):
            if value['latitude']==float('inf'):continue  # Strict JSON cases are already covered by body admission tests.
            with self.subTest(value=value):self.assertEqual(self.post('location',value=value,csrf=csrf).status_code,422)

    def test_explicit_trial_provider_reaches_admitted_service(self):
        provider = SimpleNamespace(mode="provider_stub")
        self.explain.return_value["inference_mode"] = "provider_stub"
        csrf = self.bootstrap()
        with patch.object(studio_api, "current_provider", return_value=provider):
            response = self.post("agent", {"scenario": SCENARIO, "query": "Explain"}, csrf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["inference_mode"], "provider_stub")
        self.explain.assert_called_once_with(SCENARIO, "Explain", provider=provider)

    def test_unknown_session_does_not_reach_engine(self):
        response = self.post(csrf="forged")
        self.assertEqual(response.status_code, 401)
        self.evaluate.assert_not_called()

    def test_csrf_is_session_bound(self):
        csrf = self.bootstrap()
        with TestClient(main.app) as other:
            other.get("/solquira-api/studio/bootstrap")
            response = other.post("/solquira-api/studio/evaluate", json=SCENARIO, headers={studio_api.CSRF_HEADER: csrf})
        self.assertEqual(response.status_code, 403)
        self.evaluate.assert_not_called()

    def test_expiration_enforced_at_read(self):
        csrf = self.bootstrap()
        token = self.client.cookies.get(studio_api.COOKIE)
        with studio_api.ADMISSION.lock:
            studio_api.ADMISSION.sessions[token]["expires"] = 0
        self.assertEqual(self.post(csrf=csrf).status_code, 401)
        self.assertNotIn(token, studio_api.ADMISSION.sessions)
        self.evaluate.assert_not_called()

    def test_cross_origin_and_null_origin_rejected(self):
        csrf = self.bootstrap()
        for origin in ("https://other.example", "null"):
            with self.subTest(origin=origin):
                self.assertEqual(self.post(csrf=csrf, headers={"Origin": origin}).status_code, 403)
        self.evaluate.assert_not_called()

    def test_cross_site_fetch_rejected_without_origin(self):
        csrf = self.bootstrap()
        self.assertEqual(self.post(csrf=csrf, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_explicit_dev_origin_allowlist(self):
        csrf = self.bootstrap()
        with patch.dict(os.environ, {"SOLQUIRA_STUDIO_ALLOWED_ORIGINS": "http://127.0.0.1:5173"}):
            response = self.post(csrf=csrf, headers={"Origin": "http://127.0.0.1:5173"})
        self.assertEqual(response.status_code, 200)

    def test_body_limit_before_engine_or_json_validation(self):
        csrf = self.bootstrap()
        response = self.client.post("/solquira-api/studio/evaluate", content="{" + "x" * 9000,
                                    headers={studio_api.CSRF_HEADER: csrf, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.evaluate.assert_not_called()
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_stream_body_limit_without_content_length(self):
        csrf = self.bootstrap()
        def parts():
            yield b"{" + b"x" * 5000
            yield b"x" * 5000
        response = self.client.post("/solquira-api/studio/evaluate", content=parts(),
                                    headers={studio_api.CSRF_HEADER: csrf, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.evaluate.assert_not_called()

    def test_invalid_json_nonfinite_and_content_type(self):
        csrf = self.bootstrap()
        for body in ('{"bad":', '{"water_percent":NaN}'):
            response = self.client.post("/solquira-api/studio/evaluate", content=body,
                                        headers={studio_api.CSRF_HEADER: csrf, "Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post("/solquira-api/studio/evaluate", content="text",
                          headers={studio_api.CSRF_HEADER: csrf}).status_code, 415)
        self.evaluate.assert_not_called()

    def test_schema_extra_fields_bool_and_wrong_steps_rejected(self):
        csrf = self.bootstrap()
        for value in ({**SCENARIO, "water_percent": True}, {**SCENARIO, "water_percent": 41},
                      {**SCENARIO, "owner": "FORGED_PRIVATE_MARKER"}, {**SCENARIO, "profile_id": "global"}):
            response = self.post(value=value, csrf=csrf)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("FORGED_PRIVATE_MARKER", response.text)
        self.evaluate.assert_not_called()

    def test_unknown_field_names_are_not_reflected(self):
        csrf = self.bootstrap()
        response = self.post(value={**SCENARIO, "PRIVATE_FIELD_NAME_MARKER": "value"}, csrf=csrf)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("PRIVATE_FIELD_NAME_MARKER", response.text)
        self.evaluate.assert_not_called()

    def test_deeply_nested_json_is_sanitized_before_engine(self):
        csrf = self.bootstrap()
        response = self.client.post("/solquira-api/studio/evaluate", content="[" * 1500 + "0" + "]" * 1500,
                                    headers={studio_api.CSRF_HEADER: csrf, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(studio_api.ADMISSION.active, 0)
        self.evaluate.assert_not_called()

    def test_invalid_declared_request_length(self):
        csrf = self.bootstrap()
        for length in ("-1", "not-a-number"):
            response = self.client.post("/solquira-api/studio/evaluate", content="{}",
                                        headers={studio_api.CSRF_HEADER: csrf, "Content-Type": "application/json", "Content-Length": length})
            self.assertEqual(response.status_code, 400)
        self.evaluate.assert_not_called()

    def test_bootstrap_service_error_is_sanitized(self):
        self.service.studio_status.side_effect = RuntimeError("RAW_BOOTSTRAP_PRIVATE_MARKER")
        response = self.client.get("/solquira-api/studio/bootstrap")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("RAW_BOOTSTRAP_PRIVATE_MARKER", response.text)

    def test_cancelled_admission_releases_its_slot(self):
        token, session, _ = studio_api.ADMISSION.bootstrap(None, "local")
        request = SimpleNamespace(headers=Headers({"Host":"testserver",studio_api.CSRF_HEADER: session["csrf"]}), cookies={studio_api.COOKIE: token})
        async def cancelled():
            async with studio_api._admitted(request):
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(cancelled())
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_http_inflight_limit_prevents_second_service_execution(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_service(scenario):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release service")
            return {"comparison_id": "slq-example", "synthetic": True}
        self.evaluate.side_effect = blocked_service
        csrf = self.bootstrap()
        with patch.object(studio_api, "MAX_ACTIVE", 1), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.post, csrf=csrf)
            try:
                self.assertTrue(entered.wait(3))
                second = self.post(csrf=csrf)
                self.assertEqual(second.status_code, 429)
                self.assertEqual(self.evaluate.call_count, 1)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5).status_code, 200)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_scenario_fields_are_required_without_silent_defaults(self):
        csrf = self.bootstrap()
        values = [{}] + [{key: value for key, value in SCENARIO.items() if key != missing} for missing in SCENARIO]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(self.post(value=value, csrf=csrf).status_code, 422)
                self.assertEqual(self.post("agent", {"scenario": value, "query": "Explain"}, csrf).status_code, 422)
        self.evaluate.assert_not_called()
        self.explain.assert_not_called()

    def test_root_path_closes_all_legacy_and_private_surfaces(self):
        with TestClient(main.app, root_path="/prefix") as client, \
             patch.object(main, "fetch_nasa_power", side_effect=AssertionError("NASA must not execute")) as nasa, \
             patch.object(main, "evaluate", side_effect=AssertionError("Legacy engine must not execute")) as engine, \
             patch.object(main, "save_parcel", side_effect=AssertionError("Shared parcel must not be written")) as save:
            for method, path in (("GET", "/solquira-api/fuentes/power?latitude=-30&longitude=-71&start=2026-01-01&end=2026-01-02"),
                                 ("POST", "/api/v1/assistant/stream"), ("POST", "/api/v1/telemetry/ingest"),
                                 ("GET", "/api/v1/geospatial/layers/summary"),
                                 ("PUT", "/solquira-api/parcela"), ("POST", "/solquira-api/evaluacion"),
                                 ("GET", "/docs"), ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"),
                                 ("GET", "/openapi.json"), ("GET", "/qa-preview/")):
                with self.subTest(path=path):
                    response = client.request(method, "/prefix" + path, json=SCENARIO)
                    self.assertEqual(response.status_code, 404)
            nasa.assert_not_called()
            engine.assert_not_called()
            save.assert_not_called()
        self.evaluate.assert_not_called()

    def test_root_path_studio_bootstrap_and_health_remain_accessible(self):
        with patch.dict(os.environ, {"SOLQUIRA_API_KEY": "test-only-not-real"}), TestClient(main.app, root_path="/prefix") as client:
            response = client.get("/prefix/solquira-api/studio/bootstrap")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["schema_version"], "solquira.studio/1")
            self.assertEqual(client.get("/prefix/solquira-api/healthz").status_code, 200)

    def test_prefixed_session_cookie_supports_full_studio_flow(self):
        with patch.dict(os.environ, {"SOLQUIRA_API_KEY": "test-only-not-real"}), TestClient(main.app, root_path="/prefix") as client:
            response = client.get("/prefix/solquira-api/studio/bootstrap")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Path=/prefix/", response.headers["set-cookie"])
            headers = {studio_api.CSRF_HEADER: response.json()["csrf_token"]}
            evaluated = client.post("/prefix/solquira-api/studio/evaluate", json=SCENARIO, headers=headers)
            self.assertEqual(evaluated.status_code, 200)
            self.assertEqual(evaluated.json()["scenario"], SCENARIO)
            explained = client.post("/prefix/solquira-api/studio/agent", json={"scenario": SCENARIO, "query": "Explain"}, headers=headers)
            self.assertEqual(explained.status_code, 200)
            self.assertEqual(client.get("/prefix/solquira-api/healthz").status_code, 200)

    def test_cancelled_http_request_keeps_slot_until_real_worker_finishes(self):
        import httpx
        entered, release, ended = threading.Event(), threading.Event(), threading.Event()
        def blocked_service(scenario):
            entered.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("Test did not release service")
                return {"comparison_id": "slq-example", "synthetic": True}
            finally:
                ended.set()
        self.evaluate.side_effect = blocked_service

        async def exercise():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                boot = await client.get("/solquira-api/studio/bootstrap")
                headers = {studio_api.CSRF_HEADER: boot.json()["csrf_token"]}
                pending = asyncio.create_task(client.post("/solquira-api/studio/evaluate", json=SCENARIO, headers=headers))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    self.assertEqual(studio_api.ADMISSION.active, 1)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    self.assertFalse(ended.is_set())
                    self.assertEqual(studio_api.ADMISSION.active, 1)
                    for _ in range(3):
                        denied = await client.post("/solquira-api/studio/evaluate", json=SCENARIO, headers=headers)
                        self.assertEqual(denied.status_code, 429)
                    self.assertEqual(self.evaluate.call_count, 1)
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
                    await asyncio.sleep(0.01)
                self.assertTrue(ended.is_set())
                self.assertEqual(studio_api.ADMISSION.active, 0)
                resumed = await client.post("/solquira-api/studio/evaluate", json=SCENARIO, headers=headers)
                self.assertEqual(resumed.status_code, 200)
                self.assertEqual(studio_api.ADMISSION.active, 0)
        with patch.object(studio_api, "MAX_ACTIVE", 1):
            asyncio.run(exercise())

    def test_empty_or_excessive_question_rejected(self):
        csrf = self.bootstrap()
        for query in ("  ", "x" * 1001):
            self.assertEqual(self.post("agent", {"scenario": SCENARIO, "query": query}, csrf).status_code, 422)
        self.explain.assert_not_called()

    def test_per_session_rate_limit_before_expensive_work(self):
        csrf = self.bootstrap()
        with patch.object(studio_api, "SESSION_REQUESTS", 2):
            self.assertEqual(self.post(csrf=csrf).status_code, 200)
            self.assertEqual(self.post(csrf=csrf).status_code, 200)
            response = self.post(csrf=csrf)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(self.evaluate.call_count, 2)

    def test_global_rate_limit_across_sessions(self):
        csrf = self.bootstrap()
        with patch.object(studio_api, "GLOBAL_REQUESTS", 1):
            self.assertEqual(self.post(csrf=csrf).status_code, 200)
            with TestClient(main.app) as other:
                other_csrf = self.bootstrap(other)
                response = other.post("/solquira-api/studio/evaluate", json=SCENARIO, headers={studio_api.CSRF_HEADER: other_csrf})
            self.assertEqual(response.status_code, 429)
        self.assertEqual(self.evaluate.call_count, 1)

    def test_concurrency_limit_releases_cleanly(self):
        admission = studio_api.Admission()
        token, session, _ = admission.bootstrap(None, "local")
        for _ in range(studio_api.MAX_ACTIVE):
            admission.acquire(token, session["csrf"])
        with self.assertRaises(HTTPException) as error:
            admission.acquire(token, session["csrf"])
        self.assertEqual(error.exception.status_code, 429)
        for _ in range(studio_api.MAX_ACTIVE):
            admission.release()
        self.assertEqual(admission.active, 0)

    def test_bootstrap_abuse_and_store_bounds(self):
        admission = studio_api.Admission()
        with patch.object(studio_api, "IP_BOOTSTRAPS", 2):
            admission.bootstrap(None, "one")
            admission.bootstrap(None, "one")
            with self.assertRaises(HTTPException):
                admission.bootstrap(None, "one")
        with patch.object(studio_api, "MAX_SESSIONS", 2):
            admission.bootstrap(None, "two")
            admission.bootstrap(None, "three")
        self.assertLessEqual(len(admission.sessions), 2)
        self.assertLessEqual(len(admission.bootstrap_ips), 2)

    def test_api_key_does_not_block_studio_or_health(self):
        with patch.dict(os.environ, {"SOLQUIRA_API_KEY": "test-only-not-real"}):
            csrf = self.bootstrap()
            self.assertEqual(self.post(csrf=csrf).status_code, 200)
            self.assertEqual(self.client.get("/solquira-api/healthz").status_code, 200)
            self.assertNotEqual(self.client.get("/").status_code, 401)

    def test_legacy_and_private_surfaces_closed(self):
        csrf = self.bootstrap()
        for method, path in (("POST", "/api/v1/assistant/stream"), ("POST", "/api/v1/telemetry/ingest"),
                             ("GET", "/api/v1/geospatial/layers/summary"), ("GET", "/solquira-api/fuentes/power"),
                             ("PUT", "/solquira-api/parcela"), ("POST", "/solquira-api/evaluacion"),
                             ("GET", "/docs"), ("GET", "/openapi.json"), ("GET", "/qa-preview/")):
            with self.subTest(path=path):
                response = self.client.request(method, path, json=SCENARIO, headers={studio_api.CSRF_HEADER: csrf})
                self.assertEqual(response.status_code, 404)
        self.evaluate.assert_not_called()

    def test_studio_off_is_not_exposed(self):
        with patch.dict(os.environ, {"SOLQUIRA_STUDIO_MODE": "0"}):
            self.assertEqual(self.client.get("/solquira-api/studio/bootstrap").status_code, 404)

    def test_errors_are_sanitized_and_slot_released(self):
        self.evaluate.side_effect = RuntimeError("RAW_PRIVATE_ERROR")
        csrf = self.bootstrap()
        response = self.post(csrf=csrf)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("RAW_PRIVATE_ERROR", response.text)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_studio_startup_never_starts_telegram(self):
        with patch.object(main.assistant_telegram, "start_bot") as start, patch.object(main.assistant_telegram, "stop_bot") as stop:
            with TestClient(main.app):
                pass
        start.assert_not_called()
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()

