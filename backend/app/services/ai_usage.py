"""Per-attempt usage at the provider boundary, shared by API and worker calls."""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.ai_limits import AICapacityUnavailable, shared_slot
from app.core.config import settings
from app.modules.abuse.models import AIProviderCall
from app.modules.abuse.service import AbuseControlService
from app.services.generation.gateway import GenerationError


@dataclass(frozen=True)
class UsageScope:
    db: Session
    owner_id: int
    feature: str
    resource_id: UUID | None
    worker: bool
    before_attempt: Callable[[Session], None] | None
    cancel_attempt: Callable[[Session], None] | None
    deadline: float
    operation_id: UUID


_scope: ContextVar[UsageScope | None] = ContextVar("ai_usage_scope", default=None)
_phase: ContextVar[str] = ContextVar("ai_usage_phase", default="generation")


@contextmanager
def ai_usage_scope(db, owner_id, feature, resource_id=None, *, worker=False, before_attempt=None, cancel_attempt=None):
    existing = current_usage_scope()
    duration = settings.WORKER_TASK_SOFT_TIME_LIMIT_SECONDS_V1 if worker else settings.AI_INTERACTIVE_DEADLINE_SECONDS_V1
    deadline = time.monotonic() + duration
    if existing is not None:
        if existing.owner_id != owner_id:
            raise RuntimeError("AI usage cannot change owners inside an operation")
        deadline = min(deadline, existing.deadline)
    scope = UsageScope(db, owner_id, feature, resource_id, worker, before_attempt, cancel_attempt, deadline,
                       existing.operation_id if existing is not None else uuid4())
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


@contextmanager
def ai_phase(phase):
    token = _phase.set(phase)
    try:
        yield
    finally:
        _phase.reset(token)


def current_usage_scope():
    return _scope.get()


def _integer(value):
    return value if type(value) is int and value >= 0 else None


class Attempt:
    def __init__(self):
        self.error_category = None
        self.usage = {}
        self.timeout = None

    def record_usage(self, metadata):
        # Empty protobuf usage messages expose zero-valued getters even when
        # the provider did not report usage. Preserve that distinction.
        pb = getattr(metadata, "_pb", None)
        if pb is not None and type(pb.SerializeToString()) is bytes and not pb.SerializeToString():
            metadata = None
        self.usage = {
            key: _integer(getattr(metadata, provider_key, None))
            for key, provider_key in (
                ("input_tokens", "prompt_token_count"), ("output_tokens", "candidates_token_count"),
                ("cached_input_tokens", "cached_content_token_count"), ("total_tokens", "total_token_count"),
            )
        }


