"""Monthly roll + weekly hedge driver.

    python -m dispersion.execution.runner roll   --leverage 3 --dry-run
    python -m dispersion.execution.runner hedge  --dry-run
    python -m dispersion.execution.runner status

Flow for `roll` (last trading day of the month):
  1. equity, trading status, positions
  2. universe (top-30 by liquidity, point in time) -> rotating half if small
  3. per name: chain -> 30d/10d strikes from IV30 -> 2-leg debit vertical ticket
  4. index: ES/MES (SPAN) or SPY (PM naked / Reg-T 1d spread) 30d call ticket
  5. margin dry-run of the whole roll -> apply the 60% cap rail (cut L by 1)
  6. walk each ticket from mid toward the far side (3-4 steps, 15-20 s)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os

import numpy as np

from .. import bs, config
from .broker import Broker, PaperBroker
from .hedge import hedge_order
from .orders import Leg, Quote, Ticket, work_order
from .sizing import Rails, build_plan
from .strikes import pick_strike, pick_vertical, years_to

log = logging.getLogger("runner")


def implied_vol_from_mid(S, K, T, mid, r=0.0) -> float:
    """Invert BS on the ATM-ish quote to get a live IV30 when no vendor feed is wired."""
    lo, hi = 0.01, 3.0
    for _ in range(60):
        m = 0.5 * (lo + hi)
        if bs.call_price(S, K, T, m, r) > mid:
            hi = m
        else:
            lo = m
    return 0.5 * (lo + hi)


def live_iv30(broker: Broker, underlying: str, S: float, expiry: str, strikes, T: float) -> float:
    k_atm = bs.nearest_listed(S, strikes)
    q = broker.quote(broker.option_symbol(underlying, expiry, k_atm, True), "Equity Option")
    return implied_vol_from_mid(S, k_atm, T, q.mid)


def plan_roll(broker: Broker, ranked_names: list[str], leverage: float, month_index: int, today: dt.date,
              index_mode: str, expiry_by_name: dict[str, str] | None = None) -> dict:
    E = broker.equity()
    prices = {n: broker.quote(n, "Equity").mid for n in ranked_names}
    spy = broker.quote("SPY", "Equity").mid
    plan = build_plan(E, leverage, ranked_names, prices, spy, month_index, index_mode)
    tickets, model = [], {}
    for n, k in plan.contracts.items():
        expiry = (expiry_by_name or {}).get(n) or getattr(broker, "monthly_expiry", lambda *_: {"expiry": None})(n)["expiry"]
        strikes = broker.option_chain(n, expiry)
        if not strikes:
            plan.skipped[n] = "no chain"
            continue
        T = years_to(expiry, today)
        iv = live_iv30(broker, n, prices[n], expiry, strikes, T)
        lo, hi = pick_vertical(prices[n], iv, T, strikes)
        legs = [Leg(broker.option_symbol(n, expiry, lo.strike), "Equity Option", "Buy to Open", k),
                Leg(broker.option_symbol(n, expiry, hi.strike), "Equity Option", "Sell to Open", k)]
        tickets.append(Ticket(legs, "Debit", f"{n} {lo.strike}/{hi.strike} x{k}"))
        model[n] = {"iv30": iv, "long": lo.__dict__, "short": hi.__dict__, "model_debit": lo.model_price - hi.model_price}
    # index leg
    if plan.index_contracts > 0:
        if plan.index_instrument == "SPY":
            expiry = broker.monthly_expiry("SPY")["expiry"] if hasattr(broker, "monthly_expiry") else None
            strikes = broker.option_chain("SPY", expiry)
            T = years_to(expiry, today)
            iv = live_iv30(broker, "SPY", spy, expiry, strikes, T)
            p30 = pick_strike(spy, iv, T, 30, strikes)
            legs = [Leg(broker.option_symbol("SPY", expiry, p30.strike), "Equity Option", "Sell to Open", plan.index_contracts)]
            if index_mode == "SPY_REGT":
                p1 = pick_strike(spy, iv, T, 1, strikes)   # beyond chain -> furthest listed
                legs.append(Leg(broker.option_symbol("SPY", expiry, p1.strike), "Equity Option", "Buy to Open", plan.index_contracts))
            tickets.append(Ticket(legs, "Credit", f"SPY index {p30.strike} x{plan.index_contracts}"))
            model["SPY"] = {"iv30": iv, "short": p30.__dict__}
        else:
            fo = broker.futures_option_chain(plan.index_instrument)
            e = min((x for x in fo if x["dte"] >= 20), key=lambda x: abs(x["dte"] - 30))
            fut_px = broker.quote(e["future"], "Future").mid
            T = years_to(e["expiry"], today)
            # IV30 for ES taken from the SPY chain (same underlying risk)
            spy_e = broker.monthly_expiry("SPY")
            iv = live_iv30(broker, "SPY", spy, spy_e["expiry"], spy_e["strikes"], years_to(spy_e["expiry"], today))
            p30 = pick_strike(fut_px, iv, T, 30, e["strikes"])
            legs = [Leg(e["calls"][p30.strike], "Future Option", "Sell to Open", plan.index_contracts)]
            tickets.append(Ticket(legs, "Credit", f"{plan.index_instrument} index {p30.strike} x{plan.index_contracts}"))
            model[plan.index_instrument] = {"iv30": iv, "short": p30.__dict__, "future": e["future"]}
    return {"plan": plan, "tickets": tickets, "model": model}


def execute(broker: Broker, tickets: list[Ticket], rails: Rails, leverage: float, dry_run: bool, sleep=None) -> dict:
    E = broker.equity()
    quotes = {}
    for t in tickets:
        q = [broker.quote(l.symbol, l.instrument_type) for l in t.legs]
        # net quote of the ticket: long legs add, short legs subtract
        bid = sum((x.bid if l.action.startswith("Buy") else -x.ask) for l, x in zip(t.legs, q))
        ask = sum((x.ask if l.action.startswith("Buy") else -x.bid) for l, x in zip(t.legs, q))
        if t.price_effect == "Credit":
            bid, ask = -ask, -bid
        quotes[t.external_id] = Quote(min(bid, ask), max(bid, ask))
    projected = 0.0
    for t in tickets:
        d = broker.dry_run(t, quotes[t.external_id].mid)
        projected += d.get("margin_change", 0.0)
        if not d["ok"]:
            log.warning("dry-run warnings %s: %s", t.tag, d["warnings"])
    if hasattr(broker, "margin_dry_run"):
        try:
            m = broker.margin_dry_run(tickets, [quotes[t.external_id].mid for t in tickets])
            projected = m["margin_after"] or projected
        except Exception as e:  # noqa: BLE001
            log.warning("margin dry-run unavailable: %s", e)
    L, msg = rails.leverage_after_margin_check(leverage, projected, E)
    log.info(msg)
    if L < leverage:
        return {"status": "resized", "leverage": L, "message": msg}
    if dry_run:
        return {"status": "dry-run", "leverage": L, "tickets": [t.tag for t in tickets], "projected_margin": projected}
    fills = []
    for t in tickets:
        work_order(broker.submit, broker.status, broker.replace, broker.cancel, t, quotes[t.external_id],
                   sleep=sleep or __import__("time").sleep)
        fills.append((t.tag, t.status, t.fill_price))
    return {"status": "done", "leverage": L, "fills": fills}


def _paper_prices(names):
    """Last cached closes for the paper broker (falls back to hashed prices)."""
    try:
        from ..data.universe import load_universe
        uni = load_universe()
        last = uni.close_raw.iloc[-1]
        px = {n: float(last[n]) for n in (names or uni.tradable(uni.close_adj.index[-1])) if n in last.index}
        px["SPY"] = float(uni.spy_raw.iloc[-1])
        return px
    except Exception:  # noqa: BLE001
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["roll", "hedge", "status"])
    ap.add_argument("--leverage", type=float, default=config.LEVERAGE_LIVE_START)
    ap.add_argument("--index-mode", default="ES", choices=["ES", "SPY", "SPY_REGT"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--paper", action="store_true", help="in-memory broker (no network)")
    ap.add_argument("--names", nargs="*", help="override the ranked universe (else computed from data cache)")
    ap.add_argument("--equity", type=float, default=100_000.0, help="paper-mode starting equity")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.paper:
        broker: Broker = PaperBroker(args.equity, synthetic=True, underlying_prices=_paper_prices(args.names))
    else:
        from .tastytrade import TastyBroker, TastyClient
        orders = TastyClient()
        quotes = TastyClient(env="prod") if os.environ.get("TT_PROD_REFRESH_TOKEN") is None else TastyClient(
            env="prod", refresh_token=os.environ["TT_PROD_REFRESH_TOKEN"], client_secret=os.environ.get("TT_PROD_CLIENT_SECRET"))
        broker = TastyBroker(orders, quotes=quotes if orders.env == "sandbox" else None)
    today = dt.date.today()
    if args.cmd == "status":
        print(json.dumps({"equity": broker.equity(), "margin": broker.margin_requirement(),
                          "positions": [p.__dict__ for p in broker.positions()]}, indent=2, default=str))
        return
    if args.cmd == "hedge":
        pos = broker.positions()
        unds = sorted({p.underlying for p in pos} | {"SPY"})
        prices = {u: broker.quote(u, "Equity").mid for u in unds}
        cur = sum(p.quantity for p in pos if p.symbol == "SPY" and p.instrument_type == "Equity")
        h = hedge_order(pos, prices, today, "SPY" if args.index_mode != "ES" else "MES", cur)
        print(json.dumps(h.__dict__, indent=2))
        if not args.dry_run and h.quantity != 0:
            inst = "Equity" if h.instrument == "SPY" else "Future"
            t = Ticket([Leg(h.instrument, inst, "Buy to Open" if h.quantity > 0 else "Sell to Open", abs(h.quantity))],
                       "Debit" if h.quantity > 0 else "Credit", "weekly hedge")
            q = broker.quote(h.instrument, inst)
            work_order(broker.submit, broker.status, broker.replace, broker.cancel, t, q)
        return
    names = args.names
    if not names:
        from ..data.universe import load_universe
        uni = load_universe()
        names = uni.tradable(uni.close_adj.index[-1])
    month_index = today.year * 12 + today.month
    out = plan_roll(broker, names, args.leverage, month_index, today, args.index_mode)
    print(json.dumps({"names": out["plan"].names, "contracts": out["plan"].contracts, "skipped": out["plan"].skipped,
                      "index": [out["plan"].index_instrument, out["plan"].index_contracts], "notes": out["plan"].notes},
                     indent=2))
    res = execute(broker, out["tickets"], Rails(), args.leverage, args.dry_run)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
