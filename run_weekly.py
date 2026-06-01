"""Weekly orchestrator — the entrypoint the Monday cron triggers.

Chains all seven pipeline steps in a single Python process per Decision 24:

  ① / ②  KPI pipeline                       (deterministic)
  ③       Data-quality self-audit           (deterministic; halts on broken data, Decision 20)
  ④       Insights agent                    (LLM — Sonnet 4.6, Decision 26)
  ⑤       Verifier agent                    (LLM — Haiku 4.5, Decision 26)
  ⑤b      Auto-fix loop for fixable warnings (max 2 retries, Decision 18)
  ⑥       HTML splicer + post-render check + status JSON (Decisions 22, 23, 24)
  ⑦       Fleet View builder                (Decision 11)

CLI:
  --as-of <YYYY-MM-DD>                     treat that date as "today" (RUNBOOK 6)
  --override <warning_id> <justification>  bypass a hard warning (Decision 17)
  --mock                                   use fixture files for both agents
                                           (no Claude login needed)

Failure handling (Decision 24): any halting step writes a `status: fail`
JSON with a synthetic warning so the Fleet View card surfaces the cause
uniformly. The script exits 1 so the cron / CI knows something is wrong.

The orchestrator imports each step's top-level function directly (no
subprocess). Each step is also runnable on its own via
`python -m scripts.<step>` for debugging.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

import jsonschema

from scripts import (
    build_fleet_view,
    data_audit,
    insights_agent,
    kpi_pipeline,
    splicer,
    verifier,
)

REPO_ROOT = Path(__file__).resolve().parent

# Default paths — overridable via CLI
DEFAULT_INPUT_PATH  = REPO_ROOT / "fixtures" / "synthetic_cycles.csv"
DEFAULT_OUTPUT_DIR  = REPO_ROOT / "outputs"
DEFAULT_STATUS_DIR  = REPO_ROOT / "status"
DEFAULT_FLEET_VIEW  = REPO_ROOT / "fleet_view.html"
DEFAULT_METHODOLOGY = REPO_ROOT / "methodology" / "context.md"
DEFAULT_CONFIG      = REPO_ROOT / "config" / "demo.example.yaml"
NARRATIVE_SCHEMA    = REPO_ROOT / "architecture" / "schemas" / "narrative_blocks.schema.json"
VERIFIER_SCHEMA     = REPO_ROOT / "architecture" / "schemas" / "verifier_report.schema.json"

MOCK_NARRATIVE = REPO_ROOT / "fixtures" / "narrative_blocks.json"
MOCK_VERIFIER_REPORT = REPO_ROOT / "fixtures" / "verifier_report.json"

MAX_FIX_ATTEMPTS = 2  # Decision 18 — auto-fix loop retry cap


def _display_path(p: Path) -> str:
    """Show p relative to the repo when possible (for log readability),
    otherwise absolute. The orchestrator may be invoked with paths outside
    the repo root (e.g., from integration tests pointing at a tmp dir)."""
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validated_copy(src: Path, dst: Path, schema_path: Path) -> None:
    """Copy src → dst after validating src against the JSON schema."""
    schema = json.loads(schema_path.read_text())
    obj    = json.loads(src.read_text())
    jsonschema.validate(obj, schema)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)


def _apply_override(
    report_path: Path, warning_id: str, justification: str,
) -> bool:
    """Mark the named warning's override block. Returns False if not found."""
    report = json.loads(report_path.read_text())
    found = False
    for w in report.get("warnings", []):
        if w.get("id") == warning_id:
            w["override"] = {
                "overridden":    True,
                "justification": justification,
                "overridden_at": datetime.now().isoformat(timespec="seconds"),
            }
            found = True
            break
    if not found:
        return False
    # Re-derive overall status: warn = at least one overridden hard, no un-overridden
    has_unoverridden_hard = any(
        w["severity"] == "hard"
        and not (w.get("override") or {}).get("overridden")
        for w in report.get("warnings", [])
    )
    report["status"] = "warn" if not has_unoverridden_hard else "fail"
    report_path.write_text(json.dumps(report, indent=2))
    return True


def _escalate_unresolved_fixables(report_path: Path) -> int:
    """Mark any leftover fixable warnings as hard (Decision 18). Returns count escalated."""
    report = json.loads(report_path.read_text())
    leftover = [w for w in report.get("warnings", []) if w.get("severity") == "fixable"]
    if not leftover:
        return 0
    for w in leftover:
        w["severity"] = "hard"
    report["summary"]["hard_count"]   = sum(1 for w in report["warnings"] if w["severity"] == "hard")
    report["summary"]["fixable_count"] = 0
    report_path.write_text(json.dumps(report, indent=2))
    return len(leftover)


