"""Unit tests for per-section concept extraction, using fake gateways."""
import uuid

import pytest

from app.modules.curriculum.extraction import (
    ExtractionParseError,
    group_chunks_into_sections,
    propose_concepts_for_section,
)
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway


class FakeChunk:
    def __init__(self, document_id, position, heading_path, text, chunk_id=None):
        self.id = chunk_id or uuid.uuid4()
        self.document_id = document_id
        self.position = position
        self.heading_path = heading_path
        self.text = text


class TestGrouping:
    def test_groups_chunks_by_heading_path(self):
        doc = uuid.uuid4()
        chunks = [
            FakeChunk(doc, 0, "A", "text1"),
            FakeChunk(doc, 1, "B", "text2"),
            FakeChunk(doc, 2, "A", "text3"),
        ]
        groups = group_chunks_into_sections(chunks)
        headings = [g[0].heading_path for g in groups]
        assert set(headings) == {"A", "B"}

    def test_preserves_document_order_within_a_group(self):
        doc = uuid.uuid4()
        chunks = [FakeChunk(doc, 2, "A", "c"), FakeChunk(doc, 0, "A", "a"), FakeChunk(doc, 1, "A", "b")]
        groups = group_chunks_into_sections(chunks)
        assert [c.text for c in groups[0]] == ["a", "b", "c"]

    def test_chunk_with_no_heading_gets_its_own_group(self):
        doc = uuid.uuid4()
        chunks = [FakeChunk(doc, 0, None, "orphan")]
        groups = group_chunks_into_sections(chunks)
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_empty_input_returns_no_groups(self):
        assert group_chunks_into_sections([]) == []


class TestConceptProposal:
    def test_bounded_context_not_whole_document(self):
        """The prompt for one section must not include chunks from another."""
        doc = uuid.uuid4()
        section_a = [FakeChunk(doc, 0, "A", "Section A discusses deadlocks.")]
        gateway = FakeGenerationGateway().set_default('{"concepts": []}')

        propose_concepts_for_section(section_a, gateway, FakeEmbeddingGateway())

        assert "Section A" in gateway.calls[0] or "deadlocks" in gateway.calls[0]

    def test_parses_a_well_formed_response_into_candidates(self):
        chunks = [FakeChunk(uuid.uuid4(), 0, "Deadlocks", "text about deadlocks")]
        gateway = FakeGenerationGateway().set_default(
            '{"concepts": [{"name": "Deadlock", "definition": "A circular wait.", '
            '"importance": 0.8, "bloom_level": "understand"}]}'
        )

        result = propose_concepts_for_section(chunks, gateway, FakeEmbeddingGateway())

        assert len(result) == 1
        assert result[0].name == "Deadlock"
        assert result[0].definition == "A circular wait."
        assert result[0].importance == 0.8
        assert result[0].bloom_level == "understand"

    def test_source_chunk_ids_cover_every_chunk_in_the_section(self):
        c1, c2 = FakeChunk(uuid.uuid4(), 0, "H", "a"), FakeChunk(uuid.uuid4(), 1, "H", "b")
        gateway = FakeGenerationGateway().set_default(
            '{"concepts": [{"name": "X", "definition": "d", "importance": 0.5}]}'
        )

        result = propose_concepts_for_section([c1, c2], gateway, FakeEmbeddingGateway())

        assert set(result[0].source_chunk_ids) == {c1.id, c2.id}

    def test_every_concept_gets_a_real_embedding(self):
        chunks = [FakeChunk(uuid.uuid4(), 0, "H", "text")]
        gateway = FakeGenerationGateway().set_default(
            '{"concepts": [{"name": "X", "definition": "d"}]}'
        )

        result = propose_concepts_for_section(chunks, gateway, FakeEmbeddingGateway())

        assert result[0].embedding
        assert len(result[0].embedding) > 0

    def test_empty_concepts_response_is_valid(self):
        chunks = [FakeChunk(uuid.uuid4(), 0, "H", "text with nothing teachable")]
        gateway = FakeGenerationGateway().set_default('{"concepts": []}')

        assert propose_concepts_for_section(chunks, gateway, FakeEmbeddingGateway()) == []

    def test_malformed_entry_is_skipped_not_fatal(self):
        chunks = [FakeChunk(uuid.uuid4(), 0, "H", "text")]
        gateway = FakeGenerationGateway().set_default(
            '{"concepts": [{"name": "Good", "definition": "d"}, {"bad": "entry"}]}'
        )

        result = propose_concepts_for_section(chunks, gateway, FakeEmbeddingGateway())

        assert len(result) == 1
        assert result[0].name == "Good"

    def test_unparseable_response_raises(self):
        chunks = [FakeChunk(uuid.uuid4(), 0, "H", "text")]
        gateway = FakeGenerationGateway().set_default("not json")

        with pytest.raises(ExtractionParseError):
            propose_concepts_for_section(chunks, gateway, FakeEmbeddingGateway())

    def test_importance_is_clamped_to_valid_range(self):
        chunks = [FakeChunk(uuid.uuid4(), 0, "H", "text")]
        gateway = FakeGenerationGateway().set_default(
            '{"concepts": [{"name": "X", "definition": "d", "importance": 5.0}]}'
        )

        result = propose_concepts_for_section(chunks, gateway, FakeEmbeddingGateway())

        assert result[0].importance == 1.0

    def test_no_chunks_makes_no_llm_call(self):
        gateway = FakeGenerationGateway()
        assert propose_concepts_for_section([], gateway, FakeEmbeddingGateway()) == []
        assert gateway.calls == []
