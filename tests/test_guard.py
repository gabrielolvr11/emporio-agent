"""T-guard: amount extraction and the numeric fidelity check, plus its use in the agent."""

import json
from decimal import Decimal

import pytest
from pydantic_ai.messages import ModelResponse, RetryFeedbackPart, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from emporio_agent import guard
from emporio_agent.agent import build_agent, context_block, new_session, run_turn
from emporio_agent.data import load_store
from emporio_agent.policies import load_policies

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Custa R$ 2.199,00.", "2199.00"),
        ("10x de R$ 219,90 sem juros", "219.90"),
        ("até R$1000", "1000"),
        ("R$ 2.199", "2199"),
        ("R$1234.56", "1234.56"),
        ("parcelas de 219,90", "219.90"),
        ("R$ 12.000,00, à vista", "12000.00"),
    ],
)
def test_amount_formats(text, expected):
    assert list(guard.amounts_in(text)) == [Decimal(expected)]


def test_ignores_dates_percentages_and_measures():
    assert guard.amounts_in("Reabre 03/11 às 09:00, 20% off, escala 650mm, nota 4,5") == {}


def test_amount_from_a_tool_is_allowed_in_another_format():
    tool = json.dumps({"price": "R$ 2.199,00", "pix": "R$ 2.089,05"}, ensure_ascii=False)
    assert guard.unsupported_amounts("Custa R$ 2.199 ou R$2089.05 no PIX", [tool]) == []


def test_invented_amount_is_caught():
    tool = '{"price": "R$ 2.199,00"}'
    reply = "Custa R$ 2.199,00 e você economiza R$ 109,95 no PIX"
    assert guard.unsupported_amounts(reply, [tool]) == ["R$ 109,95"]


def test_amount_from_the_customer_is_allowed():
    assert guard.unsupported_amounts("Até R$ 1.000 temos estas opções", ["até R$1000?"]) == []


# --- In the agent ---------------------------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    return new_session(
        load_store(), load_policies(), "2026-11-03T10:00", tmp_path / "t.jsonl", None
    )


def scripted(replies):
    """Calls get_product(95), then answers with each reply in turn (one per model call)."""
    queue = list(replies)

    def model(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("get_product", {"product_id": 95})])
        return ModelResponse(parts=[TextPart(queue.pop(0))])

    return FunctionModel(model)


async def test_supported_reply_passes(session):
    turn = await run_turn(build_agent(scripted(["Custa R$ 2.199,00."])), session, "Preço?")
    assert turn.reply == "Custa R$ 2.199,00." and turn.guard == "ok"


async def test_invented_amount_gets_one_retry(session):
    agent = build_agent(scripted(["Custa R$ 1.999,00.", "Custa R$ 2.199,00."]))
    turn = await run_turn(agent, session, "Preço?")
    assert turn.reply == "Custa R$ 2.199,00."
    assert turn.guard == "retried"
    retries = [
        part
        for message in session.history
        for part in message.parts
        if isinstance(part, RetryFeedbackPart)
    ]
    assert "R$ 1.999,00" in retries[0].content


async def test_second_failure_is_blocked_with_a_ticket(session):
    agent = build_agent(scripted(["Custa R$ 1.999,00.", "Custa R$ 1.999,00, prometo."]))
    turn = await run_turn(agent, session, "Preço?")
    assert turn.guard == "blocked"
    assert "R$ 1.999" not in turn.reply
    assert "EMP-20261103-0001" in turn.reply
    assert session.deps.state.tickets == ["EMP-20261103-0001"]
    ticket = json.loads(session.deps.tickets_file.read_text(encoding="utf-8"))
    assert ticket["kind"] == "unanswered_question" and "Preço?" in ticket["summary"]
    # The model's own reply stays in the append-only history, so the next turn is told.
    block = context_block(session.deps.clock(), session.deps)
    assert "Sua última resposta foi substituída" in block
