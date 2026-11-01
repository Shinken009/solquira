"""Offline contract tests for the Solquira agri-assistant gateway (no live adapters)."""

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import time
import unittest
import wave

import httpx
import numpy as np
from fastapi.testclient import TestClient

import assistant_routes
from assistant_layers import feed_state_of, overall_feed_state
from assistant_pipeline import extract_sensor_features
from assistant_prompts import parse_report
from assistant_schema import FieldContext, WeatherTelemetry
import main

ENV_KEYS = (
    "SOLQUIRA_WEBHOOK_SECRET",
    "SOLQUIRA_INGEST_MAX",
    "SOLQUIRA_INGEST_GLOBAL_MAX",
    "SOLQUIRA_LIVE_ADAPTERS",
    "SOLQUIRA_API_KEY",
    "NEBIUS_API_KEY",
)


def make_wav_uri(samples: np.ndarray, rate: int = 16000) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        )
    return "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )


class AssistantContractTests(unittest.TestCase):
    def setUp(self):
        self._saved = {key: os.environ.pop(key, None) for key in ENV_KEYS}
        assistant_routes.INGEST_LIMITER.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        assistant_routes.INGEST_LIMITER.clear()

    @staticmethod
    def payload(**overrides):
        body = {
            "telegram_id": 12345,
            "latitude": -30.031,
            "longitude": -70.708,
            "text_query": "¿Mi parcela necesita riego esta semana?",
            "crop_type": "cereal",
            "parcel_name": "Parcela Norte",
        }
        body.update(overrides)
        return body

    def wait_for_task(self, task_id: str) -> dict:
        for _ in range(60):
            record = self.client.get(f"/api/v1/telemetry/tasks/{task_id}")
            self.assertEqual(record.status_code, 200)
            data = record.json()
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.05)
        self.fail("la tarea no terminó a tiempo")

    def test_ingest_queued_and_diagnosis_done(self):
        response = self.client.post("/api/v1/telemetry/ingest", json=self.payload())
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertTrue(body["task_id"].startswith("slq-task-"))
        self.assertEqual(body["signature"]["status"], "not_configured")

        record = self.wait_for_task(body["task_id"])
        self.assertEqual(record["status"], "done")
        report = record["report"]
        self.assertEqual(report["language"], "es")
        self.assertEqual(report["inference_mode"], "local_fallback")
        self.assertTrue(report["disclaimer"])
        self.assertIn("parcel-input", report["fuentes"])
        self.assertTrue(
            any(item.startswith("capa microclima") for item in report["lagunas_datos"])
        )
        summary = record["context_summary"]
        self.assertEqual(summary["capas"]["parcelas"], "nominal")
        self.assertEqual(summary["capas"]["microclima"], "unavailable")

    def test_english_language_echoed(self):
        response = self.client.post(
            "/api/v1/telemetry/ingest", json=self.payload(language="en")
        )
        self.assertEqual(response.status_code, 202)
        record = self.wait_for_task(response.json()["task_id"])
        report = record["report"]
        self.assertEqual(report["language"], "en")
        self.assertIn("Received query", report["diagnostico_preliminar"])
        self.assertTrue(report["disclaimer"])

    def test_validation_rejects_bad_payloads(self):
        bad_image = self.payload(image_base64="data:image/png;base64,@@no-valid@@")
        self.assertEqual(
            self.client.post("/api/v1/telemetry/ingest", json=bad_image).status_code,
            422,
        )
        international = self.payload(latitude=-20.0, longitude=-75.0)
        self.assertEqual(
            self.client.post(
                "/api/v1/telemetry/ingest", json=international
            ).status_code,
            202,
        )
        out_of_range = self.payload(latitude=95.0)
        self.assertEqual(
            self.client.post("/api/v1/telemetry/ingest", json=out_of_range).status_code,
            422,
        )
        long_text = self.payload(text_query="x" * 1001)
        self.assertEqual(
            self.client.post("/api/v1/telemetry/ingest", json=long_text).status_code,
            422,
        )
        unknown_task = self.client.get("/api/v1/telemetry/tasks/slq-task-missing")
        self.assertEqual(unknown_task.status_code, 404)

    def test_hmac_signature_enforced_when_secret_set(self):
        secret = "test-secret-not-real"
        os.environ["SOLQUIRA_WEBHOOK_SECRET"] = secret
        raw = json.dumps(self.payload(), ensure_ascii=False).encode("utf-8")
        url = "/api/v1/telemetry/ingest"
        headers = {"Content-Type": "application/json"}
        self.assertEqual(
            self.client.post(url, content=raw, headers=headers).status_code, 401
        )
        wrong = {**headers, "X-Solquira-Signature": "sha256=" + "0" * 64}
        self.assertEqual(
            self.client.post(url, content=raw, headers=wrong).status_code, 401
        )
        good = (
            "sha256="
            + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        )
        ok = self.client.post(
            url, content=raw, headers={**headers, "X-Solquira-Signature": good}
        )
        self.assertEqual(ok.status_code, 202)
        self.assertEqual(ok.json()["signature"]["status"], "verified")

    def test_rate_limit_returns_429_with_retry_after(self):
        os.environ["SOLQUIRA_INGEST_MAX"] = "2"
        url = "/api/v1/telemetry/ingest"
        self.assertEqual(self.client.post(url, json=self.payload()).status_code, 202)
        self.assertEqual(self.client.post(url, json=self.payload()).status_code, 202)
        blocked = self.client.post(url, json=self.payload())
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_layers_registry_summary_and_unknown(self):
        registry = self.client.get("/api/v1/geospatial/layers")
        self.assertEqual(registry.status_code, 200)
        ids = {layer["id"] for layer in registry.json()}
        self.assertEqual(ids, {"microclima", "ndvi_stac", "parcelas"})

        parcels = self.client.get(
            "/api/v1/geospatial/layers/parcelas",
            params={"latitude": -30.031, "longitude": -70.708},
        )
        self.assertEqual(parcels.status_code, 200)
        parcel_body = parcels.json()
        self.assertEqual(parcel_body["status"], "ok")
        self.assertEqual(parcel_body["feed_state"], "nominal")
        self.assertEqual(len(parcel_body["entries"]), 3)
        self.assertIn("license", parcel_body["provenance"])

        weather = self.client.get(
            "/api/v1/geospatial/layers/microclima",
            params={"latitude": -30.031, "longitude": -70.708},
        )
        self.assertEqual(weather.json()["status"], "unavailable")
        self.assertEqual(weather.json()["feed_state"], "unavailable")

        summary = self.client.get(
            "/api/v1/geospatial/layers/summary",
            params={"latitude": -30.031, "longitude": -70.708},
        )
        self.assertEqual(summary.status_code, 200)
        summary_body = summary.json()
        self.assertEqual(summary_body["overall"], "unavailable")
        self.assertEqual(summary_body["layers"]["parcelas"]["feed_state"], "nominal")

        missing = self.client.get(
            "/api/v1/geospatial/layers/nope",
            params={"latitude": -30.031, "longitude": -70.708},
        )
        self.assertEqual(missing.status_code, 404)

    def test_tools_registry_merges_base_and_assistant(self):
        tools = self.client.get("/api/v1/assistant/tools")
        self.assertEqual(tools.status_code, 200)
        payload = tools.json()
        self.assertIn("evaluate_demo", payload)
        self.assertIn("field_observation_ingest", payload)
        self.assertIn("open_data_layers", payload)
        self.assertIn("preflight", payload["field_observation_ingest"])


