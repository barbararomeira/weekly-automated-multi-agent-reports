"""Insights agent (step ④ of the weekly run).

Reads the methodology document, the report config, the pipeline's output
CSVs, and last week's status JSON (if any). Calls Claude Sonnet via the
`claude` CLI (Claude-subscription OAuth, no API key) and produces a
narrative_blocks.json that conforms to v1.0 of
architecture/schemas/narrative_blocks.schema.json.

The agent validates its own output against the schema before writing.
On a schema violation, it retries once with the error context in the
prompt; if still invalid, it propagates the validation error.

`--mock-narrative <path>` bypasses the model call entirely and just copies
the given JSON to the output (validated against the schema first). This is
how the repo demo runs without a Claude login — the agent code is real, the
model call is just skipped in mock mode.

Model: Sonnet via the `claude` CLI, with `--fallback-model haiku`. Each call
is bounded by a per-attempt timeout and retried once, so a no-response stall
fails fast instead of hanging.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import jsonschema
import yaml

INSIGHTS_MODEL = "sonnet"   # claude CLI model alias (Claude-subscription OAuth, no API key)
FALLBACK_MODEL = "haiku"    # passed to `claude --fallback-model` for overload/error
SUBPROCESS_TIMEOUT_S = 150  # per-attempt hard cap — fail fast instead of hanging the run
CALL_MAX_ATTEMPTS    = 2    # retry once on a no-response stall
CALL_RETRY_BACKOFF_S = 10


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------

def _read_or_empty(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    parsed = yaml.safe_load(path.read_text())
    return parsed or {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


PIPELINE_CSVS = [
    "weekly_production_opportunity.csv",
    "weekly_opportunity_by_shift.csv",
    "weekly_time_on_product.csv",
    "shift_summary.csv",
    "weekday_summary.csv",
    "data_exclusions.csv",
]


def _load_pipeline_csvs(output_dir: Path) -> dict[str, str]:
    return {fn: _read_or_empty(output_dir / fn) for fn in PIPELINE_CSVS}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _system_prompt(methodology: str, schema: dict) -> str:
    rules = (
        "Rules:\n\n"

        "1. STRUCTURE\n"
        "- Lead with what is improving; end with the single highest-leverage opportunity.\n"
        "- Refer to the customer and line by the names in the config; do not invent.\n\n"

        "2. TREND FRAMING — SLOPE ONLY (the rule the agent breaks most often)\n"
        "Use OLS slope across the per-week rate. Do NOT compare arbitrary period\n"
        "aggregates as the primary trend statement.\n"
        "  FORBIDDEN phrasings:\n"
        "    ✗ 'up from W19', 'down from W19'\n"
        "    ✗ 'compared to last week', 'compared to the prior week'\n"
        "    ✗ 'rose from 18 to 84', 'jumped from X to Y'\n"
        "    ✗ 'ticked up', 'ticked down', 'bounced from'\n"
        "    ✗ 'Δ vs prior week', 'change from W19 to W20'\n"
        "  REQUIRED phrasings:\n"
        "    ✓ 'the OLS slope over the period is +X bags/h/wk'\n"
        "    ✓ 'trending downward at -X bags/h/wk'\n"
        "    ✓ 'the latest complete week sits at X bags/h; the OLS slope is Y bags/h/wk'\n\n"

        "3. NO JUDGEMENT WORDS — describe metrics neutrally\n"
        "  FORBIDDEN:  ✗ cleanest, slowest, best, worst, strongest, weakest,\n"
        "              disappointing, poor, great, excellent, healthy, problematic\n"
        "  REQUIRED:   ✓ 'has the lowest loss rate', 'has the highest loss rate',\n"
        "              ✓ 'lower than', 'higher than', 'at X bags/h vs Y bags/h'\n\n"

        "4. NO EVALUATIVE FRAMING — stay descriptive\n"
        "  FORBIDDEN:  ✗ 'well inside the SOP', 'at the ceiling', 'comfortably below',\n"
        "              ✗ 'concerning', 'reassuring', 'on track'\n"
        "  REQUIRED:   ✓ 'below the SOP', 'near the SOP allowance',\n"
        "              ✓ 'at X seconds against the Y-second SOP'\n\n"

        "5. NUMBER FORMATTING\n"
        "- Every number includes its unit. Always. (bags/h, pct, seconds, bags, hours)\n"
        "- No comma separators in numerals. Write '8000' not '8,000'.\n\n"

        "6. DATA ADEQUACY\n"
        "- Calibrate claim strength. Read productive_hours per week, total weeks\n"
        "  available, and which shifts contributed each week. If data is thin, describe\n"
        "  what's there without anchoring strong claims on noisy signals.\n\n"

        "7. CROSS-WEEK CONTINUITY\n"
        "- Process this week's data BEFORE anchoring on last week's. Form your own\n"
        "  conclusions from the new CSVs first; then check whether last week's\n"
        "  observations are still valid given the new data. Reference prior\n"
        "  observations only where the new data still supports them. Do NOT treat\n"
        "  last week's blocks as a starting template.\n\n"

        "8. MANDATORY SELF-CHECK BEFORE EMITTING\n"
        "For EVERY numeric claim in your response, before you emit:\n"
        "  a. Locate the exact CSV row + column the value comes from.\n"
        "  b. Verify the value matches (no rounding errors, no fabricated values).\n"
        "  c. Cross-check directional words ('up', 'down', 'rose', 'fell', 'increased',\n"
        "     'decreased') against the actual values — does the data actually move in\n"
        "     that direction?\n"
        "  d. If a claim cannot be traced to a specific CSV cell, REMOVE it or rephrase\n"
        "     as a qualitative statement.\n"
        "  e. Cross-check counts ('14 weeks', '18 weeks') against the actual row count\n"
        "     in the CSV — count carefully before stating.\n"
    )
    return (
        "You are a data-driven narrator for a weekly manufacturing report. Your job is "
        "to turn the week's data into a short, clear, balanced narrative that a production "
        "operator could read. Follow the methodology document exactly. Write only the "
        "fields requested by the schema. Numbers always come from the data — never invent.\n\n"
        f"{rules}\n"
        "METHODOLOGY DOCUMENT\n"
        f"{methodology}\n\n"
        "OUTPUT SCHEMA  (your response MUST be a single JSON object conforming to this schema; "
        "no prose, no markdown fences, no commentary outside the JSON):\n"
        f"{json.dumps(schema, indent=2)}\n"
    )


def _user_prompt(config: dict, prior_status: dict, csv_texts: dict[str, str]) -> str:
    parts: list[str] = []
    parts.append("REPORT CONFIG")
    parts.append(yaml.safe_dump(config, sort_keys=False) if config else "(no config provided)")
    parts.append("")
    parts.append("LAST WEEK'S STATUS  (cross-week continuity — reference layer, NOT a template)")
    if prior_status:
        parts.append(json.dumps(prior_status, indent=2))
    else:
        parts.append("(no prior status — this is the first run, or last run failed before producing one)")
    parts.append("")
    parts.append("THIS WEEK'S DATA  (full pipeline output)")
    for fn, text in csv_texts.items():
        parts.append(f"--- {fn} ---")
        parts.append(text if text else "(missing)")
        parts.append("")
    parts.append("FINAL REMINDERS before you emit:")
    parts.append("- Every numeric claim must trace to a specific CSV cell. Cross-check before emitting.")
    parts.append("- Every directional word ('up', 'down', 'rose', 'fell') must match the actual data values.")
    parts.append("- Trend statements use OLS slope only. No 'up from W19' / 'rose from X to Y' phrasings.")
    parts.append("- No judgement words: ✗ cleanest, slowest, best, worst, strongest, weakest.")
    parts.append("- No evaluative framing: ✗ 'well inside', 'at the ceiling', 'comfortably below'.")
    parts.append("")
    parts.append("Produce the narrative as a single JSON object conforming to the schema. "
                 "Do not include any prose, commentary, or markdown fences outside the JSON.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude call + JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Pull the first valid JSON object out of the response.

    Tolerates:
      - ```json fences around the JSON
      - prose before / after the JSON
      - trailing extra characters or stray braces (uses raw_decode to find
        the end of the first valid object, ignoring everything after)
    """
    text = text.strip()
    if text.startswith("```"):
        # strip leading fence + optional language hint
        text = text.split("\n", 1)[1] if "\n" in text else text
        # strip trailing fence
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    text = text.strip()
    first = text.find("{")
    if first < 0:
        raise json.JSONDecodeError("no '{' in response", text, 0)
    obj, _ = json.JSONDecoder().raw_decode(text[first:])
    return obj