@contextmanager
def provider_attempt(operation, model_id, *, provider="gemini", retry_index=0, input_items=1):
    scope = current_usage_scope()
    attempt = Attempt()
    if scope is None:
        yield attempt
        return
    remaining = scope.deadline - time.monotonic()
    if remaining <= 0:
        raise GenerationError("AI operation deadline exceeded")
    limits = {
        f"ai:slots:{{{provider}}}:all": settings.AI_PROVIDER_MAX_CONCURRENT_V1,
        f"ai:slots:{{{provider}}}:owner:{scope.owner_id}": settings.AI_PROVIDER_MAX_CONCURRENT_PER_USER_V1,
    }
    if scope.worker:
        limits[f"ai:slots:{{{provider}}}:workers"] = settings.AI_WORKER_MAX_CONCURRENT_V1
    timeout = (settings.GEMINI_GENERATION_TIMEOUT_SECONDS_V1 if operation == "generation"
               else settings.GEMINI_EMBEDDING_TIMEOUT_SECONDS_V1)
    waiting_started = time.monotonic()
    wait = settings.AI_WORKER_SLOT_WAIT_SECONDS_V1 if scope.worker else settings.AI_PROVIDER_SLOT_WAIT_SECONDS_V1
    try:
        reservation_timeout = settings.AI_ACCOUNTING_RESERVATION_TIMEOUT_SECONDS_V1
        with shared_slot(limits, ttl_seconds=timeout + reservation_timeout + 15, wait_seconds=min(wait, remaining)):
            capacity_wait_ms = round((time.monotonic() - waiting_started) * 1000)
            slot_deadline = time.monotonic() + timeout + reservation_timeout + 15
            remaining = scope.deadline - time.monotonic()
            if remaining <= 0:
                raise GenerationError("AI operation deadline exceeded")
            attempt.timeout = min(timeout, remaining)
            call_id = uuid4()
            bind = scope.db.get_bind()
            # Each attempt owns its session. Parallel validation never shares the
            # domain Session, and a later artifact rollback cannot erase usage.
            try:
                with Session(bind=bind) as db:
                    if bind.dialect.name == "postgresql":
                        milliseconds = max(1, int(min(remaining, reservation_timeout) * 1000))
                        db.execute(text("SELECT set_config('lock_timeout', :value, true), set_config('statement_timeout', :value, true)"),
                                   {"value": f"{milliseconds}ms"})
                    if scope.before_attempt is not None:
                        scope.before_attempt(db)
                    reserved_at = datetime.now(timezone.utc)
                    if operation == "generation":
                        reserved_date = AbuseControlService(db).enforce_daily_budget(
                            scope.owner_id, commit=False, usage_date=reserved_at.date())
                    remaining = min(scope.deadline, slot_deadline - 1) - time.monotonic()
                    if remaining <= 0:
                        raise GenerationError("AI operation deadline exceeded before dispatch")
                    db.add(AIProviderCall(
                        id=call_id, owner_id=scope.owner_id, operation_id=scope.operation_id, resource_id=scope.resource_id,
                        feature=scope.feature,
                        phase="embedding" if operation == "embedding" and _phase.get() == "generation" else _phase.get(),
                        provider=provider, model_id=model_id, operation=operation, retry_index=retry_index,
                        input_items=input_items, status="STARTED", started_at=reserved_at,
                        capacity_wait_ms=capacity_wait_ms,
                    ))
                    db.commit()
            except SQLAlchemyError as exc:
                raise GenerationError("AI accounting reservation unavailable") from exc
            remaining = min(scope.deadline, slot_deadline - 1) - time.monotonic()
            if remaining <= 0:
                # The reservation committed, but slow commit/connection work
                # consumed the dispatch deadline. It must not count as a sent call.
                with Session(bind=bind) as db:
                    if bind.dialect.name == "postgresql":
                        milliseconds = max(1, int(reservation_timeout * 1000))
                        db.execute(text("SELECT set_config('lock_timeout', :value, true), set_config('statement_timeout', :value, true)"),
                                   {"value": f"{milliseconds}ms"})
                    # Match reservation's lock order: resource, owner budget,
                    # then its ledger record. Parallel callbacks cannot invert it.
                    if scope.cancel_attempt is not None:
                        scope.cancel_attempt(db)
                    if operation == "generation":
                        from app.modules.abuse.models import AIUsageDaily
                        db.execute(update(AIUsageDaily).where(AIUsageDaily.owner_id == scope.owner_id,
                                   AIUsageDaily.usage_date == reserved_date).values(call_count=AIUsageDaily.call_count - 1))
                    db.execute(update(AIProviderCall).where(AIProviderCall.id == call_id).values(
                        status="CANCELLED", finished_at=datetime.now(timezone.utc), elapsed_ms=0,
                        error_category="DEADLINE_BEFORE_DISPATCH",
                    ))
                    db.commit()
                raise GenerationError("AI operation deadline exceeded before dispatch")
            attempt.timeout = min(timeout, remaining)
            started = time.monotonic()
            status = "SUCCEEDED"
            try:
                yield attempt
            except BaseException:
                status = "FAILED"
                raise
            finally:
                with Session(bind=bind) as db:
                    db.execute(update(AIProviderCall).where(AIProviderCall.id == call_id).values(
                        status="FAILED" if attempt.error_category else status, finished_at=datetime.now(timezone.utc),
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                        error_category=attempt.error_category or ("PROVIDER_ERROR" if status == "FAILED" else None),
                        **attempt.usage,
                    ))
                    db.commit()
    except AICapacityUnavailable as exc:
        raise GenerationError("AI provider capacity unavailable") from exc
