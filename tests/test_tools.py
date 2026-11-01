"""Tools and tickets (T-ticket), called directly with a fake context, without the API."""

import json
from types import SimpleNamespace

import pytest

from emporio_agent import rules
from emporio_agent.data import load_store
from emporio_agent.policies import load_policies
from emporio_agent.tools import (
    Deps,
    SessionState,
    get_order_status,
    get_policy,
    get_product,
    open_ticket,
    search_products,
)


@pytest.fixture(scope="module")
def store():
    return load_store()


@pytest.fixture(scope="module")
def policies():
    return load_policies()


def make_ctx(store, policies, tmp_path, now="2026-10-31T19:40"):
    deps = Deps(
        store=store,
        policies=policies,
        state=SessionState("test"),
        clock=lambda: rules.current_time(now),
        tickets_file=tmp_path / "tickets.jsonl",
    )
    return SimpleNamespace(deps=deps)


@pytest.fixture
def ctx(store, policies, tmp_path):
    return make_ctx(store, policies, tmp_path)


def ids(result):
    return [p["product_id"] for p in result["products"]]


# --- search_products ------------------------------------------------------------------------


def test_violoes_up_to_1000(ctx):
    result = search_products(ctx, category="violão", max_price=1000)
    assert result["found"] == 12
    assert 96 not in ids(result) and 113 not in ids(result)  # no stock / discontinued
    assert {p["product_id"] for p in result["unavailable_matches"]} == {96, 113}
    prices = [p["price"] for p in result["products"]]
    assert prices[0] == "R$ 429,90"  # sorted by price


def test_search_by_name(ctx):
    assert ids(search_products(ctx, query="Takamine GD20")) == [95]


def test_search_by_name_finds_unavailable_product(ctx):
    result = search_products(ctx, query="Giannini GF-3D")
    assert result["found"] == 0
    assert result["unavailable_matches"][0]["product_id"] == 96
    assert "note" not in result


def test_empty_category_is_not_a_no(ctx):
    result = search_products(ctx, category="saxofone")
    assert result["found"] == 0
    assert "Não diga que a loja não vende" in result["note"]


def test_unknown_category_lists_the_categories(ctx):
    result = search_products(ctx, category="sintetizador modular")
    assert "Violões" in result["categories"]


def test_price_filter_uses_the_promotional_price(ctx):
    result = search_products(ctx, category="ukulele", min_price=439, max_price=440)
    assert ids(result) == [121]  # R$ 549 list price, R$ 439,20 with 20% off
    assert result["products"][0]["promotion"] == "20% de desconto, de R$ 549,00 por R$ 439,20"


# --- get_product ----------------------------------------------------------------------------


def test_product_with_pix_and_installments(ctx):
    price = get_product(ctx, 95)["price"]
    assert price["list_price"] == "R$ 2.199,00"
    assert price["pix"].startswith("R$ 2.089,05")
    assert price["credit_card_no_interest"]["10x"] == "10x de R$ 219,90"


def test_product_with_promotion(ctx):
    price = get_product(ctx, 121)["price"]
    assert price["active_promotion"] == {
        "name": "Desconto Exclusivo",
        "percent": 20,
        "price": "R$ 439,20",
    }
    assert "não há desconto" in price["pix"]
    assert price["max_installments"] == 5


def test_discontinued_product_has_no_price_and_has_alternatives(ctx):
    result = get_product(ctx, 113)
    assert result["price"] is None
    assert [a["product_id"] for a in result["alternatives"]] == [84, 82, 81]
    assert "R$" not in json.dumps({k: v for k, v in result.items() if k != "alternatives"})


def test_out_of_stock_product_keeps_price_and_has_alternatives(ctx):
    result = get_product(ctx, 96)
    assert result["availability"] == "sem estoque no momento"
    assert result["price"]["list_price"] == "R$ 799,90"
    assert len(result["alternatives"]) == 3


