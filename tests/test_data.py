"""T-data: the loader validates the CSVs and flags the known inconsistencies."""

import shutil
from datetime import date

import pytest

from emporio_agent.data import ISSUE_KINDS, RAW_DIR, DataLoadError, find_issues, load_store


@pytest.fixture(scope="module")
def store():
    return load_store()


@pytest.fixture(scope="module")
def issues(store):
    return find_issues(store, today=date(2026, 11, 1))


def ids_of(issues, kind):
    return {issue.ids for issue in issues if issue.kind == kind}


def test_loads_every_row(store):
    assert len(store.categories) == 9
    assert len(store.products) == 65
    assert len(store.promotions) == 25
    assert len(store.customers) == 50
    assert len(store.orders) == 20
    assert len(store.order_items) == 22


def test_rows_are_typed(store):
    takamine = store.products[95]
    assert str(takamine.price_brl) == "2199"
    assert takamine.specs["top"] == "Cedro Sólido"
    assert store.orders[9].tracking_code is None
    assert store.orders[9].estimated_delivery is None
    assert store.promotions[13].is_active is True  # promotion 14, Ohana CK-20


def test_availability_follows_status_and_stock(store):
    products = store.products
    assert products[95].available
    assert not products[96].available  # active, no stock
    assert not products[113].available  # discontinued
    assert not products[136].available  # discontinued with 13 in stock
    assert not products[130].available  # coming soon


def test_flags_contradictory_descriptions(store, issues):
    conflicts = ids_of(issues, "description_conflict")
    assert conflicts == {(135,), (137,), (139,), (140,), (142,), (144,)}
    assert "5 cordas" in store.products[137].description_conflict
    assert all(
        store.products[pid].description_conflict is None for pid in range(81, 135)
    )  # the hand-written catalog rows are consistent


def test_flags_catalog_issues(issues):
    assert ids_of(issues, "empty_category") == {(6,), (7,), (8,)}
    assert ids_of(issues, "discontinued_in_stock") == {(136,)}
    assert ids_of(issues, "promotion_history") == {(93,), (96,), (99,)}


def test_flags_order_issues(issues):
    assert ids_of(issues, "order_total_mismatch") == {(3,), (20,)}
    assert ids_of(issues, "unreleased_product_sold") == {(3,)}
    assert ids_of(issues, "overdue_order") == {(n,) for n in range(8, 17)}


def test_overdue_depends_on_today(store):
    early = find_issues(store, today=date(2026, 3, 1))
    assert ids_of(early, "overdue_order") == {(8,)}  # shipped, due 2026-02-28


def test_flags_customer_issues(issues):
    assert ids_of(issues, "shared_email") == {(11, 43)}
    assert ids_of(issues, "shared_phone") == set()
    assert ids_of(issues, "similar_names") == {(11, 27), (11, 43), (13, 47)}


def test_every_issue_kind_has_a_handling_rule(issues):
    assert {issue.kind for issue in issues} <= ISSUE_KINDS.keys()


@pytest.fixture
def raw_copy(tmp_path):
    return shutil.copytree(RAW_DIR, tmp_path / "raw")


def _edit(raw_dir, name, old, new):
    path = raw_dir / f"desafio_tecnico_ai_eng - {name}.csv"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_rejects_a_row_that_breaks_the_schema(raw_copy):
    _edit(raw_copy, "products", ",12,active,", ",12,sold_out,")
    with pytest.raises(DataLoadError, match=r"products\.csv, line 2"):
        load_store(raw_copy)


def test_rejects_a_missing_reference(raw_copy):
    _edit(raw_copy, "order_items", "1,1,93", "1,1,999")
    with pytest.raises(DataLoadError, match=r"order_items\.product_id .*999"):
        load_store(raw_copy)
