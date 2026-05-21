"""Synthetic cycle-level data generator for the portfolio demo.

Produces `fixtures/synthetic_cycles.csv` — fake but realistically-shaped
cycle records that exercise the full pipeline downstream.

The data tells a story (so the Insights agent has something with signal):
- Overall loss rate trends downward across 8 weeks (improvement).
- 2nd shift regresses in the last 2 weeks (per-shift concern worth flagging).
- A couple of shift-dates are deliberately sparse (< 20 cycles) so Decision 5's
  exclusion threshold has something to exclude.

Deterministic — same seed produces the same CSV every run. No real customer
data; no real customer / employer names.

Run from the repo root:

    python fixtures/generate.py

Or to a custom path:

    python fixtures/generate.py path/to/output.csv
"""

import csv
import random
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------

SOP_SECONDS = 138                       # nominal cycle target
N_WEEKS = 8                             # weeks of history
START_MONDAY = datetime(2026, 3, 23)    # 8 weeks before 2026-05-18 (a Monday)
SEED = 42

CYCLES_PER_SHIFT_MEAN = 30              # average cycles per shift
CYCLES_PER_SHIFT_STD = 3                # gaussian std (kept small so most shifts stay above the 20-cycle exclusion threshold)

# Probability that a given cycle had any loss (vs ran at SOP)
P_LOSS = 0.6

# Shift definitions (Decision 4: strict boundaries)
SHIFT_DEFS = [
    ("1st", time(6, 30), time(14, 30)),
    ("2nd", time(14, 30), time(22, 30)),
    ("3rd", time(22, 30), time(6, 30)),  # crosses midnight
]

# Cause distribution (within loss cycles)
CAUSES = ["machine_wait", "material", "operator_absent", "rework", "changeover"]
CAUSE_WEIGHTS = [0.30, 0.20, 0.20, 0.15, 0.15]

# Anonymous operator pool
OPERATORS = [f"OP{i:02d}" for i in range(1, 11)]

# Specific shift-dates deliberately made sparse to exercise the exclusion rule.
# Keyed by (week_idx, weekday_offset_from_monday, shift_name) → n_cycles override.
SPARSE_OVERRIDES = {
    (3, 2, "3rd"): 8,    # week 4, Wed 3rd shift — power-outage-style sparse day
    (1, 5, "2nd"): 12,   # week 2, Sat 2nd shift — partial-day; positioned BEFORE the
                         # week-6/7 2nd-shift regression so the two stories don't conflate
}


# ---------------------------------------------------------------------------
# Story: target per-shift loss rate per week (bags/h)
# ---------------------------------------------------------------------------

def target_loss_rate(week_idx: int, shift_name: str) -> float:
    """Bags/h target for this (week, shift) combination.

    Story:
      - 1st shift: cleanest baseline, steady improvement (~-0.4 bags/h/wk).
      - 3rd shift: highest baseline, steeper improvement (~-0.6 bags/h/wk).
      - 2nd shift: middle baseline, steady improvement for the first 6 weeks
        (slope ~-0.5), then a visible regression in weeks 6 and 7 — partial
        bounce-back in week 6, larger bounce in week 7.

    Calibration: the overall (cross-shift) per-week rate slopes downward
    around -0.3 bags/h/wk despite the 2nd-shift regression in the tail —
    so the Insights agent has both a positive headline AND a per-shift
    concern to flag.
    """
    week_0_base = {"1st": 9.0,  "2nd": 10.0, "3rd": 12.0}[shift_name]
    week_slope =  {"1st": -0.40, "2nd": -0.50, "3rd": -0.60}[shift_name]
    rate = week_0_base + week_slope * week_idx

    # 2nd shift regression in the last 2 weeks (weeks 6 and 7, zero-indexed).
    # Hand-tuned so it's visibly worse than the trajectory it WAS on, but not
    # so large that it kills the overall improvement.
    if shift_name == "2nd" and week_idx == 6:
        rate = 8.0   # vs ~7.0 it WAS on track for
    if shift_name == "2nd" and week_idx == 7:
        rate = 9.5   # vs ~6.5 it was on track for

    return max(rate, 4.0)  # floor