def _write_failure_status(
    status_dir: Path, report_id: str, customer: str,
    report_display_name: str, week_end: str, cause: str,
) -> Path:
    """Per Decision 24 — write a status: fail JSON when any step halts."""
    status_dir.mkdir(parents=True, exist_ok=True)
    warning = {
        "id":         "w1",
        "severity":   "hard",
        "category":   "other",
        "claim":      "(pipeline halted before this could be produced)",
        "location":   "pipeline",
        "issue":      cause,
        "evidence":   "see stderr for the full traceback",
        "suggestion": None,
        "rule_id":    None,
        "override":   {"overridden": False, "justification": "", "overridden_at": None},
    }
    status = {
        "schema_version":      "1.0",
        "report_id":           report_id,
        "customer":            customer,
        "report_display_name": report_display_name,
        "week_end":            week_end,
        "updated_at":          datetime.now().isoformat(timespec="seconds"),
        "status":              "fail",
        "summary":             {"hard_count": 1, "fixable_count": 0},
        "warnings":            [warning],
    }
    path = status_dir / f"{report_id}.json"
    path.write_text(json.dumps(status, indent=2))
    return path


# ---------------------------------------------------------------------------
# Each pipeline step
# ---------------------------------------------------------------------------

def _step_pipeline(args: argparse.Namespace) -> None:
    print("① / ②  KPI pipeline...")
    kpi_pipeline.compute_kpis(args.input_path, args.output_dir)


def _step_audit(args: argparse.Namespace) -> None:
    print("③  data audit...")
    violations = data_audit.run_audit(args.output_dir)
    if violations:
        raise RuntimeError(
            f"Data audit halted with {len(violations)} violation(s). "
            f"First: {violations[0]}"
        )


def _step_insights(args: argparse.Namespace, narrative_path: Path) -> None:
    print("④  insights agent...")
    if args.mock:
        _validated_copy(MOCK_NARRATIVE, narrative_path, NARRATIVE_SCHEMA)
        print(f"     (mock — used {_display_path(MOCK_NARRATIVE)})")
        return
    insights_agent.generate(
        methodology_path  = DEFAULT_METHODOLOGY,
        schema_path       = NARRATIVE_SCHEMA,
        config_path       = args.config,
        prior_status_path = args.status_dir / f"{args.report_id}.json",
        output_dir        = args.output_dir,
        narrative_out     = narrative_path,
    )


def _step_verifier(args: argparse.Namespace,
                    narrative_path: Path, report_path: Path) -> None:
    print("⑤  verifier agent...")
    if args.mock:
        _validated_copy(MOCK_VERIFIER_REPORT, report_path, VERIFIER_SCHEMA)
        print(f"     (mock — used {_display_path(MOCK_VERIFIER_REPORT)})")
        return
    verifier.verify(
        narrative_path   = narrative_path,
        methodology_path = DEFAULT_METHODOLOGY,
        schema_path      = VERIFIER_SCHEMA,
        output_dir       = args.output_dir,
        report_id        = args.report_id,
        report_out       = report_path,
    )


def _step_autofix_loop(args: argparse.Namespace,
                       narrative_path: Path, report_path: Path) -> None:
    """Decision 18 — re-emit + re-verify up to MAX_FIX_ATTEMPTS for fixables."""
    if args.mock:
        return  # mock report is always clean by construction

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        report = json.loads(report_path.read_text())
        fixables = [w for w in report.get("warnings", []) if w.get("severity") == "fixable"]
        if not fixables:
            return
        print(f"⑤b  auto-fix loop attempt {attempt}/{MAX_FIX_ATTEMPTS} — {len(fixables)} fixable")
        # Simplification for the POC: regenerate the whole narrative rather than
        # a single block. The orchestrator-side targeted re-emit can be added
        # without changing the verifier/splicer contracts.
        insights_agent.generate(
            methodology_path  = DEFAULT_METHODOLOGY,
            schema_path       = NARRATIVE_SCHEMA,
            config_path       = args.config,
            prior_status_path = args.status_dir / f"{args.report_id}.json",
            output_dir        = args.output_dir,
            narrative_out     = narrative_path,
        )
        verifier.verify(
            narrative_path   = narrative_path,
            methodology_path = DEFAULT_METHODOLOGY,
            schema_path      = VERIFIER_SCHEMA,
            output_dir       = args.output_dir,
            report_id        = args.report_id,
            report_out       = report_path,
        )

    # Retry cap reached — escalate any still-fixable warnings to hard
    escalated = _escalate_unresolved_fixables(report_path)
    if escalated:
        print(f"     auto-fix loop exhausted — {escalated} warning(s) escalated to hard")


def _step_override(args: argparse.Namespace, report_path: Path) -> None:
    """Apply --override if provided (Decision 17)."""
    if not args.override_id:
        return
    print(f"   applying override on {args.override_id}...")
    applied = _apply_override(report_path, args.override_id, args.override_justification)
    if not applied:
        raise RuntimeError(
            f"--override {args.override_id} did not match any warning id in {report_path}"
        )


