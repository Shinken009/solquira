"""HTTP surface for the agri-assistant: webhook ingest, task polling, layer and tool registry.

Contract endpoints (see INTEGRACION.md):
  POST /api/v1/telemetry/ingest        -> 202 {status: queued, task_id, ...}
  GET  /api/v1/telemetry/tasks/{id}    -> task record with structured diagnosis
  GET  /api/v1/geospatial/layers       -> layer registry (declared, not simulated)
  GET  /api/v1/geospatial/layers/{id}  -> layer envelope for a point
  GET  /api/v1/assistant/tools         -> tool registry with preflight + evidence (REA style)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from assistant_layers import layer_summary, list_layers
from assistant_pipeline import (
    DISCLAIMER,
    enqueue,
    dump_record,
    estimate_latency,
    prepare_context,
    STORE,
)
from assistant_rate_limit import INGEST_LIMITER
from assistant_schema import (
    IngestPayload,
    LAT_RANGE,
    LON_RANGE,
    TaskAccepted,
)

logger = logging.getLogger("solquira.assistant")

# 3 MB image + 5 MB audio as base64 plus JSON envelope and headroom.
MAX_BODY_BYTES = 12_000_000
SIGNATURE_HEADER = "X-Solquira-Signature"
assistant_router = APIRouter(prefix="/api/v1", tags=["assistant"])


def _webhook_secret() -> str:
    return os.environ.get("SOLQUIRA_WEBHOOK_SECRET", "")


def verify_signature(raw_body: bytes, header_value: Optional[str]) -> dict:
    """HMAC-SHA256 over the raw body. Secret unset => dev mode, explicitly reported."""
    secret = _webhook_secret()
    if not secret:
        return {
            "status": "not_configured",
            "detail": "SOLQUIRA_WEBHOOK_SECRET ausente; firma no exigida en desarrollo.",
        }
    if not header_value:
        raise HTTPException(
            status_code=401, detail=f"Falta el encabezado {SIGNATURE_HEADER}."
        )
    expected = (
        "sha256="
        + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(expected, header_value.strip()):
        raise HTTPException(status_code=401, detail="Firma HMAC inválida.")
    return {"status": "verified", "algorithm": "hmac-sha256"}


@assistant_router.post("/telemetry/ingest", status_code=202)
async def telemetry_ingest(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    client = request.client.host if request.client else "anonymous"
    allowed, retry_after = INGEST_LIMITER.allow(client)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Demasiadas observaciones; reintenta más tarde.",
            headers={"Retry-After": str(retry_after)},
        )
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Cuerpo demasiado grande (máximo {MAX_BODY_BYTES} bytes).",
        )
    signature = verify_signature(raw, request.headers.get(SIGNATURE_HEADER))
    try:
        payload = IngestPayload.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Payload inválido: {exc}") from exc

    record = enqueue(background_tasks, payload)
    accepted = TaskAccepted(
        task_id=record.task_id,
        estimated_latency_seconds=estimate_latency(),
        message="Observación en cola; consulta el estado en GET /api/v1/telemetry/tasks/{task_id}.",
    )
    body = json.loads(accepted.model_dump_json())
    body["signature"] = signature
    return JSONResponse(status_code=202, content=body)


@assistant_router.get("/telemetry/tasks/{task_id}")
async def telemetry_task(task_id: str) -> dict:
    record = STORE.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="task_id desconocido o expirado.")
    return dump_record(record)


@assistant_router.get("/geospatial/layers")
async def geospatial_layers() -> list[dict]:
    return list_layers()


@assistant_router.get("/geospatial/layers/summary")
async def geospatial_layers_summary(
    latitude: float = Query(ge=LAT_RANGE[0], le=LAT_RANGE[1]),
    longitude: float = Query(ge=LON_RANGE[0], le=LON_RANGE[1]),
) -> dict:
    """Worst-case feed state across the roster (GEV layerSnapshot semantics)."""
    return await layer_summary(latitude, longitude)


@assistant_router.get("/geospatial/layers/{layer_id}")
async def geospatial_layer(
    layer_id: str,
    latitude: float = Query(ge=LAT_RANGE[0], le=LAT_RANGE[1]),
    longitude: float = Query(ge=LON_RANGE[0], le=LON_RANGE[1]),
):
    from assistant_layers import get_layer

    try:
        envelope = await get_layer(layer_id, latitude, longitude)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Capa desconocida: {layer_id}."
        ) from exc
    return json.loads(envelope.model_dump_json())


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@assistant_router.post("/assistant/stream")
async def assistant_stream(payload: IngestPayload) -> StreamingResponse:
    """SSE inference stream for the live demo (#4108).

    Events: meta (mode/model) -> delta* (raw model text, pre-clamp) -> final
    (clamped DiagnosisReport). Local fallback mode sends one delta + final.
    """
    from assistant_inference import _inference_settings, diagnose_stream, live_enabled
    from assistant_prompts import parse_report

    async def event_stream():
        live = live_enabled()
        mode = "nebius_live" if live else "local_fallback"
        model = _inference_settings()[2] if live else "solquira-deterministic-v1"
        yield _sse(
            "meta",
            {"inference_mode": mode, "model": model, "language": payload.language},
        )
        context = await prepare_context(payload)
        chunks: list[str] = []
        async for delta in diagnose_stream(
            context, payload.text_query, payload.image_base64, payload.language
        ):
            chunks.append(delta)
            yield _sse("delta", {"text": delta})
        raw = "".join(chunks)
        report = parse_report(
            raw,
            model=model,
            inference_mode=mode,
            context=context,
            disclaimer=DISCLAIMER,
            language=payload.language,
        )
        yield _sse("final", json.loads(report.model_dump_json()))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@assistant_router.get("/assistant/tools")
async def assistant_tools() -> dict:
    from assistant_tools import REA_FORMS, REGISTRY_VERSION, registry

    body = registry()
    body["_registry"] = {"version": REGISTRY_VERSION, "rea_forms": list(REA_FORMS)}
    return body
