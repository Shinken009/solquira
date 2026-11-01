import copy
import hashlib
import json
import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parents[1]
sys.path.insert(0, str(BASE / "src"))
from solquira_agent import Agent, AgentError, DemoTools, NebiusProvider
from solquira_agent.core import MAX_TURNS, _engine


def call(name="comparar_escenarios", args=None, cid="c1"):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


class Model:
    mode = "scripted_demo"
    model = "test-only-not-nemotron"

    def __init__(self, callback):
        self.callback, self.calls = callback, 0

    def complete(self, messages, tools):
        self.calls += 1
        return self.callback(messages, tools)


def working(messages, schemas):
    if messages[-1]["role"] == "user":
        return {
            "tool_calls": [
                call(args={"water_percent": 40, "previous_crop": "leguminosa"})
            ]
        }
    data = json.loads(messages[-1]["content"])
    return {
        "content": json.dumps({"comparison_id": data["comparison_id"], "focus": "agua"})
    }


class EngineProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.file = REPO / "artifacts/solquira/backend/main.py"
        self.source_sha = hashlib.sha256(self.file.read_bytes()).hexdigest()

    def test_running_engine_reused_only_with_matching_import_capture(self):
        loaded = SimpleNamespace(
            __file__=str(self.file), evaluate=Mock(), _solquira_source_sha=self.source_sha
        )
        with patch.dict(sys.modules, {"main": loaded}):
            self.assertIs(_engine(REPO), loaded)
        self.assertEqual(loaded._solquira_source_sha, self.source_sha)
        loaded.evaluate.assert_not_called()

    def test_disk_drift_requires_restart_without_relabelling_loaded_code(self):
        loaded = SimpleNamespace(
            __file__=str(self.file), evaluate=Mock(), _solquira_source_sha=self.source_sha
        )
        with (
            patch.dict(sys.modules, {"main": loaded}),
            patch.object(Path, "read_bytes", return_value=b"changed source on disk"),
        ):
            with self.assertRaises(AgentError) as error:
                _engine(REPO)
        self.assertEqual(error.exception.code, "engine_restart_required")
        self.assertEqual(loaded._solquira_source_sha, self.source_sha)
        loaded.evaluate.assert_not_called()

    def test_running_engine_without_import_capture_is_not_relabelled(self):
        loaded = SimpleNamespace(__file__=str(self.file), evaluate=Mock())
        with patch.dict(sys.modules, {"main": loaded}):
            with self.assertRaises(AgentError) as error:
                _engine(REPO)
        self.assertEqual(error.exception.code, "engine_restart_required")
        self.assertFalse(hasattr(loaded, "_solquira_source_sha"))
        loaded.evaluate.assert_not_called()

    def test_private_cached_engine_must_keep_immutable_matching_capture(self):
        name = (
            "_solquira_agent_demo_engine_"
            + hashlib.sha256((str(self.file) + self.source_sha).encode()).hexdigest()[
                :12
            ]
        )
        for captured in (None, "old-loaded-source"):
            with self.subTest(captured=captured):
                loaded = SimpleNamespace(evaluate=Mock())
                if captured is not None:
                    loaded._solquira_source_sha = captured
                with patch.dict(sys.modules, {"main": None, name: loaded}):
                    with self.assertRaises(AgentError) as error:
                        _engine(REPO)
                self.assertEqual(error.exception.code, "engine_restart_required")
                self.assertEqual(getattr(loaded, "_solquira_source_sha", None), captured)
                loaded.evaluate.assert_not_called()


