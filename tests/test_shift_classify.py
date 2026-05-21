"""Tests for scripts/shift_classify.py — Decision 4 strict boundaries."""

from datetime import date, datetime

import pytest

from scripts.shift_classify import classify, week_start, weekday_name


# ---------------------------------------------------------------------------
# Boundary edge cases — the whole point of Decision 4
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ts, expected", [
    # 06:30 boundary
    (datetime(2026, 5, 18,  6, 29, 59), (date(2026, 5, 17), "3rd")),
    (datetime(2026, 5, 18,  6, 30,  0), (date(2026, 5, 18), "1st")),
    # 14:30 boundary
    (datetime(2026, 5, 18, 14, 29, 59), (date(2026, 5, 18), "1st")),
    (datetime(2026, 5, 18, 14, 30,  0), (date(2026, 5, 18), "2nd")),
    # 22:30 boundary
    (datetime(2026, 5, 18, 22, 29, 59), (date(2026, 5, 18), "2nd")),
    (datetime(2026, 5, 18, 22, 30,  0), (date(2026, 5, 18), "3rd")),
    # late-night and past midnight, both → previous day 3rd
    (datetime(2026, 5, 18, 23, 59, 59), (date(2026, 5, 18), "3rd")),
    (datetime(2026, 5, 19,  0,  0,  0), (date(2026, 5, 18), "3rd")),
    (datetime(2026, 5, 19,  4, 15,  0), (date(2026, 5, 18), "3rd")),
])
def test_classify(ts, expected):
    assert classify(ts) == expected


def test_week_start_monday_to_monday():
    # Each day of the same week should map to the same Monday
    monday = date(2026, 5, 18)  # a Monday
    for offset in range(7):
        d = date.fromordinal(monday.toordinal() + offset)
        assert week_start(d) == monday


def test_weekday_name():
    assert weekday_name(date(2026, 5, 18)) == "Monday"
    assert weekday_name(date(2026, 5, 24)) == "Sunday"
