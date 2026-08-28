"""Mandate test case 8: CSV/JSON export round-trips against a versioned schema."""
import pytest

from app.modules.evaluation import export


FIXTURE_RECORDS = [
    {"decision_id": "d1", "outcome_type": "ASSESSED", "mastery_delta": "0.12"},
    {"decision_id": "d2", "outcome_type": "COMPLETED", "mastery_delta": ""},
]


class TestJsonRoundTrip:
    def test_export_then_reimport_reproduces_the_records(self):
        payload = export.to_json(FIXTURE_RECORDS)
        restored = export.from_json(payload)
        assert restored == FIXTURE_RECORDS

    def test_rejects_an_unrecognized_schema_version(self):
        import json

        tampered = json.dumps({"schema_version": "some-other-version", "records": []})
        with pytest.raises(ValueError):
            export.from_json(tampered)


class TestCsvRoundTrip:
    def test_export_then_reimport_reproduces_the_records(self):
        fields = ["decision_id", "outcome_type", "mastery_delta"]
        payload = export.to_csv(FIXTURE_RECORDS, fields)
        restored = export.from_csv(payload)
        assert restored == FIXTURE_RECORDS

    def test_rejects_an_unrecognized_schema_version(self):
        tampered = "schema_version,decision_id\nold-version,d1\n"
        with pytest.raises(ValueError):
            export.from_csv(tampered)

    def test_schema_version_column_is_present_and_correct(self):
        payload = export.to_csv(FIXTURE_RECORDS, ["decision_id"])
        assert export.EXPORT_SCHEMA_VERSION in payload
