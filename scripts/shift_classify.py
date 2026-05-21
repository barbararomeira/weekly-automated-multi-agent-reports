"""Shift classification (Decision 4 — strict boundary cutover).

The methodology classifies each cycle into a (shift_date, shift_name) by the
*strict* boundaries 06:30 / 14:30 / 22:30 — overriding any upstream tolerance.

This module is used by both the KPI pipeline (step ②) and the data audit
(step ③) so the classification is consistent.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

SHIFT_1ST_START = time(6, 30)
SHIFT_2ND_START = time(14, 30)
SHIFT_3RD_START = time(22, 30)


def classify(ts: datetime) -> tuple[date, str]:
    """Return (shift_date, shift_name) for the given cycle timestamp.

    - 06:30 ≤ time < 14:30 → 1st shift; shift_date = same calendar day.
    - 14:30 ≤ time < 22:30 → 2nd shift; shift_date = same calendar day.
    - 22:30 ≤ time         → 3rd shift; shift_date = same calendar day.
    - time < 06:30         → 3rd shift of the PREVIOUS calendar day.

    The previous-day rule for the early-morning hours is what makes the
    classification consistent with how an operator on the night shift would
    recognise their own shift (they started yesterday at 22:30 and are now
    working past midnight).
    """
    t = ts.time()
    d = ts.date()

    if SHIFT_1ST_START <= t < SHIFT_2ND_START:
        return d, "1st"
    if SHIFT_2ND_START <= t < SHIFT_3RD_START:
        return d, "2nd"
    if t >= SHIFT_3RD_START:
        return d, "3rd"
    # t < SHIFT_1ST_START — early morning belongs to the prior day's 3rd shift
    return d - timedelta(days=1), "3rd"


def week_start(d: date) -> date:
    """Return the Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def weekday_name(d: date) -> str:
    return ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"][d.weekday()]


# ---------------------------------------------------------------------------
# Smoke test for `python scripts/shift_classify.py` standalone debugging
# ---------------------------------------------------------------------------

def _smoke_test() -> None:
    cases = [
        # boundary edge cases
        (datetime(2026, 5, 18,  6, 29), (date(2026, 5, 17), "3rd")),  # just before 06:30
        (datetime(2026, 5, 18,  6, 30), (date(2026, 5, 18), "1st")),  # exactly 06:30
        (datetime(2026, 5, 18, 14, 29), (date(2026, 5, 18), "1st")),  # just before 14:30
        (datetime(2026, 5, 18, 14, 30), (date(2026, 5, 18), "2nd")),  # exactly 14:30
        (datetime(2026, 5, 18, 22, 29), (date(2026, 5, 18), "2nd")),  # just before 22:30
        (datetime(2026, 5, 18, 22, 30), (date(2026, 5, 18), "3rd")),  # exactly 22:30
        (datetime(2026, 5, 18, 23, 59), (date(2026, 5, 18), "3rd")),  # late night
        (datetime(2026, 5, 19,  0,  0), (date(2026, 5, 18), "3rd")),  # past midnight
    ]
    for ts, expected in cases:
        got = classify(ts)
        assert got == expected, f"FAIL: classify({ts}) = {got}, expected {expected}"
        print(f"  OK  {ts.isoformat()}  ->  {got[0].isoformat()} {got[1]}")
    print(f"{len(cases)} cases passed.")


if __name__ == "__main__":
    _smoke_test()
