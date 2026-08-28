"""
Basic analysis utilities: summary statistics per condition, computed from
already-exported records. This is tooling for a future real study to use --
it does not draw or assert any conclusion, and it never runs on anything
but data the caller passes in (real, exported Phase 7 data, or an
explicitly-labeled synthetic fixture -- see synthetic_fixtures.py).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class MetricSummary:
    n: int
    mean: Optional[float]
    min: Optional[float]
    max: Optional[float]


def _summarize(values: List[float]) -> MetricSummary:
    clean = [v for v in values if v is not None]
    if not clean:
        return MetricSummary(n=0, mean=None, min=None, max=None)
    return MetricSummary(n=len(clean), mean=sum(clean) / len(clean), min=min(clean), max=max(clean))


def summarize_by_condition(
    records: List[dict], condition_field: str, metric_fields: List[str]
) -> Dict[str, Dict[str, MetricSummary]]:
    """
    records: flat dicts, each carrying `condition_field` (e.g. "B2") and
    zero or more of `metric_fields` (missing/None values are excluded from
    that metric's summary, not treated as zero).

    Returns {condition_code: {metric_field: MetricSummary}}. This is
    descriptive statistics only -- no significance test, no effect size, no
    comparative claim of any kind. Computing those, and interpreting them,
    is explicitly future work requiring a real pilot (mandate).
    """
    by_condition: Dict[str, List[dict]] = {}
    for record in records:
        code = record.get(condition_field)
        if code is None:
            continue
        by_condition.setdefault(code, []).append(record)

    result: Dict[str, Dict[str, MetricSummary]] = {}
    for code, rows in by_condition.items():
        result[code] = {
            field: _summarize([row.get(field) for row in rows if row.get(field) is not None])
            for field in metric_fields
        }
    return result
