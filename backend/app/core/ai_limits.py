"""Atomic shared AI limits. Lease tokens expire after bounded provider deadlines."""
import logging
import time
from contextlib import contextmanager
from uuid import uuid4

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_ACQUIRE = """
for _, key in ipairs(KEYS) do redis.call('ZREMRANGEBYSCORE', key, '-inf', ARGV[1]) end
for i, key in ipairs(KEYS) do
  if redis.call('ZCARD', key) >= tonumber(ARGV[3 + i]) then return 0 end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, ARGV[2], ARGV[3])
  local last = redis.call('ZRANGE', key, -1, -1, 'WITHSCORES')
  redis.call('PEXPIREAT', key, tonumber(last[2]))
end
return 1
"""
_RELEASE = "for _, key in ipairs(KEYS) do redis.call('ZREM', key, ARGV[1]) end return 1"
_BURST = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1] - ARGV[2])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""


class AICapacityUnavailable(Exception):
    """No shared capacity or the coordinator is unavailable; no request was sent."""


def _client():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=1, socket_timeout=1)


def check_shared_burst(key, maximum, window_seconds):
    try:
        with _client() as client:
            return bool(client.eval(_BURST, 1, f"ai:burst:{key}", int(time.time() * 1000),
                                    int(window_seconds * 1000), maximum, uuid4().hex))
    except redis.RedisError as exc:
        raise AICapacityUnavailable from exc


@contextmanager
def shared_slot(limits: dict[str, int], *, ttl_seconds: float, wait_seconds: float = 0):
    if not settings.AI_SHARED_LIMITS_ENABLED:
        yield
        return
    token = uuid4().hex
    keys = list(limits)
    deadline = time.monotonic() + wait_seconds
    try:
        client = _client()
        with client:
            while True:
                now = int(time.time() * 1000)
                if client.eval(_ACQUIRE, len(keys), *keys, now, now + int(ttl_seconds * 1000), token, *limits.values()):
                    break
                if time.monotonic() >= deadline:
                    raise AICapacityUnavailable
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            try:
                yield
            finally:
                try:
                    client.eval(_RELEASE, len(keys), *keys, token)
                except redis.RedisError:
                    # Never replace a completed provider result with a release
                    # failure; the deadline-bounded token expires automatically.
                    logger.warning("AI slot release unavailable; lease will expire")
    except redis.RedisError as exc:
        raise AICapacityUnavailable from exc