class FeedStateSemanticsTests(unittest.TestCase):
    def test_severity_and_mapping(self):
        self.assertEqual(feed_state_of("ok"), "nominal")
        self.assertEqual(feed_state_of("ok", stale=True), "stale")
        self.assertEqual(feed_state_of("ok", fallback=True), "fallback")
        self.assertEqual(feed_state_of("degraded"), "degraded")
        self.assertEqual(feed_state_of("not_configured"), "unavailable")
        self.assertEqual(feed_state_of("unavailable"), "unavailable")
        self.assertEqual(overall_feed_state(["nominal", "stale"]), "stale")
        self.assertEqual(overall_feed_state(["nominal", "unavailable"]), "unavailable")
        self.assertEqual(overall_feed_state([]), "unavailable")


class VibroacousticClassifierTests(unittest.TestCase):
    def test_silence_short_tone_and_noise(self):
        self.assertEqual(extract_sensor_features(None).status, "not_provided")

        silence = extract_sensor_features(make_wav_uri(np.zeros(32000)))
        self.assertEqual(silence.status, "ok")
        self.assertEqual(silence.classification, "sin_senal")

        short = extract_sensor_features(make_wav_uri(np.ones(4000) * 0.2))
        self.assertEqual(short.status, "insufficient")

        t = np.arange(32000) / 16000.0
        tone = extract_sensor_features(make_wav_uri(0.4 * np.sin(2 * np.pi * 60 * t)))
        self.assertEqual(tone.status, "ok")
        self.assertEqual(tone.classification, "operacion_normal")
        self.assertAlmostEqual(tone.dominant_hz, 60.0, delta=1.0)

        rng = np.random.default_rng(7)
        noise = extract_sensor_features(make_wav_uri(0.3 * rng.standard_normal(32000)))
        self.assertEqual(noise.status, "ok")
        self.assertEqual(noise.classification, "posible_cavitacion")

        invalid = extract_sensor_features(
            "data:audio/wav;base64," + base64.b64encode(b"not a wav").decode("ascii")
        )
        self.assertEqual(invalid.status, "invalid")


