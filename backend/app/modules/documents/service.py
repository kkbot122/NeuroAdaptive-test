"""
Document upload and storage.

New originals use private object storage through signed browser upload intents.
The legacy multipart path remains for existing local-development tests only;
it is never a public static file path.

Ownership is enforced in this layer, as with courses: every query filters by
owner_id, so a route that forgets cannot leak another learner's file.
"""
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.documents.magic_bytes import SignatureMismatch, verify_signature
from app.modules.documents.models import (
    Document,
    DocumentRole,
    DocumentSourceKind,
    DocumentStatus,
    StorageUploadIntent,
)
from app.modules.documents.storage import S3PrivateStorage


@dataclass(frozen=True)
class SourceUploadResult:
    document: Document
    source_changed: bool = False
    replaced_filename: Optional[str] = None
    cleanup_pending: bool = False

# frozen-scope.md per-course limits, narrowed to this sprint's supported set.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_STUDY_FILES = 5          # frozen-scope.md: one syllabus plus five study files
MAX_SYLLABUS_FILES = 1
ALLOWED_SUFFIXES = (".pdf", ".txt", ".md", ".markdown")

STORAGE_ROOT = Path(os.getenv("DOCUMENT_STORAGE_ROOT", "var/uploads"))


class DocumentNotFound(Exception):
    """Not found, or not owned by the caller. Rendered as 404 either way."""


class UploadRejected(Exception):
    """Rejected before enqueue: bad extension, oversize, or over the file cap."""


class SourcesLocked(Exception):
    """The course's source set was finalized; documents are immutable."""


class UploadIntentNotFound(Exception):
    """Intent is missing, expired, consumed, or unavailable to this owner."""


class UploadIntentConflict(Exception):
    """The source selected for replacement changed before finalization."""


