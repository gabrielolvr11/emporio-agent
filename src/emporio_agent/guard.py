"""Numeric fidelity guard: every amount in a reply must come from a tool result or the customer.

The model copies amounts from the tools (R$ 1.234,56); this check catches the ones it
computed or invented. It is a plain comparison of numbers, with no extra LLM call. agent.py runs
it as an output validator: one retry with a correction, then a safe reply and a ticket.
"""

import re
from collections.abc import Iterable
from decimal import Decimal

# "R$ 2.199,00", "R$2199", "R$ 1234.56", and bare Brazilian money such as "219,90".
_PREFIXED = r"R\$\s*(\d[\d.,]*\d|\d)"
_BARE = r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,])"
AMOUNT = re.compile(f"{_PREFIXED}|{_BARE}")


def parse_amount(raw: str) -> Decimal:
    """'2.199,00' -> 2199.00, '1234.56' -> 1234.56, '2.199' -> 2199."""
    raw = raw.strip(".,")
    if "," in raw:
        whole, cents = raw.rsplit(",", 1)
        return Decimal(f"{whole.replace('.', '')}.{cents}")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        return Decimal(raw.replace(".", ""))
    return Decimal(raw)


def amounts_in(text: str) -> dict[Decimal, str]:
    """Amounts found in `text`, mapped to how they were written."""
    found = {}
    for match in AMOUNT.finditer(text):
        raw = match.group(1) or match.group(2)
        found.setdefault(parse_amount(raw), match.group(0))
    return found


def unsupported_amounts(reply: str, sources: Iterable[str]) -> list[str]:
    """Amounts in `reply` that appear in none of `sources`, as written in the reply."""
    allowed: set[Decimal] = set()
    for source in sources:
        allowed |= amounts_in(source).keys()
    return [written for value, written in amounts_in(reply).items() if value not in allowed]
