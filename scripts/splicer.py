"""HTML splicer (step ⑥ of the weekly run, Decisions 7, 21, 23).

Reads narrative_blocks.json + the pipeline's output CSVs and renders a
customer-facing HTML dashboard. After rendering, runs the post-render check
(Decision 23) — extracts the headline KPI value + trend direction from the
HTML and verifies they match the pipeline's computed values.

The dashboard is self-contained: Plotly is loaded from CDN; CSS is inlined;
no external dependencies once the page is open.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Decision 21 / 22 — trend direction thresholds.
FLAT_THRESHOLD = 0.10  # bags/h/wk

# Decision 21 — KPI widget colours derived from the slope sign.
COLOR_IMPROVING = "#1a9850"
COLOR_WORSENING = "#d73027"
COLOR_FLAT      = "#6b7280"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _load_narrative(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Computations
# ---------------------------------------------------------------------------

def ols_slope(xs: list[float], ys: list[float]) -> float:
    """OLS slope of y on x. Returns 0.0 for fewer than 2 points or constant x."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den > 0 else 0.0


def classify_trend(slope: float) -> str:
    """Decision 22 — improving / worsening / flat from the slope sign."""
    if slope < -FLAT_THRESHOLD:
        return "improving"
    if slope > FLAT_THRESHOLD:
        return "worsening"
    return "flat"


def trend_color(direction: str) -> str:
    return {
        "improving": COLOR_IMPROVING,
        "worsening": COLOR_WORSENING,
        "flat":      COLOR_FLAT,
    }[direction]


def _week_end(week_start_iso: str) -> date:
    return date.fromisoformat(week_start_iso) + timedelta(days=6)


def latest_complete_week(weekly_rows: list[dict], today: date) -> dict | None:
    """Decision 14 — pick the row whose week_end ≤ today, latest first."""
    complete = [r for r in weekly_rows if _week_end(r["week_start"]) <= today]
    if not complete:
        return None
    return max(complete, key=lambda r: r["week_start"])


# ---------------------------------------------------------------------------
# Chart generators — each returns an HTML string to embed in the template
# ---------------------------------------------------------------------------

def chart_weekly_trend_html(weekly_rows: list[dict], slope: float) -> str:
    rows = sorted(weekly_rows, key=lambda r: r["week_start"])
    weeks = [r["week_start"] for r in rows]
    rates = [float(r["loss_rate_bags_per_hour"]) for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=weeks, y=rates,
        name="Loss rate", marker_color="#94a3b8",
    ))
    if len(rates) >= 2:
        xs = list(range(len(rates)))
        ys_mean = sum(rates) / len(rates)
        xs_mean = sum(xs) / len(xs)
        intercept = ys_mean - slope * xs_mean
        ys_line = [slope * x + intercept for x in xs]
        fig.add_trace(go.Scatter(
            x=weeks, y=ys_line, mode="lines",
            name="OLS trend",
            line=dict(color="#374151", dash="dash", width=2),
        ))
    fig.update_layout(
        title="Weekly loss rate (bags/h)",
        yaxis_title="bags/h",
        xaxis_title="Week starting",
        height=400,
        margin=dict(l=60, r=20, t=50, b=60),
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn",
                       div_id="chart-weekly-trend")


def chart_by_shift_html(shift_rows: list[dict]) -> str:
    shift_order = ["1st", "2nd", "3rd"]
    by_shift = {r["shift_name"]: float(r["loss_rate_bags_per_hour"]) for r in shift_rows}
    xs = [s for s in shift_order if s in by_shift]
    ys = [by_shift[s] for s in xs]
    fig = go.Figure([go.Bar(x=xs, y=ys, marker_color="#94a3b8")])
    fig.update_layout(
        title="Loss rate by shift (all weeks)",
        yaxis_title="bags/h",
        height=350,
        margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       div_id="chart-by-shift")


def chart_by_weekday_html(weekday_rows: list[dict]) -> str:
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]
    by_day = {r["weekday"]: float(r["loss_rate_bags_per_hour"]) for r in weekday_rows}
    xs = [d for d in day_order if d in by_day]
    ys = [by_day[d] for d in xs]
    fig = go.Figure([go.Bar(x=xs, y=ys, marker_color="#94a3b8")])
    fig.update_layout(
        title="Loss rate by weekday (all weeks)",
        yaxis_title="bags/h",
        height=350,
        margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       div_id="chart-by-weekday")


