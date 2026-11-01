"""Studio service contract tests: synthetic data and forbidden real network."""

import copy
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
from studio_service import AgentError, evaluate_studio, explain_studio, studio_status

SCENARIO = {"profile_id": "valle", "water_percent": 100, "previous_crop": "cereal"}


class Provider:
    mode = "provider_stub"
    model = "nvidia/nemotron-test-not-a-live-model"

    def __init__(self, *, changes=None, malformed=False, fail=False):
        self.calls = 0
        self.changes, self.malformed, self.fail = changes or {}, malformed, fail

    def complete(self, messages, tools, *, deadline=None, max_tokens=700):
        self.calls += 1
        if self.fail:
            raise TimeoutError("PRIVATE_PROVIDER_ERROR")
        if self.malformed:
            return {"content": "PRIVATE_UNSUPPORTED_CLAIM"}
        if self.calls == 1:
            return {
                "tool_calls": [
                    {
                        "id": "test-1",
                        "type": "function",
                        "function": {
                            "name": "comparar_escenarios",
                            "arguments": json.dumps(self.changes),
                        },
                    }
                ]
            }
        result = json.loads(messages[-1]["content"])
        return {
            "content": json.dumps(
                {"comparison_id": result["comparison_id"], "focus": "water"}
            )
        }


