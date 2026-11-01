"""T-pricing, T-clock, T-verify, T-returns and alternatives: the business rules, without the API."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from emporio_agent import rules
from emporio_agent.data import find_issues, load_store
from emporio_agent.rules import TIMEZONE


@pytest.fixture(scope="module")
def store():
    return load_store()


def price(store, product_id):
    return rules.price_of(store.products[product_id], store.promotions_of(product_id))


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=TIMEZONE)


# --- T-pricing ------------------------------------------------------------------------------


def test_product_without_promotion_gets_pix_discount(store):
    takamine = price(store, 95)
    assert takamine.promotion is None
    assert takamine.final_price == Decimal("2199")
    assert takamine.pix_price == Decimal("2089.05")
    assert takamine.installments[-1].count == 12
    assert takamine.installments[9] == rules.Installment(10, Decimal("219.90"))


def test_active_promotion_replaces_pix_discount(store):
    ohana = price(store, 121)  # Ohana CK-20, R$ 549, 20% off
    assert ohana.promotion.discount_percent == 20
    assert ohana.final_price == Decimal("439.20")
    assert ohana.pix_price is None  # discounts do not stack (manual 6.2)
    assert ohana.installments[-1] == rules.Installment(5, Decimal("87.84"))  # 6x would be 73.20


def test_ended_promotions_do_not_count(store):
    martin = price(store, 93)  # two ended Easter promotions
    assert martin.promotion is None
    assert martin.final_price == Decimal("11499")


@pytest.mark.parametrize(
    ("count", "minimum"), [(1, 50), (3, 50), (4, 80), (6, 80), (7, 100), (12, 100)]
)
def test_minimum_installment_by_tier(count, minimum):
    assert rules.minimum_installment(count) == Decimal(minimum)


def test_installment_count_out_of_range():
    with pytest.raises(ValueError):
        rules.minimum_installment(13)


@pytest.mark.parametrize(
    ("total", "expected"),
    [("49.90", 1), ("100", 2), ("150", 3), ("319.99", 3), ("320", 4), ("699.99", 6), ("700", 7)],
)
def test_max_installments_at_tier_edges(total, expected):
    assert rules.max_installments(Decimal(total)) == expected


def test_prices_round_half_up():
    assert rules.pix_price(Decimal("599.9")) == Decimal("569.91")  # 569.905


# --- T-clock --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "is_open", "next_opening"),
    [
        ("2026-11-02T09:00", True, None),  # Monday opening
        ("2026-11-02T17:59", True, None),
        ("2026-11-02T08:59", False, "2026-11-02T09:00"),
        ("2026-11-02T18:00", False, "2026-11-03T09:00"),
        ("2026-11-06T18:30", False, "2026-11-07T09:00"),  # Friday evening -> Saturday
        ("2026-10-31T12:59", True, None),  # Saturday until 13:00
        ("2026-10-31T13:00", False, "2026-11-02T09:00"),
        ("2026-10-31T19:40", False, "2026-11-02T09:00"),
        ("2026-11-01T10:00", False, "2026-11-02T09:00"),  # Sunday
    ],
)
def test_store_status(now, is_open, next_opening):
    status = rules.store_status(at(now))
    assert status.is_open is is_open
    assert status.next_opening == (at(next_opening) if next_opening else None)


def test_closing_time_when_open():
    assert rules.store_status(at("2026-10-31T10:00")).closes_at == at("2026-10-31T13:00")


def test_current_time_override():
    assert rules.current_time("2026-11-03T15:30") == at("2026-11-03T15:30")
    assert rules.current_time("2026-11-03T19:30+00:00") == at("2026-11-03T15:30")  # UTC-4
    assert rules.current_time().tzinfo == TIMEZONE


# --- T-verify -------------------------------------------------------------------------------

# Order 1 belongs to customer 3: pedro.oliveira@jmail.com, (67) 98432-1098.


@pytest.mark.parametrize(
    ("order_id", "email", "phone"),
    [
        (1, "pedro.oliveira@jmail.com", None),
        (1, None, "(67) 98432-1098"),
        (None, "  Pedro.Oliveira@JMAIL.com ", "+55 67 98432-1098"),
        (1, None, "98432-1098"),  # without the area code
        (999, "pedro.oliveira@jmail.com", "67984321098"),  # e-mail and phone are enough
    ],
)
def test_two_of_three_identifiers_verify(store, order_id, email, phone):
    assert rules.verify_customer(store, order_id, email, phone).customer_id == 3


@pytest.mark.parametrize(
    ("order_id", "email", "phone"),
    [
        (1, None, None),
        (None, "pedro.oliveira@jmail.com", None),
        (1, "lucas.mendes@jmail.com", None),  # someone else's e-mail
        (1, None, "8432-1098"),  # too few digits
        (5, "pedro.oliveira@jmail.com", None),  # order 5 belongs to customer 5
        (None, "pedro.oliveira@jmail.com", "(67) 99812-3456"),  # customer 1's phone
    ],
)
def test_fewer_than_two_matches_fail(store, order_id, email, phone):
    assert rules.verify_customer(store, order_id, email, phone) is None


def test_shared_email_is_resolved_by_phone(store):
    email = "bruno.martins@jmail.com"  # customers 11 and 43
    assert rules.verify_customer(store, None, email, "(67) 98543-2109").customer_id == 11
    assert rules.verify_customer(store, None, email, "(67) 99456-3456").customer_id == 43


# --- T-returns ------------------------------------------------------------------------------

# Order 7: placed 2026-02-03, delivered (estimated) 2026-02-17.


def windows(store, order_id, today):
    return {
        w.kind: w for w in rules.return_windows(store.orders[order_id], date.fromisoformat(today))
    }


def test_return_deadlines(store):
    result = windows(store, 7, "2026-02-20")
    assert result["regret"].deadline == date(2026, 2, 24)  # receipt + 7
    assert result["preference_exchange"].deadline == date(2026, 2, 24)
    assert result["defect_exchange"].deadline == date(2026, 3, 5)  # purchase + 30
    assert result["legal_warranty"].deadline == date(2026, 5, 18)  # receipt + 90
    assert all(w.is_open for w in result.values())


def test_windows_close_after_the_deadline(store):
    assert windows(store, 7, "2026-02-24")["regret"].is_open  # last day included
    after = windows(store, 7, "2026-02-25")
    assert not after["regret"].is_open
    assert after["defect_exchange"].is_open
    assert not windows(store, 7, "2026-11-01")["legal_warranty"].is_open


@pytest.mark.parametrize("order_id", [8, 13, 17])  # shipped, pending, cancelled
def test_no_windows_before_delivery(store, order_id):
    assert rules.return_windows(store.orders[order_id], date(2026, 11, 1)) == ()


# --- Alternatives ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("product_id", "expected"),
    [
        (96, [86, 91, 87]),  # out of stock, steel-string: steel-string at the closest prices
        (113, [84, 82, 81]),  # discontinued, nylon-string: nylon-string first
    ],
)
def test_alternatives(store, product_id, expected):
    found = rules.alternatives(store, store.products[product_id])
    assert [p.product_id for p in found] == expected
    assert all(p.available and p.category_id == 5 for p in found)


# --- Payment checks on recorded orders ------------------------------------------------------


def test_recorded_orders_checked_against_payment_rules(store):
    issues = find_issues(store, date(2026, 11, 1))
    ids = {kind: {i.ids[0] for i in issues if i.kind == kind} for kind in {i.kind for i in issues}}
    assert ids["pix_without_discount"] == {1, 5, 10, 13, 17}
    assert ids["installment_below_minimum"] == {9, 16, 18}
