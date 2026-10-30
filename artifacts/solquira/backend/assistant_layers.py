"""Open-data geospatial layer registry, adapted from God's Eye View layer modules.

Each layer is an independent module with id/name/source/status/entries/provenance and
degrades honestly (unavailable/not_configured) instead of inventing data. The contract
mirrors GEV's overlayHost.setEntries/setVisible semantics, flattened for an HTTP API,
plus GEV's layerSnapshot feed-state vocabulary (nominal|loading|degraded|stale|
fallback|unavailable) and severity ranking so an aggregate never narrates a stale
or fallback feed as live. Open data only: global sources (Open-Meteo, NASA/ESA STAC)
that work beyond any single pilot region.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Awaitable, Callable

import httpx

from assistant_schema import FeedState, LayerEnvelope, WeatherTelemetry, utc_now_iso

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_LAYER_TIMEOUT_SECONDS = 5.0


def _layer_timeout() -> float:
    """Per-call env read (R7): a restart is not required to retune the budget."""
    try:
        return float(
            os.environ.get("SOLQUIRA_LAYER_TIMEOUT", str(DEFAULT_LAYER_TIMEOUT_SECONDS))
        )
    except ValueError:
        return DEFAULT_LAYER_TIMEOUT_SECONDS


def _live_enabled() -> bool:
    """Per-call env read (R7): gates can be flipped without restarting uvicorn."""
    return os.environ.get("SOLQUIRA_LIVE_ADAPTERS", "0") == "1"


_HTTP_CLIENT: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    """Shared pooled client (R6); timeouts are always passed per request."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient()
    return _HTTP_CLIENT


async def aclose_client() -> None:
    """Release the pooled client on shutdown (called from the app lifespan)."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


# GEV layerSnapshot: lower rank is more severe; overall = worst queried state.
FEED_STATE_SEVERITY: dict[str, int] = {
    "unavailable": 0,
    "loading": 1,
    "degraded": 2,
    "stale": 3,
    "fallback": 4,
    "nominal": 5,
}

Fetcher = Callable[..., Awaitable[LayerEnvelope]]

_LAYERS: dict[str, dict] = {}


def feed_state_of(
    status: str, *, stale: bool = False, fallback: bool = False
) -> FeedState:
    """Port of GEV layerFeedState for the flattened HTTP envelope."""
    if status in ("unavailable", "not_configured"):
        return "unavailable"
    if status == "degraded":
        return "degraded"
    if fallback:
        return "fallback"
    if stale:
        return "stale"
    return "nominal"


def overall_feed_state(states: list[str]) -> str:
    """Worst feed state across a roster (GEV FEED_STATE_SEVERITY)."""
    if not states:
        return "unavailable"
    return min(states, key=lambda state: FEED_STATE_SEVERITY.get(state, 0))


def register_layer(
    layer_id: str, *, name: str, source: str, license_url: str, update_interval_s: int
) -> Callable[[Fetcher], Fetcher]:
    def decorator(fetcher: Fetcher) -> Fetcher:
        _LAYERS[layer_id] = {
            "id": layer_id,
            "name": name,
            "source": source,
            "license_url": license_url,
            "update_interval_s": update_interval_s,
            "fetcher": fetcher,
        }
        return fetcher

    return decorator


def list_layers() -> list[dict]:
    return [
        {key: value for key, value in spec.items() if key != "fetcher"}
        for spec in _LAYERS.values()
    ]


def _admit_entries(entries: list[dict] | None) -> tuple[list[dict], int]:
    """Port of GEV admitRecords: entries must be dicts keyed by a non-empty kind.

    An all-invalid nonempty feed is unavailable; partial feeds keep the valid rows.
    """
    if not entries:
        return [], 0
    valid = [
        entry for entry in entries if isinstance(entry, dict) and entry.get("kind")
    ]
    return valid, len(entries) - len(valid)


def _envelope(
    spec: dict,
    status: str,
    note: str,
    entries: list[dict] | None = None,
    *,
    stale: bool = False,
    fallback: bool = False,
    observed_at: str | None = None,
    provenance_extra: dict | None = None,
) -> LayerEnvelope:
    admitted, rejected = _admit_entries(entries)
    if entries and not admitted:
        status = "unavailable"
        note = f"{note} Respuesta rechazada en admisión de registros ({len(entries)} inválidos)."
    elif rejected:
        status = "degraded" if status == "ok" else status
        note = f"{note} {rejected} registro(s) inválido(s) descartado(s)."
    provenance = {
        "license": spec["license_url"],
        "source": spec["source"],
        "update_interval_s": spec["update_interval_s"],
    }
    if provenance_extra:
        provenance.update(provenance_extra)
    return LayerEnvelope(
        id=spec["id"],
        name=spec["name"],
        source=spec["source"],
        status=status,  # type: ignore[arg-type]
        feed_state=feed_state_of(status, stale=stale, fallback=fallback),
        updated_at=utc_now_iso(),
        observed_at=observed_at,
        stale=stale,
        fallback=fallback,
        note=note,
        entries=admitted,
        provenance=provenance,
    )


def _disabled(spec: dict, reason: str) -> LayerEnvelope:
    """Session-disabled (gate off): the data is genuinely absent this session,
    so it votes on `overall` as unavailable. Distinct from a never-cabled
    extension point (status=not_configured), which is excluded from `overall`
    and reported in `layer_summary.declared_off[]` (R3)."""
    return _envelope(spec, "unavailable", reason)


# Last-good microclima envelope per rounded coordinate pair, for honest stale
# serving when a refresh fails (R2). Bounded by MAX_CACHE_AGE_SECONDS.
_MICROCLIMA_CACHE: dict[tuple[float, float], tuple[LayerEnvelope, float]] = {}
MAX_CACHE_AGE_SECONDS = 3600.0


def _classify_error(exc: Exception) -> str:
    """Stable error taxonomy for diagnostics (R2); keeps notes short and greppable."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "network"
    if isinstance(exc, ValueError):
        return "invalid_payload"
    return "unexpected"


