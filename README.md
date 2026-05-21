# Weekly automated multi-agent reports

A multi-agent system that turns weekly manufacturing-line data into a customer-facing HTML report. The pipeline is mostly deterministic; two LLM-based agents handle the narrative writing and the methodology-compliance verification.

## What this is

Manufacturing customer-improvement teams spend roughly a day a week assembling a weekly performance report for one of their production lines. This system aims to compress that work to ≤30 minutes of human review:

- A deterministic pipeline computes the KPIs from raw shift and cycle data.
- A **self-audit** checks pipeline-correctness invariants (aggregate sums, schema, no NaN, no impossible values) before the agents run.
- An **Insights agent** turns the data into customer-facing prose, following a methodology document as ground truth.
- A **Verifier agent** checks that every claim in the prose traces back to the data and respects the methodology. Fixable issues (missing units, framing tics) round-trip through an auto-fix loop with the Insights agent; hard issues halt the pipeline for reviewer attention.
- A deterministic splicer assembles the final HTML, with a small post-render check on the headline KPI widget value and trend colour.
- A **Fleet View** indexes all reports across all customers in one place, surfacing failures inline with a CLI override path for false positives.

The agent boundary is narrow on purpose: only the two LLM agents above. Data extraction, KPI computation, HTML rendering, and the index page are all deterministic Python — cheaper, faster, more reviewable.

## Reading order

1. **[DECISIONS.md](./DECISIONS.md)** — design and methodology decisions with rationale (chose / considered / why). 26 entries spanning methodology (1-6, 14-15), agent contracts (7-10), scoping (11-15), and architecture (16-26: verifier, self-audit, schemas, orchestrator, audit, model choice).
2. **[architecture/overview.md](./architecture/overview.md)** — system shape, end-to-end data flow, where each step sits.
3. **[architecture/agents/insights_agent.md](./architecture/agents/insights_agent.md)** — Insights agent contract: inputs, outputs, prompt skeleton, failure modes.
4. **[architecture/agents/verifier_agent.md](./architecture/agents/verifier_agent.md)** — Verifier agent contract, including the auto-fix loop and `--override` flow.
5. **[architecture/schemas/narrative_blocks.schema.json](./architecture/schemas/narrative_blocks.schema.json)** — JSON contract between the Insights agent and the HTML splicer.
6. **[architecture/schemas/verifier_report.schema.json](./architecture/schemas/verifier_report.schema.json)** — Verifier agent's structured output (warnings, severity, override block).
7. **[architecture/schemas/status.schema.json](./architecture/schemas/status.schema.json)** — per-report status JSON consumed by the Fleet View builder and by next week's Insights agent for cross-week continuity.
8. **[RUNBOOK.md](./RUNBOOK.md)** — operational scenarios (verifier halt, auto-fix loop exhausted, pipeline failure, new monthly data, past-week re-run).

## Status

**Design phase complete.** The architecture is locked end-to-end — methodology, agent contracts, schemas, orchestrator shape, retry + failure handling, audit trail, model choice. 26 decisions captured in [DECISIONS.md](./DECISIONS.md) with the chose / considered / why for each.

Next phase: implementation. Code that realises the design is not yet committed in this repo. Each phase is scoped intentionally and shipped one at a time.

## Installing (dev)

Requires Python 3.11+. From the repo root:

```bash
pip install -e ".[dev]"
```

This installs the runtime dependencies plus the dev extras (pytest, ruff). Using [uv](https://github.com/astral-sh/uv) instead:

```bash
uv pip install -e ".[dev]"
```

## Confidentiality

This is a portfolio project. No real customer data, customer names, employer-specific tooling, or internal endpoints appear in committed files. Real customer values live in untracked local configs.
