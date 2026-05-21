"""KPI pipeline (step ② of the weekly run).

Reads cycle-level raw data, applies shift classification (Decision 4) and the
exclusion threshold (Decision 5), then computes rates per Decisions 1, 2, 3,
and 6:

- Total bags-of-opportunity per group = round(Σ loss_seconds / SOP)
  (Decision 2 — round once, at the end).
- Per-cause bags allocated by largest-remainder method (Decision 3).
- Loss rate = bags / productive_hours (Decision 1 — per-productive-hour
  denominator).

Writes six CSVs into the output directory:

  weekly_production_opportunity.csv     — headline weekly aggregate + cause split
  weekly_opportunity_by_shift.csv       — per-week-per-shift breakdown
  weekly_time_on_product.csv            — productive hours per week
  shift_summary.csv                     — per-shift across all weeks
  weekday_summary.csv                   — per-weekday across all weeks
  data_exclusions.csv                   — shift-dates excluded by Decision 5
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from scripts.shift_classify import classify, week_start, weekday_name

SOP_SECONDS = 138
EXCLUSION_THRESHOLD = 20  # Decision 5 — exclude shift-dates with fewer than this many cycles
CAUSES = ["machine_wait", "material", "operator_absent", "rework", "changeover"]
WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Reading + classification
# ---------------------------------------------------------------------------

def read_cycles(input_path: Path) -> list[dict]:
    """Read raw cycle CSV and decorate each row with derived shift fields.

    Adds:
        shift_date (str, YYYY-MM-DD), shift_name ("1st"/"2nd"/"3rd"),
        week_start (str, YYYY-MM-DD), weekday (str)
    """
    rows: list[dict] = []
    with input_path.open() as f:
        for raw in csv.DictReader(f):
            ts = datetime.fromisoformat(raw["timestamp_start"])
            sd, sn = classify(ts)
            raw["shift_date"] = sd.isoformat()
            raw["shift_name"] = sn
            raw["week_start"] = week_start(sd).isoformat()
            raw["weekday"] = weekday_name(sd)
            raw["cycle_seconds"] = float(raw["cycle_seconds"])
            raw["loss_seconds"] = float(raw["loss_seconds"])
            rows.append(raw)
    return rows


def excluded_shift_dates(rows: list[dict]) -> set[tuple[str, str]]:
    """Return the set of (shift_date, shift_name) below the threshold (Decision 5)."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        counts[(r["shift_date"], r["shift_name"])] += 1
    return {k for k, n in counts.items() if n < EXCLUSION_THRESHOLD}


# ---------------------------------------------------------------------------
# Aggregation primitives
# ---------------------------------------------------------------------------

def _empty_agg() -> dict:
    return {
        "cycles": 0,
        "loss_seconds": 0.0,
        "productive_seconds": 0.0,
        "loss_by_cause": defaultdict(float),
    }


def aggregate(rows: list[dict], key_fn) -> dict:
    """Group rows by key_fn(row) and accumulate the four metrics."""
    out: dict = defaultdict(_empty_agg)
    for r in rows:
        agg = out[key_fn(r)]
        agg["cycles"] += 1
        agg["loss_seconds"] += r["loss_seconds"]
        # productive seconds = total cycle time minus the loss portion
        agg["productive_seconds"] += r["cycle_seconds"] - r["loss_seconds"]
        if r["loss_cause"]:
            agg["loss_by_cause"][r["loss_cause"]] += r["loss_seconds"]
    return out


def bags_from_loss(loss_seconds: float) -> int:
    """Decision 2 — round once, at the end."""
    return round(loss_seconds / SOP_SECONDS)


def largest_remainder(values: list[float], total: int) -> list[int]:
    """Allocate `total` units across `values` by the largest-remainder method.

    Decision 3 — each cause gets floor(exact_share) bags; the leftover bags
    are distributed to the causes with the largest fractional remainders.
    Guarantees Σ outputs = total (when total ≥ 0 and Σ values > 0).
    """
    if total <= 0 or sum(values) <= 0:
        return [0] * len(values)
    s = sum(values)
    exact = [v / s * total for v in values]
    floors = [int(e) for e in exact]
    remainders = sorted(
        [(e - f, i) for i, (e, f) in enumerate(zip(exact, floors))],
        key=lambda x: (-x[0], x[1]),
    )
    short = total - sum(floors)
    for k in range(short):
        floors[remainders[k][1]] += 1
    return floors


def loss_rate(bags: int, productive_hours: float) -> float:
    return bags / productive_hours if productive_hours > 0 else 0.0


# ---------------------------------------------------------------------------
# CSV writers — each is small enough to read top-to-bottom
# ---------------------------------------------------------------------------

def _write_weekly_production_opportunity(path: Path, by_week: dict) -> None:
    cols = ["week_start", "cycles", "productive_hours", "total_bags",
            "loss_rate_bags_per_hour"]
    cols += [f"{c}_bags" for c in CAUSES]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for wk in sorted(by_week):
            agg = by_week[wk]
            bags = bags_from_loss(agg["loss_seconds"])
            ph = agg["productive_seconds"] / 3600
            cause_values = [agg["loss_by_cause"].get(c, 0.0) for c in CAUSES]
            cause_bags = largest_remainder(cause_values, bags)
            row = {
                "week_start": wk,
                "cycles": agg["cycles"],
                "productive_hours": round(ph, 2),
                "total_bags": bags,
                "loss_rate_bags_per_hour": round(loss_rate(bags, ph), 2),
            }
            for c, b in zip(CAUSES, cause_bags):
                row[f"{c}_bags"] = b
            w.writerow(row)


