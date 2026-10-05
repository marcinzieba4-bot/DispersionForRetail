"""Weekly delta hedge (spec 4): sum BS deltas of every option leg at ENTRY IV
with remaining time, in dollar terms, and neutralise with SPY shares or MES."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .. import bs
from .broker import Position


@dataclass
class HedgeOrder:
    instrument: str        # SPY | MES
    quantity: int          # signed shares / contracts to trade
    dollar_delta_before: float
    dollar_delta_after: float


def book_dollar_delta(positions: list[Position], prices: dict[str, float], today: dt.date, r: float = 0.0) -> float:
    total = 0.0
    for p in positions:
        if p.is_call is None or p.strike is None or p.expiry is None:
            continue   # not an option
        S = prices[p.underlying]
        T = max((dt.date.fromisoformat(p.expiry) - today).days, 0) / 365.0
        d = float(bs.call_delta(S, p.strike, T, p.entry_iv, r))
        notional_per_contract = p.multiplier * S
        total += p.quantity * d * notional_per_contract
    return total


def hedge_order(positions, prices, today, hedge_instrument: str = "SPY", current_hedge_qty: int = 0,
                spy_price: float | None = None, r: float = 0.0) -> HedgeOrder:
    dd = book_dollar_delta(positions, prices, today, r)
    spy = spy_price or prices["SPY"]
    if hedge_instrument == "SPY":
        per_unit = spy
    else:  # MES: $5 x SPX ~ 50 x SPY
        per_unit = 5.0 * spy * 10.0
    hedge_dd = current_hedge_qty * per_unit
    target_qty = int(round(-(dd) / per_unit))
    trade = target_qty - current_hedge_qty
    after = dd + hedge_dd + trade * per_unit
    return HedgeOrder(hedge_instrument, trade, dd + hedge_dd, after)
