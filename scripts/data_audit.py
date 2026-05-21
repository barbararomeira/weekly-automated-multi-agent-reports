"""Pipeline self-audit (step ③ of the weekly run, Decision 20).

Invariant-only bug detection on the KPI pipeline's output CSVs. Halts the
pipeline when the data is BROKEN in a way the rest of the system cannot
recover from. Does NOT pre-judge data adequacy — that judgement (sparse
weeks, sufficient data for a slope, etc.) is the Insights agent's job.

Checks (each producing zero-or-more violations):

  1. Existence       — every expected output CSV exists and is non-empty.
  2. Schema          — each CSV has its required columns.
  3. Numeric ranges  — cycles / hours / bags / rate are non-negative and
                       non-NaN; rate is within a plausible upper bound.
  4. Cause split     — Σ cause-bags == total_bags per week (Decision 3
                       largest-remainder guarantees this).
  5. Cross-aggregate — per-week cycles + productive_hours equal the sum
                       across that week's shifts (rounding tolerance on
                       bags only — independent rounding can drift ±1-2).

Exits 0 when clean; exits 1 and prints violations otherwise.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

REQUIRED_FILES = [
    "weekly_production_opportunity.csv",
    "weekly_opportunity_by_shift.csv",
    "weekly_time_on_product.csv",
    "shift_summary.csv",
    "weekday_summary.csv",
]

# Per-file expected columns (subset that's safety-checked here)
SCHEMA: dict[str, list[str]] = {
    "weekly_production_opportunity.csv": [
        "week_start", "cycles", "productive_hours", "total_bags",
        "loss_rate_bags_per_hour",
        "machine_wait_bags", "material_bags", "operator_absent_bags",
        "rework_bags", "changeover_bags",
    ],
    "weekly_opportunity_by_shift.csv": [
        "week_start", "shift_name", "cycles", "productive_hours",
        "total_bags", "loss_rate_bags_per_hour",
    ],
    "weekly_time_on_product.csv": [
        "week_start", "productive_hours", "cycles",
    ],
    "shift_summary.csv": [
        "shift_name", "cycles", "productive_hours", "total_bags",
        "loss_rate_bags_per_hour",
    ],
    "weekday_summary.csv": [
        "weekday", "cycles", "productive_hours", "total_bags",
        "loss_rate_bags_per_hour",
    ],
}

NUMERIC_COLS: dict[str, list[str]] = {
    "weekly_production_opportunity.csv": [
        "cycles", "productive_hours", "total_bags", "loss_rate_bags_per_hour",
    ],
    "weekly_opportunity_by_shift.csv": [
        "cycles", "productive_hours", "total_bags", "loss_rate_bags_per_hour",
    ],
    "weekly_time_on_product.csv": ["productive_hours", "cycles"],
    "shift_summary.csv": [
        "cycles", "productive_hours", "total_bags", "loss_rate_bags_per_hour",
    ],
    "weekday_summary.csv": [
        "cycles", "productive_hours", "total_bags", "loss_rate_bags_per_hour",
    ],
}

CAUSE_COLS = [
    "machine_wait_bags", "material_bags", "operator_absent_bags",
    "rework_bags", "changeover_bags",
]

MAX_PLAUSIBLE_RATE = 1000.0  # bags/h — anything above this is a code bug


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Individual checks — each appends to `violations`
# ---------------------------------------------------------------------------

def _check_existence(output_dir: Path, violations: list[str]) -> None:
    for filename in REQUIRED_FILES:
        path = output_dir / filename
        if not path.exists():
            violations.append(f"missing file: {filename}")
        elif path.stat().st_size == 0:
            violations.append(f"empty file: {filename}")


def _check_schema(output_dir: Path, violations: list[str]) -> None:
    for filename, expected_cols in SCHEMA.items():
        path = output_dir / filename
        if not path.exists():
            continue  # already reported by existence check
        rows = _read_csv(path)
        if not rows:
            violations.append(f"{filename}: no data rows")
            continue
        actual_cols = set(rows[0].keys())
        missing = set(expected_cols) - actual_cols
        if missing:
            violations.append(
                f"{filename}: missing columns {sorted(missing)}"
            )


def _check_numeric_values(output_dir: Path, violations: list[str]) -> None:
    for filename, cols in NUMERIC_COLS.items():
        path = output_dir / filename
        if not path.exists():
            continue
        for row_idx, row in enumerate(_read_csv(path), start=2):  # row 2 = first data row in the CSV file
            for col in cols:
                val_str = row.get(col, "")
                if val_str == "":
                    violations.append(
                        f"{filename} row {row_idx}: column {col!r} is empty"
                    )
                    continue
                try:
                    val = float(val_str)
                except ValueError:
                    violations.append(
                        f"{filename} row {row_idx}: column {col!r} not numeric: {val_str!r}"
                    )
                    continue
                if val != val:  # NaN check
                    violations.append(
                        f"{filename} row {row_idx}: column {col!r} is NaN"
                    )
                    continue
                if val < 0:
                    violations.append(
                        f"{filename} row {row_idx}: column {col!r} is negative ({val})"
                    )
                if col == "loss_rate_bags_per_hour" and val > MAX_PLAUSIBLE_RATE:
                    violations.append(
                        f"{filename} row {row_idx}: loss_rate {val} > {MAX_PLAUSIBLE_RATE} (likely a code bug)"
                    )


def _check_cause_sums(output_dir: Path, violations: list[str]) -> None:
    """Decision 3 — cause-bags must sum to total_bags per week."""
    path = output_dir / "weekly_production_opportunity.csv"
    if not path.exists():
        return
    for row_idx, row in enumerate(_read_csv(path), start=2):
        try:
            total = int(row.get("total_bags", 0))
            cause_sum = sum(int(row.get(c, 0) or 0) for c in CAUSE_COLS)
        except ValueError as e:
            violations.append(
                f"weekly_production_opportunity.csv row {row_idx}: non-integer bags ({e})"
            )
            continue
        if cause_sum != total:
            violations.append(
                f"weekly_production_opportunity.csv row {row_idx}: "
                f"cause-bag sum {cause_sum} != total_bags {total} "
                f"for week_start={row.get('week_start')}"
            )


def _check_weekly_vs_shift_aggregates(output_dir: Path, violations: list[str]) -> None:
    """Per-week weekly aggregates should match sum across that week's shifts."""
    p_weekly = output_dir / "weekly_production_opportunity.csv"
    p_shift  = output_dir / "weekly_opportunity_by_shift.csv"
    if not (p_weekly.exists() and p_shift.exists()):
        return

    by_week_from_shifts: dict[str, dict] = defaultdict(
        lambda: {"cycles": 0, "productive_hours": 0.0, "total_bags": 0}
    )
    for row in _read_csv(p_shift):
        wk = row["week_start"]
        by_week_from_shifts[wk]["cycles"]            += int(row["cycles"])
        by_week_from_shifts[wk]["productive_hours"]  += float(row["productive_hours"])
        by_week_from_shifts[wk]["total_bags"]        += int(row["total_bags"])

    for row in _read_csv(p_weekly):
        wk = row["week_start"]
        if wk not in by_week_from_shifts:
            violations.append(
                f"week {wk}: present in weekly_production_opportunity but missing in weekly_opportunity_by_shift"
            )
            continue
        expected = by_week_from_shifts[wk]

        cycles_w = int(row["cycles"])
        if cycles_w != expected["cycles"]:
            violations.append(
                f"week {wk}: cycles mismatch — weekly={cycles_w} vs sum-of-shifts={expected['cycles']}"
            )

        ph_w = float(row["productive_hours"])
        if abs(ph_w - expected["productive_hours"]) > 0.05:
            violations.append(
                f"week {wk}: productive_hours mismatch — weekly={ph_w} vs sum-of-shifts={expected['productive_hours']:.2f}"
            )

        # total_bags can drift by up to 1-2 across 3 shifts due to independent rounding
        bags_w = int(row["total_bags"])
        if abs(bags_w - expected["total_bags"]) > 2:
            violations.append(
                f"week {wk}: bags mismatch >2 — weekly={bags_w} vs sum-of-shifts={expected['total_bags']}"
            )


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def run_audit(output_dir: Path) -> list[str]:
    violations: list[str] = []
    _check_existence(output_dir, violations)
    _check_schema(output_dir, violations)
    _check_numeric_values(output_dir, violations)
    _check_cause_sums(output_dir, violations)
    _check_weekly_vs_shift_aggregates(output_dir, violations)
    return violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline self-audit (step ③ of the weekly run, Decision 20)."
    )
    parser.add_argument(
        "--output", default="outputs/",
        help="Output directory to audit (default: %(default)s)",
    )
    args = parser.parse_args()

    violations = run_audit(Path(args.output))
    if violations:
        print(f"AUDIT FAILED — {len(violations)} violation(s):")
        for v in violations:
            print(f"  ✗ {v}")
        sys.exit(1)
    print(f"AUDIT PASSED — all invariants OK in {args.output}/")


if __name__ == "__main__":
    main()
