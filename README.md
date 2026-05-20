# Weekly automated multi-agent reports

A multi-agent system that turns weekly manufacturing-line data into a customer-facing HTML report. The pipeline is mostly deterministic; two LLM-based agents handle the narrative writing and the methodology-compliance verification.

## What this is

Manufacturing customer-improvement teams spend roughly a day a week assembling a weekly performance report for one of their production lines. This system aims to compress that work to ≤30 minutes of human review:

- A deterministic pipeline computes the KPIs from raw shift and cycle data.
- An **Insights agent** turns the data into customer-facing prose, following a methodology document as ground truth.
- A **Verifier agent** checks that every claim in the prose traces back to the data and respects the methodology.
- A deterministic splicer assembles the final HTML.
- A **Fleet View** indexes all reports across all customers in one place.

The agent boundary is narrow on purpose: only the two LLM agents above. Data extraction, KPI computation, HTML rendering, and the index page are all deterministic Python — cheaper, faster, more reviewable.

## Reading order

1. **[DECISIONS.md](./DECISIONS.md)** — design and methodology decisions with rationale (chose / considered / why).
2. **[architecture/overview.md](./architecture/overview.md)** — system shape, data flow, where the agents sit.
3. **[architecture/agents/insights_agent.md](./architecture/agents/insights_agent.md)** — Insights agent contract: inputs, outputs, prompt skeleton, failure modes.
4. **[architecture/agents/verifier_agent.md](./architecture/agents/verifier_agent.md)** — Verifier agent contract.
5. **[architecture/schemas/narrative_blocks.schema.json](./architecture/schemas/narrative_blocks.schema.json)** — the JSON data contract between the Insights agent and the deterministic HTML splicer.

## Status

Design phase. Architecture documented, agent contracts drafted, code not yet committed in this repo. Each phase is scoped intentionally and shipped one at a time.

## Confidentiality

This is a portfolio project. No real customer data, customer names, employer-specific tooling, or internal endpoints appear in committed files. Real customer values live in untracked local configs.
