from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.assessment.models import QuizAttempt
from app.modules.assessment.schemas import QuizAttemptIn, QuizAttemptOut
from app.modules.auth.models import User

router = APIRouter()


def grade(questions: List[dict], answers: List) -> List[bool]:
    """
    Score an attempt server-side.

    Answers are positional. A missing or unanswered question counts as wrong
    rather than being skipped, so `score / total` is always the fraction the
    learner actually got right.
    """
    results = []
    for index, question in enumerate(questions):
        given = answers[index] if index < len(answers) else None
        results.append(given is not None and given == question["correct_answer"])
    return results


@router.post("/quiz-attempts", response_model=QuizAttemptOut, status_code=201)
def submit_quiz_attempt(
    body: QuizAttemptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Persist a completed quiz against the authenticated user and score it."""
    if len(body.answers) > len(body.questions):
        raise HTTPException(
            status_code=400, detail="More answers submitted than questions asked."
        )

    questions = [q.model_dump() for q in body.questions]
    correct = grade(questions, body.answers)

    attempt = QuizAttempt(
        user_id=user.id,
        title=body.title,
        topic=body.topic,
        score=sum(correct),
        total_questions=len(questions),
        questions=questions,
        answers=list(body.answers),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return QuizAttemptOut(
        id=str(attempt.id),
        score=attempt.score,
        total_questions=attempt.total_questions,
        correct=correct,
    )


@router.get("/quiz-attempts")
def list_my_quiz_attempts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The caller's own attempts. Scoped by user id, never by a query param."""
    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == user.id)
        .order_by(QuizAttempt.submitted_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": str(a.id),
            "title": a.title,
            "topic": a.topic,
            "score": a.score,
            "total_questions": a.total_questions,
            "submitted_at": a.submitted_at,
        }
        for a in attempts
    ]