class HonestyClampTests(unittest.TestCase):
    """R1: la salida del modelo no puede afirmar más confianza que el contexto verificado."""

    MODEL = {
        "diagnostico_preliminar": "Amarillez leve; revisar humedad en la base.",
        "contexto_parcela": "Valle interior, riego por goteo.",
        "acciones": ["Medir humedad a 10 cm."],
        "nota_seguridad": "Orientación educativa.",
        "confianza": "alta",
        "lagunas_datos": [],
        "fuentes": ["open-meteo"],
    }

    @staticmethod
    def _context(
        *,
        clima_status="ok",
        lagunas=(),
        evidencias=("parcel-input", "demo-rules", "open-meteo"),
    ):
        return FieldContext(
            clima=WeatherTelemetry(source="open-meteo", status=clima_status),
            foto={"adjunta": True},
            lagunas=list(lagunas),
            evidencias=list(evidencias),
        )

    def _parse(self, model: dict, context: FieldContext):
        return parse_report(
            json.dumps(model, ensure_ascii=False),
            model="test-model",
            inference_mode="nebius_live",
            context=context,
            disclaimer="disclaimer de prueba",
            language="es",
        )

    def test_confianza_forced_baja_when_weather_not_ok(self):
        report = self._parse(
            dict(self.MODEL), self._context(clima_status="unavailable")
        )
        self.assertEqual(report.confianza, "baja")

    def test_confianza_capped_media_when_lagunas_present(self):
        report = self._parse(
            dict(self.MODEL),
            self._context(lagunas=["sin fotografía: fuera de alcance"]),
        )
        self.assertEqual(report.confianza, "media")

    def test_confianza_alta_preserved_without_lagunas(self):
        report = self._parse(dict(self.MODEL), self._context())
        self.assertEqual(report.confianza, "alta")

    def test_fuentes_filtered_to_traceable_evidence(self):
        model = dict(self.MODEL, fuentes=["open-meteo", "sentinel-2-inventada"])
        report = self._parse(model, self._context())
        self.assertEqual(report.fuentes, ["open-meteo"])

    def test_fuentes_backfilled_when_all_untraceable(self):
        model = dict(self.MODEL, fuentes=["todo-inventado"])
        report = self._parse(model, self._context())
        self.assertEqual(report.fuentes, ["parcel-input", "demo-rules", "open-meteo"])

    def test_system_lagunas_never_hidden_by_model_list(self):
        model = dict(
            self.MODEL, confianza="media", lagunas_datos=["detalle menor del modelo"]
        )
        report = self._parse(
            model,
            self._context(
                lagunas=[
                    "sin telemetría climática confiable (capa microclima no disponible)"
                ]
            ),
        )
        self.assertIn("detalle menor del modelo", report.lagunas_datos)
        self.assertIn(
            "sin telemetría climática confiable (capa microclima no disponible)",
            report.lagunas_datos,
        )
        self.assertEqual(report.confianza, "media")


