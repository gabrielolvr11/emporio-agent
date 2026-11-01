"""Run the LLM eval scenarios against a real model and write a report.

Usage: uv run python -m evals.run [--model anthropic:claude-sonnet-5-5] [--only E-01,E-04]

Writes evals/reports/<date>_<model>/report.md (pass rate, latency, tokens, cost, failed checks)
and one transcript per scenario in transcripts/. Exits with 1 if a P0 scenario fails.
"""

import argparse
import asyncio
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from emporio_agent import rules
from emporio_agent.agent import ToolCall, TurnResult, build_agent, new_session, run_turn
from emporio_agent.config import ConfigError, load_settings
from emporio_agent.data import StoreData, load_store
from emporio_agent.formatting import day_pt
from emporio_agent.policies import Topic, load_policies
from evals.scenarios import SCENARIOS, Scenario, Turn

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
# USD per million input / output tokens, prompts up to 100K tokens.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}
_CALL_SPEC = re.compile(r"^(\w+)(?:\((\w+)=(.+)\))?$")


@dataclass
class TurnRecord:
    turn: Turn
    result: TurnResult
    failures: list[str]


@dataclass
class ScenarioResult:
    scenario: Scenario
    turns: list[TurnRecord] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    """Scenario-level failures (must_call_in_conversation)."""

    @property
    def passed(self) -> bool:
        return not self.failures and not any(t.failures for t in self.turns)

    @property
    def all_failures(self) -> list[str]:
        per_turn = [f"turn {i}: {f}" for i, t in enumerate(self.turns, 1) for f in t.failures]
        return per_turn + self.failures


def call_matches(spec: str, call: ToolCall) -> bool:
    """Match 'get_product', 'get_product(product_id=95)' or alternatives 'a|b'."""
    for alternative in spec.split("|"):
        name, key, value = _CALL_SPEC.match(alternative).groups()
        if call.name == name and (key is None or str(call.args.get(key)) == value):
            return True
    return False


def check_turn(turn: Turn, result: TurnResult) -> list[str]:
    failures = []
    for spec in turn.must_call:
        if not any(call_matches(spec, call) for call in result.tool_calls):
            failures.append(f"did not call {spec}")
    for spec in turn.must_not_call:
        if any(call_matches(spec, call) for call in result.tool_calls):
            failures.append(f"called {spec}")
    for pattern in turn.must_include:
        if not re.search(pattern, result.reply, re.IGNORECASE):
            failures.append(f"reply lacks /{pattern}/")
    for pattern in turn.must_not_include:
        if match := re.search(pattern, result.reply, re.IGNORECASE):
            failures.append(f"reply has forbidden /{pattern}/: {match.group(0)!r}")
    if result.error:
        failures.append(f"error: {result.error}")
    if result.guard == "blocked":
        failures.append("the numeric fidelity guard blocked the reply")
    return failures


async def run_scenario(
    agent, scenario: Scenario, store: StoreData, policies: dict[Topic, str], out_dir: Path
) -> ScenarioResult:
    session = new_session(store, policies, scenario.now, out_dir / "tickets.jsonl", None)
    outcome = ScenarioResult(scenario)
    calls: list[ToolCall] = []
    for turn in scenario.turns:
        result = await run_turn(agent, session, turn.user)
        calls += result.tool_calls
        outcome.turns.append(TurnRecord(turn, result, check_turn(turn, result)))
    for spec in scenario.must_call_in_conversation:
        if not any(call_matches(spec, call) for call in calls):
            outcome.failures.append(f"conversation never called {spec}")
    replies = [record.result.reply for record in outcome.turns]
    for pattern in scenario.must_include_in_conversation:
        if not any(re.search(pattern, reply, re.IGNORECASE) for reply in replies):
            outcome.failures.append(f"no reply has /{pattern}/")
    return outcome


async def run_all(model: str, scenarios: list[Scenario], out_dir: Path, concurrency: int):
    agent = build_agent(model)
    store, policies = load_store(), load_policies()
    limit = asyncio.Semaphore(concurrency)

    async def one(scenario: Scenario) -> ScenarioResult:
        async with limit:
            print(f"  running {scenario.id} {scenario.title}", flush=True)
            return await run_scenario(agent, scenario, store, policies, out_dir)

    return await asyncio.gather(*(one(s) for s in scenarios))


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    prices = PRICES.get(model.split(":")[-1])
    if prices is None:
        return None
    return (tokens_in * prices[0] + tokens_out * prices[1]) / 1_000_000


