"""
Unit tests for concept normalization: threshold banding, canonical keys,
and LLM adjudication for the mid-confidence band.
"""
import uuid

import pytest

from app.modules.curriculum.normalization import (
    HIGH_CONFIDENCE_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
    CandidateConcept,
    MergeDecision,
    NormalizedConcept,
    canonical_key,
    classify_similarity,
    normalize_concepts,
)
from app.services.generation.fake import FakeGenerationGateway


def candidate(name, definition, embedding, chunk_ids=None):
    return CandidateConcept(
        name=name,
        definition=definition,
        source_chunk_ids=chunk_ids or [uuid.uuid4()],
        embedding=embedding,
    )


class TestCanonicalKey:
    def test_is_case_insensitive(self):
        assert canonical_key("Virtual Memory") == canonical_key("virtual memory")

    def test_is_whitespace_insensitive(self):
        assert canonical_key("Virtual Memory") == canonical_key("virtual memory ")

    def test_collapses_internal_whitespace(self):
        assert canonical_key("virtual   memory") == canonical_key("virtual memory")

    def test_different_names_produce_different_keys(self):
        assert canonical_key("virtual memory") != canonical_key("physical memory")

    def test_is_deterministic(self):
        assert canonical_key("Deadlock") == canonical_key("Deadlock")


class TestSimilarityBanding:
    def test_high_similarity_auto_merges(self):
        assert classify_similarity(HIGH_CONFIDENCE_THRESHOLD) == MergeDecision.AUTO_MERGE
        assert classify_similarity(0.99) == MergeDecision.AUTO_MERGE

    def test_mid_similarity_routes_to_adjudication(self):
        midpoint = (HIGH_CONFIDENCE_THRESHOLD + LOW_CONFIDENCE_THRESHOLD) / 2
        assert classify_similarity(midpoint) == MergeDecision.ADJUDICATE

    def test_low_similarity_keeps_distinct(self):
        assert classify_similarity(LOW_CONFIDENCE_THRESHOLD - 0.01) == MergeDecision.KEEP_DISTINCT
        assert classify_similarity(0.0) == MergeDecision.KEEP_DISTINCT

    def test_band_boundaries_are_well_ordered(self):
        assert 0.0 <= LOW_CONFIDENCE_THRESHOLD < HIGH_CONFIDENCE_THRESHOLD <= 1.0


class TestAutoMerge:
    def test_high_similarity_pair_merges_into_one_concept(self):
        c1 = candidate("Virtual Memory", "A memory management technique.", [1.0, 0.0, 0.0])
        c2 = candidate("Virtual Memory (detailed)", "A memory management technique, detailed.", [0.999, 0.001, 0.0])
        gateway = FakeGenerationGateway()  # must not be called for auto-merge

        result = normalize_concepts([c1, c2], gateway)

        assert len(result) == 1
        assert gateway.calls == []

    def test_merged_concept_has_the_union_of_source_references(self):
        chunk1, chunk2 = uuid.uuid4(), uuid.uuid4()
        c1 = candidate("Deadlock", "A circular wait condition.", [1.0, 0.0], chunk_ids=[chunk1])
        c2 = candidate("Deadlock", "A circular wait condition, restated.", [1.0, 0.0], chunk_ids=[chunk2])

        result = normalize_concepts([c1, c2], FakeGenerationGateway())

        assert set(result[0].source_chunk_ids) == {chunk1, chunk2}

    def test_merged_concept_retains_the_other_name_as_an_alias(self):
        c1 = candidate("Virtual Memory", "def", [1.0, 0.0])
        c2 = candidate("Memory Virtualization", "def restated", [0.999, 0.001])

        result = normalize_concepts([c1, c2], FakeGenerationGateway())

        assert "Memory Virtualization" in result[0].aliases


class TestAdjudication:
    def test_mid_confidence_pair_triggers_an_adjudication_call(self):
        """The call must actually happen -- assert against the fake's
        recorded calls, not just the final merge outcome."""
        c1 = candidate("Virtual Memory", "Uses disk as an extension of RAM.", [1.0, 0.0, 0.0])
        # Cosine similarity of these two vectors lands in the mid band.
        c2 = candidate("Paging", "Divides memory into fixed-size blocks.", [0.8, 0.6, 0.0])

        gateway = FakeGenerationGateway().set_default(
            '{"same_concept": true, "merged_definition": "merged text"}'
        )
        normalize_concepts([c1, c2], gateway)

        assert len(gateway.calls) == 1
        assert "Virtual Memory" in gateway.calls[0]
        assert "Paging" in gateway.calls[0]

    def test_adjudication_yes_merges_with_the_provided_definition(self):
        c1 = candidate("Virtual Memory", "def A", [1.0, 0.0, 0.0])
        c2 = candidate("Paging", "def B", [0.8, 0.6, 0.0])
        gateway = FakeGenerationGateway().set_default(
            '{"same_concept": true, "merged_definition": "the real merged definition"}'
        )

        result = normalize_concepts([c1, c2], gateway)

        assert len(result) == 1
        assert result[0].definition == "the real merged definition"

    def test_adjudication_no_keeps_concepts_distinct(self):
        c1 = candidate("Virtual Memory", "def A", [1.0, 0.0, 0.0])
        c2 = candidate("Paging", "def B", [0.8, 0.6, 0.0])
        gateway = FakeGenerationGateway().set_default(
            '{"same_concept": false, "merged_definition": null}'
        )

        result = normalize_concepts([c1, c2], gateway)

        assert len(result) == 2

    def test_auto_merge_never_calls_adjudication_and_vice_versa(self):
        """High band -> no LLM call at all. Confirms the bands are mutually
        exclusive in what they trigger, not just in their score ranges."""
        c1 = candidate("X", "def", [1.0, 0.0, 0.0])
        c2 = candidate("X restated", "def restated", [0.999, 0.0, 0.001])
        gateway = FakeGenerationGateway()

        normalize_concepts([c1, c2], gateway)

        assert gateway.calls == []

    def test_malformed_adjudication_response_keeps_concepts_distinct(self):
        """A corrupted response is not evidence the concepts should merge."""
        c1 = candidate("Virtual Memory", "def A", [1.0, 0.0, 0.0])
        c2 = candidate("Paging", "def B", [0.8, 0.6, 0.0])
        gateway = FakeGenerationGateway().set_default("not valid json at all")

        result = normalize_concepts([c1, c2], gateway)

        assert len(result) == 2


class TestKeepDistinct:
    def test_low_similarity_pair_stays_distinct_without_any_llm_call(self):
        c1 = candidate("Deadlock", "def A", [1.0, 0.0, 0.0])
        c2 = candidate("Garbage Collection", "def B", [0.0, 1.0, 0.0])
        gateway = FakeGenerationGateway()

        result = normalize_concepts([c1, c2], gateway)

        assert len(result) == 2
        assert gateway.calls == []

    def test_single_candidate_is_never_compared_against_anything(self):
        result = normalize_concepts([candidate("Deadlock", "def", [1.0, 0.0])], FakeGenerationGateway())
        assert len(result) == 1

    def test_empty_input_returns_empty_output(self):
        assert normalize_concepts([], FakeGenerationGateway()) == []
