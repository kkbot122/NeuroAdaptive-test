"""
Phase 8 guardrail (mandate test 9): scans the repo for hard-coded
percentages, effect sizes, or comparative claims ("improves by X%",
"outperforms Y") that read like a fabricated result rather than something
sourced from an actual computed-metric call.

This is a heuristic, not a proof -- it flags for MANUAL REVIEW, exactly as
the mandate asks ("flag any match for manual review"), not an automatic
pass/fail on content. The pytest wrapper
(tests/evaluation/test_no_fabrication_guardrail.py) asserts this script
runs and currently finds zero matches in the real repo; it does not assert
the regex is exhaustive or unbeatable by someone determined to hide a
number, because no such static check can be.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCAN_DIRS = ["docs", "frontend/app", "backend/app/modules/evaluation"]
SCAN_EXTENSIONS = {".md", ".tsx", ".ts", ".py", ".html"}

# Deliberately excludes this script and its own test/payload fixtures --
# the check should not flag its own docstrings/examples describing what it
# looks for, or unrelated attack-payload strings from Phase 6.
EXCLUDE_PATH_FRAGMENTS = [
    "check_no_fabricated_results.py",
    "test_no_fabrication_guardrail.py",
    "injection_payloads.py",
    "measure_injection_resistance.py",
    "node_modules",
    ".venv",
    ".git",
]

# "improves by 12%", "outperforms baseline by 8.5%", "40% better", "increases
# accuracy by 15%" -- a number tied to a comparative/improvement verb.
COMPARATIVE_CLAIM_PATTERNS = [
    re.compile(r"\b(improves?|increases?|decreases?|outperforms?|beats?)\b[^.\n]{0,40}\b\d{1,3}(\.\d+)?%", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(\.\d+)?%\s*(better|worse|higher|lower|faster|slower|improvement)\b", re.IGNORECASE),
    re.compile(r"\boutperforms?\b\s+\w+", re.IGNORECASE),
]


def _should_scan(path: Path) -> bool:
    if path.suffix not in SCAN_EXTENSIONS:
        return False
    path_str = str(path)
    return not any(fragment in path_str for fragment in EXCLUDE_PATH_FRAGMENTS)


def scan() -> list:
    findings = []
    for scan_dir in SCAN_DIRS:
        base = REPO_ROOT / scan_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or not _should_scan(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                for pattern in COMPARATIVE_CLAIM_PATTERNS:
                    if pattern.search(line):
                        findings.append((str(path.relative_to(REPO_ROOT)), line_no, line.strip()))
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print(f"FOUND {len(findings)} POTENTIAL FABRICATED-RESULT CLAIM(S) -- MANUAL REVIEW REQUIRED:\n")
        for path, line_no, line in findings:
            print(f"  {path}:{line_no}: {line}")
        return 1
    print("No hard-coded comparative/percentage claims found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