def _call_claude(system: str, user: str, schema_error: str = "") -> str:
    """One Claude call via the `claude` CLI as a subprocess — OAuth from the
    operator's Claude subscription, no API key. Bounded by a per-attempt
    timeout and retried once, so a no-response stall fails fast instead of
    hanging the run. (--fallback-model does NOT rescue a *silent* hang — there
    is no error response to trigger the fallback — hence the explicit timeout.)
    """
    if schema_error:
        # Schema-violation retry: re-ask with the validator's error in context
        user = (
            user
            + "\n\n---\n"
            + "Your previous response did not validate against the schema.\n"
            + f"Validator error: {schema_error}\n"
            + "Return a corrected JSON object conforming to the schema, with the "
            + "same content adjusted to fix the violation. Do not include any text "
            + "outside the JSON."
        )
    cmd = [
        "claude", "-p", user,
        "--system-prompt", system,
        "--model", INSIGHTS_MODEL,
        "--fallback-model", FALLBACK_MODEL,
    ]
    last_err: Exception | None = None
    for attempt in range(1, CALL_MAX_ATTEMPTS + 1):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                timeout=SUBPROCESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            last_err = e
            print(f"  insights: attempt {attempt}/{CALL_MAX_ATTEMPTS} got no response "
                  f"within {SUBPROCESS_TIMEOUT_S}s", flush=True)
        else:
            if r.returncode == 0:
                return r.stdout
            last_err = RuntimeError(f"claude CLI exit {r.returncode}: {r.stderr[:500]}")
            print(f"  insights: attempt {attempt}/{CALL_MAX_ATTEMPTS} failed "
                  f"(exit {r.returncode})", flush=True)
        if attempt < CALL_MAX_ATTEMPTS:
            time.sleep(CALL_RETRY_BACKOFF_S)
    raise RuntimeError(
        f"claude CLI returned nothing after {CALL_MAX_ATTEMPTS} attempts "
        f"({SUBPROCESS_TIMEOUT_S}s each, model={INSIGHTS_MODEL}/{FALLBACK_MODEL}). "
        f"Ensure the `claude` CLI is signed in and no other heavy Claude session is "
        f"running, then retry (or use --mock)."
    ) from last_err