# ---------------------------------------------------------------------------
# Cycle generation for one shift
# ---------------------------------------------------------------------------

def generate_shift_cycles(
    shift_start: datetime,
    shift_end: datetime,
    shift_name: str,
    week_idx: int,
    n_cycles: int,
    rng: random.Random,
) -> list[dict]:
    """Generate n_cycles cycle records for a single shift."""
    if n_cycles <= 0:
        return []

    rate = target_loss_rate(week_idx, shift_name)
    # avg loss per cycle (across all cycles, including no-loss ones)
    avg_loss_seconds = rate * SOP_SECONDS * SOP_SECONDS / 3600
    # mean loss conditional on the cycle being a loss-cycle
    loss_mean_when_loss = max(avg_loss_seconds / P_LOSS, 1.0)

    shift_duration = (shift_end - shift_start).total_seconds()
    gap = shift_duration / n_cycles

    rows = []
    for i in range(n_cycles):
        # Evenly distributed start times with ±10 % jitter
        offset = i * gap + rng.uniform(-gap * 0.1, gap * 0.1)
        ts_start = shift_start + timedelta(seconds=offset)

        if rng.random() < P_LOSS:
            loss_s = rng.expovariate(1.0 / loss_mean_when_loss)
            cycle_s = SOP_SECONDS + loss_s
            cause = rng.choices(CAUSES, weights=CAUSE_WEIGHTS, k=1)[0]
        else:
            loss_s = 0.0
            cycle_s = float(SOP_SECONDS)
            cause = ""

        operator = rng.choice(OPERATORS)
        rows.append({
            "timestamp_start": ts_start.isoformat(timespec="seconds"),
            "timestamp_end": (ts_start + timedelta(seconds=cycle_s)).isoformat(timespec="seconds"),
            "cycle_seconds": round(cycle_s, 1),
            "loss_seconds": round(loss_s, 1),
            "loss_cause": cause,
            "operator_id": operator,
        })

    return rows


# ---------------------------------------------------------------------------
# Top-level generator
# ---------------------------------------------------------------------------

def generate(out_path: Path) -> int:
    """Generate the full synthetic CSV. Returns row count."""
    rng = random.Random(SEED)
    all_rows = []

    for week_idx in range(N_WEEKS):
        for day_offset in range(7):
            day_date = (START_MONDAY + timedelta(weeks=week_idx, days=day_offset)).date()
            for shift_name, start_time, end_time in SHIFT_DEFS:
                shift_start = datetime.combine(day_date, start_time)
                if shift_name == "3rd":
                    shift_end = datetime.combine(day_date, end_time) + timedelta(days=1)
                else:
                    shift_end = datetime.combine(day_date, end_time)

                key = (week_idx, day_offset, shift_name)
                if key in SPARSE_OVERRIDES:
                    n_cycles = SPARSE_OVERRIDES[key]
                else:
                    n_cycles = max(0, int(rng.gauss(CYCLES_PER_SHIFT_MEAN, CYCLES_PER_SHIFT_STD)))

                all_rows.extend(generate_shift_cycles(
                    shift_start, shift_end, shift_name, week_idx, n_cycles, rng,
                ))

    # Assign monotonic cycle_id after sorting by timestamp_start so the IDs
    # increase with time.
    all_rows.sort(key=lambda r: r["timestamp_start"])
    for cid, row in enumerate(all_rows, start=1):
        row["cycle_id"] = cid

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "cycle_id", "timestamp_start", "timestamp_end",
            "cycle_seconds", "loss_seconds", "loss_cause", "operator_id",
        ])
        writer.writeheader()
        writer.writerows(all_rows)

    return len(all_rows)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
    else:
        # default: write into fixtures/synthetic_cycles.csv next to this script
        out = Path(__file__).parent / "synthetic_cycles.csv"

    n = generate(out)
    print(f"Generated {n} cycle records → {out}")
