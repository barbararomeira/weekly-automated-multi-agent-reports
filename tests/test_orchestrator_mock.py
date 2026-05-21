"""End-to-end integration test — runs run_weekly.py --mock and asserts artefacts.

The mock mode bypasses both LLM agents (copying fixture JSONs through). All
deterministic steps still run for real, so this test exercises:

- KPI pipeline math (Decisions 1-6)
- Data audit invariants (Decision 20)
- Splicer + post-render check (Decision 23)
- Status JSON writer (Decisions 22 + 24)
- Fleet View builder (Decision 11)
- Schema validation on both narrative_blocks.json and verifier_report.json
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fresh_workspace(tmp_path: Path) -> Path:
    """Run the orchestrator inside tmp_path so each test starts clean."""
    return tmp_path


def test_orchestrator_mock_end_to_end(fresh_workspace: Path):
    out_dir    = fresh_workspace / "outputs"
    status_dir = fresh_workspace / "status"
    fleet_view = fresh_workspace / "fleet_view.html"

    result = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "run_weekly.py"),
            "--mock",
            "--as-of", "2026-05-21",
            "--input-path",        str(REPO_ROOT / "fixtures" / "synthetic_cycles.csv"),
            "--output-dir",        str(out_dir),
            "--status-dir",        str(status_dir),
            "--fleet-view-output", str(fleet_view),
            "--config",            str(REPO_ROOT / "config" / "demo.example.yaml"),
            "--report-id",         "demo",
        ],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, (
        f"orchestrator failed (exit {result.returncode})\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    # Pipeline CSVs
    for fn in [
        "weekly_production_opportunity.csv",
        "weekly_opportunity_by_shift.csv",
        "weekly_time_on_product.csv",
        "shift_summary.csv",
        "weekday_summary.csv",
        "data_exclusions.csv",
    ]:
        assert (out_dir / fn).exists(), f"missing pipeline output: {fn}"

    # Narrative + verifier report
    narrative_path = out_dir / "narrative_blocks.json"
    verifier_path  = out_dir / "verifier_report.json"
    assert narrative_path.exists()
    assert verifier_path.exists()

    narrative = json.loads(narrative_path.read_text())
    assert narrative["schema_version"] == "1.0"
    assert 4 <= len(narrative["main_conclusions"]["bullets"]) <= 6
    assert len(narrative["top_3_actions"]) == 3

    verifier = json.loads(verifier_path.read_text())
    assert verifier["report_id"] == "demo"
    assert verifier["status"] == "pass"  # mock fixture is clean
    assert verifier["warnings"] == []

    # Dashboard
    dashboard = out_dir / "weekly_kpi_dashboard.html"
    assert dashboard.exists()
    html = dashboard.read_text()
    assert 'data-test-id="headline-rate-value"' in html
    assert 'data-test-id="headline-trend-direction"' in html

    # Status JSON
    status_path = status_dir / "demo.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text())
    assert status["schema_version"] == "1.0"
    assert status["status"] == "pass"
    assert status["headline"]["unit"] == "bags/h"
    assert status["trend"]["direction"] == "improving"

    # Fleet view — written to the path we passed via --fleet-view-output
    assert fleet_view.exists(), "fleet_view.html not written to the test path"
    fv_html = fleet_view.read_text()
    assert "Demo line — synthetic data" in fv_html or "demo" in fv_html.lower()
