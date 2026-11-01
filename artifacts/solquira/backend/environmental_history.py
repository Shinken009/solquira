"""Optional ERA5 monthly context; no imports activate a source or model.

One fixed-model archive request compares complete calendar months only. ERA5
is gridded reanalysis, not a station or parcel observation. No SPI, soil-water
balance or degradation diagnosis is inferred from the precipitation anomaly.
"""

from __future__ import annotations

import calendar
import copy
import json
import math
import threading
import time
from collections import OrderedDict, deque
from datetime import date, datetime, timedelta, timezone

import httpx

from studio_geo import enabled
from studio_schema import StudioLocation

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_URL = "https://open-meteo.com/en/docs/historical-weather-api"
BASELINE_START_YEAR = 1991
BASELINE_END_YEAR = 2020
BASELINE_YEARS = 30
SOURCE_LAG_DAYS = 5
MAX_RESPONSE_BYTES = 1_000_000
MAX_REQUESTS_HOUR = 1
MAX_REQUESTS_DAY = 4
MAX_CACHE = 16
CACHE_TTL = 86400
NEAR_ZERO_MM = 0.01
_cache = OrderedDict()
_requests = deque()
_inflight = set()
_lock = threading.Lock()

LIMITATIONS = [
    "ERA5 is approximately 0.25-degree gridded model reanalysis, not a station or parcel measurement.",
    "The same calendar month is compared with 1991–2020; this is not a seasonal forecast, SPI or a diagnosis of desertification.",
    "Precipitation includes rain and snow water equivalent; it is not effective rainfall, stored soil water or irrigation need.",
    "Thirty complete baseline months and the complete target month are required; no missing values are invented.",
    "Percentage anomaly is unavailable when baseline precipitation is below 0.01 mm.",
    "The latest complete month respects the source's five-day publication lag; reanalysis may later be revised.",
    "One archive HTTP request spans decades and can count as multiple provider API units; this prototype limits uncached lookups to one per hour and four per day per process.",
]


def _today():
    return datetime.now(timezone.utc).date()


def target_period(as_of=None):
    """Most recent full calendar month within the lagged source availability."""
    available = (as_of or _today()) - timedelta(days=SOURCE_LAG_DAYS)
    last_day = calendar.monthrange(available.year, available.month)[1]
    if available.day != last_day:
        available = available.replace(day=1) - timedelta(days=1)
    start = available.replace(day=1)
    if start.year <= BASELINE_END_YEAR:
        raise ValueError("A target after the fixed baseline is required.")
    return start, available


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Unsupported historical source value.")
    return value


def summarize_history(payload, start, end):
    """Validate the fixed source window and reconstruct thirty monthly totals."""
    if not isinstance(payload, dict) or payload.get("timezone") not in {"GMT", "UTC"}:
        raise ValueError("Historical source must use UTC.")
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("Historical source offset must be zero.")
    if start.day != 1 or end != date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]):
        raise ValueError("Compare a complete calendar month.")
    if start.year <= BASELINE_END_YEAR:
        raise ValueError("Target overlaps the reference baseline.")
    units, daily = payload.get("daily_units"), payload.get("daily")
    if not isinstance(units, dict) or units.get("time") != "iso8601" or units.get("precipitation_sum") != "mm":
        raise ValueError("Historical units must be daily millimetres.")
    if not isinstance(daily, dict):
        raise ValueError("Missing historical daily series.")
    stamps, values = daily.get("time"), daily.get("precipitation_sum")
    first = date(BASELINE_START_YEAR, 1, 1)
    expected_days = (end - first).days + 1
    if not isinstance(stamps, list) or not isinstance(values, list) or len(stamps) != expected_days or len(values) != expected_days:
        raise ValueError("Historical dates or daily coverage are incomplete.")
    totals = {year: [] for year in range(BASELINE_START_YEAR, BASELINE_END_YEAR + 1)}
    target_values = []
    for index, (stamp, value) in enumerate(zip(stamps, values)):
        day = first + timedelta(days=index)
        if stamp != day.isoformat():
            raise ValueError("Historical dates must be consecutive and exact.")
        amount = _number(value, 0, 2000)
        if day.month == start.month and day.year in totals:
            totals[day.year].append(amount)
        if start <= day <= end:
            target_values.append(amount)
    for year, amounts in totals.items():
        if len(amounts) != calendar.monthrange(year, start.month)[1]:
            raise ValueError("A baseline calendar month is incomplete.")
    if len(target_values) != (end-start).days + 1:
        raise ValueError("The target calendar month is incomplete.")
    baseline_totals = [math.fsum(totals[year]) for year in sorted(totals)]
    mean = math.fsum(baseline_totals) / BASELINE_YEARS
    target = math.fsum(target_values)
    anomaly = target - mean
    return {
        "grid_point": {
            "latitude": _number(payload.get("latitude"), -90, 90),
            "longitude": _number(payload.get("longitude"), -180, 180),
        },
        "precipitation_mm": round(target, 3),
        "baseline_mean_mm": round(mean, 3),
        "anomaly_mm": round(anomaly, 3),
        "anomaly_percent": round(anomaly / mean * 100, 2) if mean >= NEAR_ZERO_MM else None,
        "baseline_monthly_totals": [
            {"year": year, "precipitation_mm": round(math.fsum(amounts), 3), "days": len(amounts)}
            for year, amounts in sorted(totals.items())
        ],
        "coverage": {
            "target_days": len(target_values),
            "target_expected_days": (end-start).days + 1,
            "baseline_complete_years": len(totals),
            "missing_days": 0,
        },
    }


