from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.mastery.schemas import MasteryReportRow

PreparationStatusValue = Literal["PENDING", "RUNNING", "READY", "RECOVERABLE_FAILURE"]
PreparationStageValue = Literal["CONTENT", "QUESTIONS", "COMPLETE"]
PresentationFormat = Literal[
    "concise", "detailed", "worked_example", "analogy", "diagram", "source_view", "quiz_first"
]


class PreparationOut(BaseModel):
    id: UUID
    status: PreparationStatusValue
    stage: PreparationStageValue
    progress: int = Field(ge=0, le=100)
    error_category: Optional[str] = None
    content_ready: bool
    assessment_ready: bool
    updated_at: datetime


class LearningActivityOut(BaseModel):
    id: UUID
    course_version_id: UUID
    decision_id: Optional[UUID]
    activity_type: str
    experience_availability: Literal["SUPPORTED", "UNAVAILABLE"]
    unavailable_reason: Optional[str] = None
    target_concept_ids: list[UUID]
    lesson_id: Optional[UUID]
    reason: Optional[str]
    status: str
    presentation_format: str
    question_count: int = Field(ge=0)
    reading_position: int
    reading_completed_at: Optional[datetime]
    assessment_session_id: Optional[UUID] = None
    preparation: Optional[PreparationOut] = None


class PreparedStatementOut(BaseModel):
    text: str
    concept_ids: list[UUID]
    citation_chunk_ids: list[UUID]


class PreparedLessonSectionsOut(BaseModel):
    objective: list[PreparedStatementOut]
    explanation: list[PreparedStatementOut]
    example: list[PreparedStatementOut]
    recap: list[PreparedStatementOut]


class PreparedLessonContentOut(BaseModel):
    artifact_id: UUID
    course_version_id: UUID
    lesson_id: Optional[UUID] = None
    presentation_format: PresentationFormat
    sections: PreparedLessonSectionsOut
    source_chunk_ids: list[UUID]


class ActivityContentResponseOut(BaseModel):
    status: PreparationStatusValue
    preparation: Optional[PreparationOut] = None
    content: Optional[PreparedLessonContentOut] = None


class ActivityProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_position: Optional[int] = Field(default=None, ge=0)
    presentation_format: Optional[PresentationFormat] = None


class AnswerOut(BaseModel):
    given_answer: Any
    status: str
    submitted_at: datetime


class QuestionResultOut(BaseModel):
    correctness: Optional[float] = None
    expected_answer: Any = None
    rubric: Optional[list[str]] = None
    explanation: Optional[str] = None
    source_chunk_ids: Optional[list[UUID]] = None


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


class AssessmentConceptProgressOut(BaseModel):
    concept_id: UUID
    concept_name: str
    before_band: str
    after_band: str
    before_evidence_strength: str
    after_evidence_strength: str


class AssessmentSessionOut(BaseModel):
    id: UUID
    activity_id: UUID
    assessment_type: str
    submission_state: str
    grading_state: str
    submitted_at: Optional[datetime]
    questions: list[AssessmentQuestionOut]
    graded_answer_count: int
    unresolved_answer_count: int
    concept_progress_reference_at: Optional[datetime] = None
    concept_progress: Optional[list[AssessmentConceptProgressOut]] = None


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
