"""
In-memory per-user rate limiting and generation-concurrency limiting (T5).

RESIDUAL RISK, STATED PLAINLY: this is process-local state -- a
sliding-window dict, not Redis. It is correct for exactly one backend
process (this sprint's deployment: a single FastAPI process, no worker
pool). Running multiple backend replicas would let each replica enforce its
own independent limit, so the effective limit becomes
(configured limit) x (replica count). Documented here and in
docs/SECURITY.md rather than silently assumed away; a production multi-
replica deployment needs a shared store (Redis) for this to hold.
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
        with _lock:
            if _active_generations[self.key] >= self.max_concurrent:
                raise ConcurrencyLimitExceeded(self.max_concurrent)
            _active_generations[self.key] += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with _lock:
            _active_generations[self.key] = max(0, _active_generations[self.key] - 1)
        return False
