# Decisions log

A running record of the substantive design and methodology decisions made while
building this project. Each entry captures **what was considered**, **what was
chosen**, and **why**. New entries get appended at the bottom.

**Note on names**: this log uses generic placeholders (*the customer*, *the line*,
*the data source*) instead of real customer / location / vendor names. The real
names live only in the local working copy and untracked config files.

---

## 1. Per-productive-hour denominator for the headline rate

**Chose**: divide the loss-seconds-equivalent by `hours_on_product` — the time the
operator was actually filling product.

**Considered**: dividing by `active_shift_hours` (total scheduled time of the
active shifts in the period).

**Why**: production demand varies week to week. A low-demand week with few cycles
would look "efficient" under scheduled-hour normalisation (small numerator, larger
denominator) even if every cycle ran badly. Dividing by productive time asks the
question that matters — *when the operator was producing, how clean were the
cycles?* — and stays comparable across weeks of any volume. The per-scheduled-hour
rate is still computed and kept in the CSV for reference, but it isn't the
headline.

---

## 2. Compute rates on raw sums; round only at the end

**Chose**: `rate = (Σ loss_seconds / SOP) / (Σ cycle_seconds / 3600)` computed on
raw float sums, with the final value rounded only for display.

**Considered**: rounding intermediates first (e.g., converting loss to integer bag
count, then dividing by hours).

**Why**: rounding the numerator to integer bags before division silently
zeros-out small inefficiencies for narrow groups. A 2.5-bag-equivalent loss rounded
to 2 loses meaningful precision when the denominator is also small (a quiet shift,
a tail-end day). Aligning the code to "round once, at the end" preserves precision
and matches the formula as written in the methodology.

---

## 3. Largest-remainder method for cause-split allocation

**Chose**: total bags-of-opportunity is `round(Σ loss / SOP)`. Per-cause bags are
then allocated by **largest-remainder method** — floor each cause's exact value,
then distribute the remaining bags to causes with the largest fractional parts
until Σ(causes) = total.

**Considered**: rounding each cause's bag count independently.

**Why**: independent rounding can produce off-by-one mismatches between Σ(causes)
and the total, because `round(0.5) + round(0.5) + round(0.5)` is not the same as
`round(1.5)`. The dashboard would then display a cause split that doesn't add up to
the headline. Largest-remainder allocation (the same algorithm used in
proportional-voting seat allocation) guarantees Σ(causes) = total exactly, while
giving the extra bag(s) to the cause(s) with the largest fractional shares — i.e.,
the most "deserving" of an extra.

---

## 4. Strict shift-boundary cutover, overriding upstream tolerance

**Chose**: re-derive `shift_date`, `shift_name`, and `weekday` from each cycle's
`timestamp_start` using the strict shift boundaries (timezone-aware), inside the
pipeline.

**Considered**: trust the upstream data source's shift assignment.

**Why**: the upstream source allows a few minutes' tolerance around boundaries (a
cycle starting a few minutes before the boundary is classified as the upcoming
shift). The methodology classifies it as the outgoing shift. The disagreement is
small (~0.14 % of cycles in our data) but propagates into weekly rollups around the
cutover. Re-deriving in the pipeline keeps the methodology document and the
implementation in lockstep. The original upstream behaviour is documented so anyone
investigating discrepancies has the context.

---

## 5. Exclude shift-dates with fewer than N cycles per day

**Chose**: a shift-date is excluded if its total cycle count is below 20. The
threshold lives in a constant in the exclusions module; a separate audit script
re-runs the threshold against the latest data and reports any new dates that should
be added to the explicit exclusion list.

**Considered**:
- A manually-curated list of excluded dates with no rule.
- Threshold on hours-on-product or operator presence instead of cycle count.

