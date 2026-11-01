"""The terminal chat, with a scripted model and scripted input."""

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from emporio_agent import cli
from emporio_agent.agent import build_agent, new_session
from emporio_agent.data import load_store
from emporio_agent.policies import load_policies

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def price_model(messages, info):
    returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart)]
    if returns:
        price = returns[0].content["price"]["list_price"]
        return ModelResponse(parts=[TextPart(f"O Takamine custa **{price}**.")])
    return ModelResponse(parts=[ToolCallPart("get_product", {"product_id": 95})])


async def test_chat_shows_tools_and_reply(capsys, tmp_path):
    store, policies = load_store(), load_policies()
    lines = iter(["Quanto custa o Takamine?", "/novo", "sair"])
    await cli.chat(
        build_agent(FunctionModel(price_model)),
        lambda: new_session(store, policies, "2026-11-03T10:00", tmp_path / "t.jsonl", None),
        simulated_time=True,
        read=lambda _: next(lines),
    )
    out = capsys.readouterr().out
    assert "terça, 03/11/2026, 10:00 (data simulada)" in out
    assert "· Consultou o produto 95" in out
    assert "Tom: O Takamine custa R$ 2.199,00." in out  # markdown bold removed
    assert "(nova conversa)" in out
    assert out.rstrip().endswith("Até a próxima!")


def test_rejects_an_invalid_date(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--now", "amanhã"])
    assert "--now is not an ISO 8601 date-time" in capsys.readouterr().err
