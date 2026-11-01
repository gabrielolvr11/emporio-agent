"""T-agent: the turn flow with a scripted model (FunctionModel), without the API."""

import json

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from emporio_agent import rules
from emporio_agent.agent import (
    ERROR_REPLY,
    REFUSAL_REPLY,
    build_agent,
    context_block,
    new_session,
    run_turn,
)
from emporio_agent.data import load_store
from emporio_agent.policies import load_policies

pytestmark = pytest.mark.anyio  # async tests run with anyio's pytest plugin


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def store():
    return load_store()


@pytest.fixture(scope="module")
def policies():
    return load_policies()


@pytest.fixture
def session(store, policies, tmp_path):
    return new_session(
        store, policies, "2026-10-31T19:40", tmp_path / "tickets.jsonl", tmp_path / "turns.jsonl"
    )


def price_model(messages: list, info: AgentInfo) -> ModelResponse:
    """Calls get_product(95) and then answers with the price the tool returned."""
    returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart)]
    if returns:
        return ModelResponse(
            parts=[TextPart(f"Custa {returns[0].content['price']['list_price']}.")]
        )
    return ModelResponse(parts=[ToolCallPart("get_product", {"product_id": 95})])


async def test_turn_calls_the_tool_and_answers(session):
    turn = await run_turn(build_agent(FunctionModel(price_model)), session, "Quanto custa?")
    assert turn.reply == "Custa R$ 2.199,00."
    assert [call.name for call in turn.tool_calls] == ["get_product"]
    assert turn.tool_calls[0].args == {"product_id": 95}
    assert turn.tool_calls[0].result["price"]["pix"].startswith("R$ 2.089,05")
    assert turn.error is None


async def test_user_turn_carries_the_context_block(session):
    await run_turn(build_agent(FunctionModel(price_model)), session, "Quanto custa?")
    first_request = session.history[0]
    assert isinstance(first_request, ModelRequest)
    (prompt,) = [p for p in first_request.parts if isinstance(p, UserPromptPart)]
    context, text = prompt.content
    assert context.startswith("[Contexto do sistema")
    assert "segunda, 02/11 (Finados)" in context
    assert text == "Quanto custa?"


async def test_history_is_append_only(session):
    agent = build_agent(FunctionModel(price_model))
    await run_turn(agent, session, "Quanto custa?")
    before = list(session.history)
    await run_turn(agent, session, "E o PIX?")
    assert session.history[: len(before)] == before
    assert len(session.history) > len(before)


async def test_failed_turn_keeps_the_session(session):
    def broken(messages, info):
        raise RuntimeError("API down")

    turn = await run_turn(build_agent(FunctionModel(broken)), session, "Oi")
    assert turn.reply == ERROR_REPLY
    assert "API down" in turn.error
    assert session.history == []


async def test_refusal_becomes_a_safe_reply(session):
    def refuse(messages, info):
        return ModelResponse(
            parts=[TextPart("")],
            finish_reason="content_filter",
            provider_details={"refusal": "simulated"},
        )

    turn = await run_turn(build_agent(FunctionModel(refuse)), session, "Oi")
    assert turn.reply == REFUSAL_REPLY
    assert turn.error == "refusal"


async def test_turn_log_has_metadata_but_no_text(session):
    await run_turn(build_agent(FunctionModel(price_model)), session, "Quanto custa?")
    (line,) = session.turns_file.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["tools"] == ["get_product"]
    assert "Quanto custa" not in line


def test_context_block_on_a_holiday(session):
    block = context_block(rules.current_time("2026-11-02T10:00"), session.deps)
    assert "Loja: FECHADA. Hoje é feriado (Finados). Reabre terça, 03/11, às 09:00." in block


def test_context_block_shows_the_verified_customer(session):
    session.deps.state.verified_customer_id = 3
    session.deps.state.tickets.append("EMP-20261031-0001")
    block = context_block(rules.current_time("2026-11-03T10:00"), session.deps)
    assert "Loja: ABERTA até as 18:00." in block
    assert "Cliente verificado nesta conversa: Pedro." in block
    assert "EMP-20261031-0001" in block
