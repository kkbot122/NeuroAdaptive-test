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


class OutcomeType(str, enum.Enum):
    """Engagement (VIEWED/COMPLETED/ABANDONED/REJECTED), pedagogical-effect
    (ASSESSED/TRANSFER_SUCCESS), and self-reported (HELPFULNESS_FEEDBACK)
    are three DIFFERENT kinds of evidence, not one "did it work" scale --
    see SIGNAL_CATEGORY_BY_OUTCOME_TYPE and the guardrail in
    outcome_service.py. A learner can feel helped without measurably
    improving, and vice versa; engagement alone is never pedagogical
    evidence (Phase 7 mandate's central guardrail)."""

    VIEWED = "VIEWED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    REJECTED = "REJECTED"  # learner picked a different alternative
    ASSESSED = "ASSESSED"  # a subsequent question on the same concept was answered
    TRANSFER_SUCCESS = "TRANSFER_SUCCESS"  # a different question, same concept, new context
    HELPFULNESS_FEEDBACK = "HELPFULNESS_FEEDBACK"


class SignalCategory(str, enum.Enum):
    ENGAGEMENT = "ENGAGEMENT"
    PEDAGOGICAL_EFFECT = "PEDAGOGICAL_EFFECT"
    SELF_REPORTED = "SELF_REPORTED"


SIGNAL_CATEGORY_BY_OUTCOME_TYPE = {
    OutcomeType.VIEWED: SignalCategory.ENGAGEMENT,
    OutcomeType.COMPLETED: SignalCategory.ENGAGEMENT,
    OutcomeType.ABANDONED: SignalCategory.ENGAGEMENT,
    OutcomeType.REJECTED: SignalCategory.ENGAGEMENT,
    OutcomeType.ASSESSED: SignalCategory.PEDAGOGICAL_EFFECT,
    OutcomeType.TRANSFER_SUCCESS: SignalCategory.PEDAGOGICAL_EFFECT,
    OutcomeType.HELPFULNESS_FEEDBACK: SignalCategory.SELF_REPORTED,
}


class AdaptationOutcome(Base):
    """
    What actually happened after a recommendation was served -- a SEPARATE
    table from AdaptationDecision (never merged; SYSTEM_ARCHITECTURE.md S8),
    linked back by decision_id. One decision can have many outcomes over
    time (viewed, then later completed, then later still assessed) -- this
    is an append-only evidence log, not a single mutable "result" field on
    the decision.

    signal_category is stored, not just derivable from outcome_type in
    Python, so an aggregation query can filter engagement out of a
    pedagogical-effect metric at the SQL layer without trusting every
    caller to apply the same Python-side mapping correctly (mandate
    guardrail: never average these into one "success" number).
    """

    __tablename__ = "adaptation_outcomes"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    decision_id = Column(Uuid, ForeignKey("adaptation_decisions.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    outcome_type = Column(String(32), nullable=False)
    signal_category = Column(String(32), nullable=False)

    concept_id = Column(Uuid, nullable=True)  # the concept this outcome concerns, when applicable

    # Pedagogical-effect fields. Null unless outcome_type actually computes them.
    mastery_delta = Column(Float, nullable=True)
    transfer_success = Column(Integer, nullable=True)  # bool-as-int (SQLite portability, matches Question.is_diagnostic)
    hint_usage_delta = Column(Float, nullable=True)
    time_to_correct_delta = Column(Float, nullable=True)

    # Self-reported.
    helpfulness_rating = Column(Integer, nullable=True)  # three-state: -1 / 0 / 1

    # The evidence a pedagogical-effect outcome was computed from.
    question_attempt_id = Column(Uuid, ForeignKey("question_attempts.id"), nullable=True)
    baseline_question_attempt_id = Column(Uuid, ForeignKey("question_attempts.id"), nullable=True)

    extra = Column(JSON, nullable=True)
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
