"""The eval checks themselves, without the API."""

import re

from emporio_agent.agent import ToolCall, TurnResult
from emporio_agent.tools import TOOLS
from evals.run import call_matches, check_turn
from evals.scenarios import SCENARIOS, Turn

TOOL_NAMES = {tool.__name__ for tool in TOOLS}


def result(reply="", calls=(), guard="ok", error=None):
    return TurnResult(reply, list(calls), 1.0, guard=guard, error=error)


def test_call_spec_with_and_without_argument():
    call = ToolCall("get_policy", {"topic": "store_info"}, "...")
    assert call_matches("get_policy", call)
    assert call_matches("get_policy(topic=store_info)", call)
    assert not call_matches("get_policy(topic=hours)", call)
    product = ToolCall("get_product", {"product_id": 95}, {})
    assert call_matches("get_product(product_id=95)", product)
    assert call_matches("search_products|get_product", product)


def test_check_turn_reports_each_failure():
    turn = Turn(
        "?",
        must_call=("get_product",),
        must_not_call=("open_ticket",),
        must_include=(r"2\.199",),
        must_not_include=(r"417,24",),
    )
    bad = result(
        "Fica R$ 417,24", [ToolCall("open_ticket", {"kind": "complaint"}, {})], guard="blocked"
    )
    failures = check_turn(turn, bad)
    assert len(failures) == 5
    good = result("Custa R$ 2.199,00", [ToolCall("get_product", {"product_id": 95}, {})])
    assert check_turn(turn, good) == []


def test_errors_fail_the_turn():
    assert check_turn(Turn("?"), result(error="ModelHTTPError: 500")) == [
        "error: ModelHTTPError: 500"
    ]


def test_scenarios_are_well_formed():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids)) >= 15
    for scenario in SCENARIOS:
        specs = scenario.must_call_in_conversation + tuple(
            spec for turn in scenario.turns for spec in turn.must_call + turn.must_not_call
        )
        names = {alt.split("(")[0] for spec in specs for alt in spec.split("|")}
        assert names <= TOOL_NAMES, scenario.id
        patterns = scenario.must_include_in_conversation + tuple(
            p for turn in scenario.turns for p in turn.must_include + turn.must_not_include
        )
        for pattern in patterns:
            re.compile(pattern)


def test_p0_scenarios_match_the_plan():
    p0 = {s.id for s in SCENARIOS if s.p0}
    assert p0 == {f"E-{n:02d}" for n in (*range(1, 12), 14, 15)}
