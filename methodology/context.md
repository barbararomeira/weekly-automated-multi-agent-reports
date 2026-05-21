# Methodology — Weekly Operations Reporting

This document is the **source of truth** for how the weekly customer report is
computed and how the narrative is written. It is read by:

- The **Insights agent** (writes the customer-facing narrative)
- The **Verifier agent** (checks that the narrative respects every rule below)

If a rule appears in this document, the narrative MUST follow it and the
verifier MUST flag violations. If a rule is not in this document, the agent
should not invent one.

---

## 1. Domain

The system reports on one production-line station. Each cycle of the station
produces one unit of output (we'll call them **bags** throughout — the
methodology is generic to whatever the line produces). Each cycle has:

- A **timestamp** (when it started and ended)
- A **duration** in seconds (the elapsed wall-clock time)
- A **loss cause** — `machine_wait`, `material`, `operator_absent`, `rework`,
  or `changeover`, populated for any cycle whose duration exceeded the SOP
  (Standard Operating Procedure time — the cycle target, in seconds)
- An anonymous **operator id**

The pipeline aggregates these cycles into weekly, daily, per-shift, and
per-weekday tables. The agents read those aggregates plus this document plus
the prior week's status JSON, and write a structured narrative.

---

## 2. Core metric: loss rate (bags per productive hour)

The headline metric is **loss rate, in bags per productive hour**.

For every cycle:

- `loss_seconds = max(0, cycle_seconds - SOP)`
  (the excess time over the SOP target — if the cycle ran at SOP, loss is zero)
- `productive_seconds = cycle_seconds - loss_seconds`
  (the time that was actually used to produce output)

For any group (a week, a shift, a weekday, a per-week-per-shift slice):

- `bags = round(Σ loss_seconds / SOP)` — round **once, at the end** (the sum is
  done on raw float seconds; the integer rounding happens only on the final
  bag count)
- `productive_hours = Σ productive_seconds / 3600`
- `loss_rate = bags / productive_hours` *(bags per productive hour)*

**Why per productive hour?** Production volume varies week to week — a quiet
week with few cycles would look "efficient" if normalised by scheduled hours
(small numerator over a larger fixed denominator) even if every cycle ran
badly. Dividing by productive time asks the question that matters: *when the
operator was producing, how clean were the cycles?* The rate is comparable
across weeks of any size — including weeks shortened by maintenance.

---

## 3. Aggregation

### Shift classification (strict boundaries)

Every cycle is classified into a `(shift_date, shift_name)` from its
`timestamp_start`, using strict boundaries:

- `06:30 ≤ time < 14:30` → **1st shift**, `shift_date` = same calendar day
- `14:30 ≤ time < 22:30` → **2nd shift**, `shift_date` = same calendar day
- `22:30 ≤ time`          → **3rd shift**, `shift_date` = same calendar day
- `time < 06:30`          → **3rd shift of the PREVIOUS calendar day**

Any tolerance the upstream data system applies is *overridden* — the
methodology classification is strict. This matters because a cycle starting
a few minutes either side of a boundary would otherwise be attributed to the
wrong shift.

### Exclusion threshold

A shift-date is **excluded** from all aggregates if it has fewer than **20
cycles**. This filters out non-production activity (rework sessions, brief
tests, anomalous days) that would distort weekly rollups by contributing a
small denominator with abnormal cycle quality.

The exclusion list is auditable: the pipeline writes
`outputs/data_exclusions.csv` with each excluded shift-date and its cycle
count.

### Cause-split allocation

For each group, total bags is the integer above (`round(Σ loss_seconds / SOP)`).
Per-cause bags are allocated using the **largest-remainder method**:

1. Compute each cause's exact share = `(cause_loss_seconds / total_loss_seconds) × total_bags`
2. Take `floor()` of each cause's exact share → "floor bags"
3. Distribute the remainder (`total_bags − Σ floor_bags`) to causes with the
   largest fractional parts

This guarantees `Σ cause_bags == total_bags` exactly — without it, three
causes rounded independently could sum to one more or one less than the
headline number, leaving the customer confused.

---

## 4. Trend signal

### Per-week OLS slope

The **headline trend** is the OLS slope of the per-week loss rate across all
weeks of data. Each week's rate is one point on a chart whose x-axis is the
week index and whose y-axis is `loss_rate` in bags per hour. The slope's
sign and magnitude is what the narrative reports.

**Trend direction** (with a flat threshold of `|slope| < 0.10 bags/h/wk`):

- `slope < −0.10` → **improving**
- `slope >  0.10` → **worsening**
- `|slope| ≤ 0.10` → **flat**

The flat threshold prevents the dashboard's KPI widget from flickering
green/red between weeks when the rate is essentially stable.

### What NOT to use as the headline trend

The following are **forbidden** as headline trend statements:

- *Period-aggregate comparisons* such as "January-February versus April",
  "first half versus second half", "month over month".
  These depend on where the boundaries are drawn — with unequal-length
  periods the comparison is sensitive to which weeks fall on which side.
  Use the OLS slope instead, which doesn't depend on bucketing choices.

Period-mean comparisons may be mentioned as a secondary sanity check but
never as the primary headline.

---

## 5. Latest complete week

The KPI widget at the top of the dashboard shows the **latest complete
week**, defined as the most recent week whose week_end (Sunday) is on or
before today. If a partial week is present in the data (week_end is in the
future), it is rendered visibly in the weekly charts but does **not** drive
the headline KPI widget. A partial week's rate can swing wildly because its
denominator (productive hours so far) is incomplete.

When the narrative quotes a single-week value, it should use the latest
complete week — not the partial week.

---

## 6. Narrative rules

### Always

- **Every number includes its unit**. Always. Even when the unit feels
  obvious. The reader includes production operators, not just analysts — no
  inference is required.
