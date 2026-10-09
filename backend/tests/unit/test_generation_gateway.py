"""Unit tests for the generation gateway contract, using the fake."""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gateway import GenerationError, GenerationGateway
from app.services.generation.gemini import GeminiGenerationGateway


def test_provider_retry_logs_attempts_and_timing_without_error_payload(caplog):
    class ResourceExhausted(Exception):
        pass
    gateway = GeminiGenerationGateway(api_key="fake-key")
    gateway._genai = MagicMock()
    gateway._genai.GenerativeModel.return_value.generate_content.side_effect = [
        ResourceExhausted("PRIVATE-PROVIDER-ERROR-SENTINEL"), SimpleNamespace(text="done"),
    ]
    with patch("app.services.generation.gemini.time.sleep"), caplog.at_level("INFO"):
        assert gateway.generate("PRIVATE-PROMPT-SENTINEL") == "done"
    retries = [record for record in caplog.records if hasattr(record, "backoff_seconds")]
    completed = [record for record in caplog.records if hasattr(record, "provider_attempts")]
    assert len(retries) == 1
    assert retries[0].backoff_seconds == 2
    assert completed[-1].provider_attempts == 2
    assert completed[-1].elapsed_seconds >= 0
    assert "PRIVATE-PROVIDER-ERROR-SENTINEL" not in caplog.text
    assert "PRIVATE-PROMPT-SENTINEL" not in caplog.text


def test_gemini_gateway_forwards_structured_schema_with_json_mime_type():
    gateway = GeminiGenerationGateway(api_key="fake-key")
    gateway._genai = MagicMock()
    gateway._genai.GenerativeModel.return_value.generate_content.return_value.text = '{"criteria_met": [true]}'
    schema = {"type": "OBJECT", "properties": {"criteria_met": {"type": "ARRAY", "items": {"type": "BOOLEAN"}, "minItems": 1, "maxItems": 1}}, "required": ["criteria_met"]}
    assert gateway.generate("grade", response_schema=schema) == '{"criteria_met": [true]}'
    options = gateway._genai.types.GenerationConfig.call_args.kwargs
    forwarded = options["response_schema"]["properties"]["criteria_met"]
    assert forwarded["min_items"] == forwarded["max_items"] == 1
    assert options["response_mime_type"] == "application/json"


def test_installed_sdk_serializes_fixed_length_boolean_schema():
    from google.generativeai.types import GenerationConfig
    from google.generativeai.types.generation_types import to_generation_config_dict

    gateway = GeminiGenerationGateway(api_key="fake-key")
    gateway._genai = MagicMock()
    gateway._genai.types.GenerationConfig.side_effect = GenerationConfig

    def respond(prompt, *, generation_config, request_options):
        encoded = to_generation_config_dict(generation_config)
        array = encoded["response_schema"].properties["criteria_met"]
        assert array.min_items == array.max_items == 3
        return SimpleNamespace(text='{"criteria_met": [true, false, true]}')

    gateway._genai.GenerativeModel.return_value.generate_content.side_effect = respond
    schema = {"type": "OBJECT", "properties": {"criteria_met": {"type": "ARRAY", "items": {"type": "BOOLEAN"}, "minItems": 3, "maxItems": 3}}, "required": ["criteria_met"]}
    assert gateway.generate("grade", response_schema=schema) == '{"criteria_met": [true, false, true]}'
    assert schema["properties"]["criteria_met"]["minItems"] == 3


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
