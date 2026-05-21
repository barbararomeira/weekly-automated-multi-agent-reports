# Verifier agent — contract

**Status: DRAFT v0**  ·  *not finalised, iterating*

## Purpose

Check that the Insights agent's output is fit to publish. Two questions, one
agent:

1. **Methodology compliance** — does the narrative respect the rules in the
   methodology document? (E.g., it should never report `hours_lost` as the
   primary trend signal; it should always frame the loss-rate per productive
   hour; it should use OLS slopes, not period-aggregate comparisons.)
2. **Data sync** — does every numeric claim in the narrative trace back to an
   actual value in the latest CSV outputs? (E.g., if the narrative says
   "loss rate 10.88 bags/h," that exact value should appear in the latest
   complete week's row of `weekly_production_opportunity.csv`.)

This agent **does not** rewrite the narrative; it only emits a warnings report.
The pipeline halts on hard errors and continues with surface-warnings on soft
errors.

## Inputs

| What | Source | Why |
|---|---|---|
| The just-generated narrative | `outputs/narrative_blocks.json` | What needs checking |
| Latest CSV outputs | `outputs/*.csv` | The data the narrative claims to be derived from |
| Methodology document | `methodology/context.md` | The rules every claim must respect |
| Report config | `config/<report_id>.yaml` *(TBD)* | Customer / line / threshold constants |

The Verifier runs **before** the HTML splicer — we don't render output we
already know is wrong.

## Output

`outputs/verifier_report.json`. Shape locked in
[`architecture/schemas/verifier_report.schema.json`](../schemas/verifier_report.schema.json).
Summary of the structure:

- `report_id`, `status` (`pass` / `warn` / `fail`), `checks_run`, `summary` (counts of hard + soft), and an array of `warnings`.
- Each warning has a stable `id` (e.g. `w1`), `severity`, `category`, the `claim` from the narrative, its `location` (JSON-path into `narrative_blocks.json`), the `issue`, the `evidence`, an optional `suggestion`, an optional `rule_id` pointing to a methodology section, and an `override` block.
- The `override` block contains `overridden: bool` and a `justification` string — populated by the orchestrator's `--override` flag when the reviewer bypasses a hard error.

**Status semantics:**

- `pass` — no warnings.
- `warn` — soft warnings only, OR all hard errors have been overridden by the reviewer (justifications recorded in each `override.justification`).
- `fail` — at least one un-overridden hard error; pipeline halts; dashboard NOT generated.

## Override workflow

When the verifier emits `status: fail`, the pipeline halts and the Fleet View card surfaces each hard warning inline (claim + issue + evidence + the override command). The reviewer can either fix the root cause and re-run normally, or — if convinced the warning is a false positive — bypass it:

```bash
python run_weekly.py --override w1 "<short, specific reason — what made this a false positive>"
```

The justification is mandatory; the command fails without it. On re-run:

1. The verifier re-executes with `w1` flagged as overridden.
2. `verifier_report.json` records `override: { overridden: true, justification: "..." }` for that warning.
3. `status` drops from `fail` to `warn` (because the only hard error is now bypassed).
4. The dashboard renders, with the inline Reviewer flags section at the top showing the overridden warning + the justification — visible to anyone reading the dashboard later.
5. The Fleet View card flips from 🔴 to 🟡, with the warning count showing 1 overridden.

See [RUNBOOK.md](../../RUNBOOK.md) for the full operational walk-through.

## Prompt skeleton

```
SYSTEM
You are a methodology compliance + data sync verifier for a manufacturing
weekly report. You read a narrative and check it against (a) the methodology
document and (b) the underlying data. You emit a structured warnings report.

You DO NOT rewrite the narrative. You only flag issues. Be specific: every
warning must quote the exact claim and reference either a methodology rule or a
CSV cell as evidence.

Severity rules:
- HARD: a numeric claim that does not exist in the data, or a direct violation
  of a methodology rule explicitly stated as "MUST" in the methodology
  document. Hard errors halt the pipeline.
- SOFT: tone drift, weak framing, ambiguous phrasing, or methodology guidance
  the document expresses as "SHOULD" rather than "MUST". Soft warnings surface
  to the human reviewer.

METHODOLOGY DOCUMENT
{contents of context.md}

THE NARRATIVE BEING VERIFIED
{outputs/narrative_blocks.json}

THE UNDERLYING DATA
- weekly_time_on_product.csv:   {csv}
- weekly_long_cycle_loss.csv:   {csv}
- weekly_operator_presence.csv: {csv}
- weekly_production_opportunity.csv: {csv}
- shift_summary.csv:            {csv}
- weekday_summary.csv:          {csv}
- weekly_opportunity_by_shift.csv: {csv}

USER
Produce the verifier report as JSON conforming to the schema. Do not include
explanatory text outside the JSON.
```

## What "hard error" actually means (examples)

| Example | Why it's hard |
|---|---|
| Narrative claims "latest complete week loss rate: 10.88 bags/h" but the latest complete week in the CSV is 12.45 bags/h | Hallucinated number — direct contradiction with data |
| Narrative quotes `hours_lost` as the primary trend signal | Direct methodology violation — §6d explicitly says don't read absolute hours as the headline trend |
| Narrative compares "Jan-Feb vs April" as the headline trend statement | Direct methodology violation — Decision #6 / §8 says the per-week slope is the headline |
| Narrative references a customer name not in the report config | Direct misattribution |

## What "soft warning" looks like (examples)

| Example | Why it's soft |
|---|---|
| Narrative leads with the worst metric instead of leading with what's improving | Tone drift — the methodology prefers positive-first framing but doesn't mandate it |
| Slope is reported as "+0.41" but methodology rounds to "+0.4" | Precision style — cosmetic |
| Pattern intro references a shift the latest data doesn't actually mention | Soft, because the claim may still be reasonable in context |
| Narrative cites a partial week's value as if it were complete | Soft, because the data is real but the framing is misleading |

## Failure modes (the verifier itself)

| Mode | Detection | Behaviour |
|---|---|---|
| Schema-non-conforming verifier output | JSON-schema validation post-call | retry once with the schema error; if still bad, halt as if hard error |
| API error / timeout | wrapper script | retry with backoff; halt after N attempts |
| Verifier flags a hard error that is actually a false positive | Manual review when status shows `fail` and the run halts | TBD — should there be an override mechanism? |
| Verifier misses a real bug ("silent pass") | Catches via human review of the eventual dashboard | Reduces over time as prompt is tuned |

## Cost notes

- Run cadence: once per report per Monday, immediately after the Insights agent.
- Input size: same as Insights agent — under 50 KB total prompt.
- Output size: small — a list of warnings, typically empty or short.
- Model choice: TBD. A cheaper, faster model (Haiku-tier) is plausible because
  the task is structural and constrained.

## Open questions

- ~~Schema for `verifier_report.json`~~ **Resolved 2026-05-21** — see [`architecture/schemas/verifier_report.schema.json`](../schemas/verifier_report.schema.json) and DECISIONS.md entry 16.
- ~~Hard-error override mechanism~~ **Resolved 2026-05-21** — CLI flag `--override <warning_id> "<justification>"`. See DECISIONS.md entry 17 and RUNBOOK.md scenario 1.
- How is the methodology document parsed for "MUST" vs "SHOULD" rules? Currently
  these are not strictly tagged. Worth marking them up explicitly. *(Open — A.3)*
- Should the verifier also check for *missing* coverage (i.e., the narrative
  failed to mention an important week-over-week change)? More ambitious — might
  belong to a separate "coverage" agent later. *(Open — A.4)*
- Cost of running both agents weekly — needs an estimate before locking in
  model choice. *(Open — E)*
