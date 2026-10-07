"""
T5 durable abuse controls: per-user daily AI-call budget and per-course
regeneration-frequency cap. Both are named, unvalidated defaults (AGENTS.md
§1) -- generous enough not to interfere with normal use, not tuned against
real usage data.

"Token budget" in the mandate's own words is approximated here as a call
count, not a true token count: GenerationGateway (Phase 1) does not surface
per-call token usage anywhere in this codebase, and extending that
interface is a larger change than this phase's abuse controls need to make
first. Documented as a stated simplification, not silently substituted.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.problem_details import ProblemDetailException
from app.core.rate_limit import check_rate_limit
from app.modules.abuse.models import AIUsageDaily
from app.modules.curriculum.models import CourseVersion

DAILY_AI_CALL_BUDGET = 200
COURSE_REGENERATION_DAILY_CAP = 3

# Applies to any single generation-triggering endpoint (tutor ask, lesson
# content, diagnostic generation) -- a per-route burst limit, distinct from
# the daily budget above.
GENERATION_RATE_LIMIT_MAX = 20
GENERATION_RATE_LIMIT_WINDOW_SECONDS = 60.0


def _next_utc_midnight_iso() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.isoformat()


class AbuseControlService:
    def __init__(self, db: Session):
        self.db = db

    def enforce_daily_budget(self, owner_id: int, budget: int = DAILY_AI_CALL_BUDGET) -> None:
        """Atomically reserves one daily AI-call slot or raises a 429.

        The unique owner/date row is inserted idempotently, then a guarded
        UPDATE performs the increment. Concurrent workers therefore cannot
        overwrite one another or exceed the configured budget.
        """
        today = datetime.now(timezone.utc).date()
        dialect = self.db.get_bind().dialect.name
        insert = {
            "postgresql": postgresql_insert,
            "sqlite": sqlite_insert,
        }.get(dialect)
        if insert is None:
            raise RuntimeError(f"Atomic AI budget reservation is unsupported for database dialect {dialect}")

        self.db.execute(
            insert(AIUsageDaily)
            .values(owner_id=owner_id, usage_date=today, call_count=0)
            .on_conflict_do_nothing(index_elements=[AIUsageDaily.owner_id, AIUsageDaily.usage_date])
        )
        reserved = self.db.execute(
            update(AIUsageDaily)
            .where(
                AIUsageDaily.owner_id == owner_id,
                AIUsageDaily.usage_date == today,
                AIUsageDaily.call_count < budget,
            )
            .values(call_count=AIUsageDaily.call_count + 1)
            .returning(AIUsageDaily.call_count)
        ).scalar_one_or_none()
        if reserved is None:
            self.db.rollback()
            row = (
                self.db.query(AIUsageDaily)
                .filter(AIUsageDaily.owner_id == owner_id, AIUsageDaily.usage_date == today)
                .first()
            )
            used = row.call_count if row is not None else 0
            raise ProblemDetailException(
                status_code=429,
                type_="https://neurolearn.internal/problems/daily-budget-exhausted",
                title="Daily AI Budget Exhausted",
                detail=f"You've used your {budget}-request daily AI budget. It resets at midnight UTC.",
                extra={"reset_at": _next_utc_midnight_iso(), "budget": budget, "used": used},
            )
        self.db.commit()

    def enforce_generation_request_controls(self, owner_id: int) -> None:
        """The two request-level checks every generation-triggering
        endpoint should run before calling an LLM: a burst rate limit, then
        the durable daily budget. Call generation_slot(...) separately
        around the actual generation call for the concurrency limit --
        that one needs to wrap the call's duration, not just its start."""
        check_rate_limit(
            f"generation:{owner_id}", GENERATION_RATE_LIMIT_MAX, GENERATION_RATE_LIMIT_WINDOW_SECONDS
        )
        self.enforce_daily_budget(owner_id)

    def enforce_course_regeneration_cap(
        self, course_id: UUID, owner_id: int, cap: int = COURSE_REGENERATION_DAILY_CAP
    ) -> None:
        """Raises a 429 if this course has already been (re)generated `cap`
        times today. Reads CourseVersion.created_at directly -- no separate
        counter table needed, since every regeneration already creates one
        of these rows (CurriculumService.generate_version)."""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count_today = (
            self.db.query(CourseVersion)
            .filter(CourseVersion.course_id == course_id, CourseVersion.owner_id == owner_id)
            .filter(CourseVersion.created_at >= today_start)
            .count()
        )
        if count_today >= cap:
            raise ProblemDetailException(
                status_code=429,
                type_="https://neurolearn.internal/problems/regeneration-cap",
                title="Course Regeneration Limit Reached",
                detail=f"This course has already been generated {count_today} time(s) today (limit {cap}). Try again tomorrow.",
                extra={"reset_at": _next_utc_midnight_iso(), "cap": cap, "used": count_today},
            )
