"""
JobService.run() exercised directly (no HTTP layer) for the provider-failure
pause path -- no existing test covered a job actually reaching PAUSED before
this, so the friendly error_detail added alongside provider_errors.py had
nothing proving it actually lands on the job row.
"""
import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.jobs.models import (
    ACTIVE_STAGE_ORDER,
    JobStatus,
    ProcessingJob,
    ProcessingStage,
    StageStatus,
)
from app.modules.jobs.service import JobService
from app.services.embedding.gateway import EmbeddingError
from app.services.generation.fake import FakeGenerationGateway
from app.services.vectorstore.fake import FakeVectorStore


class ResourceExhausted(Exception):
    """Named to match google.api_core.exceptions.ResourceExhausted exactly
    -- classify_provider_error (provider_errors.py) matches by type NAME,
    the same brittle-by-design convention gemini.py's own
    _is_rate_limit_error already relies on, so this stand-in must share
    the real exception's class name, not just its meaning."""


class _AlwaysQuotaExhaustedEmbeddings:
    """A minimal EmbeddingGateway that fails exactly the way
    GeminiEmbeddingGateway does when the real provider is rate-limited: an
    EmbeddingError whose __cause__ is the real provider exception type."""

    model_name = "broken-embeddings"
    dimensions = 32

    def embed_texts(self, texts):
        try:
            raise ResourceExhausted("simulated: quota exceeded")
        except ResourceExhausted as exc:
            raise EmbeddingError("Gemini embedding call failed: ResourceExhausted") from exc


@pytest.fixture()
def course(db_session, owner):
    c = Course(owner_id=owner.id, title="OS Course")
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


def _job_ready_for_indexing(db_session, course, owner) -> ProcessingJob:
    """A job whose VALIDATING/EXTRACTING/CHUNKING stages already succeeded,
    with one real chunk in place -- JobService.run() skips SUCCEEDED stages
    (idempotent resume), so this starts execution at INDEXING directly
    without needing to run real text extraction first."""
    document = Document(
        course_id=course.id, owner_id=owner.id, filename="notes.txt",
        storage_path="/dev/null", checksum_sha256="c" * 64,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    db_session.add(
        Chunk(
            id=uuid.uuid4(), document_id=document.id, course_id=course.id, owner_id=owner.id,
            position=0, heading_path="Intro", text="Some text about deadlocks.", token_count=6,
        )
    )

    job = ProcessingJob(course_id=course.id, owner_id=owner.id, status=JobStatus.PENDING.value)
    db_session.add(job)
    db_session.flush()
    for position, stage_name in enumerate(ACTIVE_STAGE_ORDER):
        already_done = stage_name.value in ("VALIDATING", "EXTRACTING", "CHUNKING")
        db_session.add(
            ProcessingStage(
                job_id=job.id, name=stage_name.value, position=position,
                status=StageStatus.SUCCEEDED.value if already_done else StageStatus.PENDING.value,
            )
        )
    db_session.commit()
    db_session.refresh(job)
    return job


class TestProviderFailurePausesWithAFriendlyReason:
    def test_a_quota_exceeded_embedding_failure_pauses_with_an_authored_message(
        self, db_session, owner, course
    ):
        job = _job_ready_for_indexing(db_session, course, owner)
        service = JobService(
            db_session,
            embeddings=_AlwaysQuotaExhaustedEmbeddings(),
            vectors=FakeVectorStore(),
            generation=FakeGenerationGateway(),
        )

        result = service.run(job.id, owner.id)

        assert result.status == JobStatus.PAUSED.value
        assert result.error_category == "EmbeddingError"
        assert result.error_detail  # never blank -- this is what the UI now shows
        assert "quota" in result.error_detail.lower()
        # Never the raw provider exception name -- only our authored sentence.
        assert "ResourceExhausted" not in result.error_detail

        indexing_stage = next(s for s in result.stages if s.name == "INDEXING")
        assert indexing_stage.status == StageStatus.PENDING.value  # retryable, not FAILED

    def test_retrying_after_the_provider_recovers_resumes_past_the_pause(
        self, db_session, owner, course
    ):
        """What /jobs/{id}/retry (jobs/router.py) relies on: re-running a
        PAUSED job with a now-working provider must resume from the stage
        that paused it, not restart the whole pipeline."""
        job = _job_ready_for_indexing(db_session, course, owner)
        broken_service = JobService(
            db_session,
            embeddings=_AlwaysQuotaExhaustedEmbeddings(),
            vectors=FakeVectorStore(),
            generation=FakeGenerationGateway().set_default('{"concepts": [], "edges": []}'),
        )
        paused = broken_service.run(job.id, owner.id)
        assert paused.status == JobStatus.PAUSED.value

        from app.services.embedding.fake import FakeEmbeddingGateway

        recovered_service = JobService(
            db_session,
            embeddings=FakeEmbeddingGateway(),
            vectors=FakeVectorStore(),
            generation=FakeGenerationGateway().set_default('{"concepts": [], "edges": []}'),
        )
        result = recovered_service.run(job.id, owner.id)

        assert result.status == JobStatus.READY.value
        stages_by_name = {s.name: s.status for s in result.stages}
        # The stages that already succeeded before the pause were not re-run.
        assert stages_by_name["VALIDATING"] == StageStatus.SUCCEEDED.value
        assert stages_by_name["EXTRACTING"] == StageStatus.SUCCEEDED.value
        assert stages_by_name["CHUNKING"] == StageStatus.SUCCEEDED.value
        assert stages_by_name["INDEXING"] == StageStatus.SUCCEEDED.value
