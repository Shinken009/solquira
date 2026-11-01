"""Bounded, opt-in geographic context. Never alters the synthetic crop scenario."""
from __future__ import annotations
import copy
import json
import math
import os
import threading
import time
from collections import OrderedDict, deque
from datetime import date, datetime, timedelta, timezone
import httpx
from studio_schema import StudioLocation

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
_cache = OrderedDict()
_requests = deque()
_lock = threading.Lock()
TTL = 600
MAX_CACHE = 64
MAX_REQUESTS_HOUR = 60
LOW_PRECIPITATION_MM_PER_DAY = 1.0


def forecast_anticipation(weather, retrieved_at):
    """Descriptive seven-day indicators, never a soil balance or climate index.

    Amounts include rain/showers/snow water equivalent as supplied by Open-Meteo.
    The declared one-millimetre threshold separates low-precipitation days from
    contiguous wet-day episodes. It is not a site-specific biological threshold.
    """
    missing = ["Observed soil moisture and root-zone storage",
               "Soil infiltration, slope and observed ground cover",
               "Local precipitation measurements and precipitation phase",
               "Crop or vegetation type and growth stage"]
    result = {
        "schema_version": "solquira.anticipation/1", "state": "unavailable",
        "source": "Open-Meteo", "source_url": "https://open-meteo.com/en/docs",
        "kind": "model_forecast", "time_zone": "UTC", "retrieved_at": None,
        "period_start": None, "period_end": None,
        "method": "seven-day-descriptive-v1",
        "units": {"precipitation": "mm", "reference_et0": "mm",
                  "precipitation_minus_et0": "mm", "temperature": "°C",
                  "duration": "days", "share": "%"},
        "thresholds": {"low_precipitation_mm_per_day": LOW_PRECIPITATION_MM_PER_DAY,
                       "wet_episode_min_mm_per_day": LOW_PRECIPITATION_MM_PER_DAY},
        "missing_inputs": ["Complete seven-day forecast"] + missing,
        "limitations": [
            "Precipitation minus reference ET0 is not soil moisture, a real water balance or an irrigation dose.",
            "Seven forecast days do not diagnose drought, desertification or land degradation.",
            "Daily precipitation includes snow water equivalent; it is not effective rainfall or stored water.",
            "Daily concentration does not measure hourly intensity, runoff, flooding or infiltration.",
            "Runs and episodes are truncated at the seven-day window; no preceding or later conditions are inferred.",
        ],
    }
    try:
        stamp = datetime.fromisoformat(retrieved_at)
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError("A UTC retrieval time is required.")
        if (not isinstance(weather, dict) or weather.get("state") not in {"ready", "cached"}
                or weather.get("source") != "Open-Meteo" or weather.get("kind") != "model_forecast"
                or weather.get("time_zone") != "UTC"):
            return result
        days = weather.get("days")
        if not isinstance(days, list) or len(days) != 7:
            return result
        precipitation, et0, minima, maxima, dates = [], [], [], [], []
        for day in days:
            value = date.fromisoformat(day["date"])
            if day["date"] != value.isoformat() or dates and value - dates[-1] != timedelta(days=1):
                raise ValueError("Consecutive dated days are required.")
            dates.append(value)
            precipitation.append(_number(day["precipitation_mm"], 0, 2000))
            et0.append(_number(day["reference_et0_mm"], 0, 100))
            minima.append(_number(day["temperature_min_c"], -100, 80))
            maxima.append(_number(day["temperature_max_c"], -100, 80))
            if minima[-1] > maxima[-1]:
                raise ValueError("Invalid temperature interval.")
    except (ValueError, TypeError, KeyError, OverflowError):
        return result

    total, reference_total = math.fsum(precipitation), math.fsum(et0)
    low_days = longest_run = run = episodes = 0
    episode_total = largest_episode = 0.0
    in_episode = False
    for amount in precipitation:
        if amount < LOW_PRECIPITATION_MM_PER_DAY:
            low_days += 1
            run += 1
            longest_run = max(longest_run, run)
            largest_episode = max(largest_episode, episode_total)
            episode_total, in_episode = 0.0, False
        else:
            run = 0
            if not in_episode:
                episodes += 1
            in_episode = True
            episode_total += amount
    largest_episode = max(largest_episode, episode_total)
    maximum = max(precipitation)
    return result | {
        "state": "ready", "retrieved_at": stamp.isoformat(),
        "period_start": dates[0].isoformat(), "period_end": dates[-1].isoformat(),
        "precipitation_total_mm": round(total, 1),
        "reference_et0_total_mm": round(reference_total, 1),
        "precipitation_minus_et0_mm": round(total - reference_total, 1),
        "low_precipitation_days": low_days,
        "longest_low_precipitation_run_days": longest_run,
        "precipitation_days": 7 - low_days, "precipitation_episode_count": episodes,
        "maximum_daily_precipitation_mm": maximum,
        "wettest_day_date": dates[precipitation.index(maximum)].isoformat() if total else None,
        "wettest_day_share_percent": round(100 * maximum / total, 1) if total else None,
        "largest_episode_total_mm": round(largest_episode, 1),
        "largest_episode_share_percent": round(100 * largest_episode / total, 1) if episodes else None,
        "temperature_min_c": min(minima), "temperature_max_c": max(maxima),
        "missing_inputs": missing,
    }


