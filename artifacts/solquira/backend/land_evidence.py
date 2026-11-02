"""Offline territorial evidence inventory, not a degradation or MEDALUS-R model.

Only an already source-validated, session-bound forecast can populate a slot.
This module performs no acquisition, inference, persistence or numeric indices.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Literal

SCHEMA_VERSION = "solquira.land-evidence/1"
EvidenceState = Literal["available", "missing", "unavailable", "stale", "not_applicable"]
SLOTS = (
    ("forecast_context", "The coming weather", "forecast",
     "No current server-verified forecast was included in this inventory.",
     "Review the forecast for this point if you need context for the next field visit."),
    ("climate_baseline", "Long-term climate", "reanalysis",
     "No comparable multi-decadal climate dataset is attached. A seven-day forecast is not climatology.",
     "Identify a documented long-term climate record and its period before discussing aridity."),
    ("land_cover_change", "How land cover has changed", "satellite",
     "No quality-checked, comparable land-cover time series is attached. True-color map imagery is not a change assessment.",
     "Note the date and describe visible cover at an accessible place; compare like seasons before interpreting change."),
    ("land_productivity", "How vegetation productivity has changed", "satellite",
     "No validated multi-year productivity series is attached. Plant appearance is not a productivity measurement.",
     "Record which plants changed and when. A comparable seasonal series and quality masks are needed for a trend."),
    ("soil_organic_carbon", "What is known about soil carbon", "modeled_soil",
     "No soil-carbon dataset, sampling depth, date or local measurement is attached. Teaching profiles do not fill this gap.",
     "Identify an existing soil analysis with location, depth, method and date before planning additional sampling."),
    ("terrain", "What is known about terrain", "terrain",
     "No elevation or slope dataset is attached. The Cesium ellipsoid is not a terrain survey.",
     "From a safe place, note where runoff leaves marks. A documented elevation dataset is needed to estimate slope."),
    ("field_observations", "What has been observed in the field", "self_reported",
     "No georeferenced field observations were submitted to this inventory. Local notes are not shared automatically.",
     "Use the existing land record to note what you actually observed, its date and one check to repeat."),
)


def _number(value, lower, upper):
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError("Coordinates and coverage must be finite WGS84 numbers.")
    return float(value)


def _point(point):
    if not isinstance(point, dict) or set(point) != {"latitude", "longitude"}:
        raise ValueError("A complete global point is required.")
    return {"latitude": _number(point["latitude"], -90, 90),
            "longitude": _number(point["longitude"], -180, 180)}


def query_point(point):
    """Match the existing browser's forecast query copy; never alter stored raw GPS."""
    selected = _point(point)
    return {key: math.floor(value * 1000 + 0.5) / 1000 for key, value in selected.items()}


def footprint_contains(point, bbox):
    """Closed WGS84 footprint; west > east represents antimeridian crossing.

    This validates supplied metadata only. It does not invent a footprint for a
    source that supplied a query point or a grid point but no spatial extent.
    """
    selected = _point(point)
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("A WGS84 bounding box has four coordinates.")
    west, south, east, north = (_number(bbox[0], -180, 180), _number(bbox[1], -90, 90),
                                _number(bbox[2], -180, 180), _number(bbox[3], -90, 90))
    if south > north:
        raise ValueError("Footprint latitude bounds are reversed.")
    longitudes = (selected["longitude"],)
    if abs(selected["longitude"]) == 180:
        longitudes = (-180., 180.)
    inside_longitude = any(west <= longitude <= east if west <= east
                           else longitude >= west or longitude <= east for longitude in longitudes)
    return south <= selected["latitude"] <= north and inside_longitude


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit time zone is required.")
    return value.astimezone(timezone.utc)


def metadata_state(point, *, slot_id, nature, retrieved_at, valid_until, period_start,
                   period_end, coverage=None, now=None, source_available=True,
                   applicable=True) -> EvidenceState:
    """Assess metadata eligibility, never source accuracy or scientific validity.

    Pure helper for future reviewed adapters. It accepts no URLs and fetches
    nothing; malformed metadata are rejected, not repaired or extrapolated.
    """
    selected = _point(point)
    if slot_id not in {slot[0] for slot in SLOTS}:
        raise ValueError("Unknown evidence slot.")
    if slot_id == "climate_baseline" and nature == "forecast":
        raise ValueError("Forecasts cannot populate a climate baseline.")
    if nature != next(slot[2] for slot in SLOTS if slot[0] == slot_id):
        raise ValueError("Evidence nature does not match this slot.")
    if type(source_available) is not bool or type(applicable) is not bool:
        raise ValueError("Source and applicability states must be explicit booleans.")
    start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    if start > end:
        raise ValueError("Evidence period is reversed.")
    retrieved, expires, current = _aware(retrieved_at), _aware(valid_until), _aware(now or datetime.now(timezone.utc))
    if expires <= retrieved or retrieved > current + timedelta(seconds=60):
        raise ValueError("Evidence freshness metadata are invalid.")
    covered = coverage is None or footprint_contains(selected, coverage)
    if not applicable or not covered:
        return "not_applicable"
    if not source_available:
        return "unavailable"
    if current >= expires:
        return "stale"
    return "available"