**Why**: the line occasionally records a handful of cycles on days that don't
reflect real production sessions — rework, brief tests, anomalies. Including those
distorts weekly rollups because the denominator is small *and* the cycle quality is
abnormal. There is a natural break in the data between ~15 and ~25 cycles per day;
below it, productive time is also marginal. A threshold is more maintainable than a
manual list and auto-catches future low-activity days. The audit script keeps the
explicit list reviewable in source control rather than recomputing the exclusion
set silently at runtime.

---

## 6. Per-week OLS slope, not period-vs-period aggregates, as the primary trend statement

**Chose**: the trend is the OLS slope on the per-week rate across all weeks with
data, with gap-respecting indices on the timeline.

**Considered**: comparing aggregates between two periods (e.g., first half vs
second half, month-over-month, "Jan-Feb vs April").

**Why**: period aggregates depend on where the boundary is drawn. With
unequal-length periods (e.g., one month vs two months) the comparison ends up
sensitive to which weeks fall on which side, and small framing changes (which
weeks are "early" vs "late") can flip the headline. The OLS slope across all weeks
gives a single answer that doesn't depend on bucketing choices, and its stability
can be probed (removing individual outlier weeks shows whether the trend is robust
or driven by one data point). Period-mean comparisons are kept as a secondary
sanity check, never as the primary headline.

---

## 7. Agent emits structured narrative JSON; deterministic code splices into HTML

**Chose**: the **Insights agent** emits a `narrative_blocks.json` (headline,
conclusions, actions, pattern intros, …) conforming to a fixed schema. A
deterministic Python script reads that JSON and splices the strings into a pre-built
HTML template. Charts, tables, layout, styling all remain deterministic — not
agent-touchable.

**Considered**: have the agent write the full HTML directly.

**Why**: an agent generating 900 KB of HTML has a wide blast radius — it could
change a chart configuration, drop a section, mis-quote a number into a styled
element. Narrowing the agent's output to narrative blocks only means
- the JSON schema can be validated before any HTML is built;
- charts / layout / methodology tab stay deterministic and reviewable;
- the agent's behaviour is testable in isolation, no HTML parsing required;
- the LLM can be swapped without changing the rendering.

Narrower contract, easier to reason about, easier to verify.

---

## 8. Insights agent is lightly stateful — reads last week's status JSON

