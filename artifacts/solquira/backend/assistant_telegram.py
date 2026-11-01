"""Telegram inbound bot for the agri-assistant (long-poll getUpdates).

Design notes (adapted pattern, no external project code): per-chat state and
locks, long message splitting and a /help /status /setloc command set,
implemented on raw httpx to match ``assistant_pipeline.dispatch_result``
(no new dependencies; same TELEGRAM_BOT_TOKEN / TELEGRAM_API_BASE env
contract).

Flow: update -> IngestPayload -> STORE + run_task(suppress_dispatch=True)
-> formatted report reply. The HTTP ingest surface keeps its own dispatch
behaviour untouched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Optional

import httpx

from assistant_layers import layer_summary
from assistant_pipeline import STORE, TELEGRAM_API, run_task
from assistant_rate_limit import INGEST_LIMITER
from assistant_schema import MAX_TEXT_CHARS, IngestPayload

logger = logging.getLogger("solquira.assistant.telegram")

DEFAULT_LAT = -30.031
DEFAULT_LON = -70.708
MSG_MAX_LEN = 4000
POLL_TIMEOUT_S = 50

HELP_TEXT = {
    "es": (
        "🌱 *Solquira — asistencia de campo*\n\n"
        "/help — Esta ayuda\n"
        "/estado — Estado de las capas abiertas en tu punto\n"
        "/setloc lat lon — Guarda la ubicación de la consulta\n"
        "  Ej: /setloc -30.031 -70.708\n"
        "/lang es|en — Idioma de la respuesta\n\n"
        "Envía tu consulta como mensaje de texto; puedes adjuntar primero "
        "la ubicación con el botón de Telegram. Cada dato faltante se "
        "declara como laguna; nada se inventa."
    ),
    "en": (
        "🌱 *Solquira — field assistance*\n\n"
        "/help — This help\n"
        "/status — Open-layer feed state at your point\n"
        "/setloc lat lon — Save the query location\n"
        "  e.g. /setloc -30.031 -70.708\n"
        "/lang es|en — Answer language\n\n"
        "Send your question as a text message; you can attach a Telegram "
        "location first. Every missing datum is declared as a gap; "
        "nothing is invented."
    ),
}

FEED_LABEL = {
    "es": "Capas abiertas",
    "en": "Open layers",
}


def bot_enabled() -> bool:
    """Presence of TELEGRAM_BOT_TOKEN is the single gate (same as ESTADO I2)."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", ""))


def split_message(text: str, limit: int = MSG_MAX_LEN) -> list[str]:
    if not text:
        return ["(sin respuesta)"]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def format_report(report) -> str:
    """DiagnosisReport -> Telegram text (fields mirror the web panel card)."""
    lang = report.language if report.language in ("es", "en") else "es"
    labels = {
        "es": {
            "actions": "💡 Acciones:",
            "gaps": "🔍 Lagunas de datos:",
            "sources": "📎 Fuentes:",
            "confidence": "Confianza",
            "mode": "Modo",
            "context": "🌍 Contexto",
        },
        "en": {
            "actions": "💡 Actions:",
            "gaps": "🔍 Data gaps:",
            "sources": "📎 Sources:",
            "confidence": "Confidence",
            "mode": "Mode",
            "context": "🌍 Context",
        },
    }[lang]
    lines = [
        report.diagnostico_preliminar,
        "",
        labels["context"],
        report.contexto_parcela,
        "",
    ]
    if report.acciones:
        lines.append(labels["actions"])
        lines.extend(f"{i}. {a}" for i, a in enumerate(report.acciones, 1))
        lines.append("")
    if report.lagunas_datos:
        lines.append(labels["gaps"])
        lines.extend(f"• {gap}" for gap in report.lagunas_datos)
        lines.append("")
    if report.fuentes:
        lines.append(labels["sources"])
        lines.extend(f"• {source}" for source in report.fuentes)
        lines.append("")
    lines.append(
        f"{labels['confidence']}: {report.confianza} · {labels['mode']}: {report.inference_mode}"
    )
    lines.append("")
    lines.append(f"⚠️ {report.nota_seguridad}")
    lines.append(f"_{report.disclaimer}_")
    return "\n".join(lines)


def format_summary(summary: dict, latitude: float, longitude: float, lang: str) -> str:
    title = FEED_LABEL[lang]
    overall = summary.get("overall", "unknown")
    lines = [f"{title} @ ({latitude:.3f}, {longitude:.3f})", f"overall: {overall}", ""]
    layers = summary.get("layers") or {}
    for layer_id, feed in layers.items():
        state = feed.get("feed_state", "?")
        note = feed.get("note", "")
        lines.append(f"• {layer_id}: {state}" + (f" — {note}" if note else ""))
    generated = summary.get("generated_at")
    if generated:
        lines.append("")
        lines.append(f"generated_at: {generated}")
    return "\n".join(lines)


