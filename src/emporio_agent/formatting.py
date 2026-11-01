"""Brazilian Portuguese formatting of money and dates, shared by the tools, tickets and agent."""

from datetime import date, datetime
from decimal import Decimal

WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def brl(value: Decimal) -> str:
    """R$ 2.199,00"""
    digits = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {digits}"


def day_pt(day: date) -> str:
    """sábado, 31/10/2026"""
    return f"{WEEKDAYS[day.weekday()]}, {day:%d/%m/%Y}"


def when_pt(moment: datetime) -> str:
    """terça, 03/11, às 09:00"""
    return f"{WEEKDAYS[moment.weekday()]}, {moment:%d/%m}, às {moment:%H:%M}"
