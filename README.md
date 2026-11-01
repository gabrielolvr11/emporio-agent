# Empório da Música — Customer Service Agent

Prototype of a text-based customer service agent for *Empório da Música*, a fictional musical instrument store in Campo Grande/MS (Artefact AI Engineer technical challenge).

> Work in progress. The requirements and the development plan are in [docs/design/requirements-and-plan.md](docs/design/requirements-and-plan.md). Design decisions, known limitations and example conversations will be documented here.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then set ANTHROPIC_API_KEY
```

## Run

```bash
uv run python -m emporio_agent.cli                          # chat in the terminal
uv run python -m emporio_agent.cli --now 2026-10-31T19:40   # with a simulated date and time
uv run pytest                                               # tests, no API calls
uv run python -m evals.run                                  # LLM scenarios + report (cents)
```
