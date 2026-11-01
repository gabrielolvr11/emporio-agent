"""Business rules from the store manual, as pure functions.

Everything with a consequence for the customer is decided here, not by the LLM: prices,
promotions, PIX discount, installments, opening hours, customer verification, return windows
and product alternatives. Section numbers refer to the manual (data/processed/policies).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from emporio_agent.data import Customer, Order, Product, Promotion, StoreData

# --- Prices (manual 3, 3.1 and 6.2) ---------------------------------------------------------

PIX_DISCOUNT = Decimal("0.05")
MAX_INSTALLMENTS = 12
# (highest installment count in the tier, minimum installment amount)
INSTALLMENT_TIERS = ((3, Decimal(50)), (6, Decimal(80)), (12, Decimal(100)))


@dataclass(frozen=True)
class Installment:
    count: int
    amount: Decimal
    """Amount of each installment, rounded to cents. No interest."""


@dataclass(frozen=True)
class Price:
    list_price: Decimal
    promotion: Promotion | None
    """The active promotion, if any. Ended promotions never count."""
    final_price: Decimal
    """Promotional price when a promotion is active, otherwise the list price."""
    pix_price: Decimal | None
    """List price minus 5%. None when a promotion is active: discounts do not stack."""
    installments: tuple[Installment, ...]
    """Credit-card options allowed for the final price."""


def round_brl(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def pix_price(list_price: Decimal) -> Decimal:
    return round_brl(list_price * (1 - PIX_DISCOUNT))


def minimum_installment(count: int) -> Decimal:
    for highest_count, minimum in INSTALLMENT_TIERS:
        if 1 <= count <= highest_count:
            return minimum
    raise ValueError(f"installment count must be 1 to {MAX_INSTALLMENTS}, got {count}")


def installment_options(total: Decimal) -> tuple[Installment, ...]:
    """Paying in full (1x) is always allowed; more installments must meet the tier minimum."""
    return tuple(
        Installment(count, round_brl(total / count))
        for count in range(1, MAX_INSTALLMENTS + 1)
        if count == 1 or total >= minimum_installment(count) * count
    )


def max_installments(total: Decimal) -> int:
    return installment_options(total)[-1].count


def price_of(product: Product, promotions: list[Promotion]) -> Price:
    """Price of a product, given all of its promotion rows (active and ended)."""
    active = [promo for promo in promotions if promo.is_active]
    promotion = max(active, key=lambda promo: promo.discount_percent, default=None)
    if promotion:
        final = round_brl(product.price_brl * (100 - promotion.discount_percent) / 100)
        pix = None
    else:
        final = product.price_brl
        pix = pix_price(product.price_brl)
    return Price(product.price_brl, promotion, final, pix, installment_options(final))


# --- Opening hours (manual 2) ---------------------------------------------------------------

TIMEZONE = ZoneInfo("America/Campo_Grande")
# weekday (Monday = 0) -> (opening, closing). Sunday is closed. Holidays are closed too, but
# the data has no holiday calendar, so they are not modelled (a documented limitation).
OPENING_HOURS = {
    **{weekday: (time(9), time(18)) for weekday in range(5)},
    5: (time(9), time(13)),
}


@dataclass(frozen=True)
class StoreStatus:
    is_open: bool
    closes_at: datetime | None
    """Today's closing time, when open."""
    next_opening: datetime | None
    """The next opening time, when closed."""


def current_time(override: str | None = None) -> datetime:
    """Now in Campo Grande, or `override` (ISO 8601; local time when it has no offset)."""
    if not override:
        return datetime.now(TIMEZONE)
    parsed = datetime.fromisoformat(override)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(TIMEZONE)


def store_status(now: datetime) -> StoreStatus:
    now = now.astimezone(TIMEZONE)
    hours = OPENING_HOURS.get(now.weekday())
    if hours and hours[0] <= now.time() < hours[1]:
        return StoreStatus(True, datetime.combine(now.date(), hours[1], TIMEZONE), None)
    return StoreStatus(False, None, _next_opening(now))


