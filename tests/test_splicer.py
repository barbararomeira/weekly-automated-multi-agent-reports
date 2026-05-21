"""Tests for scripts/splicer.py — pure helpers + post-render check tampering."""

from datetime import date

import pytest

from scripts.splicer import (
    FLAT_THRESHOLD,
    classify_trend,
    latest_complete_week,
    ols_slope,
    post_render_check,
    trend_color,
)


# ---------------------------------------------------------------------------
# OLS slope (Decision 6)
# ---------------------------------------------------------------------------

def test_ols_slope_perfectly_linear():
    # y = 2x: slope should be exactly 2
    xs = [0, 1, 2, 3, 4]
    ys = [0, 2, 4, 6, 8]
    assert ols_slope(xs, ys) == pytest.approx(2.0)


def test_ols_slope_perfectly_descending():
    xs = [0, 1, 2, 3]
    ys = [10, 8, 6, 4]
    assert ols_slope(xs, ys) == pytest.approx(-2.0)


def test_ols_slope_flat():
    xs = [0, 1, 2, 3]
    ys = [5, 5, 5, 5]
    assert ols_slope(xs, ys) == pytest.approx(0.0)


def test_ols_slope_too_few_points():
    assert ols_slope([], []) == 0.0
    assert ols_slope([1.0], [5.0]) == 0.0


# ---------------------------------------------------------------------------
# classify_trend / trend_color (Decisions 21 + 22)
# ---------------------------------------------------------------------------

def test_classify_trend_thresholds():
    # Just below the flat threshold (either direction) → flat
    assert classify_trend(0.0) == "flat"
    assert classify_trend(FLAT_THRESHOLD - 0.001) == "flat"
    assert classify_trend(-FLAT_THRESHOLD + 0.001) == "flat"
    # Past the threshold → categorical
    assert classify_trend(-1.0) == "improving"
    assert classify_trend( 1.0) == "worsening"


def test_trend_color_keys():
    # All three directions must produce a colour
    for d in ("improving", "worsening", "flat"):
        c = trend_color(d)
        assert c.startswith("#") and len(c) == 7


# ---------------------------------------------------------------------------
# latest_complete_week (Decision 14)
# ---------------------------------------------------------------------------

def _row(week_start: str) -> dict:
    return {"week_start": week_start, "loss_rate_bags_per_hour": "9.0"}


def test_latest_complete_week_picks_latest_complete():
    rows = [
        _row("2026-05-04"),  # week_end 2026-05-10
        _row("2026-05-11"),  # week_end 2026-05-17
        _row("2026-05-18"),  # week_end 2026-05-24 — incomplete on 2026-05-21
    ]
    result = latest_complete_week(rows, date(2026, 5, 21))
    assert result["week_start"] == "2026-05-11"


def test_latest_complete_week_returns_none_when_all_in_future():
    rows = [_row("2099-01-01")]
    assert latest_complete_week(rows, date(2026, 5, 21)) is None


# ---------------------------------------------------------------------------
# post-render check (Decision 23) — verify it catches tampering
# ---------------------------------------------------------------------------

CLEAN_HTML = '''
<html><body>
<span data-test-id="headline-rate-value">7.77</span>
<span data-test-id="headline-rate-unit">bags/h</span>
<span data-test-id="headline-trend-direction">improving</span>
</body></html>
'''.strip()

EXPECTED_OK = {
    "headline_value":  7.77,
    "headline_unit":   "bags/h",
    "trend_direction": "improving",
}


def test_post_render_check_clean_html():
    assert post_render_check(CLEAN_HTML, EXPECTED_OK) == []


def test_post_render_check_catches_tampered_value():
    tampered = CLEAN_HTML.replace(">7.77<", ">9.99<")
    violations = post_render_check(tampered, EXPECTED_OK)
    assert len(violations) == 1
    assert "9.99" in violations[0]


def test_post_render_check_catches_tampered_unit():
    tampered = CLEAN_HTML.replace(">bags/h<", ">bags/min<")
    violations = post_render_check(tampered, EXPECTED_OK)
    assert len(violations) == 1
    assert "bags/min" in violations[0]


def test_post_render_check_catches_tampered_direction():
    tampered = CLEAN_HTML.replace(">improving<", ">worsening<")
    violations = post_render_check(tampered, EXPECTED_OK)
    assert len(violations) == 1
    assert "worsening" in violations[0]


def test_post_render_check_missing_elements():
    minimal = "<html><body><p>no test-ids here</p></body></html>"
    violations = post_render_check(minimal, EXPECTED_OK)
    # Should flag all 3 missing markers
    assert len(violations) == 3