def chart_per_shift_trend_html(week_shift_rows: list[dict]) -> str:
    by_shift: dict[str, list[tuple[str, float]]] = {"1st": [], "2nd": [], "3rd": []}
    for r in week_shift_rows:
        by_shift[r["shift_name"]].append(
            (r["week_start"], float(r["loss_rate_bags_per_hour"]))
        )
    fig = go.Figure()
    palette = {"1st": "#2563eb", "2nd": "#f59e0b", "3rd": "#7c3aed"}
    for shift, entries in by_shift.items():
        entries.sort()
        weeks_list = [e[0] for e in entries]
        rates_list = [e[1] for e in entries]
        fig.add_trace(go.Scatter(
            x=weeks_list, y=rates_list, mode="lines+markers",
            name=f"{shift} shift", line=dict(color=palette[shift], width=2),
        ))
    fig.update_layout(
        title="Per-shift loss rate over weeks (bags/h)",
        yaxis_title="bags/h",
        xaxis_title="Week starting",
        height=400,
        margin=dict(l=60, r=20, t=50, b=60),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       div_id="chart-per-shift-trend")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_dashboard(
    narrative: dict,
    output_dir: Path,
    today: date,
    report_meta: dict,
) -> tuple[str, dict]:
    """Render the dashboard HTML. Returns (html, expected_for_postcheck).

    expected_for_postcheck = the values the post-render check should find
    after parsing the rendered HTML.
    """
    weekly_rows        = _load_csv(output_dir / "weekly_production_opportunity.csv")
    by_shift_week_rows = _load_csv(output_dir / "weekly_opportunity_by_shift.csv")
    shift_rows         = _load_csv(output_dir / "shift_summary.csv")
    weekday_rows       = _load_csv(output_dir / "weekday_summary.csv")

    # OLS slope over the per-week rate (Decision 6).
    rates_in_order = [
        float(r["loss_rate_bags_per_hour"])
        for r in sorted(weekly_rows, key=lambda r: r["week_start"])
    ]
    slope = ols_slope(list(range(len(rates_in_order))), rates_in_order)
    direction = classify_trend(slope)

    latest = latest_complete_week(weekly_rows, today)
    if latest is None:
        raise RuntimeError(
            f"No complete week found in weekly_production_opportunity.csv on or before {today.isoformat()}. "
            f"Pass --as-of <YYYY-MM-DD> with a date after a week_end in the data."
        )

    headline_value = float(latest["loss_rate_bags_per_hour"])
    headline_unit  = "bags/h"
    headline_color = trend_color(direction)
    week_end_str   = _week_end(latest["week_start"]).isoformat()

    # Reviewer flags from verifier_report.json (if present).
    reviewer_flags: list[dict] = []
    verifier_report_path = output_dir / "verifier_report.json"
    if verifier_report_path.exists():
        try:
            verifier_report = json.loads(verifier_report_path.read_text())
            reviewer_flags = verifier_report.get("warnings", [])
        except json.JSONDecodeError:
            pass  # silently ignore — the verifier might still be writing

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("dashboard.html.j2")
    html = template.render(
        report_display_name = report_meta["report_display_name"],
        customer            = report_meta["customer"],
        report_id           = report_meta["report_id"],
        week_end            = week_end_str,
        generated_at        = datetime.now().isoformat(timespec="seconds"),

        main_conclusions = narrative["main_conclusions"],
        top_3_actions    = narrative["top_3_actions"],
        drivers_insight  = narrative["drivers_insight"],
        patterns         = narrative["patterns"],

        headline_value   = headline_value,
        headline_unit    = headline_unit,
        headline_color   = headline_color,
        trend_direction  = direction,
        trend_slope      = round(slope, 2),
        trend_slope_unit = "bags/h/wk",

        chart_weekly_trend    = chart_weekly_trend_html(weekly_rows, slope),
        chart_by_shift        = chart_by_shift_html(shift_rows),
        chart_by_weekday      = chart_by_weekday_html(weekday_rows),
        chart_per_shift_trend = chart_per_shift_trend_html(by_shift_week_rows),

        reviewer_flags = reviewer_flags,
    )

    expected = {
        "headline_value": headline_value,
        "headline_unit":  headline_unit,
        "trend_direction": direction,
    }
    return html, expected


