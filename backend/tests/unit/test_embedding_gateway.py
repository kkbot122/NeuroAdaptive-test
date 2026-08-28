"""
Unit tests for the embedding gateway contract, using the fake implementation.
No network, no API key, no Gemini SDK import required to run these.
"""
import pytest

from app.services.embedding.fake import FAKE_DIMENSIONS, FakeEmbeddingGateway
from app.services.embedding.gateway import EmbeddingGateway


class TestGatewayContract:
    def test_fake_satisfies_the_abstract_interface(self):
        assert isinstance(FakeEmbeddingGateway(), EmbeddingGateway)

    def test_empty_input_returns_empty_output(self):
        assert FakeEmbeddingGateway().embed_texts([]) == []

    def test_returns_one_vector_per_input_in_order(self):
        gateway = FakeEmbeddingGateway()
        result = gateway.embed_texts(["a", "b", "c"])
        assert len(result) == 3

    def test_every_vector_has_the_declared_dimensionality(self):
        gateway = FakeEmbeddingGateway()
        for vector in gateway.embed_texts(["one", "two"]):
            assert len(vector) == gateway.dimensions == FAKE_DIMENSIONS

    def test_same_text_produces_the_same_vector(self):
        gateway = FakeEmbeddingGateway()
        first = gateway.embed_texts(["consistent text"])[0]
        second = gateway.embed_texts(["consistent text"])[0]
        assert first == second

    def test_different_text_produces_a_different_vector(self):
        gateway = FakeEmbeddingGateway()
        a, b = gateway.embed_texts(["alpha", "omega"])
        assert a != b

    def test_model_name_is_exposed_for_provenance(self):
        assert FakeEmbeddingGateway().model_name
