"""TLT straddle / strangle sleeves: monthly third-Friday cycle, Black-Scholes at the VolVue 30-day IV, marks at
entry IV, settled at expiry; delta hedge none / weekly / daily / band (stop-and-reverse at +/-band from entry).
Fixed notional per cycle; costs: half-spread on premium, capped commissions, 1 bp on hedge turnover."""
from __future__ import annotations
import numpy as np, pandas as pd
from . import bs, config
from .backtest.engine import third_fridays


def tlt_option_leg(px: pd.Series, iv: pd.Series, rf: pd.Series, side=-1, structure="straddle", delta=25, hedge="W",
                   band=0.01, notional=1e6, half_spread=0.015, hedge_bp=1e-4, mask: pd.Series | None = None,
                   iv_mult=1.0, start="2007-01-01"):
    """side -1 = short the structure. structure: straddle (ATM) | strangle (delta-bucket wings). hedge: None|'W'|'D'|'band'.
    mask: optional daily bool, cycle skipped when False at entry. Returns daily P&L on `notional` (additive) and diagnostics."""
    days = px.index; cyc = third_fridays(days); cyc = cyc[cyc >= pd.Timestamp(start)]
    pnl = pd.Series(np.nan, index=days); pnl.iloc[0] = 0.0; run = 0.0; diag = []
    ivd = iv.reindex(days).ffill(limit=5)
    for a, b in zip(cyc[:-1], cyc[1:]):
        if mask is not None and not bool(mask.reindex([a], method="ffill").iloc[0]):
            pnl.loc[a:b] = run; continue
        S0 = float(px.loc[a]); sig = float(ivd.loc[a]) if np.isfinite(ivd.loc[a]) else np.nan
        if not np.isfinite(sig):
            pnl.loc[a:b] = run; continue
        sig *= iv_mult; T = (b - a).days / 365.0; r = float(rf.loc[a]); sh = notional / S0
        if structure == "straddle":
            Kc = Kp = S0
        else:
            Kc = bs.strike_for_delta(S0, sig, T, delta, r); Kp = bs.put_strike_for_delta(S0, sig, T, delta, r)
        C0 = float(bs.call_price(S0, Kc, T, sig, r)); P0 = float(bs.put_price(S0, Kp, T, sig, r))
        prem = (C0 + P0) * sh
        cost = prem * half_spread + 2 * config.commission(sh / 100.0, config.REALISTIC_SPY)
        seg = px.loc[a:b]; dates = seg.index; S = seg.to_numpy(dtype=float)
        Trem = np.array([(b - d).days / 365.0 for d in dates])
        val = side * sh * (bs.call_price(S, Kc, Trem, sig, r) + bs.put_price(S, Kp, Trem, sig, r))    # option value path
        dl = side * sh * (bs.call_delta(S, Kc, Trem, sig, r) + bs.put_delta(S, Kp, Trem, sig, r))      # position delta (shares)
        h = np.zeros(len(S)); hedge_pnl = np.zeros(len(S)); turn = 0.0
        hs = 0.0
        for i in range(1, len(S)):
            hedge_pnl[i] = hs * (S[i] - S[i - 1])
            if i == len(S) - 1:
                turn += abs(hs) * S[i]; hs = 0.0; continue
            if hedge == "D" or (hedge == "W" and i % 5 == 0):
                tgt = -dl[i]; turn += abs(tgt - hs) * S[i]; hs = tgt
            elif hedge == "band":
                if side < 0:   # short straddle: hedge the losing side fully beyond the band, unwind at the start level
                    tgt = sh if S[i] > S0 * (1 + band) else (-sh if S[i] < S0 * (1 - band) else (hs if (hs > 0 and S[i] > S0) or (hs < 0 and S[i] < S0) else 0.0))
                else:
                    tgt = -sh if S[i] > S0 * (1 + band) else (sh if S[i] < S0 * (1 - band) else (hs if (hs < 0 and S[i] > S0) or (hs > 0 and S[i] < S0) else 0.0))
                turn += abs(tgt - hs) * S[i]; hs = tgt
            h[i] = hs
        path = (val - val[0]) + np.cumsum(hedge_pnl) - cost - turn * hedge_bp
        pnl.loc[a:b] = run + path; run += float(path[-1])
        diag.append(dict(entry=a, iv=sig, prem=prem / notional, pnl=path[-1] / notional, rv=float(np.std(np.diff(np.log(S))) * np.sqrt(252))))
    return pnl.ffill().fillna(0.0), pd.DataFrame(diag).set_index("entry")
