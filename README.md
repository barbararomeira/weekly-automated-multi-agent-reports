# Weekly automated multi-agent reports

[![CI](https://github.com/barbararomeira/weekly-automated-multi-agent-reports/actions/workflows/ci.yml/badge.svg)](https://github.com/barbararomeira/weekly-automated-multi-agent-reports/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

> **A multi-agent system that turns a week of raw production-line data into a customer-ready performance report — in ~10 minutes of compute instead of a day of manual analysis.**

Most of the system is deterministic Python (KPI computation, HTML rendering, a cross-report index). Two LLM agents handle the parts where judgment helps: writing the narrative and verifying every claim against the underlying data + methodology before publication.

---

## The problem this solves

Manufacturing customer-improvement teams spend **roughly a day a week** assembling a weekly performance report for one production line:

- Pull cycle-level data from the customer's DB
- Compute loss rates, productive-hour denominators, per-shift breakdowns, weekly trends
- Filter shift-dates with too few cycles
- Write the narrative
- Sanity-check every number against the spreadsheets
- Render the dashboard

Goal: **compress that day to ≤30 minutes of human review**, freeing the operator to coach the customer on interpretation — the actual value-add.

## See a live example

[`examples/sample_fleet_view.html`](./examples/sample_fleet_view.html) and [`examples/sample_dashboard.html`](./examples/sample_dashboard.html) are pre-generated from the synthetic fixture in this repo, no API key required to view. They show what the system produces end-to-end.

## Architecture at a glance

```
  ⏰ Mondays
       │
       ▼
  ①  Extract raw cycle data from the production DB           (Python)
       │
  ②  KPI pipeline — loss rate / cause-split / trend           (Python)
       │
  ③  Self-audit — invariants, schema, no NaN, no impossibles  (Python, halts on broken data)
       │
  ④  Insights agent — narrative_blocks.json                   (Claude Sonnet 4.6)
       │       ↑
  ⑤  Verifier agent — checks every claim vs data + methodology (Claude Haiku 4.5)
       │       │
  ⑤b ←─ fix loop ─ for "fixable" warnings, re-emit narrative (max 2 retries)
       │
  ⑥  Splicer — narrative JSON + KPIs → HTML dashboard         (Python + Jinja2)
       │       └─ post-render check on headline widget value + trend colour
       │
  ⑦  Fleet View — cross-report index, surfaces failures inline  (Python)
       │
       ▼
  weekly_kpi_dashboard.html + status JSON + fleet_view.html
```

The agent boundary is narrow on purpose. Everything outside steps ④ and ⑤ is deterministic — cheaper, faster, more reviewable.

## Key engineering decisions

These are short distillations. Full *chose / considered / why* for all 27 decisions in [DECISIONS.md](./DECISIONS.md).

| # | Decision | Why this shape |
|---|---|---|
| 7 | **Agent emits structured JSON, not HTML.** Insights agent's output is a schema-validated `narrative_blocks.json`; deterministic code splices it into the dashboard template. | Agent stays in the language layer. Layout / KPI widgets / trend colours / chart binding stay in code. Easier to test the splicer in isolation, easier to swap models without breaking the dashboard. |
| 9 | **Verifier runs BEFORE HTML splicing.** The agent's narrative is gated by a second-agent check that compares every claim against the underlying CSVs and methodology before anything is rendered. | "Render then re-check" is too late — if a wrong number reaches a customer, the trust hit is irreversible. Verifying upstream catches hallucinations + methodology drift while there's still time to fix. |
| 18 | **Auto-fix loop for fixable warnings; cap at 2 retries.** Fixable verifier warnings (missing units, framing tics, off-by-one phrasing) round-trip to the Insights agent for targeted re-emit. Hard warnings halt and ping a reviewer with the CLI override path. | Doing N retries silently can mask real methodology problems behind a "looks-clean" pass. The cap forces human attention when the loop doesn't converge. The hard/fixable split keeps the auto-loop scoped to surface noise. |
| 20 | **Self-audit catches bugs, not judgment calls.** Step ③ verifies invariants only (aggregate sums match, schema OK, no NaN/impossible values). It does NOT pre-judge whether the data is "good enough" for narrative. | Two failure modes need separate handling. Broken data should halt cheap (before any LLM call). "Sparse week, can't really call a slope" is an interpretation question and belongs to the Insights agent + the methodology. Conflating them produces bad halts and bad narratives. |
| 24 | **Single Python orchestrator; chained imports, no subprocess fan-out.** Each step is also runnable standalone (`python -m scripts.<step>`) for debugging, but the weekly run is one process. | Subprocess + filesystem state between steps invites flakiness (race conditions on `outputs/`, lost stderr, partial writes). One process keeps the failure surface obvious and the orchestrator owns the retry policy. |
| 26 | **Sonnet for narrative, Haiku for verifier — ~$0.21 per weekly run.** Two models, two roles. The Insights agent is one expensive call (~9k tokens out); the Verifier is structured-output cheap-call. | Symmetric model choice ("use Sonnet for both") doubles the cost without gaining accuracy on the verifier's task — it's a JSON-comparison job, not a writing job. Asymmetry is the design that makes the budget work. |

## Quick start (no API key needed)

Requires Python 3.11+.

```bash
git clone https://github.com/barbararomeira/weekly-automated-multi-agent-reports
cd weekly-automated-multi-agent-reports
pip install -e ".[dev]"

# Run the full pipeline on synthetic data. --mock swaps in fixed agent outputs.
python run_weekly.py --mock --as-of 2026-05-21
```

You'll see in `outputs/`:

| Artefact | What |
|---|---|
| `weekly_kpi_dashboard.html` | The customer-facing dashboard (4 Plotly charts + narrative panel) |
| `narrative_blocks.json` | Insights agent output, validated against schema v1.0 |
| `verifier_report.json` | Verifier output, severity-tagged warnings |
| `*.csv` × 6 | Pipeline aggregates: weekly, per-shift, per-weekday, cause split, exclusions, productive hours |
| `status/demo.json` | Per-report status — consumed by the Fleet View + next week's Insights agent |
| `fleet_view.html` | Cross-report index, surfaces verifier failures inline |

### Running with real agents

Drop `--mock` and set an Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-...
python run_weekly.py --as-of 2026-05-21
```

Rough cost per run: **~$0.21** (Sonnet for the narrative, Haiku for the verifier). See [DECISIONS.md §26](./DECISIONS.md#26-model-choice-per-agent--rough-cost).

### Operational scenarios

```bash
# Override a verifier false positive (justification mandatory, recorded in the dashboard footer)
python run_weekly.py --override w1 "<short reason — what made this a false positive>"

# Re-run a past week
python run_weekly.py --as-of 2026-05-13
```

Full runbook covering "verifier halt", "auto-fix loop exhausted", "broken data audit", "new monthly data drop" → [RUNBOOK.md](./RUNBOOK.md).

## Tests

```bash
pytest
```

**39 tests** covering: shift-classification edge cases, largest-remainder cause-split allocation, OLS slope + trend classification, post-render-check tampering detection, the audit's bug-catching, and an end-to-end orchestrator integration test in mock mode. **CI runs on every push** (Python 3.11 + 3.12).

## Reading order — for the curious

1. **[DECISIONS.md](./DECISIONS.md)** — all 27 design + methodology decisions with rationale. The spine of the project.
2. **[architecture/overview.md](./architecture/overview.md)** — system shape + end-to-end data flow.
3. **[methodology/context.md](./methodology/context.md)** — the methodology spec the agents read at runtime. Decoupling rules from prompts lets us update one without touching the other.
4. **[architecture/agents/insights_agent.md](./architecture/agents/insights_agent.md)** + **[verifier_agent.md](./architecture/agents/verifier_agent.md)** — agent contracts: inputs, outputs, prompt skeleton, failure modes.
5. **[architecture/schemas/](./architecture/schemas/)** — JSON Schemas between agents and the rest of the system (narrative blocks, verifier report, status).
6. **[RUNBOOK.md](./RUNBOOK.md)** — operational scenarios.

## What's in the repo

| Path | Purpose |
|---|---|
| `run_weekly.py` | The orchestrator (entrypoint) |
| `scripts/` | KPI pipeline, audit, splicer, fleet-view builder, the two agents |
| `architecture/` | Schemas + per-agent contracts |
| `methodology/context.md` | Methodology spec, agent-readable |
| `fixtures/` | Synthetic cycle data + mock agent outputs (lets `--mock` run anywhere) |
| `examples/` | Pre-generated sample dashboard + fleet view |
| `tests/` | 39 pytest tests |
| `DECISIONS.md` | 27 chose-considered-why entries |
| `RUNBOOK.md` | Operational scenarios |

## Status

**End-to-end working on synthetic data.** Design, implementation, and tests are all committed and verified:

- 27 decisions captured with chose / considered / why
- 6 deterministic pipeline modules + 2 LLM agents + 1 orchestrator + a Jinja2 dashboard template
- 8 weeks of synthetic cycle data (4941 cycles), with deliberate sparse days to exercise the exclusion rule
- 39 tests covering pure helpers, the bug-catching audit, the post-render check, and a full end-to-end orchestrator run
- GitHub Actions CI on every push

## Confidentiality

No real customer data, customer names, employer-specific tooling, or internal endpoints appear in committed files. Real customer values live in untracked local configs (`config/<report_id>.yaml`, `inputs/`, `outputs/`, `status/` are all gitignored). This repo is the **portfolio-shareable** version of a multi-agent reporting design.

---

*If you're a recruiter or fellow engineer poking around: start with [DECISIONS.md](./DECISIONS.md). That's where the actual interesting trade-offs live.*
