"""MCP stdio server exposing Solquira tools to external agents.

Implements the Model Context Protocol server surface (JSON-RPC 2.0 over
newline-delimited stdio) with the standard library only. Tools mirror the
HTTP/assistant contracts — every call runs the same deterministic engine or
pipeline, so an agent cannot obtain claims the API would not serve.

Tools:
  evaluate_demo            — deterministic rotation comparison (POST /solquira-api/evaluacion)
  layers_summary           — honest feed-state roster for a point (GET /api/v1/geospatial/layers/summary)
  field_observation_ingest — queue a field observation and wait for the report
  tool_registry            — preflight + evidence registry (GET /api/v1/assistant/tools subset)
  nasa_power_daily         — NASA POWER daily series (GET /solquira-api/fuentes/power); OFF unless
                             SOLQUIRA_MCP_NASA_POWER=1 (network egress per call, budget gate)

Run: python -m mcp_server   (stdio; disabled unless SOLQUIRA_MCP=1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "solquira", "version": "1.1.0"}
logger = logging.getLogger("solquira.mcp")

TOOLS: list[dict] = [
    {
        "name": "evaluate_demo",
        "description": (
            "Comparación determinista de rotaciones para una parcela sintética de demostración. "
            "Devuelve scores, balance de agua y evidencia; insufficient_data si faltan insumos. "
            "No es recomendación agronómica certificada."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["latitude", "longitude", "waterPercent"],
            "properties": {
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                "waterPercent": {"type": "number", "minimum": 0, "maximum": 150},
                "waterM3Ha": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 20000,
                },
                "soil": {
                    "type": "string",
                    "enum": ["franco", "arenoso", "arcilloso", "desconocido"],
                },
                "previousCrops": {"type": "array", "items": {"type": "string"}},
                "priorities": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["agua", "suelo", "diversidad"],
                    },
                },
            },
        },
    },
    {
        "name": "layers_summary",
        "description": (
            "Estado honesto de las capas abiertas en un punto (feed_state por capa y overall = peor estado). "
            "unavailable/not_configured significan sin dato vivo; nunca se sustituyen por datos inventados."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["latitude", "longitude"],
            "properties": {
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            },
        },
    },
    {
        "name": "field_observation_ingest",
        "description": (
            "Encola una observación de campo (texto + coordenadas, imagen/audio opcionales en data URI) "
            "y devuelve el informe estructurado cuando la tarea termina. Cada dato faltante se declara como laguna."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["latitude", "longitude", "text_query"],
            "properties": {
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                "text_query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "language": {"type": "string", "enum": ["es", "en"]},
                "parcel_name": {"type": "string", "maxLength": 120},
                "crop_type": {"type": "string", "maxLength": 60},
            },
        },
    },
    {
        "name": "tool_registry",
        "description": (
            "Registro de tools con forma REA (action+object), preflight, evidence y capability report "
            "(qué puede, qué no, cómo degrada y qué gates aplica); incluye extensiones pendientes."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nasa_power_daily",
        "description": (
            "Serie diaria NASA POWER (celdas de referencia, no medición de parcela ni pronóstico). "
            "Coordenadas acotadas al piloto de Coquimbo y ventana histórica de 1 a 31 días. "
            "Deshabilitado por defecto: requiere SOLQUIRA_MCP_NASA_POWER=1 (egreso de red por llamada)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["latitude", "longitude", "start", "end"],
            "properties": {
                "latitude": {"type": "number", "minimum": -32.3, "maximum": -29},
                "longitude": {"type": "number", "minimum": -72, "maximum": -69},
                "start": {
                    "type": "string",
                    "format": "date",
                    "description": "YYYY-MM-DD, histórico",
                },
                "end": {
                    "type": "string",
                    "format": "date",
                    "description": "YYYY-MM-DD, hasta hoy",
                },
            },
        },
    },
]

NASA_POWER_TOOL = "nasa_power_daily"


def _nasa_power_enabled() -> bool:
    """Budget gate (env por llamada, igual que el rate limit): apagado por defecto."""
    return os.environ.get("SOLQUIRA_MCP_NASA_POWER", "0") == "1"


def _visible_tools() -> list[dict]:
    return [
        tool
        for tool in TOOLS
        if tool["name"] != NASA_POWER_TOOL or _nasa_power_enabled()
    ]


async def call_tool(name: str, arguments: dict) -> str:
    """Dispatch a tool call; returns the JSON text for the MCP content block."""
    if name == "evaluate_demo":
        from main import EvaluationInput, Parcel, evaluate

        parcel = Parcel(
            name="Parcela MCP",
            profileId="valle",
            areaHa=1,
            waterM3Ha=arguments.get("waterM3Ha"),
            soil=arguments.get("soil", "desconocido"),
            previousCrops=arguments.get("previousCrops", []),
            priorities=arguments.get("priorities", ["agua"]),
            latitude=arguments["latitude"],
            longitude=arguments["longitude"],
        )
        result = evaluate(
            EvaluationInput(parcel=parcel, waterPercent=arguments["waterPercent"])
        )
        return json.dumps(result, ensure_ascii=False)

    if name == "layers_summary":
        from assistant_layers import layer_summary

        summary = await layer_summary(arguments["latitude"], arguments["longitude"])
        return json.dumps(summary, ensure_ascii=False)

    if name == "field_observation_ingest":
        from assistant_pipeline import STORE, run_task
        from assistant_schema import IngestPayload

        payload = IngestPayload(
            telegram_id=-2,
            latitude=arguments["latitude"],
            longitude=arguments["longitude"],
            text_query=arguments["text_query"],
            language=arguments.get("language", "es"),
            parcel_name=arguments.get("parcel_name"),
            crop_type=arguments.get("crop_type"),
        )
        record = STORE.create(-2)
        await run_task(record.task_id, payload, suppress_dispatch=True)
        finished = STORE.get(record.task_id)
        if finished is None:
            return json.dumps(
                {"status": "error", "error": "task evicted"}, ensure_ascii=False
            )
        return json.dumps(json.loads(finished.model_dump_json()), ensure_ascii=False)

    if name == "tool_registry":
        from assistant_layers import list_layers
        from assistant_tools import REA_FORMS, REGISTRY_VERSION, registry

        body = registry()
        body["_registry"] = {"version": REGISTRY_VERSION, "rea_forms": list(REA_FORMS)}
        body["layers"] = [layer["id"] for layer in list_layers()]
        body["disclaimer"] = (
            "Registro con forma REA, preflight, evidence y capability report; las extensiones "
            "listadas como pendientes declaran capability vacío y no aportan datos."
        )
        return json.dumps(body, ensure_ascii=False)

    if name == NASA_POWER_TOOL:
        if not _nasa_power_enabled():
            raise PermissionError(
                "nasa_power_daily deshabilitado (SOLQUIRA_MCP_NASA_POWER=1 para habilitar; "
                "egreso de red por llamada)."
            )
        import asyncio as _asyncio
        from datetime import date as _date
        from power import fetch_nasa_power

        result = await _asyncio.to_thread(
            fetch_nasa_power,
            arguments["latitude"],
            arguments["longitude"],
            _date.fromisoformat(arguments["start"]),
            _date.fromisoformat(arguments["end"]),
        )
        return json.dumps(result, ensure_ascii=False)

    raise ValueError(f"Herramienta desconocida: {name}")


async def handle_request(request: dict) -> dict | None:
    """Pure JSON-RPC handler (no socket I/O) — the testable core."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": _visible_tools()},
        }
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        tool = next((item for item in TOOLS if item["name"] == name), None)
        if tool is None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"},
            }
        try:
            text = await call_tool(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            }
        except Exception as exc:  # noqa: BLE001 - tool errors are results, not protocol errors
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": type(exc).__name__, "detail": str(exc)},
                                ensure_ascii=False,
                            ),
                        }
                    ],
                    "isError": True,
                },
            }
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    if os.environ.get("SOLQUIRA_MCP", "0") != "1":
        print(
            "SOLQUIRA_MCP=1 para habilitar el servidor MCP (stdio). Tools: evaluate_demo, layers_summary, "
            "field_observation_ingest, tool_registry. nasa_power_daily requiere además "
            "SOLQUIRA_MCP_NASA_POWER=1.",
            file=sys.stderr,
        )
        return
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger.info("solquira mcp server on stdio")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = loop.run_until_complete(handle_request(request))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