class StudioServiceTests(unittest.TestCase):
    def setUp(self):
        self.net = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("External network forbidden"),
        )
        self.net.start()
        self.addCleanup(self.net.stop)

    def test_status_is_disabled_without_reading_or_activating_provider(self):
        with patch.dict(
            "os.environ",
            {"NEBIUS_API_KEY": "test-only-marker", "SOLQUIRA_LIVE_ADAPTERS": "1"},
        ):
            self.assertEqual(
                studio_status(),
                {
                    "available": False,
                    "mode": "disabled",
                    "provider": "nebius",
                    "model": None,
                },
            )
            result = explain_studio(SCENARIO, "Explain the water balance")
        self.assertEqual(result["inference_mode"], "local_fallback")
        self.assertEqual(result["outcome"], "disabled")
        self.assertIsNone(result["model"])

    def test_current_scenario_reaches_engine_not_a_fixed_parcel(self):
        first = evaluate_studio(SCENARIO)
        second = evaluate_studio(
            {"profile_id": "secano", "water_percent": 40, "previous_crop": "leguminosa"}
        )
        self.assertEqual(first["available_water_m3_ha"], 2400)
        self.assertEqual(second["available_water_m3_ha"], 480)
        self.assertNotEqual(first["scenario_revision"], second["scenario_revision"])
        self.assertEqual(second["scenario"]["previous_crop"], "leguminosa")

    def test_complete_report_is_english_and_preserves_limits(self):
        report = evaluate_studio(SCENARIO)
        self.assertEqual(report["language"], "en")
        self.assertIn(
            "Agronomic viability has not been assessed", report["explanation"]
        )
        self.assertEqual(report["resilience"]["environmental_impact"], "not_measured")
        self.assertEqual(report["agronomicViability"], "not_assessed")
        self.assertTrue(report["synthetic"])
        self.assertFalse(report["decision"]["external_actions_executed"])
        for plan in report["plans"]:
            self.assertEqual(len(plan["sequence"]), len(plan["sequence_ids"]))
            labels = {
                "cereal": "Cereal",
                "leguminosa": "Legume",
                "cubierta": "Cover crop",
                "descanso": "Fallow",
            }
            self.assertEqual(
                plan["sequence"], [labels[crop] for crop in plan["sequence_ids"]]
            )
            self.assertIn("demand_m3_ha", plan)
            self.assertIn("score", plan)

    def test_all_108_reports_match_fixture_oracles(self):
        fixture = (
            Path(__file__).resolve().parents[3]
            / "artifacts/solquira/src/studio/fixtures.json"
        )
        data = json.loads(fixture.read_text(encoding="utf-8"))
        for profile in ("valle", "secano", "precordillera"):
            for crop in ("cereal", "leguminosa", "cubierta", "descanso"):
                for water in range(40, 121, 10):
                    with self.subTest(profile=profile, crop=crop, water=water):
                        result = evaluate_studio(
                            {
                                "profile_id": profile,
                                "water_percent": water,
                                "previous_crop": crop,
                            }
                        )
                        expected = data["historyEvaluations"][
                            f"{profile}:{water}:{crop}"
                        ]
                        self.assertEqual(
                            result["available_water_m3_ha"],
                            expected["availableWaterM3Ha"],
                        )
                        self.assertEqual(
                            [p["eligible"] for p in result["plans"]],
                            [
                                p["eligibleUnderDemoRules"]
                                for p in expected["rotations"]
                            ],
                        )
                        self.assertEqual(
                            [p["demand_m3_ha"] for p in result["plans"]],
                            [p["demandM3Ha"] for p in expected["rotations"]],
                        )

    def test_disabled_explanation_executes_real_tools(self):
        result = explain_studio(SCENARIO, "What evidence supports soil restoration?")
        names = [row["tool"] for row in result["trace"]]
        self.assertEqual(
            names,
            [
                "leer_escenario",
                "comparar_escenarios",
                "revisar_evidencia",
                "revisar_suelo_cobertura",
                "preparar_decision",
            ],
        )
        self.assertTrue(all(row["status"] == "executed" for row in result["trace"]))
        self.assertEqual(result["report"]["focus"], "resilience")
        self.assertEqual(result["report"]["scenario"], SCENARIO)

    def test_soil_question_returns_actionable_review_not_only_a_scope_notice(self):
        result = explain_studio(SCENARIO, "What should I check about soil cover?")
        report = result["report"]
        self.assertEqual(report["focus"], "resilience")
        for question in report["resilience"]["open_questions"]:
            self.assertIn(question, report["explanation"])
        self.assertEqual(report["resilience"]["observations"], [])
        self.assertEqual(report["resilience"]["environmental_impact"], "not_measured")
        self.assertEqual(len(result["trace"]), 5)

    def test_scenario_and_question_are_not_persisted_or_mutated(self):
        scenario = copy.deepcopy(SCENARIO)
        result = explain_studio(scenario, "Try much less water")
        result["requested_scenario"]["water_percent"] = 40
        self.assertEqual(scenario, SCENARIO)
        self.assertEqual(evaluate_studio(SCENARIO)["scenario"], SCENARIO)

    def test_injected_stub_runs_loop_but_never_claims_live(self):
        provider = Provider()
        result = explain_studio(SCENARIO, "Explain water", provider=provider)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(result["inference_mode"], "provider_stub")
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["report"]["scenario"], SCENARIO)
        self.assertNotIn("proposed_scenario", result)

    def test_alternate_scenario_is_explicit_and_does_not_replace_request(self):
        result = explain_studio(
            SCENARIO,
            "Explore 40 percent",
            provider=Provider(changes={"water_percent": 40}),
        )
        self.assertEqual(result["requested_scenario"], SCENARIO)
        self.assertEqual(result["proposed_scenario"]["water_percent"], 40)
        self.assertEqual(result["report"]["available_water_m3_ha"], 960)
        self.assertEqual(SCENARIO["water_percent"], 100)

    def test_malformed_provider_output_is_replaced_by_honest_fallback(self):
        result = explain_studio(SCENARIO, "Explain", provider=Provider(malformed=True))
        self.assertEqual(result["outcome"], "invalid_output")
        self.assertEqual(result["inference_mode"], "local_fallback")
        self.assertNotIn("PRIVATE_UNSUPPORTED_CLAIM", json.dumps(result))
        self.assertIsNone(result["model"])

    def test_timeout_has_effective_mode_and_no_raw_error(self):
        result = explain_studio(SCENARIO, "Explain", provider=Provider(fail=True))
        self.assertEqual(result["outcome"], "timeout")
        self.assertEqual(result["inference_mode"], "local_fallback")
        self.assertNotIn("PRIVATE_PROVIDER_ERROR", json.dumps(result))

    def test_cancelled_request_does_not_execute_model(self):
        cancelled = threading.Event()
        cancelled.set()
        provider = Provider()
        with self.assertRaises(AgentError) as error:
            explain_studio(
                SCENARIO, "Explain", provider=provider, cancel_event=cancelled
            )
        self.assertEqual(error.exception.code, "cancelled")
        self.assertEqual(provider.calls, 0)

    def test_malformed_scenarios_rejected_before_execution(self):
        for changes in (
            {"water_percent": True},
            {"water_percent": 40.0},
            {"water_percent": 41},
            {"profile_id": "world"},
            {"previous_crop": []},
            {"owner": "other"},
        ):
            scenario = {**SCENARIO, **changes}
            with self.subTest(scenario=scenario), self.assertRaises(AgentError):
                evaluate_studio(scenario)
        with self.assertRaises(AgentError):
            evaluate_studio({})

    def test_invalid_question_rejected(self):
        for query in ("", "  ", "x" * 1001, None):
            with self.subTest(query=query), self.assertRaises(AgentError):
                explain_studio(SCENARIO, query)


if __name__ == "__main__":
    unittest.main()
