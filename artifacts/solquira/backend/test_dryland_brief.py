"""Field-brief contracts: server-owned numbers, bounded text, no live network.

The example payload is a release fixture, not a SoilGrids or FAO observation.
Every provider here is a fake or an httpx MockTransport; sockets are patched.
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import socket
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

import main
import studio_api
import studio_service  # Reviewed agent import path; never activates inference.
import dryland_brief as brief
from solquira_agent import AgentError, NebiusProvider
from studio_budget import BudgetLedger, BudgetPolicy, PersistentBudgetProvider
from studio_runtime import NANO_MODEL


class RecordingProvider:
    """Stands in for the injected Nano provider; records the wire envelope."""

    model = NANO_MODEL
    mode = "provider_stub"

    def __init__(self, content=None, error=None):
        self.calls, self.seen, self._content, self._error = 0, [], content, error

    def complete(self, messages, tools, *, deadline=None, max_tokens=None):
        self.calls += 1
        self.seen.append(
            {"messages": messages, "tools": tools, "max_tokens": max_tokens}
        )
        if self._error is not None:
            raise self._error
        return {"content": self._content}


def compact_json(payload):
    """Provider wording only; HTTP still returns the server-built full card."""
    actions = brief.fallback_brief(payload).model_dump(mode="json")["actions"]
    chosen = [a for a in actions if a["id"] != "biocrust_check"][:2]
    chosen.append(next(a for a in actions if a["id"] == "biocrust_check"))
    return json.dumps({"actions": [{"id": a["id"], "detail": a["detail"]} for a in chosen]})


class GenerationCapTests(unittest.TestCase):
    """The cap belongs to the call: studio reserves 700, the brief asks for 350."""

    def setUp(self):
        self.network = patch(
            "socket.socket.connect",
            side_effect=AssertionError("External network forbidden in brief tests"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    def wire(self, **kwargs):
        bodies = []

        def transport(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]
                },
            )

        provider = NebiusProvider(
            enabled=True,
            api_key="SYNTHETIC_TEST_ONLY",
            model=NANO_MODEL,
            transport=httpx.MockTransport(transport),
        )
        provider.complete([{"role": "user", "content": "Example"}], [], **kwargs)
        return bodies[0]

    def test_studio_call_keeps_the_reserved_output_envelope(self):
        self.assertEqual(self.wire()["max_tokens"], 700)

    def test_explicit_brief_cap_reaches_the_wire(self):
        self.assertEqual(self.wire(max_tokens=brief.MAX_TOKENS)["max_tokens"], 350)

    def test_caps_outside_the_reserved_envelope_are_rejected_before_any_request(self):
        for value in (0, -1, 701, 3500, True, "350", 350.0):
            with self.subTest(value=value), self.assertRaises(AgentError):
                self.wire(max_tokens=value)

    def test_receipt_marks_a_paid_answer_that_failed_validation(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        ledger = BudgetLedger(
            Path(directory.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        payload = brief.golden_payload("dry")
        # A syntactically valid JSON answer that is not a valid brief.
        recording = RecordingProvider(content="{}")
        wrapped = PersistentBudgetProvider(recording, ledger)

        result, outcome, attempt = brief.compose_brief(
            payload, wrapped, deadline=time.monotonic() + 2.9
        )

        self.assertEqual(outcome, "invalid_output")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(attempt.state, "rejected")
        self.assertTrue(attempt.reserved)
        self.assertIn("response_received", attempt.transitions)
        self.assertEqual(recording.calls, 1)
        # The reservation is real even though the answer was discarded.
        self.assertEqual(ledger.snapshot()["calls_used"], 1)

    def test_receipt_distinguishes_a_refused_reservation_from_a_rejected_answer(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # One admitted call: the second attempt is refused before any request.
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 1, 4, 7000)
        ledger = BudgetLedger(
            Path(directory.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        payload = brief.golden_payload("dry")
        recording = RecordingProvider(
            content=compact_json(payload)
        )
        wrapped = PersistentBudgetProvider(recording, ledger)

        first = brief.compose_brief(payload, wrapped, deadline=time.monotonic() + 2.9)
        second = brief.compose_brief(payload, wrapped, deadline=time.monotonic() + 2.9)

        self.assertEqual(first[1], "success")
        self.assertEqual(second[1], "budget_limit")
        self.assertEqual(second[2].state, "none")
        self.assertFalse(second[2].reserved)
        self.assertEqual(recording.calls, 1)

    def test_compose_brief_requests_the_brief_generation_cap(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        ledger = BudgetLedger(
            Path(directory.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        payload = brief.golden_payload("dry")
        recording = RecordingProvider(
            content=compact_json(payload)
        )
        wrapped = PersistentBudgetProvider(recording, ledger)

        result, outcome, attempt = brief.compose_brief(
            payload, wrapped, deadline=time.monotonic() + 2.9
        )

        self.assertEqual(outcome, "success")
        self.assertEqual(attempt.mode, "provider_stub")
        self.assertEqual(attempt.state, "returned")
        self.assertEqual(attempt.reserved, True)
        self.assertEqual(recording.calls, 1)
        self.assertEqual(recording.seen[0]["max_tokens"], brief.MAX_TOKENS)
        self.assertEqual(result.vital_signs.soil_life, payload.soc_band)
        self.assertEqual(result.vital_signs.water_held, payload.awc_band)

    def test_the_model_never_receives_coordinates(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        ledger = BudgetLedger(
            Path(directory.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        payload = brief.golden_payload("dry")
        recording = RecordingProvider(
            content=compact_json(payload)
        )
        brief.compose_brief(
            payload,
            PersistentBudgetProvider(recording, ledger),
            deadline=time.monotonic() + 2.9,
        )
        sent = json.dumps(recording.seen[0]["messages"])
        # The location stays server-side; the model receives the categorical envelope only.
        for leaked in ("12.371", "-1.519", '"lat"', '"lon"', "latitude", "longitude"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, sent)


def _shape(value):
    """Key/type skeleton, ignoring list length so model and fallback compare."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


