"""Solquira FastAPI demo service. Synthetic planning rules, never agronomic advice."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette._utils import get_route_path
from studio_api import studio_mode, studio_router, field_router

# The legacy assistant stack pulls numpy and other adapters that the Studio image
# does not ship. Import it only outside Studio mode; the routes stay 404 there.
if not studio_mode():
    import assistant_telegram
    from assistant_routes import assistant_router
    from power import fetch_nasa_power

# Capture provenance of the code loaded now, never relabel a cached module later.
_solquira_source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

VERSION = "demo-rules-1.1"
DISCLAIMER = (
    "Demostración educativa: puntuaciones, demandas y señales sintéticas, no "
    "mediciones locales ni recomendaciones agronómicas validadas. No predice "
    "rendimientos, no diagnostica desertificación ni promete revertirla. "
    "Confirme decisiones con asesoría local."
)
DATA_DIR = Path(os.environ.get("SOLQUIRA_DATA_DIR", Path(__file__).parent / ".data"))
DB_PATH = Path(os.environ.get("SOLQUIRA_SQLITE_PATH", DATA_DIR / "solquira.sqlite3"))
API_PREFIX = os.environ.get("SOLQUIRA_API_PREFIX", "/solquira-api").rstrip("/")


class Parcel(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    profileId: Literal["valle", "secano", "precordillera"]
    areaHa: float = Field(gt=0, le=1000)
    waterM3Ha: float | None = Field(default=None, ge=0, le=20000)
    soil: Literal["franco", "arenoso", "arcilloso", "desconocido"]
    previousCrops: list[str] = Field(max_length=10)
    priorities: list[Literal["agua", "suelo", "diversidad"]] = Field(
        default_factory=list
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class EvaluationInput(BaseModel):
    parcel: Parcel
    waterPercent: float = Field(ge=0, le=150)


PROFILES = [
    {
        "id": "valle",
        "name": "Valle con riego",
        "location": "Valle de referencia sin ubicación asignada",
        "description": "Perfil territorial demostrativo de valle con riego.",
        "climate": "Árido; condición sintética, sin serie local verificada.",
        "soil": "No caracterizado; completar en la ficha de parcela.",
        "waterM3Ha": 2400,
        "latitude": None,
        "longitude": None,
        "dataStatus": "Perfil de comparación sin ubicación ni medición local asignada.",
    },
    {
        "id": "secano",
        "name": "Secano semiárido",
        "location": "Secano de referencia sin ubicación asignada",
        "description": "Perfil territorial demostrativo de secano semiárido.",
        "climate": "Semiárido; condición sintética, sin serie local verificada.",
        "soil": "No caracterizado; completar en la ficha de parcela.",
        "waterM3Ha": 1200,
        "latitude": None,
        "latitude": None,
        "dataStatus": "Perfil de comparación sin ubicación ni medición local asignada.",
    },
    {
        "id": "precordillera",
        "name": "Precordillera",
        "location": "Relieve de referencia sin ubicación asignada",
        "description": "Perfil territorial demostrativo de precordillera.",
        "climate": "Variabilidad altitudinal; condición sintética, sin serie local verificada.",
        "soil": "No caracterizado; completar en la ficha de parcela.",
        "waterM3Ha": 1600,
        "latitude": None,
        "longitude": None,
        "dataStatus": "Perfil de comparación sin ubicación ni medición local asignada.",
    },
]

SCENARIOS = [
    {
        "id": "normal",
        "name": "Base",
        "multiplier": 100,
        "description": "Agua de la ficha, sin ajuste.",
    },
    {
        "id": "moderado",
        "name": "Menos agua",
        "multiplier": 70,
        "description": "Ensaya 70 % del agua ingresada.",
    },
    {
        "id": "severo",
        "name": "Sequía severa",
        "multiplier": 40,
        "description": "Ensaya 40 % del agua ingresada.",
    },
    {
        "id": "recuperacion",
        "name": "Más agua",
        "multiplier": 120,
        "description": "Ensaya 120 % del agua ingresada.",
    },
]

SOURCES = [
    {
        "id": "parcel-input",
        "title": "Ficha de parcela",
        "status": "local_user_input",
        "detail": "Los valores precargados son una parcela sintética editable; los cambios guardados son datos locales ingresados, no mediciones verificadas.",
        "url": None,
    },
    {
        "id": "demo-rules",
        "title": "Reglas comparativas de Solquira",
        "status": "synthetic_demo",
        "detail": "Rubrica fija de demostración, versión demo-rules-1.1; demandas supuestas, sin validación agronómica.",
        "url": None,
    },
    {
        "id": "nasa-power",
        "title": "NASA POWER",
        "status": "connector_available_not_fetched",
        "detail": "Adaptador HTTP disponible. Solo se presenta como dato real al recibir una respuesta válida del endpoint.",
        "url": "https://power.larc.nasa.gov/docs/services/api/",
    },
    {
        "id": "nasa-hls",
        "title": "NASA HLS",
        "status": "extension_not_connected",
        "detail": "Punto de extensión; sin cliente ni observaciones consultadas.",
        "url": "https://hls.gsfc.nasa.gov/",
    },
    {
        "id": "chirps",
        "title": "CHIRPS",
        "status": "extension_not_connected",
        "detail": "Punto de extensión; sin cliente ni observaciones consultadas.",
        "url": "https://www.chc.ucsb.edu/data/chirps",
    },
    {
        "id": "qfield",
        "title": "QField",
        "status": "extension_not_connected",
        "detail": "Punto de extensión; sin intercambio QField configurado.",
        "url": "https://qfield.org/",
    },
    {
        "id": "ceazamet",
        "title": "CEAZAMET",
        "status": "extension_not_connected",
        "detail": "Punto de extensión; no se consulta ni extrae información. No scraping.",
        "url": "https://www.ceazamet.cl/",
    },
    {
        "id": "anvil-pattern",
        "title": "Patrón de orquestación",
        "status": "conceptual_only",
        "detail": "Registro de herramientas, preflight y evidencia implementados aquí como patrón; no se recibió ni reutilizó código Anvil.",
        "url": None,
    },
]

ROTATION_RULES = [
    {
        "id": "rotation-a",
        "name": "Cobertura → leguminosa → cereal",
        "crops": ["Cubierta (supuesto)", "Leguminosa (supuesto)", "Cereal (supuesto)"],
        "demandM3Ha": 2600,
        "coveragePercent": 78,
        "diversity": 80,
        "sequence": ["cubierta", "leguminosa", "cereal"],
    },
    {
        "id": "rotation-b",
        "name": "Leguminosa → cereal → descanso",
        "crops": ["Leguminosa (supuesto)", "Cereal (supuesto)", "Descanso (supuesto)"],
        "demandM3Ha": 1950,
        "coveragePercent": 56,
        "diversity": 65,
        "sequence": ["leguminosa", "cereal", "descanso"],
    },
    {
        "id": "rotation-c",
        "name": "Cereal → cubierta → descanso",
        "crops": ["Cereal (supuesto)", "Cubierta (supuesto)", "Descanso (supuesto)"],
        "demandM3Ha": 1300,
        "coveragePercent": 43,
        "diversity": 55,
        "sequence": ["cereal", "cubierta", "descanso"],
    },
]


@contextmanager
def _connect():
    """Open a transaction and always close the SQLite connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS parcel (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
        )
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def default_parcel() -> Parcel:
    profile = PROFILES[0]
    return Parcel(
        name="Parcela demostrativa",
        profileId="valle",
        areaHa=1,
        waterM3Ha=profile["waterM3Ha"],
        soil="franco",
        previousCrops=["cereal"],
        priorities=["agua", "suelo"],
        latitude=profile["latitude"],
        longitude=profile["longitude"],
    )


