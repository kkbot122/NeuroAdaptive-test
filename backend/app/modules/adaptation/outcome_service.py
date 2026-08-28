"""
AdaptationOutcomeService: records what actually happened after a
recommendation was served, linked back to the AdaptationDecision that
served it, and answers the "what/why/what happened" analytics view.

GUARDRAIL, enforced structurally here (not just by convention): engagement
outcomes (VIEWED/COMPLETED/ABANDONED/REJECTED) never receive a
mastery_delta, transfer_success, or hint/time delta -- only
record_assessment_outcome (which requires real QuestionAttempt evidence)
populates those fields. There is no code path that lets a caller claim a
pedagogical effect from an engagement-only signal.
"""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.adaptation.models import (
    SIGNAL_CATEGORY_BY_OUTCOME_TYPE,
    AdaptationDecision,
    AdaptationOutcome,
    OutcomeType,
)
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.mastery.models import QuestionAttempt
from app.modules.mastery.service import MasteryService

ENGAGEMENT_OUTCOME_TYPES = {
    OutcomeType.VIEWED, OutcomeType.COMPLETED, OutcomeType.ABANDONED, OutcomeType.REJECTED,
}


class AdaptationOutcomeNotFound(Exception):
    """Decision (or referenced attempt) not found, or not owned by the caller."""


class InvalidOutcome(Exception):
    pass


