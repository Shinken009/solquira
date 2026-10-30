"""Explicit process-local provider injection. Importing never activates inference."""

from __future__ import annotations
import json
import threading
import time

_provider = None
_lock = threading.Lock()
NANO_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


class ConnectionTrial:
    """One bounded, single-process Nano trial. Failed attempts consume admission."""

    def __init__(self, provider, *, calls=5, seconds=1200):
        if (
            provider.model != NANO_MODEL
            or type(calls) is not int
            or type(seconds) is not int
            or not 1 <= calls <= 5
            or not 1 <= seconds <= 1200
        ):
            raise ValueError(
                "Only the catalogue-verified bounded Nano trial is supported."
            )
        self._provider = provider
        self.model, self.mode = provider.model, provider.mode
        self._lock = threading.Lock()
        self._serial = threading.Lock()
        self._expires = time.monotonic() + seconds
        self._limit, self._calls, self._reserved = calls, 0, 0.0
        self._receipts = []

    def status(self):
        with self._lock:
            available = self._calls < self._limit and time.monotonic() < self._expires
            return {
                "available": available,
                "mode": "available" if available else "trial_finished",
                "provider": "nebius",
                "model": self.model,
                "calls_used": self._calls,
                "calls_limit": self._limit,
            }

    def receipts(self):
        with self._lock:
            return [dict(row) for row in self._receipts]

    def complete(self, messages, tools, *, deadline=None, max_tokens=700):
        with self._serial:
            return self._complete(
                messages, tools, deadline=deadline, max_tokens=max_tokens
            )

    def _complete(self, messages, tools, *, deadline=None, max_tokens=700):
        from solquira_agent import AgentError

        # Verified public Nano pricing: USD .06 input / .24 output per million.
        # UTF-8 bytes + framing reserve is deliberately above normal token counts.
        bound = (
            len(
                json.dumps(
                    {"messages": messages, "tools": tools}, ensure_ascii=False
                ).encode("utf-8")
            )
            + 4096
        )
        estimate = (bound * 0.06 + 700 * 0.24) / 1_000_000
        with self._lock:
            if (
                self._calls >= self._limit
                or time.monotonic() >= self._expires
                or bound > 100000
                or self._reserved + estimate > 1
            ):
                raise AgentError(
                    "The authorized connection trial has reached its limit.",
                    code="trial_limit",
                )
            self._calls += 1
            self._reserved += estimate
        started = time.monotonic()
        try:
            result = self._provider.complete(
                messages, tools, deadline=deadline, max_tokens=max_tokens
            )
        except AgentError as exc:
            with self._lock:
                self._receipts.append(
                    {
                        "outcome": exc.code,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                    }
                )
            raise
        with self._lock:
            self._receipts.append(
                {
                    "outcome": "response_received",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "usage": dict(getattr(self._provider, "last_usage", {})),
                    "http_status": getattr(self._provider, "last_http_status", None),
                }
            )
        return result


def install_trial(provider):
    global _provider
    if not isinstance(provider, ConnectionTrial):
        raise ValueError("A bounded explicit connection trial is required.")
    with _lock:
        if _provider is not None:
            raise ValueError(
                "A trial is already configured; restart explicitly to change it."
            )
        _provider = provider


def install_hosted(provider):
    """Explicit hosted admission only; never infer credentials or reset a trial."""
    global _provider
    from studio_budget import PersistentBudgetProvider

    if not isinstance(provider, PersistentBudgetProvider):
        raise ValueError("A persistent-budget hosted provider is required.")
    with _lock:
        if _provider is not None:
            raise ValueError(
                "A provider is already configured; restart explicitly to change it."
            )
        _provider = provider


def current_provider():
    with _lock:
        return _provider


def status():
    provider = current_provider()
    return (
        provider.status()
        if provider is not None
        else {
            "available": False,
            "mode": "disabled",
            "provider": "nebius",
            "model": None,
        }
    )
