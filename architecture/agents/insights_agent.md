# Insights agent — contract

**Status: DRAFT v0**  ·  *not finalised, iterating*

## Purpose

Generate the customer-facing narrative for one weekly report. Reads the
pipeline's deterministic outputs and the methodology document, writes a
structured JSON containing every prose block the dashboard renders.

This agent is **the only writer of customer-facing prose**. It does not generate
HTML, charts, layout, or numbers it didn't pull from a CSV. It does not author
methodology — it follows the methodology document as ground truth.

## Inputs

| What | Source | Why |
|---|---|---|
| Latest weekly + daily CSVs | `outputs/*.csv` | The data being narrated |
| Methodology document | `methodology/context.md` | Defines metric semantics, what's a valid framing, what's "improving" vs "worsening" |
| Last week's compact status | `status/<report_id>.json` from the prior run | Light continuity — agent knows last week's headline value + trend direction, but not the prior narrative wording |
| Report config | `config/<report_id>.yaml` *(TBD)* | Customer name, line name, methodology constants (SOP, presence threshold, exclusion threshold) |

Inputs are read at agent-call time, not embedded statically in the prompt.

## Output

Exactly one file: `outputs/narrative_blocks.json`, conforming to
`architecture/schemas/narrative_blocks.schema.json`.

Validation happens before the file is handed downstream:
- Schema check (structural)
- Every numeric claim either matches a CSV value or is one of a whitelisted set of
  derived computations (e.g., a slope reported in the methodology) — full
  semantic check is the Verifier agent's job, but a basic shape check happens here.

## Prompt skeleton

```
SYSTEM
You are a data-driven narrator for a manufacturing weekly report. Your job is to
turn the week's data into a short, clear, balanced narrative that a production
operator could read. Follow the methodology document exactly. Write only the
fields requested by the schema. Numbers always come from the data — never invent.

Rules:
- Lead with what is improving; end with the single highest-leverage opportunity.
- Use OLS-slope phrasing for trends; do not compare arbitrary period aggregates.
- Every number must include its unit.
- No comma separators in numerals.
- Refer to the customer and line by the names in the config; do not invent.
- Calibrate the strength of claims to data adequacy. Before drawing trend
  conclusions, read `productive_hours` per week, the total number of weeks
  available, and which shifts contributed each week. If the data is thin —
  few weeks, a week with anomalously low `productive_hours` relative to the
  dataset typical, or a shift absent from recent weeks — describe what's
  there without anchoring strong claims on noisy signals (Decision 20).

METHODOLOGY DOCUMENT
{contents of context.md}

LAST WEEK'S COMPACT STATUS  (light continuity)
{contents of status/<report_id>.json from the prior run, or {} if none}

THIS WEEK'S DATA
- weekly_time_on_product.csv:   {csv contents or summary}
- weekly_long_cycle_loss.csv:   {csv contents or summary}
- weekly_operator_presence.csv: {csv contents or summary}
- weekly_production_opportunity.csv: {csv contents or summary}
- shift_summary.csv:            {csv contents}
- weekday_summary.csv:          {csv contents}
- weekly_opportunity_by_shift.csv: {csv contents}

REPORT CONFIG
{contents of config/<report_id>.yaml}

USER
Produce the narrative as JSON conforming to the schema. Do not include
explanatory text outside the JSON.
```

The prompt is intentionally minimal — most of the agent's "rules" live in the
methodology document, which the agent has full access to. That keeps the
behaviour tuneable without touching code.

## Failure modes (and how they're handled)

| Mode | Detection | Behaviour |
|---|---|---|
| Schema-non-conforming JSON | JSON-schema validation post-call | retry once with the schema error in the prompt; if still bad, halt the pipeline |
| Numeric claim doesn't exist in the data | Verifier agent (next step) | severity-based — see Verifier doc |
| Tone drift (e.g., leads with what's worst) | Verifier agent | soft warning, dashboard still renders |
| API error / timeout | wrapper script | retry with exponential backoff; halt after N attempts |
| Methodology violation (e.g., reads absolute hours_lost as the headline trend) | Verifier agent | hard error |

## Cost notes

- Run cadence: once per report per Monday.
- Input size: bounded by the size of the CSV outputs (small — a few KB each) plus
  the methodology document (~20 KB) and last week's status (~1 KB). Total prompt
  well under 50 KB.
- Output size: bounded by the narrative schema (a few KB).
- Model choice: TBD. Sonnet-tier is the natural starting point for the prose
  quality; cheaper models worth testing.

## Open questions

- Should the agent see the full CSV contents inline, or pre-summarised by a
  deterministic helper that extracts only the values the narrative needs (e.g.,
  the OLS slope, the latest complete week's value)? Inline is simpler; pre-summary
  is cheaper and gives the agent less rope to misread.
- Schema versioning: do we tag every `narrative_blocks.json` with the schema
  version so the splicer knows how to handle future shape changes?
- How does the agent address the customer? Always second-person ("your line"),
  third-person ("the line"), or driven by config? Probably config.
- For a *partial week* in the data, should the agent be told explicitly to ignore
  it, or to mention it as caveat-only? The data is already flagged with
  `partial_weeks`; the agent should be instructed to treat those carefully.
- Retry-on-validation-failure: bounded to 1 retry, or 0 (let the next Monday
  catch it)?
