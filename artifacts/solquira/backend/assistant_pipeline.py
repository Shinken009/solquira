"""Async task store, qualitative vibroacoustic classifier and pipeline orchestration."""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import uuid
import wave
from collections import OrderedDict

import httpx
import numpy as np

from assistant_inference import build_context, diagnose
from assistant_layers import get_layer, microclima_weather
from assistant_prompts import parse_report
from assistant_schema import DiagnosisReport, IngestPayload, SensorFeatures, TaskRecord

logger = logging.getLogger("solquira.assistant")

TELEGRAM_API = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
TASK_TTL_SECONDS = float(os.environ.get("SOLQUIRA_TASK_TTL", "3600"))
TASK_LIMIT = 500
MIN_AUDIO_SECONDS = 1.0
SILENCE_RMS = 0.004
HIGH_BAND_RATIO_CAVITATION = 0.30
DISCLAIMER = (
    "Orientación educativa generada con datos de demostración y fuentes abiertas; "
    "no es asesoría agronómica certificada ni un diagnóstico fitosanitario."
)


class TaskStore:
    """In-process store with TTL eviction; the swap seam for Celery/Redis later."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, TaskRecord] = OrderedDict()
        self._lock = threading.Lock()

    def create(self, telegram_id: int) -> TaskRecord:
        task_id = f"slq-task-{uuid.uuid4().hex[:12]}"
        record = TaskRecord(task_id=task_id, telegram_id=telegram_id)
        _CREATED[record.task_id] = time.monotonic()
        with self._lock:
            self._evict_locked()
            self._items[task_id] = record
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._items.get(task_id)

    def update(self, task_id: str, mutate) -> TaskRecord | None:
        with self._lock:
            record = self._items.get(task_id)
            if record is None:
                return None
            mutate(record)
            record.touch()
            self._items.move_to_end(task_id)
            return record

    def _evict_locked(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, rec in self._items.items()
            if now - _created_monotonic(rec) > TASK_TTL_SECONDS
        ]
        for key in stale:
            self._items.pop(key, None)
            _CREATED.pop(key, None)
        while len(self._items) >= TASK_LIMIT:
            evicted, _ = self._items.popitem(last=False)
            _CREATED.pop(evicted, None)


_CREATED: dict[str, float] = {}


def _created_monotonic(record: TaskRecord) -> float:
    return _CREATED.get(record.task_id, 0.0)


STORE = TaskStore()


def estimate_latency() -> int:
    from assistant_inference import live_enabled

    return 12 if live_enabled() else 2


def extract_sensor_features(audio_data_uri: str | None) -> SensorFeatures:
    """Qualitative FFT classification of a pump recording (thresholds, never lab metrics)."""
    if not audio_data_uri:
        return SensorFeatures(status="not_provided")
    try:
        from assistant_schema import decode_data_uri

        _, raw = decode_data_uri(
            audio_data_uri, 5_000_000, ("audio/", "application/octet-stream")
        )
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if wav.getsampwidth() != 2:
                return SensorFeatures(
                    kind="audio_fft",
                    status="invalid",
                    note="Formato WAV distinto de 16 bits.",
                )
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
            channels = wav.getnchannels()
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        samples /= 32768.0
        duration = len(samples) / rate if rate else 0.0
        if duration < MIN_AUDIO_SECONDS:
            return SensorFeatures(
                kind="audio_fft",
                status="insufficient",
                duration_s=round(duration, 2),
                note=f"Grabación de {duration:.1f}s; se requiere al menos {MIN_AUDIO_SECONDS:.0f}s.",
            )
        rms = float(np.sqrt(np.mean(samples**2)))
        if rms < SILENCE_RMS:
            return SensorFeatures(
                kind="audio_fft",
                status="ok",
                classification="sin_senal",
                rms=round(rms, 5),
                duration_s=round(duration, 2),
                note="Señal por debajo del umbral; acerca el teléfono a la bomba.",
            )
        spectrum = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(len(samples), d=1.0 / rate)
        total = float(np.sum(spectrum**2)) or 1.0
        dominant = (
            float(freqs[int(np.argmax(spectrum[1:]) + 1)]) if len(spectrum) > 1 else 0.0
        )
        high = float(np.sum(spectrum[(freqs >= 3000) & (freqs <= 8000)] ** 2)) / total
        low_power = spectrum[(freqs >= 20) & (freqs <= 150)] ** 2
        low = float(np.sum(low_power)) / total
        low_sum = float(np.sum(low_power))
        peakiness = float(np.max(low_power)) / low_sum if low_sum > 0 else 0.0
        if high >= HIGH_BAND_RATIO_CAVITATION:
            classification, note = (
                "posible_cavitacion",
                "Energía alta en banda de 3-8 kHz (patrón de siseo/cavitación), umbral cualitativo.",
            )
        elif low >= 0.55 and peakiness < 0.5 and dominant < 200:
            classification, note = (
                "posible_entrada_aire",
                "Banda baja ancha (no un tono limpio) con fundamental por debajo de 200 Hz.",
            )
        else:
            classification, note = (
                "operacion_normal",
                "Espectro sin bandas anómalas dominantes según los umbrales del demo.",
            )
        return SensorFeatures(
            kind="audio_fft",
            status="ok",
            classification=classification,
            dominant_hz=round(dominant, 1),
            rms=round(rms, 5),
            duration_s=round(duration, 2),
            note=note,
        )
    except Exception as exc:  # noqa: BLE001 - malformed audio must not break the pipeline
        return SensorFeatures(
            kind="audio_fft",
            status="invalid",
            note=f"Audio no interpretable: {type(exc).__name__}.",
        )


def deterministic_snapshot(payload: IngestPayload) -> dict:
    """Cross-check against the synthetic demo engine; values are declared synthetic."""
    try:
        from main import EvaluationInput, Parcel, evaluate

        parcel = Parcel(
            name=payload.parcel_name or "Parcela de consulta",
            profileId="valle",
            areaHa=1,
            waterM3Ha=2400,
            soil="desconocido",
            previousCrops=[],
            priorities=["agua"],
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
        result = evaluate(EvaluationInput(parcel=parcel, waterPercent=100))
        if result.get("status") != "ok":
            return {
                "status": result.get("status"),
                "sintetico": True,
                "nota": "Motor sin datos suficientes para comparar; no se inventan secuencias.",
            }
        return {
            "status": "ok",
            "sintetico": True,
            "ruleVersion": result.get("ruleVersion"),
            "summary": result.get("summary"),
            "secuencias_elegibles": [
                {
                    "id": row["id"],
                    "score": row["score"],
                    "balance_m3_ha": row["waterBalanceM3Ha"],
                }
                for row in result.get("rotations", [])
            ],
            "nota": "Valores sintéticos de demostración; no acreditan viabilidad agronómica.",
        }
    except Exception as exc:  # noqa: BLE001 - the engine is optional context
        return {
            "status": "unavailable",
            "sintetico": True,
            "nota": f"Motor no disponible: {type(exc).__name__}.",
        }


async def dispatch_result(chat_id: int, report: DiagnosisReport) -> None:
    text = (
        f"{report.diagnostico_preliminar}\n\n"
        f"⚡ Acciones:\n"
        + "\n".join(f"{i}. {a}" for i, a in enumerate(report.acciones, 1))
        + f"\n\n🛡 {report.nota_seguridad}"
    )
    logger.info("dispatch task chat=%s mode=%s", chat_id, report.inference_mode)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not (os.environ.get("SOLQUIRA_LIVE_ADAPTERS", "0") == "1" and token):
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{TELEGRAM_API}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
    except Exception as exc:  # noqa: BLE001 - messaging failure is retried by the queue layer later
        logger.warning("telegram dispatch failed: %s", type(exc).__name__)


async def prepare_context(payload: IngestPayload):
    """Shared context assembly for run_task and the streaming endpoint."""
    envelopes = {
        layer_id: await get_layer(layer_id, payload.latitude, payload.longitude)
        for layer_id in ("microclima", "ndvi_stac", "parcelas")
    }
    capas = {layer_id: envelope.feed_state for layer_id, envelope in envelopes.items()}
    weather = microclima_weather(envelopes["microclima"])
    sensor = extract_sensor_features(payload.audio_base64)
    deterministic = deterministic_snapshot(payload)
    context = await build_context(
        payload=payload,
        weather=weather,
        sensor=sensor,
        deterministic=deterministic,
        capas=capas,
    )
    return context


async def run_task(
    task_id: str, payload: IngestPayload, *, suppress_dispatch: bool = False
) -> None:
    _CREATED[task_id] = time.monotonic()
    STORE.update(task_id, lambda rec: setattr(rec, "status", "running"))
    try:
        context = await prepare_context(payload)
        raw, meta = await diagnose(
            context, payload.text_query, payload.image_base64, payload.language
        )
        report = parse_report(
            raw,
            model=meta.get("model", ""),
            inference_mode=meta.get("mode", "local_fallback"),
            context=context,
            disclaimer=DISCLAIMER,
            language=payload.language,
        )
        STORE.update(task_id, lambda rec: _finish(rec, report, context))
        if not suppress_dispatch:
            await dispatch_result(payload.telegram_id, report)
    except Exception as exc:  # noqa: BLE001 - surface as task error, never crash the app
        logger.exception("task %s failed", task_id)
        STORE.update(task_id, lambda rec: _fail(rec, type(exc).__name__))


def _finish(record: TaskRecord, report: DiagnosisReport, context) -> None:
    record.status = "done"
    record.report = report
    record.context_summary = {
        "clima": context.clima.status,
        "sensor": context.sensor.status,
        "capas": context.capas,
        "lagunas": context.lagunas,
        "evidencias": context.evidencias,
    }


def _fail(record: TaskRecord, error: str) -> None:
    record.status = "error"
    record.error = error


def enqueue(background_tasks, payload: IngestPayload) -> TaskRecord:
    record = STORE.create(payload.telegram_id)
    background_tasks.add_task(run_task, record.task_id, payload)
    return record


def dump_record(record: TaskRecord) -> dict:
    return json.loads(record.model_dump_json())
