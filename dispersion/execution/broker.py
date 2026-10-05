"""Abstract broker interface + an in-memory paper broker used by tests and by
the dry-run CLI. Real implementations: tastytrade.py (retail default) and
institutional.py (prime-broker/PM naked SPY calls)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .orders import Quote, Ticket


@dataclass
class Position:
    symbol: str
    instrument_type: str
    quantity: int          # signed
    multiplier: float
    underlying: str
    strike: float | None = None
    expiry: str | None = None
    is_call: bool | None = None
    entry_iv: float | None = None
    entry_price: float | None = None


class Broker(ABC):
    name = "abstract"

    @abstractmethod
    def equity(self) -> float: ...
    @abstractmethod
    def positions(self) -> list[Position]: ...
    @abstractmethod
    def quote(self, symbol: str, instrument_type: str) -> Quote: ...
    @abstractmethod
    def option_chain(self, underlying: str, expiry: str) -> list[float]:
        """Listed call strikes for an expiry."""
    @abstractmethod
    def option_symbol(self, underlying: str, expiry: str, strike: float, call: bool = True) -> str: ...
    @abstractmethod
    def dry_run(self, ticket: Ticket, price: float) -> dict:
        """Returns {'ok': bool, 'margin_change': $, 'bp_change': $, 'warnings': [...]}"""
    @abstractmethod
    def submit(self, ticket: Ticket, price: float) -> str: ...
    @abstractmethod
    def status(self, order_id: str) -> str: ...
    @abstractmethod
    def replace(self, order_id: str, price: float) -> str: ...
    @abstractmethod
    def cancel(self, order_id: str) -> None: ...
    @abstractmethod
    def margin_requirement(self) -> dict:
        """{'maintenance': $, 'buying_power': $}"""


class PaperBroker(Broker):
    """Deterministic fills at the first price (mid) -- for tests / dry runs.

    With `synthetic=True` it fabricates chains and Black-Scholes quotes
    (flat 25% IV singles / 16% SPY, 3% half-spread) around the supplied
    underlying prices so the roll/hedge commands run end to end offline."""
    name = "paper"

    def __init__(self, equity=100_000.0, quotes: dict[str, Quote] | None = None, chains: dict | None = None,
                 synthetic=False, underlying_prices: dict[str, float] | None = None, today=None):
        import datetime as dt
        self._equity = equity
        self._quotes = quotes or {}
        self._chains = chains or {}
        self._orders: dict[str, dict] = {}
        self._pos: dict[str, Position] = {}
        self.margin_per_contract = 0.0
        self.synthetic = synthetic
        self.today = today or dt.date.today()
        self._und = underlying_prices or {}

    # ---- synthetic market (offline mechanics validation) -------------------
    @staticmethod
    def third_friday(y, m):
        import datetime as dt
        d = dt.date(y, m, 15)
        return d + dt.timedelta(days=(4 - d.weekday()) % 7)

    def monthly_expiry(self, underlying, target_dte=30):
        y, m = (self.today.year + (self.today.month // 12), self.today.month % 12 + 1)
        e = self.third_friday(y, m)
        return {"expiry": e.isoformat(), "dte": (e - self.today).days, "type": "Regular",
                "strikes": self.option_chain(underlying, e.isoformat())}

    def _und_px(self, u):
        if u in self._und:
            return self._und[u]
        return abs(hash(u)) % 400 + 50.0

    def _bs_quote(self, under, strike, expiry, call=True, mult=1.0):
        from .. import bs
        from .strikes import years_to
        S = self._und_px(under) * mult
        iv = 0.16 if under in ("SPY", "ES", "MES") else 0.25
        p = float(bs.call_price(S, strike, years_to(expiry, self.today), iv))
        hs = max(0.03 * p, 0.01)
        return Quote(max(p - hs, 0.0), p + hs)

    def equity(self):
        return self._equity

    def positions(self):
        return list(self._pos.values())

    def quote(self, symbol, instrument_type):
        if symbol in self._quotes:
            return self._quotes[symbol]
        if not self.synthetic:
            return Quote(0.95, 1.05)
        if instrument_type == "Equity":
            S = self._und_px(symbol)
            return Quote(S * 0.9999, S * 1.0001)
        if instrument_type == "Equity Option":
            from .symbols import parse_occ
            o = parse_occ(symbol)
            return self._bs_quote(o["root"], o["strike"], o["expiry"], o["call"])
        if instrument_type == "Future":
            S = self._und_px("SPY") * 10
            return Quote(S * 0.9999, S * 1.0001)
        if instrument_type == "Future Option":
            import datetime as dt
            tail = symbol.split()[-1]
            e = dt.datetime.strptime(tail[:6], "%y%m%d").date().isoformat()
            return self._bs_quote("SPY", float(tail[7:]), e, tail[6] == "C", mult=10.0)
        return Quote(0.95, 1.05)

    def option_chain(self, underlying, expiry):
        if (underlying, expiry) in self._chains or not self.synthetic:
            return self._chains.get((underlying, expiry), [])
        S = self._und_px(underlying)
        step = 1.0 if S < 100 else 2.5 if S < 300 else 5.0
        import numpy as np
        return [float(k) for k in np.arange(np.floor(S * 0.7 / step) * step, S * 1.4, step)]

    def futures_option_chain(self, product="ES"):
        import datetime as dt
        e = self.monthly_expiry("SPY")
        fut = f"/{product}Z{str(self.today.year)[-1]}"
        root = "EW3"
        S = self._und_px("SPY") * 10
        strikes = [float(k) for k in range(int(S * 0.8 // 25 * 25), int(S * 1.3), 25)]
        from .symbols import future_option
        return [{"expiry": e["expiry"], "dte": e["dte"], "future": fut, "root": root, "contract": root,
                 "type": "Regular", "strikes": strikes,
                 "calls": {k: future_option(fut, root + fut[-2:], e["expiry"], k, True) for k in strikes}}]

    def margin_dry_run(self, tickets, prices):
        # crude SPAN-ish stand-in: 20% of short option notional + debit paid on verticals
        m = 0.0
        for t, p in zip(tickets, prices):
            for l in t.legs:
                if l.action == "Sell to Open" and len(t.legs) == 1:
                    mult = 100.0 if l.instrument_type == "Equity Option" else (50.0 if "/ES" in l.symbol else 5.0)
                    under = self._und_px("SPY") * (10 if l.instrument_type == "Future Option" else 1)
                    m += 0.20 * mult * under * l.quantity
            if t.price_effect == "Debit":
                m += abs(p) * 100 * t.legs[0].quantity
        return {"margin_after": m, "margin_change": m, "bp_after": self._equity - m, "raw": {}}

    def option_symbol(self, underlying, expiry, strike, call=True):
        from .symbols import occ
        return occ(underlying, expiry, strike, call)

    def dry_run(self, ticket, price):
        return {"ok": True, "margin_change": self.margin_per_contract * sum(l.quantity for l in ticket.legs),
                "bp_change": 0.0, "warnings": []}

    def submit(self, ticket, price):
        oid = f"paper-{len(self._orders) + 1}"
        self._orders[oid] = {"ticket": ticket, "price": price, "status": "Filled"}
        for l in ticket.legs:
            q = l.quantity if l.action.startswith("Buy") else -l.quantity
            p = self._pos.setdefault(l.symbol, Position(l.symbol, l.instrument_type, 0, 100.0 if "Option" in l.instrument_type else 1.0, l.symbol.split()[0]))
            p.quantity += q
            p.entry_price = price
        return oid

    def status(self, order_id):
        return self._orders[order_id]["status"]

    def replace(self, order_id, price):
        self._orders[order_id]["price"] = price
        return order_id

    def cancel(self, order_id):
        self._orders[order_id]["status"] = "Cancelled"

    def margin_requirement(self):
        return {"maintenance": 0.0, "buying_power": self._equity}
