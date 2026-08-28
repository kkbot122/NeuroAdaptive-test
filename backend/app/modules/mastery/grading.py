"""
Grading logic per question type. MCQ/MULTI_SELECT/NUMERIC are pure functions
of (question, given_answer). SHORT_TEXT is graded against a rubric via the
LLM-provider abstraction (Phase 1) -- "not against the model's own 'ideal
answer' alone" (mandate #3): the rubric is authored at question-creation
time, independent of any single grading call, and grading asks only which
listed criteria the answer satisfies.
"""
import json
from typing import List, Optional

from app.modules.mastery.models import Question, QuestionType
from app.services.generation.gateway import GenerationGateway


class GradingError(Exception):
    """Raised when a SHORT_TEXT rubric grading response cannot be parsed."""


def grade_mcq(question: Question, given: Optional[str]) -> float:
    return 1.0 if given is not None and given == question.correct_answer else 0.0


def grade_multi_select(question: Question, given: Optional[List[str]]) -> float:
    """
    Partial credit: fraction of the correct set selected, minus fraction of
    the correct set's size contributed by incorrect selections, clamped to
    [0, 1]. Selecting nothing, or only wrong options, scores 0 -- it never
    goes negative.
    """
    correct = set(question.correct_answer or [])
    if not correct:
        return 0.0
    selected = set(given or [])
    true_positive = len(selected & correct)
    false_positive = len(selected - correct)
    score = (true_positive - false_positive) / len(correct)
    return max(0.0, min(1.0, score))


def grade_numeric(question: Question, given: Optional[float]) -> float:
    if given is None or not question.correct_answer:
        return 0.0
    target = question.correct_answer.get("value")
    tolerance = question.correct_answer.get("tolerance", 0.0)
    if target is None:
        return 0.0
    return 1.0 if abs(given - target) <= tolerance else 0.0


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def grade_short_text(question: Question, given: Optional[str], generation: GenerationGateway) -> float:
    """
    Correctness = fraction of rubric criteria the answer satisfies. An
    ungraded/empty answer scores 0 without spending an LLM call.
    """
    rubric = question.rubric or []
    if not rubric or given is None or not given.strip():
        return 0.0

    prompt = (
        "You are grading a short-answer response against a rubric.\n"
        f"Question: {question.prompt}\n"
        f"Rubric criteria (JSON list): {json.dumps(rubric)}\n"
        f"Learner's answer: {given}\n\n"
        'Return ONLY JSON: {"criteria_met": [true, false, ...]} -- one boolean '
        "per rubric criterion, in the same order, true if the answer satisfies it."
    )
    raw = generation.generate(prompt, temperature=0.0)
    try:
        parsed = json.loads(_strip_code_fence(raw))
        met = parsed["criteria_met"]
        if not isinstance(met, list) or len(met) != len(rubric):
            raise ValueError("criteria_met length must match rubric length")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise GradingError(f"Could not parse short-text grading response: {raw[:200]!r}") from exc

    return sum(1 for ok in met if ok) / len(rubric)


def grade_attempt(question: Question, given_answer, generation: GenerationGateway) -> float:
    """Dispatch by question_type. Returns correctness in [0, 1]."""
    if question.question_type == QuestionType.MCQ.value:
        return grade_mcq(question, given_answer)
    if question.question_type == QuestionType.MULTI_SELECT.value:
        return grade_multi_select(question, given_answer)
    if question.question_type == QuestionType.NUMERIC.value:
        return grade_numeric(question, given_answer)
    if question.question_type == QuestionType.SHORT_TEXT.value:
        return grade_short_text(question, given_answer, generation)
    raise ValueError(f"Unknown question_type: {question.question_type}")