def parse_setloc(args: list[str]) -> Optional[tuple[float, float]]:
    if len(args) != 2:
        return None
    try:
        lat, lon = float(args[0]), float(args[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


@dataclass
class ChatState:
    latitude: float = DEFAULT_LAT
    longitude: float = DEFAULT_LON
    language: Literal["es", "en"] = "es"
    parcel_name: Optional[str] = None
    crop_type: Optional[str] = None


class ChatStates:
    """Per-chat state with per-chat locks so one user cannot queue parallel tasks."""

    def __init__(self) -> None:
        self._items: dict[int, ChatState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._lock = threading.Lock()

    def get(self, chat_id: int) -> ChatState:
        with self._lock:
            state = self._items.get(chat_id)
            if state is None:
                state = ChatState()
                self._items[chat_id] = state
            return state

    def chat_lock(self, chat_id: int) -> asyncio.Lock:
        with self._lock:
            lock = self._locks.get(chat_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[chat_id] = lock
            return lock

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._locks.clear()


class TelegramAPI:
    """Thin raw Bot API client (getUpdates long-poll + sendMessage)."""

    def __init__(
        self,
        token: Optional[str] = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise ValueError("TELEGRAM_BOT_TOKEN ausente; el bot no puede arrancar.")
        self._client = httpx.AsyncClient(
            base_url=f"{TELEGRAM_API}/bot{self._token}",
            timeout=POLL_TIMEOUT_S + 10.0,
            transport=transport,
        )

    async def get_updates(
        self, offset: int, timeout_s: int = POLL_TIMEOUT_S
    ) -> list[dict]:
        response = await self._client.post(
            "/getUpdates",
            json={
                "offset": offset,
                "timeout": timeout_s,
                "allowed_updates": ["message"],
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(
                f"getUpdates no ok: {data.get('error_code')} {data.get('description')}"
            )
        return data.get("result", [])

    async def send_message(
        self, chat_id: int, text: str, parse_mode: Optional[str] = None
    ) -> None:
        # Reports go plain: underscores like "local_fallback" break legacy Markdown.
        for chunk in split_message(text):
            payload: dict = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            response = await self._client.post("/sendMessage", json=payload)
            response.raise_for_status()

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self._client.post(
                "/sendChatAction", json={"chat_id": chat_id, "action": action}
            )
        except httpx.HTTPError as exc:  # noqa: BLE001 - cosmetic only
            logger.debug("chat action failed: %s", type(exc).__name__)

    async def aclose(self) -> None:
        await self._client.aclose()


async def handle_command(
    command: str, args: list[str], state: ChatState
) -> Optional[str]:
    lang = state.language
    if command in ("start", "help"):
        return HELP_TEXT[lang]
    if command in ("status", "estado"):
        summary = await layer_summary(state.latitude, state.longitude)
        return format_summary(summary, state.latitude, state.longitude, lang)
    if command == "setloc":
        parsed = parse_setloc(args)
        if parsed is None:
            return (
                "Uso: /setloc -30.031 -70.708"
                if lang == "es"
                else "Usage: /setloc -30.031 -70.708"
            )
        state.latitude, state.longitude = parsed
        if lang == "es":
            return f"📍 Ubicación registrada: ({state.latitude:.3f}, {state.longitude:.3f})"
        return f"📍 Location saved: ({state.latitude:.3f}, {state.longitude:.3f})"
    if command == "lang":
        if not args or args[0].lower() not in ("es", "en"):
            return "Uso: /lang es|en" if lang == "es" else "Usage: /lang es|en"
        state.language = args[0].lower()  # type: ignore[assignment]
        return (
            "✅ Idioma: español" if state.language == "es" else "✅ Language: English"
        )
    return None


async def process_update(
    update: dict,
    api: TelegramAPI,
    states: ChatStates,
    limiter=INGEST_LIMITER,
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return
    state = states.get(chat_id)
    text = (message.get("text") or "").strip()
    location = message.get("location")

    if text.startswith("/"):
        command, *args = text.lstrip("/").split()
        reply = await handle_command(command.lower(), args, state)
        if reply:
            mode = "Markdown" if command.lower() in ("start", "help") else None
            await api.send_message(chat_id, reply, parse_mode=mode)
        return

    if isinstance(location, dict):
        try:
            state.latitude = float(location["latitude"])
            state.longitude = float(location["longitude"])
        except (KeyError, TypeError, ValueError):
            return
        if state.language == "es":
            await api.send_message(
                chat_id,
                f"📍 Ubicación registrada: ({state.latitude:.3f}, {state.longitude:.3f})",
            )
        else:
            await api.send_message(
                chat_id,
                f"📍 Location saved: ({state.latitude:.3f}, {state.longitude:.3f})",
            )
        return

    if not text:
        return
    if len(text) > MAX_TEXT_CHARS:
        notice = (
            f"Tu consulta tiene {len(text)} caracteres; el máximo es {MAX_TEXT_CHARS}. Acórtala e inténtalo de nuevo."
            if state.language == "es"
            else f"Your query has {len(text)} characters; the limit is {MAX_TEXT_CHARS}. Shorten it and retry."
        )
        await api.send_message(chat_id, notice)
        return

    allowed, retry_after = limiter.allow(f"tg:{chat_id}")
    if not allowed:
        notice = (
            f"Demasiadas consultas seguidas; espera {retry_after}s e inténtalo de nuevo."
            if state.language == "es"
            else f"Too many queries in a row; wait {retry_after}s and retry."
        )
        await api.send_message(chat_id, notice)
        return

    lock = states.chat_lock(chat_id)
    if lock.locked():
        notice = (
            "⏳ Procesando mensaje anterior…"
            if state.language == "es"
            else "⏳ Processing your previous message…"
        )
        await api.send_message(chat_id, notice)
        return

    async with lock:
        await api.send_chat_action(chat_id)
        payload = IngestPayload(
            telegram_id=chat_id,
            latitude=state.latitude,
            longitude=state.longitude,
            text_query=text,
            language=state.language,
            parcel_name=state.parcel_name,
            crop_type=state.crop_type,
        )
        record = STORE.create(chat_id)
        await run_task(record.task_id, payload, suppress_dispatch=True)
        finished = STORE.get(record.task_id)
        if finished is None or finished.status != "done" or finished.report is None:
            error = finished.error if finished is not None else "TaskStoreEvicted"
            logger.warning("telegram task failed chat=%s error=%s", chat_id, error)
            notice = (
                "No se pudo generar el diagnóstico; intenta de nuevo en unos minutos."
                if state.language == "es"
                else "The diagnosis could not be generated; please try again in a few minutes."
            )
            await api.send_message(chat_id, notice)
            return
        await api.send_message(chat_id, format_report(finished.report))


async def poll_loop(api: TelegramAPI, states: ChatStates, stop: asyncio.Event) -> None:
    offset = 0
    backoff_s = 3.0
    while not stop.is_set():
        # Yield every iteration: in-process/mock transports can complete
        # without suspending, and a starved loop never fires stop timers.
        await asyncio.sleep(0)
        try:
            updates = await api.get_updates(offset=offset)
            backoff_s = 3.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - network errors must not kill the loop
            logger.warning(
                "getUpdates failed (%s); retrying in %.0fs",
                type(exc).__name__,
                backoff_s,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff_s)
            except asyncio.TimeoutError:
                pass
            backoff_s = min(backoff_s * 2, 60.0)
            continue
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            try:
                await process_update(update, api, states)
            except Exception:  # noqa: BLE001 - one bad update must not kill the loop
                logger.exception("update processing failed")


@dataclass
class _Runtime:
    api: TelegramAPI
    stop: asyncio.Event
    task: asyncio.Task
    states: ChatStates = field(default_factory=ChatStates)


_RUNTIME: Optional[_Runtime] = None


async def start_bot(states: Optional[ChatStates] = None) -> Optional[asyncio.Task]:
    """Start long-polling when TELEGRAM_BOT_TOKEN is set; no-op otherwise."""
    global _RUNTIME
    if not bot_enabled():
        logger.info("telegram bot disabled (TELEGRAM_BOT_TOKEN ausente)")
        return None
    chat_states = states or ChatStates()
    api = TelegramAPI()
    stop = asyncio.Event()
    task = asyncio.create_task(poll_loop(api, chat_states, stop))
    _RUNTIME = _Runtime(api=api, stop=stop, task=task, states=chat_states)
    logger.info("telegram bot long-polling started")
    return task


async def stop_bot(task: Optional[asyncio.Task]) -> None:
    global _RUNTIME
    runtime = _RUNTIME
    _RUNTIME = None
    if runtime is None or task is None:
        return
    runtime.stop.set()
    runtime.task.cancel()
    try:
        await runtime.task
    except asyncio.CancelledError:
        pass
    await runtime.api.aclose()
    logger.info("telegram bot stopped")


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    task = await start_bot()
    try:
        yield
    finally:
        await stop_bot(task)


async def run_standalone() -> None:
    """``python -m assistant_telegram`` — bot without the HTTP server."""
    states = ChatStates()
    api = TelegramAPI()
    stop = asyncio.Event()
    logger.info("standalone telegram bot polling")
    try:
        await poll_loop(api, states, stop)
    finally:
        await api.aclose()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] solquira.assistant.telegram: %(message)s",
        level=logging.INFO,
    )
    asyncio.run(run_standalone())
