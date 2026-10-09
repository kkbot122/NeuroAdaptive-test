"""Request limits. AI generation slots use shared Redis coordination in production.

Other legacy/IP limiters remain process-local. Offline tests explicitly disable
shared AI limits; production defaults to fail-closed Redis admission.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from app.core.problem_details import ProblemDetailException

_lock = threading.Lock()
_request_log: Dict[str, Deque[float]] = defaultdict(deque)
_active_generations: Dict[str, int] = defaultdict(int)


def check_rate_limit(key: str, max_requests: int, window_seconds: float) -> None:
    """Sliding-window limiter. `key` is caller-chosen -- typically
    f"user:{owner_id}" or f"ip:{client_ip}" for an unauthenticated route."""
    now = time.monotonic()
    with _lock:
        log = _request_log[key]
        cutoff = now - window_seconds
        while log and log[0] < cutoff:
            log.popleft()
        if len(log) >= max_requests:
            retry_after = window_seconds - (now - log[0])
            raise ProblemDetailException(
                status_code=429,
                type_="https://neurolearn.internal/problems/rate-limited",
                title="Too Many Requests",
                detail=f"You're sending requests faster than the {max_requests}-per-{int(window_seconds)}s limit. Wait a moment and try again.",
                extra={"retry_after_seconds": round(max(retry_after, 0), 1)},
            )
        log.append(now)


class ConcurrencyLimitExceeded(ProblemDetailException):
    def __init__(self, max_concurrent: int):
        super().__init__(
            status_code=429,
            type_="https://neurolearn.internal/problems/concurrency-limited",
            title="Too Many Concurrent Generations",
            detail=f"You already have {max_concurrent} generation request(s) in flight. Wait for one to finish before starting another.",
        )


class generation_slot:
    """Context manager bounding how many AI generation calls one user can
    have in flight at once. Raises before entering if the caller is already
    at the limit; always releases its slot on exit, success or failure."""

    def __init__(self, key: str, max_concurrent: int):
        self.key = key
        self.max_concurrent = max_concurrent

    def __enter__(self):
        from app.core.config import settings
        if settings.AI_SHARED_LIMITS_ENABLED:
            from app.core.ai_limits import AICapacityUnavailable, shared_slot
            self._shared = shared_slot({f"ai:requests:{self.key}": self.max_concurrent},
                                       ttl_seconds=settings.AI_INTERACTIVE_DEADLINE_SECONDS_V1 + 15)
            try:
                self._shared.__enter__()
            except AICapacityUnavailable as exc:
                raise ConcurrencyLimitExceeded(self.max_concurrent) from exc
            return self
        with _lock:
            if _active_generations[self.key] >= self.max_concurrent:
                raise ConcurrencyLimitExceeded(self.max_concurrent)
            _active_generations[self.key] += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "_shared"):
            return self._shared.__exit__(exc_type, exc_val, exc_tb)
        with _lock:
            _active_generations[self.key] = max(0, _active_generations[self.key] - 1)
        return False
