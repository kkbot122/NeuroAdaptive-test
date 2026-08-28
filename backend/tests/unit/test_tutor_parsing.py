import pytest

from app.modules.tutor.parsing import TutorParseError, parse_tutor_response


class TestParsing:
    def test_parses_a_well_formed_response(self):
        raw = (
            '{"insufficient_evidence": false, "answer_markdown": "X is Y.", '
            '"claims": [{"text": "X is Y.", "chunk_id": "abc"}]}'
        )
        parsed = parse_tutor_response(raw)
        assert parsed.insufficient_evidence is False
        assert parsed.answer_markdown == "X is Y."
        assert len(parsed.claims) == 1
        assert parsed.claims[0].chunk_id == "abc"

    def test_strips_code_fences(self):
        raw = '```json\n{"insufficient_evidence": true, "answer_markdown": "", "claims": []}\n```'
        parsed = parse_tutor_response(raw)
        assert parsed.insufficient_evidence is True

    def test_malformed_individual_claim_is_dropped_not_fatal(self):
        raw = '{"insufficient_evidence": false, "answer_markdown": "X.", "claims": [{"text": "X."}]}'
        parsed = parse_tutor_response(raw)
        assert parsed.claims == []

    def test_unparseable_response_raises(self):
        with pytest.raises(TutorParseError):
            parse_tutor_response("not json at all")

    def test_missing_claims_field_defaults_to_empty(self):
        raw = '{"insufficient_evidence": false, "answer_markdown": "X."}'
        parsed = parse_tutor_response(raw)
        assert parsed.claims == []
