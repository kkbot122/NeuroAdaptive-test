from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.mastery.schemas import MasteryReportRow


class LearningActivityOut(BaseModel):
    id: UUID
    course_version_id: UUID
    decision_id: Optional[UUID]
    activity_type: str
    target_concept_ids: list[UUID]
    lesson_id: Optional[UUID]
    reason: Optional[str]
    status: str
    presentation_format: str
    reading_position: int
    reading_completed_at: Optional[datetime]
    assessment_session_id: Optional[UUID] = None


class ActivityProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_position: Optional[int] = Field(default=None, ge=0)
    presentation_format: Optional[
        Literal["concise", "detailed", "worked_example", "analogy", "diagram", "source_view", "quiz_first"]
    ] = None


class AnswerOut(BaseModel):
    given_answer: Any
    status: str
    submitted_at: datetime


class QuestionResultOut(BaseModel):
    correctness: Optional[float] = None
    expected_answer: Any = None
    rubric: Optional[list[str]] = None


class AssessmentQuestionOut(BaseModel):
    question_id: UUID
    question_version: int
    position: int
    question_type: str
    prompt: str
    options: Optional[list[str]]
    difficulty: float
    answer: Optional[AnswerOut] = None
    result: Optional[QuestionResultOut] = None


class AssessmentSessionOut(BaseModel):
    id: UUID
    activity_id: UUID
    assessment_type: str
    submission_state: str
    grading_state: str
    submitted_at: Optional[datetime]
    questions: list[AssessmentQuestionOut]


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    given_answer: Any


class LessonCoverageOut(BaseModel):
    course_version_id: UUID
    lessons_total: int
    lessons_covered: int
    covered_lesson_ids: list[UUID]


class LearningStateOut(BaseModel):
    active_activity: Optional[LearningActivityOut]
    lesson_coverage: LessonCoverageOut
    concept_understanding: list[MasteryReportRow]
