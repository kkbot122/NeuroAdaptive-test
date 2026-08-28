"""
Evaluation harness API surface. Authentication only, no admin-role gate --
this codebase has no admin role yet (the same stated gap Phase 6's audit
log has); a real deployment running an actual pilot would add one before
exposing this beyond a researcher's own use.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.evaluation.service import DuplicateAssignment, EvaluationNotFound, EvaluationService

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> EvaluationService:
    return EvaluationService(db)


class ConditionIn(BaseModel):
    code: str
    description: str
    config: dict = {}


class ExperimentCreateIn(BaseModel):
    name: str
    description: Optional[str] = None
    conditions: List[ConditionIn]


class AssignIn(BaseModel):
    learner_email: str
    condition_code: str


@router.post("/evaluation/experiments", status_code=201)
def create_experiment(
    body: ExperimentCreateIn,
    user: User = Depends(get_current_user),
    service: EvaluationService = Depends(_service),
    db: Session = Depends(get_db),
):
    experiment = service.create_experiment(
        user.id, body.name, body.description, [c.model_dump() for c in body.conditions]
    )
    return {"id": str(experiment.id), "name": experiment.name}


@router.get("/evaluation/experiments/{experiment_id}/conditions")
def list_conditions(
    experiment_id: UUID,
    user: User = Depends(get_current_user),
    service: EvaluationService = Depends(_service),
):
    try:
        conditions = service.get_conditions(experiment_id, user.id)
    except EvaluationNotFound:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return [{"code": c.code, "description": c.description, "config": c.config} for c in conditions]


@router.post("/evaluation/experiments/{experiment_id}/assign")
def assign_learner(
    experiment_id: UUID,
    body: AssignIn,
    user: User = Depends(get_current_user),
    service: EvaluationService = Depends(_service),
    db: Session = Depends(get_db),
):
    learner = db.query(User).filter(User.email == body.learner_email).first()
    if learner is None:
        raise HTTPException(status_code=404, detail="Learner not found")
    try:
        assignment = service.assign_to_condition(experiment_id, body.condition_code, learner.id, user.id)
    except EvaluationNotFound:
        raise HTTPException(status_code=404, detail="Experiment or condition not found")
    except DuplicateAssignment as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"assignment_id": str(assignment.id), "condition_id": str(assignment.condition_id)}


@router.get("/evaluation/experiments/{experiment_id}/my-condition")
def get_my_condition(
    experiment_id: UUID,
    user: User = Depends(get_current_user),
    service: EvaluationService = Depends(_service),
):
    """A learner checking which condition they're in for an experiment they
    were assigned to -- not gated to the experiment's owner, since the
    caller is only ever reading their own assignment (user.id, never a
    query param)."""
    condition = service.get_learner_condition(experiment_id, user.id)
    if condition is None:
        return {"assigned": False}
    return {"assigned": True, "code": condition.code, "config": condition.config}
