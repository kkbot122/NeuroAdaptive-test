"""
Course domain service.

Ownership is enforced *here*, not in the router. Every read and write is
filtered by owner_id inside the query itself, so a route that forgets to check
still cannot return another learner's course. This is the non-negotiable from
the build mandate and AGENTS.md §7.

Absence and non-ownership are deliberately indistinguishable: both raise
CourseNotFound, which the router renders as 404. Returning 403 for a course
that exists but belongs to someone else would confirm its existence.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.models import Course, CourseStatus


class CourseNotFound(Exception):
    """Raised when a course does not exist *or* is not owned by the caller."""


class SourcesImmutable(Exception):
    """Raised when altering a source set that has already been finalized."""


class CourseService:
    def __init__(self, db: Session):
        self.db = db

    # ── reads ────────────────────────────────────────────────────────────────

    def list_for_owner(self, owner_id: int) -> List[Course]:
        return (
            self.db.query(Course)
            .filter(Course.owner_id == owner_id)
            .order_by(Course.created_at.desc())
            .all()
        )

    def out(self, course: Course) -> dict:
        """Return the lifecycle summary used by both detail and dashboard.

        This deliberately computes only safe, course-scoped summaries; raw
        job errors and other users' rows never enter a course listing.
        """
        from app.modules.curriculum.models import CourseVersion
        from app.modules.documents.models import Document
        from app.modules.jobs.models import ProcessingJob

        latest_job = (
            self.db.query(ProcessingJob)
            .filter(ProcessingJob.course_id == course.id, ProcessingJob.owner_id == course.owner_id)
            .order_by(ProcessingJob.created_at.desc())
            .first()
        )
        review_version = (
            self.db.query(CourseVersion)
            .filter(CourseVersion.course_id == course.id, CourseVersion.owner_id == course.owner_id)
            .order_by(CourseVersion.version_number.desc())
            .first()
        )
        return {
            "id": course.id,
            "title": course.title,
            "goal": course.goal,
            "starting_confidence": course.starting_confidence,
            "status": course.status,
            "sources_finalized_at": course.sources_finalized_at,
            "source_revision": course.source_revision,
            "source_count": self.db.query(Document).filter(Document.course_id == course.id).count(),
            "latest_job": None if latest_job is None else {
                "id": str(latest_job.id),
                "status": latest_job.status,
                "current_stage": latest_job.current_stage,
                "error_category": latest_job.error_category,
            },
            "latest_review_version_id": None if review_version is None else review_version.id,
            "active_version_id": course.active_version_id,
            "created_at": course.created_at,
        }

    def get_owned(self, course_id: UUID, owner_id: int, *, lock: bool = False) -> Course:
        """
        The single accessor every other module must use to resolve a course.

        The owner filter is part of the query, so there is no window in which
        an unowned Course object exists in memory and could be returned by
        mistake.
        """
        query = self.db.query(Course).filter(Course.id == course_id, Course.owner_id == owner_id)
        if lock:
            # Re-read under the row lock even if an earlier request step
            # cached this course before another transaction finalized it.
            query = query.populate_existing().with_for_update()
        course = query.first()
        if course is None:
            raise CourseNotFound(str(course_id))
        return course

    # ── writes ───────────────────────────────────────────────────────────────

    def create(
        self,
        owner_id: int,
        title: str,
        goal: Optional[str] = None,
        starting_confidence: Optional[int] = None,
    ) -> Course:
        course = Course(
            owner_id=owner_id,
            title=title.strip(),
            goal=goal,
            starting_confidence=starting_confidence,
            status=CourseStatus.DRAFT.value,
        )
        self.db.add(course)
        self.db.commit()
        self.db.refresh(course)
        return course

    def update(
        self,
        course_id: UUID,
        owner_id: int,
        title: Optional[str] = None,
        goal: Optional[str] = None,
        starting_confidence: Optional[int] = None,
    ) -> Course:
        course = self.get_owned(course_id, owner_id)
        if title is not None:
            course.title = title.strip()
        if goal is not None:
            course.goal = goal
        if starting_confidence is not None:
            course.starting_confidence = starting_confidence
        self.db.commit()
        self.db.refresh(course)
        return course

    def delete(self, course_id: UUID, owner_id: int) -> None:
        self.get_owned(course_id, owner_id)
        # Course removal uses the same ordered retention cleanup as account
        # deletion, including saved answers and grading review history.
        from app.modules.privacy.service import PrivacyService

        PrivacyService(self.db).delete_owned_course(course_id, owner_id)

    def finalize_sources(self, course_id: UUID, owner_id: int) -> Course:
        """
        Record that preparation began. The owner can still revise sources
        until explicit publication; revisions invalidate the current outline.
        """
        from sqlalchemy.sql import func

        course = self.get_owned(course_id, owner_id, lock=True)
        if course.sources_are_finalized:
            raise SourcesImmutable(str(course_id))
        course.sources_finalized_at = func.now()
        course.status = CourseStatus.PROCESSING.value
        self.db.commit()
        self.db.refresh(course)
        return course