class BriefContractTests(unittest.TestCase):
    """The Python fixture owns the numbers; the model only owns the wording."""

    def setUp(self):
        self.dry = brief.golden_payload("dry")
        self.rain = brief.golden_payload("rain")

    def test_golden_payload_is_reproducible_and_server_owned(self):
        self.assertEqual(self.dry.lat, 12.371)
        self.assertEqual(self.dry.lon, -1.519)
        self.assertEqual(self.dry.soc_band, brief.SoilLife.weak)  # 6 g/kg
        self.assertEqual(self.dry.awc_band, brief.WaterHeld.fair)  # (0.23-0.11)*600
        self.assertEqual(self.dry.dry_days_ahead, 6)
        self.assertEqual(self.rain.dry_days_ahead, 1)
        self.assertEqual(brief.resolve_payload(self.dry), ("dry", self.dry))
        self.assertEqual(brief.resolve_payload(self.rain), ("rain", self.rain))

    def test_client_measurements_cannot_replace_the_server_fixture(self):
        forged = self.dry.model_copy(update={"dry_days_ahead": 1})
        with self.assertRaises(ValueError):
            brief.resolve_payload(forged)

    def test_bands_follow_the_documented_thresholds(self):
        cases = (
            (0, brief.SoilLife.weak),
            (7.9, brief.SoilLife.weak),
            (8, brief.SoilLife.building),
            (15, brief.SoilLife.strong),
        )
        for value, expected in cases:
            self.assertEqual(brief.band_soc(value), expected)
        for value, expected in (
            (59.9, brief.WaterHeld.low),
            (60, brief.WaterHeld.fair),
            (120, brief.WaterHeld.good),
        ):
            self.assertEqual(brief.band_awc(value), expected)
        # A missing measurement is refused, never banded: the hero card must not
        # claim "Building" or "Fair" soil from data that was never observed.
        for bad in (None, -1, float("nan"), float("inf"), True, "60"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                brief.band_awc(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                brief.band_soc(bad)

    def test_available_water_capacity_guards_incoherent_profiles(self):
        self.assertAlmostEqual(brief.available_water_capacity(0.23, 0.11, 600), 72.0)
        for args in (
            (0.11, 0.23, 600),
            (-0.1, 0.0, 600),
            (0.23, 0.11, 0),
            (1.2, 0.1, 600),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                brief.available_water_capacity(*args)

    def test_model_text_cannot_carry_measurements_or_links(self):
        for text in (
            "Rain in 3 days ahead",
            "Use 40 kg per hectare",
            "See https://example.invalid",
            "Apply herbicide now",
            "Irrigate 100% of the plot",
            # Spelled-out quantities and units the digit rule cannot see.
            "Apply kg of compost here",
            "Water mm per plot daily",
            "Three hectares of cover",
            "Use grams of ash around the stem",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                brief.VitalSigns(soil_life="Weak", water_held="Low", rain_outlook=text)

    def test_model_text_cannot_carry_agrochemical_prescriptions(self):
        # The product forbids chemical prescriptions outright, in either language.
        for text in (
            "Spray glyphosate on the weeds",
            "Use Roundup on the field edges",
            "Apply atrazine before sowing",
            "Spray paraquat between the rows",
            "Aplicar herbicida en el lote",
            "Fumigar con plaguicida el borde",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                brief.ActionItem(
                    id="mulch", title="Spray the field", detail=text, labor="low"
                )

    def test_the_guard_still_accepts_the_shipped_field_language(self):
        # A guard that rejects normal instructions would be useless: keep both
        # shipped briefs and every promised phrase valid.
        for scenario in ("dry", "rain"):
            payload = brief.golden_payload(scenario)
            self.assertTrue(brief.fallback_brief(payload).actions)
        for text in (
            "Dry week ahead — prepare water pockets",
            "Rain may return — inspect water pathways",
            "Keep dark or lichen-covered surface patches undisturbed.",
            "Inspect existing cover and retain suitable plant residues.",
            "Mark candidate water-catching pockets before disturbing soil.",
            "Trace where rain can soak in without concentrating runoff.",
            "Check for returning rain and moist soil before sowing.",
            "Compare a diverse grain and leaf pathway.",
            "Rooting needs care before dry-season resilience.",
        ):
            with self.subTest(text=text):
                brief.VitalSigns(
                    soil_life="Weak", water_held="Low", rain_outlook=text[:72]
                )
                brief.ActionItem(
                    id="mulch", title="Protect bare soil", detail=text, labor="low"
                )

    def model_json(self, payload):
        """A model answer that is valid except where a test says otherwise."""
        data = brief.fallback_brief(payload).model_dump(mode="json")
        data["source"] = "model"
        return data

    def test_parse_refuses_a_pathway_outside_the_payload_catalog(self):
        data = self.model_json(self.dry)
        data["pathways"][0]["id"] = (
            "quinoa"  # dry fixture lists sorghum, millet, amaranth
        )
        with self.assertRaises(ValueError):
            brief.parse_brief(json.dumps(data), self.dry)

    def test_parse_refuses_an_action_that_escapes_the_evidence_policy(self):
        data = self.model_json(self.rain)  # rain fixture: no dry week
        data["actions"][0]["id"] = "delay_sowing"
        with self.assertRaises(ValueError):
            brief.parse_brief(json.dumps(data), self.rain)
        data = self.model_json(self.dry)
        data["actions"] = [
            item for item in data["actions"] if item["id"] != "biocrust_check"
        ]
        with self.assertRaises(ValueError):
            brief.parse_brief(json.dumps(data), self.dry)

    def test_parse_refuses_duplicate_keys_and_non_finite_numbers(self):
        with self.assertRaises(ValueError):
            brief.parse_brief('{"source": "model", "source": "fallback"}', self.dry)
        with self.assertRaises(ValueError):
            brief.parse_brief('{"source": NaN}', self.dry)

    def test_server_overwrites_bands_rain_and_checked_state(self):
        data = self.model_json(self.dry)
        data["vital_signs"].update(soil_life="Strong", water_held="Good")
        for item in data["actions"]:
            item["done"] = True
        parsed = brief.parse_brief(json.dumps(data), self.dry)
        self.assertEqual(parsed.vital_signs.soil_life, self.dry.soc_band)
        self.assertEqual(parsed.vital_signs.water_held, self.dry.awc_band)
        self.assertEqual(parsed.vital_signs.rain_outlook, self.dry.rain_phrase)
        self.assertEqual(parsed.source, "model")
        self.assertTrue(all(item.done is False for item in parsed.actions))

    def test_fallback_and_model_share_one_json_structure(self):
        for payload in (self.dry, self.rain):
            fallback = brief.fallback_brief(payload).model_dump(mode="json")
            model = brief.parse_brief(
                json.dumps(self.model_json(payload)), payload
            ).model_dump(mode="json")
            self.assertEqual(_shape(fallback), _shape(model))
            self.assertEqual(fallback["source"], "fallback")
            self.assertEqual(model["source"], "model")
            self.assertEqual(
                set(fallback["vital_signs"]),
                {"soil_life", "water_held", "rain_outlook"},
            )
            self.assertEqual(
                {item["id"] for item in fallback["pathways"]},
                {pathway.value for pathway in payload.fao_crops},
            )


class SlowProvider:
    """Answers long after the deadline; used to inspect the receipt, not the brief."""

    model = NANO_MODEL
    mode = "provider_stub"

    def __init__(self, seconds=1.0):
        self.calls, self.seconds = 0, seconds

    def complete(self, messages, tools, *, deadline=None, max_tokens=None):
        self.calls += 1
        time.sleep(self.seconds)
        return {"content": "{}"}


class FieldApiTests(unittest.TestCase):
    """Same-origin Studio session, server-built payload, bounded brief route."""

    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SOLQUIRA_STUDIO_MODE": "1",
                "SOLQUIRA_STUDIO_ALLOWED_HOSTS": "testserver",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        studio_api.ADMISSION.reset()
        self.addCleanup(studio_api.ADMISSION.reset)
        self.service = SimpleNamespace(
            studio_status=Mock(
                return_value={"available": False, "mode": "disabled", "model": None}
            ),
        )
        self.service_patch = patch.object(
            studio_api, "_service", return_value=self.service
        )
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)
        original_connect = socket.socket.connect

        def deny_external_connect(sock, address):
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original_connect(sock, address)
            raise AssertionError("External socket network forbidden")

        self.network = patch("socket.socket.connect", new=deny_external_connect)
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.csrf = self.bootstrap()

    def bootstrap(self):
        response = self.client.get("/solquira-api/studio/bootstrap")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def payload(self, scenario="dry"):
        response = self.client.get(
            f"/v1/field-payload?scenario={scenario}",
            headers={studio_api.CSRF_HEADER: self.csrf},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def post_brief(self, body, csrf=None):
        return self.client.post(
            "/v1/field-brief",
            json=body,
            headers={studio_api.CSRF_HEADER: self.csrf if csrf is None else csrf},
        )

    def test_payload_requires_a_studio_session(self):
        with TestClient(main.app) as anonymous:
            self.assertEqual(
                anonymous.get(
                    "/v1/field-payload?scenario=dry",
                    headers={studio_api.CSRF_HEADER: self.csrf},
                ).status_code,
                401,
            )

    def test_payload_is_server_built_and_scenario_bound(self):
        response = self.client.get(
            "/v1/field-payload?scenario=dry",
            headers={studio_api.CSRF_HEADER: self.csrf},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["lat"], body["lon"]), (12.371, -1.519))
        self.assertEqual(body["soc_band"], "Weak")
        self.assertEqual(body["awc_band"], "Fair")
        self.assertEqual(body["dry_days_ahead"], 6)
        self.assertEqual(
            response.headers["X-Solquira-Evidence"], "synthetic-golden-path"
        )
        self.assertEqual(response.headers["X-Solquira-Scenario"], "dry")
        self.assertNotIn("X-Solquira-Outcome", response.headers)
        self.assertEqual(self.payload("rain")["dry_days_ahead"], 1)

    def test_payload_scenario_selection_is_strict(self):
        for query in (
            "",
            "?scenario=monsoon",
            "?scenario=dry&scenario=rain",
            "?scenario=dry&extra=1",
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    self.client.get(
                        "/v1/field-payload" + query,
                        headers={studio_api.CSRF_HEADER: self.csrf},
                    ).status_code,
                    422,
                )

    def test_brief_needs_csrf_and_a_json_body(self):
        body = self.payload()
        self.assertEqual(self.post_brief(body, csrf="wrong-token").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/v1/field-brief",
                content=json.dumps(body),
                headers={studio_api.CSRF_HEADER: self.csrf},
            ).status_code,
            415,
        )

    def test_brief_falls_back_without_a_provider_and_keeps_one_structure(self):
        body = self.payload()
        response = self.post_brief(body)
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value["source"], "fallback")
        self.assertEqual(value["vital_signs"]["soil_life"], "Weak")
        self.assertEqual(value["vital_signs"]["water_held"], "Fair")
        self.assertEqual(response.headers["X-Solquira-Outcome"], "disabled")
        self.assertEqual(response.headers["X-Solquira-Inference"], "disabled")
        self.assertEqual(response.headers["X-Solquira-Model"], "")
        self.assertEqual(response.headers["X-Solquira-Max-Tokens"], "350")
        self.assertEqual(response.headers["X-Solquira-Deadline-Ms"], "3000")
        self.assertLess(int(response.headers["X-Solquira-Latency-Ms"]), 3000)
        self.assertEqual(
            _shape(value),
            _shape(
                brief.fallback_brief(brief.golden_payload("dry")).model_dump(
                    mode="json"
                )
            ),
        )

    def test_brief_timeout_reports_the_called_model_instead_of_denying_it(self):
        ledger_dir = tempfile.TemporaryDirectory()
        self.addCleanup(ledger_dir.cleanup)
        payload = brief.golden_payload("dry")
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        ledger = BudgetLedger(
            Path(ledger_dir.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        slow = SlowProvider(seconds=1.0)
        provider = PersistentBudgetProvider(slow, ledger)
        with (
            patch.object(studio_api, "TIMEOUT_S", 0.4),
            patch.object(studio_api, "current_provider", return_value=provider),
        ):
            response = self.post_brief(payload.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "fallback")
        self.assertEqual(response.headers["X-Solquira-Outcome"], "timeout")
        # The provider was genuinely called and the ledger reserved for it, so the
        # receipt must not claim "no model request".
        self.assertEqual(response.headers["X-Solquira-Inference"], "provider_stub")
        self.assertEqual(response.headers["X-Solquira-Model"], brief.MODEL)
        self.assertEqual(slow.calls, 1)
        self.assertEqual(ledger.snapshot()["calls_used"], 1)

    def test_receipt_exposes_attempt_state_and_reservation(self):
        ledger_dir = tempfile.TemporaryDirectory()
        self.addCleanup(ledger_dir.cleanup)
        payload = brief.golden_payload("dry")
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        ledger = BudgetLedger(
            Path(ledger_dir.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        provider = PersistentBudgetProvider(RecordingProvider(content="{}"), ledger)
        with patch.object(studio_api, "current_provider", return_value=provider):
            response = self.post_brief(payload.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "fallback")
        self.assertEqual(response.headers["X-Solquira-Attempt"], "rejected")
        self.assertEqual(response.headers["X-Solquira-Reserved"], "1")
        self.assertIn("response_received", response.headers["X-Solquira-Transitions"])
        self.assertEqual(ledger.snapshot()["calls_used"], 1)

    def test_receipt_without_a_provider_reports_no_attempt(self):
        response = self.post_brief(self.payload())
        self.assertEqual(response.headers["X-Solquira-Attempt"], "none")
        self.assertEqual(response.headers["X-Solquira-Reserved"], "0")

    def test_brief_refuses_client_measurements(self):
        body = self.payload()
        body["dry_days_ahead"] = 1  # rewrite the week, keep the rest
        response = self.post_brief(body)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("exception", response.text.lower())

    def test_brief_reports_a_validated_model_composition(self):
        ledger_dir = tempfile.TemporaryDirectory()
        self.addCleanup(ledger_dir.cleanup)
        payload = brief.golden_payload("dry")
        policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        ledger = BudgetLedger(
            Path(ledger_dir.name) / "ledger.sqlite3",
            policy,
            clock=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        ledger.initialize()
        recording = RecordingProvider(
            content=compact_json(payload)
        )
        provider = PersistentBudgetProvider(recording, ledger)
        with patch.object(studio_api, "current_provider", return_value=provider):
            response = self.post_brief(payload.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value["source"], "model")
        self.assertEqual(response.headers["X-Solquira-Outcome"], "success")
        self.assertEqual(response.headers["X-Solquira-Inference"], "provider_stub")
        self.assertEqual(response.headers["X-Solquira-Model"], brief.MODEL)
        self.assertEqual(recording.calls, 1)
        self.assertEqual(recording.seen[0]["max_tokens"], brief.MAX_TOKENS)
        self.assertEqual(ledger.snapshot()["calls_used"], 1)


if __name__ == "__main__":
    unittest.main()
