"""Broker-agnostic order objects and the limit-walk algorithm (spec 10):
limit at mid, walked toward the far side in 3-4 steps of ~20% of the
half-spread, 15-20 s apart. Verticals are single 2-leg tickets."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Leg:
    symbol: str
    instrument_type: str       # Equity | Equity Option | Future | Future Option
    action: str                # Buy to Open | Sell to Open | Buy to Close | Sell to Close
    quantity: int


@dataclass
class Ticket:
    legs: list[Leg]
    price_effect: str          # Debit | Credit (net)
    tag: str
    external_id: str = field(default_factory=lambda: uuid.uuid4().hex[:20])
    order_id: str | None = None
    status: str = "new"
    fill_price: float | None = None

    @property
    def is_vertical(self):
        return len(self.legs) == 2


@dataclass
class Quote:
    bid: float
    ask: float

    @property
    def mid(self):
        return (self.bid + self.ask) / 2

    @property
    def half_spread(self):
        return (self.ask - self.bid) / 2


def walk_prices(q: Quote, price_effect: str, steps: int = 4, step_frac: float = 0.20, tick: float = 0.01) -> list[float]:
    """Mid, then 3-4 steps of ~20% of the half-spread toward the far side.
    Debit orders walk up toward the ask, credit orders walk down toward the bid."""
    sign = 1 if price_effect == "Debit" else -1
    out = []
    for i in range(steps):
        p = q.mid + sign * i * step_frac * q.half_spread
        out.append(round(round(p / tick) * tick, 4))
    return out


def work_order(submit: Callable[[Ticket, float], str], status: Callable[[str], str],
               replace: Callable[[str, float], str], cancel: Callable[[str], None],
               ticket: Ticket, quote: Quote, wait_s: float = 17.0, steps: int = 4,
               sleep=time.sleep) -> Ticket:
    """Place at mid, re-price toward the far side until filled; cancel after the
    last step if still unfilled (the caller decides whether to retry)."""
    prices = walk_prices(quote, ticket.price_effect, steps=steps)
    oid = submit(ticket, prices[0])
    ticket.order_id, ticket.status = oid, "working"
    log.info("%s submitted %s @ %.2f (mid)", ticket.tag, ticket.price_effect, prices[0])
    for i, p in enumerate(prices):
        if i > 0:
            oid = replace(oid, p)
            ticket.order_id = oid
            log.info("%s re-priced -> %.2f (step %d)", ticket.tag, p, i)
        sleep(wait_s)
        st = status(oid)
        if st.lower() == "filled":
            ticket.status, ticket.fill_price = "filled", p
            return ticket
    cancel(oid)
    ticket.status = "cancelled"
    log.warning("%s unfilled after %d steps -> cancelled", ticket.tag, steps)
    return ticket