def get_parcel() -> Parcel:
    with _connect() as connection:
        row = connection.execute("SELECT payload FROM parcel WHERE id=1").fetchone()
    if row is None:
        return default_parcel()
    return Parcel.model_validate_json(row[0])


def save_parcel(parcel: Parcel) -> Parcel:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO parcel(id, payload) VALUES(1, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (parcel.model_dump_json(),),
        )
    return parcel


def evaluate(request: EvaluationInput) -> dict:
    parcel = request.parcel
    missing: list[str] = []
    if parcel.waterM3Ha is None:
        missing.append("Agua disponible")
    if parcel.soil == "desconocido":
        missing.append("Tipo de suelo")
    if not parcel.previousCrops:
        missing.append("Historial de cultivos (aún no informado)")
    if not parcel.priorities:
        missing.append("Prioridades agrícolas")
    if any(
        crop.strip().lower() not in {"cereal", "leguminosa", "cubierta", "descanso"}
        for crop in parcel.previousCrops
    ):
        missing.append(
            "Historial: use categorías cereal, leguminosa, cubierta o descanso; sin equivalencias agronómicas asumidas"
        )
    if missing:
        return {
            "status": "insufficient_data",
            "dataStatus": "Datos locales incompletos; no se compara ni se aconseja sembrar.",
            "availableWaterM3Ha": None
            if parcel.waterM3Ha is None
            else round(parcel.waterM3Ha * request.waterPercent / 100, 1),
            "baselineLeaderId": None,
            "scenarioLeaderId": None,
            "summary": "Información insuficiente para una comparación demostrativa útil.",
            "missingFields": missing,
            "rotations": [],
            "signals": [
                {
                    "id": "insufficient",
                    "label": "Señales territoriales",
                    "level": "sin_datos",
                    "detail": "No hay observaciones verificadas de cobertura vegetal o presión hídrica para esta parcela.",
                    "evidenceIds": ["parcel-input", "nasa-hls", "chirps"],
                }
            ],
            "evidence": SOURCES,
            "disclaimer": DISCLAIMER,
            "ruleVersion": VERSION,
        }

    available = parcel.waterM3Ha * request.waterPercent / 100
    previous = {crop.strip().lower() for crop in parcel.previousCrops}
    priorities = set(parcel.priorities)
    rotations: list[dict] = []
    for rule in ROTATION_RULES:
        water_fit = (
            min(100.0, available / rule["demandM3Ha"] * 100)
            if rule["demandM3Ha"]
            else 0
        )
        history_repeats = sum(crop in previous for crop in rule["sequence"])
        history_fit = max(0, 100 - history_repeats * 20)
        diversity_fit = float(rule["diversity"])
        weights = {"agua": 0.6, "suelo": 0.2, "diversidad": 0.2}
        # The "suelo" preference means preserve uncertainty: without measured soil context,
        # no candidate receives an invented soil suitability bonus.
        if "agua" not in priorities:
            weights["agua"] = 0.35
        if "diversidad" in priorities:
            weights["diversidad"] = 0.4
            weights["agua"] = min(weights["agua"], 0.4)
        if "suelo" in priorities:
            weights["suelo"] = 0.35
            weights["agua"] = min(weights["agua"], 0.45)
        total_weight = sum(weights.values())
        score = round(
            (
                water_fit * weights["agua"]
                + history_fit * weights["suelo"]
                + diversity_fit * weights["diversidad"]
            )
            / total_weight,
            1,
        )
        balance = round(available - rule["demandM3Ha"], 1)
        if balance < 0:
            compatibility = "Supera el supuesto hídrico de la demo"
            water_reason = (
                f"El agua del escenario queda {abs(balance):.0f} m³/ha bajo el supuesto sintético "
                f"de {rule['demandM3Ha']} m³/ha; no es una demanda real del cultivo."
            )
        else:
            compatibility = "Dentro del supuesto hídrico de la demo"
            water_reason = (
                f"El agua del escenario supera en {balance:.0f} m³/ha el supuesto sintético "
                f"de {rule['demandM3Ha']} m³/ha; no acredita viabilidad agronómica."
            )
        repeat_names = [crop for crop in rule["sequence"] if crop in previous]
        history_reason = (
            f"El historial local coincide con {', '.join(repeat_names)}; la rúbrica resta 20 puntos por coincidencia."
            if repeat_names
            else "No hay coincidencia literal entre el historial registrado y las etiquetas de esta secuencia."
        )
        rotation_allowed = (
            rule["sequence"][0] != parcel.previousCrops[-1].strip().lower()
        )
        if not rotation_allowed:
            history_reason += " Restricción didáctica: no repetir consecutivamente la última categoría declarada; no es una regla agronómica local validada."
        rotations.append(
            {
                "id": rule["id"],
                "name": rule["name"],
                "crops": rule["crops"],
                "score": score,
                "eligibleUnderDemoRules": balance >= 0 and rotation_allowed,
                "rotationAllowed": rotation_allowed,
                "agronomicViability": "not_assessed",
                "synthetic": True,
                "scoreWeights": {
                    key: round(value / total_weight, 6)
                    for key, value in weights.items()
                },
                "demandM3Ha": rule["demandM3Ha"],
                "waterBalanceM3Ha": balance,
                "coveragePercent": rule["coveragePercent"],
                "diversity": rule["diversity"],
                "compatibility": compatibility,
                "explanations": [
                    water_reason,
                    history_reason,
                    f"Suelo indicado: {parcel.soil}. No se aplica compatibilidad de suelo porque no hay reglas locales validadas.",
                    f"Prioridades ingresadas: {', '.join(parcel.priorities)}; pesos visibles y fijos de la rúbrica {VERSION}.",
                    "Demanda, cobertura y diversidad son valores sintéticos de demostración, no mediciones ni referencias de cultivo.",
                ],
                "evidenceIds": ["parcel-input", "demo-rules"],
            }
        )
    rotations.sort(
        key=lambda candidate: (
            not candidate["eligibleUnderDemoRules"],
            -candidate["score"],
            candidate["id"],
        )
    )
    baseline = (
        evaluate(EvaluationInput(parcel=parcel, waterPercent=100))["rotations"]
        if request.waterPercent != 100
        else rotations
    )
    within_assumption = sum(item["waterBalanceM3Ha"] >= 0 for item in rotations)
    summary = (
        f"Ninguna secuencia queda dentro del supuesto hídrico de esta simulación "
        f"({available:.0f} m³/ha). La clasificación no significa que alguna sea viable. "
        "No se puede recomendar sembrar con estos datos; revise restricciones y valide localmente."
        if within_assumption == 0
        else f"{within_assumption} de 3 secuencias cumplen el supuesto hídrico de esta demostración "
        f"({available:.0f} m³/ha). Esto no acredita viabilidad agrícola."
    )
    return {
        "status": "ok",
        "waterAssumptionMatches": within_assumption,
        "waterScenarioWarning": within_assumption == 0,
        "dataStatus": "Comparación sintética; la ficha inicial es un ejemplo editable y no una medición de parcela.",
        "availableWaterM3Ha": round(available, 1),
        "baselineLeaderId": next(
            (row["id"] for row in baseline if row["eligibleUnderDemoRules"]), None
        ),
        "scenarioLeaderId": next(
            (row["id"] for row in rotations if row["eligibleUnderDemoRules"]), None
        ),
        "demoRuleMatches": sum(row["eligibleUnderDemoRules"] for row in rotations),
        "summary": summary,
        "missingFields": [],
        "rotations": rotations,
        "signals": [
            {
                "id": "water-pressure",
                "label": "Presión hídrica del escenario",
                "level": "alta"
                if request.waterPercent <= 50
                else "moderada"
                if request.waterPercent < 100
                else "sin_señal_validada",
                "detail": (
                    "Nivel ilustrativo derivado únicamente del porcentaje elegido; no es una medición de cuenca ni de parcela."
                ),
                "evidenceIds": ["parcel-input", "demo-rules"],
            },
            {
                "id": "vegetation-cover",
                "label": "Cobertura vegetal",
                "level": "sin_datos",
                "detail": "No hay observación HLS ni medición de cobertura conectada para esta parcela.",
                "evidenceIds": ["nasa-hls"],
            },
            {
                "id": "soil-degradation",
                "label": "Vulnerabilidad a degradación del suelo",
                "level": "sin_datos",
                "detail": "No se diagnostica erosión ni desertificación; faltan observaciones locales verificadas.",
                "evidenceIds": ["parcel-input", "nasa-hls"],
            },
        ],
        "evidence": SOURCES,
        "disclaimer": DISCLAIMER,
        "ruleVersion": VERSION,
    }