def _with_anticipation(result):
    return result | {"anticipation": forecast_anticipation(result["weather"], result["retrieved_at"])}


def enabled():
    return os.environ.get("SOLQUIRA_STUDIO_GEODATA", "0") == "1"


def _number(value, lower, upper):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError("Unsupported source value.")
    return value


def parse_weather(payload):
    if not isinstance(payload, dict) or payload.get("timezone") != "GMT" and payload.get("timezone") != "UTC":
        raise ValueError("Unsupported source time zone.")
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("Unsupported time offset.")
    daily, units = payload.get("daily"), payload.get("daily_units")
    fields = {"temperature_2m_max": "°C", "temperature_2m_min": "°C", "precipitation_sum": "mm", "et0_fao_evapotranspiration": "mm"}
    if not isinstance(daily, dict) or not isinstance(units, dict) or units.get("time") != "iso8601":
        raise ValueError("Missing daily data.")
    dates = daily.get("time")
    if not isinstance(dates, list) or len(dates) != 7:
        raise ValueError("Expected seven forecast days.")
    for field, unit in fields.items():
        if units.get(field) != unit or not isinstance(daily.get(field), list) or len(daily[field]) != 7:
            raise ValueError("Unsupported units or series length.")
    days, previous = [], None
    for i, stamp in enumerate(dates):
        if not isinstance(stamp, str): raise ValueError("Missing forecast date.")
        day = date.fromisoformat(stamp)
        if stamp != day.isoformat() or previous is not None and day - previous != timedelta(days=1):
            raise ValueError("Forecast dates are not consecutive.")
        if i == 0 and abs((day - datetime.now(timezone.utc).date()).days) > 1:
            raise ValueError("Forecast date is not current.")
        previous = day
        maximum = _number(daily["temperature_2m_max"][i], -100, 80)
        minimum = _number(daily["temperature_2m_min"][i], -100, 80)
        if minimum > maximum: raise ValueError("Temperature interval is invalid.")
        days.append({"date": stamp, "temperature_max_c": maximum, "temperature_min_c": minimum,
                     "precipitation_mm": _number(daily["precipitation_sum"][i], 0, 2000),
                     "reference_et0_mm": _number(daily["et0_fao_evapotranspiration"][i], 0, 100)})
    return {"state": "ready", "source": "Open-Meteo", "source_url": "https://open-meteo.com/", "kind": "model_forecast", "time_zone": "UTC",
            "grid_point": {"latitude": _number(payload.get("latitude"), -90, 90), "longitude": _number(payload.get("longitude"), -180, 180)},
            "license_url": "https://open-meteo.com/en/terms", "days": days}


def _fetch(latitude, longitude):
    deadline = time.monotonic() + 9
    params = {"latitude": latitude, "longitude": longitude, "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration", "forecast_days": 7, "timezone": "UTC"}
    with httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", WEATHER_URL, params=params) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 65536 or time.monotonic() > deadline: raise ValueError("Source response exceeded its budget.")
                chunks.append(chunk)
            data = json.loads(b"".join(chunks), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    return parse_weather(data)


def location_context(value):
    selected = StudioLocation.model_validate(value)
    latitude, longitude = round(selected.latitude, 3), round(selected.longitude, 3)
    now = datetime.now(timezone.utc)
    imagery_day = (now - timedelta(days=3)).date().isoformat()
    imagery = {"source": "NASA GIBS", "state": "ready_to_load" if enabled() and abs(latitude) <= 85.05112878 else "disabled", "date": imagery_day,
               "layer": "MODIS_Terra_CorrectedReflectance_TrueColor", "projection": "EPSG:3857", "tile_matrix_set": "GoogleMapsCompatible_Level9", "max_level": 9,
               "url_template": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/" + imagery_day + "/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg"}
    result = {"schema_version": "solquira.location/1", "location": {"latitude": latitude, "longitude": longitude, "precision_degrees": .001},
              "retrieved_at": now.isoformat(), "imagery": imagery}
    empty = {"source": "Open-Meteo", "source_url": "https://open-meteo.com/", "kind": "model_forecast", "time_zone": "UTC", "days": []}
    if not enabled():
        return _with_anticipation(result | {"weather": empty | {"state": "disabled", "note": "Live geographic sources are off for this local session."}})
    key = (latitude, longitude)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < TTL:
            saved = copy.deepcopy(cached[1]); saved["weather"]["state"] = "cached"
            _cache.move_to_end(key)
            return saved
        while _requests and time.monotonic() - _requests[0] > 3600: _requests.popleft()
        if len(_requests) >= MAX_REQUESTS_HOUR:
            return _with_anticipation(result | {"weather": empty | {"state": "unavailable", "note": "The geographic source request limit has been reached. Try again later."}})
        _requests.append(time.monotonic())
    try:
        result["weather"] = _fetch(latitude, longitude)
    except Exception:
        return _with_anticipation(result | {"weather": empty | {"state": "unavailable", "note": "Weather could not be retrieved. Keep exploring the map or retry the forecast."}})
    result = _with_anticipation(result)
    with _lock:
        _cache[key] = (time.monotonic(), copy.deepcopy(result)); _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE: _cache.popitem(last=False)
    return result
