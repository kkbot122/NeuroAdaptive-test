"""
Adaptive-sequencing persistence: AdaptationDecision and PresentationAffinity.

AdaptationDecision is deliberately a SEPARATE table from any future
AdaptationOutcome (Phase 7) -- SYSTEM_ARCHITECTURE.md S8 calls this split
"the single most important structural choice" and forbids ever merging them.
This phase does not build AdaptationOutcome; it only shapes this table so a
later `outcome` row can link back to `decision_id` without a migration that
touches this one (Phase 4 pack's own "what not to claim").

PresentationAffinity is per (owner_id, format) -- an empirical, continuously
revisable statistic, never a label. Nothing here or anywhere downstream may
render a fixed "you are a ___ learner" string; see guardrail tests.
"""
import enum
import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.sql import func

from app.db.base import Base


class PresentationFormat(str, enum.Enum):
    CONCISE = "concise"
    DETAILED = "detailed"
    WORKED_EXAMPLE = "worked_example"
    ANALOGY = "analogy"
    DIAGRAM = "diagram"
    SOURCE_VIEW = "source_view"
    QUIZ_FIRST = "quiz_first"


class ActivityType(str, enum.Enum):
    NEW_LESSON = "NEW_LESSON"
    PREREQUISITE_REMEDIATION = "PREREQUISITE_REMEDIATION"
    TARGETED_PRACTICE = "TARGETED_PRACTICE"
    CHALLENGE = "CHALLENGE"
    RESUME_INTERRUPTED = "RESUME_INTERRUPTED"


class AdaptationDecision(Base):
    """
    Written BEFORE the recommendation is returned to any caller (mandate
    section D). `candidates_considered` holds every scored candidate, not
    only the winner, so the protected trace can answer "what else could have
    been recommended and why wasn't it" later.
    """

    __tablename__ = "adaptation_decisions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)

    selected_activity_type = Column(String(32), nullable=False)
    selected_concept_id = Column(Uuid, nullable=True)
    selected_lesson_id = Column(Uuid, nullable=True)
    reason_text = Column(String(500), nullable=False)

    # Every candidate's type/target/features/score -- the full trace, not
    # just the winner. List[dict], JSON-serializable.
    candidates_considered = Column(JSON, nullable=False)

    policy_version = Column(String(32), nullable=False)
    input_snapshot = Column(JSON, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PresentationAffinity(Base):
    """One row per (owner, format). effectiveness starts at the documented
    prior (0.5) and only ever moves via engine.py's EMA update -- never set
    directly to reflect a label."""

    __tablename__ = "presentation_affinities"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    format = Column(String(32), nullable=False)

    exposure_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    effectiveness = Column(Float, nullable=False, default=0.5)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
