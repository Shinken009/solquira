"""Single source of truth for the Solquira tool registry (REA tool-design aspects 2 and 3).

Original project code. Implements the two REA tool-contract aspects that were
recognized by Engram (obs #4109) but not yet wired into the architecture:

  Aspect 2 — action+object forms. Every tool declares exactly one REA form
    (inspect|search|list|trace|compare|workflow|observe) plus its object, so a
    caller knows the shape of the answer before invoking it. One tool never
    mixes unrelated modes.

  Aspect 3 — capability reporting instead of schema truncation. Every tool
    reports what it can do, what it cannot do, how it degrades honestly and
    which gates guard it. A pending extension is declared with an empty
    `can[]` and an explicit `cannot[]` — it is never hidden and never fakes
    data. Names are provider-neutral: contracts do not embed a vendor name
    (env gates keep their real variable names because they are configuration,
    not contract vocabulary).

Backward compatible: every entry keeps the historical `preflight`, `evidence`
and `contract` keys consumed by the panel, the HTTP registry and the MCP
`tool_registry` tool.
"""

from __future__ import annotations

# REA action vocabulary (subset adapted to an agricultural assistant).
REA_FORMS = ("inspect", "search", "list", "trace", "compare", "workflow", "observe")

REGISTRY_VERSION = "solquira.tool-registry/2"


def _tool(
    form: str,
    object_: str,
    *,
    preflight: list[str],
    evidence: list[str],
    contract: str,
    can: list[str],
    cannot: list[str],
    degrades_to: str,
    gates: list[str],
    status: str = "ready",
) -> dict:
    if form not in REA_FORMS:
        raise ValueError(
            f"Forma REA desconocida: {form!r} (esperada una de {REA_FORMS})."
        )
    return {
        "form": form,
        "object": object_,
        "status": status,
        "preflight": preflight,
        "evidence": evidence,
        "contract": contract,
        "capability": {
            "can": can,
            "cannot": cannot,
            "degrades_to": degrades_to,
            "gates": gates,
        },
    }


# Engine + open-data tools served over HTTP (main.py / assistant_routes.py).
ENGINE_TOOLS: dict[str, dict] = {
    "evaluate_demo": _tool(
        "compare",
        "scenarios",
        preflight=["agua", "suelo", "historial"],
        evidence=["parcel-input", "demo-rules"],
        contract="POST /solquira-api/evaluacion",
        can=[
            "compara rotaciones deterministas (3 perfiles x 9 niveles de agua x 4 historiales)",
            "reporta balance hídrico y restricciones explícitas de la rúbrica",
            "devuelve insufficient_data sin secuencia cuando faltan insumos",
        ],
        cannot=[
            "no acredita viabilidad agronómica (agronomicViability=not_assessed)",
            "no usa datos de parcelas medidas: los perfiles son sintéticos de demostración",
        ],
        degrades_to="insufficient_data",
        gates=["sin gate de red: motor local determinista"],
    ),
    "nasa_power_daily": _tool(
        "inspect",
        "daily-series",
        preflight=["coordenadas", "fecha", "conectividad"],
        evidence=["nasa-power"],
        contract="GET /solquira-api/fuentes/power",
        can=[
            "consulta la serie diaria histórica NASA POWER (PRECTOTCORR, T2M) de una celda",
            "valida respuesta, faltantes y ventana de 1 a 31 días con caché de una hora",
        ],
        cannot=[
            "no es estación de parcela ni pronóstico",
            "no alimenta la rúbrica de comparación",
            "no se ejecuta sin autorización explícita de egreso de red",
        ],
        degrades_to="not_configured",
        gates=["SOLQUIRA_MCP_NASA_POWER=1 (egreso de red por llamada)"],
    ),
    "hls_extension": _tool(
        "inspect",
        "imagery-tiles",
        preflight=["integración pendiente"],
        evidence=["nasa-hls"],
        contract="punto de extensión sin cliente",
        can=[],
        cannot=[
            "capacidad no implementada en esta copia: la extensión se declara, no se simula"
        ],
        degrades_to="not_configured",
        gates=["sin cliente HLS conectado"],
        status="pending",
    ),
    "chirps_extension": _tool(
        "inspect",
        "precipitation-series",
        preflight=["integración pendiente"],
        evidence=["chirps"],
        contract="punto de extensión sin cliente",
        can=[],
        cannot=[
            "capacidad no implementada en esta copia: la extensión se declara, no se simula"
        ],
        degrades_to="not_configured",
        gates=["sin cliente CHIRPS conectado"],
        status="pending",
    ),
    "qfield_extension": _tool(
        "observe",
        "field-forms",
        preflight=["integración pendiente"],
        evidence=["qfield"],
        contract="punto de extensión sin cliente",
        can=[],
        cannot=[
            "capacidad no implementada en esta copia: la extensión se declara, no se simula"
        ],
        degrades_to="not_configured",
        gates=["sin cliente QField conectado"],
        status="pending",
    ),
    "ceazamet_extension": _tool(
        "inspect",
        "station-series",
        preflight=["integración pendiente; no scraping"],
        evidence=["ceazamet"],
        contract="punto de extensión sin cliente",
        can=[],
        cannot=[
            "capacidad no implementada en esta copia: la extensión se declara, no se simula"
        ],
        degrades_to="not_configured",
        gates=["sin cliente CEAZAMet conectado"],
        status="pending",
    ),
}

