"""Durable generation-attempt allowance and shared AI request admission.

The 200-attempt UTC budget is a generation policy. Embedding calls and returned
provider token metadata are itemized separately; token/cost estimates never
replace the atomic admission counter.
"""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.problem_details import ProblemDetailException
from app.core.rate_limit import check_rate_limit
from app.core.config import settings
from app.core.ai_limits import AICapacityUnavailable, check_shared_burst
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

    def _budget_exempt(self, owner_id: int) -> bool:
        emails = {email.strip().casefold() for email in settings.AI_BUDGET_EXEMPT_EMAILS.split(",") if email.strip()}
        if not emails:
            return False
        email = self.db.execute(text("SELECT email FROM users WHERE id = :owner_id"), {"owner_id": owner_id}).scalar_one_or_none()
        return email is not None and email.strip().casefold() in emails

    def _raise_budget_exhausted(self, budget, used):
        raise ProblemDetailException(
            status_code=429, type_="https://neurolearn.internal/problems/daily-budget-exhausted",
            title="Daily AI Budget Exhausted",
            detail=f"You've used your {budget}-attempt daily generation budget. It resets at midnight UTC.",
            extra={"reset_at": _next_utc_midnight_iso(), "budget": budget, "used": used},
        )

    def ensure_daily_budget_available(self, owner_id: int, budget: int = DAILY_AI_CALL_BUDGET) -> None:
        row = self.db.query(AIUsageDaily).filter_by(owner_id=owner_id, usage_date=datetime.now(timezone.utc).date()).first()
        if row is not None and row.call_count >= budget and not self._budget_exempt(owner_id):
            self._raise_budget_exhausted(budget, row.call_count)

    def enforce_daily_budget(self, owner_id: int, budget: int = DAILY_AI_CALL_BUDGET, *, commit: bool = True,
                             usage_date: date | None = None) -> date:
        """Atomically reserves one daily AI-call slot or raises a 429.

        The unique owner/date row is inserted idempotently, then an UPDATE
        performs the increment. Concurrent workers therefore cannot overwrite
        one another. Explicitly configured developer accounts still increment
        the counter, but do not have the daily threshold enforced.
        """
        budget_exempt = self._budget_exempt(owner_id)

        today = usage_date or datetime.now(timezone.utc).date()
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
        reservation_filters = [
            AIUsageDaily.owner_id == owner_id,
            AIUsageDaily.usage_date == today,
        ]
        if not budget_exempt:
            reservation_filters.append(AIUsageDaily.call_count < budget)
        reserved = self.db.execute(
            update(AIUsageDaily)
            .where(*reservation_filters)
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
            self._raise_budget_exhausted(budget, used)
        if commit:
            self.db.commit()
        return today

    def enforce_generation_request_controls(self, owner_id: int) -> None:
        """Check request admission; outbound attempts reserve their own budget."""
        if settings.AI_SHARED_LIMITS_ENABLED:
            try:
                allowed = check_shared_burst(f"generation:{owner_id}", GENERATION_RATE_LIMIT_MAX, GENERATION_RATE_LIMIT_WINDOW_SECONDS)
            except AICapacityUnavailable as exc:
                raise ProblemDetailException(status_code=503,
                    type_="https://neurolearn.internal/problems/ai-coordinator-unavailable",
                    title="AI capacity unavailable", detail="AI request capacity could not be checked. Try again shortly.") from exc
            if not allowed:
                raise ProblemDetailException(status_code=429, type_="https://neurolearn.internal/problems/rate-limited",
                    title="Too Many Requests", detail="Too many AI requests. Wait a moment and try again.",
                    extra={"retry_after_seconds": GENERATION_RATE_LIMIT_WINDOW_SECONDS})
        else:
            check_rate_limit(f"generation:{owner_id}", GENERATION_RATE_LIMIT_MAX, GENERATION_RATE_LIMIT_WINDOW_SECONDS)
        self.ensure_daily_budget_available(owner_id)

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
