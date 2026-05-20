# Architecture overview

**Status: DRAFT v0**

This document is the entry point for understanding how the system is structured.
For *why* particular choices were made, see `DECISIONS.md`. For per-agent details,
see `architecture/agents/*.md`. For the data contract between the insights agent
and the rendering script, see `architecture/schemas/narrative_blocks.schema.json`.

## What this system does

Takes raw shift-and-cycle data for a manufacturing line, runs a deterministic
KPI pipeline, lets an LLM-based **Insights agent** write the customer-facing
narrative from the data, runs a **Verifier agent** to check that narrative against
the methodology and the data, and emits:

- a per-report dashboard (`weekly_kpi_dashboard.html`)
- a status JSON consumed by the cross-report Fleet View

## Weekly run — top to bottom

```
   ⏰ Monday cron
        │
        ▼
   ① EXTRACTOR              [script — POC: notebook trigger by hand]
        │   inputs/*.csv (monthly drops)
        ▼
   ② KPI PIPELINE           [scripts]
        │   outputs/*.csv (weekly + daily aggregates, shift / weekday tables)
        ▼
   ③ DATA-QUALITY CHECK     [script]
        │   fail-fast if anything is wrong
        ▼
   ④ INSIGHTS AGENT         [LLM]
        │   outputs/narrative_blocks.json
        ▼
   ⑤ VERIFIER AGENT         [LLM]
        │   outputs/verifier_report.json (severity-tagged warnings)
        ▼
   ⑥ HTML SPLICER           [script — deterministic]
        │   weekly_kpi_dashboard.html
        │   status/<report_id>.json
        ▼
   ⑦ FLEET VIEW BUILDER     [script]
            fleet_view.html
```

Of those seven steps, only ④ and ⑤ are LLM-based. Everything else is deterministic
Python — cheaper, faster, more reviewable.

## Why these are the only agents

The agent boundary is drawn around two jobs where natural-language reasoning beats
code:

- **Insights agent** — writing customer-facing prose that explains the data
  faithfully, with the right framing, in plain language. Deterministic templates
  can fill numbers in but can't decide which finding to lead with or how to phrase
  a balanced narrative.
- **Verifier agent** — checking whether the narrative respects the methodology
  document and whether every claimed number traces back to the data. The
  methodology is written in prose, so the natural compliance check is also in
  prose. Deterministic asserts handle the schema and the data shape; the agent
  handles the harder semantic checks.

Everything else (extracting, computing KPIs, building the HTML, building the
index) is deterministic because the rules are deterministic.

## Data contracts

- `outputs/*.csv` — the deterministic pipeline outputs, schema documented in the
  methodology.
- `outputs/narrative_blocks.json` — the Insights agent's structured output.
  Schema: `architecture/schemas/narrative_blocks.schema.json`.
- `outputs/verifier_report.json` — the Verifier agent's structured warnings.
  Schema: `architecture/schemas/verifier_report.schema.json` *(TBD)*.
- `status/<report_id>.json` — the per-report status consumed by the Fleet View.
  Schema: `architecture/schemas/status.schema.json` *(TBD)*.

## Open questions (top of the stack)

- Final shape of the `narrative_blocks.json` schema — see the schema file for the
  current strawman + open points.
- Where the agents are invoked from (orchestrator script structure, retry policy).
- Cost / model choice per agent (probably Sonnet for insights, Haiku for verifier,
  but unconfirmed).
- Failure-mode coverage in the verifier's prompt — specifically what "hard error"
  vs "soft warning" looks like in practice.
