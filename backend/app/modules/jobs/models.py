import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    NEEDS_INPUT = "NEEDS_INPUT"
    FAILED = "FAILED"
    PAUSED = "PAUSED"  # provider quota/availability; awaiting manual retry


class StageStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ProcessingStageName(str, enum.Enum):
    """
    The pipeline from frozen-scope.md.

    INTERPRETING_VISUALS is declared but is SKIPPED this sprint: the frozen
    substitutions restrict input to native-text PDF and TXT/Markdown, with no
    OCR and no multimodal fallback. It is kept in the enum so the stage list
    stays truthful about the target pipeline rather than silently omitting it.
    """

    VALIDATING = "VALIDATING"
    EXTRACTING = "EXTRACTING"
    INTERPRETING_VISUALS = "INTERPRETING_VISUALS"
    CHUNKING = "CHUNKING"
    INDEXING = "INDEXING"
    EXTRACTING_CONCEPTS = "EXTRACTING_CONCEPTS"
    BUILDING_GRAPH = "BUILDING_GRAPH"
    GENERATING_STRUCTURE = "GENERATING_STRUCTURE"
    VALIDATING_COURSE = "VALIDATING_COURSE"


# The order stages actually run in. INTERPRETING_VISUALS is absent because it
# is out of scope this sprint; see ProcessingStageName.
ACTIVE_STAGE_ORDER = [
    ProcessingStageName.VALIDATING,
    ProcessingStageName.EXTRACTING,
    ProcessingStageName.CHUNKING,
    ProcessingStageName.INDEXING,
    ProcessingStageName.EXTRACTING_CONCEPTS,
    ProcessingStageName.BUILDING_GRAPH,
    ProcessingStageName.GENERATING_STRUCTURE,
    ProcessingStageName.VALIDATING_COURSE,
]


class ProcessingJob(Base):
    """
    One run of the course processing pipeline.

    Run as an in-process background task this sprint rather than through
    Celery/Redis. The job and stage rows are what make progress real and
    resumable regardless of which executor runs them, so the durable record is
    identical to what a queued worker would write.
    """

    __tablename__ = "processing_jobs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    status = Column(String(32), nullable=False, default=JobStatus.PENDING.value, index=True)
    current_stage = Column(String(48), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)

    # Category only, never provider text or document content (AGENTS.md §1,
    # frozen-scope.md data-lifecycle rules on logging).
    error_category = Column(String(64), nullable=True)

    # T2 (Phase 6): unlike error_category, this MAY hold a message -- but
    # only ever one of NoExtractableText's own authored, human-facing
    # strings (e.g. "This PDF is password-protected...", "This PDF has 750
    # pages, over the 500-page limit..."). Never populated from a provider
    # exception or raw document content -- see jobs/service.py's
    # _run_stage(), which sets this in exactly one except branch.
    error_detail = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    stages = relationship(
        "ProcessingStage",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ProcessingStage.position",
    )


class ProcessingStage(Base):
    """One stage of one job. Idempotent: re-running replaces its own output."""

    __tablename__ = "processing_stages"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id = Column(Uuid, ForeignKey("processing_jobs.id"), nullable=False, index=True)

    name = Column(String(48), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default=StageStatus.PENDING.value)
    attempts = Column(Integer, nullable=False, default=0)
    error_category = Column(String(64), nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    job = relationship("ProcessingJob", back_populates="stages")
