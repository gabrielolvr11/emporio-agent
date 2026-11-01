"""Tickets for complaints, returns and handoffs to the team, appended to a JSONL file."""

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from emporio_agent import rules
from emporio_agent.formatting import when_pt

TicketKind = Literal[
    "complaint", "return_request", "human_request", "unanswered_question", "shipping_quote"
]

_lock = threading.Lock()  # protocol numbers must stay unique across concurrent sessions


@dataclass(frozen=True)
class Ticket:
    protocol: str
    """EMP-YYYYMMDD-NNNN, numbered per day."""
    created_at: str
    kind: TicketKind
    summary: str
    customer_id: int | None
    session_id: str


def create_ticket(
    path: Path,
    now: datetime,
    kind: TicketKind,
    summary: str,
    session_id: str,
    customer_id: int | None = None,
) -> Ticket:
    prefix = f"EMP-{now:%Y%m%d}-"
    with _lock:
        today_count = 0
        if path.exists():
            with path.open(encoding="utf-8") as file:
                today_count = sum(json.loads(line)["protocol"].startswith(prefix) for line in file)
        ticket = Ticket(
            f"{prefix}{today_count + 1:04d}",
            now.isoformat(timespec="seconds"),
            kind,
            summary,
            customer_id,
            session_id,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(ticket), ensure_ascii=False) + "\n")
    return ticket


def response_time(now: datetime) -> str:
    """Manual 7.3: the team answers within 24 business hours, counted from reopening if closed."""
    status = rules.store_status(now)
    if status.is_open:
        return "em até 24 horas úteis"
    return (
        f"em até 24 horas úteis, contadas a partir da reabertura ({when_pt(status.next_opening)})"
    )
