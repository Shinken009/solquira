"""Sliding-window request admission, ported from God's Eye View sources/rateLimit.js.

Per-key and global backstop with a hard key cap so a key-rotating caller cannot
grow the map without bound. Reads limits from the environment on every call so
tests and operators can retune without reimporting the module.
"""

from __future__ import annotations

import os
import time

MAX_KEYS = 2000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class RateLimiter:
    def __init__(self, window_seconds: float | None = None) -> None:
        self.window_seconds = (
            window_seconds
            if window_seconds is not None
            else float(os.environ.get("SOLQUIRA_INGEST_WINDOW_S", "60"))
        )
        self._hits: dict[str, list[float]] = {}
        self._global: list[float] = []

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). Limits are read per call."""
        max_per_key = _env_int("SOLQUIRA_INGEST_MAX", 60)
        global_max = _env_int("SOLQUIRA_INGEST_GLOBAL_MAX", 300)
        now = time.monotonic()
        window = self.window_seconds
        self._global = [stamp for stamp in self._global if now - stamp < window]
        if global_max and len(self._global) >= global_max:
            return False, self._retry_after(now)
        recent = [stamp for stamp in self._hits.get(key, []) if now - stamp < window]
        if len(recent) >= max_per_key:
            self._hits[key] = recent
            return False, self._retry_after(now)
        recent.append(now)
        self._hits[key] = recent
        self._global.append(now)
        if len(self._hits) > MAX_KEYS:
            for older_key in list(self._hits)[: len(self._hits) - MAX_KEYS]:
                self._hits.pop(older_key, None)
        if len(self._hits) > 256:
            for older_key, stamps in list(self._hits.items()):
                if not stamps or now - stamps[-1] > window:
                    self._hits.pop(older_key, None)
        return True, 0

    def _retry_after(self, now: float) -> int:
        oldest = min(
            (stamp for stamps in self._hits.values() for stamp in stamps), default=now
        )
        return max(1, int(self.window_seconds - (now - oldest)) + 1)

    def clear(self) -> None:
        self._hits.clear()
        self._global.clear()


INGEST_LIMITER = RateLimiter()
