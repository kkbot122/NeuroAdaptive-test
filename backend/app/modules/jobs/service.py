"""
Processing job orchestration.

Runs the pipeline in-process rather than through Celery (frozen substitution),
but writes the same durable job/stage records a queued worker would, so
progress is real and the executor can be swapped later without changing the
observable contract.

Stages are idempotent: re-running one replaces its own output rather than
appending. That is what makes retry safe.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.models import Course, CourseStatus
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.extraction import (
    ExtractionError,
    NoExtractableText,
    chunk as chunk_document,
    extract,
)
from app.modules.documents.models import Document, DocumentStatus
from app.modules.documents.service import DocumentService
from app.modules.jobs.models import (
    ACTIVE_STAGE_ORDER,
    JobStatus,
    ProcessingJob,
    ProcessingStage,
    ProcessingStageName,
    StageStatus,
)

logger = logging.getLogger(__name__)


class JobNotFound(Exception):
    """Not found, or not owned by the caller."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobService:
    def __init__(self, db: Session):
        self.db = db
        self.documents = DocumentService(db)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def create_for_course(self, course_id: UUID, owner_id: int) -> ProcessingJob:
        job = ProcessingJob(
            course_id=course_id, owner_id=owner_id, status=JobStatus.PENDING.value
        )
        self.db.add(job)
        self.db.flush()
        for position, stage_name in enumerate(ACTIVE_STAGE_ORDER):
            self.db.add(
                ProcessingStage(
                    job_id=job.id,
                    name=stage_name.value,
                    position=position,
                    status=StageStatus.PENDING.value,
                )
            )
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_owned(self, job_id: UUID, owner_id: int) -> ProcessingJob:
        job = (
            self.db.query(ProcessingJob)
            .filter(ProcessingJob.id == job_id, ProcessingJob.owner_id == owner_id)
            .first()
        )
        if job is None:
            raise JobNotFound(str(job_id))
        return job

    # ── execution ────────────────────────────────────────────────────────────

    def run(self, job_id: UUID, owner_id: int) -> ProcessingJob:
        """
        Walk the pipeline until it completes, needs the learner, or pauses.

        Stops at the first stage that is not implemented yet and marks the job
        PAUSED with a retryable provider error, which is the behaviour
        frozen-scope.md specifies for provider unavailability. Completed stages
        are preserved, so a retry after configuration resumes rather than
        restarting.
        """
        job = self.get_owned(job_id, owner_id)
        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or _now()
        self.db.commit()

        for stage in job.stages:
            if stage.status == StageStatus.SUCCEEDED.value:
                continue  # idempotent resume

            outcome = self._run_stage(job, stage)
            if outcome != StageStatus.SUCCEEDED:
                self.db.commit()
                return job

        job.status = JobStatus.READY.value
        job.current_stage = None
        job.finished_at = _now()
        self._set_course_status(job, CourseStatus.READY)
        self.db.commit()
        return job

    def _run_stage(self, job: ProcessingJob, stage: ProcessingStage) -> StageStatus:
        stage.status = StageStatus.RUNNING.value
        stage.attempts += 1
        stage.started_at = _now()
        stage.error_category = None
        job.current_stage = stage.name
        self.db.commit()

        handler = {
            ProcessingStageName.VALIDATING.value: self._stage_validating,
            ProcessingStageName.EXTRACTING.value: self._stage_extracting,
            ProcessingStageName.CHUNKING.value: self._stage_chunking,
        }.get(stage.name)

        if handler is None:
            # Not built yet. Pause rather than fail: no work was lost and the
            # job is resumable once the dependency exists.
            stage.status = StageStatus.PENDING.value
            stage.started_at = None
            job.status = JobStatus.PAUSED.value
            job.error_category = "STAGE_NOT_IMPLEMENTED"
            logger.info("Job %s paused before unimplemented stage %s", job.id, stage.name)
            return StageStatus.PENDING

        try:
            handler(job)
        except NoExtractableText as exc:
            stage.status = StageStatus.FAILED.value
            stage.finished_at = _now()
            stage.error_category = "NO_EXTRACTABLE_TEXT"
            job.status = JobStatus.NEEDS_INPUT.value
            job.error_category = "NO_EXTRACTABLE_TEXT"
            self._set_course_status(job, CourseStatus.NEEDS_INPUT)
            logger.info("Job %s needs input at %s", job.id, stage.name)
            return StageStatus.FAILED
        except Exception as exc:
            stage.status = StageStatus.FAILED.value
            stage.finished_at = _now()
            stage.error_category = type(exc).__name__
            job.status = JobStatus.FAILED.value
            job.error_category = type(exc).__name__
            self._set_course_status(job, CourseStatus.FAILED)
            # Category only — never document text or provider payloads.
            logger.error("Job %s failed at %s: %s", job.id, stage.name, type(exc).__name__)
            return StageStatus.FAILED

        stage.status = StageStatus.SUCCEEDED.value
        stage.finished_at = _now()
        self.db.commit()
        return StageStatus.SUCCEEDED

    # ── stage handlers ───────────────────────────────────────────────────────

    def _documents(self, job: ProcessingJob) -> List[Document]:
        return (
            self.db.query(Document)
            .filter(Document.course_id == job.course_id, Document.owner_id == job.owner_id)
            .order_by(Document.created_at.asc())
            .all()
        )

    def _stage_validating(self, job: ProcessingJob) -> None:
        documents = self._documents(job)
        if not documents:
            raise ValueError("Course has no documents to process")

    def _stage_extracting(self, job: ProcessingJob) -> None:
        """
        Extract native text per document.

        A document with no extractable text sets NEEDS_INPUT with a
        learner-facing reason rather than producing silent empty output.
        """
        for document in self._documents(job):
            document.status = DocumentStatus.EXTRACTING.value
            self.db.commit()

            raw = self.documents.read_bytes(document)
            try:
                extracted = extract(raw, document.filename)
            except NoExtractableText as exc:
                document.status = DocumentStatus.NEEDS_INPUT.value
                document.needs_input_reason = exc.reason
                self.db.commit()
                raise
            except ExtractionError as exc:
                document.status = DocumentStatus.FAILED.value
                document.needs_input_reason = str(exc)
                self.db.commit()
                raise

            document.page_count = extracted.page_count
            document.status = DocumentStatus.EXTRACTED.value
            document.needs_input_reason = None
            self.db.commit()

    def _stage_chunking(self, job: ProcessingJob) -> None:
        for document in self._documents(job):
            # Idempotent: drop this document's previous chunks before rewriting.
            self.db.query(Chunk).filter(Chunk.document_id == document.id).delete()
            self.db.commit()

            raw = self.documents.read_bytes(document)
            extracted = extract(raw, document.filename)
            for proposed in chunk_document(extracted):
                self.db.add(
                    Chunk(
                        document_id=document.id,
                        course_id=job.course_id,
                        owner_id=job.owner_id,
                        position=proposed.position,
                        heading_path=proposed.heading_path,
                        content_type=proposed.content_type,
                        text=proposed.text,
                        char_count=len(proposed.text),
                        page_start=proposed.page_start,
                        page_end=proposed.page_end,
                    )
                )
            self.db.commit()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _set_course_status(self, job: ProcessingJob, status: CourseStatus) -> None:
        course = self.db.query(Course).filter(Course.id == job.course_id).first()
        if course is not None:
            course.status = status.value
