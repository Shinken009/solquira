"""Field-observation ontology and contracts for the Solquira agri-assistant gateway."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ONTOLOGY_VERSION = "solquira.field-observation/1"
REPORT_VERSION = "solquira.diagnosis-report/1"
MAX_IMAGE_BYTES = 3_000_000
MAX_AUDIO_BYTES = 5_000_000
MAX_TEXT_CHARS = 1000
_DATA_URI = re.compile(
    r"^data:(?P<mime>[a-z0-9.+/-]+);base64,(?P<b64>[A-Za-z0-9+/=\s]+)$", re.IGNORECASE
)


# Coordinates are always validated worldwide. Deployment-specific regional
# overrides cannot narrow the user's selected location.
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)

FeedState = Literal[
    "nominal", "loading", "degraded", "stale", "fallback", "unavailable"
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_data_uri(
    value: str, max_bytes: int, allowed_prefixes: tuple[str, ...]
) -> tuple[str, bytes]:
    """Validate a base64 data URI and return (mime, decoded bytes) or raise ValueError."""
    match = _DATA_URI.match(value.strip())
    if not match:
        raise ValueError("Se esperaba una imagen o audio en formato data URI base64.")
    mime = match.group("mime").lower()
    if not mime.startswith(allowed_prefixes):
        raise ValueError(f"Tipo de contenido no admitido: {mime}.")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", match.group("b64")), validate=True)
    except Exception as exc:  # noqa: BLE001 - normalization of decoder errors
        raise ValueError("Payload base64 inválido.") from exc
    if len(raw) > max_bytes:
        raise ValueError(
            f"Archivo demasiado grande ({len(raw)} bytes); máximo {max_bytes}."
        )
    return mime, raw


class IngestPayload(BaseModel):
    """POST /api/v1/telemetry/ingest request body (Telegram webhook or light web client)."""

    telegram_id: int = Field(
        ...,
        description="Identificador del chat de Telegram o usuario anónimo negativo.",
    )
    latitude: float = Field(..., ge=LAT_RANGE[0], le=LAT_RANGE[1])
    longitude: float = Field(..., ge=LON_RANGE[0], le=LON_RANGE[1])
    text_query: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    image_base64: Optional[str] = Field(
        None, description="data URI image/jpeg|png del cultivo o suelo."
    )
    audio_base64: Optional[str] = Field(
        None, description="data URI audio/wav de 3-8 s (bomba de riego)."
    )
    crop_type: Optional[str] = Field(None, max_length=60)
    parcel_name: Optional[str] = Field(None, max_length=120)
    language: Literal["es", "en"] = Field(
        "es", description="Idioma de la respuesta; es/en para audiencia internacional."
    )

    @field_validator("image_base64")
    @classmethod
    def _check_image(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        decode_data_uri(value, MAX_IMAGE_BYTES, ("image/",))
        return value

    @field_validator("audio_base64")
    @classmethod
    def _check_audio(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        decode_data_uri(value, MAX_AUDIO_BYTES, ("audio/", "application/octet-stream"))
        return value


class TaskAccepted(BaseModel):
    status: Literal["queued"] = "queued"
    task_id: str
    message: str
    estimated_latency_seconds: int


class LayerEnvelope(BaseModel):
    """Adapted God's Eye View layer contract: id/name/source/status/entries/provenance.

    feed_state follows GEV's layerSnapshot vocabulary (severity: unavailable <
    loading < degraded < stale < fallback < nominal) so a mixed roster cannot
    narrate a stale feed as live. observed_at is the source observation time
    when the provider reports one, otherwise null — never the receipt time.
    """

    id: str
    name: str
    source: str
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    feed_state: FeedState = "unavailable"
    updated_at: str
    observed_at: Optional[str] = None
    stale: bool = False
    fallback: bool = False
    note: str = ""
    entries: list[dict] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)


class WeatherTelemetry(BaseModel):
    source: str
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    temp_min_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    avg_relative_humidity_pct: Optional[float] = None
    frost_event_detected: Optional[bool] = None
    note: str = ""


class SensorFeatures(BaseModel):
    """Qualitative vibroacoustic summary (Lab4You lesson: thresholds, not lab metrics)."""

    kind: Literal["audio_fft", "none"] = "none"
    status: Literal["ok", "insufficient", "not_provided", "invalid"] = "not_provided"
    classification: Optional[
        Literal[
            "operacion_normal",
            "posible_cavitacion",
            "posible_entrada_aire",
            "sin_senal",
        ]
    ] = None
    dominant_hz: Optional[float] = None
    rms: Optional[float] = None
    duration_s: Optional[float] = None
    note: str = ""


class FieldContext(BaseModel):
    """JSON ontology handed to the multimodal model as structured context."""

    ontology: str = ONTOLOGY_VERSION
    generated_at: str = Field(default_factory=utc_now_iso)
    parcela: dict = Field(default_factory=dict)
    clima: WeatherTelemetry
    foto: dict = Field(default_factory=dict)
    sensor: SensorFeatures = Field(default_factory=SensorFeatures)
    motor_determinista: dict = Field(default_factory=dict)
    capas: dict[str, str] = Field(
        default_factory=dict,
        description="layer id -> feed_state (nominal|stale|fallback|degraded|unavailable).",
    )
    lagunas: list[str] = Field(default_factory=list)
    evidencias: list[str] = Field(default_factory=list)


class DiagnosisReport(BaseModel):
    """Structured output the language model must return (solquira.diagnosis-report/1)."""

    schema_version: str = REPORT_VERSION
    diagnostico_preliminar: str
    contexto_parcela: str
    acciones: list[str] = Field(default_factory=list)
    nota_seguridad: str
    confianza: Literal["alta", "media", "baja"] = "baja"
    lagunas_datos: list[str] = Field(default_factory=list)
    fuentes: list[str] = Field(default_factory=list)
    inference_mode: Literal["nebius_live", "local_fallback"] = "local_fallback"
    model: str = ""
    language: Literal["es", "en"] = "es"
    disclaimer: str = ""


class TaskRecord(BaseModel):
    task_id: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    telegram_id: int = 0
    report: Optional[DiagnosisReport] = None
    context_summary: dict = Field(default_factory=dict)
    error: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = utc_now_iso()
