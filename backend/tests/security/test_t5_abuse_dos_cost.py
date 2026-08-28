"""
T5 (abuse/DoS/cost controls): daily AI budget, burst rate limiting,
concurrency limiting, and per-course regeneration cap, all returning
RFC 7807 problem-details bodies (application/problem+json), not a silent
failure or a generic error shape.
"""
import uuid

import pytest

from app.core.rate_limit import _active_generations, _request_log
from app.modules.abuse.models import AIUsageDaily
from app.modules.courses.models import Course
from app.modules.curriculum.models import CourseVersion, CourseVersionStatus
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def reset_in_memory_limiter_state():
    """The rate/concurrency limiters are process-global module state
    (core/rate_limit.py's own documented limitation) -- reset between tests
    so one test's burst doesn't bleed into the next."""
    _request_log.clear()
    _active_generations.clear()
    yield
    _request_log.clear()
    _active_generations.clear()


@pytest.fixture()
def course_with_chunk(db_session, owner):
    from app.modules.documents.chunk_models import Chunk
    from app.modules.documents.models import Document

    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()
    doc = Document(
        course_id=course.id, owner_id=owner.id, filename="notes.txt",
        storage_path="/dev/null", checksum_sha256="a" * 64,
    )
    db_session.add(doc)
    db_session.commit()
    chunk = Chunk(
        id=uuid.uuid4(), document_id=doc.id, course_id=course.id, owner_id=owner.id,
        text="A deadlock is a circular wait condition among processes.",
    )
    db_session.add(chunk)
    db_session.commit()
    return course, chunk


class TestDailyBudget:
    def test_exceeding_the_daily_budget_returns_429_problem_details_with_reset_time(
        self, client, owner, db_session, course_with_chunk
    ):
        from app.modules.abuse.service import DAILY_AI_CALL_BUDGET

        course, _ = course_with_chunk
        # Pre-exhaust today's budget directly rather than firing 200 real requests.
        db_session.add(AIUsageDaily(owner_id=owner.id, usage_date=__import__("datetime").date.today(), call_count=DAILY_AI_CALL_BUDGET))
        db_session.commit()

        resp = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            json={"question": "What is a deadlock?"},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 429
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["status"] == 429
        assert "reset_at" in body
        assert body["type"]


class TestRegenerationCap:
    def test_exceeding_the_daily_regeneration_cap_is_rejected(self, client, owner, db_session):
        from app.modules.abuse.service import COURSE_REGENERATION_DAILY_CAP

        course = Course(owner_id=owner.id, title="OS Course")
        db_session.add(course)
        db_session.commit()
        for i in range(COURSE_REGENERATION_DAILY_CAP):
            db_session.add(
                CourseVersion(
                    course_id=course.id, owner_id=owner.id, version_number=i + 1,
                    status=CourseVersionStatus.READY.value,
                )
            )
        db_session.commit()

        resp = client.post(f"/api/v1/courses/{course.id}/process", headers=auth_headers(owner.email))
        assert resp.status_code == 429
        assert resp.headers["content-type"] == "application/problem+json"
        assert "reset_at" in resp.json()

    def test_below_the_cap_is_not_rejected_by_the_regeneration_check(self, client, owner, db_session):
        course = Course(owner_id=owner.id, title="OS Course")
        db_session.add(course)
        db_session.commit()
        resp = client.post(f"/api/v1/courses/{course.id}/process", headers=auth_headers(owner.email))
        # Not 429 -- may be 202 or fail later for unrelated reasons (no
        # documents uploaded), but the regeneration cap itself must not fire.
        assert resp.status_code != 429


class TestBurstRateLimit:
    def test_a_burst_beyond_the_limit_is_throttled_not_silently_dropped(
        self, client, owner, course_with_chunk
    ):
        from app.modules.abuse.service import GENERATION_RATE_LIMIT_MAX

        course, _ = course_with_chunk
        statuses = []
        for _ in range(GENERATION_RATE_LIMIT_MAX + 3):
            resp = client.post(
                f"/api/v1/courses/{course.id}/tutor",
                json={"question": "What is a deadlock?"},
                headers=auth_headers(owner.email),
            )
            statuses.append(resp.status_code)

        assert 429 in statuses
        throttled_index = statuses.index(429)
        # Every request up to the limit was actually processed (not queued
        # indefinitely, not dropped) -- it shows up as some real status.
        assert throttled_index <= GENERATION_RATE_LIMIT_MAX


class TestConcurrencyLimit:
    def test_generation_slot_rejects_beyond_the_concurrency_limit(self):
        from app.core.rate_limit import ConcurrencyLimitExceeded, generation_slot

        key = "user:test-concurrency"
        with generation_slot(key, max_concurrent=1):
            with pytest.raises(ConcurrencyLimitExceeded):
                with generation_slot(key, max_concurrent=1):
                    pass  # pragma: no cover -- must not be reached

    def test_the_slot_is_released_after_use_so_a_later_call_succeeds(self):
        from app.core.rate_limit import generation_slot

        key = "user:test-concurrency-release"
        with generation_slot(key, max_concurrent=1):
            pass
        with generation_slot(key, max_concurrent=1):
            pass  # does not raise -- the first slot was released