**Chose**: agent input = (current week's CSVs) + (methodology document) + (last
week's compact status JSON containing headline value + trend direction). NOT the
full previous narrative.

**Considered**:
- *Stateless*: agent sees only current data + methodology each run.
- *Fully stateful*: agent sees the previous full narrative + current data.

**Why**: stateless gives clean independent runs but loses week-over-week
continuity ("the trend we flagged last week is still climbing"). Fully stateful
provides rich continuity but inherits the prior run's framing and any stale claims,
and grows the prompt. **Light state** — just the headline value and trend direction
— gives enough continuity to reference past observations without constraining the
agent to past wording. The compact summary is also easier to archive and re-feed
when re-running a past week.

---

## 9. Single verifier agent, runs BEFORE HTML splicing

**Chose**: one Verifier agent reads (a) the agent-emitted `narrative_blocks.json`,
(b) the underlying outputs, (c) the methodology document. It runs *before* the HTML
splice step. Emits a structured warnings report.

**Considered**:
- Read the final rendered HTML.
- Split into two verifiers (methodology compliance vs narrative-data sync).

**Why**: reading the narrative JSON is simpler than parsing the rendered HTML to
extract numbers — every claim is already structured. Running before HTML render
means catching hallucinated numbers early; we never render output we already know
is wrong. A single agent with two checks (methodology + sync) is simpler to operate
for the POC. Splitting into two specialised agents is justified later if their
inputs diverge meaningfully.

---

## 10. Severity-based verifier failure handling

> **Refined by Decision 18.** The original two-tier model (hard halts / soft
> surfaces) has been superseded by an auto-fix loop. The intent — distinguish
> what halts from what doesn't — is preserved; the soft tier is removed and an
> automated correction step now sits between the verifier and the reviewer.

**Chose**:
- **Hard errors** (e.g., a number in narrative does not exist in the data) →
  pipeline halts, status JSON marked `failed`, dashboard NOT generated.
- **Soft warnings** (e.g., methodology-tone drift, claim with weak support) →
  pipeline continues, warnings carried into the status JSON and surfaced on the
  fleet view card.

**Considered**:
- Block everything on any warning.
- Warn-and-continue for everything.

**Why**: blocking on every warning would make the pipeline brittle — it would halt
on cosmetic issues the customer wouldn't notice. Warning-but-continuing on
everything would let real bugs ship into the dashboard. Severity gives the verifier
the power to stop only what genuinely shouldn't be rendered, and gives the human
reviewer the soft-warning list to triage before sharing the dashboard further.

---

## 11. Fleet View as the cross-report index, driven by per-report status JSONs

**Chose**: a top-level `fleet_view.html` lists all reports across all customers.
Each report writes a small `status/<report_id>.json` at the end of its pipeline
run. The index builder reads all status JSONs and renders one card per report,
grouped by customer. New reports = drop a new config + run the pipeline; the index
auto-picks them up.

**Considered**:
- A single growing dashboard with multiple report tabs.
- Per-customer dashboards instead of per-report.

**Why**: customers can have N reports at different scopes (one line, multiple
lines, customer summary, cross-cutting comparisons across lines). A flat
per-customer-tab design would force every report into the same shape. A status-JSON
driven index decouples the per-report pipelines from the index — adding a new
report is a config change, not an index code change. The index regenerates from
whatever status files happen to be present.

---

## 12. POC scope: one customer, one report, full agent stack, automated

**Chose**:
- One customer, one report (single line, scope = single station).
- Full multi-agent stack (Insights + Verifier).
- Local cron for the Monday weekly run.
- No hosting in the POC.

**Considered**:
- Multiple reports for the same customer at the POC stage.
- Manual weekly trigger (no automation).
- Hosted from day one.

**Why**: the goal of the POC is to prove the **architecture**, not to spread across
many reports half-built. With one report end-to-end, additional reports become
config-file changes — the structural cost is paid once. Automating from day one
validates the loop (extract → pipeline → agents → fleet view); without it the
demo would still feel manual. Hosting can be deferred without risk because the
artefacts are static HTML — the pipeline doesn't depend on knowing where they'll
eventually live.

---

## 13. Folder structure: inputs / outputs / scripts at root

**Chose**: monthly source CSVs live in `inputs/` (with video-match files in
`inputs/videos/`); pipeline-generated CSVs live in `outputs/`; Python scripts and
the dashboard HTML live at the root.

**Considered**: keeping everything flat at the root; moving scripts into a
`scripts/` subfolder.

**Why**: separating inputs from outputs makes the regeneration story clean — every
file in `outputs/` is reproducible from `inputs/` + the scripts; nothing in
`inputs/` is touched by the pipeline. Scripts at the root keep intra-module imports
simple (`from data_exclusions import …` works without packaging), which matters
because several scripts share helpers. This split also makes `.gitignore` natural:
ignore `inputs/` and `outputs/` (they contain customer data), commit the scripts +
docs.

---

## 14. Show the *latest complete* week in the snapshot widgets

**Chose**: the KPI widgets at the top of the dashboard show the value of the
*latest complete* week (`week_end` ≤ today), not the absolute latest week. If a
partial week is present in the data, it is rendered visibly (a lighter bar in every
weekly chart) but does not feed the headline numbers.

**Considered**: show the absolute latest week's value in the widgets, partial or
not.

**Why**: a partial week's headline can swing wildly because the denominator
(productive hours so far this week) is incomplete. Reporting that number as the
"latest" misleads a customer who reads the widget value as a stable snapshot. The
partial week's data is still visible in the charts (with a lighter colour and a
chart-footer note explaining why) so the reader can see it's there — but the
headline values stay tied to a fully observed period.

---

## 15. Plain-language, slope-only narrative framing

