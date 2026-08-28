"""
Assessment and mastery-evidence models.

Mastery is per (owner_id, concept_id), never a single global learner score
(Phase 3 mandate #8) -- there is deliberately no "user mastery" row anywhere,
only ConceptMastery rows scoped to one concept each. It is also independent
from presentation affinity (Phase 4): nothing in this module reads or writes
anything format-related.

Questions are versioned and immutable once published (mandate #4): "fixing"
a question never mutates the row in place, it creates a new Question with
version = old.version + 1 and supersedes_question_id set. QuestionAttempt
snapshots `question_version` at submission time, so a later supersession can
never retroactively change what an old attempt was scored against.

Full per-attempt evidence history is persisted as MasteryEvent rows rather
than a running (S, W) total, because recency decay is applied at READ time
(engine.py), not baked into a stored running sum -- see engine.py's
module docstring for why, and Phase 8's replay requirement (mandate: "later
phases and the Phase 8 evaluation harness need to replay it").
"""
import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
)
from sqlalchemy.sql import func

from app.db.base import Base


class QuestionType(str, enum.Enum):
    MCQ = "MCQ"
    MULTI_SELECT = "MULTI_SELECT"
    SHORT_TEXT = "SHORT_TEXT"
    NUMERIC = "NUMERIC"


class Question(Base):
    """
    One version of one question. `correct_answer` shape depends on
    question_type:
      MCQ           -> str (the correct option)
      MULTI_SELECT  -> list[str] (the correct option set)
      SHORT_TEXT    -> null (graded against `rubric` by an LLM, not by
                       string-matching a single "ideal answer")
      NUMERIC       -> {"value": float, "tolerance": float}
    """

    __tablename__ = "questions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    question_type = Column(String(16), nullable=False)
    prompt = Column(Text, nullable=False)
    options = Column(JSON, nullable=True)  # list[str], MCQ/MULTI_SELECT only
    correct_answer = Column(JSON, nullable=True)
    rubric = Column(JSON, nullable=True)  # list[str] criteria, SHORT_TEXT only

    difficulty = Column(Float, nullable=False, default=0.5)  # [0, 1], unvalidated default
    is_diagnostic = Column(Integer, nullable=False, default=0)  # bool-as-int: SQLite portability

    version = Column(Integer, nullable=False, default=1)
    supersedes_question_id = Column(Uuid, ForeignKey("questions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class QuestionConcept(Base):
    """Question -> concept mapping. Weights sum to 1 per question (enforced
    by the service, not the schema) so a multi-concept question's evidence
    splits proportionally rather than counting fully against every concept
    it touches."""

    __tablename__ = "question_concepts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    question_id = Column(Uuid, ForeignKey("questions.id"), nullable=False, index=True)
    concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)

    weight = Column(Float, nullable=False, default=1.0)  # (0, 1]


class QuestionAttempt(Base):
    """
    One graded answer. `question_version` is a snapshot, not a live
    reference -- it never changes even if `question_id` is later superseded,
    so historical scoring stays reproducible (mandate #4, #12 test).
    """

    __tablename__ = "question_attempts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    question_id = Column(Uuid, ForeignKey("questions.id"), nullable=False, index=True)
    question_version = Column(Integer, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)

    given_answer = Column(JSON, nullable=True)
    correctness = Column(Float, nullable=False)  # partial credit in [0, 1]

    time_taken_seconds = Column(Float, nullable=True)
    hints_used = Column(Integer, nullable=False, default=0)
    retry_index = Column(Integer, nullable=False, default=0)  # 0 = first attempt
    self_reported_confidence = Column(Float, nullable=True)  # [0, 1]

    submitted_at = Column(DateTime(timezone=True), server_default=func.now())


class MasteryEvent(Base):
    """
    One piece of evidence toward one concept's mastery. Permanent and never
    rewritten -- current mastery is always recomputed from the full history
    (engine.compute_mastery), not from a mutated running total.
    """

    __tablename__ = "mastery_events"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    question_attempt_id = Column(Uuid, ForeignKey("question_attempts.id"), nullable=True)

    correctness = Column(Float, nullable=False)  # o_i in [0, 1]
    evidence_weight_base = Column(Float, nullable=False)  # w_i excluding recency

    created_at = Column(DateTime(timezone=True), server_default=func.now())