# ---------------------------------------------------------------------------
# Top-level generate
# ---------------------------------------------------------------------------

def generate(
    *,
    methodology_path: Path,
    schema_path: Path,
    config_path: Path,
    prior_status_path: Path,
    output_dir: Path,
    narrative_out: Path,
) -> dict[str, Any]:
    # Preflight: real-mode needs the `claude` CLI on PATH (signed in to a Claude
    # subscription). Fires on every call path (CLI and orchestrator alike), so
    # callers see a clean message instead of a FileNotFoundError two steps in.
    if shutil.which("claude") is None:
        print(
            "ERROR: the `claude` CLI is not on PATH.\n"
            "Install Claude Code and sign in to your Claude subscription, or pass "
            "--mock to run_weekly.py (or --mock-narrative to insights_agent directly).",
            file=sys.stderr,
        )
        sys.exit(1)

    methodology = _read_or_empty(methodology_path)
    schema      = _load_json(schema_path)
    config      = _load_yaml(config_path)
    prior       = _load_json(prior_status_path)
    csv_texts   = _load_pipeline_csvs(output_dir)

    system = _system_prompt(methodology, schema)
    user   = _user_prompt(config, prior, csv_texts)

    # First call
    raw = _call_claude(system, user)
    try:
        narrative = _extract_json(raw)
        jsonschema.validate(narrative, schema)
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        print(f"  insights: first response failed validation ({e}); retrying with error context")
        raw = _call_claude(system, user, schema_error=str(e))
        narrative = _extract_json(raw)
        jsonschema.validate(narrative, schema)  # if this still fails, propagate

    narrative_out.parent.mkdir(parents=True, exist_ok=True)
    narrative_out.write_text(json.dumps(narrative, indent=2))
    return narrative


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Insights agent (step ④ of the weekly run, Decisions 7-8, 22, 26).",
    )
    parser.add_argument(
        "--methodology", default="methodology/context.md",
        help="Path to the methodology document (default: %(default)s)",
    )
    parser.add_argument(
        "--schema", default="architecture/schemas/narrative_blocks.schema.json",
        help="Path to the narrative_blocks JSON schema (default: %(default)s)",
    )
    parser.add_argument(
        "--config", default="config/demo.example.yaml",
        help="Report config YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--prior-status", default="status/demo.json",
        help="Path to last week's status JSON for cross-week continuity. "
             "Empty file or missing path is treated as a fresh start (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir", default="outputs/",
        help="Directory containing the pipeline CSVs (default: %(default)s)",
    )
    parser.add_argument(
        "--narrative-out", default="outputs/narrative_blocks.json",
        help="Where to write the resulting narrative (default: %(default)s)",
    )
    parser.add_argument(
        "--mock-narrative", default=None,
        help="If provided, copy this JSON to --narrative-out (after schema validation) "
             "and skip the model call entirely. Lets the repo demo run without a Claude "
             "login. The agent code is the same in both modes; only the model call is "
             "bypassed.",
    )
    args = parser.parse_args()

    narrative_out = Path(args.narrative_out)
    schema_path = Path(args.schema)

    # Mock mode: validate the supplied file and copy it through
    if args.mock_narrative:
        src = Path(args.mock_narrative)
        if not src.exists():
            print(f"ERROR: mock narrative not found: {src}", file=sys.stderr)
            sys.exit(1)
        narrative = _load_json(src)
        schema    = _load_json(schema_path)
        try:
            jsonschema.validate(narrative, schema)
        except jsonschema.ValidationError as e:
            print(f"ERROR: mock narrative does not conform to schema: {e}", file=sys.stderr)
            sys.exit(1)
        narrative_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, narrative_out)
        print(f"MOCK MODE: validated and copied {src} → {narrative_out}")
        return

    narrative = generate(
        methodology_path=Path(args.methodology),
        schema_path=schema_path,
        config_path=Path(args.config),
        prior_status_path=Path(args.prior_status),
        output_dir=Path(args.output_dir),
        narrative_out=narrative_out,
    )
    n_bullets = len(narrative.get("main_conclusions", {}).get("bullets", []))
    n_actions = len(narrative.get("top_3_actions", []))
    print(
        f"Wrote narrative to {narrative_out} "
        f"({n_bullets} bullets, {n_actions} actions, schema_version {narrative.get('schema_version')})"
    )


if __name__ == "__main__":
    main()
