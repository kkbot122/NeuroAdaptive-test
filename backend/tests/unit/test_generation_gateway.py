"""Unit tests for the generation gateway contract, using the fake."""
import pytest

from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gateway import GenerationError, GenerationGateway


class TestFakeGateway:
    def test_satisfies_the_abstract_interface(self):
        assert isinstance(FakeGenerationGateway(), GenerationGateway)

    def test_matches_by_registered_substring(self):
        gateway = FakeGenerationGateway().when_prompt_contains(
            "extract concepts", '{"concepts": []}'
        )
        assert gateway.generate("Please extract concepts from this text.") == '{"concepts": []}'

    def test_first_matching_registration_wins(self):
        gateway = (
            FakeGenerationGateway()
            .when_prompt_contains("concepts", "first")
            .when_prompt_contains("concepts", "second")
        )
        assert gateway.generate("about concepts") == "first"

    def test_falls_back_to_default_when_set(self):
        gateway = FakeGenerationGateway().set_default("fallback")
        assert gateway.generate("anything at all") == "fallback"

    def test_raises_when_nothing_matches_and_no_default(self):
        gateway = FakeGenerationGateway()
        with pytest.raises(GenerationError):
            gateway.generate("unregistered prompt")

    def test_records_every_call_for_assertions(self):
        gateway = FakeGenerationGateway().set_default("x")
        gateway.generate("prompt one")
        gateway.generate("prompt two")
        assert gateway.calls == ["prompt one", "prompt two"]

    def test_model_name_is_exposed_for_provenance(self):
        assert FakeGenerationGateway().model_name
