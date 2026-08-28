"""Unit tests for prerequisite-edge proposal, using the fake gateway."""
import uuid

import pytest

from app.modules.curriculum.edges import ConceptForEdges, EdgeParseError, propose_edges
from app.services.generation.fake import FakeGenerationGateway


def concept(name, definition="def"):
    return ConceptForEdges(id=uuid.uuid4(), name=name, definition=definition)


class TestEdgeProposal:
    def test_parses_a_well_formed_edge_list(self):
        a, b = concept("Memory"), concept("Virtual Memory")
        gateway = FakeGenerationGateway().set_default(
            '{"edges": [{"prerequisite": "Memory", "dependent": "Virtual Memory", '
            '"strength": "HARD", "confidence": 0.9}]}'
        )

        edges = propose_edges([a, b], gateway)

        assert len(edges) == 1
        assert edges[0].prerequisite_id == a.id
        assert edges[0].dependent_id == b.id
        assert edges[0].strength == "HARD"
        assert edges[0].confidence == 0.9

    def test_name_matching_is_case_insensitive(self):
        a, b = concept("Memory"), concept("Virtual Memory")
        gateway = FakeGenerationGateway().set_default(
            '{"edges": [{"prerequisite": "MEMORY", "dependent": "virtual memory", '
            '"strength": "SOFT", "confidence": 0.5}]}'
        )

        edges = propose_edges([a, b], gateway)

        assert len(edges) == 1

    def test_hallucinated_concept_name_is_dropped(self):
        a, b = concept("Memory"), concept("Virtual Memory")
        gateway = FakeGenerationGateway().set_default(
            '{"edges": [{"prerequisite": "Nonexistent Concept", "dependent": "Virtual Memory", '
            '"strength": "SOFT", "confidence": 0.5}]}'
        )

        edges = propose_edges([a, b], gateway)

        assert edges == []

    def test_self_edge_is_dropped(self):
        a = concept("Memory")
        gateway = FakeGenerationGateway().set_default(
            '{"edges": [{"prerequisite": "Memory", "dependent": "Memory", '
            '"strength": "SOFT", "confidence": 0.5}]}'
        )

        assert propose_edges([a, concept("Other")], gateway) == []

    def test_invalid_strength_defaults_to_soft(self):
        a, b = concept("A"), concept("B")
        gateway = FakeGenerationGateway().set_default(
            '{"edges": [{"prerequisite": "A", "dependent": "B", '
            '"strength": "MAYBE", "confidence": 0.5}]}'
        )

        edges = propose_edges([a, b], gateway)

        assert edges[0].strength == "SOFT"

    def test_fewer_than_two_concepts_makes_no_llm_call(self):
        gateway = FakeGenerationGateway()
        assert propose_edges([concept("Only One")], gateway) == []
        assert gateway.calls == []

    def test_empty_edges_response_is_valid(self):
        a, b = concept("A"), concept("B")
        gateway = FakeGenerationGateway().set_default('{"edges": []}')
        assert propose_edges([a, b], gateway) == []

    def test_unparseable_response_raises(self):
        a, b = concept("A"), concept("B")
        gateway = FakeGenerationGateway().set_default("garbage")
        with pytest.raises(EdgeParseError):
            propose_edges([a, b], gateway)

    def test_confidence_is_clamped_to_valid_range(self):
        a, b = concept("A"), concept("B")
        gateway = FakeGenerationGateway().set_default(
            '{"edges": [{"prerequisite": "A", "dependent": "B", '
            '"strength": "SOFT", "confidence": 3.0}]}'
        )
        edges = propose_edges([a, b], gateway)
        assert edges[0].confidence == 1.0
