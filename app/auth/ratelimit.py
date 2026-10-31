"""In-memory login throttling: N failures for the same (username, IP) lock it for a while.

Waitress runs a single process with threads, so a lock-protected dict is enough.
"""
from __future__ import annotations

import threading
import time


class LoginLimiter:
    def __init__(self, max_attempts: int = 5, lock_seconds: int = 300, clock=time.monotonic):
        self.max_attempts = max_attempts
        self.lock_seconds = lock_seconds
        self.clock = clock
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, ip: str) -> tuple[str, str]:
        return username.strip().casefold(), ip or "-"

    def _recent(self, key, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.lock_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, username: str, ip: str) -> int:
        """Seconds until the next attempt is allowed (0 = allowed now)."""
        with self._lock:
            now = self.clock()
            recent = self._recent(self._key(username, ip), now)
            if len(recent) < self.max_attempts:
                return 0
            return max(1, int(recent[-1] + self.lock_seconds - now + 0.999))

    def failure(self, username: str, ip: str) -> None:
        with self._lock:
            key = self._key(username, ip)
            now = self.clock()
            self._recent(key, now)
            self._failures.setdefault(key, []).append(now)

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username, ip), None)
