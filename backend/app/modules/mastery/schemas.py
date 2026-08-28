from typing import List, Optional

from pydantic import BaseModel, Field


class DiagnosticRequest(BaseModel):
    max_questions: Optional[int] = Field(default=None, gt=0)


class QuestionOut(BaseModel):
    """Learner-facing question shape. Deliberately excludes correct_answer
    and rubric -- those never leave the server before grading."""

    id: str
    question_type: str
    prompt: str
    options: Optional[List[str]] = None
    difficulty: float


class AttemptRequest(BaseModel):
    given_answer: object = None
    hints_used: int = Field(default=0, ge=0)
    retry_index: int = Field(default=0, ge=0)
    time_taken_seconds: Optional[float] = Field(default=None, ge=0)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class AttemptOut(BaseModel):
    id: str
    question_id: str
    correctness: float


class MasteryReportRow(BaseModel):
    concept_id: str
    concept_name: str
    band: str
    raw: Optional[dict] = None
