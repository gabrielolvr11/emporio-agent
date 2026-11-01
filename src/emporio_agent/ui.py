"""Gradio chat with Tom in the store's visual identity.

Usage: uv run python -m emporio_agent.ui [--now 2026-11-03T10:00] [--model ...] [--port 7860]

Each browser tab has its own conversation. The "Mostrar raciocínio" switch shows what Tom
consulted (tools, policies, tickets, the fidelity guard) as collapsible blocks; a customer
would not see them, but they make the agent's decisions visible.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field, replace
from html import escape
from pathlib import Path
from typing import Any

import gradio as gr
from pydantic_ai import Agent

from emporio_agent import rules
from emporio_agent.agent import Session, ToolCall, build_agent, new_session, run_turn
from emporio_agent.config import ConfigError, load_settings
from emporio_agent.data import StoreData, load_store
from emporio_agent.formatting import WEEKDAYS, day_pt, when_pt
from emporio_agent.policies import Topic, load_policies
from emporio_agent.tools import Deps

ASSETS = Path(__file__).parent / "assets"
TOM_AVATAR = ASSETS / "tom.svg"
SUGGESTIONS = {
    "Violões até R$ 1.000": "Quais opções de violões disponíveis custando até R$1000?",
    "Endereço da loja": "Qual o endereço da loja?",
    "Preço do Takamine GD20": "Quanto custa o Takamine GD20?",
    "Me arrependi da compra": "Me arrependi da minha compra, posso devolver?",
    "Meu pedido": "Quero saber do meu pedido",
    "Produto sem estoque": "O violão Giannini GF-3D está disponível?",
}
TOOL_ICONS = {
    "search_products": "🔎",
    "get_product": "🎸",
    "get_order_status": "📦",
    "get_policy": "📘",
    "open_ticket": "🎫",
}
GUARD_NOTES = {
    "retried": "A resposta citava um valor que nenhuma ferramenta retornou e foi refeita.",
    "blocked": "A resposta citava valores sem fonte duas vezes: foi substituída e um chamado foi "
    "aberto.",
}
TYPING = {"role": "assistant", "content": "_Tom está digitando…_"}
WELCOME = (
    "<div class='emp-welcome'><strong>Oi! Eu sou o Tom 🎶</strong><br>"
    "Posso te ajudar com instrumentos, preços, pedidos, trocas e horários da loja.</div>"
)

NAVY = gr.themes.Color(
    c50="#eef2f6", c100="#d9e1ea", c200="#b4c3d4", c300="#8ea4bd", c400="#5f7a96",
    c500="#3d566e", c600="#2c3e50", c700="#233241", c800="#1b2733", c900="#141d26",
    c950="#0d141a", name="navy",
)  # fmt: skip
THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.amber,
    secondary_hue=NAVY,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Nunito Sans"), "system-ui", "sans-serif"],
)
CSS = """
:root { --emp-navy: #2c3e50; --emp-blue: #1a5276; --emp-orange: #f39c12; --emp-gray: #7f8c8d; }
.gradio-container { max-width: 1180px !important; margin: 0 auto !important; }
.emp-header { display: flex; align-items: center; gap: 16px; padding: 18px 24px;
  border-radius: 16px; color: #fff; border-bottom: 4px solid var(--emp-orange);
  background: linear-gradient(135deg, var(--emp-navy), var(--emp-blue)); }
.emp-header svg { width: 56px; height: 56px; flex: none; }
.emp-header h1 { margin: 0; font-size: 1.45rem; font-weight: 800; color: #fff; }
.emp-header h1 span { color: var(--emp-orange); }
.emp-header p { margin: 2px 0 0; color: #fff !important; opacity: .85; font-size: .95rem; }
.emp-welcome { text-align: center; color: var(--emp-navy); line-height: 1.6; }
#emp-chat { background: #fff; border: 1px solid #e3e8ee; border-radius: 16px; }
@media (max-width: 640px) {
  .emp-header { padding: 14px 16px; gap: 12px; }
  .emp-header svg { width: 42px; height: 42px; }
  .emp-header h1 { font-size: 1.1rem; }
  .emp-header p { font-size: .85rem; }
}
.emp-chips button { border: 1px solid #d9e1ea !important; color: var(--emp-navy) !important;
  background: #fff !important; font-weight: 600 !important; }
.emp-chips button:hover { border-color: var(--emp-orange) !important; }
.emp-panel { display: grid; gap: 12px; color: var(--emp-navy); }
.emp-card { background: #fff; border: 1px solid #e3e8ee; border-radius: 12px; padding: 12px 14px; }
.emp-card h4 { margin: 0 0 6px; font-size: .72rem; letter-spacing: .06em; text-transform: uppercase;
  color: var(--emp-gray); }
.emp-card p { margin: 0; font-size: .95rem; }
.emp-badge { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px;
  font-size: .72rem; font-weight: 700; background: #fdf0d9; color: #9a5d00; }
.emp-status { font-weight: 700; }
.emp-status.open { color: #1e8449; }
.emp-status.closed { color: #b03a2e; }
.emp-muted { color: var(--emp-gray); font-size: .85rem; margin-top: 4px !important; }
.emp-tickets { margin: 0; padding-left: 18px; font-family: ui-monospace, monospace;
  font-size: .85rem; }
.emp-footer { color: var(--emp-gray); font-size: .78rem; text-align: center; }
"""
# The store's identity is a light theme: open the page in Gradio's light mode even when the
# operating system prefers dark.
HEAD = """<script>
const url = new URL(window.location);
if (!url.searchParams.has("__theme")) {
  url.searchParams.set("__theme", "light");
  window.location.replace(url);
}
</script>"""


@dataclass
class Conversation:
    """One browser tab: the agent session and every message shown, tool blocks included."""

    session: Session
    messages: list[dict[str, Any]] = field(default_factory=list)


def visible(conversation: Conversation, show_reasoning: bool) -> list[dict[str, Any]]:
    return [m for m in conversation.messages if show_reasoning or "metadata" not in m]


def tool_message(call: ToolCall) -> dict[str, Any]:
    if isinstance(call.result, str):  # a policy section
        result = call.result if len(call.result) < 900 else call.result[:900] + "\n…"
        body = f"> {result.replace(chr(10), chr(10) + '> ')}"
    else:
        result = json.dumps(call.result, ensure_ascii=False, indent=2)
        body = f"```json\n{result if len(result) < 2500 else result[:2500] + chr(10) + '…'}\n```"
    args = ", ".join(f"{key}={value!r}" for key, value in call.args.items())
    return {
        "role": "assistant",
        "content": f"`{call.name}({args})`\n\n{body}",
        "metadata": {"title": f"{TOOL_ICONS.get(call.name, '⚙️')} {call.label}", "status": "done"},
    }


def status_panel(conversation: Conversation | None, simulated: bool, model: str) -> str:
    if conversation is None:
        return ""
    deps = conversation.session.deps
    now = deps.clock()
    status = rules.store_status(now)
    badge = "<span class='emp-badge'>simulada</span>" if simulated else ""
    if status.is_open:
        store = f"<p class='emp-status open'>● Aberta até as {status.closes_at:%H:%M}</p>"
    else:
        reason = f" · feriado: {escape(status.holiday)}" if status.holiday else ""
        store = (
            f"<p class='emp-status closed'>● Fechada{reason}</p>"
            f"<p class='emp-muted'>Reabre {when_pt(status.next_opening)}</p>"
        )
    upcoming = [(d, n) for d, n in rules.upcoming_holidays(now.date()) if d != now.date()]
    if upcoming:
        listed = "; ".join(f"{WEEKDAYS[d.weekday()]}, {d:%d/%m} ({escape(n)})" for d, n in upcoming)
        store += f"<p class='emp-muted'>Feriados próximos: {listed}</p>"
    state = deps.state
    if state.verified_customer_id is not None:
        first_name = deps.store.customers[state.verified_customer_id].name.split()[0]
        customer = f"<p>{escape(first_name)} <span class='emp-badge'>verificado</span></p>"
    else:
        customer = "<p class='emp-muted'>Ainda não verificado</p>"
    if state.tickets:
        tickets = "<ul class='emp-tickets'>" + "".join(f"<li>{t}</li>" for t in state.tickets)
        tickets += "</ul>"
    else:
        tickets = "<p class='emp-muted'>Nenhum nesta conversa</p>"
    return (
        "<div class='emp-panel'>"
        f"<div class='emp-card'><h4>Agora em Campo Grande</h4>"
        f"<p>{day_pt(now.date())}, {now:%H:%M}{badge}</p></div>"
        f"<div class='emp-card'><h4>Loja</h4>{store}</div>"
        f"<div class='emp-card'><h4>Cliente</h4>{customer}</div>"
        f"<div class='emp-card'><h4>Chamados</h4>{tickets}</div>"
        f"<p class='emp-footer'>Modelo: {escape(model.split(':')[-1])}</p>"
        "</div>"
    )


def build_app(
    agent: Agent[Deps, str],
    store: StoreData,
    policies: dict[Topic, str],
    agent_now: str | None,
    model: str,
) -> gr.Blocks:
    simulated = agent_now is not None

    def start():
        conversation = Conversation(new_session(store, policies, agent_now))
        return conversation, [], status_panel(conversation, simulated, model)

    def screen(conversation: Conversation, show_reasoning: bool, typing: bool = False):
        messages = visible(conversation, show_reasoning) + ([TYPING] if typing else [])
        return messages, "", status_panel(conversation, simulated, model), conversation

    async def respond(text: str, show_reasoning: bool, conversation: Conversation | None):
        if conversation is None:  # the page-load event did not run
            conversation = Conversation(new_session(store, policies, agent_now))
        text = text.strip()
        if not text:
            yield screen(conversation, show_reasoning)
            return
        conversation.messages.append({"role": "user", "content": text})
        yield screen(conversation, show_reasoning, typing=True)
        turn = await run_turn(agent, conversation.session, text)
        conversation.messages += [tool_message(call) for call in turn.tool_calls]
        if turn.guard in GUARD_NOTES:
            conversation.messages.append(
                {
                    "role": "assistant",
                    "content": GUARD_NOTES[turn.guard],
                    "metadata": {"title": "🛡️ Trava de valores", "status": "done"},
                }
            )
        conversation.messages.append({"role": "assistant", "content": turn.reply})
        yield screen(conversation, show_reasoning)

    with gr.Blocks(title="Empório da Música · Tom") as app:
        conversation = gr.State()
        gr.HTML(
            f"<div class='emp-header'>{TOM_AVATAR.read_text(encoding='utf-8')}<div>"
            "<h1>Empório da Música <span>—</span> Sua música começa aqui</h1>"
            "<p>Converse com o Tom, o assistente virtual da loja</p></div></div>"
        )
        with gr.Row(equal_height=False):
            with gr.Column(scale=3, min_width=340):
                chatbot = gr.Chatbot(
                    elem_id="emp-chat",
                    height=540,
                    avatar_images=(None, str(TOM_AVATAR)),
                    placeholder=WELCOME,
                    show_label=False,
                    layout="bubble",
                    buttons=["copy_all"],
                )
                with gr.Row(elem_classes="emp-chips"):
                    chips = [gr.Button(label, size="sm") for label in SUGGESTIONS]
                with gr.Row():
                    textbox = gr.Textbox(
                        placeholder="Escreva sua mensagem…",
                        show_label=False,
                        scale=5,
                        autofocus=True,
                        container=False,
                    )
                    send = gr.Button("Enviar", variant="primary", scale=1, min_width=90)
            with gr.Column(scale=1, min_width=260):
                show_reasoning = gr.Checkbox(
                    value=True,
                    label="Mostrar raciocínio",
                    info="Exibe as ferramentas e políticas consultadas pelo Tom.",
                )
                panel = gr.HTML()
                new_chat = gr.Button("Nova conversa", variant="secondary")

        inputs = [textbox, show_reasoning, conversation]
        outputs = [chatbot, textbox, panel, conversation]
        app.load(start, outputs=[conversation, chatbot, panel], queue=False)
        textbox.submit(respond, inputs, outputs)
        send.click(respond, inputs, outputs)
        for chip, prompt in zip(chips, SUGGESTIONS.values(), strict=True):
            chip.click(lambda prompt=prompt: prompt, outputs=textbox).then(respond, inputs, outputs)
        show_reasoning.change(visible, [conversation, show_reasoning], chatbot)
        new_chat.click(start, outputs=[conversation, chatbot, panel])
        gr.Timer(30).tick(
            lambda c: status_panel(c, simulated, model), inputs=conversation, outputs=panel
        )
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gradio chat with Tom.")
    parser.add_argument("--now", help="simulated date-time, e.g. 2026-11-03T10:00 (AGENT_NOW)")
    parser.add_argument("--model", help="PydanticAI model string (MODEL)")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args(argv)
    if args.now:
        try:
            rules.current_time(args.now)
        except ValueError:
            parser.error(f"--now is not an ISO 8601 date-time: {args.now!r}")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    settings = replace(
        settings, model=args.model or settings.model, agent_now=args.now or settings.agent_now
    )
    agent = build_agent(settings.model)
    app = build_app(agent, load_store(), load_policies(), settings.agent_now, settings.model)
    app.launch(
        server_port=args.port,
        theme=THEME,
        css=CSS,
        head=HEAD,
        allowed_paths=[str(ASSETS)],
        footer_links=[],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