class MicroclimaResilienceTests(unittest.TestCase):
    """R2: error_type + reintento transitorio + caché stale en microclima (sin red)."""

    GOOD_PAYLOAD = {
        "hourly": {
            "time": ["2026-10-13T00:00", "2026-10-13T01:00"],
            "temperature_2m": [12.0, 13.5],
            "relative_humidity_2m": [60.0, 61.0],
        }
    }
    EMPTY_PAYLOAD = {
        "hourly": {"time": [], "temperature_2m": [], "relative_humidity_2m": []}
    }

    def setUp(self):
        import assistant_layers

        self.layers = assistant_layers
        self._saved_live = os.environ.get("SOLQUIRA_LIVE_ADAPTERS")
        os.environ["SOLQUIRA_LIVE_ADAPTERS"] = "1"
        assistant_layers._MICROCLIMA_CACHE.clear()
        self._saved_client = assistant_layers.httpx.AsyncClient
        self._saved_pooled = assistant_layers._HTTP_CLIENT

    def tearDown(self):
        if self._saved_live is None:
            os.environ.pop("SOLQUIRA_LIVE_ADAPTERS", None)
        else:
            os.environ["SOLQUIRA_LIVE_ADAPTERS"] = self._saved_live
        self.layers.httpx.AsyncClient = self._saved_client
        self.layers._HTTP_CLIENT = self._saved_pooled
        self.layers._MICROCLIMA_CACHE.clear()

    @staticmethod
    def _http_error(status_code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
        response = httpx.Response(status_code, request=request)
        return httpx.HTTPStatusError(
            f"HTTP {status_code}", request=request, response=response
        )

    def _script(self, actions: list):
        """Install a scripted AsyncClient; items are payloads or Exceptions."""
        script = list(actions)

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, params=None, timeout=None):
                action = script.pop(0)
                if isinstance(action, Exception):
                    raise action
                return _Response(action)

        self.layers.httpx.AsyncClient = _Client
        self.layers._HTTP_CLIENT = None  # pooled client must pick up the scripted class

    def _fetch(self, latitude=-30.031, longitude=-70.708):
        return asyncio.run(self.layers.fetch_microclima(latitude, longitude))

    def test_timeout_retries_once_then_unavailable_with_error_type(self):
        self._script([httpx.TimeoutException("t1"), httpx.TimeoutException("t2")])
        envelope = self._fetch()
        self.assertEqual(envelope.status, "unavailable")
        self.assertEqual(envelope.feed_state, "unavailable")
        self.assertEqual(envelope.provenance.get("error_type"), "timeout")
        self.assertEqual(envelope.provenance.get("attempts"), 2)
        self.assertIn("(timeout)", envelope.note)

    def test_transient_timeout_then_success(self):
        self._script([httpx.TimeoutException("t1"), self.GOOD_PAYLOAD])
        envelope = self._fetch()
        self.assertEqual(envelope.status, "ok")
        self.assertFalse(envelope.stale)
        self.assertEqual(len(envelope.entries), 1)
        self.assertEqual(envelope.entries[0]["temp_max_c"], 13.5)

    def test_invalid_payload_fails_without_retry(self):
        self._script([self.EMPTY_PAYLOAD])
        envelope = self._fetch()
        self.assertEqual(envelope.status, "unavailable")
        self.assertEqual(envelope.provenance.get("error_type"), "invalid_payload")
        self.assertEqual(envelope.provenance.get("attempts"), 1)

    def test_http_error_single_attempt_with_status_in_error_type(self):
        self._script([self._http_error(500)])
        envelope = self._fetch()
        self.assertEqual(envelope.status, "unavailable")
        self.assertEqual(envelope.provenance.get("error_type"), "http_500")
        self.assertEqual(envelope.provenance.get("attempts"), 1)

    def test_stale_cache_served_after_refresh_failure(self):
        self._script([self.GOOD_PAYLOAD])
        fresh = self._fetch()
        self.assertEqual(fresh.status, "ok")

        self._script([httpx.TimeoutException("t1"), httpx.TimeoutException("t2")])
        stale = self._fetch()
        self.assertEqual(stale.status, "ok")
        self.assertTrue(stale.stale)
        self.assertFalse(stale.fallback)
        self.assertEqual(stale.feed_state, "stale")
        self.assertEqual(len(stale.entries), 1)
        self.assertEqual(stale.provenance.get("error_type"), "timeout")
        self.assertIn("cache_age_s", stale.provenance)
        self.assertIn("stale", stale.note)
        self.assertEqual(stale.observed_at, fresh.observed_at)

        weather = self.layers.microclima_weather(stale)
        self.assertEqual(weather.status, "degraded")

    def test_expired_cache_is_not_served(self):
        self._script([self.GOOD_PAYLOAD])
        self._fetch()
        key = (round(-30.031, 3), round(-70.708, 3))
        envelope, _ = self.layers._MICROCLIMA_CACHE[key]
        self.layers._MICROCLIMA_CACHE[key] = (envelope, time.time() - 7200.0)

        self._script([httpx.TimeoutException("t1"), httpx.TimeoutException("t2")])
        expired = self._fetch()
        self.assertEqual(expired.status, "unavailable")
        self.assertEqual(expired.provenance.get("error_type"), "timeout")

    def test_fresh_weather_stays_ok(self):
        self._script([self.GOOD_PAYLOAD])
        envelope = self._fetch()
        weather = self.layers.microclima_weather(envelope)
        self.assertEqual(weather.status, "ok")