class AdaptationOutcomeService:
    def __init__(self, db: Session, mastery: Optional[MasteryService] = None):
        self.db = db
        self.courses = CourseService(db)
        self.mastery = mastery  # only required by record_assessment_outcome

    def _get_owned_decision(self, decision_id: UUID, owner_id: int) -> AdaptationDecision:
        decision = (
            self.db.query(AdaptationDecision)
            .filter(AdaptationDecision.id == decision_id, AdaptationDecision.owner_id == owner_id)
            .first()
        )
        if decision is None:
            raise AdaptationOutcomeNotFound(str(decision_id))
        return decision

    # -- engagement (never carries a pedagogical-effect field) -----------------

    def record_engagement(
        self, decision_id: UUID, owner_id: int, outcome_type: str, extra: Optional[dict] = None
    ) -> AdaptationOutcome:
        if outcome_type not in {t.value for t in ENGAGEMENT_OUTCOME_TYPES}:
            raise InvalidOutcome(f"{outcome_type} is not an engagement outcome type.")
        decision = self._get_owned_decision(decision_id, owner_id)
        outcome = AdaptationOutcome(
            decision_id=decision.id,
            owner_id=owner_id,
            outcome_type=outcome_type,
            signal_category=SIGNAL_CATEGORY_BY_OUTCOME_TYPE[OutcomeType(outcome_type)].value,
            concept_id=decision.selected_concept_id,
            extra=extra,
        )
        self.db.add(outcome)
        self.db.commit()
        self.db.refresh(outcome)
        return outcome

    # -- self-reported ----------------------------------------------------------

    def record_helpfulness_feedback(self, decision_id: UUID, owner_id: int, rating: int) -> AdaptationOutcome:
        if rating not in (-1, 0, 1):
            raise InvalidOutcome("helpfulness rating must be -1, 0, or 1.")
        decision = self._get_owned_decision(decision_id, owner_id)
        outcome = AdaptationOutcome(
            decision_id=decision.id,
            owner_id=owner_id,
            outcome_type=OutcomeType.HELPFULNESS_FEEDBACK.value,
            signal_category=SIGNAL_CATEGORY_BY_OUTCOME_TYPE[OutcomeType.HELPFULNESS_FEEDBACK].value,
            concept_id=decision.selected_concept_id,
            helpfulness_rating=rating,
        )
        self.db.add(outcome)
        self.db.commit()
        self.db.refresh(outcome)
        return outcome

    # -- pedagogical effect (the only path that can set these fields) ----------

    def record_assessment_outcome(
        self,
        decision_id: UUID,
        owner_id: int,
        question_attempt_id: UUID,
        is_transfer_question: bool = False,
        baseline_question_attempt_id: Optional[UUID] = None,
    ) -> AdaptationOutcome:
        """
        mastery_delta is computed, never caller-supplied: it is the
        difference between the concept's mastery right now
        (MasteryService.get_concept_mastery) and the mastery value already
        frozen in the decision's own input_snapshot at recommendation time
        (Phase 4's AdaptationDecision.input_snapshot["concept_mastery"]) --
        reusing Phase 4's own recorded state rather than needing a second,
        separate "before" snapshot mechanism.
        """
        if self.mastery is None:
            raise InvalidOutcome("record_assessment_outcome requires a MasteryService.")

        decision = self._get_owned_decision(decision_id, owner_id)
        concept_id = decision.selected_concept_id
        if concept_id is None:
            raise InvalidOutcome("This decision has no target concept to assess an outcome against.")

        attempt = (
            self.db.query(QuestionAttempt)
            .filter(QuestionAttempt.id == question_attempt_id, QuestionAttempt.owner_id == owner_id)
            .first()
        )
        if attempt is None:
            raise AdaptationOutcomeNotFound(str(question_attempt_id))

        snapshot_mastery = (decision.input_snapshot or {}).get("concept_mastery", {}).get(str(concept_id))
        current_state = self.mastery.get_concept_mastery(owner_id, concept_id)
        mastery_delta = (current_state.mastery - snapshot_mastery) if snapshot_mastery is not None else None

        transfer_success = int(attempt.correctness >= 0.5) if is_transfer_question else None

        hint_usage_delta = None
        time_to_correct_delta = None
        if baseline_question_attempt_id is not None:
            baseline = (
                self.db.query(QuestionAttempt)
                .filter(QuestionAttempt.id == baseline_question_attempt_id, QuestionAttempt.owner_id == owner_id)
                .first()
            )
            if baseline is not None:
                hint_usage_delta = attempt.hints_used - baseline.hints_used
                if attempt.time_taken_seconds is not None and baseline.time_taken_seconds is not None:
                    time_to_correct_delta = attempt.time_taken_seconds - baseline.time_taken_seconds

        outcome_type = OutcomeType.TRANSFER_SUCCESS if is_transfer_question else OutcomeType.ASSESSED
        outcome = AdaptationOutcome(
            decision_id=decision.id,
            owner_id=owner_id,
            outcome_type=outcome_type.value,
            signal_category=SIGNAL_CATEGORY_BY_OUTCOME_TYPE[outcome_type].value,
            concept_id=concept_id,
            mastery_delta=mastery_delta,
            transfer_success=transfer_success,
            hint_usage_delta=hint_usage_delta,
            time_to_correct_delta=time_to_correct_delta,
            question_attempt_id=attempt.id,
            baseline_question_attempt_id=baseline_question_attempt_id,
        )
        self.db.add(outcome)
        self.db.commit()
        self.db.refresh(outcome)
        return outcome

    # -- analytics: what / why / what happened ----------------------------------

    def get_adaptation_history(self, course_id: UUID, owner_id: int) -> List[dict]:
        try:
            self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise AdaptationOutcomeNotFound(str(course_id))

        decisions = (
            self.db.query(AdaptationDecision)
            .filter(AdaptationDecision.course_id == course_id, AdaptationDecision.owner_id == owner_id)
            .order_by(AdaptationDecision.created_at.asc())
            .all()
        )

        history = []
        for decision in decisions:
            outcomes = (
                self.db.query(AdaptationOutcome)
                .filter(AdaptationOutcome.decision_id == decision.id)
                .order_by(AdaptationOutcome.created_at.asc())
                .all()
            )
            history.append({
                "decision_id": str(decision.id),
                "created_at": decision.created_at,
                # 1. What was recommended?
                "recommended": {
                    "activity_type": decision.selected_activity_type,
                    "concept_id": str(decision.selected_concept_id) if decision.selected_concept_id else None,
                    "lesson_id": str(decision.selected_lesson_id) if decision.selected_lesson_id else None,
                },
                # 2. Why was it recommended?
                "reason": decision.reason_text,
                "input_snapshot": decision.input_snapshot,
                "policy_version": decision.policy_version,
                # 3-7: what happened afterward, per outcome.
                "outcomes": [
                    {
                        "outcome_id": str(o.id),
                        "outcome_type": o.outcome_type,
                        "signal_category": o.signal_category,
                        "concept_id": str(o.concept_id) if o.concept_id else None,
                        "mastery_delta": o.mastery_delta,
                        "transfer_success": bool(o.transfer_success) if o.transfer_success is not None else None,
                        "hint_usage_delta": o.hint_usage_delta,
                        "time_to_correct_delta": o.time_to_correct_delta,
                        "helpfulness_rating": o.helpfulness_rating,
                        "created_at": o.created_at,
                    }
                    for o in outcomes
                ],
            })
        return history