@register_layer(
    "microclima",
    name="Microclima abierto (Open-Meteo)",
    source="Open-Meteo",
    license_url="https://open-meteo.com/en/terms",
    update_interval_s=3600,
)
async def fetch_microclima(latitude: float, longitude: float) -> LayerEnvelope:
    spec = _LAYERS["microclima"]
    if not _live_enabled():
        return _disabled(
            spec,
            "Adaptador en vivo desactivado (SOLQUIRA_LIVE_ADAPTERS=0). Sin telemetría satelital/climática en esta sesión.",
        )
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,relative_humidity_2m",
        "past_days": 2,
        "forecast_days": 1,
    }

    async def _pull() -> LayerEnvelope:
        timeout = _layer_timeout()
        response = await _client().get(OPEN_METEO_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        temps = [
            t
            for t in data.get("hourly", {}).get("temperature_2m", [])
            if isinstance(t, (int, float))
        ]
        humidity = [
            h
            for h in data.get("hourly", {}).get("relative_humidity_2m", [])
            if isinstance(h, (int, float))
        ]
        times = data.get("hourly", {}).get("time", [])
        if not temps or not humidity:
            raise ValueError("serie horaria vacía")
        entries = [
            {
                "kind": "weather_point",
                "latitude": latitude,
                "longitude": longitude,
                "temp_min_c": round(min(temps), 1),
                "temp_max_c": round(max(temps), 1),
                "avg_relative_humidity_pct": round(sum(humidity) / len(humidity), 1),
                "frost_event_detected": min(temps) <= 2.0,
            }
        ]
        observed_at = times[-1] if times and isinstance(times[-1], str) else None
        return _envelope(
            spec,
            "ok",
            "Serie horaria de las últimas 48 h recibida.",
            entries,
            observed_at=observed_at,
        )

    attempts = 0
    for attempt in (1, 2):  # single retry, only for transient transport errors
        attempts = attempt
        try:
            envelope = await _pull()
            _MICROCLIMA_CACHE[(round(latitude, 3), round(longitude, 3))] = (
                envelope,
                time.time(),
            )
            return envelope
        except Exception as exc:  # noqa: BLE001 - degradation is part of the contract
            error_type = _classify_error(exc)
            transient = error_type in ("timeout", "network")
            if transient and attempt == 1:
                await asyncio.sleep(0.2)
                continue
            note = f"Consulta falló ({error_type}) tras {attempts} intento(s); se omite la capa sin sustitutos sintéticos."
            cached = _MICROCLIMA_CACHE.get((round(latitude, 3), round(longitude, 3)))
            if cached is not None:
                last_good, cached_at = cached
                age_s = time.time() - cached_at
                if age_s <= MAX_CACHE_AGE_SECONDS:
                    stale_note = (
                        f"Último dato bueno de hace {int(age_s)} s servido como stale "
                        f"tras fallo ({error_type}); no se pinta como vivo."
                    )
                    return _envelope(
                        spec,
                        "ok",
                        stale_note,
                        list(last_good.entries),
                        stale=True,
                        observed_at=last_good.observed_at,
                        provenance_extra={
                            "error_type": error_type,
                            "attempts": attempts,
                            "cache_age_s": int(age_s),
                        },
                    )
            return _envelope(
                spec,
                "unavailable",
                note,
                stale=True,
                fallback=True,
                provenance_extra={"error_type": error_type, "attempts": attempts},
            )


@register_layer(
    "ndvi_stac",
    name="Índices de vegetación (Sentinel-2 / STAC)",
    source="NASA/ESA STAC catalogs",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    update_interval_s=86400,
)
async def fetch_ndvi_stac(latitude: float, longitude: float) -> LayerEnvelope:
    spec = _LAYERS["ndvi_stac"]
    if not _live_enabled():
        return _disabled(
            spec,
            "Punto de extensión sin cliente STAC conectado (SOLQUIRA_LIVE_ADAPTERS=0); la capa se declara, no se simula.",
        )
    return _envelope(
        spec,
        "not_configured",
        "Cliente STAC aún no implementado en esta copia; sin observaciones NDVI.",
        fallback=True,
    )


@register_layer(
    "parcelas",
    name="Delimitación de parcelas de referencia",
    source="Perfil territorial local (SQLite)",
    license_url="local-only",
    update_interval_s=0,
)
async def fetch_parcelas(latitude: float, longitude: float) -> LayerEnvelope:
    spec = _LAYERS["parcelas"]
    from main import PROFILES  # local import: main mounts this router

    entries = [
        {
            "kind": "parcel_reference",
            "profile_id": profile["id"],
            "name": profile["name"],
            "latitude": profile["latitude"],
            "longitude": profile["longitude"],
            "water_m3_ha": profile["waterM3Ha"],
            "synthetic": True,
        }
        for profile in PROFILES
    ]
    return _envelope(
        spec,
        "ok",
        "Perfiles territoriales sintéticos de la demo; no son parcelas medidas.",
        entries,
    )


async def get_layer(layer_id: str, latitude: float, longitude: float) -> LayerEnvelope:
    spec = _LAYERS.get(layer_id)
    if spec is None:
        raise KeyError(layer_id)
    return await spec["fetcher"](latitude, longitude)


async def layer_summary(latitude: float, longitude: float) -> dict:
    """GEV layerSnapshot for the whole roster: overall = worst feed state.

    R3: only *configured* layers vote on `overall` — a declared-off extension
    point (status=not_configured) must not pin the whole roster to unavailable.
    The declared-off ids are still reported in `declared_off[]` so nothing is hidden.
    """
    layers: dict[str, dict] = {}
    states: list[str] = []
    declared_off: list[str] = []
    for layer_id in list(_LAYERS):
        envelope = await get_layer(layer_id, latitude, longitude)
        if envelope.status == "not_configured":
            declared_off.append(layer_id)
        else:
            states.append(envelope.feed_state)
        layers[layer_id] = {
            "feed_state": envelope.feed_state,
            "status": envelope.status,
            "stale": envelope.stale,
            "fallback": envelope.fallback,
            "note": envelope.note,
        }
    return {
        "overall": overall_feed_state(states),
        "declared_off": declared_off,
        "severity_rank": FEED_STATE_SEVERITY,
        "layers": layers,
        "generated_at": utc_now_iso(),
    }


def microclima_weather(envelope: LayerEnvelope) -> WeatherTelemetry:
    """Flatten the microclima layer into the weather field of the prompt ontology.

    A stale/fallback envelope is honest data of lower rank: it surfaces as
    "degraded" telemetry so the R1 clamp treats it as non-live weather.
    """
    if envelope.status == "ok" and envelope.entries:
        entry = envelope.entries[0]
        return WeatherTelemetry(
            source=envelope.source,
            status="ok" if not (envelope.stale or envelope.fallback) else "degraded",
            temp_min_c=entry.get("temp_min_c"),
            temp_max_c=entry.get("temp_max_c"),
            avg_relative_humidity_pct=entry.get("avg_relative_humidity_pct"),
            frost_event_detected=entry.get("frost_event_detected"),
            note=envelope.note,
        )
    return WeatherTelemetry(
        source=envelope.source, status=envelope.status, note=envelope.note
    )
