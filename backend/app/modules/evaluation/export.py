"""
Export/import of evaluation records to CSV and JSON, for consumption by an
external statistical tool. Round-trips exactly (export then re-import
reproduces the same records) against a versioned schema, so a downstream
tool can detect a schema change instead of silently misreading a field.
"""
import csv
import io
import json
from typing import Any, Dict, List

EXPORT_SCHEMA_VERSION = "evaluation-export-v1"


def to_json(records: List[Dict[str, Any]]) -> str:
    return json.dumps({"schema_version": EXPORT_SCHEMA_VERSION, "records": records}, default=str, indent=2)


def from_json(payload: str) -> List[Dict[str, Any]]:
    data = json.loads(payload)
    if data.get("schema_version") != EXPORT_SCHEMA_VERSION:
        raise ValueError(f"Unrecognized export schema version: {data.get('schema_version')!r}")
    return data["records"]


def to_csv(records: List[Dict[str, Any]], fields: List[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["schema_version"] + fields, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        row = {"schema_version": EXPORT_SCHEMA_VERSION, **record}
        writer.writerow(row)
    return buffer.getvalue()


def from_csv(payload: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(payload))
    records = []
    for row in reader:
        schema_version = row.pop("schema_version", None)
        if schema_version != EXPORT_SCHEMA_VERSION:
            raise ValueError(f"Unrecognized export schema version: {schema_version!r}")
        records.append(row)
    return records
