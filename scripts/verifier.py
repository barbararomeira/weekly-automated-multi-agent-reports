"""Verifier agent (step ⑤ of the weekly run).

Reads the just-generated narrative_blocks.json plus the methodology and the
pipeline's CSVs. Calls Claude Haiku 4.5 (Decision 26) to check that:

  1. Every rule in the methodology document is respected — units mandatory,
     no comma separators, slope-only framing, no period-aggregates, no
     judgement words, etc.
  2. Every numeric claim in the narrative traces to a value in the CSVs
     (no fabrications).

Produces verifier_report.json conforming to v1.0 of
architecture/schemas/verifier_report.schema.json. Validates the response
against the schema and retries once with the validator's error in context
if the first response is malformed.

`--mock-verifier-report <path>` bypasses the API entirely (validates the
supplied JSON against the schema and copies it through). Lets the repo
demo run without an ANTHROPIC_API_KEY.

Severity model (Decision 18):
- HARD warnings — a claim or number that doesn't trace to the data
  (fabrication). Pipeline halts; cannot be fixed by re-rendering text.
- FIXABLE warnings — methodology compliance issues the agent can correct
  by rewriting the offending block. The orchestrator's auto-fix loop
  handles these.

There is no "soft warning" tier.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import jsonschema

VERIFIER_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096
SDK_MAX_RETRIES = 3  # Decision 24

PIPELINE_CSVS = [
    "weekly_production_opportunity.csv",
    "weekly_opportunity_by_shift.csv",
    "weekly_time_on_product.csv",
    "shift_summary.csv",
    "weekday_summary.csv",
    "data_exclusions.csv",
]


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------

def _read_or_empty(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _load_pipeline_csvs(output_dir: Path) -> dict[str, str]:
    return {fn: _read_or_empty(output_dir / fn) for fn in PIPELINE_CSVS}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _system_prompt(methodology: str, schema: dict) -> str:
    return (
        "You are a methodology compliance + data sync verifier for a weekly "
        "manufacturing report. You read a narrative and check it against "
        "(a) the methodology document below and (b) the underlying CSVs. "
        "You emit a structured warnings report. You DO NOT rewrite the narrative.\n\n"

        "Two kinds of checks:\n"
        "1. Methodology compliance — does the narrative respect every rule in "
        "the methodology document below? (units mandatory, no comma separators, "
        "slope-only framing, no period-aggregate comparisons, no judgement words, "
        "no fabricated customer/line names, etc.)\n"
        "2. Data sync — does every numeric claim in the narrative trace to a "
        "value in the CSVs?\n\n"

        "Severity rules:\n"
        "- HARD: a numeric claim or factual assertion that does NOT trace to the "
        "data (a fabricated number, wrong customer/line name, a conclusion the "
        "data does not support).\n"
        "- FIXABLE: a methodology rule violation the agent can correct by "
        "rewriting the offending block.\n\n"

        "There is no 'soft' tier. Every warning is hard or fixable.\n\n"

        "SCOPE RULES — BE CONSERVATIVE. DO NOT OVER-INTERPRET. Prefer a false\n"
        "negative over a false positive — the operator reviews the narrative\n"
        "visually before publication.\n\n"

        "For JUDGEMENT-WORD violations, only flag if the narrative literally\n"
        "contains one of these EXACT words: disappointing, bad, failure, worst,\n"
        "best, cleanest, slowest, strongest, weakest, poor, great, excellent,\n"
        "problematic.\n"
        "  DO NOT flag descriptive adjectives like 'clean', 'high', 'low',\n"
        "  'highest', 'lowest', 'higher', 'lower' even when applied to metrics.\n"
        "  DO NOT flag phrases like 'highest-leverage opportunity' or 'largest\n"
        "  lever' in the actions section — actions are SUPPOSED to suggest\n"
        "  priorities.\n"
        "  DO NOT flag editorialised phrases in headlines or pattern intros\n"
        "  unless they contain a literal word from the list above.\n\n"

        "For PERIOD-AGGREGATE violations, only flag if the narrative makes a\n"
        "TREND claim by comparing two specific time buckets:\n"
        "  FLAG: 'compared to last week', 'up from W19', 'rose from X to Y',\n"
        "  'first half vs second half', 'month over month', 'ticked up'.\n"
        "  DO NOT flag the literal word 'overall' or 'across the period' when\n"
        "  describing a period-aggregate figure (a secondary diagnostic per\n"
        "  methodology, not a trend claim).\n"
        "  DO NOT flag descriptive prose like 'second half of the period\n"
        "  consistently above X' if it is a descriptive anchor, NOT framed as\n"
        "  the headline trend statement.\n\n"

        "For TREND-DIRECTION claims (e.g. 'trending upward', 'gently improving'):\n"
        "  FLAG ONLY if the narrative quotes an OLS slope VALUE that\n"
        "  contradicts the data (e.g. claims slope is +0.3 but the data\n"
        "  actually slopes the other way).\n"
        "  DO NOT flag general directional phrases without a numeric slope —\n"
        "  treat those as soft narrative claims the operator can evaluate.\n\n"

        "For DATA-SYNC violations, the bar is HIGH: only flag claims that\n"
        "demonstrably contradict the CSV. Quote the contradicting CSV cell as\n"
        "evidence in every sync warning.\n\n"

        "If you self-withdraw a flag mid-analysis (i.e. you write 'flagging\n"
        "withdrawn' or 'no fix required'), DO NOT include that warning in the\n"
        "output. Only emit warnings you're confident are real.\n\n"

        "Be specific: every warning must include the exact claim, the location "
        "in the narrative (JSON-path), the issue, and the evidence — quote the "
        "CSV value or the methodology rule that conflicts.\n\n"

        "Warning IDs: assign each warning a stable id (w1, w2, ...). The reviewer "
        "uses these to target overrides.\n\n"

        "If the narrative is clean, return status: pass with an empty warnings array, "
        "summary.hard_count = 0, and summary.fixable_count = 0.\n\n"

        "Override block: every warning must include an `override` block. When you "
        "emit a warning fresh, set overridden: false, justification: \"\", and "
        "overridden_at: null. Do not invent overrides.\n\n"

        f"METHODOLOGY DOCUMENT\n{methodology}\n\n"

        "OUTPUT SCHEMA  (your response MUST be a single JSON object conforming "
        "to this schema; no prose, no markdown fences, no commentary outside "
        f"the JSON):\n{json.dumps(schema, indent=2)}\n"
    )


def _user_prompt(
    narrative: dict, csv_texts: dict[str, str], report_id: str,
) -> str:
    parts: list[str] = []
    parts.append(f"REPORT ID: {report_id}")
    parts.append("")
    parts.append("NARRATIVE TO VERIFY")
    parts.append(json.dumps(narrative, indent=2))
    parts.append("")
    parts.append("UNDERLYING DATA  (the CSVs the narrative should trace to)")
    for fn, text in csv_texts.items():
        parts.append(f"--- {fn} ---")
        parts.append(text if text else "(missing)")
        parts.append("")
    parts.append(
        "Produce the verifier report as a single JSON object conforming to the schema. "
        "Use category: 'methodology' for rule violations, 'sync' for data-sync issues, "
        "'tone' for judgement-word / framing issues, 'other' for anything else. "
        "Do not include any text outside the JSON."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Anthropic call + JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract the first valid JSON object from the response.

    Tolerates ```json fences, prose around the JSON, and trailing extra
    characters / stray braces (uses raw_decode to find the end of the
    first valid object, ignoring everything after).
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    text = text.strip()
    first = text.find("{")
    if first < 0:
        raise json.JSONDecodeError("no '{' in response", text, 0)
    obj, _ = json.JSONDecoder().raw_decode(text[first:])
    return obj


def _call_anthropic(system: str, user: str, schema_error: str = "") -> str:
    """Single API call. SDK handles retries on 429 / 5xx / timeouts (Decision 24)."""
    import anthropic  # lazy import so mock mode works without the SDK

    client = anthropic.Anthropic(max_retries=SDK_MAX_RETRIES)

    messages: list[dict] = [{"role": "user", "content": user}]
    if schema_error:
        messages.append({
            "role": "assistant",
            "content": "(previous response did not conform to the schema)",
        })
        messages.append({
            "role": "user",
            "content": (
                "Your previous response did not validate against the schema. "
                f"Validator error: {schema_error}\n"
                "Return a corrected JSON object conforming to the schema. "
                "Do not include any text outside the JSON."
            ),
        })

    response = client.messages.create(
        model=VERIFIER_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Top-level verify
# ---------------------------------------------------------------------------

def verify(
    *,
    narrative_path: Path,
    methodology_path: Path,
    schema_path: Path,
    output_dir: Path,
    report_id: str,
    report_out: Path,
) -> dict[str, Any]:
    narrative   = _load_json(narrative_path)
    methodology = _read_or_empty(methodology_path)
    schema      = _load_json(schema_path)
    csv_texts   = _load_pipeline_csvs(output_dir)

    system = _system_prompt(methodology, schema)
    user   = _user_prompt(narrative, csv_texts, report_id)

    raw = _call_anthropic(system, user)
    try:
        report = _extract_json(raw)
        jsonschema.validate(report, schema)
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        print(f"  verifier: first response failed validation ({e}); retrying with error context")
        raw = _call_anthropic(system, user, schema_error=str(e))
        report = _extract_json(raw)
        jsonschema.validate(report, schema)

    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, indent=2))
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verifier agent (step ⑤ of the weekly run, Decisions 9, 18, 26).",
    )
    parser.add_argument(
        "--narrative", default="outputs/narrative_blocks.json",
        help="Narrative JSON to verify (default: %(default)s)",
    )
    parser.add_argument(
        "--methodology", default="methodology/context.md",
        help="Path to the methodology document (default: %(default)s)",
    )
    parser.add_argument(
        "--schema", default="architecture/schemas/verifier_report.schema.json",
        help="Path to the verifier_report JSON schema (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir", default="outputs/",
        help="Directory containing the pipeline CSVs (default: %(default)s)",
    )
    parser.add_argument(
        "--report-id", default="demo",
        help="Report id (recorded in the verifier_report) (default: %(default)s)",
    )
    parser.add_argument(
        "--report-out", default="outputs/verifier_report.json",
        help="Where to write the verifier report (default: %(default)s)",
    )
    parser.add_argument(
        "--mock-verifier-report", default=None,
        help="If provided, copy this JSON to --report-out (after schema validation) "
             "and skip the API call. Lets the repo demo run without an "
             "ANTHROPIC_API_KEY.",
    )
    args = parser.parse_args()

    report_out  = Path(args.report_out)
    schema_path = Path(args.schema)

    # Mock mode
    if args.mock_verifier_report:
        src = Path(args.mock_verifier_report)
        if not src.exists():
            print(f"ERROR: mock verifier report not found: {src}", file=sys.stderr)
            sys.exit(1)
        report = _load_json(src)
        schema = _load_json(schema_path)
        try:
            jsonschema.validate(report, schema)
        except jsonschema.ValidationError as e:
            print(f"ERROR: mock verifier report does not conform to schema: {e}", file=sys.stderr)
            sys.exit(1)
        report_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, report_out)
        print(f"MOCK MODE: validated and copied {src} → {report_out}")
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Either export the env var or pass --mock-verifier-report fixtures/verifier_report.json.",
            file=sys.stderr,
        )
        sys.exit(1)

    report = verify(
        narrative_path=Path(args.narrative),
        methodology_path=Path(args.methodology),
        schema_path=schema_path,
        output_dir=Path(args.output_dir),
        report_id=args.report_id,
        report_out=report_out,
    )

    status  = report.get("status", "?")
    hard    = report.get("summary", {}).get("hard_count", 0)
    fixable = report.get("summary", {}).get("fixable_count", 0)
    print(f"Verifier emitted status={status}  (hard={hard}, fixable={fixable})")


if __name__ == "__main__":
    main()