class BaseTests(unittest.TestCase):
    def setUp(self):
        self.net = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("Real network forbidden"),
        )
        self.net.start()
        self.addCleanup(self.net.stop)
        self.tools = DemoTools(REPO)

    def test_all_108_fixture_scenarios_match_real_engine(self):
        data = json.loads(
            (REPO / "artifacts/solquira/src/studio/fixtures.json").read_text(
                encoding="utf-8"
            )
        )
        for profile in ("valle", "secano", "precordillera"):
            for history in ("cereal", "leguminosa", "cubierta", "descanso"):
                tools = DemoTools(REPO, profile=profile, history=history)
                for percent in range(40, 121, 10):
                    with self.subTest(profile=profile, history=history, water=percent):
                        actual = tools.dispatch(
                            "comparar_escenarios", {"water_percent": percent}
                        )
                        expected = data["historyEvaluations"][
                            f"{profile}:{percent}:{history}"
                        ]
                        self.assertEqual(
                            actual["available_water_m3_ha"],
                            expected["availableWaterM3Ha"],
                        )
                        self.assertEqual(
                            [p["eligible"] for p in actual["plans"]],
                            [
                                p["eligibleUnderDemoRules"]
                                for p in expected["rotations"]
                            ],
                        )
                        self.assertEqual(
                            [p["balance_m3_ha"] for p in actual["plans"]],
                            [p["waterBalanceM3Ha"] for p in expected["rotations"]],
                        )

    def test_scenario_is_not_mutated(self):
        before = self.tools.dispatch("leer_escenario", {})
        self.tools.dispatch(
            "comparar_escenarios", {"water_percent": 40, "previous_crop": "descanso"}
        )
        self.assertEqual(before, self.tools.dispatch("leer_escenario", {}))

    def test_invalid_arguments_fail_closed(self):
        for args in (
            {"water_percent": True},
            {"water_percent": 41},
            {"water_percent": float("nan")},
            {"previous_crop": []},
            {"owner": "forged"},
        ):
            with self.subTest(args=args), self.assertRaises(AgentError):
                self.tools.dispatch("comparar_escenarios", args)
        self.assertEqual(self.tools.comparisons, {})

    def test_unknown_tool_denied(self):
        with self.assertRaises(AgentError):
            self.tools.dispatch("execute_shell", {})

    def test_comparison_from_another_context_denied(self):
        other = DemoTools(REPO)
        result = other.dispatch("comparar_escenarios", {})
        with self.assertRaises(AgentError):
            self.tools.dispatch(
                "revisar_evidencia", {"comparison_id": result["comparison_id"]}
            )

    def test_missing_data_is_not_a_recommendation(self):
        self.tools.parcel["soil"] = "desconocido"
        result = self.tools.dispatch("comparar_escenarios", {})
        report = self.tools.report(result["comparison_id"], "decision")
        self.assertEqual(report["status"], "insufficient_data")
        self.assertEqual(report["plans"], [])
        self.assertIn("Soil type", report["missing_fields"])
        self.assertFalse(report["decision"]["external_actions_executed"])

    def test_evidence_and_decision_are_not_agronomic_validation(self):
        result = self.tools.dispatch("comparar_escenarios", {})
        report = self.tools.report(result["comparison_id"], "evidencia")
        self.assertEqual(report["agronomicViability"], "not_assessed")
        self.assertEqual(report["evidence"]["sources"][0]["kind"], "synthetic")
        self.assertEqual(report["decision"]["decision_status"], "human_review_required")

    def test_desertification_is_objective_not_a_claimed_measurement(self):
        result = self.tools.dispatch("comparar_escenarios", {})
        review = self.tools.dispatch(
            "revisar_suelo_cobertura", {"comparison_id": result["comparison_id"]}
        )
        self.assertEqual(review["objective"], "contribuir_a_disminuir_desertificacion")
        self.assertEqual(review["assessment_status"], "not_assessed")
        self.assertEqual(review["environmental_impact"], "not_measured")
        self.assertEqual(review["observations"], [])
        self.assertNotIn("risk_score", review)

    def test_resilience_review_does_not_change_eligibility(self):
        result = self.tools.dispatch("comparar_escenarios", {})
        before = copy.deepcopy(result["plans"])
        report = self.tools.report(result["comparison_id"], "resiliencia")
        self.assertEqual(report["plans"], before)
        self.assertEqual(report["resilience"]["environmental_impact"], "not_measured")

    def test_focused_explanations_use_returned_questions_and_checks(self):
        comparison = self.tools.dispatch("comparar_escenarios", {})
        cid = comparison["comparison_id"]
        for focus, section, key in (
            ("resilience", "resilience", "open_questions"),
            ("evidence", "evidence", "not_available"),
            ("decision", "decision", "pending"),
        ):
            with self.subTest(focus=focus):
                report = self.tools.report(cid, focus)
                for item in report[section][key]:
                    self.assertIn(item, report["explanation"])
                self.assertEqual(report["plans"], comparison["plans"])
                self.assertFalse(report["decision"]["external_actions_executed"])

    def test_missing_water_returns_gap_without_formatting_failure(self):
        self.tools.parcel["waterM3Ha"] = None
        result = self.tools.dispatch("comparar_escenarios", {})
        report = self.tools.report(result["comparison_id"], "water")
        self.assertEqual(report["status"], "insufficient_data")
        self.assertIn("Available water", report["missing_fields"])
        self.assertEqual(report["plans"], [])

    def test_report_reuses_already_executed_reviews(self):
        result = self.tools.dispatch("comparar_escenarios", {})
        cid = result["comparison_id"]
        self.tools.report(cid, "water")
        with patch.object(
            self.tools, "dispatch", side_effect=AssertionError("Review must be reused")
        ):
            self.tools.report(cid, "evidence")

    def test_cancelled_loop_does_not_call_model(self):
        import threading

        event = threading.Event()
        event.set()
        provider = Model(working)
        with self.assertRaises(AgentError) as error:
            Agent(self.tools, provider).run("Explain", cancel_event=event)
        self.assertEqual(error.exception.code, "cancelled")
        self.assertEqual(provider.calls, 0)

    def test_bounded_loop_executes_real_tool(self):
        provider = Model(working)
        result = Agent(self.tools, provider).run("Explora menos agua")
        self.assertEqual(result["inference_mode"], "scripted_demo")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(result["report"]["available_water_m3_ha"], 960)
        self.assertEqual(result["trace"][0]["tool"], "comparar_escenarios")

    def test_raw_model_text_never_becomes_report(self):
        provider = Model(lambda *_: {"content": "RAW_UNSUPPORTED_CLAIM"})
        with self.assertRaises(AgentError) as error:
            Agent(self.tools, provider).run("Consulta")
        self.assertNotIn("RAW_UNSUPPORTED_CLAIM", str(error.exception))

    def test_forged_final_comparison_is_rejected(self):
        provider = Model(
            lambda *_: {
                "content": json.dumps({"comparison_id": "forged", "focus": "agua"})
            }
        )
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("Consulta")

    def test_old_comparison_not_reused_as_fresh_tool_execution(self):
        old = self.tools.dispatch("comparar_escenarios", {})
        provider = Model(
            lambda *_: {
                "content": json.dumps(
                    {"comparison_id": old["comparison_id"], "focus": "agua"}
                )
            }
        )
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("Consulta")

    def test_extra_final_prose_is_rejected(self):
        def answer(messages, schemas):
            if messages[-1]["role"] == "user":
                return {"tool_calls": [call()]}
            result = json.loads(messages[-1]["content"])
            return {
                "content": json.dumps(
                    {
                        "comparison_id": result["comparison_id"],
                        "focus": "agua",
                        "explanation": "made up",
                    }
                )
            }

        with self.assertRaises(AgentError):
            Agent(self.tools, Model(answer)).run("Consulta")

    def test_invalid_mixed_batch_executes_nothing(self):
        provider = Model(
            lambda *_: {"tool_calls": [call(), call("execute_shell", cid="c2")]}
        )
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("Consulta")
        self.assertEqual(self.tools.comparisons, {})

    def test_tool_budget_checked_before_execution(self):
        provider = Model(
            lambda *_: {"tool_calls": [call(cid=f"c{i}") for i in range(7)]}
        )
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("Consulta")
        self.assertEqual(self.tools.comparisons, {})

    def test_turn_budget_stops_loop(self):
        counter = [0]

        def forever(*_):
            counter[0] += 1
            return {"tool_calls": [call("leer_escenario", cid=f"c{counter[0]}")]}

        provider = Model(forever)
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("Consulta")
        self.assertEqual(provider.calls, MAX_TURNS)

    def test_oversized_input_does_not_call_model(self):
        provider = Model(working)
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("x" * 1001)
        self.assertEqual(provider.calls, 0)

    def test_provider_is_disabled_without_explicit_opt_in(self):
        with self.assertRaises(AgentError):
            NebiusProvider()

    def test_endpoint_and_model_validation(self):
        # Built at runtime so the public package never contains an inline-user-info
        # URL literal: the release scan rejects those, even as a negative fixture.
        inline_userinfo = (
            "https://" + "user" + ":" + "pass" + "@api.tokenfactory.nebius.com/v1"
        )
        for url in (
            "http://api.tokenfactory.nebius.com/v1",
            "https://example.com/v1",
            inline_userinfo,
            "https://api.tokenfactory.nebius.com/v1?secret=x",
        ):
            with self.subTest(url=url), self.assertRaises(AgentError):
                NebiusProvider(
                    enabled=True,
                    api_key="test-only",
                    model="nvidia/nemotron-test",
                    base_url=url,
                )
        with self.assertRaises(AgentError):
            NebiusProvider(enabled=True, api_key="test-only", model="other/model")

    def test_provider_stub_is_never_labelled_live(self):
        import httpx

        def respond(request):
            body = json.loads(request.content)
            self.assertEqual(body["model"], "nvidia/nemotron-test")
            self.assertEqual(request.url.host, "api.tokenfactory.nebius.com")
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}}]}
            )

        provider = NebiusProvider(
            enabled=True,
            api_key="test-only",
            model="nvidia/nemotron-test",
            transport=httpx.MockTransport(respond),
        )
        self.assertEqual(provider.mode, "provider_stub")
        self.assertEqual(provider.complete([], []), {"content": "{}"})

    def test_provider_failure_sanitized_and_not_retried(self):
        import httpx

        calls = []

        def fail(request):
            calls.append(request)
            return httpx.Response(401, text="RAW_PROVIDER_PRIVATE_MARKER")

        provider = NebiusProvider(
            enabled=True,
            api_key="test-only",
            model="nvidia/nemotron-test",
            transport=httpx.MockTransport(fail),
        )
        with self.assertRaises(AgentError) as error:
            provider.complete([], [])
        self.assertNotIn("RAW_PROVIDER_PRIVATE_MARKER", str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_grounded_turn_receives_actual_tools_and_accepts_null_tool_calls(self):
        def answer(messages, schemas):
            self.assertEqual(messages[-1]["role"], "tool")
            comparison = json.loads(messages[-1]["content"])
            self.assertEqual(comparison["available_water_m3_ha"], 2400)
            return {
                "tool_calls": None,
                "content": json.dumps(
                    {"comparison_id": comparison["comparison_id"], "focus": "water"}
                ),
            }

        model = Model(answer)
        result = Agent(self.tools, model).run("Explain current water", grounded=True)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result["report"]["scenario"]["water_percent"], 100)
        self.assertEqual(len(result["trace"]), 2)
        self.assertEqual(
            self.tools.missing_reviews(result["report"]["comparison_id"]), 0
        )

    def test_grounded_final_cannot_invent_a_comparison(self):
        provider = Model(
            lambda *_: {
                "content": json.dumps({"comparison_id": "invented", "focus": "water"})
            }
        )
        with self.assertRaises(AgentError):
            Agent(self.tools, provider).run("Explain", grounded=True)

    def test_truncated_response_has_a_distinct_sanitized_outcome(self):
        import httpx

        provider = NebiusProvider(
            enabled=True,
            api_key="test-only",
            model="nvidia/nemotron-test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": "PRIVATE_TRUNCATED"},
                            }
                        ],
                        "usage": {"completion_tokens": 700},
                    },
                )
            ),
        )
        with self.assertRaises(AgentError) as exc:
            provider.complete([], [])
        self.assertEqual(exc.exception.code, "response_truncated")
        self.assertEqual(provider.last_usage, {"completion_tokens": 700})
        self.assertNotIn("PRIVATE_TRUNCATED", str(exc.exception))

    def test_nano_reasoning_control_is_explicit_and_model_scoped(self):
        import httpx

        def respond(request):
            self.assertEqual(
                json.loads(request.content)["chat_template_kwargs"],
                {"enable_thinking": False},
            )
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}}]}
            )

        provider = NebiusProvider(
            enabled=True,
            api_key="test-only",
            model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
            disable_reasoning=True,
            transport=httpx.MockTransport(respond),
        )
        provider.complete([], [])
        with self.assertRaises(AgentError):
            NebiusProvider(
                enabled=True,
                api_key="test-only",
                model="nvidia/other-nemotron",
                disable_reasoning=True,
            )


if __name__ == "__main__":
    unittest.main()
