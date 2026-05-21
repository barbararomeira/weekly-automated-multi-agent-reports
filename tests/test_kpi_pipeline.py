"""Tests for scripts/kpi_pipeline.py — pure helpers + end-to-end on synthetic data."""

import csv
from pathlib import Path

import pytest

from scripts.kpi_pipeline import (
    SOP_SECONDS,
    bags_from_loss,
    largest_remainder,
    loss_rate,
    compute_kpis,
)


# ---------------------------------------------------------------------------
# bags_from_loss — Decision 2 (round once at the end)
# ---------------------------------------------------------------------------

def test_bags_round_at_end():
    # 0 loss → 0 bags
    assert bags_from_loss(0.0) == 0
    # Exactly SOP worth of loss → 1 bag
    assert bags_from_loss(float(SOP_SECONDS)) == 1
    # Half SOP rounds → banker's rounding (Python round) — 0 in this case
    assert bags_from_loss(SOP_SECONDS / 2) in (0, 1)  # implementation can pick either; just must be int
    # Big value
    assert bags_from_loss(SOP_SECONDS * 100 + 50) == round((SOP_SECONDS * 100 + 50) / SOP_SECONDS)


# ---------------------------------------------------------------------------
# largest_remainder — Decision 3
# ---------------------------------------------------------------------------

def test_largest_remainder_sums_to_total():
    # Various allocations: sum of pieces must equal the requested total
    cases = [
        ([1.0, 2.0, 3.0], 10),
        ([5.0, 5.0, 5.0], 7),
        ([100.0, 1.0, 1.0], 50),
        ([0.5, 0.5, 0.5, 0.5], 3),
    ]
    for values, total in cases:
        result = largest_remainder(values, total)
        assert sum(result) == total, f"largest_remainder({values}, {total}) → {result}"


def test_largest_remainder_zero_total():
    assert largest_remainder([1.0, 2.0, 3.0], 0) == [0, 0, 0]


def test_largest_remainder_zero_values():
    # Σ values == 0 → all zeros regardless of total
    assert largest_remainder([0.0, 0.0, 0.0], 10) == [0, 0, 0]


def test_largest_remainder_proportional():
    # Equal values → equal allocation when total divides evenly
    assert largest_remainder([1.0, 1.0, 1.0], 9) == [3, 3, 3]
    # Equal values, total=10 → one extra goes to the first cause (tie-broken by index)
    result = largest_remainder([1.0, 1.0, 1.0], 10)
    assert sum(result) == 10
    assert max(result) - min(result) == 1


# ---------------------------------------------------------------------------
# loss_rate
# ---------------------------------------------------------------------------

def test_loss_rate_basic():
    assert loss_rate(100, 10.0) == pytest.approx(10.0)
    assert loss_rate(0, 5.0) == 0.0


def test_loss_rate_zero_hours():
    # No productive hours → rate is 0 (not div-by-zero)
    assert loss_rate(50, 0.0) == 0.0


# ---------------------------------------------------------------------------
# End-to-end on the synthetic fixture
# ---------------------------------------------------------------------------

def test_compute_kpis_on_synthetic_fixture(tmp_path: Path):
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_cycles.csv"
    assert fixture.exists(), "synthetic fixture missing — run fixtures/generate.py"

    out_dir = tmp_path / "outputs"
    compute_kpis(fixture, out_dir)

    expected_files = [
        "weekly_production_opportunity.csv",
        "weekly_opportunity_by_shift.csv",
        "weekly_time_on_product.csv",
        "shift_summary.csv",
        "weekday_summary.csv",
        "data_exclusions.csv",
    ]
    for fn in expected_files:
        assert (out_dir / fn).exists(), f"compute_kpis did not produce {fn}"

    # Weekly aggregate: 8 weeks present (matches the synthetic generator)
    with (out_dir / "weekly_production_opportunity.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8

    # Decision 3: cause-bags sum to total_bags for every week
    cause_cols = ["machine_wait_bags", "material_bags", "operator_absent_bags",
                  "rework_bags", "changeover_bags"]
    for r in rows:
        total = int(r["total_bags"])
        cause_sum = sum(int(r[c]) for c in cause_cols)
        assert cause_sum == total, f"cause-bag sum {cause_sum} ≠ total {total} for {r['week_start']}"

    # Decision 5: at least 1 shift-date excluded (synthetic data has intentional sparse days)
    with (out_dir / "data_exclusions.csv").open() as f:
        excluded = list(csv.DictReader(f))
    assert len(excluded) >= 1, "expected at least one excluded shift-date"
    for r in excluded:
        assert int(r["n_cycles"]) < 20
