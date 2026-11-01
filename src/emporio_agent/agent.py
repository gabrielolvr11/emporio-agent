"""Tom: the PydanticAI agent, the per-turn context block and the conversation session.

The system prompt and the tool list never change during a conversation, and the history only
grows: Claude Haiku 5.5 binds its thinking blocks to the exact conversation prefix. The current
date and time therefore travel in each user turn, never in the system prompt.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import RaiseContentFilterError
from pydantic_ai.exceptions import ContentFilterError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings

from emporio_agent import guard, rules, tickets
from emporio_agent.config import RUNTIME_DIR
from emporio_agent.data import StoreData
from emporio_agent.formatting import WEEKDAYS, day_pt, when_pt
from emporio_agent.policies import Topic
from emporio_agent.tools import TOOLS, Deps, SessionState

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_prompt.md").read_text(encoding="utf-8")

ERROR_REPLY = (
    "Desculpe, tive um problema técnico e não consegui responder agora. Pode tentar de novo em "
    "instantes? Se preferir, posso te encaminhar para alguém da equipe."
)
REFUSAL_REPLY = (
    "Desculpe, não consigo ajudar com isso por aqui. Posso te ajudar com instrumentos, pedidos "
    "e políticas da loja, ou te encaminhar para alguém da equipe."
)
GUARD_REPLY = (
    "Quero te passar a informação certinha, então vou confirmar esse valor com a equipe antes. "
    "Registrei o chamado {protocol}, e a equipe responde {response_time}. "
    "Posso ajudar com mais alguma coisa?"
)

logger = logging.getLogger(__name__)


def build_agent(model: str | Model) -> Agent[Deps, str]:
    agent = Agent(
        model,
        deps_type=Deps,
        output_type=str,
        instructions=SYSTEM_PROMPT,
        tools=TOOLS,
        model_settings=AnthropicModelSettings(anthropic_effort="medium"),
        capabilities=[RaiseContentFilterError()],
        retries=2,
    )
    agent.output_validator(check_amounts)
    return agent


async def check_amounts(ctx: RunContext[Deps], output: str) -> str:
    """Numeric fidelity guard: one retry with a correction, then a safe reply and a ticket."""
    unsupported = guard.unsupported_amounts(output, _amount_sources(ctx.messages))
    if not unsupported:
        return output
    state = ctx.deps.state
    listed = ", ".join(unsupported)
    if ctx.retry == 0:
        state.guard_outcome = "retried"
        raise ModelRetry(
            f"Estes valores não vieram de nenhuma ferramenta nem do cliente: {listed}. "
            "Reescreva a resposta usando apenas valores retornados pelas ferramentas, "
            "sem calcular nada, ou consulte a ferramenta que tem o valor."
        )
    state.guard_outcome = "blocked"
    now = ctx.deps.clock()
    ticket = tickets.create_ticket(
        ctx.deps.tickets_file,
        now,
        "unanswered_question",
        f"Resposta bloqueada pela trava de valores (não confirmados: {listed}). "
        f"Mensagem do cliente: {_customer_text(ctx.prompt)}",
        state.session_id,
        state.verified_customer_id,
    )
    state.tickets.append(ticket.protocol)
    reply = GUARD_REPLY.format(protocol=ticket.protocol, response_time=tickets.response_time(now))
    state.notice = (
        f"Sua última resposta foi substituída porque citava valores que nenhuma ferramenta "
        f'retornou ({listed}). O cliente recebeu: "{reply}"'
    )
    return reply


def _amount_sources(messages: list[ModelMessage]) -> list[str]:
    """Tool results and customer messages. Retry prompts are excluded: they list the rejected
    amounts."""
    sources = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                sources.append(part.model_response_str())
            elif isinstance(part, UserPromptPart):
                content = [part.content] if isinstance(part.content, str) else part.content
                sources += [item for item in content if isinstance(item, str)]
    return sources


def _customer_text(prompt: object) -> str:
    """The customer's message, without the context block."""
    if isinstance(prompt, str):
        return prompt
    texts = [item for item in prompt or [] if isinstance(item, str)]
    return texts[-1] if texts else ""


@dataclass
class Session:
    deps: Deps
    history: list[ModelMessage] = field(default_factory=list)
    """Append-only: earlier messages are never edited or removed."""
    turns_file: Path | None = RUNTIME_DIR / "turns.jsonl"
    """Per-turn metadata log (no message text); None disables it."""


