"""Source-bound field brief. No network, model activation or crop-rule mutation."""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from studio_geo import _number


def weather_evidence(context):
    """Small, typed model payload: no coordinates, cookies, IDs or imagery URLs."""
    weather = context["weather"]
    days = weather["days"]
    if weather.get("state") not in {"ready", "cached"} or weather.get("source") != "Open-Meteo" or len(days) != 7:
        raise ValueError("A complete source-backed forecast is required.")
    rain = round(sum(_number(d["precipitation_mm"], 0, 2000) for d in days), 1)
    et0 = round(sum(_number(d["reference_et0_mm"], 0, 100) for d in days), 1)
    maximum = max(_number(d["temperature_max_c"], -100, 80) for d in days)
    minimum = min(_number(d["temperature_min_c"], -100, 80) for d in days)
    return {"source": "Open-Meteo", "kind": "model_forecast", "time_zone": "UTC",
            "retrieved_at": context["retrieved_at"], "period_start": days[0]["date"], "period_end": days[-1]["date"],
            "rain_mm": rain, "reference_et0_mm": et0,
            "temperature_min_c": minimum, "temperature_max_c": maximum,
            "days_without_rain": sum(d["precipitation_mm"] == 0 for d in days),
            "boundary": "Separate location forecast; it does not calibrate the synthetic crop example. ET0 is not crop water need.",
            "missing_inputs": ["Crop and growth stage", "Observed soil moisture and soil type", "Root depth and irrigation efficiency", "Observed ground cover and slope"]}


def field_brief(context, focus):
    evidence = weather_evidence(context)
    focus = {"agua": "water", "historial": "history", "evidencia": "evidence", "resiliencia": "resilience"}.get(focus, focus)
    if focus not in {"water", "history", "evidence", "resilience", "decision"}:
        raise ValueError("Unknown brief focus.")
    rain, et0 = evidence["rain_mm"], evidence["reference_et0_mm"]
    below = rain < et0
    pattern = "rain_below_reference_et0" if below else "rain_at_or_above_reference_et0"
    summary = (f"The seven-day forecast totals {rain:g} mm of rain and {et0:g} mm of reference ET0. "
               + ("Rain is below reference atmospheric demand. Check stored soil water and available irrigation before planning field work."
                  if below else "Rain meets or exceeds reference atmospheric demand over this period. Check infiltration, drainage and runoff before planning field work."))
    first = {"id": "water-record", "title": "Check soil water before irrigation" if below else "Check infiltration after forecast rain",
             "detail": "Record soil moisture at the crop root zone, recent irrigation and usable water supply. Use crop stage and local advice to determine any irrigation amount." if below else "Inspect infiltration and drainage after rain, and note ponding or runoff. Forecast rain is not the same as water stored in the root zone.",
             "evidence_ids": ["forecast-7d"], "destination": "weather"}
    cover = {"id": "ground-cover", "title": "Walk the ground-cover gaps",
             "detail": "Record bare areas, residue or living cover, slope and any visible erosion. Discuss locally suitable cover options with an adviser; the map alone cannot establish soil condition.",
             "evidence_ids": ["field-inputs"], "destination": "soil"}
    missing = {"id": "complete-field-inputs", "title": "Bring the missing field inputs",
               "detail": "Add crop and growth stage, soil type, root-zone moisture, irrigation efficiency and recent management history before asking for a crop-specific water amount or treatment.",
               "evidence_ids": ["field-inputs"], "destination": "question"}
    actions = [cover, first, missing] if focus == "resilience" else [first, cover, missing]
    if focus == "evidence":
        actions[0] = {"id": "verify-forecast", "title": "Compare the forecast with field records",
                      "detail": "Check forecast dates against a local rain gauge and recent field notes. Note differences before making a field decision.",
                      "evidence_ids": ["forecast-7d", "field-inputs"], "destination": "weather"}
    return {"schema_version": "solquira.field-brief/1", "context_ref": context["context_ref"], "focus": focus,
            "location": copy.deepcopy(context["location"]), "created_at": datetime.now(timezone.utc).isoformat(),
            "forecast": evidence, "pattern": pattern, "summary": summary, "actions": actions,
            "sources": [{"id": "forecast-7d", "label": "Open-Meteo · seven-day forecast", "url": "https://open-meteo.com/", "kind": "model_forecast"},
                        {"id": "field-inputs", "label": "Field observations to collect", "kind": "missing_inputs"}],
            "missing_inputs": evidence["missing_inputs"], "external_actions_executed": False}