# Assistant-specific tools served over /api/v1/assistant/tools.
ASSISTANT_TOOLS: dict[str, dict] = {
    "field_observation_ingest": _tool(
        "observe",
        "field-observation",
        preflight=[
            "coordenadas",
            f"texto <= 1000 chars",
            "imagen/audio opcionales en data URI",
        ],
        evidence=["parcel-input", "foto-productor", "sensor-fft"],
        contract="POST /api/v1/telemetry/ingest",
        can=[
            "encola una observación de campo (texto + coordenadas, imagen/audio opcionales)",
            "devuelve un informe estructurado con lagunas_datos[] y fuentes[] trazables",
        ],
        cannot=[
            "no diagnostica sin contexto: cada dato faltante se declara como laguna, nunca se inventa",
            "no exige firma HMAC en desarrollo (SOLQUIRA_WEBHOOK_SECRET ausente ⇒ not_configured)",
        ],
        degrades_to="local_fallback",
        gates=["SOLQUIRA_WEBHOOK_SECRET (firma HMAC opcional)", "rate limit por IP"],
    ),
    "open_data_layers": _tool(
        "inspect",
        "geospatial-layers",
        preflight=["coordenadas", "SOLQUIRA_LIVE_ADAPTERS=1 para fuentes en vivo"],
        evidence=["open-meteo", "stac-catalogs"],
        contract="GET /api/v1/geospatial/layers/{id}",
        can=[
            "declara el roster de capas abiertas con licencia y fuente por capa",
            "reporta feed_state por capa y overall = peor estado del roster",
        ],
        cannot=[
            "no simula capas sin cliente: not_configured/unavailable es la respuesta honesta",
            "no narra un feed stale/fallback como vivo",
        ],
        degrades_to="unavailable",
        gates=["SOLQUIRA_LIVE_ADAPTERS=1 (adaptadores en vivo, apagado por defecto)"],
    ),
    "multimodal_diagnosis": _tool(
        "workflow",
        "diagnosis-cycle",
        preflight=["clave del proveedor LLM", "SOLQUIRA_LIVE_ADAPTERS=1"],
        evidence=["llm-provider-inference", "solquira-deterministic-v1"],
        contract="incluido en el ciclo ingest -> task (POST /api/v1/assistant/stream para SSE)",
        can=[
            "ejecuta el ciclo observación -> contexto -> informe estructurado",
            "emite el informe en streaming SSE (meta -> delta* -> final) o por polling",
        ],
        cannot=[
            "sin clave del proveedor responde local_fallback determinista, nunca texto inventado",
            "el proveedor del modelo es intercambiable: el contrato no depende de un vendor",
        ],
        degrades_to="local_fallback",
        gates=["NEBIUS_API_KEY (clave del proveedor LLM)", "SOLQUIRA_LIVE_ADAPTERS=1"],
    ),
    "tool_registry": _tool(
        "list",
        "tool-registry",
        preflight=["ninguna"],
        evidence=["assistant-tools"],
        contract="GET /api/v1/assistant/tools",
        can=[
            "lista todas las tools con su forma REA (action+object) y capability report",
            "incluye las extensiones pendientes con capability vacío en vez de ocultarlas",
        ],
        cannot=["no ejecuta ninguna tool por sí mismo"],
        degrades_to="nunca: el registro es estático y local",
        gates=["sin gate de red"],
    ),
}


def registry() -> dict[str, dict]:
    """Merged registry (engine + assistant), plain copies for serialization."""
    merged = {name: dict(entry) for name, entry in ENGINE_TOOLS.items()}
    merged.update({name: dict(entry) for name, entry in ASSISTANT_TOOLS.items()})
    return merged