def transcript(result: ScenarioResult, model: str) -> str:
    scenario = result.scenario
    now = rules.current_time(scenario.now)
    lines = [
        f"# {scenario.id}: {scenario.title}",
        "",
        f"Simulated time: {day_pt(now.date())}, {now:%H:%M} (Campo Grande) · Model: `{model}` · "
        f"Result: **{'PASS' if result.passed else 'FAIL'}**",
    ]
    for record in result.turns:
        lines += ["", f"**Cliente:** {record.turn.user}", ""]
        notes = [call.label for call in record.result.tool_calls]
        if record.result.guard != "ok":
            notes.append(f"trava de valores: {record.result.guard}")
        if notes:
            lines += [f"> _{' · '.join(notes)}_", ""]
        lines.append(f"**Tom:** {record.result.reply}")
        if record.failures:
            lines += ["", *(f"- ❌ {failure}" for failure in record.failures)]
    if result.failures:
        lines += ["", *(f"- ❌ {failure}" for failure in result.failures)]
    return "\n".join(lines) + "\n"


def report(results: list[ScenarioResult], model: str, started: datetime) -> str:
    turns = [t.result for r in results for t in r.turns]
    latencies = [t.latency_s for t in turns]
    tokens_in = sum(t.input_tokens for t in turns)
    tokens_out = sum(t.output_tokens for t in turns)
    cost = cost_usd(model, tokens_in, tokens_out)
    p0 = [r for r in results if r.scenario.p0]
    guard = {kind: sum(t.guard == kind for t in turns) for kind in ("retried", "blocked")}
    lines = [
        f"# Eval report: `{model}`",
        "",
        f"Run on {started:%Y-%m-%d %H:%M} (Campo Grande) with `uv run python -m evals.run`. "
        "Scenarios and checks: `evals/scenarios.py`; transcripts: `transcripts/`.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| P0 scenarios passed | {sum(r.passed for r in p0)}/{len(p0)} |",
        f"| All scenarios passed | {sum(r.passed for r in results)}/{len(results)} |",
        f"| Turns | {len(turns)} |",
        f"| Median / max latency per turn | {statistics.median(latencies):.1f} s / "
        f"{max(latencies):.1f} s |",
        f"| Tokens in / out | {tokens_in:,} / {tokens_out:,} |",
        f"| Cost | {'n/a' if cost is None else f'US$ {cost:.4f}'} |",
        f"| Guard: replies retried / blocked | {guard['retried']} / {guard['blocked']} |",
        "",
        "| ID | Scenario | P0 | Result | Turns | Latency (s) | Failed checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        latency = " / ".join(f"{t.result.latency_s:.1f}" for t in r.turns)
        failed = "<br>".join(r.all_failures).replace("|", "\\|") or "-"
        lines.append(
            f"| [{r.scenario.id}](transcripts/{r.scenario.id}.md) | {r.scenario.title} | "
            f"{'yes' if r.scenario.p0 else 'no'} | {'✅ pass' if r.passed else '❌ fail'} | "
            f"{len(r.turns)} | {latency} | {failed} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LLM eval scenarios.")
    parser.add_argument("--model", help="PydanticAI model string (default: MODEL or Haiku 5.5)")
    parser.add_argument("--only", help="comma-separated scenario ids, e.g. E-01,E-04")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    model = args.model or settings.model
    wanted = set(args.only.split(",")) if args.only else None
    scenarios = [s for s in SCENARIOS if wanted is None or s.id in wanted]

    started = datetime.now(rules.TIMEZONE)
    out_dir = REPORTS_DIR / f"{started:%Y-%m-%d_%H%M}_{model.split(':')[-1]}"
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    print(f"Running {len(scenarios)} scenarios on {model}")
    results = asyncio.run(run_all(model, scenarios, out_dir, args.concurrency))

    for result in results:
        path = out_dir / "transcripts" / f"{result.scenario.id}.md"
        path.write_text(transcript(result, model), encoding="utf-8", newline="\n")
    (out_dir / "report.md").write_text(
        report(results, model, started), encoding="utf-8", newline="\n"
    )
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.scenario.id} {'; '.join(result.all_failures)}")
    print(f"Report: {out_dir / 'report.md'}")
    return 0 if all(r.passed for r in results if r.scenario.p0) else 1


if __name__ == "__main__":
    sys.exit(main())