def _fetch(latitude, longitude, start, end):
    deadline = time.monotonic() + 25
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": f"{BASELINE_START_YEAR}-01-01",
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "models": "era5",
        "timezone": "UTC",
        "cell_selection": "nearest",
    }
    with httpx.Client(timeout=httpx.Timeout(20, connect=3), follow_redirects=False, trust_env=False,
                      headers={"User-Agent": "Solquira-Historical-Context/1"}) as client:
        with client.stream("GET", ARCHIVE_URL, params=params) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                    raise ValueError("Historical source exceeded its budget.")
                chunks.append(chunk)
            return json.loads(b"".join(chunks), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def historical_context(value):
    """Explicit, opt-in archive lookup. Never called by import or navigation."""
    selected = StudioLocation.model_validate(value)
    latitude, longitude = round(selected.latitude, 3), round(selected.longitude, 3)
    start, end = target_period()
    result = {
        "schema_version": "solquira.history/1",
        "location": {"latitude": latitude, "longitude": longitude, "precision_degrees": 0.001},
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "history": {
            "source": "Open-Meteo",
            "source_url": SOURCE_URL,
            "model": "ERA5",
            "kind": "model_reanalysis",
            "time_zone": "UTC",
            "units": "mm",
            "resolution_degrees": 0.25,
            "target_period": {"start": start.isoformat(), "end": end.isoformat()},
            "baseline": {"start_year": BASELINE_START_YEAR, "end_year": BASELINE_END_YEAR, "calendar_month": start.month, "years_required": BASELINE_YEARS, "complete_years": 0},
            "license_url": "https://open-meteo.com/en/terms",
            "limitations": copy.deepcopy(LIMITATIONS),
        },
    }
    def unavailable(note, state="unavailable"):
        return result | {"history": result["history"] | {"state": state, "note": note}}
    if not enabled():
        return unavailable("Live geographic sources are disabled; no historical source was contacted.", "disabled")
    key = (latitude, longitude, start.isoformat(), end.isoformat())
    with _lock:
        now = time.monotonic()
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            saved = copy.deepcopy(cached[1])
            saved["history"]["state"] = "cached"
            _cache.move_to_end(key)
            return saved
        while _requests and now - _requests[0] >= 86400:
            _requests.popleft()
        if key in _inflight:
            return unavailable("This historical context is already being fetched. Try again after it finishes.")
        hourly = sum(now-stamp < 3600 for stamp in _requests)
        if hourly >= MAX_REQUESTS_HOUR or len(_requests) >= MAX_REQUESTS_DAY:
            return unavailable("The historical source request limit has been reached. Try again later.")
        _requests.append(now)
        _inflight.add(key)
    try:
        parsed = summarize_history(_fetch(latitude, longitude, start, end), start, end)
        # A fixed 0.25-degree nearest-cell request must not accept a distant site.
        grid = parsed["grid_point"]
        lon_gap = min(abs(grid["longitude"]-longitude), 360-abs(grid["longitude"]-longitude))
        if abs(grid["latitude"]-latitude) > .5 or lon_gap > .5:
            raise ValueError("Historical grid point is not near the requested location.")
        result["history"].update(parsed)
        result["history"]["state"] = "ready"
        result["history"]["baseline"]["complete_years"] = BASELINE_YEARS
        with _lock:
            _cache[key] = (time.monotonic(), copy.deepcopy(result))
            _cache.move_to_end(key)
            while len(_cache) > MAX_CACHE:
                _cache.popitem(last=False)
        return result
    except Exception:
        return unavailable("Historical precipitation could not be validated. No anomaly was invented.")
    finally:
        with _lock:
            _inflight.discard(key)
