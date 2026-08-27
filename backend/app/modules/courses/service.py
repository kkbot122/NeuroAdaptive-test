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

    def get_owned(self, course_id: UUID, owner_id: int) -> Course:
        """
        The single accessor every other module must use to resolve a course.

        The owner filter is part of the query, so there is no window in which
        an unowned Course object exists in memory and could be returned by
        mistake.
        """
        course = (
            self.db.query(Course)
            .filter(Course.id == course_id, Course.owner_id == owner_id)
            .first()
        )
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
        course = self.get_owned(course_id, owner_id)
        self.db.delete(course)
        self.db.commit()

    def finalize_sources(self, course_id: UUID, owner_id: int) -> Course:
        """
        Close the source set. Documents become immutable (frozen-scope.md:
        a learner must create a new course to use different material).
        """
        from sqlalchemy.sql import func

        course = self.get_owned(course_id, owner_id)
        if course.sources_are_immutable:
            raise SourcesImmutable(str(course_id))
        course.sources_finalized_at = func.now()
        course.status = CourseStatus.PROCESSING.value
        self.db.commit()
        self.db.refresh(course)
        return course