def new_session(
    store: StoreData,
    policies: dict[Topic, str],
    agent_now: str | None = None,
    tickets_file: Path = RUNTIME_DIR / "tickets.jsonl",
    turns_file: Path | None = RUNTIME_DIR / "turns.jsonl",
) -> Session:
    deps = Deps(
        store=store,
        policies=policies,
        state=SessionState(session_id=uuid.uuid4().hex[:8]),
        clock=lambda: rules.current_time(agent_now),
        tickets_file=tickets_file,
    )
    return Session(deps, turns_file=turns_file)


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    result: Any

    @property
    def label(self) -> str:
        """What the tool did, in Portuguese, for the "Mostrar raciocínio" view."""
        args, result = self.args, self.result if isinstance(self.result, dict) else {}
        if self.name == "search_products":
            criteria = ", ".join(f"{key}={value}" for key, value in args.items())
            found = result.get("found", 0)
            return f"Consultou o catálogo ({criteria}): {found} resultado{'s' * (found != 1)}"
        if self.name == "get_product":
            return f"Consultou o produto {args.get('product_id')}"
        if self.name == "get_order_status":
            if result.get("verified"):
                return f"Consultou os pedidos de {result.get('customer_first_name')} (verificado)"
            if result.get("locked"):
                return "Verificação bloqueada após 3 tentativas"
            if "attempts_left" in result:
                return "Verificação do cliente falhou"
            return "Pediu dados de identificação"
        if self.name == "get_policy":
            return f"Consultou a política: {args.get('topic')}"
        if self.name == "open_ticket":
            return f"Abriu o chamado {result.get('protocol')} ({args.get('kind')})"
        return f"Usou {self.name}"


@dataclass(frozen=True)
class TurnResult:
    reply: str
    tool_calls: list[ToolCall]
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    guard: str = "ok"
    """Numeric fidelity guard: ok, retried (fixed on the second try) or blocked."""
    error: str | None = None


def context_block(now: datetime, deps: Deps) -> str:
    """What the code knows at this turn: time, store status, holidays, verification, tickets."""
    status = rules.store_status(now)
    lines = [
        "[Contexto do sistema, não é mensagem do cliente]",
        f"Agora: {day_pt(now.date())}, {now:%H:%M} (horário de Campo Grande).",
    ]
    if status.is_open:
        lines.append(f"Loja: ABERTA até as {status.closes_at:%H:%M}.")
    else:
        holiday = f" Hoje é feriado ({status.holiday})." if status.holiday else ""
        lines.append(f"Loja: FECHADA.{holiday} Reabre {when_pt(status.next_opening)}.")
    upcoming = [(d, name) for d, name in rules.upcoming_holidays(now.date()) if d != now.date()]
    if upcoming:
        listed = "; ".join(f"{WEEKDAYS[d.weekday()]}, {d:%d/%m} ({name})" for d, name in upcoming)
        lines.append(f"Feriados nos próximos dias (loja fechada): {listed}.")
    state = deps.state
    if state.verified_customer_id is not None:
        first_name = deps.store.customers[state.verified_customer_id].name.split()[0]
        lines.append(f"Cliente verificado nesta conversa: {first_name}.")
    else:
        lines.append("Cliente não verificado.")
    if state.tickets:
        lines.append(f"Chamados já abertos nesta conversa: {', '.join(state.tickets)}.")
    if state.notice:
        lines.append(f"Aviso: {state.notice}")
    return "\n".join(lines)


async def run_turn(agent: Agent[Deps, str], session: Session, text: str) -> TurnResult:
    """Answer one customer message. Never raises: failures become a safe reply."""
    deps = session.deps
    started = time.perf_counter()
    context = context_block(deps.clock(), deps)
    deps.state.notice = None  # delivered once
    deps.state.guard_outcome = "ok"
    try:
        result = await agent.run([context, text], message_history=session.history, deps=deps)
    except ContentFilterError:
        turn = TurnResult(REFUSAL_REPLY, [], time.perf_counter() - started, error="refusal")
    except Exception as exc:  # the conversation must survive any failed turn
        logger.exception("turn failed")
        error = f"{type(exc).__name__}: {exc}"
        turn = TurnResult(ERROR_REPLY, [], time.perf_counter() - started, error=error)
    else:
        session.history = result.all_messages()
        usage = result.usage
        turn = TurnResult(
            result.output,
            _tool_calls(result.new_messages()),
            time.perf_counter() - started,
            usage.input_tokens,
            usage.output_tokens,
            guard=deps.state.guard_outcome,
        )
    _log_turn(session, turn)
    return turn


def _tool_calls(messages: list[ModelMessage]) -> list[ToolCall]:
    calls: dict[str, ToolCall] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = ToolCall(part.tool_name, part.args_as_dict(), None)
            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                call = calls[part.tool_call_id]
                calls[part.tool_call_id] = ToolCall(call.name, call.args, part.content)
    return list(calls.values())


def _log_turn(session: Session, turn: TurnResult) -> None:
    if session.turns_file is None:
        return
    record = {
        "session_id": session.deps.state.session_id,
        "at": datetime.now(rules.TIMEZONE).isoformat(timespec="seconds"),
        "latency_s": round(turn.latency_s, 2),
        "input_tokens": turn.input_tokens,
        "output_tokens": turn.output_tokens,
        "tools": [call.name for call in turn.tool_calls],
        "guard": turn.guard,
        "error": turn.error,
    }
    session.turns_file.parent.mkdir(parents=True, exist_ok=True)
    with session.turns_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
