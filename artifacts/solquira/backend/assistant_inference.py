"""Nebius/NVIDIA OpenAI-compatible inference client with an honest local fallback.

Live mode is opt-in (SOLQUIRA_LIVE_ADAPTERS=1 + NEBIUS_API_KEY). Without them the
pipeline returns a deterministic local scaffold built from verified context only.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from assistant_prompts import build_messages
from assistant_schema import FieldContext

logger = logging.getLogger("solquira.assistant")

NEBIUS_BASE_URL = os.environ.get(
    "NEBIUS_BASE_URL", "https://api.studio.nebius.ai/v1"
).rstrip("/")
VISION_MODEL = os.environ.get(
    "SOLQUIRA_VISION_MODEL", "meta-llama/Llama-3.2-11B-Vision-Instruct"
)
TEMPERATURE = float(os.environ.get("SOLQUIRA_MODEL_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("SOLQUIRA_MODEL_MAX_TOKENS", "700"))
INFERENCE_TIMEOUT = float(os.environ.get("SOLQUIRA_INFERENCE_TIMEOUT", "45"))


def live_enabled() -> bool:
    """Per-call env read (R7): gates and credentials without a restart."""
    return os.environ.get("SOLQUIRA_LIVE_ADAPTERS", "0") == "1" and bool(
        os.environ.get("NEBIUS_API_KEY", "")
    )


def _inference_settings() -> tuple[str, str, str, float, int, float]:
    """Snapshot per-call: base_url, api_key, model, temperature, max_tokens, timeout."""
    return (
        os.environ.get("NEBIUS_BASE_URL", NEBIUS_BASE_URL).rstrip("/"),
        os.environ.get("NEBIUS_API_KEY", ""),
        os.environ.get("SOLQUIRA_VISION_MODEL", VISION_MODEL),
        float(os.environ.get("SOLQUIRA_MODEL_TEMPERATURE", str(TEMPERATURE))),
        int(os.environ.get("SOLQUIRA_MODEL_MAX_TOKENS", str(MAX_TOKENS))),
        float(os.environ.get("SOLQUIRA_INFERENCE_TIMEOUT", str(INFERENCE_TIMEOUT))),
    )


def _local_fallback(
    context: FieldContext, text_query: str, language: str = "es"
) -> str:
    """Deterministic scaffold: only claims what the verified context contains."""
    clima = context.clima
    sensor = context.sensor
    lagunas = context.lagunas or ["sin datos" if language == "es" else "no data"]
    english = language == "en"
    acciones: list[str] = []
    if clima.frost_event_detected:
        acciones.append(
            "Check exposed plants early: the available climate feed flagged frost risk."
            if english
            else "Revisa temprano las plantas expuestas: el clima marcó riesgo de helada reciente.",
        )
    if clima.status != "ok":
        acciones.append(
            "Measure soil moisture by hand: no trustworthy weather telemetry was available."
            if english
            else "Mide la humedad del suelo a mano: no había telemetría climática confiable.",
        )
    if sensor.classification and sensor.classification != "operacion_normal":
        acciones.append(
            "Listen to the pump again and compare with the normal pattern before long irrigation runs."
            if english
            else "Escucha la bomba de nuevo y compara con el patrón normal antes de sembrar riegos largos.",
        )
    acciones.append(
        "Take a new photo in good light with a colour reference object for comparison."
        if english
        else "Toma una foto nueva con buena luz y un objeto de referencia de color para comparar.",
    )
    if clima.status == "ok":
        climate_sentence = (
            f"Available weather shows {clima.temp_min_c}°C minimum and {clima.temp_max_c}°C maximum. "
            if english
            else f"El clima disponible indica {clima.temp_min_c}°C mínima y {clima.temp_max_c}°C máxima. "
        )
    else:
        climate_sentence = (
            "No trustworthy weather telemetry in this session, so no plant-health diagnosis is issued. "
            if english
            else "No hay telemetría climática confiable en esta sesión, así que no se emite diagnóstico fitosanitario. "
        )
    sensor_sentence = (
        f"The sensor classified the signal as {sensor.classification}. "
        if sensor.classification
        else ""
    )
    engine_sentence = (
        "The comparison engine is synthetic and does not certify agronomic viability."
        if english
        else "El motor de comparación es sintético y no acredita viabilidad agronómica."
    )
    diagnostico = (
        (
            f'Received query: "{text_query}". '
            if english
            else f'Consulta recibida: "{text_query}". '
        )
        + climate_sentence
        + sensor_sentence
        + engine_sentence
    )
    return json.dumps(
        {
            "diagnostico_preliminar": diagnostico,
            "contexto_parcela": clima.note
            or (
                "Climate context not declared."
                if english
                else "Contexto climático no declarado."
            ),
            "acciones": acciones[:3],
            "nota_seguridad": (
                "Educational guidance built from demo data and open sources: confirm any decision with local advisory services. "
                "It does not diagnose diseases or apply agrochemicals."
                if english
                else "Orientación educativa con datos de demostración: confirma cualquier decisión con asesoría local. "
                "No diagnostica enfermedades ni aplica agroquímicos."
            ),
            "confianza": "baja",
            "lagunas_datos": lagunas,
            "fuentes": context.evidencias,
        },
        ensure_ascii=False,
    )


async def diagnose(
    context: FieldContext,
    text_query: str,
    image_data_uri: str | None,
    language: str = "es",
) -> tuple[str, dict]:
    """Return (raw_model_answer, meta). meta.mode is nebius_live or local_fallback."""
    if not live_enabled():
        return _local_fallback(context, text_query, language), {
            "mode": "local_fallback",
            "model": "solquira-deterministic-v1",
        }

    messages = build_messages(context, text_query, image_data_uri, language)
    base_url, api_key, model, temperature, max_tokens, timeout = _inference_settings()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"]["content"]
        logger.info("Nebius inference ok model=%s", model)
        return content, {"mode": "nebius_live", "model": model}
    except Exception as exc:  # noqa: BLE001 - resilience contract: degrade, never 500
        logger.warning(
            "Nebius inference unavailable (%s); degrading to local fallback",
            type(exc).__name__,
        )
        return _local_fallback(context, text_query, language), {
            "mode": "local_fallback",
            "model": model,
            "error": type(exc).__name__,
        }


async def diagnose_stream(
    context: FieldContext,
    text_query: str,
    image_data_uri: str | None,
    language: str = "es",
):
    """Yield raw text deltas for the SSE endpoint (#4108 live demo).

    Deltas are the model's raw output (pre-clamp); the caller must emit the
    clamped report as the final event so the honest contract still holds.
    Local fallback mode yields a single chunk; live failure degrades mid-stream.
    """
    if not live_enabled():
        yield _local_fallback(context, text_query, language)
        return

    messages = build_messages(context, text_query, image_data_uri, language)
    base_url, api_key, model, temperature, max_tokens, timeout = _inference_settings()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        yield delta
        logger.info("Nebius streaming ok model=%s", model)
    except Exception as exc:  # noqa: BLE001 - resilience contract: degrade, never 500
        logger.warning(
            "Nebius streaming unavailable (%s); degrading to local fallback",
            type(exc).__name__,
        )
        yield _local_fallback(context, text_query, language)


async def build_context(
    *, payload, weather, sensor, deterministic, capas
) -> FieldContext:
    """Assemble the JSON ontology from verified sources; lagunas records what is missing.

    capas carries each layer's feed_state (GEV vocabulary) so the model can say
    when a feed is stale or on a fallback instead of narrating it as live.
    """
    lagunas: list[str] = []
    evidencias: list[str] = ["parcel-input", "demo-rules"]
    for layer_id, state in capas.items():
        if state in ("stale", "fallback", "degraded", "unavailable"):
            lagunas.append(
                f"capa {layer_id}: feed_state={state} (cobertura ausente o degradada, no ausencia del fenómeno)"
            )
    if weather.status != "ok":
        lagunas.append(
            "sin telemetría climática confiable (capa microclima no disponible)"
        )
    else:
        evidencias.append("open-meteo")
    if payload.image_base64 is None:
        lagunas.append("sin fotografía: el análisis visual queda fuera de alcance")
    else:
        evidencias.append("foto-productor")
    if payload.audio_base64 is not None:
        evidencias.append("sensor-fft")

    parcela = {
        "nombre": payload.parcel_name or "Parcela no identificada",
        "cultivo_declarado": payload.crop_type or "no declarado",
        "coordenadas": {"lat": payload.latitude, "lon": payload.longitude},
    }
    return FieldContext(
        parcela=parcela,
        clima=weather,
        foto={"adjunta": payload.image_base64 is not None},
        sensor=sensor,
        motor_determinista=deterministic,
        capas=capas,
        lagunas=lagunas,
        evidencias=evidencias,
    )
