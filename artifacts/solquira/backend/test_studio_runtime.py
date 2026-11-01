"""No-network tests of explicit, expiring, process-wide trial admission."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
import studio_service
import studio_runtime as runtime
from solquira_agent import AgentError


class Fake:
    model = runtime.NANO_MODEL
    mode = "provider_stub"
    last_usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    last_http_status = 200

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def complete(self, messages, tools, *, deadline=None, max_tokens=700):
        self.calls += 1
        self.seen_max_tokens = max_tokens
        if self.fail:
            raise AgentError("Sanitized failure", code="provider_error")
        return {"content": "{}"}


class TrialTests(unittest.TestCase):
    def test_import_does_not_activate_provider(self):
        with patch.object(runtime, "_provider", None):
            self.assertIsNone(runtime.current_provider())
            self.assertFalse(runtime.status()["available"])

    def test_explicit_injection_reports_effective_model(self):
        with patch.object(runtime, "_provider", None):
            trial = runtime.ConnectionTrial(Fake())
            runtime.install_trial(trial)
            self.assertIs(runtime.current_provider(), trial)
            self.assertEqual(
                studio_service.studio_status()["model"], runtime.NANO_MODEL
            )
            with self.assertRaises(ValueError):
                runtime.install_trial(trial)

    def test_global_request_limit_is_atomic(self):
        provider = Fake()
        trial = runtime.ConnectionTrial(provider)

        def run(_):
            try:
                trial.complete([], [])
                return True
            except AgentError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(run, range(12))), 5)
        self.assertEqual(provider.calls, 5)
        self.assertEqual(len(trial.receipts()), 5)
        self.assertFalse(trial.status()["available"])

    def test_failed_attempt_consumes_limit_without_retry(self):
        provider = Fake(fail=True)
        trial = runtime.ConnectionTrial(provider, calls=1)
        with self.assertRaises(AgentError):
            trial.complete([], [])
        with self.assertRaises(AgentError):
            trial.complete([], [])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(trial.receipts()[0]["outcome"], "provider_error")

    def test_expiry_and_oversize_precede_network(self):
        provider = Fake()
        trial = runtime.ConnectionTrial(provider)
        with patch.object(trial, "_expires", 0):
            with self.assertRaises(AgentError):
                trial.complete([], [])
        with self.assertRaises(AgentError):
            trial.complete([{"content": "x" * 100001}], [])
        self.assertEqual(provider.calls, 0)

    def test_unknown_model_or_enlarged_budget_denied(self):
        with self.assertRaises(ValueError):
            runtime.ConnectionTrial(SimpleNamespace(model="nvidia/unknown-nemotron"))
        with self.assertRaises(ValueError):
            runtime.ConnectionTrial(Fake(), calls=6)
        with self.assertRaises(ValueError):
            runtime.ConnectionTrial(Fake(), seconds=1201)
