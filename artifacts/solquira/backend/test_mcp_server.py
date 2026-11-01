"""Offline tests for the MCP stdio server (JSON-RPC core, no sockets)."""

import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import mcp_server


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "solquira")
        self.assertIn("tools", result["capabilities"])

    async def test_initialized_notification_has_no_response(self):
        response = await mcp_server.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertIsNone(response)

    async def test_tools_list(self):
        response = await mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(
            names,
            {
                "evaluate_demo",
                "layers_summary",
                "field_observation_ingest",
                "tool_registry",
            },
        )
        for tool in response["result"]["tools"]:
            self.assertIn("inputSchema", tool)
            self.assertTrue(tool["description"])

    async def test_unknown_method(self):
        response = await mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}
        )
        self.assertEqual(response["error"]["code"], -32601)

    async def test_unknown_tool_is_protocol_error(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )
        self.assertEqual(response["error"]["code"], -32602)

    async def test_ping(self):
        response = await mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 5, "method": "ping"}
        )
        self.assertEqual(response["result"], {})


class McpToolCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluate_demo_insufficient_data(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "evaluate_demo",
                    "arguments": {
                        "latitude": -30.031,
                        "longitude": -70.708,
                        "waterPercent": 100,
                    },
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "insufficient_data")

    async def test_evaluate_demo_ok(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "evaluate_demo",
                    "arguments": {
                        "latitude": -30.031,
                        "longitude": -70.708,
                        "waterPercent": 100,
                        "waterM3Ha": 2400,
                        "soil": "franco",
                        "previousCrops": ["cereal"],
                        "priorities": ["agua"],
                    },
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["rotations"])

    async def test_layers_summary_honest(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "layers_summary",
                    "arguments": {"latitude": -30.031, "longitude": -70.708},
                },
            }
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn(
            payload["overall"],
            {
                "unavailable",
                "not_configured",
                "fallback",
                "stale",
                "nominal",
                "loading",
                "degraded",
            },
        )

    async def test_field_observation_ingest_report(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "field_observation_ingest",
                    "arguments": {
                        "latitude": -30.031,
                        "longitude": -70.708,
                        "text_query": "¿Riego esta semana?",
                        "language": "es",
                    },
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["report"]["inference_mode"], "local_fallback")
        self.assertIn("lagunas_datos", payload["report"])

    async def test_tool_registry(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {"name": "tool_registry", "arguments": {}},
            }
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("evaluate_demo", payload)
        self.assertIn("layers", payload)
        self.assertIn("disclaimer", payload)

    async def test_tool_error_is_result_not_protocol_error(self):
        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {
                    "name": "layers_summary",
                    "arguments": {"longitude": 0},
                },
            }
        )
        self.assertIn("result", response)
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["error"], "KeyError")


class NasaPowerGateTests(unittest.IsolatedAsyncioTestCase):
    """SOLQUIRA_MCP_NASA_POWER: apagado por defecto (egreso de red por llamada)."""

    def setUp(self):
        self._saved = os.environ.pop("SOLQUIRA_MCP_NASA_POWER", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SOLQUIRA_MCP_NASA_POWER", None)
        else:
            os.environ["SOLQUIRA_MCP_NASA_POWER"] = self._saved

    async def test_gate_off_hides_tool_and_rejects_call(self):
        listing = await mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 20, "method": "tools/list"}
        )
        names = {tool["name"] for tool in listing["result"]["tools"]}
        self.assertNotIn("nasa_power_daily", names)
        self.assertEqual(
            names,
            {
                "evaluate_demo",
                "layers_summary",
                "field_observation_ingest",
                "tool_registry",
            },
        )

        response = await mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "nasa_power_daily",
                    "arguments": {
                        "latitude": -30.0,
                        "longitude": -70.7,
                        "start": "2025-01-01",
                        "end": "2025-01-07",
                    },
                },
            }
        )
        self.assertIn("result", response)
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["error"], "PermissionError")
        self.assertIn("SOLQUIRA_MCP_NASA_POWER", payload["detail"])

    async def test_gate_on_lists_tool(self):
        os.environ["SOLQUIRA_MCP_NASA_POWER"] = "1"
        listing = await mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 22, "method": "tools/list"}
        )
        names = {tool["name"] for tool in listing["result"]["tools"]}
        self.assertIn("nasa_power_daily", names)

    async def test_gate_on_dispatches_to_power_without_network(self):
        os.environ["SOLQUIRA_MCP_NASA_POWER"] = "1"
        fake = {"status": "ok", "cacheHit": False, "provider": "NASA POWER"}
        with patch("power.fetch_nasa_power", return_value=fake) as mocked:
            response = await mcp_server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 23,
                    "method": "tools/call",
                    "params": {
                        "name": "nasa_power_daily",
                        "arguments": {
                            "latitude": -30.031,
                            "longitude": -70.708,
                            "start": "2025-01-01",
                            "end": "2025-01-07",
                        },
                    },
                }
            )
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "ok")
        mocked.assert_called_once()
        args = mocked.call_args.args
        self.assertEqual((args[0], args[1]), (-30.031, -70.708))
        self.assertEqual(args[2].isoformat(), "2025-01-01")
        self.assertEqual(args[3].isoformat(), "2025-01-07")

    async def test_gate_on_validation_error_is_result(self):
        os.environ["SOLQUIRA_MCP_NASA_POWER"] = "1"
        with patch(
            "power.fetch_nasa_power",
            side_effect=HTTPException(422, "Use coordenadas geográficas válidas"),
        ):
            response = await mcp_server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 24,
                    "method": "tools/call",
                    "params": {
                        "name": "nasa_power_daily",
                        "arguments": {
                            "latitude": 0.0,
                            "longitude": 0.0,
                            "start": "2025-01-01",
                            "end": "2025-01-07",
                        },
                    },
                }
            )
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["error"], "HTTPException")


if __name__ == "__main__":
    unittest.main()
