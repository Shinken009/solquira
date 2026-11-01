"""Bounded NASA POWER daily connector; real gridded observations, not parcel measurements."""

from __future__ import annotations
import copy
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from fastapi import HTTPException

CACHE_TTL = 3600
CACHE_LIMIT = 32
TIMEOUT_SECONDS = 25
_CACHE = OrderedDict()
_LOCK = threading.Lock()
ENDPOINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
LIMITATIONS = (
    "Serie histórica NASA POWER de celdas de referencia, no medición de parcela ni pronóstico. "
    "No convierte precipitación en agua de riego, no modifica la rúbrica sintética ni acredita viabilidad agronómica."
)


def fetch_nasa_power(latitude: float, longitude: float, start: date, end: date) -> dict:
    days = (end - start).days + 1
    if (
        not 1 <= days <= 31
        or end > date.today()
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -bound <= value <= bound
            for value, bound in ((latitude, 90), (longitude, 180))
        )
    ):
        raise HTTPException(
            422, "Use coordenadas geográficas válidas y un intervalo histórico de 1 a 31 días."
        )
    key = (latitude, longitude, start.isoformat(), end.isoformat())
    query = urllib.parse.urlencode(
        {
            "parameters": "PRECTOTCORR,T2M",
            "community": "AG",
            "longitude": longitude,
            "latitude": latitude,
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "format": "JSON",
            "time-standard": "UTC",
        }
    )
    url = ENDPOINT + "?" + query
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            _CACHE.move_to_end(key)
            return {**copy.deepcopy(cached[1]), "cacheHit": True}
        base = {
            "provider": "NASA POWER",
            "source": url,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "location": {"latitude": latitude, "longitude": longitude},
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "limitations": LIMITATIONS,
            "dataOrigin": "nasa_power",
            "cacheHit": False,
            "timeStandard": "UTC",
        }
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Solquira/1.1"})
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise ValueError("Unexpected HTTP status")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("Oversized response")
                data = json.loads(raw)
            header = data["header"]
            meta = data["parameters"]
            values = data["properties"]["parameter"]
            coordinates = data["geometry"]["coordinates"]
            if (
                data["geometry"]["type"] != "Point"
                or abs(coordinates[0] - longitude) > 0.001
                or abs(coordinates[1] - latitude) > 0.001
            ):
                raise ValueError("Location mismatch")
            if (
                header["start"] != start.strftime("%Y%m%d")
                or header["end"] != end.strftime("%Y%m%d")
                or header["time_standard"] != "UTC"
            ):
                raise ValueError("Period or time-standard mismatch")
            expected = {
                (start + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)
            }
            normalized = {}
            units = {}
            descriptions = {}
            fill = header["fill_value"]
            for variable, expected_unit in [("PRECTOTCORR", "mm/day"), ("T2M", "C")]:
                if (
                    meta[variable]["units"] != expected_unit
                    or set(values[variable]) != expected
                ):
                    raise ValueError("Unit or daily coverage mismatch")
                units[variable] = meta[variable]["units"]
                descriptions[variable] = meta[variable]["longname"]
                normalized[variable] = {}
                for day, value in values[variable].items():
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (float, int))
                        or not math.isfinite(value)
                    ):
                        raise ValueError("Non-numeric or non-finite observation")
                    if variable == "PRECTOTCORR" and value < 0 and value != fill:
                        raise ValueError("Negative precipitation")
                    normalized[variable][day] = None if value == fill else value
            result = {
                **base,
                "status": "ok",
                "units": units,
                "values": normalized,
                "parameterDescriptions": descriptions,
                "sourceProducts": header["sources"],
                "apiVersion": header["api"]["version"],
                "missingValueCount": sum(
                    v is None for series in normalized.values() for v in series.values()
                ),
                "cacheExpiresAt": (
                    datetime.now(timezone.utc) + timedelta(seconds=CACHE_TTL)
                ).isoformat(),
            }
            _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
            _CACHE.move_to_end(key)
            while len(_CACHE) > CACHE_LIMIT:
                _CACHE.popitem(last=False)
            return result
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            return {
                **base,
                "status": "unavailable",
                "dataOrigin": "unavailable",
                "limitations": LIMITATIONS
                + " Consulta no disponible; no se generan sustitutos sintéticos.",
                "errorType": type(error).__name__,
            }
        except (KeyError, TypeError, ValueError, IndexError) as error:
            return {
                **base,
                "status": "invalid_response",
                "dataOrigin": "unavailable",
                "limitations": LIMITATIONS
                + " Respuesta rechazada por validación; no se generan sustitutos.",
                "errorType": type(error).__name__,
            }