def _write_weekly_by_shift(path: Path, by_week_shift: dict) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "week_start", "shift_name", "cycles", "productive_hours",
            "total_bags", "loss_rate_bags_per_hour",
        ])
        w.writeheader()
        for (wk, sn) in sorted(by_week_shift):
            agg = by_week_shift[(wk, sn)]
            bags = bags_from_loss(agg["loss_seconds"])
            ph = agg["productive_seconds"] / 3600
            w.writerow({
                "week_start": wk,
                "shift_name": sn,
                "cycles": agg["cycles"],
                "productive_hours": round(ph, 2),
                "total_bags": bags,
                "loss_rate_bags_per_hour": round(loss_rate(bags, ph), 2),
            })


def _write_weekly_time_on_product(path: Path, by_week: dict) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["week_start", "productive_hours", "cycles"])
        w.writeheader()
        for wk in sorted(by_week):
            agg = by_week[wk]
            w.writerow({
                "week_start": wk,
                "productive_hours": round(agg["productive_seconds"] / 3600, 2),
                "cycles": agg["cycles"],
            })


def _write_shift_summary(path: Path, by_shift: dict) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "shift_name", "cycles", "productive_hours", "total_bags",
            "loss_rate_bags_per_hour",
        ])
        w.writeheader()
        for sn in ["1st", "2nd", "3rd"]:
            if sn not in by_shift:
                continue
            agg = by_shift[sn]
            bags = bags_from_loss(agg["loss_seconds"])
            ph = agg["productive_seconds"] / 3600
            w.writerow({
                "shift_name": sn,
                "cycles": agg["cycles"],
                "productive_hours": round(ph, 2),
                "total_bags": bags,
                "loss_rate_bags_per_hour": round(loss_rate(bags, ph), 2),
            })


def _write_weekday_summary(path: Path, by_weekday: dict) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "weekday", "cycles", "productive_hours", "total_bags",
            "loss_rate_bags_per_hour",
        ])
        w.writeheader()
        for wd in WEEKDAY_ORDER:
            if wd not in by_weekday:
                continue
            agg = by_weekday[wd]
            bags = bags_from_loss(agg["loss_seconds"])
            ph = agg["productive_seconds"] / 3600
            w.writerow({
                "weekday": wd,
                "cycles": agg["cycles"],
                "productive_hours": round(ph, 2),
                "total_bags": bags,
                "loss_rate_bags_per_hour": round(loss_rate(bags, ph), 2),
            })


def _write_exclusions(path: Path, raw_rows: list[dict],
                      excluded: set[tuple[str, str]]) -> None:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in raw_rows:
        counts[(r["shift_date"], r["shift_name"])] += 1
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["shift_date", "shift_name", "n_cycles"])
        w.writeheader()
        for (sd, sn) in sorted(excluded):
            w.writerow({"shift_date": sd, "shift_name": sn,
                        "n_cycles": counts[(sd, sn)]})


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def compute_kpis(input_path: Path, output_dir: Path) -> None:
    """Run the full pipeline. Reads cycles, computes aggregates, writes CSVs."""
    raw_rows = read_cycles(input_path)
    excluded = excluded_shift_dates(raw_rows)
    included = [r for r in raw_rows
                if (r["shift_date"], r["shift_name"]) not in excluded]

    by_week        = aggregate(included, lambda r: r["week_start"])
    by_week_shift  = aggregate(included, lambda r: (r["week_start"], r["shift_name"]))
    by_shift       = aggregate(included, lambda r: r["shift_name"])
    by_weekday     = aggregate(included, lambda r: r["weekday"])

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_weekly_production_opportunity(output_dir / "weekly_production_opportunity.csv", by_week)
    _write_weekly_by_shift                (output_dir / "weekly_opportunity_by_shift.csv",  by_week_shift)
    _write_weekly_time_on_product         (output_dir / "weekly_time_on_product.csv",       by_week)
    _write_shift_summary                  (output_dir / "shift_summary.csv",                by_shift)
    _write_weekday_summary                (output_dir / "weekday_summary.csv",              by_weekday)
    _write_exclusions                     (output_dir / "data_exclusions.csv", raw_rows, excluded)

    print(
        f"Read {len(raw_rows)} cycles → "
        f"included {len(included)}, excluded {len(raw_rows) - len(included)} "
        f"({len(excluded)} shift-dates below the {EXCLUSION_THRESHOLD}-cycle threshold). "
        f"Wrote 6 CSVs to {output_dir}/"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="KPI pipeline (step ② of the weekly run).")
    parser.add_argument(
        "--input", default="fixtures/synthetic_cycles.csv",
        help="Path to the cycle-level CSV input (default: %(default)s)",
    )
    parser.add_argument(
        "--output", default="outputs/",
        help="Directory to write the aggregate CSVs into (default: %(default)s)",
    )
    args = parser.parse_args()
    compute_kpis(Path(args.input), Path(args.output))


if __name__ == "__main__":
    main()
