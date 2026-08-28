"""
Phase 8 evaluation infrastructure: Experiment/ExperimentCondition/
CohortAssignment. This is the harness a real pilot would run through --
no row in these tables is ever written from fabricated data by a
production code path (see tests/evaluation/test_no_fabrication_guardrail.py
and scripts/check_no_fabricated_results.py).

A condition's `config` JSON is what makes B1/B3/A1 genuine toggles on the
real system rather than parallel reimplementations:
  - B1 (fixed-sequence): {"adaptive": false} -- the recommendation
    endpoint, when this is set, returns the next not-yet-mastered lesson in
    course order instead of calling scoring.recommend().
  - B2 (real system): {} -- no override, the actual adaptive pipeline.
  - B3 (no citation validation): {"citation_validation_enabled": false} --
    TutorService.ask()'s own real `citation_validation_enabled` parameter,
    not a second tutor implementation.
  - A1 (no presentation-affinity term): {"policy_overrides": {"w_presentation_fit": 0.0}}
    -- a PolicyWeights override passed into the real scoring.recommend().
  - A2 (BKT vs weighted-evidence): {"mastery_model": "bkt"} -- read by the
    evaluation harness's metric computation, not by the live mastery engine
    itself (Phase 3's engine is not swapped in production; A2 is a
    comparison run over already-logged attempt data, per the mandate: "not
    a conclusion about which wins").

B4 (FSLSM-vector baseline reimplementation) is declared out of scope this
phase -- optional per the mandate ("only if practical"), and reimplementing
a second paper's whole prompt-conditioning mechanism for a like-for-like
comparison is a substantially larger undertaking than a config toggle.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.sql import func

from app.db.base import Base


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(String(2000), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ExperimentCondition(Base):
    """One named condition within an experiment (B1/B2/B3/A1/A2/...). `code`
    is the short label the mandate uses; `config` is the real toggle."""

    __tablename__ = "experiment_conditions"
    __table_args__ = (UniqueConstraint("experiment_id", "code", name="uq_experiment_condition_code"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(Uuid, ForeignKey("experiments.id"), nullable=False, index=True)
    code = Column(String(16), nullable=False)  # "B1", "B2", "B3", "A1", "A2"
    description = Column(String(500), nullable=False)
    config = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CohortAssignment(Base):
    """
    One learner's assignment to one condition within one experiment.
    Uniquely constrained on (experiment_id, owner_id): a learner appears in
    exactly one condition per experiment -- this harness does not support
    crossover designs. A study needing crossover would need an explicit
    schema change here, not a workaround; documented as an honest scope
    limit, not silently possible.
    """

    __tablename__ = "cohort_assignments"
    __table_args__ = (UniqueConstraint("experiment_id", "owner_id", name="uq_cohort_assignment_learner"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(Uuid, ForeignKey("experiments.id"), nullable=False, index=True)
    condition_id = Column(Uuid, ForeignKey("experiment_conditions.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    assigned_at = Column(DateTime(timezone=True), server_default=func.now())
