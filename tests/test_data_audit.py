"""Tests for scripts/data_audit.py — verify it catches each bug category."""

import csv
import shutil
from pathlib import Path

import pytest

from scripts.data_audit import run_audit
from scripts.kpi_pipeline import compute_kpis


@pytest.fixture
def clean_outputs(tmp_path: Path) -> Path:
    """Run the pipeline on the synthetic fixture into tmp_path/outputs/."""
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_cycles.csv"
    out_dir = tmp_path / "outputs"
    compute_kpis(fixture, out_dir)
    return out_dir


def test_audit_passes_on_clean_pipeline_output(clean_outputs: Path):
    violations = run_audit(clean_outputs)
    assert violations == [], f"unexpected violations on clean output: {violations}"


def test_audit_catches_missing_file(clean_outputs: Path):
    (clean_outputs / "shift_summary.csv").unlink()
    violations = run_audit(clean_outputs)
    assert any("missing file" in v and "shift_summary.csv" in v for v in violations)


def test_audit_catches_empty_file(clean_outputs: Path):
    (clean_outputs / "weekday_summary.csv").write_text("")
    violations = run_audit(clean_outputs)
    assert any("empty file" in v and "weekday_summary.csv" in v for v in violations)


def test_audit_catches_negative_rate(clean_outputs: Path):
    path = clean_outputs / "weekly_production_opportunity.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["loss_rate_bags_per_hour"] = "-1.0"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    violations = run_audit(clean_outputs)
    assert any("negative" in v for v in violations)


def test_audit_catches_cause_sum_mismatch(clean_outputs: Path):
    path = clean_outputs / "weekly_production_opportunity.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["total_bags"] = "999"  # break the cause-sum invariant
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    violations = run_audit(clean_outputs)
    assert any("cause-bag sum" in v or "bags mismatch" in v for v in violations)


def test_audit_catches_non_numeric_value(clean_outputs: Path):
    path = clean_outputs / "shift_summary.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["cycles"] = "not-a-number"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    violations = run_audit(clean_outputs)
    assert any("not numeric" in v for v in violations)
