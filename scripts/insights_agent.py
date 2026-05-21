"""Insights agent (step ④ of the weekly run).

Reads the methodology document, the report config, the pipeline's output
CSVs, and last week's status JSON (if any). Calls Claude Sonnet 4.6 via
the Anthropic SDK and produces a narrative_blocks.json that conforms to
v1.0 of architecture/schemas/narrative_blocks.schema.json.

The agent validates its own output against the schema before writing.
On a schema violation, it retries once with the error context in the
prompt; if still invalid, it propagates the validation error.

`--mock-narrative <path>` bypasses the API entirely and just copies the
given JSON to the output (validated against the schema first). This is
how the repo demo runs without an ANTHROPIC_API_KEY — the agent code is
real, the API call is just skipped in mock mode.

Model + retries: Decision 26 — claude-sonnet-4-6 with the SDK's built-in
retry policy (Decision 24).
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
import yaml

INSIGHTS_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
SDK_MAX_RETRIES = 3  # Decision 24 — built-in SDK retry on 429 / 5xx / timeouts


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
        "Rules:\n"
        "- Lead with what is improving; end with the single highest-leverage opportunity.\n"
        "- Use OLS-slope phrasing for trends; do not compare arbitrary period aggregates.\n"
        "- Every number must include its unit.\n"
        "- No comma separators in numerals.\n"
        "- Refer to the customer and line by the names in the config; do not invent.\n"
        "- Calibrate the strength of claims to data adequacy. Read productive_hours\n"
        "  per week, total weeks available, and which shifts contributed each week.\n"
        "  If the data is thin, describe what's there without anchoring strong claims\n"
        "  on noisy signals.\n"
        "- Process this week's data BEFORE anchoring on last week's. Form your own\n"
        "  conclusions from the new CSVs first; then check whether last week's\n"
        "  observations are still valid given the new data. Reference prior\n"
        "  observations only where the new data still supports them. Do NOT treat\n"
        "  last week's blocks as a starting template.\n"
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
    parts.append("Produce the narrative as a single JSON object conforming to the schema. "
                 "Do not include any prose, commentary, or markdown fences outside the JSON.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Anthropic call + JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the response. Tolerates ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip leading fence + optional language hint
        text = text.split("\n", 1)[1] if "\n" in text else text
        # strip trailing fence
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def _call_anthropic(system: str, user: str, schema_error: str = "") -> str:
    """Single API call. SDK handles retries on 429 / 5xx / timeouts."""
    import anthropic  # lazy import so mock mode works without the SDK

    client = anthropic.Anthropic(max_retries=SDK_MAX_RETRIES)

    messages: list[dict] = [{"role": "user", "content": user}]
    if schema_error:
        # Schema-violation retry: re-ask with the validator's error in context
        messages.append({"role": "assistant",
                         "content": "(previous response did not conform to the schema)"})
        messages.append({
            "role": "user",
            "content": (
                "Your previous response did not validate against the schema. "
                f"Validator error: {schema_error}\n"
                "Return a corrected JSON object conforming to the schema, "
                "with the same content adjusted to fix the violation. "
                "Do not include any text outside the JSON."
            ),
        })

    response = client.messages.create(
        model=INSIGHTS_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    return response.content[0].text


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
    methodology = _read_or_empty(methodology_path)
    schema      = _load_json(schema_path)
    config      = _load_yaml(config_path)
    prior       = _load_json(prior_status_path)
    csv_texts   = _load_pipeline_csvs(output_dir)

    system = _system_prompt(methodology, schema)
    user   = _user_prompt(config, prior, csv_texts)

    # First call
    raw = _call_anthropic(system, user)
    try:
        narrative = _extract_json(raw)
        jsonschema.validate(narrative, schema)
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        print(f"  insights: first response failed validation ({e}); retrying with error context")
        raw = _call_anthropic(system, user, schema_error=str(e))
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
             "and skip the API call entirely. Lets the repo demo run without an "
             "ANTHROPIC_API_KEY. The agent code is the same in both modes; only the "
             "API call is bypassed.",
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

    # Real mode requires an API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Either export the env var to make a real API call, or pass "
            "--mock-narrative fixtures/narrative_blocks.json to use the demo fixture.",
            file=sys.stderr,
        )
        sys.exit(1)

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
