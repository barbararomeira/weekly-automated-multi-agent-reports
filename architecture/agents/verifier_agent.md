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
The orchestrator reads that report and reacts:

- **Fixable warnings** (e.g. missing units, methodology framing tics) → the
  orchestrator runs an auto-fix loop: hands the warning back to the Insights
  agent for a targeted re-emit, then re-runs the verifier. Up to 2 retries.
- **Hard warnings** (e.g. a number in the narrative that doesn't exist in the
  data) → pipeline halts. The reviewer either fixes the root cause and re-runs,
  or uses `--override` to publish despite the warning.

There is no "soft warning" tier — see Decision 18.

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

- `report_id`, `status` (`pass` / `warn` / `fail`), `checks_run`, `summary` (`hard_count` + `fixable_count`), and an array of `warnings`.
- Each warning has a stable `id` (e.g. `w1`), `severity` (`hard` or `fixable`), `category`, the `claim` from the narrative, its `location` (JSON-path into `narrative_blocks.json`), the `issue`, the `evidence`, an optional `suggestion`, an optional `rule_id` pointing to a methodology section, and an `override` block.
- The `override` block contains `overridden: bool` and a `justification` string — populated by the orchestrator's `--override` flag when the reviewer bypasses a hard error.

**Status semantics** (reflect the state at the end of the run, *after* the auto-fix loop has run):

- `pass` — no warnings remain, OR every fixable warning was resolved by the auto-fix loop.
- `warn` — at least one hard error exists, but every hard error has been overridden by the reviewer (justifications recorded in each `override.justification`). Dashboard publishes with inline reviewer flags.
- `fail` — at least one un-overridden hard error remains; pipeline halts; dashboard NOT generated.

## Auto-fix loop (fixable warnings)

When the verifier flags a warning with `severity: fixable`, the orchestrator runs
an automated correction loop *before* deciding the overall status:

1. The orchestrator picks up each fixable warning from `verifier_report.json`.
2. It hands the warning back to the Insights agent with a tight, scoped prompt:
   > "You wrote `<exact claim>` at `<location>`. The verifier flagged: `<issue>`. The evidence is: `<evidence>`. Fix only this block, return the corrected JSON for `<location>`."
3. The Insights agent re-emits just that block (not the full narrative).
4. The block is patched into `narrative_blocks.json`.
5. The verifier re-runs on the updated narrative.
6. If the warning is gone, the loop succeeds for that warning. If the warning
   still flags, repeat — capped at 2 retries.
7. If the warning still flags after the retry cap, it is escalated to
   `severity: hard` (same halt + reviewer path as fabrication).

The reviewer never sees a fixable warning that the loop resolved. They only
see warnings on the dashboard if (a) the verifier flagged a hard error, or (b)
a fixable warning escalated after exhausting its retries.

## Override workflow (hard warnings only)

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
- HARD: a numeric claim or factual assertion that does not trace to the data —
  the agent has fabricated or mis-stated something the data does not support.
  Hard errors halt the pipeline; they cannot be fixed by re-rendering text.
- FIXABLE: a violation of a methodology rule that the agent can correct by
  rewriting the offending block — missing or wrong units, forbidden framing
  (e.g. period-aggregate comparisons), judgemental tone words, structural
  format issues. The orchestrator will hand each fixable warning back to the
  Insights agent for a targeted re-emit.

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

## What "fixable" looks like (examples)

| Example | Why it's fixable |
|---|---|
| Narrative writes "loss rate of 12.45" with no unit | The right value, the right framing — just missing `bags/h`. Agent is told to add the unit; re-emit. |
| Narrative compares "January-February vs April" as the headline trend | Forbidden framing per Decision 6 / §8 (period aggregates instead of the per-week slope). Agent is told to re-state the trend as a slope; re-emit. |
| Narrative leads with the worst metric instead of leading with what's improving | Methodology rule on positive-first framing (Decision 15). Agent is told to lead with the improving metric; re-emit. |
| Slope reported as "+0.41" when methodology rounds to "+0.4" | Precision style. Agent is told to round to 1 decimal; re-emit. |
| Narrative uses a judgement word like "disappointing" | Methodology forbids judgement language. Agent is told to use neutral phrasing; re-emit. |

## Failure modes (the verifier itself)

| Mode | Detection | Behaviour |
|---|---|---|
| Schema-non-conforming verifier output | JSON-schema validation post-call | retry once with the schema error; if still bad, halt as if hard error |
| API error / timeout | wrapper script | retry with backoff; halt after N attempts |
| Verifier flags a hard error that is actually a false positive | Manual review when status shows `fail` and the run halts | Reviewer uses `--override <warning_id> "<justification>"` to publish anyway (Decision 17 / RUNBOOK scenario 1) |
| Auto-fix loop runs out of retries on a fixable warning | Orchestrator escalates the warning to `severity: hard` | Same halt + override path as fabrication (RUNBOOK scenario 2) |
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
- ~~Hard vs soft vs fixable classification~~ **Resolved 2026-05-21** — auto-fix loop for fixable; immediate halt for hard; soft tier removed. See DECISIONS.md entry 18 and RUNBOOK.md scenario 2.
- Should the verifier also check for *missing* coverage (i.e., the narrative
  failed to mention an important week-over-week change)? More ambitious — might
  belong to a separate "coverage" agent later. *(Open — A.4)*
- Cost of running both agents weekly — needs an estimate before locking in
  model choice. *(Open — E)*
