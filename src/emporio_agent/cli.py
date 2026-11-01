"""Chat with Tom in the terminal. The fallback for the live demo.

Usage: uv run python -m emporio_agent.cli [--now 2026-11-03T10:00] [--model ...] [--quiet]
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from dataclasses import replace

from pydantic_ai import Agent

from emporio_agent import rules
from emporio_agent.agent import Session, build_agent, new_session, run_turn
from emporio_agent.config import ConfigError, load_settings
from emporio_agent.data import load_store
from emporio_agent.formatting import day_pt
from emporio_agent.policies import load_policies
from emporio_agent.tools import Deps

EXIT_WORDS = {"sair", "exit", "quit", "/sair"}
NEW_WORDS = {"/novo", "/new"}
GUARD_NOTES = {
    "retried": "Trava de valores: a resposta citava um valor sem fonte e foi refeita",
    "blocked": "Trava de valores: resposta substituída e chamado aberto",
}


async def chat(
    agent: Agent[Deps, str],
    make_session: Callable[[], Session],
    simulated_time: bool = False,
    show_tools: bool = True,
    read: Callable[[str], str] = input,
) -> None:
    session = make_session()
    now = session.deps.clock()
    simulated = " (data simulada)" if simulated_time else ""
    print("Empório da Música · Tom, seu assistente virtual")
    print(f"{day_pt(now.date())}, {now:%H:%M}{simulated}")
    print("Digite /novo para recomeçar e sair para encerrar.")
    while True:
        try:
            text = read("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            break
        if text.lower() in NEW_WORDS:
            session = make_session()
            print("(nova conversa)")
            continue
        turn = await run_turn(agent, session, text)
        if show_tools:
            notes = [call.label for call in turn.tool_calls]
            if turn.guard in GUARD_NOTES:
                notes.append(GUARD_NOTES[turn.guard])
            for note in notes:
                print(f"  · {note}")
        print(f"\nTom: {turn.reply.replace('**', '')}")
    print("\nAté a próxima!")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat with Tom in the terminal.")
    parser.add_argument("--now", help="simulated date-time, e.g. 2026-11-03T10:00 (AGENT_NOW)")
    parser.add_argument("--model", help="PydanticAI model string (MODEL)")
    parser.add_argument("--quiet", action="store_true", help="hide the tools Tom used")
    args = parser.parse_args(argv)
    if args.now:
        try:
            rules.current_time(args.now)
        except ValueError:
            parser.error(f"--now is not an ISO 8601 date-time: {args.now!r}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    settings = replace(
        settings, model=args.model or settings.model, agent_now=args.now or settings.agent_now
    )
    store, policies = load_store(), load_policies()
    asyncio.run(
        chat(
            build_agent(settings.model),
            lambda: new_session(store, policies, settings.agent_now),
            simulated_time=settings.agent_now is not None,
            show_tools=not args.quiet,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
