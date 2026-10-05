"""Entry-date margin of a cycle's legs, two ways:
  regt: Reg-T per position (naked short option: max(20% S - OTM, 10% S) + premium; straddle rule; long option: premium;
        stock hedge: 50%), summed over positions.
  pm:   portfolio margin as a TIMS-style stress: per underlying, worst loss of options + entry delta hedge over
        +/-12% (SPY) or +/-15% (single name) moves at entry IV, summed over underlyings (no cross-name offset).
Cboe index legs are expanded into their option legs (CBOE_LEGS) priced at the leg's IV."""
from __future__ import annotations
import numpy as np
from .. import bs
from .engine import CBOE_LEGS, Backtest

SCEN_SPY = np.linspace(-0.12, 0.12, 13)
SCEN_SN = np.linspace(-0.15, 0.15, 13)


def _price(kind, S, K, T, iv, r, q):
    return bs.put_price(S, K, T, iv, r, q) if kind == "put" else bs.call_price(S, K, T, iv, r, q)


def _delta(kind, S, K, T, iv, r, q):
    return bs.put_delta(S, K, T, iv, r, q) if kind == "put" else bs.call_delta(S, K, T, iv, r, q)


def expand(rec, T, r):
    """[(ticker, kind, units, K, iv, S0, q, is_index)] with Cboe legs expanded."""
    out = []
    for l in rec.legs:
        if l.units == 0:
            continue
        if l.kind == "cboe":
            held = -l.units                     # units of the index position we hold
            for kind, kk, sg in CBOE_LEGS[l.ticker]:
                K = Backtest._cboe_strike(kk, l.S0, l.iv, T, r - l.q)
                out.append(("SPY", kind, held * sg, K, l.iv, l.S0, l.q, True))
        else:
            out.append((l.ticker, l.kind, l.units, l.K, l.iv, l.S0, l.q, l.is_index))
    return out


def cycle_margin(rec, cfg, r: float) -> dict:
    T = (rec.expiry - rec.entry).days / 365.0
    legs = expand(rec, T, r)
    if not legs:
        return dict(regt=0.0, pm=0.0, opt_notional=0.0, stock_hedge=0.0)
    by = {}
    for t, kind, u, K, iv, S, q, isx in legs:
        by.setdefault(t, []).append((kind, u, K, iv, S, q, isx))
    hedged = cfg.hedge_scope in ("split", "singles", "index")
    regt = pm = notional = stock = 0.0
    for t, L in by.items():
        S = L[0][4]; isx = L[0][6]
        scen = SCEN_SPY if isx else SCEN_SN
        # entry delta hedge (per-name for singles when split/singles; SPY leg when split/index)
        dd = sum(u * _delta(k, S, K, T, iv, r, q) for k, u, K, iv, S_, q, _ in L)
        h = -dd if (hedged and (not isx or cfg.hedge_scope != "singles")) else 0.0
        stock += abs(h) * S
        # portfolio-margin stress
        base = sum(u * _price(k, S, K, T, iv, r, q) for k, u, K, iv, S_, q, _ in L)
        worst = 0.0
        for m in scen:
            Sm = S * (1 + m)
            pl = sum(u * _price(k, Sm, K, T, iv, r, q) for k, u, K, iv, S_, q, _ in L) - base + h * (Sm - S)
            worst = min(worst, pl)
        pm += -worst
        # Reg-T
        req_c = req_p = prem_c = prem_p = 0.0
        for k, u, K, iv, S_, q, _ in L:
            p = _price(k, S, K, T, iv, r, q)
            notional += abs(u) * S
            if u > 0:
                regt += u * p                                   # long: pay premium
                continue
            otm = max(0.0, (K - S) if k == "call" else (S - K))
            req = (max(0.20 * S - otm, 0.10 * S) + p) * (-u)
            if k == "call":
                req_c += req; prem_c += p * (-u)
            else:
                req_p += req; prem_p += p * (-u)
        if req_c and req_p:
            regt += max(req_c + prem_p, req_p + prem_c)        # short straddle / strangle rule
        else:
            regt += req_c + req_p
        regt += 0.5 * abs(h) * S
    return dict(regt=regt, pm=pm, opt_notional=notional, stock_hedge=stock)