def _register_tools() -> dict:
    # Registry moved to assistant_tools.py: single source of truth with REA
    # action+object forms (aspect 2) and capability reporting (aspect 3),
    # on top of the historical preflight + evidence metadata pattern.
    from assistant_tools import registry

    return registry()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Studio never starts external adapters, regardless of inherited credentials.
    bot_task = None if studio_mode() else await assistant_telegram.start_bot()
    try:
        yield
    finally:
        if bot_task is not None:
            await assistant_telegram.stop_bot(bot_task)
        if not studio_mode():
            from assistant_layers import aclose_client

            await aclose_client()


API_KEY_HEADER = "X-Solquira-Api-Key"


def _auth_configured() -> bool:
    """Per-call env read (R7): the gate can be flipped without a restart."""
    return bool(os.environ.get("SOLQUIRA_API_KEY", ""))


async def optional_api_key(request: Request, call_next):
    """Exposure gate: with SOLQUIRA_API_KEY set, every route except healthz demands it.

    Unset => open demo mode (documented in ESTADO.md and INTEGRACION.md; the
    prototype is never exposed without the user's explicit decision).
    """
    # Match the route-relative ASGI path used by Starlette, including root_path.
    path = get_route_path(request.scope)
    if studio_mode():
        studio_path = (
            path == "/solquira-api/studio"
            or path.startswith("/solquira-api/studio/")
            or path in {"/v1/field-payload", "/v1/field-brief"}
        )
        health_path = path == f"{API_PREFIX}/healthz"
        legacy_api = (
            path == "/api"
            or path.startswith("/api/")
            or path == API_PREFIX
            or path.startswith(API_PREFIX + "/")
        )
        private_surface = (
            path in {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
            or path == "/qa-preview"
            or path.startswith("/qa-preview/")
        )
        if (
            (legacy_api and not studio_path and not health_path)
            or private_surface
            or (not studio_path and request.method not in {"GET", "HEAD"})
        ):
            return JSONResponse(
                status_code=404,
                content={"detail": "This endpoint is not available in Studio mode."},
                headers={"Cache-Control": "no-store"},
            )
        # Studio uses its own opaque session + CSRF admission; never expose a
        # shared API key to the browser or block public HTML/assets with it.
        return await call_next(request)
    if _auth_configured():
        if path != f"{API_PREFIX}/healthz":
            provided = request.headers.get(API_KEY_HEADER, "")
            if not hmac.compare_digest(
                provided, os.environ.get("SOLQUIRA_API_KEY", "")
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": f"Falta o no coincide el encabezado {API_KEY_HEADER}."
                    },
                )
    return await call_next(request)