def _step_halt_on_hard(report_path: Path) -> None:
    """Decision 24 — pipeline halts if any un-overridden hard warning remains."""
    report = json.loads(report_path.read_text())
    leftover = [
        w for w in report.get("warnings", [])
        if w["severity"] == "hard"
        and not (w.get("override") or {}).get("overridden")
    ]
    if leftover:
        raise RuntimeError(
            f"Pipeline halted with {len(leftover)} un-overridden hard warning(s). "
            f"First: {leftover[0].get('id')} — {leftover[0].get('issue')}. "
            f"Use --override <warning_id> \"<justification>\" to publish anyway."
        )


def _step_splicer(args: argparse.Namespace,
                   narrative_path: Path, today: date) -> Path:
    print("⑥  HTML splicer + post-render check...")
    narrative = json.loads(narrative_path.read_text())
    report_meta = {
        "report_id":           args.report_id,
        "report_display_name": args.report_display_name,
        "customer":            args.customer,
    }
    html, expected = splicer.render_dashboard(narrative, args.output_dir, today, report_meta)
    html_path = args.output_dir / "weekly_kpi_dashboard.html"
    html_path.write_text(html)

    pr_violations = splicer.post_render_check(html, expected)
    if pr_violations:
        raise RuntimeError(f"Post-render check failed: {pr_violations[0]}")

    status_path = splicer.write_status_json(
        status_dir       = args.status_dir,
        report_meta      = report_meta,
        week_end         = expected["week_end"],
        dashboard_path   = html_path,
        headline_value   = expected["headline_value"],
        headline_unit    = expected["headline_unit"],
        trend_direction  = expected["trend_direction"],
        trend_slope      = expected["slope"],
        trend_slope_unit = "bags/h/wk",
        narrative        = narrative,
        reviewer_flags   = expected["reviewer_flags"],
    )
    print(f"     wrote dashboard → {_display_path(html_path)}")
    print(f"     wrote status    → {_display_path(status_path)}")
    return html_path


def _step_fleet_view(args: argparse.Namespace) -> None:
    print("⑦  Fleet View builder...")
    n = build_fleet_view.build(args.status_dir, args.fleet_view_output)
    print(f"     wrote fleet view with {n} card(s) → {_display_path(args.fleet_view_output)}")


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    narrative_path = args.output_dir / "narrative_blocks.json"
    report_path    = args.output_dir / "verifier_report.json"

    _step_pipeline(args)
    _step_audit(args)
    _step_insights(args, narrative_path)
    _step_verifier(args, narrative_path, report_path)
    _step_autofix_loop(args, narrative_path, report_path)
    _step_override(args, report_path)
    _step_halt_on_hard(report_path)
    _step_splicer(args, narrative_path, today)
    _step_fleet_view(args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weekly orchestrator — runs the full pipeline end-to-end.",
    )
    parser.add_argument(
        "--input-path", type=Path, default=DEFAULT_INPUT_PATH,
        help="Path to cycle-level CSV input (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--status-dir", type=Path, default=DEFAULT_STATUS_DIR,
        help="Status directory (default: %(default)s)",
    )
    parser.add_argument(
        "--fleet-view-output", type=Path, default=DEFAULT_FLEET_VIEW,
        help="Where to write fleet_view.html (default: %(default)s)",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="Report config YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--report-id", default="demo",
        help="Report id (default: %(default)s)",
    )
    parser.add_argument(
        "--report-display-name", default="Demo line — synthetic data",
        help="Card title on the Fleet View (default: %(default)s)",
    )
    parser.add_argument(
        "--customer", default="Synthetic customer",
        help="Customer label (default: %(default)s)",
    )
    parser.add_argument(
        "--as-of", default=None,
        help="ISO date to treat as 'today' for latest-complete-week selection (RUNBOOK 6).",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use fixture files for both LLM agents — no Claude login required. "
             "The deterministic steps still run for real.",
    )
    parser.add_argument(
        "--override", dest="override_arg",
        nargs=2, metavar=("WARNING_ID", "JUSTIFICATION"),
        help="Bypass the named hard warning (Decision 17). "
             "Both arguments are required; justification must be non-empty.",
    )
    args = parser.parse_args()

    if args.override_arg:
        args.override_id, args.override_justification = args.override_arg
        if not args.override_justification.strip():
            print("ERROR: --override requires a non-empty justification.", file=sys.stderr)
            sys.exit(1)
    else:
        args.override_id = None
        args.override_justification = None

    try:
        run(args)
        print("\n✓ weekly run complete")
    except Exception as exc:
        print(f"\n✗ weekly run failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        _write_failure_status(
            status_dir          = args.status_dir,
            report_id           = args.report_id,
            customer            = args.customer,
            report_display_name = args.report_display_name,
            week_end            = args.as_of or date.today().isoformat(),
            cause               = str(exc),
        )
        # Rebuild the fleet view so the failure is visible
        try:
            build_fleet_view.build(args.status_dir, args.fleet_view_output)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
