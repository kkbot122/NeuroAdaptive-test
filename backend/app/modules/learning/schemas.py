from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

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


class PreparedDiagramEdgeOut(PreparedStatementOut):
    from_index: int = Field(ge=0, le=11)
    to_index: int = Field(ge=0, le=11)


class PreparedLessonSectionsOut(BaseModel):
    objective: list[PreparedStatementOut]
    explanation: list[PreparedStatementOut]
    example: list[PreparedStatementOut]
    recap: list[PreparedStatementOut]
    diagram_edges: list[PreparedDiagramEdgeOut] = Field(default_factory=list)


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
    grading_failure: Optional[Literal["GRADING_UNAVAILABLE", "ALLOWANCE_UNAVAILABLE", "RETRIES_EXHAUSTED"]] = None
    retry_available: bool = False


class RubricFeedbackOut(BaseModel):
    criterion: str
    met: bool
    expected_reasoning: str
    source_chunk_ids: list[UUID]


class QuestionResultOut(BaseModel):
    correctness: Optional[float] = None
    expected_answer: Any = None
    rubric: Optional[list[str]] = None
    rubric_passing_criteria: Optional[int] = None
    explanation: Optional[str] = None
    source_chunk_ids: Optional[list[UUID]] = None
    expected_reasoning: Optional[str] = None
    rubric_score: Optional[int] = None
    rubric_feedback: Optional[list[RubricFeedbackOut]] = None
    automated_grading: bool = False
    grade_corrected: bool = False
    correction_reason: Optional[str] = None
    original_correctness: Optional[float] = None
    original_rubric_score: Optional[int] = None
    original_rubric_feedback: Optional[list[RubricFeedbackOut]] = None


class GradingIssueReportOut(BaseModel):
    id: UUID
    status: str
    received: bool = True
    created_at: datetime


class GradingIssueReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    report_text: str = Field(min_length=1, max_length=2000)


class GradingReviewReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2000)


class GradingCorrectionIn(GradingReviewReasonIn):
    criteria_met: list[StrictBool] = Field(min_length=1, max_length=5)
    expected_correction_version: int = Field(ge=0)


class GradingReviewSourceOut(BaseModel):
    chunk_id: UUID
    heading_path: Optional[str]
    text: str


class GradingCorrectionOut(BaseModel):
    version: int
    criteria_met: list[bool]
    rubric_score: int
    effective_correctness: int
    reason: str
    created_at: datetime


class GradingReviewEventOut(BaseModel):
    event_type: str
    reason: str
    correction_version: Optional[int]
    created_at: datetime


class GradingReviewItemOut(BaseModel):
    id: UUID
    course_id: UUID
    status: str
    report_text: str
    created_at: datetime
    answer: str
    question_id: UUID
    question_version: int
    prompt: str
    rubric: list[str]
    rubric_passing_criteria: int
    expected_reasoning: str
    original_criteria_met: list[bool]
    original_rubric_score: int
    original_evidence_correctness: int
    sources: list[GradingReviewSourceOut]
    corrections: list[GradingCorrectionOut]
    history: list[GradingReviewEventOut]
    latest_effective_criteria_met: list[bool]
    latest_effective_correctness: int
    latest_correction_version: int


class GradingReviewPageOut(BaseModel):
    items: list[GradingReviewItemOut]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool
    next_offset: Optional[int] = Field(default=None, ge=0)


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
    grading_issue_report: Optional[GradingIssueReportOut] = None


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
    decision_id: Optional[UUID] = None
    lesson_id: Optional[UUID] = None
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
