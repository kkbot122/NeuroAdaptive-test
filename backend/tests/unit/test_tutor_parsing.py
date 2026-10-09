import pytest

from app.modules.tutor.parsing import TutorParseError, parse_tutor_response


class TestParsing:
    @pytest.mark.parametrize("text,chunk_id", [(" ", "abc"), ("X.", "\t")])
    def test_blank_claim_or_citation_is_rejected(self, text, chunk_id):
        import json
        raw = json.dumps({"insufficient_evidence": False, "answer_markdown": "X.",
                          "claims": [{"text": text, "chunk_id": chunk_id}]})
        with pytest.raises(TutorParseError):
            parse_tutor_response(raw)

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

    def test_malformed_claim_rejects_the_response(self):
        raw = '{"insufficient_evidence": false, "answer_markdown": "X.", "claims": [{"text": "X."}]}'
        with pytest.raises(TutorParseError):
            parse_tutor_response(raw)

    def test_unparseable_response_raises(self):
        with pytest.raises(TutorParseError):
            parse_tutor_response("not json at all")

    def test_an_answer_without_claims_is_rejected(self):
        raw = '{"insufficient_evidence": false, "answer_markdown": "X."}'
        with pytest.raises(TutorParseError):
            parse_tutor_response(raw)
