"""Persistent, conservative hosted admission. Reservations are not measured bills.

Normal startup NEVER creates a missing ledger. Explicit initialization is a
separate operator action; deleting/replacing the volume is not a budget reset
mechanism. Policy changes require a separately reviewed migration, not env edits.
Only counters, UTC days and an immutable policy are stored: no queries or keys.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from studio_runtime import NANO_MODEL

MAX_INPUT_BOUND = 100000
# Verified Nano tariff assumption: .06 input / .24 output USD per million.
# <=100000 input-token bound (UTF-8 bytes + framing), <=700 output tokens.
# Fixed conservative reservation must cover the entire admitted envelope.
MIN_RESERVATION_MICRO_USD = 6168
MAX_COUNTER = 1_000_000_000_000


class BudgetError(ValueError):
    """Sanitized local ledger/configuration failure; never contains provider data."""


class BudgetLimit(BudgetError):
    pass


@dataclass(frozen=True)
class BudgetPolicy:
    budget_id: str
    daily_micro_usd: int
    lifetime_micro_usd: int
    daily_calls: int
    lifetime_calls: int
    reservation_micro_usd: int

    def __post_init__(self):
        if not isinstance(self.budget_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{8,64}", self.budget_id
        ):
            raise BudgetError(
                "Configure a stable budget identity of 8 to 64 plain characters."
            )
        for value in (
            self.daily_micro_usd,
            self.lifetime_micro_usd,
            self.daily_calls,
            self.lifetime_calls,
            self.reservation_micro_usd,
        ):
            if type(value) is not int or not 1 <= value <= MAX_COUNTER:
                raise BudgetError(
                    "Budget limits must be explicit positive integer values."
                )
        if (
            self.daily_micro_usd > self.lifetime_micro_usd
            or self.daily_calls > self.lifetime_calls
            or not MIN_RESERVATION_MICRO_USD
            <= self.reservation_micro_usd
            <= self.daily_micro_usd
        ):
            raise BudgetError(
                "Budget limits do not cover the admitted reservation envelope."
            )

    def serialized(self):
        return json.dumps(
            {
                "schema": 1,
                "model": NANO_MODEL,
                "currency": "USD",
                "reservation_basis": "nano-2026-10-25-input100000-output700",
                **asdict(self),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class BudgetLedger:
    def __init__(self, path, policy, *, clock=None):
        self.path = Path(path)
        if (
            not self.path.is_absolute()
            or self.path.is_symlink()
            or not isinstance(policy, BudgetPolicy)
        ):
            raise BudgetError(
                "Use a dedicated absolute budget ledger path and valid policy."
            )
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _today(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise BudgetError("The budget clock must be timezone aware.")
        return now.astimezone(timezone.utc).date().isoformat()

    def initialize(self):
        """EXPLICIT first creation only. Never overwrite or repair an existing file."""
        if not self.path.parent.is_dir():
            raise BudgetError("Create the dedicated persistent data directory first.")
        try:
            with self.path.open("xb"):
                pass
        except OSError:
            raise BudgetError(
                "A new ledger could not be initialized; no existing file was replaced."
            ) from None
        try:
            with self._connect() as connection:
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE budget_policy (
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        policy TEXT NOT NULL,
                        total_calls INTEGER NOT NULL CHECK(typeof(total_calls)='integer' AND total_calls>=0),
                        total_reserved INTEGER NOT NULL CHECK(typeof(total_reserved)='integer' AND total_reserved>=0)
                    );
                    CREATE TABLE reservations (
                        id INTEGER PRIMARY KEY,
                        utc_day TEXT NOT NULL,
                        reserved_micro_usd INTEGER NOT NULL CHECK(typeof(reserved_micro_usd)='integer' AND reserved_micro_usd>0)
                    );
                    CREATE INDEX reservations_day ON reservations(utc_day);
                """)
                connection.execute(
                    "INSERT INTO budget_policy VALUES (1, ?, 0, 0)",
                    (self.policy.serialized(),),
                )
                connection.commit()
            self.snapshot()
        except (OSError, sqlite3.Error, BudgetError):
            # Leave a failed/incomplete initialization visible. No silent repair,
            # unlink, reset or second attempt is performed by normal startup.
            raise BudgetError(
                "The budget ledger initialization did not complete; operator review is required."
            ) from None

    @contextmanager
    def _connect(self, timeout=5):
        if not self.path.is_file() or self.path.is_symlink():
            raise BudgetError(
                "The persistent budget ledger is missing; startup does not create one."
            )
        connection = None
        try:
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=timeout
            )
            connection.execute("PRAGMA busy_timeout=%d" % max(1, int(timeout * 1000)))
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        except sqlite3.Error:
            raise BudgetError("The persistent budget ledger is unavailable.") from None
        finally:
            if connection is not None:
                connection.close()

    def _snapshot(self, connection, today):
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise BudgetError("The budget ledger integrity check failed.")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if tables != {"budget_policy", "reservations"}:
            raise BudgetError("The budget ledger schema is not recognized.")
        policy_rows = connection.execute(
            "SELECT singleton, policy, total_calls, total_reserved FROM budget_policy"
        ).fetchall()
        if len(policy_rows) != 1 or policy_rows[0][:2] != (1, self.policy.serialized()):
            raise BudgetError(
                "The immutable budget policy differs; no limits were changed."
            )
        total_calls, total_reserved = policy_rows[0][2:]
        count, reserved, maximum = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(reserved_micro_usd),0), COALESCE(MAX(id),0) FROM reservations"
        ).fetchone()
        invalid = connection.execute(
            "SELECT COUNT(*) FROM reservations WHERE typeof(reserved_micro_usd)!='integer' OR reserved_micro_usd!=? OR typeof(utc_day)!='text' OR length(utc_day)!=10 OR utc_day NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' OR date(utc_day) IS NULL OR date(utc_day)!=utc_day",
            (self.policy.reservation_micro_usd,),
        ).fetchone()[0]
        if (
            type(total_calls) is not int
            or type(total_reserved) is not int
            or total_calls != count
            or total_calls != maximum
            or total_reserved != reserved
            or invalid
            or not 0 <= total_calls <= self.policy.lifetime_calls
            or not 0 <= total_reserved <= self.policy.lifetime_micro_usd
        ):
            raise BudgetError("The budget ledger counters are inconsistent.")
        daily_calls, daily_reserved = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(reserved_micro_usd),0) FROM reservations WHERE utc_day=?",
            (today,),
        ).fetchone()
        if (
            daily_calls > self.policy.daily_calls
            or daily_reserved > self.policy.daily_micro_usd
        ):
            raise BudgetError("The daily ledger counters are inconsistent.")
        available = (
            total_calls < self.policy.lifetime_calls
            and daily_calls < self.policy.daily_calls
            and total_reserved + self.policy.reservation_micro_usd
            <= self.policy.lifetime_micro_usd
            and daily_reserved + self.policy.reservation_micro_usd
            <= self.policy.daily_micro_usd
        )
        return {
            "available": available,
            "calls_used": total_calls,
            "calls_limit": self.policy.lifetime_calls,
            "daily_calls_used": daily_calls,
            "daily_calls_limit": self.policy.daily_calls,
            "reserved_micro_usd": total_reserved,
            "daily_reserved_micro_usd": daily_reserved,
        }

    def snapshot(self):
        try:
            with self._connect() as connection:
                connection.execute("BEGIN")
                return self._snapshot(connection, self._today())
        except (OSError, sqlite3.Error):
            raise BudgetError(
                "The persistent budget ledger cannot be validated."
            ) from None

    def reserve(self, *, deadline=None):
        try:
            timeout = (
                5
                if deadline is None
                else max(0.001, min(5, deadline - time.monotonic()))
            )
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetError("The request deadline has elapsed.")
            with self._connect(timeout=timeout) as connection:
                connection.execute("BEGIN IMMEDIATE")
                today = self._today()
                state = self._snapshot(connection, today)
                if not state["available"]:
                    raise BudgetLimit(
                        "The configured daily or lifetime admission budget is exhausted."
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    raise BudgetError("The request deadline has elapsed.")
                connection.execute(
                    "INSERT INTO reservations (utc_day,reserved_micro_usd) VALUES (?,?)",
                    (today, self.policy.reservation_micro_usd),
                )
                connection.execute(
                    "UPDATE budget_policy SET total_calls=total_calls+1,total_reserved=total_reserved+? WHERE singleton=1",
                    (self.policy.reservation_micro_usd,),
                )
                connection.commit()
        except (OSError, sqlite3.Error):
            raise BudgetError(
                "A durable budget reservation could not be committed; inference was not started."
            ) from None


class PersistentBudgetProvider:
    """Every model HTTP attempt reserves first; failures/cancellation never refund."""

    def __init__(self, provider, ledger):
        if getattr(provider, "model", None) != NANO_MODEL or not isinstance(
            ledger, BudgetLedger
        ):
            raise BudgetError(
                "Only a fixed Nano provider with a persistent budget ledger is supported."
            )
        ledger.snapshot()
        self._provider, self.ledger = provider, ledger
        self.model, self.mode = provider.model, provider.mode
        self._serial = threading.Lock()

    def status(self):
        try:
            state = self.ledger.snapshot()
            return state | {
                "mode": "available" if state["available"] else "budget_exhausted",
                "provider": "nebius",
                "model": self.model,
            }
        except BudgetError:
            return {
                "available": False,
                "mode": "budget_unavailable",
                "provider": "nebius",
                "model": self.model,
            }

    def complete(self, messages, tools, *, deadline=None, max_tokens=700, trace=None, on_delta=None):
        from solquira_agent import AgentError

        try:
            bound = (
                len(
                    json.dumps(
                        {"messages": messages, "tools": tools}, ensure_ascii=False
                    ).encode("utf-8")
                )
                + 4096
            )
        except (TypeError, ValueError):
            raise AgentError(
                "The request does not fit the hosted admission envelope.",
                code="budget_limit",
            ) from None
        if bound > MAX_INPUT_BOUND:
            raise AgentError(
                "The request exceeds the hosted admission envelope.",
                code="budget_limit",
            )
        if type(max_tokens) is not int or not 1 <= max_tokens <= 700:
            raise AgentError(
                "The generation cap must fit the reserved output envelope.",
                code="budget_limit",
            )
        if deadline is not None and time.monotonic() >= deadline:
            raise AgentError("The request deadline has elapsed.", code="timeout")
        acquired = (
            self._serial.acquire()
            if deadline is None
            else self._serial.acquire(timeout=max(0, deadline - time.monotonic()))
        )
        if not acquired:
            raise AgentError(
                "The request deadline elapsed while waiting.", code="timeout"
            )
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise AgentError("The request deadline has elapsed.", code="timeout")
            try:
                self.ledger.reserve(deadline=deadline)
            except BudgetLimit:
                raise AgentError(
                    "The configured daily or lifetime budget has been reached.",
                    code="budget_limit",
                ) from None
            except BudgetError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise AgentError(
                        "The request deadline has elapsed.", code="timeout"
                    ) from None
                raise AgentError(
                    "The persistent admission budget is unavailable. No model request was started.",
                    code="budget_unavailable",
                ) from None
            # Record a committed reservation even if the deadline closes just after it.
            if trace is not None:
                trace.append("reserved")
            if deadline is not None and time.monotonic() >= deadline:
                raise AgentError("The request deadline has elapsed.", code="timeout")
            import inspect
            supports_delta = "on_delta" in inspect.signature(self._provider.complete).parameters
            result = self._provider.complete(
                messages, tools, deadline=deadline, max_tokens=max_tokens,
                **({"on_delta": on_delta} if on_delta is not None and supports_delta else {})
            )
            if on_delta is not None and not supports_delta and isinstance(result, dict):
                content = result.get("content")
                if isinstance(content, str):
                    on_delta(content)
            if trace is not None:
                trace.append("response_received")
            return result
        finally:
            self._serial.release()
