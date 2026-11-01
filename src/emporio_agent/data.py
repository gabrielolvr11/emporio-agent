"""Store data: load the CSVs in data/raw into validated models and flag inconsistencies.

The loader fails fast on rows that break the schema or reference a missing record. Rows that
are valid but inconsistent are kept as they are and reported as `DataIssue`s
(see scripts/data_report.py); each issue kind has a handling rule the agent follows.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from emporio_agent import rules

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
FILE_PREFIX = "desafio_tecnico_ai_eng - "

# Brands sold in the catalog, plus Tama, which appears only in a description.
KNOWN_BRANDS = (
    "Crafter", "Fender", "Giannini", "Gibson", "Ibanez", "Kala", "Kalani", "Korg", "Martin",
    "Music Man", "Nord", "Ohana", "Pearl", "PRS", "Roland", "Rozini", "Shelby", "Tagima",
    "Takamine", "Tama", "Taylor", "Yamaha",
)  # fmt: skip


class DataLoadError(Exception):
    """A CSV row breaks the schema or references a record that does not exist."""


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _empty_to_none(cls, values: Any) -> Any:
        if isinstance(values, dict):
            return {key: (None if value == "" else value) for key, value in values.items()}
        return values


class Category(_Row):
    category_id: int
    name: str
    description: str


class Product(_Row):
    product_id: int
    name: str
    category_id: int
    price_brl: Decimal = Field(gt=0)
    stock_quantity: int = Field(ge=0)
    status: Literal["active", "discontinued", "coming_soon"]
    specs: dict[str, str]
    description: str
    created_at: date
    description_conflict: str | None = None
    """Why the description contradicts the structured fields, or None. Set by the loader."""

    @field_validator("specs", mode="before")
    @classmethod
    def _parse_specs(cls, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @property
    def available(self) -> bool:
        """For sale now: active and in stock. Other statuses are never for sale."""
        return self.status == "active" and self.stock_quantity > 0


class Promotion(_Row):
    promotion_id: int
    product_id: int
    discount_percent: int = Field(gt=0, lt=100)
    description: str
    is_active: bool


class Customer(_Row):
    customer_id: int
    name: str
    phone: str
    email: str
    city: str


class Order(_Row):
    order_id: int
    customer_id: int
    order_date: date
    status: Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]
    total_brl: Decimal = Field(gt=0)
    payment_method: str = Field(pattern=r"^(pix|boleto|debit|credit_\d{1,2}x)$")
    tracking_code: str | None = None
    estimated_delivery: date | None = None
    notes: str | None = None


class OrderItem(_Row):
    order_id: int
    product_id: int
    quantity: int = Field(gt=0)


@dataclass(frozen=True)
class StoreData:
    categories: dict[int, Category]
    products: dict[int, Product]
    promotions: list[Promotion]
    customers: dict[int, Customer]
    orders: dict[int, Order]
    order_items: list[OrderItem]

    def items_of(self, order_id: int) -> list[OrderItem]:
        return [item for item in self.order_items if item.order_id == order_id]

    def promotions_of(self, product_id: int) -> list[Promotion]:
        return [promo for promo in self.promotions if promo.product_id == product_id]


@dataclass(frozen=True)
class DataIssue:
    kind: str
    """A key of ISSUE_KINDS."""
    entity: str
    ids: tuple[int, ...]
    detail: str


# kind -> (title, how the agent handles it)
ISSUE_KINDS: dict[str, tuple[str, str]] = {
    "description_conflict": (
        "Description contradicts the product's structured fields",
        "Structured fields win (name, price, stock, status, specs). Tom never shows this "
        "description.",
    ),
    "empty_category": (
        "Category without products",
        "Tom says the online catalog has no items in it right now and offers a handoff. He never "
        'says "we don\'t sell that": the manual says the store sells these instruments.',
    ),
    "discontinued_in_stock": (
        "Discontinued product with stock",
        "Not for sale: the status wins over the stock. Tom suggests available equivalents.",
    ),
    "unreleased_product_sold": (
        "Coming-soon product in a shipped or delivered order",
        "Not for sale: the status wins. The order is shown as recorded.",
    ),
    "order_total_mismatch": (
        "Order total differs from its items at current prices",
        "Tom shows the recorded total. Prices may have changed since the purchase.",
    ),
    "promotion_history": (
        "Several promotion rows for one product, none with validity dates",
        "Only is_active = 1 counts. Ended promotions are mentioned only when the customer asks.",
    ),
    "shared_email": (
        "Customers sharing an e-mail",
        "An e-mail alone never identifies a customer: 2 of 3 identifiers must match "
        "(order number, e-mail, phone).",
    ),
    "shared_phone": (
        "Customers sharing a phone number",
        "A phone alone never identifies a customer: 2 of 3 identifiers must match.",
    ),
    "similar_names": (
        "Customers whose names overlap",
        "Tom never identifies a customer by name: 2 of 3 identifiers must match.",
    ),
    "overdue_order": (
        "Order still open long after it was placed, or past its estimated delivery",
        "Tom reports the recorded status and offers a ticket so the team can check the order.",
    ),
    "pix_without_discount": (
        "PIX order recorded at the list price, without the 5% PIX discount",
        "Tom shows the recorded total. New quotes apply the discount (rules.price_of).",
    ),
    "installment_below_minimum": (
        "Credit-card order with more installments than the manual's minimums allow",
        "Tom shows the recorded order. New quotes offer only the allowed installment counts "
        "(rules.installment_options).",
    ),
}


def load_store(raw_dir: Path = RAW_DIR) -> StoreData:
    """Load and validate the six CSV files. Raises DataLoadError on the first broken row."""
    categories = _index(_read(raw_dir, "categories", Category), "category_id")
    products = _index(_read(raw_dir, "products", Product), "product_id")
    promotions = _read(raw_dir, "promotions", Promotion)
    customers = _index(_read(raw_dir, "customers", Customer), "customer_id")
    orders = _index(_read(raw_dir, "orders", Order), "order_id")
    order_items = _read(raw_dir, "order_items", OrderItem)

    _check_references(
        "products.category_id", (p.category_id for p in products.values()), categories
    )
    _check_references("promotions.product_id", (p.product_id for p in promotions), products)
    _check_references("orders.customer_id", (o.customer_id for o in orders.values()), customers)
    _check_references("order_items.order_id", (i.order_id for i in order_items), orders)
    _check_references("order_items.product_id", (i.product_id for i in order_items), products)

    products = {
        pid: product.model_copy(update={"description_conflict": _description_conflict(product)})
        for pid, product in products.items()
    }
    return StoreData(categories, products, promotions, customers, orders, order_items)


def find_issues(store: StoreData, today: date) -> list[DataIssue]:
    """Rows that are valid but inconsistent. `today` is used only for overdue orders."""
    issues: list[DataIssue] = []

    def add(kind: str, entity: str, row_id: int, detail: str) -> None:
        issues.append(DataIssue(kind, entity, (row_id,), detail))

    for pid, product in store.products.items():
        if product.description_conflict:
            add("description_conflict", "product", pid, product.description_conflict)
        if product.status == "discontinued" and product.stock_quantity > 0:
            add("discontinued_in_stock", "product", pid, f"{product.stock_quantity} in stock")
        promos = store.promotions_of(pid)
        if len(promos) > 1:
            add("promotion_history", "product", pid, ", ".join(
                f"{p.description} {p.discount_percent}% ({'active' if p.is_active else 'ended'})"
                for p in promos
            ))  # fmt: skip

    used_categories = {product.category_id for product in store.products.values()}
    for cid, category in store.categories.items():
        if cid not in used_categories:
            add("empty_category", "category", cid, category.name)

    for oid, order in store.orders.items():
        items = store.items_of(oid)
        at_current_prices = sum(
            (store.products[i.product_id].price_brl * i.quantity for i in items), Decimal(0)
        )
        if at_current_prices != order.total_brl:
            add("order_total_mismatch", "order", oid, f"recorded R$ {order.total_brl:,.2f}, "
                f"items at current prices R$ {at_current_prices:,.2f}")  # fmt: skip
        if order.status in ("shipped", "delivered"):
            for item in items:
                if store.products[item.product_id].status == "coming_soon":
                    detail = f"{order.status}, contains coming-soon product {item.product_id}"
                    add("unreleased_product_sold", "order", oid, detail)
        if overdue := _overdue_detail(order, today):
            add("overdue_order", "order", oid, overdue)
        if order.payment_method == "pix" and order.total_brl == at_current_prices:
            add("pix_without_discount", "order", oid, f"R$ {order.total_brl:,.2f}; with the "
                f"discount R$ {rules.pix_price(order.total_brl):,.2f}")  # fmt: skip
        if credit := re.fullmatch(r"credit_(\d+)x", order.payment_method):
            count = int(credit[1])
            if count > rules.max_installments(order.total_brl):
                add("installment_below_minimum", "order", oid,
                    f"R$ {order.total_brl:,.2f} in {count}x = R$ {order.total_brl / count:,.2f} "
                    f"each, minimum R$ {rules.minimum_installment(count):,.2f}")  # fmt: skip

    issues += _shared_contacts(store, "email", "shared_email")
    issues += _shared_contacts(store, "phone", "shared_phone")
    issues += _similar_names(store)
    return issues


def _read[M: _Row](raw_dir: Path, name: str, model: type[M]) -> list[M]:
    path = raw_dir / f"{FILE_PREFIX}{name}.csv"
    rows: list[M] = []
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            try:
                rows.append(model.model_validate(row))
            except ValidationError as exc:
                raise DataLoadError(f"{name}.csv, line {reader.line_num}: {exc}") from exc
    return rows


def _index[M: _Row](rows: list[M], key: str) -> dict[int, M]:
    indexed: dict[int, M] = {}
    for row in rows:
        row_id = getattr(row, key)
        if row_id in indexed:
            raise DataLoadError(f"duplicate {key} {row_id}")
        indexed[row_id] = row
    return indexed


def _check_references(field: str, ids: Any, targets: dict[int, Any]) -> None:
    missing = sorted(set(ids) - targets.keys())
    if missing:
        raise DataLoadError(f"{field} references missing ids: {missing}")


def _brands_in(text: str) -> set[str]:
    return {brand for brand in KNOWN_BRANDS if re.search(rf"\b{re.escape(brand)}\b", text)}


def _description_conflict(product: Product) -> str | None:
    """Heuristic: the description names another brand, or a different string or key count."""
    reasons = []
    name_brands = _brands_in(product.name)
    other_brands = _brands_in(product.description) - name_brands
    if other_brands:
        reasons.append(
            f"description names {', '.join(sorted(other_brands))}, "
            f"the name says {', '.join(sorted(name_brands)) or 'no known brand'}"
        )
    for word, spec_key in (("cordas", "strings"), ("teclas", "keys")):
        match = re.search(rf"\b(\d+) {word}\b", product.description)
        spec = product.specs.get(spec_key, "")
        if match and spec.isdigit() and match.group(1) != spec:
            reasons.append(f"description says {match.group(1)} {word}, specs say {spec}")
    return "; ".join(reasons) or None


def _overdue_detail(order: Order, today: date) -> str | None:
    if not rules.is_overdue(order, today):
        return None
    if order.status == "shipped":
        return f"shipped, estimated delivery {order.estimated_delivery} has passed"
    return f"{order.status} since {order.order_date} ({(today - order.order_date).days} days)"


def _shared_contacts(store: StoreData, field: str, kind: str) -> list[DataIssue]:
    by_value: dict[str, list[int]] = {}
    for customer in store.customers.values():
        by_value.setdefault(getattr(customer, field).strip().lower(), []).append(
            customer.customer_id
        )
    return [
        DataIssue(kind, "customer", tuple(ids), f"{len(ids)} customers share {value}")
        for value, ids in by_value.items()
        if len(ids) > 1
    ]


def _name_tokens(name: str) -> frozenset[str]:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return frozenset(plain.split()) - {"da", "de", "do", "das", "dos"}


def _similar_names(store: StoreData) -> list[DataIssue]:
    """Pairs where one name has every word of the other (Bruno Martins, Bruno Carvalho Martins)."""
    issues = []
    for a, b in combinations(store.customers.values(), 2):
        ta, tb = _name_tokens(a.name), _name_tokens(b.name)
        if ta <= tb or tb <= ta:
            issues.append(
                DataIssue(
                    "similar_names",
                    "customer",
                    (a.customer_id, b.customer_id),
                    f"{a.name} / {b.name}",
                )
            )
    return issues
