from typing import List, Optional

from pydantic import BaseModel, Field

MAX_QUESTIONS = 50


class QuizQuestionIn(BaseModel):
    question: str = Field(min_length=1)
    topic: Optional[str] = None
    difficulty: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    correct_answer: str
    explanation: Optional[str] = None


class QuizAttemptIn(BaseModel):
    """
    What the learner was shown and what they chose.

    Note there is no `score` field. The client does not get to report its own
    result; the server derives it from questions + answers.
    """

    title: Optional[str] = None
    topic: Optional[str] = None
    questions: List[QuizQuestionIn] = Field(min_length=1, max_length=MAX_QUESTIONS)
    answers: List[Optional[str]]


class QuizAttemptOut(BaseModel):
    id: str
    score: int
    total_questions: int
    correct: List[bool]
