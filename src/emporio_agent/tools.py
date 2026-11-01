"""The five tools Tom can call.

Thin wrappers over data and rules. Every result is explicit, including the negatives ("not
found", "category has no items", "data did not match"), so the model never fills a gap.
Amounts are pre-formatted (R$ 1.234,56) so the model copies them instead of computing them.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from emporio_agent import rules, tickets
from emporio_agent.config import RUNTIME_DIR
from emporio_agent.data import Order, Product, StoreData
from emporio_agent.formatting import brl
from emporio_agent.policies import Topic
from emporio_agent.tickets import TicketKind

MAX_RESULTS = 15
STOPWORDS = {"a", "o", "e", "de", "da", "do", "das", "dos", "com", "para", "um", "uma", "em"}

STATUS_LABELS = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
WINDOW_LABELS = {
    "regret": "devolução por arrependimento (compra online)",
    "defect_exchange": "troca por defeito",
    "preference_exchange": "troca por preferência (cor, modelo, tamanho)",
    "legal_warranty": "garantia legal",
}


@dataclass
class SessionState:
    """What the code knows about the conversation, independently of the model."""

    session_id: str
    verified_customer_id: int | None = None
    failed_verifications: int = 0
    tickets: list[str] = field(default_factory=list)
    guard_outcome: str = "ok"
    """Numeric fidelity guard result of the current turn: ok, retried or blocked."""
    notice: str | None = None
    """A message for the next context block, e.g. that the last reply was replaced."""


@dataclass
class Deps:
    store: StoreData
    policies: dict[Topic, str]
    state: SessionState
    clock: Callable[[], datetime]
    tickets_file: Path = RUNTIME_DIR / "tickets.jsonl"


# --- Catalog --------------------------------------------------------------------------------


def search_products(
    ctx: RunContext[Deps],
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    only_available: bool = True,
) -> dict[str, Any]:
    """Search the catalog. Results are sorted by price and include the final price.

    Args:
        query: Brand, model or feature words, e.g. "Takamine GD20", "Yamaha", "nylon", "7 cordas".
        category: Type of instrument, e.g. "violão", "guitarra", "baixo", "bateria", "teclado",
            "ukulele", "sopro", "violino".
        min_price: Minimum final price in BRL, after any promotion.
        max_price: Maximum final price in BRL, after any promotion.
        only_available: List only products for sale and in stock (default). Matching products
            that are not available are still named in `unavailable_matches`.
    """
    store = ctx.deps.store
    categories = store.categories
    if category:
        wanted = _stems(category)
        category_ids = {
            cid
            for cid, cat in categories.items()
            if wanted <= _stems(f"{cat.name} {cat.description}")
        }
        if not category_ids:
            return {
                "found": 0,
                "note": f"Categoria desconhecida: {category!r}.",
                "categories": [cat.name for cat in categories.values()],
            }
    else:
        category_ids = set(categories)

    candidates = [p for p in store.products.values() if p.category_id in category_ids]
    if query:
        candidates = _best_matches(candidates, query, store)
    priced = [(p, rules.price_of(p, store.promotions_of(p.product_id))) for p in candidates]
    priced = [
        (p, price)
        for p, price in priced
        if (min_price is None or price.final_price >= Decimal(str(min_price)))
        and (max_price is None or price.final_price <= Decimal(str(max_price)))
    ]
    priced.sort(key=lambda pair: (pair[1].final_price, pair[0].product_id))

    available = [(p, price) for p, price in priced if p.available or not only_available]
    unavailable = [p for p, _ in priced if not p.available] if only_available else []
    result: dict[str, Any] = {
        "found": len(available),
        "products": [_summary(p, price, store) for p, price in available[:MAX_RESULTS]],
    }
    if len(available) > MAX_RESULTS:
        result["note"] = (
            f"Mostrando os {MAX_RESULTS} de menor preço entre {len(available)}. "
            "Refine a busca para ver outros."
        )
    if unavailable:
        result["unavailable_matches"] = [
            {"product_id": p.product_id, "name": p.name, "availability": _availability(p)}
            for p in unavailable[:5]
        ]
    if not available:
        empty = [
            categories[cid].name
            for cid in sorted(category_ids)
            if category and not any(p.category_id == cid for p in store.products.values())
        ]
        if empty:
            result["note"] = (
                f"Sem produtos no catálogo online no momento: {', '.join(empty)}. Não diga que "
                "a loja não vende esses instrumentos; ofereça encaminhar para a equipe."
            )
        elif not unavailable:
            result["note"] = "Nenhum produto encontrado com esses critérios."
    return result


def get_product(ctx: RunContext[Deps], product_id: int) -> dict[str, Any]:
    """Details of one product: availability, specs, price, active promotion, PIX price,
    allowed installments, ended promotions and, when it is not available, alternatives.

    Args:
        product_id: The product_id returned by search_products.
    """
    store = ctx.deps.store
    product = store.products.get(product_id)
    if product is None:
        return {"error": f"Produto {product_id} não encontrado no catálogo."}

    result: dict[str, Any] = {
        "product_id": product.product_id,
        "name": product.name,
        "category": store.categories[product.category_id].name,
        "availability": _availability(product),
        "in_stock": product.stock_quantity if product.status == "active" else 0,
        "specs": product.specs,
    }
    if product.description_conflict:
        result["description"] = None  # contradicts the structured fields (data rule 1)
    else:
        result["description"] = product.description

    promotions = store.promotions_of(product_id)
    if product.status == "active":
        price = rules.price_of(product, promotions)
        result["price"] = _price_details(price)
    else:
        result["price"] = None
        result["price_note"] = "Não está à venda: não informe preço."
    ended = [p for p in promotions if not p.is_active]
    if ended:
        result["ended_promotions"] = [
            {"name": p.description, "percent": p.discount_percent, "status": "encerrada"}
            for p in ended
        ]
    if not product.available:
        result["alternatives"] = [
            _summary(alt, rules.price_of(alt, store.promotions_of(alt.product_id)), store)
            for alt in rules.alternatives(store, product)
        ]
    return result


# --- Orders ---------------------------------------------------------------------------------


def get_order_status(
    ctx: RunContext[Deps],
    order_id: int | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """The verified customer's orders: status, items, tracking, delivery, return windows.

    Verification needs at least two of the three identifiers, as given by the customer in this
    conversation. Once the customer is verified, call it again with no arguments (or only
    order_id) to see the same customer's orders.

    Args:
        order_id: Order number.
        email: E-mail used in the purchase.
        phone: Phone used in the purchase, any format.
    """
    deps = ctx.deps
    state = deps.state
    email = email.strip() if email and email.strip() else None
    phone = phone.strip() if phone and phone.strip() else None
    given = sum(value is not None for value in (order_id, email, phone))

    if given >= 2:
        if state.failed_verifications >= rules.MAX_VERIFICATION_ATTEMPTS:
            return _locked()
        customer = rules.verify_customer(deps.store, order_id, email, phone)
        if customer is None:
            state.failed_verifications += 1
            left = rules.MAX_VERIFICATION_ATTEMPTS - state.failed_verifications
            if left == 0:
                return _locked()
            return {
                "verified": False,
                "message": "Os dados informados não conferem. Não diga qual dado não confere.",
                "attempts_left": left,
            }
        state.verified_customer_id = customer.customer_id
    elif state.verified_customer_id is None:
        if state.failed_verifications >= rules.MAX_VERIFICATION_ATTEMPTS:
            return _locked()
        return {
            "verified": False,
            "message": "Peça ao cliente pelo menos dois destes dados: número do pedido, "
            "e-mail, telefone.",
        }

    customer = deps.store.customers[state.verified_customer_id]
    today = deps.clock().date()
    orders = sorted(
        (o for o in deps.store.orders.values() if o.customer_id == customer.customer_id),
        key=lambda o: o.order_date,
        reverse=True,
    )
    result: dict[str, Any] = {
        "verified": True,
        "customer_first_name": customer.name.split()[0],
        "orders": [_order_details(o, deps.store, today) for o in orders],
    }
    if order_id is not None and order_id not in {o.order_id for o in orders}:
        result["note"] = f"O pedido {order_id} não está entre os pedidos deste cliente."
    return result


# --- Policies and tickets -------------------------------------------------------------------


def get_policy(ctx: RunContext[Deps], topic: Topic) -> str:
    """The store manual's section on a topic.

    Args:
        topic: store_info (about the store, address, phone, WhatsApp, e-mail, what it sells),
            hours (opening hours), payment (payment methods, installments, PIX discount),
            returns (returns and exchanges), shipping (delivery, freight, tracking),
            promotions (promotion rules), warranty, privacy (personal data, LGPD).
    """
    return ctx.deps.policies[topic]


def open_ticket(ctx: RunContext[Deps], kind: TicketKind, summary: str) -> dict[str, Any]:
    """Register a request for the team and get a protocol number to give the customer.

    Args:
        kind: complaint, return_request (return or exchange), human_request (wants a person),
            unanswered_question (the tools and policies don't answer it), shipping_quote
            (delivery outside the Campo Grande area).
        summary: One or two sentences for the team, in Portuguese: what the customer needs,
            with the order number if known.
    """
    deps = ctx.deps
    now = deps.clock()
    ticket = tickets.create_ticket(
        deps.tickets_file,
        now,
        kind,
        summary,
        deps.state.session_id,
        deps.state.verified_customer_id,
    )
    deps.state.tickets.append(ticket.protocol)
    return {"protocol": ticket.protocol, "response_time": tickets.response_time(now)}


# --- Helpers --------------------------------------------------------------------------------


def _stem(word: str) -> str:
    """Accent-free, lower-case, singular enough to match: violões -> violao, metais -> metal."""
    word = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower()
    for suffix, replacement in (("oes", "ao"), ("aes", "ao"), ("ais", "al")):
        if word.endswith(suffix):
            return word[: -len(suffix)] + replacement
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"\w+", text)} - STOPWORDS


def _best_matches(products: list[Product], query: str, store: StoreData) -> list[Product]:
    """Products matching the most query words in name, category and specs."""
    wanted = _stems(query)
    scored = []
    for product in products:
        text = " ".join(
            [product.name, store.categories[product.category_id].name, *product.specs.values()]
        )
        score = len(wanted & _stems(text))
        if score:
            scored.append((score, product))
    best = max((score for score, _ in scored), default=0)
    return [product for score, product in scored if score == best]


def _availability(product: Product) -> str:
    if product.status == "discontinued":
        return "descontinuado: não faz mais parte do catálogo"
    if product.status == "coming_soon":
        return "em breve: ainda não está à venda"
    if product.stock_quantity == 0:
        return "sem estoque no momento"
    return "disponível"


def _summary(product: Product, price: rules.Price, store: StoreData) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "product_id": product.product_id,
        "name": product.name,
        "category": store.categories[product.category_id].name,
        "availability": _availability(product),
    }
    if product.status == "active":
        summary["price"] = brl(price.final_price)
        if price.promotion:
            summary["promotion"] = (
                f"{price.promotion.discount_percent}% de desconto, de {brl(price.list_price)} "
                f"por {brl(price.final_price)}"
            )
    return summary


def _price_details(price: rules.Price) -> dict[str, Any]:
    details: dict[str, Any] = {"list_price": brl(price.list_price)}
    if price.promotion:
        details["active_promotion"] = {
            "name": price.promotion.description,
            "percent": price.promotion.discount_percent,
            "price": brl(price.final_price),
        }
        details["pix"] = f"{brl(price.final_price)}: no PIX não há desconto sobre promoção"
    else:
        details["active_promotion"] = None
        details["pix"] = f"{brl(price.pix_price)} à vista no PIX (5% de desconto)"
    details["credit_card_no_interest"] = {
        f"{option.count}x": f"{option.count}x de {brl(option.amount)}"
        for option in price.installments
    }
    details["max_installments"] = price.installments[-1].count
    return details


def _payment_label(method: str) -> str:
    if credit := re.fullmatch(r"credit_(\d+)x", method):
        return f"cartão de crédito em {credit[1]}x"
    return {"pix": "PIX", "boleto": "boleto bancário", "debit": "cartão de débito"}[method]


def _order_details(order: Order, store: StoreData, today: date) -> dict[str, Any]:
    details: dict[str, Any] = {
        "order_id": order.order_id,
        "date": f"{order.order_date:%d/%m/%Y}",
        "status": STATUS_LABELS[order.status],
        "total": brl(order.total_brl),
        "payment": _payment_label(order.payment_method),
        "items": [  # the category grounds the instrument type ("Soprano" is a ukulele size)
            {
                "name": store.products[item.product_id].name,
                "category": store.categories[store.products[item.product_id].category_id].name,
                "quantity": item.quantity,
            }
            for item in store.items_of(order.order_id)
        ],
        "tracking_code": order.tracking_code,
        "estimated_delivery": (
            f"{order.estimated_delivery:%d/%m/%Y}" if order.estimated_delivery else None
        ),
    }
    if order.notes:
        details["notes"] = order.notes
    if rules.is_overdue(order, today):
        details["late"] = "atrasado: informe o status registrado e ofereça abrir um chamado"
    windows = rules.return_windows(order, today)
    if windows:
        details["return_windows"] = [
            {
                "type": WINDOW_LABELS[w.kind],
                "until": f"{w.deadline:%d/%m/%Y}",
                "open": w.is_open,
            }
            for w in windows
        ]
    return details


def _locked() -> dict[str, Any]:
    return {
        "verified": False,
        "locked": True,
        "message": "Limite de tentativas de verificação atingido. Não tente de novo: "
        "ofereça encaminhar para um atendente (open_ticket human_request).",
    }


TOOLS = [search_products, get_product, get_order_status, get_policy, open_ticket]