class DocumentService:
    def __init__(self, db: Session, storage_root: Optional[Path] = None):
        self.db = db
        self.courses = CourseService(db)
        self.storage_root = Path(storage_root) if storage_root else STORAGE_ROOT

    # -- reads --------------------------------------------------------------

    def list_for_course(self, course_id: UUID, owner_id: int) -> List[Document]:
        self.courses.get_owned(course_id, owner_id)  # raises if not owned
        return (
            self.db.query(Document)
            .filter(Document.course_id == course_id, Document.owner_id == owner_id)
            .order_by(Document.created_at.asc())
            .all()
        )

    def get_owned(self, document_id: UUID, owner_id: int) -> Document:
        document = (
            self.db.query(Document)
            .filter(Document.id == document_id, Document.owner_id == owner_id)
            .first()
        )
        if document is None:
            raise DocumentNotFound(str(document_id))
        return document

    def read_bytes(self, document: Document) -> bytes:
        if document.storage_key:
            return S3PrivateStorage().read(document.storage_key)
        path = Path(document.storage_path)
        if not path.is_file():
            raise DocumentNotFound(str(document.id))
        return path.read_bytes()

    def create_upload_intent(
        self, course_id: UUID, owner_id: int, filename: str, size_bytes: int,
        checksum_sha256: str, role: str, content_type: Optional[str],
        replaces_document_id: UUID | None = None,
    ):
        course = self.courses.get_owned(course_id, owner_id, lock=True)
        self._require_sources_mutable(course)
        self._validate_metadata(filename, size_bytes, role, checksum_sha256)
        replacing = None
        if replaces_document_id is not None:
            replacing = self._owned_course_document(course_id, owner_id, replaces_document_id)
            if replacing.role != role:
                raise UploadRejected("A replacement must keep the source role.")
        self._check_role_cap(course_id, owner_id, role, excluding=replaces_document_id)
        key = f"courses/{course_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
        upload = S3PrivateStorage().create_upload_intent(key, content_type)
        intent = StorageUploadIntent(
            course_id=course_id, owner_id=owner_id, object_key=key,
            filename=Path(filename).name, content_type=content_type, role=role,
            expected_checksum_sha256=checksum_sha256, expected_size_bytes=size_bytes,
            replaces_document_id=replaces_document_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        self.db.add(intent)
        self.db.commit()
        self.db.refresh(intent)
        try:
            from app.modules.privacy.tasks import expire_upload_intent
            expire_upload_intent.apply_async(args=[str(intent.id)], countdown=15 * 60, retry=False)
        except Exception as exc:
            # The intent row itself is durable and account/source cleanup also
            # collects it if dispatch is unavailable.
            import logging
            logging.getLogger(__name__).warning("Upload expiry dispatch unavailable", extra={
                "intent_id": str(intent.id), "error_category": type(exc).__name__,
            })
        return intent, upload

    def finalize_upload(self, course_id: UUID, intent_id: UUID, owner_id: int) -> Document:
        return self.finalize_upload_result(course_id, intent_id, owner_id).document

    def finalize_upload_result(self, course_id: UUID, intent_id: UUID, owner_id: int) -> SourceUploadResult:
        # All source mutation/deletion paths lock the course first. Keep this
        # order before upload intents and documents so finalization cannot
        # invert account/course deletion's row locks.
        course = self.courses.get_owned(course_id, owner_id, lock=True)
        intent = self.db.query(StorageUploadIntent).filter(
            StorageUploadIntent.id == intent_id,
            StorageUploadIntent.course_id == course_id,
            StorageUploadIntent.owner_id == owner_id,
        ).with_for_update().populate_existing().first()
        if intent is None:
            raise UploadIntentNotFound(str(intent_id))
        if intent.finalized:
            document = self.db.query(Document).filter(
                Document.id == intent.finalized_document_id,
                Document.course_id == course_id, Document.owner_id == owner_id,
            ).first() if intent.finalized_document_id else None
            if document is None:
                if intent.upload_cleanup_task_id is not None:
                    raise UploadIntentConflict()
                raise UploadIntentNotFound(str(intent_id))
            from app.modules.privacy.models import StorageCleanupTask
            cleanup_ids = [cleanup_id for cleanup_id in (
                intent.retired_cleanup_task_id, intent.upload_cleanup_task_id,
            ) if cleanup_id is not None]
            cleanup_pending = bool(cleanup_ids) and self.db.query(StorageCleanupTask.id).filter(
                StorageCleanupTask.id.in_(cleanup_ids), StorageCleanupTask.status != "COMPLETE",
            ).first() is not None
            return SourceUploadResult(
                document, intent.source_change_applied, intent.replaced_filename, cleanup_pending,
            )
        expires_at = intent.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise UploadIntentNotFound(str(intent_id))
        self._require_sources_mutable(course)
        replacing = None
        if intent.replaces_document_id is not None:
            try:
                replacing = self._owned_course_document(course_id, owner_id, intent.replaces_document_id)
            except DocumentNotFound:
                # Another replacement/removal won the course lock after this
                # intent was created. Retire this candidate durably and make
                # the one-use intent terminal instead of leaving an orphan or
                # turning a normal race into an internal error.
                cleanup = self._schedule_cleanup(intent.object_key, "private", "superseded_upload", owner_id)
                intent.finalized = True
                intent.upload_cleanup_task_id = cleanup.id
                self.db.commit()
                self._dispatch_cleanup(cleanup.id)
                raise UploadIntentConflict() from None
            if replacing.role != intent.role:
                raise UploadRejected("A replacement must keep the source role.")
        self._check_role_cap(course_id, owner_id, intent.role, excluding=intent.replaces_document_id)
        storage = S3PrivateStorage()
        info = storage.inspect(intent.object_key)
        if (info.size_bytes != intent.expected_size_bytes
                or storage.checksum_sha256(intent.object_key) != intent.expected_checksum_sha256):
            raise UploadRejected("Uploaded object did not match the authorized file metadata.")
        content = storage.read(intent.object_key)
        try:
            verify_signature(intent.filename, content)
        except SignatureMismatch as exc:
            raise UploadRejected(str(exc)) from None

        # A same-file retry preserves the original and its chunks. A duplicate
        # source elsewhere in the course may still replace the selected file,
        # in which case only the redundant newly uploaded object is retired.
        existing = self.db.query(Document).filter(
            Document.course_id == course_id, Document.owner_id == owner_id,
            Document.checksum_sha256 == intent.expected_checksum_sha256,
            Document.role == intent.role,
        ).first()
        incompatible_duplicate = self.db.query(Document.id).filter(
            Document.course_id == course_id, Document.owner_id == owner_id,
            Document.checksum_sha256 == intent.expected_checksum_sha256,
            Document.role != intent.role,
        ).first()
        if replacing is not None and replacing.checksum_sha256 == intent.expected_checksum_sha256:
            cleanup = self._schedule_cleanup(intent.object_key, "private", "duplicate_upload", owner_id)
            intent.finalized = True
            intent.finalized_document_id = replacing.id
            intent.source_change_applied = False
            intent.upload_cleanup_task_id = cleanup.id
            self.db.commit()
            self._dispatch_cleanup(cleanup.id)
            return SourceUploadResult(replacing, cleanup_pending=True)

        if replacing is not None and existing is None and incompatible_duplicate is not None:
            # One document cannot safely stand in for the replacement while
            # retaining a different syllabus/study role. Retire only the
            # candidate upload and keep the selected original usable.
            cleanup = self._schedule_cleanup(intent.object_key, "private", "duplicate_upload", owner_id)
            intent.finalized = True
            intent.upload_cleanup_task_id = cleanup.id
            self.db.commit()
            self._dispatch_cleanup(cleanup.id)
            raise UploadRejected("A matching source already exists with a different role. The original was kept.")

        if existing is not None and (replacing is None or existing.id != replacing.id):
            cleanup = self._schedule_cleanup(intent.object_key, "private", "duplicate_upload", owner_id)
            replaced_filename = replacing.filename if replacing is not None else None
            source_changed = replacing is not None
            rebuild_required = course.sources_finalized_at is not None
            retired_cleanup_id = None
            if replacing is not None:
                if rebuild_required:
                    self._enforce_rebuild_limit(course)
                    self._invalidate_source_derived(course, replacing.id)
                retired_cleanup_id = self._retire_document(replacing, owner_id)
                intent.replaced_filename = replaced_filename
                intent.retired_cleanup_task_id = retired_cleanup_id
                self._advance_source_revision(course, rebuild=rebuild_required)
            intent.finalized = True
            intent.source_change_applied = source_changed
            intent.finalized_document_id = existing.id
            intent.upload_cleanup_task_id = cleanup.id
            self.db.commit()
            self._dispatch_cleanup(cleanup.id)
            if retired_cleanup_id is not None:
                self._dispatch_cleanup(retired_cleanup_id)
            return SourceUploadResult(existing, source_changed, replaced_filename, True)

        document = Document(
            course_id=course_id, owner_id=owner_id, filename=intent.filename,
            content_type=intent.content_type, role=intent.role,
            status=DocumentStatus.UPLOADED.value, storage_path=intent.object_key,
            storage_key=intent.object_key, size_bytes=info.size_bytes,
            checksum_sha256=intent.expected_checksum_sha256,
        )
        self.db.add(document)
        self.db.flush()
        replaced_filename = replacing.filename if replacing is not None else None
        source_changed = True
        rebuild_required = course.sources_finalized_at is not None
        cleanup_id = None
        if rebuild_required:
            self._enforce_rebuild_limit(course)
            self._invalidate_source_derived(course, replacing.id if replacing else None)
        self._advance_source_revision(course, rebuild=rebuild_required)
        if replacing is not None:
            cleanup_id = self._retire_document(replacing, owner_id)
        intent.replaced_filename = replaced_filename
        intent.retired_cleanup_task_id = cleanup_id
        intent.finalized = True
        intent.source_change_applied = source_changed
        intent.finalized_document_id = document.id
        self.db.commit()
        self.db.refresh(document)
        cleanup_pending = cleanup_id is not None
        if cleanup_id is not None:
            self._dispatch_cleanup(cleanup_id)
        return SourceUploadResult(document, source_changed, replaced_filename, cleanup_pending)

    # -- writes ---------------------------------------------------------------

    def upload(
        self,
        course_id: UUID,
        owner_id: int,
        filename: str,
        content: bytes,
        role: str = DocumentRole.STUDY.value,
        content_type: Optional[str] = None,
        source_kind: str = DocumentSourceKind.UPLOAD.value,
        replaces_document_id: UUID | None = None,
    ):
        """Returns (document, created). created=False on a checksum dedup hit,
        so the caller can report 200 rather than 201 and skip re-enqueuing."""
        try:
            course = self.courses.get_owned(course_id, owner_id, lock=True)
        except CourseNotFound:
            raise DocumentNotFound(str(course_id))

        self._require_sources_mutable(course)

        self._validate_shape(filename, content, role)

        checksum = hashlib.sha256(content).hexdigest()

        try:
            verify_signature(filename, content)
        except SignatureMismatch as exc:
            raise UploadRejected(str(exc))

        existing = self.db.query(Document).filter(
            Document.course_id == course_id, Document.owner_id == owner_id,
            Document.checksum_sha256 == checksum,
            Document.role == role,
        ).first()
        replacing = None
        if replaces_document_id is not None:
            try:
                replacing = self._owned_course_document(course_id, owner_id, replaces_document_id)
            except DocumentNotFound:
                # A retry after the replacement committed sees the retired id.
                # Return the already saved matching source instead of turning
                # a lost success response into a false upload failure.
                if existing is not None and existing.role == role:
                    return existing, False
                raise
            if replacing.role != role:
                raise UploadRejected("A replacement must keep the source role.")
            incompatible_duplicate = self.db.query(Document.id).filter(
                Document.course_id == course_id, Document.owner_id == owner_id,
                Document.checksum_sha256 == checksum, Document.role != role,
            ).first()
            if existing is None and incompatible_duplicate is not None:
                raise UploadRejected("A matching source already exists with a different role. The original was kept.")
        if existing is not None:
            if replacing is None or replacing.id == existing.id:
                return existing, False
            rebuild_required = course.sources_finalized_at is not None
            if rebuild_required:
                self._enforce_rebuild_limit(course)
                self._invalidate_source_derived(course, replacing.id)
            cleanup_id = self._retire_document(replacing, owner_id)
            self._advance_source_revision(course, rebuild=rebuild_required)
            self.db.commit()
            if cleanup_id is not None:
                self._dispatch_cleanup(cleanup_id)
            return existing, False
        self._check_role_cap(course_id, owner_id, role, excluding=replaces_document_id)
        if course.sources_finalized_at is not None:
            self._enforce_rebuild_limit(course)

        # Store under a generated name: a learner-supplied filename must never
        # decide a path on disk.
        suffix = Path(filename).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        course_dir = self.storage_root / str(course_id)
        course_dir.mkdir(parents=True, exist_ok=True)
        path = course_dir / stored_name
        path.write_bytes(content)

        document = Document(
            course_id=course_id,
            owner_id=owner_id,
            filename=Path(filename).name,
            content_type=content_type,
            role=role,
            source_kind=source_kind,
            status=DocumentStatus.UPLOADED.value,
            storage_path=str(path),
            size_bytes=len(content),
            checksum_sha256=checksum,
        )
        cleanup_id = None
        try:
            self.db.add(document)
            self.db.flush()
            rebuild_required = course.sources_finalized_at is not None
            if rebuild_required:
                self._invalidate_source_derived(course, replacing.id if replacing else None)
            self._advance_source_revision(course, rebuild=rebuild_required)
            cleanup_id = self._retire_document(replacing, owner_id) if replacing is not None else None
            self.db.commit()
        except Exception:
            self.db.rollback()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup = self._schedule_cleanup(str(path), "local", "failed_upload", owner_id)
                self.db.commit()
                self._dispatch_cleanup(cleanup.id)
            raise
        self.db.refresh(document)
        if cleanup_id is not None:
            self._dispatch_cleanup(cleanup_id)
        return document, True

    def paste_text(
        self,
        course_id: UUID,
        owner_id: int,
        title: str,
        text: str,
        role: str = DocumentRole.STUDY.value,
    ):
        """
        Pasted text skips the upload step entirely, but is written to disk as
        a .txt exactly like an uploaded one, so extraction and chunking need
        no separate code path for it. Returns (document, created), same as
        upload().
        """
        safe_title = "".join(c for c in (title or "pasted-text") if c.isalnum() or c in " -_").strip()
        filename = f"{safe_title or 'pasted-text'}.txt"
        return self.upload(
            course_id=course_id,
            owner_id=owner_id,
            filename=filename,
            content=text.encode("utf-8"),
            role=role,
            content_type="text/plain",
            source_kind=DocumentSourceKind.PASTED_TEXT.value,
        )

    def _validate_shape(self, filename: str, content: bytes, role: str) -> None:
        self._validate_metadata(filename, len(content), role, "0" * 64)
        if len(content) == 0:
            raise UploadRejected("The file is empty.")

    def remove_source(self, course_id: UUID, document_id: UUID, owner_id: int) -> tuple[str, int, bool]:
        """Remove one owned pre-publication source and invalidate its outline."""
        try:
            course = self.courses.get_owned(course_id, owner_id, lock=True)
        except CourseNotFound:
            raise DocumentNotFound(str(course_id))
        self._require_sources_mutable(course)
        document = self._owned_course_document(course_id, owner_id, document_id)
        filename = document.filename
        rebuild_required = course.sources_finalized_at is not None
        if rebuild_required:
            self._enforce_rebuild_limit(course)
            self._invalidate_source_derived(course, document.id)
        self._advance_source_revision(course, rebuild=rebuild_required)
        cleanup_id = self._retire_document(document, owner_id)
        remaining = self.db.query(Document).filter(
            Document.course_id == course_id, Document.owner_id == owner_id,
        ).count()
        if remaining == 0:
            course.sources_finalized_at = None
            course.status = "DRAFT"
        self.db.commit()
        if cleanup_id is not None:
            self._dispatch_cleanup(cleanup_id)
        return filename, remaining, cleanup_id is not None

    def _require_sources_mutable(self, course) -> None:
        from app.modules.courses.models import CourseStatus

        if course.status == CourseStatus.PUBLISHED.value or course.active_version_id is not None:
            raise SourcesLocked("Published course sources cannot be changed. Create a new course to use different material.")

    def _owned_course_document(self, course_id: UUID, owner_id: int, document_id: UUID) -> Document:
        document = self.db.query(Document).filter(
            Document.id == document_id, Document.course_id == course_id, Document.owner_id == owner_id,
        ).with_for_update().first()
        if document is None:
            raise DocumentNotFound(str(document_id))
        return document

    def _schedule_cleanup(self, target: str, kind: str, reason: str, owner_id: int):
        from app.modules.privacy.service import PrivacyService

        return PrivacyService(self.db).schedule_storage_cleanup(target, kind, reason, owner_id)

    @staticmethod
    def _cleanup_target(document: Document) -> tuple[str, str] | None:
        if document.storage_key:
            return document.storage_key, "private"
        if document.storage_path:
            return document.storage_path, "local"
        return None

    def _retire_document(self, document: Document, owner_id: int):
        from app.modules.documents.chunk_models import Chunk

        target = self._cleanup_target(document)
        cleanup = self._schedule_cleanup(target[0], target[1], "source_replaced", owner_id) if target else None
        self.db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
        self.db.delete(document)
        return cleanup.id if cleanup is not None else None

    def _dispatch_cleanup(self, cleanup_id: UUID) -> None:
        try:
            from app.modules.privacy.tasks import cleanup_storage_object
            cleanup_storage_object.apply_async(args=[str(cleanup_id)], retry=False)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Storage cleanup dispatch unavailable", extra={
                "task_id": str(cleanup_id), "error_category": type(exc).__name__,
            })

    def _advance_source_revision(self, course, *, rebuild: bool) -> None:
        from app.modules.jobs.models import ProcessingJob

        course.source_revision += 1
        if rebuild:
            course.status = "PROCESSING"
        self.db.query(ProcessingJob).filter(
            ProcessingJob.course_id == course.id,
            ProcessingJob.owner_id == course.owner_id,
            ProcessingJob.status.in_(["PENDING", "RUNNING"]),
        ).update({
            ProcessingJob.status: "CANCELLED",
            ProcessingJob.lease_token: None,
            ProcessingJob.lease_expires_at: None,
            ProcessingJob.error_category: "SOURCE_SET_CHANGED",
            ProcessingJob.error_detail: "The course source set changed. A fresh preparation is required.",
        }, synchronize_session=False)

    def _enforce_rebuild_limit(self, course) -> None:
        from app.modules.abuse.service import AbuseControlService

        AbuseControlService(self.db).enforce_course_regeneration_cap(course.id, course.owner_id)

    def _invalidate_source_derived(self, course, changed_document_id: UUID | None) -> None:
        """Remove pre-publication derived data while preserving other source chunks."""
        from app.modules.adaptation.models import AdaptationDecision, AdaptationOutcome
        from app.modules.curriculum.models import (
            AssessmentBlueprint, Concept, ConceptPrerequisite, ConceptSource,
            CourseVersion, Lesson, LessonConcept, Module,
        )
        from app.modules.documents.chunk_models import Chunk
        from app.modules.learning.models import LearningActivity
        from app.modules.mastery.models import Question, QuestionConcept
        from app.modules.preparation.models import (
            ActivityPreparation, LessonContentArtifact, LessonContentCitation,
            PreparedActivityQuestion, QuestionSource,
        )

        if self.db.query(LearningActivity.id).filter(LearningActivity.course_id == course.id).first():
            raise SourcesLocked("Learning has started for this course; its source set is fixed.")

        preparation_ids = [row[0] for row in self.db.query(ActivityPreparation.id).filter(
            ActivityPreparation.course_id == course.id,
        ).all()]
        if preparation_ids:
            self.db.query(PreparedActivityQuestion).filter(
                PreparedActivityQuestion.preparation_id.in_(preparation_ids),
            ).delete(synchronize_session=False)
            self.db.query(ActivityPreparation).filter(
                ActivityPreparation.id.in_(preparation_ids),
            ).delete(synchronize_session=False)

        artifact_ids = [row[0] for row in self.db.query(LessonContentArtifact.id).filter(
            LessonContentArtifact.course_id == course.id,
        ).all()]
        if artifact_ids:
            self.db.query(LessonContentCitation).filter(
                LessonContentCitation.artifact_id.in_(artifact_ids),
            ).delete(synchronize_session=False)
            self.db.query(LessonContentArtifact).filter(
                LessonContentArtifact.id.in_(artifact_ids),
            ).delete(synchronize_session=False)

        question_ids = [row[0] for row in self.db.query(Question.id).filter(
            Question.course_id == course.id,
        ).all()]
        if question_ids:
            self.db.query(QuestionSource).filter(QuestionSource.question_id.in_(question_ids)).delete(synchronize_session=False)
            self.db.query(QuestionConcept).filter(QuestionConcept.question_id.in_(question_ids)).delete(synchronize_session=False)
            self.db.query(Question).filter(Question.id.in_(question_ids)).delete(synchronize_session=False)

        self.db.query(AdaptationOutcome).filter(AdaptationOutcome.decision_id.in_(
            self.db.query(AdaptationDecision.id).filter(AdaptationDecision.course_id == course.id),
        )).delete(synchronize_session=False)
        self.db.query(AdaptationDecision).filter(AdaptationDecision.course_id == course.id).delete(synchronize_session=False)

        version_ids = [row[0] for row in self.db.query(CourseVersion.id).filter(
            CourseVersion.course_id == course.id,
        ).all()]
        if version_ids:
            self.db.query(CourseVersion).filter(CourseVersion.id.in_(version_ids)).update({
                CourseVersion.status: "STALE",
                CourseVersion.validation_errors: ["Source material changed; review a newly prepared outline."],
            }, synchronize_session=False)
            module_ids = [row[0] for row in self.db.query(Module.id).filter(Module.course_version_id.in_(version_ids)).all()]
            lesson_ids = [row[0] for row in self.db.query(Lesson.id).filter(Lesson.module_id.in_(module_ids)).all()] if module_ids else []
            if lesson_ids:
                self.db.query(LessonConcept).filter(LessonConcept.lesson_id.in_(lesson_ids)).delete(synchronize_session=False)
                self.db.query(Lesson).filter(Lesson.id.in_(lesson_ids)).delete(synchronize_session=False)
            if module_ids:
                self.db.query(Module).filter(Module.id.in_(module_ids)).delete(synchronize_session=False)
            self.db.query(AssessmentBlueprint).filter(AssessmentBlueprint.course_version_id.in_(version_ids)).delete(synchronize_session=False)
            self.db.query(ConceptPrerequisite).filter(ConceptPrerequisite.course_version_id.in_(version_ids)).delete(synchronize_session=False)
        self.db.query(ConceptSource).filter(ConceptSource.course_id == course.id).delete(synchronize_session=False)
        self.db.query(Concept).filter(Concept.course_id == course.id).delete(synchronize_session=False)

        # Unchanged sources keep their chunk text and reusable embeddings.
        if changed_document_id is not None:
            self.db.query(Chunk).filter(
                Chunk.course_id == course.id,
                Chunk.document_id == changed_document_id,
            ).delete(synchronize_session=False)

    def _validate_metadata(self, filename: str, size_bytes: int, role: str, checksum_sha256: str) -> None:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise UploadRejected(
                f"Unsupported file type '{suffix or filename}'. "
                "Supported this release: PDF, TXT, Markdown."
            )
        if size_bytes <= 0:
            raise UploadRejected("The file is empty.")
        if size_bytes > MAX_FILE_BYTES:
            raise UploadRejected(
                f"File is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB limit."
            )
        if role not in (DocumentRole.SYLLABUS.value, DocumentRole.STUDY.value):
            raise UploadRejected(f"Unknown document role '{role}'.")
        if len(checksum_sha256) != 64 or any(c not in "0123456789abcdef" for c in checksum_sha256.lower()):
            raise UploadRejected("Checksum must be a SHA-256 hexadecimal digest.")

    def _check_role_cap(
        self, course_id: UUID, owner_id: int, role: str, *, excluding: UUID | None = None,
    ) -> None:
        existing = (
            self.db.query(Document)
            .filter(
                Document.course_id == course_id,
                Document.owner_id == owner_id,
                Document.role == role,
            )
            .filter(Document.id != excluding if excluding is not None else True)
            .count()
        )
        cap = MAX_SYLLABUS_FILES if role == DocumentRole.SYLLABUS.value else MAX_STUDY_FILES
        if existing >= cap:
            label = "syllabus" if role == DocumentRole.SYLLABUS.value else "study"
            raise UploadRejected(
                f"This course already has the maximum of {cap} {label} file(s)."
            )
