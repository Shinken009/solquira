"""Persistent hosted admission with fake providers only; no external connections."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import studio_service  # Reviewed agent import path; never activates inference.
import studio_runtime as runtime
from studio_budget import (
    BudgetError,
    BudgetLedger,
    BudgetLimit,
    BudgetPolicy,
    PersistentBudgetProvider,
)
from solquira_agent import AgentError


class FakeProvider:
    model = runtime.NANO_MODEL
    mode = "provider_stub"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def complete(self, messages, tools, *, deadline=None, max_tokens=700):
        self.calls += 1
        if self.fail:
            raise AgentError("Synthetic transport failure", code="provider_error")
        return {"content": "{}"}


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.today = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.policy = BudgetPolicy("test-ledger-identity", 20000, 40000, 2, 4, 7000)
        self.ledger = BudgetLedger(self.path, self.policy, clock=lambda: self.today)
        self.network = patch(
            "socket.socket.connect",
            side_effect=AssertionError("External network forbidden in budget tests"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    def initialize(self):
        self.ledger.initialize()
        return self.ledger

    def test_missing_ledger_is_not_created_by_snapshot_or_wrapper(self):
        for action in (
            self.ledger.snapshot,
            lambda: PersistentBudgetProvider(FakeProvider(), self.ledger),
        ):
            with self.assertRaises(BudgetError):
                action()
        self.assertFalse(self.path.exists())

    def test_explicit_initialization_never_overwrites_existing_ledger(self):
        self.initialize().reserve()
        before = self.path.read_bytes()
        with self.assertRaises(BudgetError):
            self.ledger.initialize()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ledger.snapshot()["calls_used"], 1)

    def test_boolean_fractional_negative_and_incoherent_limits_are_rejected(self):
        for field, value in (
            ("daily_calls", True),
            ("lifetime_calls", 2.5),
            ("daily_micro_usd", 0),
            ("reservation_micro_usd", 6167),
            ("daily_calls", 5),
            ("daily_micro_usd", 50000),
            ("budget_id", "x"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(BudgetError):
                replace(self.policy, **{field: value})
        with self.assertRaises(BudgetError):
            BudgetLedger("relative.sqlite3", self.policy)

    def test_daily_call_limit_is_atomic_across_independent_connections(self):
        self.initialize()

        def reserve(_):
            ledger = BudgetLedger(self.path, self.policy, clock=lambda: self.today)
            try:
                ledger.reserve()
                return True
            except BudgetLimit:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(12))), 2)
        self.assertEqual(self.ledger.snapshot()["reserved_micro_usd"], 14000)

    def test_daily_spend_ceiling_applies_even_with_more_calls(self):
        policy = replace(self.policy, daily_micro_usd=13000)
        ledger = BudgetLedger(self.path, policy, clock=lambda: self.today)
        ledger.initialize()
        ledger.reserve()
        with self.assertRaises(BudgetLimit):
            ledger.reserve()
        self.assertEqual(ledger.snapshot()["calls_used"], 1)

    def test_lifetime_survives_restart_new_date_and_clock_return(self):
        self.initialize()
        self.ledger.reserve()
        self.ledger.reserve()
        self.today += timedelta(days=1)
        restarted = BudgetLedger(self.path, self.policy, clock=lambda: self.today)
        self.assertEqual(restarted.snapshot()["calls_used"], 2)
        restarted.reserve()
        restarted.reserve()
        self.today += timedelta(days=1)
        with self.assertRaises(BudgetLimit):
            restarted.reserve()
        self.today -= timedelta(days=2)
        with self.assertRaises(BudgetLimit):
            restarted.reserve()
        self.assertEqual(restarted.snapshot()["calls_used"], 4)

    def test_lifetime_spend_ceiling_is_separate_from_lifetime_calls(self):
        policy = replace(self.policy, lifetime_micro_usd=20000)
        ledger = BudgetLedger(self.path, policy, clock=lambda: self.today)
        ledger.initialize()
        ledger.reserve()
        ledger.reserve()
        self.today += timedelta(days=1)
        with self.assertRaises(BudgetLimit):
            ledger.reserve()
        self.assertEqual(ledger.snapshot()["calls_used"], 2)

    def test_changed_policy_id_or_limits_fail_closed_in_both_directions(self):
        self.initialize().reserve()
        for change in (
            {"daily_micro_usd": 21000},
            {"daily_micro_usd": 19000},
            {"lifetime_micro_usd": 50000},
            {"lifetime_micro_usd": 30000},
            {"daily_calls": 1},
            {"budget_id": "different-budget-id"},
            {"reservation_micro_usd": 8000},
        ):
            with self.subTest(change=change):
                changed = BudgetLedger(
                    self.path, replace(self.policy, **change), clock=lambda: self.today
                )
                with self.assertRaises(BudgetError):
                    changed.snapshot()
                with self.assertRaises(BudgetError):
                    changed.reserve()
        self.assertEqual(self.ledger.snapshot()["calls_used"], 1)

    def test_corrupt_file_or_missing_rows_do_not_reinitialize(self):
        self.path.write_bytes(b"not a ledger")
        before = self.path.read_bytes()
        with self.assertRaises(BudgetError):
            self.ledger.snapshot()
        self.assertEqual(self.path.read_bytes(), before)
        self.path.unlink()
        self.initialize().reserve()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DELETE FROM reservations")
            connection.commit()
        with self.assertRaises(BudgetError):
            self.ledger.reserve()

    def test_failed_attempt_keeps_reservation_and_never_retries(self):
        fake = FakeProvider(fail=True)
        hosted = PersistentBudgetProvider(fake, self.initialize())
        with self.assertRaises(AgentError):
            hosted.complete([], [])
        self.assertEqual(fake.calls, 1)
        self.assertEqual(self.ledger.snapshot()["reserved_micro_usd"], 7000)

    def test_oversize_or_nonserializable_request_never_reaches_provider(self):
        fake = FakeProvider()
        hosted = PersistentBudgetProvider(fake, self.initialize())
        for messages in ([{"content": "x" * 100001}], [object()]):
            with self.assertRaises(AgentError):
                hosted.complete(messages, [])
        self.assertEqual(fake.calls, 0)
        self.assertEqual(self.ledger.snapshot()["calls_used"], 0)

    def test_exhaustion_and_deleted_volume_fail_before_provider(self):
        fake = FakeProvider()
        hosted = PersistentBudgetProvider(fake, self.initialize())
        hosted.complete([], [])
        hosted.complete([], [])
        with self.assertRaises(AgentError) as caught:
            hosted.complete([], [])
        self.assertEqual(caught.exception.code, "budget_limit")
        self.assertEqual(fake.calls, 2)
        self.assertEqual(hosted.status()["mode"], "budget_exhausted")
        self.path.unlink()
        with self.assertRaises(AgentError) as caught:
            hosted.complete([], [])
        self.assertEqual(caught.exception.code, "budget_unavailable")
        self.assertEqual(hosted.status()["mode"], "budget_unavailable")
        self.assertFalse(self.path.exists())
        self.assertEqual(fake.calls, 2)

    def test_install_hosted_requires_wrapper_and_never_replaces_trial(self):
        hosted = PersistentBudgetProvider(FakeProvider(), self.initialize())
        with patch.object(runtime, "_provider", None):
            with self.assertRaises(ValueError):
                runtime.install_hosted(FakeProvider())
            with self.assertRaises(ValueError):
                runtime.install_hosted(runtime.ConnectionTrial(FakeProvider()))
            runtime.install_hosted(hosted)
            self.assertIs(runtime.current_provider(), hosted)
            with self.assertRaises(ValueError):
                runtime.install_hosted(hosted)
            with self.assertRaises(ValueError):
                runtime.install_trial(runtime.ConnectionTrial(FakeProvider()))
        with patch.object(
            runtime, "_provider", runtime.ConnectionTrial(FakeProvider())
        ):
            with self.assertRaises(ValueError):
                runtime.install_hosted(hosted)


if __name__ == "__main__":
    unittest.main()
