"""EvaluationService: experiment/condition/cohort management. Ownership of
an Experiment is by whoever created it (a researcher account); a real
deployment would gate this behind an admin role, which doesn't exist yet in
this codebase (Phase 6's audit log has the same stated gap) -- this phase
requires authentication only, documented as a known scope limit."""
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.evaluation.models import CohortAssignment, Experiment, ExperimentCondition


class EvaluationNotFound(Exception):
    pass


class DuplicateAssignment(Exception):
    """A learner is already assigned to a condition in this experiment --
    this harness does not support crossover designs (models.py docstring)."""


class EvaluationService:
    def __init__(self, db: Session):
        self.db = db

    def create_experiment(
        self, owner_id: int, name: str, description: Optional[str],
        conditions: List[dict],  # [{"code": "B1", "description": "...", "config": {...}}, ...]
    ) -> Experiment:
        experiment = Experiment(owner_id=owner_id, name=name, description=description)
        self.db.add(experiment)
        self.db.flush()
        for c in conditions:
            self.db.add(
                ExperimentCondition(
                    experiment_id=experiment.id, code=c["code"], description=c["description"],
                    config=c.get("config", {}),
                )
            )
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def _get_owned_experiment(self, experiment_id: UUID, owner_id: int) -> Experiment:
        experiment = (
            self.db.query(Experiment)
            .filter(Experiment.id == experiment_id, Experiment.owner_id == owner_id)
            .first()
        )
        if experiment is None:
            raise EvaluationNotFound(str(experiment_id))
        return experiment

    def get_conditions(self, experiment_id: UUID, owner_id: int) -> List[ExperimentCondition]:
        self._get_owned_experiment(experiment_id, owner_id)
        return (
            self.db.query(ExperimentCondition)
            .filter(ExperimentCondition.experiment_id == experiment_id)
            .all()
        )

    def assign_to_condition(
        self, experiment_id: UUID, condition_code: str, learner_id: int, owner_id: int
    ) -> CohortAssignment:
        """`owner_id` is the experiment owner authorizing this call;
        `learner_id` is the (possibly different) user being assigned to a
        cohort -- an evaluation harness assigns other accounts to
        conditions, it doesn't just self-assign the researcher."""
        experiment = self._get_owned_experiment(experiment_id, owner_id)
        condition = (
            self.db.query(ExperimentCondition)
            .filter(ExperimentCondition.experiment_id == experiment.id, ExperimentCondition.code == condition_code)
            .first()
        )
        if condition is None:
            raise EvaluationNotFound(condition_code)

        existing = (
            self.db.query(CohortAssignment)
            .filter(CohortAssignment.experiment_id == experiment.id, CohortAssignment.owner_id == learner_id)
            .first()
        )
        if existing is not None:
            raise DuplicateAssignment(
                f"Learner {learner_id} is already assigned to condition {existing.condition_id} in this experiment."
            )

        assignment = CohortAssignment(experiment_id=experiment.id, condition_id=condition.id, owner_id=learner_id)
        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def get_learner_condition(self, experiment_id: UUID, learner_id: int) -> Optional[ExperimentCondition]:
        assignment = (
            self.db.query(CohortAssignment)
            .filter(CohortAssignment.experiment_id == experiment_id, CohortAssignment.owner_id == learner_id)
            .first()
        )
        if assignment is None:
            return None
        return (
            self.db.query(ExperimentCondition)
            .filter(ExperimentCondition.id == assignment.condition_id)
            .first()
        )
