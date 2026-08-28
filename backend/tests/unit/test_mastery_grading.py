import uuid

import pytest

from app.modules.mastery.grading import (
    GradingError,
    grade_attempt,
    grade_mcq,
    grade_multi_select,
    grade_numeric,
    grade_short_text,
)
from app.modules.mastery.models import Question, QuestionType
from app.services.generation.fake import FakeGenerationGateway


def make_question(**overrides):
    defaults = dict(
        id=uuid.uuid4(), course_id=uuid.uuid4(), course_version_id=uuid.uuid4(), owner_id=1,
        question_type=QuestionType.MCQ.value, prompt="?", options=["a", "b"], correct_answer="a",
        difficulty=0.5,
    )
    defaults.update(overrides)
    return Question(**defaults)


class TestMCQ:
    def test_correct_option_scores_one(self):
        q = make_question(correct_answer="a")
        assert grade_mcq(q, "a") == 1.0

    def test_wrong_option_scores_zero(self):
        q = make_question(correct_answer="a")
        assert grade_mcq(q, "b") == 0.0

    def test_unanswered_scores_zero(self):
        q = make_question(correct_answer="a")
        assert grade_mcq(q, None) == 0.0


class TestMultiSelect:
    def test_exact_match_scores_one(self):
        q = make_question(question_type=QuestionType.MULTI_SELECT.value, correct_answer=["a", "b"])
        assert grade_multi_select(q, ["a", "b"]) == 1.0

    def test_partial_selection_scores_partial_credit(self):
        q = make_question(question_type=QuestionType.MULTI_SELECT.value, correct_answer=["a", "b"])
        assert grade_multi_select(q, ["a"]) == pytest.approx(0.5)

    def test_a_wrong_extra_selection_is_penalized(self):
        q = make_question(question_type=QuestionType.MULTI_SELECT.value, correct_answer=["a", "b"])
        assert grade_multi_select(q, ["a", "b", "c"]) == pytest.approx(0.5)

    def test_never_goes_negative(self):
        q = make_question(question_type=QuestionType.MULTI_SELECT.value, correct_answer=["a"])
        assert grade_multi_select(q, ["x", "y", "z"]) == 0.0


class TestNumeric:
    def test_within_tolerance_scores_one(self):
        q = make_question(question_type=QuestionType.NUMERIC.value, correct_answer={"value": 10.0, "tolerance": 0.5})
        assert grade_numeric(q, 10.4) == 1.0

    def test_outside_tolerance_scores_zero(self):
        q = make_question(question_type=QuestionType.NUMERIC.value, correct_answer={"value": 10.0, "tolerance": 0.5})
        assert grade_numeric(q, 12.0) == 0.0

    def test_unanswered_scores_zero(self):
        q = make_question(question_type=QuestionType.NUMERIC.value, correct_answer={"value": 10.0, "tolerance": 0.5})
        assert grade_numeric(q, None) == 0.0


class TestShortText:
    def test_all_criteria_met_scores_one(self):
        q = make_question(
            question_type=QuestionType.SHORT_TEXT.value, correct_answer=None,
            rubric=["mentions X", "mentions Y"],
        )
        gen = FakeGenerationGateway().set_default('{"criteria_met": [true, true]}')
        assert grade_short_text(q, "answer", gen) == 1.0

    def test_partial_criteria_met_scores_partial_credit(self):
        q = make_question(
            question_type=QuestionType.SHORT_TEXT.value, correct_answer=None,
            rubric=["mentions X", "mentions Y"],
        )
        gen = FakeGenerationGateway().set_default('{"criteria_met": [true, false]}')
        assert grade_short_text(q, "answer", gen) == pytest.approx(0.5)

    def test_empty_answer_scores_zero_without_a_generation_call(self):
        q = make_question(
            question_type=QuestionType.SHORT_TEXT.value, correct_answer=None, rubric=["mentions X"],
        )
        gen = FakeGenerationGateway()  # no responses registered -- would raise if called
        assert grade_short_text(q, "", gen) == 0.0
        assert gen.calls == []

    def test_malformed_response_raises_grading_error(self):
        q = make_question(
            question_type=QuestionType.SHORT_TEXT.value, correct_answer=None, rubric=["mentions X"],
        )
        gen = FakeGenerationGateway().set_default("not json")
        with pytest.raises(GradingError):
            grade_short_text(q, "answer", gen)


class TestDispatch:
    def test_grade_attempt_dispatches_by_question_type(self):
        q = make_question(question_type=QuestionType.MCQ.value, correct_answer="a")
        assert grade_attempt(q, "a", FakeGenerationGateway()) == 1.0
