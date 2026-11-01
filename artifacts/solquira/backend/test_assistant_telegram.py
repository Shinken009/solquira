"""Offline tests for the Telegram inbound bot (raw httpx, MockTransport, no network)."""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import assistant_pipeline
import assistant_telegram as tg
from assistant_rate_limit import INGEST_LIMITER
from assistant_schema import DiagnosisReport


def make_transport(sent: list, updates: list | None = None):
    queue = list(updates or [])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/getUpdates"):
            batch, queue[:] = queue[:], []
            return httpx.Response(200, json={"ok": True, "result": batch})
        if path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if path.endswith("/sendChatAction"):
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404, json={"ok": False, "description": path})

    return httpx.MockTransport(handler)


def make_report(**overrides) -> DiagnosisReport:
    body = {
        "diagnostico_preliminar": "Hojas amarillas al borde tras riego.",
        "contexto_parcela": "Coordenadas en rango global; capas en fallback local.",
        "acciones": ["Revisar humedad del suelo", "Comparar con histórico del sector"],
        "nota_seguridad": "Orientación educativa; no es asesoría certificada.",
        "confianza": "media",
        "lagunas_datos": ["Sin dato de humedad en vivo"],
        "fuentes": ["motor determinista solquira"],
        "inference_mode": "local_fallback",
        "model": "",
        "language": "es",
        "disclaimer": "Datos de demostración.",
    }
    body.update(overrides)
    return DiagnosisReport(**body)


class SyncHelpersTests(unittest.TestCase):
    def test_split_message_boundaries(self):
        self.assertEqual(tg.split_message(""), ["(sin respuesta)"])
        self.assertEqual(tg.split_message("abc", limit=10), ["abc"])
        chunks = tg.split_message("x" * 4001)
        self.assertEqual([len(c) for c in chunks], [4000, 1])

    def test_format_report_includes_all_sections(self):
        text = tg.format_report(make_report())
        self.assertIn("Hojas amarillas", text)
        self.assertIn("💡 Acciones:", text)
        self.assertIn("1. Revisar humedad", text)
        self.assertIn("🔍 Lagunas de datos:", text)
        self.assertIn("📎 Fuentes:", text)
        self.assertIn("Confianza: media", text)
        self.assertIn("local_fallback", text)
        self.assertIn("⚠️ Orientación educativa", text)
        self.assertIn("Datos de demostración", text)

    def test_format_report_english_labels(self):
        text = tg.format_report(make_report(language="en"))
        self.assertIn("💡 Actions:", text)
        self.assertIn("Confidence: media", text)

    def test_parse_setloc(self):
        self.assertEqual(tg.parse_setloc(["-30.031", "-70.708"]), (-30.031, -70.708))
        self.assertIsNone(tg.parse_setloc(["-30.031"]))
        self.assertIsNone(tg.parse_setloc(["a", "b"]))
        self.assertIsNone(tg.parse_setloc(["95", "0"]))

    def test_bot_disabled_without_token(self):
        saved = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        try:
            self.assertFalse(tg.bot_enabled())
            os.environ["TELEGRAM_BOT_TOKEN"] = "x"
            self.assertTrue(tg.bot_enabled())
        finally:
            if saved is None:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            else:
                os.environ["TELEGRAM_BOT_TOKEN"] = saved

    def test_start_bot_noop_without_token(self):
        saved = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        try:
            task = asyncio.run(tg.start_bot())
            self.assertIsNone(task)
        finally:
            if saved is not None:
                os.environ["TELEGRAM_BOT_TOKEN"] = saved


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_and_setloc_and_lang(self):
        state = tg.ChatState()
        help_text = await tg.handle_command("help", [], state)
        self.assertIn("/setloc", help_text)
        ack = await tg.handle_command("setloc", ["-30.5", "-71.2"], state)
        self.assertIn("(-30.500, -71.200)", ack)
        self.assertEqual((state.latitude, state.longitude), (-30.5, -71.2))
        bad = await tg.handle_command("setloc", ["95", "0"], state)
        self.assertIn("Uso:", bad)
        lang_ack = await tg.handle_command("lang", ["en"], state)
        self.assertIn("English", lang_ack)
        self.assertEqual(state.language, "en")
        help_en = await tg.handle_command("start", [], state)
        self.assertIn("/status", help_en)

    async def test_status_command_uses_layer_summary(self):
        state = tg.ChatState()
        text = await tg.handle_command("status", [], state)
        self.assertIn("overall:", text)

    async def test_unknown_command_is_none(self):
        self.assertIsNone(await tg.handle_command("nope", [], tg.ChatState()))


class ProcessUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        INGEST_LIMITER.clear()
        self.sent: list = []
        self.states = tg.ChatStates()
        self.api = tg.TelegramAPI(
            token="test-token", transport=make_transport(self.sent)
        )

    def tearDown(self):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        if self._saved_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = self._saved_token
        INGEST_LIMITER.clear()

    @staticmethod
    def update(
        text: str | None = None, location: dict | None = None, chat_id: int = 77
    ) -> dict:
        message: dict = {"chat": {"id": chat_id}}
        if text is not None:
            message["text"] = text
        if location is not None:
            message["location"] = location
        return {"update_id": 1, "message": message}

    async def test_text_query_gets_report_reply(self):
        await tg.process_update(
            self.update("¿Mi parcela necesita riego?"), self.api, self.states
        )
        self.assertEqual(len(self.sent), 1)
        body = self.sent[0]
        self.assertEqual(body["chat_id"], 77)
        self.assertNotIn("parse_mode", body)
        self.assertIn("Confianza", body["text"])
        self.assertIn("local_fallback", body["text"])

    async def test_help_command_reply(self):
        await tg.process_update(self.update("/help"), self.api, self.states)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("/setloc", self.sent[0]["text"])

    async def test_location_message_updates_state(self):
        await tg.process_update(
            self.update(location={"latitude": -31.5, "longitude": -70.4}),
            self.api,
            self.states,
        )
        state = self.states.get(77)
        self.assertEqual((state.latitude, state.longitude), (-31.5, -70.4))
        self.assertIn("(-31.500, -70.400)", self.sent[0]["text"])

    async def test_oversize_query_rejected_with_hint(self):
        await tg.process_update(self.update("x" * 1001), self.api, self.states)
        self.assertIn("1000", self.sent[0]["text"])

    async def test_rate_limit_reply(self):
        allowed, retry = INGEST_LIMITER.allow("tg:77")
        self.assertTrue(allowed)
        INGEST_LIMITER.clear()
        saved_max = os.environ.get("SOLQUIRA_INGEST_MAX")
        os.environ["SOLQUIRA_INGEST_MAX"] = "1"
        try:
            await tg.process_update(
                self.update("consulta primera"), self.api, self.states
            )
            self.sent.clear()
            await tg.process_update(
                self.update("consulta segunda"), self.api, self.states
            )
            self.assertEqual(len(self.sent), 1)
            self.assertIn("Demasiadas", self.sent[0]["text"])
        finally:
            if saved_max is None:
                os.environ.pop("SOLQUIRA_INGEST_MAX", None)
            else:
                os.environ["SOLQUIRA_INGEST_MAX"] = saved_max

    async def test_poll_loop_consumes_updates_and_stops(self):
        updates = [{"update_id": 10, "message": {"chat": {"id": 5}, "text": "/help"}}]
        api = tg.TelegramAPI(
            token="test-token", transport=make_transport(self.sent, updates)
        )
        stop = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.2)
            stop.set()

        stopper = asyncio.create_task(stop_soon())
        await asyncio.wait_for(tg.poll_loop(api, self.states, stop), timeout=5)
        await stopper
        self.assertEqual(len(self.sent), 1)
        self.assertIn("/setloc", self.sent[0]["text"])


class SuppressDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_task_suppress_dispatch(self):
        payload = {
            "telegram_id": -1,
            "latitude": -30.031,
            "longitude": -70.708,
            "text_query": "consulta de prueba",
        }
        from assistant_schema import IngestPayload

        body = IngestPayload(**payload)
        record = assistant_pipeline.STORE.create(-1)
        with patch.object(
            assistant_pipeline, "dispatch_result", new=AsyncMock()
        ) as mock_dispatch:
            await assistant_pipeline.run_task(
                record.task_id, body, suppress_dispatch=True
            )
            mock_dispatch.assert_not_awaited()
        record2 = assistant_pipeline.STORE.create(-1)
        with patch.object(
            assistant_pipeline, "dispatch_result", new=AsyncMock()
        ) as mock_dispatch:
            await assistant_pipeline.run_task(record2.task_id, body)
            mock_dispatch.assert_awaited_once()
        finished = assistant_pipeline.STORE.get(record.task_id)
        self.assertEqual(finished.status, "done")
        self.assertIsNotNone(finished.report)


if __name__ == "__main__":
    unittest.main()