def _next_opening(now: datetime) -> datetime:
    for days_ahead in range(8):
        day = now.date() + timedelta(days=days_ahead)
        if hours := OPENING_HOURS.get(day.weekday()):
            opening = datetime.combine(day, hours[0], TIMEZONE)
            if opening > now:
                return opening
    raise AssertionError("the store opens at least once a week")


# --- Customer verification (decision B1) ----------------------------------------------------

MAX_VERIFICATION_ATTEMPTS = 3


def phones_match(stored: str, given: str) -> bool:
    """Compare digits only. Accepts the country code (+55) and a number without the area code."""
    given_digits = re.sub(r"\D", "", given)
    if len(given_digits) in (12, 13) and given_digits.startswith("55"):
        given_digits = given_digits[2:]
    return len(given_digits) in (9, 11) and re.sub(r"\D", "", stored).endswith(given_digits)


def verify_customer(
    store: StoreData, order_id: int | None, email: str | None, phone: str | None
) -> Customer | None:
    """The customer matched by at least 2 of the 3 identifiers, or None. Never by name."""
    order = store.orders.get(order_id) if order_id is not None else None
    given_email = email.strip().lower() if email else None
    for customer in store.customers.values():
        matches = (
            order is not None and order.customer_id == customer.customer_id,
            given_email is not None and customer.email.strip().lower() == given_email,
            phone is not None and phones_match(customer.phone, phone),
        )
        if sum(matches) >= 2:
            return customer
    return None


# --- Returns, exchanges and warranty (manual 4 and 8) ---------------------------------------

ReturnKind = Literal["regret", "defect_exchange", "preference_exchange", "legal_warranty"]
# kind -> (days, counted from). The data has no receipt date: receipt = estimated_delivery
# (data rule 10). The manual does not say when the preference window starts; like regret,
# it is counted from receipt.
RETURN_WINDOWS: dict[ReturnKind, tuple[int, Literal["receipt", "purchase"]]] = {
    "regret": (7, "receipt"),  # 4.1, online purchases
    "defect_exchange": (30, "purchase"),  # 4.2; afterwards, the manufacturer's warranty
    "preference_exchange": (7, "receipt"),  # 4.3, subject to availability
    "legal_warranty": (90, "receipt"),  # 8.1
}


@dataclass(frozen=True)
class ReturnWindow:
    kind: ReturnKind
    deadline: date
    """Last day of the window, inclusive."""
    is_open: bool


def return_windows(order: Order, today: date) -> tuple[ReturnWindow, ...]:
    """Windows of a delivered order. Empty while it has not been delivered (or was cancelled)."""
    if order.status != "delivered" or order.estimated_delivery is None:
        return ()
    starts = {"receipt": order.estimated_delivery, "purchase": order.order_date}
    windows = []
    for kind, (days, counted_from) in RETURN_WINDOWS.items():
        deadline = starts[counted_from] + timedelta(days=days)
        windows.append(ReturnWindow(kind, deadline, today <= deadline))
    return tuple(windows)


# --- Alternatives (manual 7.3) --------------------------------------------------------------


def alternatives(store: StoreData, product: Product, limit: int = 3) -> list[Product]:
    """Available products of the same category: same string type and electronics first, then
    the closest price. For out-of-stock, discontinued and coming-soon products."""

    def rank(candidate: Product) -> tuple[int, Decimal, int]:
        same_specs = sum(
            candidate.specs.get(key) == value
            for key, value in product.specs.items()
            if key in ("strings", "electronics")
        )
        return (-same_specs, abs(candidate.price_brl - product.price_brl), candidate.product_id)

    candidates = [
        candidate
        for candidate in store.products.values()
        if candidate.category_id == product.category_id
        and candidate.available
        and candidate.product_id != product.product_id
    ]
    return sorted(candidates, key=rank)[:limit]
