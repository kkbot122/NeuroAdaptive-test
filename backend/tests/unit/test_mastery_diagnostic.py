import uuid

import pytest

from app.modules.curriculum.models import Concept, ConceptPrerequisite
from app.modules.mastery.diagnostic import (
    DiagnosticParseError,
    diagnostic_size,
    generate_diagnostic_questions,
    sample_concepts_for_diagnostic,
)
from app.services.generation.fake import FakeGenerationGateway


def make_concept(name, importance=0.5):
    return Concept(
        id=uuid.uuid4(), course_id=uuid.uuid4(), course_version_id=uuid.uuid4(), owner_id=1,
        canonical_key=name.lower(), name=name, definition=f"def of {name}", importance=importance,
    )


def make_edge(prereq, dependent):
    return ConceptPrerequisite(
        id=uuid.uuid4(), course_id=prereq.course_id, course_version_id=prereq.course_version_id,
        prerequisite_concept_id=prereq.id, dependent_concept_id=dependent.id,
    )


class TestDiagnosticSize:
    def test_clamped_to_minimum(self):
        assert diagnostic_size(3) == 3  # can't exceed the concept count itself
        assert diagnostic_size(100) == 20  # clamped to the max

    def test_respects_a_smaller_max_questions_override(self):
        assert diagnostic_size(100, max_questions=5) == 5

    def test_zero_concepts_is_zero(self):
        assert diagnostic_size(0) == 0


class TestSampling:
    def test_root_concepts_sort_before_dependents(self):
        root = make_concept("Root", importance=0.1)
        leaf = make_concept("Leaf", importance=0.9)
        edge = make_edge(root, leaf)
        sampled = sample_concepts_for_diagnostic([leaf, root], [edge], max_questions=1)
        assert sampled == [root]

    def test_truncates_to_diagnostic_size(self):
        concepts = [make_concept(f"C{i}") for i in range(30)]
        sampled = sample_concepts_for_diagnostic(concepts, [], max_questions=5)
        assert len(sampled) == 5


class TestGenerateDiagnosticQuestions:
    def test_empty_concept_list_returns_no_drafts(self):
        assert generate_diagnostic_questions([], FakeGenerationGateway()) == []

    def test_parses_matching_concept_by_name(self):
        concept = make_concept("Deadlock")
        gen = FakeGenerationGateway().set_default(
            '{"questions": [{"concept_name": "Deadlock", "prompt": "What is a deadlock?", '
            '"options": ["A", "B"], "correct_answer": "A", "difficulty": 0.4}]}'
        )
        drafts = generate_diagnostic_questions([concept], gen)
        assert len(drafts) == 1
        assert drafts[0].concept_id == concept.id
        assert drafts[0].correct_answer == "A"

    def test_skips_entries_with_unmatched_concept_name(self):
        concept = make_concept("Deadlock")
        gen = FakeGenerationGateway().set_default(
            '{"questions": [{"concept_name": "Nonexistent", "prompt": "?", '
            '"options": ["A", "B"], "correct_answer": "A"}]}'
        )
        assert generate_diagnostic_questions([concept], gen) == []

    def test_skips_entries_whose_correct_answer_is_not_in_options(self):
        concept = make_concept("Deadlock")
        gen = FakeGenerationGateway().set_default(
            '{"questions": [{"concept_name": "Deadlock", "prompt": "?", '
            '"options": ["A", "B"], "correct_answer": "C"}]}'
        )
        assert generate_diagnostic_questions([concept], gen) == []

    def test_unparseable_response_raises(self):
        concept = make_concept("Deadlock")
        gen = FakeGenerationGateway().set_default("not json")
        with pytest.raises(DiagnosticParseError):
            generate_diagnostic_questions([concept], gen)