- **No comma separators in numerals**. Write `8000` not `8,000`. Write
  `1234.5` not `1,234.5`. This is a brand convention.
- **Lead with what's improving**; end with the single highest-leverage
  opportunity. Operators read reports more carefully when the report opens
  with progress, not deficit.
- **Slope-only framing for trends.** Use OLS slope phrasing
  (e.g., *"improving at -0.23 bags/h/wk"*), not period aggregates.
- **Plain operator-friendly language.** Avoid statistical jargon when a
  shorter sentence works. The audience may not be an analyst.
- **Refer to the customer and line by the names in the config**; never
  invent.

### Calibrate claim strength to data adequacy

Before drawing trend conclusions, read:

- `productive_hours` per week (`weekly_time_on_product.csv`)
- The total number of weeks available
- Which shifts contributed each week (`weekly_opportunity_by_shift.csv`)

If the data is thin — a week with anomalously low productive hours relative
to the dataset typical, a shift absent from recent weeks, only one or two
weeks of data total — **describe what's there without anchoring strong
claims on noisy signals.** For a sparse week (low productive hours), the
rate value is still valid (it's hours-normalised) — the noisiness comes from
the smaller sample. Mention the sparseness in the relevant bullet, do not
treat the value as the new baseline.

### Cross-week continuity

The agent receives last week's status (`status/<report_id>.json` from the
prior run) including last week's `main_conclusions` and `top_3_actions`.
Use this for genuine continuity — *"last week we flagged the 2nd shift
slope reversal; this week..."* — but follow this ordering:

1. **First, analyse this week's data and form your own insights** from the
   new CSVs and this methodology.
2. **Then, check whether last week's observations are still valid given the
   new data.** Only reference prior observations where the new data still
   supports them.

Treat prior context as a **reference layer**, not a template. Do not reuse
last week's bullet titles or phrasing as a starting point; write fresh and
reference where appropriate.

### Forbidden patterns

The verifier MUST flag any of these:

- A number that doesn't trace to a value in the CSVs (fabrication)
- A number written without its unit
- A numeral with a comma separator
- A period-aggregate trend comparison (see §4)
- Judgement words like *"disappointing"*, *"bad"*, *"failure"* — use
  neutral, descriptive language (*"worsened by"*, *"increased to"*,
  *"opportunity to address"*)
- A customer or line name not in the report config
- A claim that disregards a clear data-adequacy caveat (e.g., asserting
  a "new baseline" from a single sparse week)

---

## 7. Output contract

The Insights agent's output is a single file:
`outputs/narrative_blocks.json` conforming to v1.0 of
`architecture/schemas/narrative_blocks.schema.json`. Required blocks:

- `schema_version`: const `"1.0"`
- `main_conclusions`: `{ headline, bullets[] }` — headline (10-120 chars,
  no numerals); 4-6 bullets, each `{ title, body }` only (no `tone` field).
- `top_3_actions`: exactly 3 items, each `{ title, body }`.
- `drivers_insight`: single `{ title, body }` — one diagnostic insight
  about what drives week-to-week variance in the loss rate.
- `patterns`: three intro paragraphs — `by_shift_intro`, `by_weekday_intro`,
  `per_shift_trend_intro`.

The bullet `body` should weave any data-adequacy caveats into the prose
where relevant — there is no separate "caveats" field.

---

## 8. Inputs the agent reads

The Insights agent reads, in this order of precedence:

1. **This methodology document** — the rules.
2. **The report config** (`config/<report_id>.yaml`) — customer name, line
   name, SOP value, plus any constants.
3. **The pipeline outputs** in the output directory:
   - `weekly_production_opportunity.csv` — the headline weekly aggregate +
     cause split
   - `weekly_opportunity_by_shift.csv` — per-week-per-shift breakdown
   - `weekly_time_on_product.csv` — productive hours per week
   - `shift_summary.csv` — per-shift across all weeks
   - `weekday_summary.csv` — per-weekday across all weeks
   - `data_exclusions.csv` — shift-dates excluded by the 20-cycle threshold
4. **Last week's status JSON** (`status/<report_id>.json` from the prior
   run) — headline + trend + last week's `main_conclusions` and
   `top_3_actions` for continuity.

---

## 9. Verifier scope

The Verifier reads the same inputs plus the just-generated
`narrative_blocks.json`, and checks every claim in two ways:

- **Data sync** — every numeric claim in the narrative traces to a value in
  the CSVs.
- **Methodology compliance** — every rule in this document is respected
  (units, slope-only framing, no period-aggregates, no comma separators,
  no judgement words, etc.).

Each warning has a `severity`:

- `hard` — a claim or number that doesn't trace to the data (fabrication).
  Pipeline halts. The reviewer must either fix the root cause and re-run,
  or use the `--override <warning_id> "<justification>"` CLI flag to
  publish anyway.
- `fixable` — a methodology compliance issue the Insights agent can correct
  by rewriting the offending block (missing unit, forbidden framing, etc.).
  The orchestrator runs an auto-fix loop: hands the warning back to the
  Insights agent, gets a re-emitted block, re-verifies. Up to 2 retries
  before escalating to `hard`.

There is no "soft warning" tier. The dashboard either ships clean
(everything passed or was auto-fixed) or halts.

---

## 10. What this document deliberately does NOT cover

- **What the customer should DO with the report** — that's the customer's
  improvement program, not the agent's territory. The agent surfaces
  opportunities (in `top_3_actions`); the customer decides what to act on.
- **Coverage of missing topics** — if the narrative fails to mention
  something the data showed, the current verifier does not flag it. That
  check is deferred to a future iteration.
- **Inter-customer comparisons** — each report is per-customer per-line.
  Cross-customer benchmarking is not in scope.
