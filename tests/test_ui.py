"""The Gradio UI's rendering helpers and app construction, without launching a server."""

import gradio as gr
import pytest

from emporio_agent.agent import ToolCall, build_agent, new_session
from emporio_agent.data import load_store
from emporio_agent.policies import load_policies
from emporio_agent.ui import Conversation, build_app, status_panel, tool_message, visible


@pytest.fixture(scope="module")
def store():
    return load_store()


@pytest.fixture(scope="module")
def policies():
    return load_policies()


def conversation(store, policies, now="2026-10-31T19:40"):
    return Conversation(new_session(store, policies, now, turns_file=None))


def test_tool_message_is_a_collapsible_block():
    message = tool_message(ToolCall("get_product", {"product_id": 95}, {"name": "Takamine"}))
    assert message["metadata"]["title"] == "🎸 Consultou o produto 95"
    assert message["content"].startswith("`get_product(product_id=95)`")
    assert '"name": "Takamine"' in message["content"]


def test_policy_result_is_quoted():
    message = tool_message(ToolCall("get_policy", {"topic": "hours"}, "# Horário\nSábado"))
    assert "> # Horário\n> Sábado" in message["content"]


def test_reasoning_switch_hides_tool_blocks(store, policies):
    conv = conversation(store, policies)
    conv.messages = [
        {"role": "user", "content": "Preço?"},
        tool_message(ToolCall("get_product", {"product_id": 95}, {})),
        {"role": "assistant", "content": "R$ 2.199,00"},
    ]
    assert len(visible(conv, True)) == 3
    assert [m["content"] for m in visible(conv, False)] == ["Preço?", "R$ 2.199,00"]


def test_panel_before_a_holiday(store, policies):
    html = status_panel(conversation(store, policies), True, "anthropic:claude-haiku-5-5")
    assert "sábado, 31/10/2026, 19:40" in html and "simulada" in html
    assert "Fechada" in html and "Reabre terça, 03/11, às 09:00" in html
    assert "segunda, 02/11 (Finados)" in html
    assert "Ainda não verificado" in html and "Nenhum nesta conversa" in html


def test_panel_shows_customer_and_tickets(store, policies):
    conv = conversation(store, policies, "2026-11-03T10:00")
    conv.session.deps.state.verified_customer_id = 3
    conv.session.deps.state.tickets.append("EMP-20261103-0001")
    html = status_panel(conv, False, "anthropic:claude-haiku-5-5")
    assert "Aberta até as 18:00" in html and "simulada" not in html
    assert "Pedro" in html and "EMP-20261103-0001" in html


def test_app_builds(store, policies):
    app = build_app(build_agent("test"), store, policies, None, "anthropic:claude-haiku-5-5")
    assert isinstance(app, gr.Blocks)