def test_contradictory_description_is_hidden(ctx):
    assert get_product(ctx, 135)["description"] is None


def test_unknown_product(ctx):
    assert "não encontrado" in get_product(ctx, 999)["error"]


# --- get_order_status -----------------------------------------------------------------------


def test_asks_for_two_identifiers(ctx):
    result = get_order_status(ctx, order_id=1)
    assert result["verified"] is False and "dois" in result["message"]
    assert ctx.deps.state.failed_verifications == 0


def test_verifies_and_remembers_the_customer(ctx):
    result = get_order_status(ctx, order_id=1, email="pedro.oliveira@jmail.com")
    assert result["verified"] and result["customer_first_name"] == "Pedro"
    assert [o["order_id"] for o in result["orders"]] == [19, 1]  # newest first
    again = get_order_status(ctx)  # no identifiers needed any more
    assert again["verified"] and ctx.deps.state.verified_customer_id == 3


def test_someone_elses_order_is_not_shown(ctx):
    get_order_status(ctx, order_id=1, email="pedro.oliveira@jmail.com")
    result = get_order_status(ctx, order_id=5)
    assert 5 not in [o["order_id"] for o in result["orders"]]
    assert "não está entre os pedidos deste cliente" in result["note"]


def test_failed_verification_does_not_say_which_field(ctx):
    result = get_order_status(ctx, order_id=1, email="outra@pessoa.com")
    assert result == {
        "verified": False,
        "message": "Os dados informados não conferem. Não diga qual dado não confere.",
        "attempts_left": 2,
    }


def test_locks_after_three_failures(ctx):
    for _ in range(3):
        result = get_order_status(ctx, order_id=1, email="outra@pessoa.com")
    assert result["locked"]
    correct = get_order_status(ctx, order_id=1, email="pedro.oliveira@jmail.com")
    assert correct["locked"]  # even the right data is refused now


def test_late_order_is_flagged(ctx):
    result = get_order_status(ctx, order_id=8, phone="(67) 99654-7821")  # shipped, due 28/02
    assert "atrasado" in result["orders"][0]["late"]


def test_return_windows_follow_the_clock(store, policies, tmp_path):
    ctx = make_ctx(store, policies, tmp_path, now="2026-02-20T10:00")
    result = get_order_status(ctx, order_id=7, email="leticia.rocha@jmail.com")
    windows = {w["type"]: w for w in result["orders"][0]["return_windows"]}
    regret = windows["devolução por arrependimento (compra online)"]
    assert regret == {
        "type": "devolução por arrependimento (compra online)",
        "until": "24/02/2026",
        "open": True,
    }


# --- get_policy and open_ticket (T-ticket) --------------------------------------------------


def test_get_policy(ctx):
    assert "09:00 às 13:00" in get_policy(ctx, "hours")


def test_ticket_protocol_and_file(ctx):
    first = open_ticket(ctx, "complaint", "Cliente reclama do atraso do pedido 8.")
    second = open_ticket(ctx, "human_request", "Quer falar com um atendente.")
    assert first["protocol"] == "EMP-20261031-0001"
    assert second["protocol"] == "EMP-20261031-0002"
    assert ctx.deps.state.tickets == ["EMP-20261031-0001", "EMP-20261031-0002"]
    lines = ctx.deps.tickets_file.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "complaint"


def test_ticket_response_time_counts_from_reopening_when_closed(ctx):
    response = open_ticket(ctx, "complaint", "Teste.")["response_time"]
    assert response == (
        "em até 24 horas úteis, contadas a partir da reabertura (terça, 03/11, às 09:00)"
    )


def test_ticket_response_time_when_open(store, policies, tmp_path):
    ctx = make_ctx(store, policies, tmp_path, now="2026-11-03T10:00")
    assert open_ticket(ctx, "complaint", "Teste.")["response_time"] == "em até 24 horas úteis"
