"""
Mandate test cases 9-11 -- the most important category in this phase: no
fabricated result anywhere, and no way to produce one from a production
code path.
"""
import subprocess
import sys
from pathlib import Path

from app.modules.evaluation.synthetic_fixtures import SYNTHETIC_LABEL, build_synthetic_demo_report

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class TestStaticFabricationScan:
    """Test 9: the guardrail script itself runs clean against the real repo."""

    def test_the_guardrail_script_runs_and_currently_finds_nothing(self):
        script = REPO_ROOT / "backend" / "scripts" / "check_no_fabricated_results.py"
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, cwd=str(REPO_ROOT / "backend")
        )
        assert result.returncode == 0, f"Fabrication guardrail found matches:\n{result.stdout}"

    def test_the_scanner_actually_detects_a_planted_comparative_claim(self, tmp_path):
        """The scanner is only meaningful if it can actually catch
        something -- prove it does, against a throwaway fixture file, not
        the real repo."""
        sys.path.insert(0, str(REPO_ROOT / "backend" / "scripts"))
        import check_no_fabricated_results as guard

        planted = tmp_path / "docs"
        planted.mkdir()
        bad_file = planted / "fake_report.md"
        bad_file.write_text("The adaptive condition improves mastery by 23% over the baseline.")

        original_scan_dirs = guard.SCAN_DIRS
        original_root = guard.REPO_ROOT
        try:
            guard.REPO_ROOT = tmp_path
            guard.SCAN_DIRS = ["docs"]
            findings = guard.scan()
        finally:
            guard.REPO_ROOT = original_root
            guard.SCAN_DIRS = original_scan_dirs

        assert len(findings) == 1
        assert "23%" in findings[0][2]


class TestSyntheticArtifactIsLabeled:
    """Test 10: a 'sample results' artifact built from the harness is
    unambiguously labeled as synthetic, never presented as real."""

    def test_demo_report_title_and_caption_both_declare_synthetic_data(self):
        report = build_synthetic_demo_report()
        assert SYNTHETIC_LABEL in report["title"]
        assert SYNTHETIC_LABEL in report["caption"]
        assert "not real learners" in report["caption"].lower() or "not a claim" in report["caption"].lower()

    def test_demo_report_still_uses_the_real_metric_functions(self):
        """Not a fake chart -- the numbers really did come from
        metrics.py's real functions, just fed synthetic input."""
        report = build_synthetic_demo_report()
        assert report["citation_precision_recall"]["precision"] == 6 / 8
        assert "p50" in report["latency_percentiles"]


class TestNoProductionPathReachesSyntheticData:
    """Test 11: no router (or other production-reachable module) imports
    synthetic_fixtures -- a "seed fake results" utility must not exist
    reachable from a live request."""

    def test_no_router_module_imports_synthetic_fixtures(self):
        app_dir = REPO_ROOT / "backend" / "app"
        offending = []
        for path in app_dir.rglob("router.py"):
            text = path.read_text(encoding="utf-8")
            if "synthetic_fixtures" in text:
                offending.append(str(path))
        assert offending == [], f"Router(s) importing synthetic fixture data: {offending}"

    def test_no_service_module_imports_synthetic_fixtures_either(self):
        app_dir = REPO_ROOT / "backend" / "app"
        offending = []
        for path in app_dir.rglob("service.py"):
            if "evaluation" in str(path):
                continue  # the evaluation service itself may legitimately reference metrics, not fixtures
            text = path.read_text(encoding="utf-8")
            if "synthetic_fixtures" in text:
                offending.append(str(path))
        assert offending == []

    def test_evaluation_router_has_no_seed_or_demo_endpoint(self):
        router_path = REPO_ROOT / "backend" / "app" / "modules" / "evaluation" / "router.py"
        text = router_path.read_text(encoding="utf-8").lower()
        for banned in ("seed", "fake", "demo", "synthetic"):
            assert banned not in text, f"evaluation/router.py references {banned!r} -- must not expose fixture data"