app = FastAPI(
    title="Solquira API", version="1.1.0", description=DISCLAIMER, lifespan=lifespan
)
app.middleware("http")(optional_api_key)
origins = [
    value.strip()
    for value in os.environ.get("CORS_ORIGINS", "").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "PUT", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-Solquira-Signature",
        API_KEY_HEADER,
        "X-Solquira-CSRF",
    ],
)
router = APIRouter(prefix=API_PREFIX)


@router.get("/healthz")
def health():
    return {"status": "ok", "service": "solquira-fastapi"}


@router.get("/perfiles")
def profiles():
    return PROFILES


@router.get("/parcela")
def parcel():
    return get_parcel().model_dump()


@router.put("/parcela")
def parcel_update(value: Parcel):
    return save_parcel(value).model_dump()


@router.get("/escenarios")
def scenarios():
    return SCENARIOS


@router.get("/fuentes")
def sources():
    return SOURCES


@router.get("/herramientas")
def tools():
    return _register_tools()


@router.post("/evaluacion")
def evaluation(value: EvaluationInput):
    return evaluate(value)


@router.get("/fuentes/power")
def nasa_power(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    start: date = Query(),
    end: date = Query(),
):
    return fetch_nasa_power(latitude, longitude, start, end)


app.include_router(router)
# Agri-assistant gateway contract: /api/v1/telemetry|geospatial|assistant.
if not studio_mode():
    app.include_router(assistant_router)
app.include_router(studio_router)
app.include_router(field_router)

# Explicit local-only visual QA harness; disabled in production unless opted in.
if os.environ.get("SOLQUIRA_ENABLE_QA_PREVIEW") == "1" and not studio_mode():
    from fastapi.staticfiles import StaticFiles

    QA_DIR = Path(__file__).resolve().parent.parent / "qa-preview"
    app.mount(
        "/qa-preview",
        StaticFiles(directory=QA_DIR, html=True),
        name="visual-qa-preview",
    )

# Optional static delivery for single-process deployment. When built, the same
# FastAPI process serves the UI and API; in dev, Vite proxies API requests.
DIST_DIR = Path(__file__).resolve().parent.parent / "dist" / "public"
if DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="solquira-web")
