# Contributing

Thanks for considering a contribution.

## Setup

```bash
git clone https://github.com/barbararomeira/weekly-automated-multi-agent-reports
cd weekly-automated-multi-agent-reports
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a PR

Run the test suite and the end-to-end smoke test:

```bash
pytest
python run_weekly.py --mock --as-of 2026-05-21
```

Both should pass cleanly. CI runs the same checks on Python 3.11 and 3.12 on every push.

## Code style

Ruff is configured in `pyproject.toml` (line length 100):

```bash
ruff check .
```

## What's helpful

- Bug reports with a small reproducible case.
- Documentation / clarity improvements — the README is the most-read file, tightening it counts.
- New methodology rules — PR to `methodology/context.md` with the rationale alongside.
- Test coverage for paths currently untested.

## What's likely out of scope

- Major rework of the agent architecture. That shape is documented in [DECISIONS.md](./DECISIONS.md) and changes there need a strong *"what's the new shape and why"* before code.
- Features only useful for one specific data source / customer / line — the goal is to keep the template generic.

## Filing issues

Use GitHub Issues. Please include: what you ran, what you expected, what happened, Python version, OS.