def _empty(slot):
    identity, label, nature, reason, next_observation = slot
    return {"id": identity, "label": label, "state": "missing", "reason": reason,
            "nature": nature, "source": None, "period": None, "retrieved_at": None,
            "spatial_support": None, "units": None, "coverage": None,
            "quality": "not_assessed", "uncertainty": "No source-specific uncertainty has been assessed for this slot.",
            "limitations": ["Missing evidence is not evidence that degradation is absent."],
            "next_observation": next_observation}


def evidence_inventory(point, forecast_context=None, *, now=None):
    """List current knowledge and gaps without calling any environmental source.

    forecast_context must be resolved by the API from this session's opaque
    server reference. The API never accepts a client-authored source object.
    """
    selected, current = _point(point), _aware(now or datetime.now(timezone.utc))
    items = [_empty(slot) for slot in SLOTS]
    if forecast_context is not None:
        item = items[0]
        try:
            context = forecast_context
            if not isinstance(context, dict) or not isinstance(context.get("location"), dict):
                raise ValueError("Invalid bound context.")
            bound = {key: context["location"][key] for key in ("latitude", "longitude")}
            if _point(bound) != query_point(selected):
                raise ValueError("The forecast belongs to another query point.")
            weather = context["weather"]
            if weather["source"] != "Open-Meteo" or weather["kind"] != "model_forecast" or weather["time_zone"] != "UTC":
                raise ValueError("Unsupported forecast provenance.")
            if weather["state"] not in {"ready", "cached"}:
                item.update(state="unavailable", reason="The included forecast source is unavailable.")
            else:
                days = weather["days"]
                if not isinstance(days, list) or len(days) != 7:
                    raise ValueError("A complete seven-day source period is required.")
                dates = [date.fromisoformat(day["date"]) for day in days]
                if any(dates[index] - dates[index - 1] != timedelta(days=1) for index in range(1, 7)):
                    raise ValueError("Forecast period is not consecutive.")
                retrieved = _aware(context["retrieved_at"])
                state = metadata_state(selected, slot_id="forecast_context", nature="forecast",
                                       retrieved_at=retrieved, valid_until=retrieved + timedelta(minutes=10),
                                       period_start=dates[0].isoformat(), period_end=dates[-1].isoformat(), now=current)
                # A timestamp alone does not make an old forecast period current.
                if state == "available" and abs((dates[0] - current.date()).days) > 1:
                    state = "stale"
                item.update(state=state,
                            reason="A current session-bound model forecast is available as context, not as a land measurement."
                            if state == "available" else "The included forecast is no longer current; refresh it explicitly if needed.",
                            source={"id": "open-meteo-forecast", "name": "Open-Meteo", "version": None,
                                    "method": "seven-day-model-forecast", "url": "https://open-meteo.com/en/docs",
                                    "license_url": "https://open-meteo.com/en/terms"},
                            period={"start": dates[0].isoformat(), "end": dates[-1].isoformat(), "time_zone": "UTC"},
                            retrieved_at=retrieved.isoformat(), units=["mm", "°C"],
                            spatial_support={"description": "Modeled grid forecast. Grid resolution and footprint were not supplied by this adapter; not a parcel measurement.",
                                             "resolution_m": None, "query_precision_degrees": .001},
                            quality="metadata_checked",
                            uncertainty="Forecast uncertainty is not quantified by this inventory. Coordinate precision is not model resolution.",
                            limitations=["Metadata checks cover source identity and time, not spatial footprint or scientific validation.",
                                         "Reference ET0 is not soil moisture, crop water use or an irrigation prescription.",
                                         "A seven-day forecast cannot establish climatology, degradation or desertification."])
        except (ValueError, KeyError, TypeError, OverflowError):
            item.update(state="unavailable", reason="Included forecast metadata could not be verified; no replacement data were acquired.")
    return {"schema_version": SCHEMA_VERSION, "status": "inventory_only",
            "scientific_validation": "not_validated", "is_medalus_r": False,
            "location": selected, "generated_at": current.isoformat(), "items": items,
            "limitations": ["This inventory lists evidence and gaps. It calculates no degradation index, risk class or intervention ranking.",
                            "It does not implement MEDALUS-R, SDG 15.3.1, a water balance or irrigation recommendations.",
                            "No new source, historical query, field observation or model call is triggered by this inventory."]}