class LayerSummaryOverallTests(unittest.TestCase):
    """R3: capas declaradas fuera (not_configured) no anclan el overall; se listan aparte."""

    GOOD_PAYLOAD = MicroclimaResilienceTests.GOOD_PAYLOAD

    def setUp(self):
        import assistant_layers

        self.layers = assistant_layers
        self._saved_live = os.environ.get("SOLQUIRA_LIVE_ADAPTERS")
        self._saved_client = assistant_layers.httpx.AsyncClient
        self._saved_pooled = assistant_layers._HTTP_CLIENT
        assistant_layers._MICROCLIMA_CACHE.clear()

    def tearDown(self):
        if self._saved_live is None:
            os.environ.pop("SOLQUIRA_LIVE_ADAPTERS", None)
        else:
            os.environ["SOLQUIRA_LIVE_ADAPTERS"] = self._saved_live
        self.layers.httpx.AsyncClient = self._saved_client
        self.layers._HTTP_CLIENT = self._saved_pooled
        self.layers._MICROCLIMA_CACHE.clear()

    def _script_ok_microclima(self):
        payload = self.GOOD_PAYLOAD

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def get(self, url, params=None, timeout=None):
                return _Response()

        self.layers.httpx.AsyncClient = _Client
        self.layers._HTTP_CLIENT = None

    def test_gates_off_votes_unavailable_and_declares_nothing_off(self):
        os.environ.pop("SOLQUIRA_LIVE_ADAPTERS", None)
        summary = asyncio.run(self.layers.layer_summary(-30.031, -70.708))
        self.assertEqual(summary["overall"], "unavailable")
        self.assertEqual(summary["declared_off"], [])
        self.assertEqual(summary["layers"]["microclima"]["status"], "unavailable")

    def test_ndvi_declared_off_does_not_pin_overall(self):
        os.environ["SOLQUIRA_LIVE_ADAPTERS"] = "1"
        self._script_ok_microclima()
        summary = asyncio.run(self.layers.layer_summary(-30.031, -70.708))
        self.assertEqual(summary["layers"]["ndvi_stac"]["status"], "not_configured")
        self.assertEqual(summary["declared_off"], ["ndvi_stac"])
        self.assertEqual(summary["overall"], "nominal")

    def test_live_failure_still_pins_overall_unavailable(self):
        os.environ["SOLQUIRA_LIVE_ADAPTERS"] = "1"

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def get(self, url, params=None, timeout=None):
                raise httpx.TimeoutException("down")

        self.layers.httpx.AsyncClient = _Client
        self.layers._HTTP_CLIENT = None
        summary = asyncio.run(self.layers.layer_summary(-30.031, -70.708))
        self.assertEqual(summary["layers"]["microclima"]["status"], "unavailable")
        self.assertEqual(summary["overall"], "unavailable")
        self.assertEqual(summary["declared_off"], ["ndvi_stac"])


