# Weekly automated multi-agent reports

[![CI](https://github.com/barbararomeira/weekly-automated-multi-agent-reports/actions/workflows/ci.yml/badge.svg)](https://github.com/barbararomeira/weekly-automated-multi-agent-reports/actions/workflows/ci.yml)

A multi-agent system that turns weekly manufacturing-line data into a customer-facing HTML report. The pipeline is mostly deterministic; two LLM-based agents handle the narrative writing and the methodology-compliance verification.

## What this is

Manufacturing customer-improvement teams spend roughly a day a week assembling a weekly performance report for one of their production lines. This system aims to compress that work to ≤30 minutes of human review:

- A deterministic pipeline computes the KPIs from raw shift and cycle data.
- A **self-audit** checks pipeline-correctness invariants (aggregate sums, schema, no NaN, no impossible values) before the agents run.
- An **Insights agent** (Claude Sonnet 4.6) turns the data into customer-facing prose, following a methodology document as ground truth.
- A **Verifier agent** (Claude Haiku 4.5) checks that every claim in the prose traces back to the data and respects the methodology. Fixable issues (missing units, framing tics) round-trip through an auto-fix loop with the Insights agent; hard issues halt the pipeline for reviewer attention.
- A deterministic splicer assembles the final HTML, with a small post-render check on the headline KPI widget value and trend colour.
- A **Fleet View** indexes all reports across all customers in one place, surfacing failures inline with a CLI override path for false positives.

The agent boundary is narrow on purpose: only the two LLM agents above. Data extraction, KPI computation, HTML rendering, and the index page are all deterministic Python — cheaper, faster, more reviewable.

## Quick start

Requires Python 3.11+.

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Run the full pipeline on the synthetic fixture
#    Mock mode bypasses the LLM agents (no ANTHROPIC_API_KEY needed)
python run_weekly.py --mock --as-of 2026-05-21
```

Produces:

| Artefact                              | What it is |
|---------------------------------------|------------|
| `outputs/*.csv`                       | 6 pipeline aggregate CSVs (weekly + per-shift + per-weekday + cause split + exclusions + productive hours) |
| `outputs/narrative_blocks.json`       | Insights agent's output (validated against schema v1.0) |
| `outputs/verifier_report.json`        | Verifier agent's output (validated against schema v1.0) |
| `outputs/weekly_kpi_dashboard.html`   | The customer-facing dashboard (4 Plotly charts + prose) |
| `status/demo.json`                    | Per-report status, consumed by the Fleet View + next week's Insights agent |
| `fleet_view.html`                     | Cross-report index page |

### Running with a real Anthropic API key

Drop `--mock` and set the API key. The orchestrator will call Sonnet 4.6 for the narrative and Haiku 4.5 for the verifier. Rough cost: ~$0.21 per weekly run (see [DECISIONS.md](./DECISIONS.md) entry 26).

```bash
export ANTHROPIC_API_KEY=...
python run_weekly.py --as-of 2026-05-21
```

### Operational scenarios

- **Override a verifier false positive:** `python run_weekly.py --override w1 "<justification>"`
- **Re-run a past week:** `python run_weekly.py --as-of 2026-05-13`
- **Full operator runbook:** [RUNBOOK.md](./RUNBOOK.md)

## Running the tests

```bash
pytest
```

39 tests covering shift classification edge cases, the largest-remainder cause-split allocation, OLS slope + trend classification, post-render check tampering detection, the audit's bug-catching, and an integration test that runs the orchestrator end-to-end in mock mode. CI runs them on every push (Python 3.11 + 3.12).

## Reading order

1. **[DECISIONS.md](./DECISIONS.md)** — design and methodology decisions with rationale (chose / considered / why). 27 entries spanning methodology (1-6, 14-15), agent contracts (7-10), scoping (11-15), architecture (16-26: verifier, self-audit, schemas, orchestrator, audit, model choice), and repo strategy (27).
2. **[architecture/overview.md](./architecture/overview.md)** — system shape, end-to-end data flow, where each step sits.
3. **[methodology/context.md](./methodology/context.md)** — the source of truth the agents read at runtime. Distils the relevant decisions into agent-readable rules.
4. **[architecture/agents/insights_agent.md](./architecture/agents/insights_agent.md)** — Insights agent contract: inputs, outputs, prompt skeleton, failure modes.
5. **[architecture/agents/verifier_agent.md](./architecture/agents/verifier_agent.md)** — Verifier agent contract, including the auto-fix loop and `--override` flow.
6. **[architecture/schemas/narrative_blocks.schema.json](./architecture/schemas/narrative_blocks.schema.json)** — JSON contract between the Insights agent and the HTML splicer.
7. **[architecture/schemas/verifier_report.schema.json](./architecture/schemas/verifier_report.schema.json)** — Verifier agent's structured output (warnings, severity, override block).
8. **[architecture/schemas/status.schema.json](./architecture/schemas/status.schema.json)** — per-report status JSON consumed by the Fleet View builder and by next week's Insights agent for cross-week continuity.
9. **[RUNBOOK.md](./RUNBOOK.md)** — operational scenarios (verifier halt, auto-fix loop exhausted, pipeline failure, new monthly data, past-week re-run).

## Status

**End-to-end working on synthetic data.** Design + implementation + tests are all committed and verified:

- 27 decisions captured in [DECISIONS.md](./DECISIONS.md) with the chose / considered / why for each.
- 6 deterministic pipeline modules + 2 LLM agents + 1 orchestrator + a Jinja2 dashboard template.
- 8 weeks of synthetic cycle-level data (4941 cycles) with deliberate sparse days to exercise the exclusion rule.
- A pytest suite covering the pure helpers, the bug-catching audit, the post-render check, and the full end-to-end orchestrator run.
- GitHub Actions CI on every push.

## Confidentiality

This is a portfolio project. No real customer data, customer names, employer-specific tooling, or internal endpoints appear in committed files. Real customer values live in untracked local configs (`config/<report_id>.yaml`, `inputs/`, `outputs/`, `status/` are all in `.gitignore`).
