"""
Mastery/assessment API surface.

Route naming follows architecture.md's `/courses/{courseId}/...` nesting
convention (same one curriculum/router.py's docstring already reconciles
Phase 2 against) rather than inventing a separate top-level resource.
"""
from app.services.providers import generation_gateway, embedding_gateway, vector_store
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.rate_limit import generation_slot
from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.abuse.service import AbuseControlService
from app.modules.auth.models import User
from app.modules.mastery.grading import GradingError
from app.modules.mastery.schemas import (
    AttemptOut,
    AttemptRequest,
    DiagnosticRequest,
    MasteryReportRow,
    QuestionOut,
)
from app.modules.mastery.service import MasteryNotFound, MasteryService
from app.modules.learning.schemas import AssessmentSessionOut
from app.modules.learning.service import (
    AssessmentUnavailable,
    LearningConflict,
    LearningNotFound,
    LearningService,
)
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.generation.gemini import GeminiGenerationGateway

router = APIRouter()

MAX_CONCURRENT_GENERATIONS_PER_USER = 2


def _service(db: Session = Depends(get_db)) -> MasteryService:
    # Lazy clients: constructing these per request touches no network until
    # a route actually generates or grades (curriculum/router.py's pattern).
    return MasteryService(db, generation_gateway(), embedding_gateway())


def _learning_service(db: Session = Depends(get_db)) -> LearningService:
    return LearningService(db, generation_gateway(), embedding_gateway())


@router.post(
    "/courses/{course_id}/diagnostic",
    response_model=AssessmentSessionOut,
    response_model_exclude_none=True,
)
def generate_diagnostic(
    course_id: UUID,
    body: DiagnosticRequest = DiagnosticRequest(),
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_learning_service),
    db: Session = Depends(get_db),
):
    """
    Skipping the diagnostic is not a separate action: a learner who never
    calls this route, or never answers the questions it returns, leaves
    every concept at the honest "Not assessed" prior (engine.py) -- there is
    no fabricated baseline to opt out of.
    """
    try:
        resumed = service.resume_diagnostic(course_id, user.id)
        if resumed is not None:
            return resumed
        AbuseControlService(db).enforce_generation_request_controls(user.id)
        with generation_slot(f"user:{user.id}", MAX_CONCURRENT_GENERATIONS_PER_USER):
            return service.start_diagnostic(course_id, user.id, body.max_questions)
    except (LearningNotFound, MasteryNotFound):
        raise HTTPException(status_code=404, detail="Course not found")
    except (LearningConflict, AssessmentUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/questions/{question_id}/attempts", response_model=AttemptOut, status_code=201)
def submit_attempt(
    question_id: UUID,
    body: AttemptRequest,
    user: User = Depends(get_current_user),
    service: MasteryService = Depends(_service),
):
    try:
        attempt = service.submit_attempt(
            question_id,
            user.id,
            body.given_answer,
            hints_used=body.hints_used,
            retry_index=body.retry_index,
            time_taken_seconds=body.time_taken_seconds,
            confidence=body.confidence,
        )
    except MasteryNotFound:
        raise HTTPException(status_code=404, detail="Question not found")
    except GradingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return AttemptOut(id=str(attempt.id), question_id=str(attempt.question_id), correctness=attempt.correctness)


@router.get("/courses/{course_id}/mastery-report", response_model=List[MasteryReportRow])
def get_mastery_report(
    course_id: UUID,
    include_raw: bool = False,
    user: User = Depends(get_current_user),
    service: MasteryService = Depends(_service),
):
    """Qualitative bands are the primary shape; raw mastery/uncertainty are
    only attached, under `raw`, when include_raw is explicitly set."""
    try:
        report = service.get_mastery_report(course_id, user.id, include_raw=include_raw)
    except MasteryNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    return report
