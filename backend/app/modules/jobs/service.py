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
import time
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.models import Course, CourseStatus
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.extraction import (
    EXTRACTION_VERSION,
    ExtractionError,
    NoExtractableText,
    chunk as chunk_document,
    deterministic_chunk_id,
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
from app.modules.curriculum.models import CourseVersionStatus
from app.modules.curriculum.service import CurriculumService
from app.modules.retrieval.service import CHUNKS_COLLECTION
from app.services.embedding.gateway import EmbeddingError, EmbeddingGateway
from app.services.generation.gateway import GenerationError, GenerationGateway
from app.services.vectorstore.store import VectorPoint, VectorStore, VectorStoreError

logger = logging.getLogger(__name__)


class JobNotFound(Exception):
    """Not found, or not owned by the caller."""


class CourseVersionValidationFailed(Exception):
    """generate_version() produced a version that failed validation. The
    stage this is raised from is marked FAILED, with the version's own
    validation_errors as the detail a human would need -- never swallowed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobService:
    def __init__(
        self,
        db: Session,
        embeddings: Optional[EmbeddingGateway] = None,
        vectors: Optional[VectorStore] = None,
        generation: Optional[GenerationGateway] = None,
    ):
        self.db = db
        self.documents = DocumentService(db)
        # Real providers are constructed lazily and only if not injected, so
        # a JobService built for a test that never reaches INDEXING/concept
        # extraction pays no cost and needs no credentials.
        self._embeddings = embeddings
        self._vectors = vectors
        self._generation = generation

    def _get_embeddings(self) -> EmbeddingGateway:
        if self._embeddings is None:
            from app.services.embedding.gemini import GeminiEmbeddingGateway

            self._embeddings = GeminiEmbeddingGateway()
        return self._embeddings

    def _get_vectors(self) -> VectorStore:
        if self._vectors is None:
            from app.services.vectorstore.qdrant_store import QdrantVectorStore

            self._vectors = QdrantVectorStore()
        return self._vectors

    def _get_generation(self):
        if self._generation is None:
            from app.services.generation.gemini import GeminiGenerationGateway

            self._generation = GeminiGenerationGateway()
        return self._generation

    # ── lifecycle ────────────────────────────────────────────────────────────

    def has_active_job_for_course(self, course_id: UUID) -> bool:
        """
        True if a job for this course is already PENDING or RUNNING.

        Found live: two near-simultaneous POST .../process calls for the
        same course (a UI double/triple-click with no loading feedback)
        both passed each chunk's "does this id already exist" check before
        either had committed, then both tried to INSERT the same
        deterministic chunk id -- a real IntegrityError, not a hypothetical
        one. Chunk upsert-by-id (jobs/service.py's _stage_chunking) is
        idempotent against a SEQUENTIAL retry, exactly as designed, but was
        never meant to be safe against two of these running at once. This
        check is the fix: reject the second call before it starts, rather
        than letting two pipelines race.
        """
        return (
            self.db.query(ProcessingJob)
            .filter(
                ProcessingJob.course_id == course_id,
                ProcessingJob.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
            .first()
            is not None
        )

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
            ProcessingStageName.INDEXING.value: self._stage_indexing,
            ProcessingStageName.EXTRACTING_CONCEPTS.value: self._stage_extracting_concepts,
            ProcessingStageName.BUILDING_GRAPH.value: self._stage_noop,
            ProcessingStageName.GENERATING_STRUCTURE.value: self._stage_noop,
            ProcessingStageName.VALIDATING_COURSE.value: self._stage_noop,
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
            # Safe to surface verbatim: every NoExtractableText message is
            # our own authored, human-facing text (encrypted/too-many-pages/
            # no-text-found), never provider output or raw document content.
            job.error_detail = str(exc)
            self._set_course_status(job, CourseStatus.NEEDS_INPUT)
            logger.info("Job %s needs input at %s", job.id, stage.name)
            return StageStatus.FAILED
        except (EmbeddingError, VectorStoreError, GenerationError) as exc:
            # frozen-scope.md: "Provider quota or availability failure pauses
            # the job for manual retry; there is no automatic provider
            # fallback." Distinct from a content problem: nothing about this
            # document is wrong, the dependency is unavailable right now.
            # Stage stays PENDING (not FAILED) so a retry re-attempts it
            # rather than requiring the whole job to be treated as broken.
            stage.status = StageStatus.PENDING.value
            stage.started_at = None
            job.status = JobStatus.PAUSED.value
            job.error_category = type(exc).__name__
            logger.error("Job %s paused at %s: %s", job.id, stage.name, type(exc).__name__)
            return StageStatus.PENDING
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
        """
        Idempotent by construction rather than by delete-then-reinsert: each
        chunk's id is deterministic_chunk_id(document_id, extraction_version,
        position), so re-running this stage on the same document and the same
        EXTRACTION_VERSION regenerates the identical id set. A retry after a
        crash upserts in place -- overwriting text/offsets if anything
        changed -- instead of deleting everything and handing out fresh ids
        that would orphan any citation recorded elsewhere.

        Only stale rows (positions the current run no longer produces, e.g.
        because the source shrank) are removed.
        """
        for document in self._documents(job):
            raw = self.documents.read_bytes(document)
            extracted = extract(raw, document.filename)
            proposed_chunks = chunk_document(extracted)

            live_ids = set()
            for proposed in proposed_chunks:
                chunk_id = deterministic_chunk_id(
                    document.id, EXTRACTION_VERSION, proposed.position
                )
                live_ids.add(chunk_id)

                existing = self.db.query(Chunk).filter(Chunk.id == chunk_id).first()
                if existing is None:
                    existing = Chunk(id=chunk_id, document_id=document.id)
                    self.db.add(existing)

                existing.course_id = job.course_id
                existing.owner_id = job.owner_id
                existing.position = proposed.position
                existing.heading_path = proposed.heading_path
                existing.content_type = proposed.content_type
                existing.text = proposed.text
                existing.char_count = len(proposed.text)
                existing.token_count = proposed.token_count
                existing.char_start = proposed.char_start
                existing.char_end = proposed.char_end
                existing.page_start = proposed.page_start
                existing.page_end = proposed.page_end
                existing.extraction_version = EXTRACTION_VERSION
                # A rewritten chunk is no longer known-good in the index
                # until the INDEXING stage re-embeds it.
                existing.embedding_model = None
                existing.indexed_at = None

            # Remove chunks from a previous run of this document that the
            # current run did not reproduce (e.g. the source shrank).
            self.db.query(Chunk).filter(
                Chunk.document_id == document.id, ~Chunk.id.in_(live_ids) if live_ids else True
            ).delete(synchronize_session=False)

            self.db.commit()

    def _stage_indexing(self, job: ProcessingJob) -> None:
        """
        Embed every not-yet-indexed chunk and upsert it into the vector
        store, keyed by the chunk's own id -- re-indexing after a reprocess
        overwrites the same point rather than creating a second one.

        Item 7's requirement that each chunk's heading path be prepended
        before embedding is applied here, at embed time, not at chunk-storage
        time: the stored chunk.text stays exactly the source text (needed for
        citations and for the "ingest hostile text as inert data" property),
        while the embedded representation includes the heading for retrieval
        quality.
        """
        embeddings = self._get_embeddings()
        vectors = self._get_vectors()

        vectors.ensure_collection(CHUNKS_COLLECTION, embeddings.dimensions)

        pending = (
            self.db.query(Chunk)
            .filter(Chunk.course_id == job.course_id, Chunk.owner_id == job.owner_id)
            .filter(Chunk.indexed_at.is_(None))
            .all()
        )
        if not pending:
            return

        # Matches GeminiEmbeddingGateway._MAX_BATCH_SIZE, tuned against the
        # live free-tier API (see that module). A different gateway may
        # tolerate a larger batch; this stage does not assume one.
        batch_size = 10
        for index, start in enumerate(range(0, len(pending), batch_size)):
            if index > 0:
                # A short pause between batches, independent of the
                # gateway's own retry-on-rate-limit: spreads requests out so
                # the retry path is needed less often, not a substitute for it.
                time.sleep(1)
            batch = pending[start : start + batch_size]
            texts_to_embed = [
                f"{c.heading_path}\n\n{c.text}" if c.heading_path else c.text for c in batch
            ]
            vectors_out = embeddings.embed_texts(texts_to_embed)

            points = [
                VectorPoint(
                    id=chunk.id,
                    vector=vector,
                    payload={
                        "owner_id": chunk.owner_id,
                        "course_id": str(chunk.course_id),
                        "document_id": str(chunk.document_id),
                    },
                )
                for chunk, vector in zip(batch, vectors_out)
            ]
            vectors.upsert(CHUNKS_COLLECTION, points)

            for chunk in batch:
                chunk.embedding_model = embeddings.model_name
                chunk.indexed_at = _now()
            self.db.commit()

    def _stage_extracting_concepts(self, job: ProcessingJob) -> None:
        """
        Runs the whole Phase 2 curriculum pipeline: concept extraction,
        normalization, prerequisite graph, module/lesson clustering,
        blueprinting and validation. One stage rather than the four separate
        ones the frozen pipeline names (BUILDING_GRAPH, GENERATING_STRUCTURE,
        VALIDATING_COURSE) because CurriculumService.generate_version() is a
        single cohesive unit internally -- splitting it into four separately
        resumable stages would mean persisting intermediate state between
        them, which nothing downstream needs yet. The other three stage names
        stay in the pipeline (frozen-scope.md's own vocabulary is preserved)
        and are recorded as trivially succeeding immediately after. Finer
        per-stage progress within curriculum generation is a scope
        simplification, not an attempt at the mandate's full granularity.

        Raises CourseVersionValidationFailed (-> stage FAILED, not paused) if
        the generated version does not pass validation. This is a content
        problem, not a provider outage, so it is not retried automatically.
        """
        service = CurriculumService(self.db, self._get_generation(), self._get_embeddings())
        version = service.generate_version(job.course_id, job.owner_id)
        if version.status != CourseVersionStatus.READY.value:
            raise CourseVersionValidationFailed("; ".join(version.validation_errors) or "unknown")

    def _stage_noop(self, job: ProcessingJob) -> None:
        """BUILDING_GRAPH, GENERATING_STRUCTURE and VALIDATING_COURSE: their
        work already happened inside _stage_extracting_concepts. Kept as
        distinct, always-succeeding stages so the frozen pipeline's stage
        names stay visible in the job's stage list, matching what
        frozen-scope.md's polling contract names."""
        return None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _set_course_status(self, job: ProcessingJob, status: CourseStatus) -> None:
        course = self.db.query(Course).filter(Course.id == job.course_id).first()
        if course is not None:
            course.status = status.value
