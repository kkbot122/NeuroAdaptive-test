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
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from sqlalchemy.orm import Session

from app.modules.courses.models import Course, CourseStatus, CourseSubject
from app.modules.courses.p8_models import CrossCourseConceptMatch


class CourseNotFound(Exception):
    """Raised when a course does not exist *or* is not owned by the caller."""


class SourcesImmutable(Exception):
    """Raised when altering a source set that has already been finalized."""


class RelationshipInvalid(Exception):
    """Subject or pinned earlier-course relationship is invalid."""


class RelationshipImmutable(Exception):
    """P8 relationships are frozen once the current course is published."""


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

    def list_subjects_for_owner(self, owner_id: int) -> list[CourseSubject]:
        return (
            self.db.query(CourseSubject)
            .filter(CourseSubject.owner_id == owner_id)
            .order_by(CourseSubject.normalized_name, CourseSubject.id)
            .all()
        )

    def create_subject(self, owner_id: int, name: str) -> CourseSubject:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise RelationshipInvalid("Subject name is required")
        normalized = cleaned.casefold()
        subject, created = self._get_or_create_subject(owner_id, cleaned, normalized)
        if created:
            self.db.commit()
            self.db.refresh(subject)
        return subject

    def _find_subject(self, owner_id: int, normalized_name: str) -> CourseSubject | None:
        return self.db.query(CourseSubject).filter_by(
            owner_id=owner_id, normalized_name=normalized_name
        ).first()

    def _get_or_create_subject(
        self, owner_id: int, cleaned_name: str, normalized_name: str
    ) -> tuple[CourseSubject, bool]:
        subject = self._find_subject(owner_id, normalized_name)
        if subject is not None:
            return subject, False

        try:
            # Keep a concurrent unique-key collision inside a savepoint so
            # the enclosing course-creation transaction remains usable.
            with self.db.begin_nested():
                subject = CourseSubject(
                    owner_id=owner_id, name=cleaned_name, normalized_name=normalized_name
                )
                self.db.add(subject)
                self.db.flush()
        except IntegrityError:
            subject = self._find_subject(owner_id, normalized_name)
            if subject is None:
                raise
            return subject, False
        return subject, True

    def _resolve_subject(self, owner_id: int, subject_id: UUID | None, new_subject_name: str | None = None):
        if subject_id is not None and new_subject_name is not None:
            raise RelationshipInvalid("Choose an existing subject or create a new one, not both")
        if new_subject_name is not None:
            cleaned = " ".join(new_subject_name.split())
            if not cleaned:
                raise RelationshipInvalid("Subject name is required")
            normalized = cleaned.casefold()
            subject, _created = self._get_or_create_subject(owner_id, cleaned, normalized)
            return subject
        if subject_id is None:
            return None
        subject = self.db.query(CourseSubject).filter_by(id=subject_id, owner_id=owner_id).first()
        if subject is None:
            raise RelationshipInvalid("Subject not found")
        return subject

    def _resolve_link(self, owner_id: int, linked_course_id: UUID | None):
        if linked_course_id is None:
            return None, None
        linked = self.db.query(Course).filter_by(id=linked_course_id, owner_id=owner_id).first()
        if linked is None or linked.status != CourseStatus.PUBLISHED.value or linked.active_version_id is None:
            raise RelationshipInvalid("Choose an owned, published earlier course")
        from app.modules.curriculum.models import CourseVersion, CourseVersionStatus

        version = self.db.query(CourseVersion).filter_by(
            id=linked.active_version_id,
            course_id=linked.id,
            owner_id=owner_id,
            status=CourseVersionStatus.READY.value,
        ).first()
        if version is None:
            raise RelationshipInvalid("The selected course has no eligible published version")
        return linked, version

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
        active_version_ready = bool(
            course.active_version_id is not None
            and self.db.query(CourseVersion.id).filter_by(
                id=course.active_version_id,
                course_id=course.id,
                owner_id=course.owner_id,
                status="READY",
            ).first() is not None
        )
        match_counts = {status: 0 for status in ("RELIABLE", "UNCERTAIN", "UNSUPPORTED")}
        if course.active_version_id is not None:
            for status, count in (
                self.db.query(CrossCourseConceptMatch.status, func.count())
                .filter(
                    CrossCourseConceptMatch.current_course_id == course.id,
                    CrossCourseConceptMatch.current_version_id == course.active_version_id,
                    CrossCourseConceptMatch.owner_id == course.owner_id,
                    CrossCourseConceptMatch.status.in_(match_counts),
                )
                .group_by(CrossCourseConceptMatch.status)
                .all()
            ):
                match_counts[status] = count
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
            "eligible_as_earlier_course": (
                course.status == CourseStatus.PUBLISHED.value and active_version_ready
            ),
            "subject_id": course.subject_id,
            "subject_name": (
                self.db.query(CourseSubject.name).filter_by(id=course.subject_id, owner_id=course.owner_id).scalar()
                if course.subject_id else None
            ),
            "builds_on_course_id": course.linked_course_id,
            "builds_on_course_title": (
                self.db.query(Course.title).filter_by(id=course.linked_course_id, owner_id=course.owner_id).scalar()
                if course.linked_course_id else course.linked_course_title_snapshot
            ),
            "builds_on_version_number": course.linked_version_number,
            "builds_on_sources_available": self._linked_sources_available(course),
            "link_revision": course.link_revision,
            "reliable_linked_match_count": match_counts["RELIABLE"],
            "uncertain_linked_match_count": match_counts["UNCERTAIN"],
            "unsupported_linked_match_count": match_counts["UNSUPPORTED"],
            "created_at": course.created_at,
        }

    def _linked_sources_available(self, course: Course) -> bool:
        if course.linked_course_id is None or course.linked_version_id is None or course.link_revoked_at is not None:
            return False
        from app.modules.curriculum.models import CourseVersion, CourseVersionStatus

        return self.db.query(CourseVersion.id).filter(
            CourseVersion.id == course.linked_version_id,
            CourseVersion.course_id == course.linked_course_id,
            CourseVersion.owner_id == course.owner_id,
            CourseVersion.status == CourseVersionStatus.READY.value,
            self.db.query(Course.id).filter(
                Course.id == course.linked_course_id,
                Course.owner_id == course.owner_id,
                Course.status == CourseStatus.PUBLISHED.value,
            ).exists(),
        ).first() is not None

    def linked_match_summaries(self, course: Course, owner_id: int) -> list[dict]:
        if course.active_version_id is None or not self._linked_sources_available(course):
            return []
        from app.modules.curriculum.models import Concept

        matches = self.db.query(CrossCourseConceptMatch).filter_by(
            owner_id=owner_id,
            current_course_id=course.id,
            current_version_id=course.active_version_id,
            linked_course_id=course.linked_course_id,
            linked_version_id=course.linked_version_id,
        ).order_by(CrossCourseConceptMatch.current_concept_id, CrossCourseConceptMatch.linked_concept_id).all()
        result = []
        for match in matches:
            current_name = self.db.query(Concept.name).filter_by(
                id=match.current_concept_id, course_id=course.id,
                course_version_id=course.active_version_id, owner_id=owner_id,
            ).scalar()
            linked_name = self.db.query(Concept.name).filter_by(
                id=match.linked_concept_id, course_id=course.linked_course_id,
                course_version_id=course.linked_version_id, owner_id=owner_id,
            ).scalar()
            if current_name is None or linked_name is None:
                continue
            result.append({
                "id": match.id,
                "current_concept_id": match.current_concept_id,
                "current_concept_name": current_name,
                "linked_concept_id": match.linked_concept_id,
                "linked_concept_name": linked_name,
                "status": match.status,
                "has_source_support": bool(match.provenance.get("source_support")),
            })
        return result

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
        subject_id: UUID | None = None,
        new_subject_name: str | None = None,
        builds_on_course_id: UUID | None = None,
    ) -> Course:
        subject = self._resolve_subject(owner_id, subject_id, new_subject_name)
        linked, linked_version = self._resolve_link(owner_id, builds_on_course_id)
        course = Course(
            owner_id=owner_id,
            title=title.strip(),
            goal=goal,
            starting_confidence=starting_confidence,
            status=CourseStatus.DRAFT.value,
            subject_id=subject.id if subject else None,
            linked_course_id=linked.id if linked else None,
            linked_version_id=linked_version.id if linked_version else None,
            linked_course_title_snapshot=linked.title if linked else None,
            linked_version_number=linked_version.version_number if linked_version else None,
            link_revision=1 if linked else 0,
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
        subject_id: UUID | None = None,
        subject_was_set: bool = False,
        builds_on_course_id: UUID | None = None,
        link_was_set: bool = False,
    ) -> Course:
        course = self.get_owned(course_id, owner_id, lock=subject_was_set or link_was_set)
        if (subject_was_set or link_was_set) and course.sources_are_immutable:
            raise RelationshipImmutable(str(course_id))
        if title is not None:
            course.title = title.strip()
        if goal is not None:
            course.goal = goal
        if starting_confidence is not None:
            course.starting_confidence = starting_confidence
        if subject_was_set:
            subject = self._resolve_subject(owner_id, subject_id)
            course.subject_id = subject.id if subject else None
        if link_was_set:
            linked, version = self._resolve_link(owner_id, builds_on_course_id)
            course.linked_course_id = linked.id if linked else None
            course.linked_version_id = version.id if version else None
            course.linked_course_title_snapshot = linked.title if linked else None
            course.linked_version_number = version.version_number if version else None
            course.link_revoked_at = None
            course.link_revision += 1
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
