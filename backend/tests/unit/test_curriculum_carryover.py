"""Unit tests for concept carryover matching across course versions."""
import uuid

from app.modules.curriculum.carryover import CarryoverCandidate, compute_carryover


def concept(key, embedding=None):
    return CarryoverCandidate(id=uuid.uuid4(), canonical_key=key, embedding=embedding)


class TestExactKeyMatch:
    def test_same_canonical_key_is_carried_over(self):
        old = concept("virtual memory")
        new = concept("virtual memory")

        result = compute_carryover([old], [new])

        assert result[str(new.id)] == {"from": str(old.id), "status": "carried"}

    def test_different_canonical_key_with_no_embedding_is_new(self):
        old = concept("virtual memory")
        new = concept("garbage collection")

        result = compute_carryover([old], [new])

        assert result[str(new.id)] == {"from": None, "status": "new"}

    def test_a_genuinely_new_concept_is_not_matched_to_something_unrelated(self):
        """The mandate's specific concern: a new concept must not be
        silently matched to an unrelated old one just because it's the only
        option left."""
        old = concept("deadlock", embedding=[1.0, 0.0, 0.0])
        new = concept("garbage collection", embedding=[0.0, 1.0, 0.0])

        result = compute_carryover([old], [new])

        assert result[str(new.id)]["status"] == "new"
        assert result[str(new.id)]["from"] is None

    def test_each_old_concept_is_used_at_most_once(self):
        old = concept("x")
        new1 = concept("x")
        new2 = concept("x")  # duplicate key on the new side; should not both match old

        result = compute_carryover([old], [new1, new2])

        statuses = [result[str(new1.id)]["status"], result[str(new2.id)]["status"]]
        assert statuses.count("carried") == 1
        assert statuses.count("new") == 1


class TestEmbeddingFallback:
    def test_renamed_concept_is_carried_via_similarity(self):
        old = concept("virtual memory", embedding=[1.0, 0.0, 0.0])
        new = concept("memory virtualization", embedding=[0.999, 0.001, 0.0])  # renamed, same idea

        result = compute_carryover([old], [new])

        assert result[str(new.id)] == {"from": str(old.id), "status": "carried"}

    def test_similarity_below_the_threshold_is_marked_new_not_matched(self):
        old = concept("virtual memory", embedding=[1.0, 0.0, 0.0])
        new = concept("paging", embedding=[0.5, 0.5, 0.0])  # related but not the same concept

        result = compute_carryover([old], [new])

        assert result[str(new.id)]["status"] == "new"

    def test_key_match_is_tried_before_embedding_fallback(self):
        """If the key matches, embedding is irrelevant -- confirms pass
        ordering rather than assuming it."""
        old = concept("x", embedding=[1.0, 0.0])
        new = concept("x", embedding=[0.0, 1.0])  # deliberately dissimilar embedding

        result = compute_carryover([old], [new])

        assert result[str(new.id)]["status"] == "carried"


class TestMultipleConcepts:
    def test_a_mix_of_carried_and_new_concepts_in_one_regeneration(self):
        old_a = concept("deadlock")
        old_b = concept("paging")
        new_a = concept("deadlock")          # exact match -> carried
        new_c = concept("thrashing")          # unrelated, no embedding -> new

        result = compute_carryover([old_a, old_b], [new_a, new_c])

        assert result[str(new_a.id)]["status"] == "carried"
        assert result[str(new_c.id)]["status"] == "new"

    def test_empty_old_concepts_marks_everything_new(self):
        new = concept("anything")
        result = compute_carryover([], [new])
        assert result[str(new.id)]["status"] == "new"

    def test_empty_new_concepts_returns_empty_map(self):
        assert compute_carryover([concept("x")], []) == {}