**Chose**: the customer-facing narrative uses only OLS slopes and per-week values;
no period-aggregate comparisons (e.g., "month A vs month B"), no derived metrics
not defined in the methodology. Numbers always carry their units. Headlines lead
with what's improving and end with the single highest-leverage opportunity.

**Considered**: rich period-comparison framings ("month over month", "first half vs
second half"); leading with what's worst to drive urgency.

**Why**: period-aggregate framings invite arbitrary boundary choices that can
distort the story (see Decision #6). Slope-only framing is the same number the
chart trend line shows, so the narrative and the visuals tell the same story.
Leading with what's improving and ending with the opportunity gives the customer a
balanced read — they see progress and they know where to focus next — rather than a
deficit-only framing that erodes trust over time. Units on every number are a
basic clarity rule for an audience that includes production operators, not just
analysts.

---

## 16. Verifier report schema

**Chose**: the verifier emits a structured JSON file conforming to
[`architecture/schemas/verifier_report.schema.json`](./architecture/schemas/verifier_report.schema.json).
Top level: `report_id`, `status` (`pass`/`warn`/`fail`), `checks_run`, `summary`
(`{hard_count, soft_count}`), `warnings[]`. Each warning has a stable `id`
(`w1`, `w2`, …), `severity`, `category`, `claim`, `location` (JSON-path into the
narrative), `issue`, `evidence`, optional `suggestion`, optional `rule_id`
(reference to a methodology section), and an `override` block (`overridden: bool`,
`justification: str`).

**Considered**:
- A human-readable single-string `summary` instead of structured counts.
- A richer audit envelope at the top (`generated_at`, `verifier_model`,
  reviewer name).
- A minimal schema without the `override` block — relying on a separate file
  for the audit trail.

**Why**: counts in `summary` keep the data structured so the Fleet View card can
format the display string itself — `2 warnings`, `1 hard 2 soft`, or anything
else — without changing the verifier. A stable `id` per warning makes the
override CLI possible (it has something specific to target). Embedding the
`override` block inside the warning keeps the audit trail in one place: you
read the file and you see which warnings were overridden and why. The extra
envelope fields (`generated_at`, model identifier, reviewer name) were dropped
to keep the schema lean for the POC — they can be added later without breaking
existing consumers.

---

## 17. Reviewer override via CLI flag

**Chose**: when the verifier emits `status: fail`, the reviewer can publish the
dashboard anyway by re-running the pipeline with a CLI flag:

```bash
python run_weekly.py --override <warning_id> "<justification>"
```

The justification is mandatory; the orchestrator refuses to run the command
without it. On re-run, the targeted warning's `override` block is populated,
the status drops from `fail` to `warn`, and the dashboard renders with an
inline "Reviewer flags" section showing the overridden warning + justification.

**Considered**:
- *Manual JSON edit*: open `verifier_report.json` in an editor, flip
  `overridden: true`, type a justification, save, re-run. No CLI work, no
  extra code — but no enforcement (you could forget the justification) and
  no audit hook on the command itself.
- *No override at all*: if the verifier flags a hard error, you must fix the
  underlying problem (correct the narrative, update the methodology, or
  tighten the agent prompt) and re-run. Strictest possible.

**Why**: the CLI flag enforces a structured override (you cannot bypass without
a justification), the command itself is the audit trail (shell history records
when overrides happened), and re-running the verifier confirms that only the
warning you named was bypassed — nothing else snuck through. The
"no override" option is too brittle for a POC where the verifier is still
being calibrated and false positives are likely. The manual-JSON-edit option
reaches the same end state but with none of the enforcement.

The reviewer is always the project owner for the POC, so the override block
records the `justification` but not a separate `reviewer` identifier. If more
than one person reviews in future, that field is a small additive change.

See [RUNBOOK.md](./RUNBOOK.md) scenario 1 for the operational walk-through.

---

## 18. Auto-fix loop for fixable warnings; soft-warning tier removed

**Chose**: the verifier classifies each warning as `severity: hard` or
`severity: fixable`. The orchestrator handles each class differently:

- **Fixable** — missing or wrong units, methodology framing rules, structural
  format issues. The orchestrator hands the warning back to the Insights agent
  with a tight prompt:
  > *"You wrote `<the bullet>`. The verifier flagged: `<the issue>`. Fix only
  > this block, return the corrected JSON."*

  Insights re-emits just that block; the verifier re-checks; cap at 2 retries.
  If clean after the loop, the dashboard ships clean — the reviewer is not
  involved.
- **Hard** — narrative fabrication: a number or claim in the narrative that
  doesn't trace to a value in the CSVs. Pipeline halts immediately, no
  auto-fix attempted. The reviewer either fixes the root cause and re-runs,
  or uses `--override` (Decision 17) to publish anyway.

If the auto-fix loop exhausts its retries on a fixable warning, that warning is
escalated to hard severity — same halt-and-reviewer path as fabrication.

The original "soft warning" tier from Decision 10 is removed. Under this
model, the dashboard either ships **clean** (everything passed or was auto-fixed
in the loop) or **halts** (reviewer judgment required). The Fleet View has no
"yellow with informational warnings" state; 🟡 is reserved for "published with
override" only.

**Considered**:
- *Keep the soft tier*: let the dashboard ship with informational warnings
  visible inline. Reviewer scans them but isn't required to act.
- *Auto-fix loop AND a soft tier*: auto-fix strict-rule violations; surface
  stylistic things as soft warnings.
- *Reading 1 — surface every auto-fix to the reviewer*: even when the loop
  succeeds, the reviewer sees an "orientation" list of what was fixed. More
  audit visibility at the cost of reviewer load.

**Why**: the primary goal is an accurate report — accuracy first. The secondary
goal is to minimise the time the reviewer spends per week. The auto-fix loop
serves both: it eliminates the trivial corrections (missing units, framing tics)
automatically, and only escalates when the agent can't get something right after
being told. The reviewer's inbox stays small and high-signal.

The "surface every auto-fix" option (Reading 1) would have given more audit
visibility but at the cost of putting more on the reviewer's screen each week —
defeating the goal. Auto-fixes are still recorded in `verifier_report.json` and
in the per-report status JSON for audit; they're just not surfaced on the
Fleet View card or the dashboard by default.

The "keep soft tier" option preserves the original two-tier model from
Decision 10 but doesn't take advantage of the agent's ability to fix its own
mistakes when told what's wrong. Auto-fix turns most soft warnings into a
non-event.

See [RUNBOOK.md](./RUNBOOK.md) scenario 2 for what happens when the auto-fix
loop exhausts.

---

## 19. Coverage checks deferred to post-POC

**Chose**: the verifier only checks what the narrative *says*, not what it
*omits*. If the underlying data contains a notable week-over-week change that
the Insights agent failed to discuss, the verifier will not flag it.

**Considered**:
- Add an *omission* check to the verifier — give it the CSVs plus an explicit
  list of "things worth mentioning" rules, then let it flag the narrative when
  an important data point is missing.
- Build a separate "coverage" agent dedicated to the question.

**Why defer**: the question is hard for two reasons. First, "important enough
to mention" is subjective — turning it into a verifier rule means writing
explicit thresholds into the methodology (e.g., *"any per-shift slope reversal
of more than 0.2 bags/h/wk must be discussed in the per-shift trend intro"*).
That's a body of methodology work in itself, and the right thresholds aren't
obvious upfront. Second, the narrative is already tightly structured
(Decision 15) — 4-6 conclusion bullets, top 3 actions, drivers insight,
pattern intros — leaving the agent little room to *miss* a big story. The
structure functions as a coverage rail by design.

**Trigger to revisit**: if a few weeks of real runs show the Insights agent
consistently missing notable week-over-week changes, that's the signal to add
a coverage check. Until then, the cost of writing coverage rules (methodology
effort) and the risk of verifier nagging (narratives bloating into a wall of
text covering every minor change) outweigh the benefit.

---
