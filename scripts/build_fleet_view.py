"""Fleet View builder (step ⑦ of the weekly run, Decision 11).

Scans every status/<report_id>.json and renders a single fleet_view.html
with one clickable card per report, grouped by customer. Adding a new
report = drop a new status JSON; the index regenerates from whatever
status files are present.

Status chip colours (Decisions 17 + 18):
- pass → green (clean ship)
- warn → amber (published with override(s))
- fail → red   (halted, dashboard NOT generated)

Cards in the `fail` state surface each un-overridden hard warning inline
with the override command (RUNBOOK scenarios 1 and 2).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"

STATUS_COLORS = {
    "pass": "#1a9850",
    "warn": "#f59e0b",
    "fail": "#d73027",
}
STATUS_EMOJI = {
    "pass": "🟢",
    "warn": "🟡",
    "fail": "🔴",
}


def _load_status_files(status_dir: Path) -> list[dict]:
    files = sorted(status_dir.glob("*.json")) if status_dir.exists() else []
    statuses: list[dict] = []
    for f in files:
        try:
            statuses.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            print(f"warning: skipping malformed JSON: {f}")
    return statuses


def _shape_card(s: dict) -> dict:
    """Reshape a raw status dict into the fields the template uses."""
    status = s.get("status", "unknown")
    return {
        "report_id":           s.get("report_id", "unknown"),
        "report_display_name": s.get("report_display_name", s.get("report_id", "?")),
        "status":              status,
        "status_color":        STATUS_COLORS.get(status, "#888"),
        "status_emoji":        STATUS_EMOJI.get(status, "❓"),
        "summary":             s.get("summary", {}),
        "warnings":            s.get("warnings", []),
        "week_end":            s.get("week_end", "—"),
        "updated_at":          s.get("updated_at", "—"),
        "dashboard_path":      s.get("dashboard_path", "#"),
    }


def build(status_dir: Path, output_path: Path) -> int:
    """Render fleet_view.html. Returns the count of cards rendered."""
    cards_by_customer: dict[str, list[dict]] = {}
    for s in _load_status_files(status_dir):
        cards_by_customer.setdefault(
            s.get("customer", "Unknown"), [],
        ).append(_shape_card(s))

    # Sort cards within each customer by week_end DESC (most recent first)
    for cards in cards_by_customer.values():
        cards.sort(key=lambda c: c["week_end"], reverse=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("fleet_view.html.j2")
    total = sum(len(v) for v in cards_by_customer.values())
    html = template.render(
        cards_by_customer=cards_by_customer,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        total_reports=total,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fleet View builder (step ⑦ of the weekly run, Decision 11).",
    )
    parser.add_argument(
        "--status-dir", default="status/",
        help="Directory of per-report status JSONs to index (default: %(default)s)",
    )
    parser.add_argument(
        "--output", default="fleet_view.html",
        help="Output path for the fleet_view.html (default: %(default)s)",
    )
    args = parser.parse_args()

    n = build(Path(args.status_dir), Path(args.output))
    print(f"Wrote fleet view with {n} card(s) to {args.output}")


if __name__ == "__main__":
    main()
