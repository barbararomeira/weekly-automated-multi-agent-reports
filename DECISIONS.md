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

> **Expanded by Decision 22.** The "light state" was originally just the
> headline value + trend direction. Decision 22 broadens it to include the
> previous week's `main_conclusions` and `top_3_actions` blocks so the agent
> can write genuine cross-week continuity (*"last week we flagged X; this
> week..."*). Mitigations against framing inheritance are documented in
> Decision 22.

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

## 20. Pipeline self-audit scope: bug detection only

**Chose**: step ③ (`DATA-QUALITY CHECK`) only halts the pipeline when the
data is *broken* in a way the rest of the system cannot recover from.
Everything interpretive — *"is this week sparse enough to caveat?"*, *"do we
have enough data for a slope?"*, *"is some shift missing from this week's
data?"* — moves to the Insights agent, which reasons about adequacy directly
from the data.

**The audit halts on:**
- **Pipeline correctness**: cause-bags sum to total bags; weekly aggregates
  match the sum of daily aggregates; per-shift bags summed across shifts
  equal the weekly total.
- **Schema**: all expected columns present in each output CSV.
- **Corruption**: no NaN in critical columns (`loss_rate`, `productive_hours`,
  `cycle_count`).
- **Impossible values**: no negative `loss_rate`, no negative
  `productive_hours`, no negative `cycle_count`; OLS slope within plausible
  bounds.
- **Existence**: every output CSV is non-empty.

**The audit does NOT halt on:**
- Sparse weeks (low productive hours).
- Too few weeks to compute a meaningful slope.
- Latest complete week not yet existing (e.g., brand-new report).
- Missing shifts in this week's data.
- Any other question of "is the data enough for this analysis?"

**Considered**:
- Folding interpretive thresholds into the audit (e.g., halt if fewer than N
  weeks of data; tag as sparse if productive hours below threshold T).
- A graded severity scheme on the audit itself (halt / warn / pass-through).

**Why**: the report is built end-to-end on a per-active-hour rate (Decision 1).
The Insights agent already has access to productive hours per week
(`weekly_time_on_product.csv`), the row count, and the shift coverage —
everything needed to judge whether a conclusion is supported by enough data.
Pre-deciding adequacy with audit thresholds duplicates that signal and makes
the system brittle: any new edge case would need a new threshold, and
thresholds chosen at audit-design time may not match how the agent should
narrate. Letting the agent reason directly from the data keeps both layers
honest — the audit becomes a small, testable set of deterministic invariants,
and the agent stays responsible for the qualitative judgement.

This also collapses the originally-imagined "warn / pass-through" tier in
the audit. The audit is binary: data is broken (halt) or data is fine
(pass). All interpretation happens downstream.

**Implication for the Insights agent**: the agent's prompt explicitly directs
it to consider how many weeks of data exist, how many productive hours each
week contributed, and whether all shifts are represented — and to calibrate
the strength of claims to what the data supports. The data carries the
signal; the prompt makes the agent attend to it.

---

## 21. narrative_blocks.json schema locked at v1.0

Schema: [`architecture/schemas/narrative_blocks.schema.json`](./architecture/schemas/narrative_blocks.schema.json).

**Chose**: lock the schema at v1.0 with these fields:

- `main_conclusions`: `{ headline, bullets[] }` where `bullets` is 4-6 items, each `{ title, body }` only — *no* `tone` field
- `top_3_actions`: exactly 3 items, each `{ title, body }`
- `drivers_insight`: single `{ title, body }`
- `patterns`: three intro paragraphs (`by_shift_intro`, `by_weekday_intro`, `per_shift_trend_intro`)
- `schema_version`: const `"1.0"`

**Considered**:
- Keep the `tone` field as the agent's call (good / watch / opportunity).
- Make `tone` deterministic — code reads the bullet body after emission and
  assigns the colour stripe.
- Add a structured `data_caveats` field for sparse / partial-week notes,
  rendered as a banner on the dashboard.

**Why**:

- **`tone` removed**. Subjective agent call invited verifier disagreement;
  mixed-signal bullets (*"loss rate improved overall, but 2nd shift trending
  down"*) had no clean answer. Removing the field removes the
  argument-surface entirely. Bullets without colour stripes read as prose
  rather than as alerts — appropriate for an operator audience.
- **Colour moves to the KPI widgets only**, deterministically from the
  per-week slope sign (improving → green, worsening → red, flat → neutral).
  The eye expects a quick signal at the headline number; the prose carries
  its own message without visual decoration. This logic lives in the
  dashboard rendering code, not in the schema or the agent.
- **No structured `data_caveats` field**. The report is built on
  per-active-hour rates (Decision 1) so a sparse or partial week's rate is
  still valid; the caveat is informational, not load-bearing. The agent
  weaves any caveat into the body of the relevant bullet where the context
  matters, rather than via a banner that would imply the data is suspect.
- **4-6 bullets / exactly 3 actions / three pattern intros** carry over
  from the v0 draft unchanged — the visual range had already been
  prototyped and works.

**Implication for the Insights agent**: tone selection drops out of the
agent's job. One fewer thing for the agent to be wrong about and one fewer
class of verifier warning. The agent still owns the positive-lean ordering
(Decision 15) and the in-prose data-adequacy caveats (Decision 20).

**Implication for the HTML splicer**: needs to compute KPI widget colours
deterministically from the per-week slope sign, and keep the trend-chart
slope line neutral (no green / red).

---

## 22. Status JSON schema locked at v1.0; continuity expanded to include main conclusions + top 3 actions

Schema: [`architecture/schemas/status.schema.json`](./architecture/schemas/status.schema.json).

**Chose**: each pipeline run writes `status/<report_id>.json` conforming to
v1.0 of the schema. The file serves two consumers:

- **Fleet View builder**: reads every status file and renders one card per
  report. Consumes `status`, `summary`, `warnings`, `report_display_name`,
  `customer`, `week_end`, `updated_at`, `dashboard_path`.
- **Next week's Insights agent**: reads its own previous status for
  cross-week continuity. Consumes `headline`, `trend`, `main_conclusions`,
  `top_3_actions`.

The schema references `narrative_blocks.schema.json` and
`verifier_report.schema.json` via `$ref`, so when those evolve, status.json
auto-tracks.

**Considered** (for the continuity portion specifically):
- Stay with Decision 8's *headline value + trend direction only*.
- Include *top_3_actions only* (no `main_conclusions`) — hedge that gives
  follow-through continuity without the previous narrative's full framing.
- Include the *full prior narrative* (every block including pattern intros
  and drivers insight).

**Why**: a weekly operations report serves the same team every Monday. The
customer's value comes from the *thread* — did last week's flagged
opportunity improve, was the top action acted on, did the trend they were
worried about continue? Without that thread, every report reads as week
zero. Decision 8 was conservative for fear that fully-stateful agents would
inherit prior framing; the full-narrative option is still too much
(crowds out fresh analysis with pattern intros / drivers insight), but
`main_conclusions` + `top_3_actions` is the right middle ground — the
customer-relevant context without the broader narrative scaffolding.

**Mitigations against framing inheritance**:

1. **Prompt ordering**. The Insights agent's prompt explicitly directs:
   *"First, analyse this week's data and form your own insights. Then check
   whether last week's observations are still valid given the new data.
   Only reference prior observations where the new data still supports
   them."* Prior context is a reference layer, not a template.
2. **Verifier as safety net**. The Verifier agent (Decisions 9 + 18) catches
   numeric claims that don't trace to the data. A stale inherited claim
   should be flagged if the data no longer supports it.
3. **Cheap revert**. If real runs show the agent leaning on prior framing
   in ways the verifier misses, reverting to Decision 8's
   headline + trend only is a small change.

**Self-containment**: `warnings` are copied verbatim from
`verifier_report.json` into status.json, so the Fleet View card needs only
one file per report to render. Reading every status file is the index
builder's only filesystem dependency.

---

## 23. HTML splicer correctness — hybrid safety net

The Verifier agent (Decisions 9, 18) checks the *narrative prose* against the
data. It does **not** check the rendered HTML — and the splicer pulls some
displayed values (the headline KPI widget, chart bar heights, tables, slope
line) directly from the CSVs without going through the narrative. A bug in
the splicer code (wrong column, wrong filter, wrong rounding) could make
those values disagree with the narrative without anything flagging it at
runtime.

**Chose**: a hybrid safety net.

1. **Dev-time tests on the splicer code**. Unit tests with known CSV inputs
   assert the rendered HTML contains the expected values; an integration
   test against a golden fixture week exercises the full splicer pipeline.
   These cover the broad surface area (every chart, table, widget).

2. **Lightweight runtime check on two critical values**. After the splicer
   produces the HTML, a small post-render step parses out and verifies:
   - The headline KPI widget value + unit (the rate displayed at the top
     of the dashboard).
   - The headline trend direction (the green / red colour, derived from
     the slope sign per Decision 21).

   Both are compared against what the pipeline computed for the latest
   complete week. Mismatch halts the run as a hard failure; no dashboard
   published; the reviewer sees it on the Fleet View the same way as a
   verifier-side hard warning.

Broader runtime HTML parsing (chart bars, tables, per-pattern numbers) is
**not** done — dev-time tests cover those, and runtime parsing of every
element grows brittle and expensive as the layout evolves.

**Considered**:
- **Dev-time tests only** — trust the deterministic splicer entirely; rely
  on unit + integration tests at development time.
- **Full runtime HTML-vs-CSV check** — parse the entire rendered HTML and
  verify every displayed number against the source CSVs.

**Why hybrid**:

- The headline KPI widget is the single highest-visibility number on the
  dashboard. A splicer bug that displayed the wrong number there would be
  the most damaging mistake the system could make. A two-value runtime
  check is small, fast, and catches the worst case.
- Beyond that, runtime HTML parsing grows brittle quickly — every chart
  and table needs its own rule; parsers break on layout changes. Dev-time
  tests are a better long-term home for that coverage.
- The Verifier agent already provides a runtime safety net for the
  narrative. The hybrid adds a deterministic check at the symmetrical
  layer (the rendered output) without the maintenance burden of a full
  HTML verifier.

**Implementation note**: the runtime check lives inside the orchestrator's
splicer step. On mismatch, the orchestrator writes a synthetic warning into
`verifier_report.json` (using `category: "other"`) and sets the status to
`fail`. From the reviewer's perspective, the failure mode is identical to a
verifier-side hard warning — same Fleet View card, same `--override` flow
(RUNBOOK scenario 1).

**Future revisit**: if dev-time tests prove sufficient after a few real
operating weeks, the runtime check can be removed without architectural
impact — deletion is a small change. Reviewing this is an explicit
post-POC task.

---

## 24. Orchestrator architecture (shape, retries, failure handling)

`run_weekly.py` is the entrypoint the Monday cron triggers. Three coupled
sub-decisions locked together.

### a. Shape: direct Python imports

Each pipeline step is a Python module exposing a top-level function
(e.g., `compute_kpis(kpi_dir, config_path)`). `run_weekly.py` imports them
and calls them in order in a single process. Data passes between steps in
memory (DataFrames, dicts) — *and* the steps still write their canonical
outputs to disk (CSVs, JSONs) per the existing data contracts.

Each step module *also* has an `if __name__ == "__main__":` block so it can
be run standalone for debugging or single-step re-runs
(`python scripts/insights_agent.py --kpi-dir outputs/ --config ...`).

**Considered**: shell-out via `subprocess.run` per step. Better isolation
and trivially re-runnable on its own — but cold-start overhead each week,
more CLI plumbing for API keys and model config, and tracebacks lose the
call-chain context.

**Why**: keeps the runtime simple — one process, function calls, full
Python tracebacks. The `__main__` blocks give the easy-debugging story of
subprocess without the cold-start cost. Configuration (API keys, model
choice, retry knobs) is plain function args, not env-var plumbing.

### b. Retry policy: rely on SDK built-ins

The Anthropic Python SDK ships with retry on 429 / 5xx / timeouts using
exponential backoff. Configure `max_retries=3` and trust it. No
orchestrator-level wrap-around retry layer.

Schema-validation retries on agent output are a separate one-shot retry
handled inside each agent's wrapper — already documented in
[insights_agent.md](./architecture/agents/insights_agent.md) and
[verifier_agent.md](./architecture/agents/verifier_agent.md).

**Considered**: custom retry layer wrapping the SDK for finer control over
specific error types.

**Why**: built-in SDK retries are tested in the wild against the exact
transient-error patterns we'd want to handle. Re-implementing buys nothing
for the POC and just adds maintenance.

### c. Failure handling: halt cleanly + write `status: fail`

Any step that fails — LLM retries exhausted, audit invariant violated,
splicer post-render mismatch, unexpected exception — halts the pipeline.
The orchestrator writes a synthetic warning into `verifier_report.json`
with `category: "other"` (same shape as a verifier-side hard warning),
copies it into `status.json`, sets `status: fail`, and exits.

The Fleet View card surfaces the failure with the cause inline; the
RUNBOOK scenarios apply identically regardless of cause.

**Considered**: let the orchestrator just crash (exception propagates,
nothing written). Simpler code, but the reviewer wouldn't see the failure
until they checked the cron logs.

**Why**: a single failure-reporting channel means one set of Fleet View
behaviours covers every failure mode. The operator never has to check
cron logs to know something broke.

### Implications

- **status.schema.json**: the four continuity fields (`headline`, `trend`,
  `main_conclusions`, `top_3_actions`) and `dashboard_path` are optional —
  they're only present when the run got far enough to produce them. The
  required set narrows to the fields every run can produce regardless of
  outcome.
- **Insights agent**: must be defensive about the continuity payload. If
  the prior week's status is `fail` and the continuity fields are absent,
  the agent treats it as *"no prior status available"* and writes a fresh
  start that week.

---

## 25. Override audit: timestamp added now, cross-week log deferred

**Chose (now)**: extend the `override` block in `verifier_report.json`
with an `overridden_at` field — an ISO 8601 timestamp of when the
reviewer's `--override` CLI invocation was applied. Null when
`overridden` is false. Stamped automatically by the orchestrator at
override time. The new field propagates into `status.json` via the
existing `$ref` from status.schema.json to verifier_report.schema.json,
so no separate change is needed there.

**Deferred (revisit when needed)**: do NOT build a dedicated cross-week
overrides log file for the POC. Per-week audit is already captured fully
in `verifier_report.json` + `status.json`; a cross-week lookup is served
by `grep -r '"overridden": true' status/` for POC-scale volumes.

**Considered (deferred bucket)**:
- Append-only `overrides.log` at the project root with one line per
  override across all reports.
- A dashboard page or Fleet View section surfacing recent overrides.

**Why the timestamp now**: knowing *when* a decision was made is
load-bearing for audit / compliance. The marginal cost is tiny — one
extra field on the schema plus the orchestrator stamping it when
applying `--override`. The cost of adding it later, after overrides
have already happened without timestamps, is much higher (a body of
data missing the field).

**Why defer the cross-week log**: per-week files capture every override
completely (claim, issue, evidence, override block, rule_id). Cross-week
aggregation is a query over those files, not new data. Building a
dedicated log adds maintenance for marginal benefit at POC scale.
Revisit when:
- The number of reports makes `grep` over `status/` impractical, OR
- Compliance / governance asks for a single feed.

**Trigger to revisit**: the operator finding themselves reaching for
`grep` more than a couple of times a month, or external audit
requirements landing.

---

## 26. Model choice per agent + rough cost

**Chose**:

- **Insights agent**: Claude Sonnet 4.6 (`claude-sonnet-4-6`).
- **Verifier agent**: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`).

Both call through the official Anthropic Python SDK with `max_retries=3`
on transient errors (Decision 24).

**Cost envelope** (rough, not exact pricing):

| Setup | Per weekly run | Per year (52 runs / report) |
|---|---|---|
| Insights=Sonnet, Verifier=Haiku *(chosen)* | ~$0.21 | ~$11 |
| Insights=Sonnet, Verifier=Sonnet | ~$0.30 | ~$15 |
| Insights=Haiku, Verifier=Haiku | ~$0.07 | ~$4 |

The chosen setup includes the headline call plus an assumed average of
two auto-fix loop rounds per run. Cost is rounding-error at this scale;
even at 10× the report volume the system stays well under $200/year.

**Considered**:
- *Both Haiku*: ~$4/year per report. Rejected because the Insights agent's
  prose-writing job involves multiple framing constraints (claim-strength
  calibration → Decision 20; positive-lean ordering → Decision 15; in-prose
  caveats → Decision 22) where Haiku carries a higher risk of generic or
  mis-calibrated output. Cents saved isn't worth the quality risk.
- *Both Sonnet*: ~$15/year per report. Rejected as overkill — the Verifier's
  task is structural (does this number trace to the CSV; is this framing
  forbidden by the methodology), squarely in Haiku's wheelhouse.
- *Opus for either*: rejected — several × the cost without measurable
  quality gain for this use case.

**Why Sonnet for Insights**:
- Multi-step reasoning: read methodology + data + prior status, then write
  balanced prose with specific rules. Sonnet handles instruction-following
  with many simultaneous constraints better than Haiku.
- Narrative quality: the output is customer-facing prose; tone, clarity,
  and ordering all matter.
- Cost difference is rounding error.

**Why Haiku for Verifier**:
- Structural task: matching numbers from prose to CSV cells; checking
  forbidden phrases (period-aggregate comparisons, judgement words);
  validating unit presence. All in Haiku's strength.
- Speed: Haiku's lower latency matters because the auto-fix loop can run
  the Verifier multiple times per weekly run.
- Cheap upgrade path: if real runs show the Verifier missing real issues,
  upgrading to Sonnet is a config-only change.

**Trigger to revisit**:
- Verifier consistently missing real issues (false negatives) → upgrade to
  Sonnet.
- Insights producing generic or off-brand prose → likely prompt-side fix
  first; Opus is the step beyond that if needed.
- Anthropic releases newer / cheaper variants — re-evaluate.

---