class StreamingInferenceTests(unittest.TestCase):
    """#4108: endpoint SSE con clamps; fallback local emite meta + 1 delta + final."""

    def setUp(self):
        self._saved = {key: os.environ.pop(key, None) for key in ENV_KEYS}
        assistant_routes.INGEST_LIMITER.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        assistant_routes.INGEST_LIMITER.clear()

    @staticmethod
    def payload():
        return {
            "telegram_id": 1,
            "text_query": "¿Cómo va mi parcela?",
            "latitude": -30.031,
            "longitude": -70.708,
            "language": "es",
        }

    @staticmethod
    def parse_sse(body: str) -> list[tuple[str, dict]]:
        events = []
        for block in body.split("\n\n"):
            lines = dict(
                line.split(": ", 1) for line in block.splitlines() if ": " in line
            )
            if "event" in lines and "data" in lines:
                events.append((lines["event"], json.loads(lines["data"])))
        return events

    def test_stream_contract_local_fallback(self):
        response = self.client.post("/api/v1/assistant/stream", json=self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = self.parse_sse(response.text)
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0], "meta")
        self.assertEqual(kinds[-1], "final")
        self.assertIn("delta", kinds)
        meta = events[0][1]
        self.assertEqual(meta["inference_mode"], "local_fallback")
        final = events[-1][1]
        self.assertEqual(final["inference_mode"], "local_fallback")
        self.assertIn("nota_seguridad", final)
        self.assertIn("confianza", final)

    def test_stream_final_is_clamped_not_raw(self):
        response = self.client.post("/api/v1/assistant/stream", json=self.payload())
        events = self.parse_sse(response.text)
        final = events[-1][1]
        self.assertEqual(final["confianza"], "baja")


class ReaToolContractTests(unittest.TestCase):
    """Aspectos 2 y 3 de la herencia REA (obs #4109): formas action+object y capability report."""

    def setUp(self):
        from assistant_tools import REA_FORMS, registry

        self.forms = REA_FORMS
        self.registry = registry()

    def test_every_tool_declares_exactly_one_rea_form(self):
        for name, tool in self.registry.items():
            with self.subTest(tool=name):
                self.assertIn(tool["form"], self.forms)
                self.assertTrue(
                    tool["object"], "toda tool declara su objeto (action+object)"
                )
                self.assertIn(tool["status"], ("ready", "pending"))

    def test_registry_keeps_historical_contract_keys(self):
        # Backward compat: panel e integraciones previas consumen preflight/evidence/contract.
        for name, tool in self.registry.items():
            with self.subTest(tool=name):
                self.assertIn("preflight", tool)
                self.assertIn("evidence", tool)
                self.assertIn("contract", tool)

    def test_capability_reports_can_cannot_and_degradation(self):
        # Aspecto 3: reportar capacidad en vez de truncar schemas; nada se oculta.
        for name, tool in self.registry.items():
            capability = tool["capability"]
            with self.subTest(tool=name):
                self.assertIsInstance(capability["can"], list)
                self.assertIsInstance(capability["cannot"], list)
                self.assertTrue(capability["cannot"], "toda tool declara sus límites")
                self.assertTrue(capability["degrades_to"])
                self.assertIsInstance(capability["gates"], list)

    def test_pending_extensions_declare_empty_capability_not_hidden(self):
        pending = [
            tool for tool in self.registry.values() if tool["status"] == "pending"
        ]
        self.assertTrue(
            pending, "las extensiones pendientes se declaran, no se ocultan"
        )
        for tool in pending:
            with self.subTest(tool=tool["object"]):
                self.assertEqual(tool["capability"]["can"], [])
                self.assertEqual(tool["capability"]["degrades_to"], "not_configured")

    def test_ready_tools_report_nonempty_capability(self):
        for name, tool in self.registry.items():
            if tool["status"] == "ready":
                with self.subTest(tool=name):
                    self.assertTrue(
                        tool["capability"]["can"],
                        "una tool operativa reporta qué puede hacer",
                    )

    def test_contracts_are_provider_neutral(self):
        # Aspecto 3: sin vendor en nombre/forma/contrato/evidencia; los gates
        # conservan el nombre real de la env var porque es configuración.
        banned = ("nebius", "openai", "anthropic", "gemini")
        for name, tool in self.registry.items():
            haystack = " ".join(
                [
                    name,
                    tool["form"],
                    tool["object"],
                    tool["contract"],
                    *tool["evidence"],
                ]
            ).lower()
            with self.subTest(tool=name):
                for vendor in banned:
                    self.assertNotIn(
                        vendor, haystack, f"nombre atado al proveedor: {vendor}"
                    )

    def test_http_registry_exposes_forms_and_version(self):
        client = TestClient(main.app)
        response = client.get("/api/v1/assistant/tools")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("_registry", payload)
        self.assertEqual(payload["_registry"]["rea_forms"], list(self.forms))
        self.assertIn("form", payload["evaluate_demo"])
        self.assertIn("capability", payload["field_observation_ingest"])
        self.assertIn("contract", payload["field_observation_ingest"])


if __name__ == "__main__":
    unittest.main()