# ---------------------------------------------------------------------------
# Post-render check (Decision 23)
# ---------------------------------------------------------------------------

def post_render_check(html: str, expected: dict) -> list[str]:
    """Verify the rendered HTML's headline matches pipeline-computed values.

    Decision 23 — narrow runtime safety net on the two highest-visibility
    values (headline KPI + trend direction). Broader correctness is covered
    by dev-time tests.
    """
    violations: list[str] = []

    m_value = re.search(
        r'data-test-id="headline-rate-value"[^>]*>\s*([\d.]+)\s*</', html,
    )
    if not m_value:
        violations.append("post-render: headline-rate-value not found in rendered HTML")
    else:
        rendered = float(m_value.group(1))
        if abs(rendered - expected["headline_value"]) > 0.01:
            violations.append(
                f"post-render: headline rate mismatch — rendered {rendered}, "
                f"pipeline computed {expected['headline_value']}"
            )

    m_unit = re.search(
        r'data-test-id="headline-rate-unit"[^>]*>\s*([^<\s]+)\s*</', html,
    )
    if not m_unit:
        violations.append("post-render: headline-rate-unit not found in rendered HTML")
    else:
        rendered_unit = m_unit.group(1)
        if rendered_unit != expected["headline_unit"]:
            violations.append(
                f"post-render: headline unit mismatch — rendered {rendered_unit!r}, "
                f"pipeline expects {expected['headline_unit']!r}"
            )

    m_dir = re.search(
        r'data-test-id="headline-trend-direction"[^>]*>\s*([a-z]+)\s*</', html,
    )
    if not m_dir:
        violations.append("post-render: headline-trend-direction not found in rendered HTML")
    else:
        rendered_dir = m_dir.group(1)
        if rendered_dir != expected["trend_direction"]:
            violations.append(
                f"post-render: trend direction mismatch — rendered {rendered_dir!r}, "
                f"pipeline computed {expected['trend_direction']!r}"
            )

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="HTML splicer (step ⑥ of the weekly run, Decision 23).",
    )
    parser.add_argument(
        "--narrative", default="fixtures/narrative_blocks.json",
        help="Path to the narrative_blocks JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir", default="outputs/",
        help="Directory containing the pipeline CSVs; also where the HTML is written (default: %(default)s)",
    )
    parser.add_argument(
        "--report-id", default="demo",
        help="Report ID — appears in the dashboard footer and Fleet View (default: %(default)s)",
    )
    parser.add_argument(
        "--report-display-name", default="Demo line — synthetic data",
        help="Human-readable card title (default: %(default)s)",
    )
    parser.add_argument(
        "--customer", default="Synthetic customer",
        help="Customer label shown in the dashboard header (default: %(default)s)",
    )
    parser.add_argument(
        "--as-of", default=None,
        help="ISO date to treat as 'today' for latest-complete-week selection. "
             "Defaults to today.",
    )
    args = parser.parse_args()

    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    narrative = _load_narrative(Path(args.narrative))
    output_dir = Path(args.output_dir)

    html, expected = render_dashboard(
        narrative, output_dir, today,
        report_meta={
            "report_id":           args.report_id,
            "report_display_name": args.report_display_name,
            "customer":            args.customer,
        },
    )

    html_path = output_dir / "weekly_kpi_dashboard.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html)
    print(f"Wrote dashboard to {html_path}")

    violations = post_render_check(html, expected)
    if violations:
        print(f"POST-RENDER CHECK FAILED — {len(violations)} violation(s):")
        for v in violations:
            print(f"  ✗ {v}")
        sys.exit(1)
    print(
        f"POST-RENDER CHECK PASSED — "
        f"headline {expected['headline_value']} {expected['headline_unit']}, "
        f"trend {expected['trend_direction']}."
    )


if __name__ == "__main__":
    main()
