"""Option versions of the momentum sleeve: instead of the 5 stocks, buy 1-month ATM calls on them (and optionally
sell a 20-delta call against each). Priced with Black-Scholes at the VolVue 30-day call IV, marks at entry IV,
settled at expiry intrinsic; per-leg half-spreads and commissions from the cost model; fixed notional per cycle."""
from __future__ import annotations
import numpy as np, pandas as pd
from . import bs, config
from .data import rates as R


def momentum_option_leg(uni, iv_panel: pd.DataFrame, hold: dict, short_wing: int | None = None, notional=1e6,
                        exposure=1.0, cm=config.REALISTIC_SPY, long_bucket=50):
    """hold: {cycle entry -> [names]} from momentum_leg. exposure: option share-equivalent per $ of sleeve notional
    (1.0 = calls on the same share count the stock sleeve would hold). Returns (daily P&L series, diagnostics)."""
    px = uni.close_adj; days = px.index
    cyc = list(hold.keys()); cyc_all = sorted(set(cyc))
    from .backtest.engine import third_fridays
    tf = third_fridays(days)
    rf = R.fedfunds_daily(days)
    pnl = pd.Series(np.nan, index=days); pnl.iloc[0] = 0.0; run = 0.0; diag = []
    for a in cyc_all:
        j = tf.get_loc(a) if a in tf else None
        if j is None or j + 1 >= len(tf):
            continue
        b = tf[j + 1]; T = (b - a).days / 365.0; r = float(rf.loc[a])
        names = hold[a]; w = exposure * notional / len(names)
        path = pd.Series(0.0, index=px.loc[a:b].index); prem_total = cost_total = 0.0; n_ok = 0
        for t in names:
            ivs = iv_panel[t].loc[:a].dropna() if t in iv_panel.columns else pd.Series(dtype=float)
            if len(ivs) == 0 or (a - ivs.index[-1]).days > 7:
                continue                                           # no IV: name skipped (cash)
            iv = float(ivs.iloc[-1]); S0 = float(px.at[a, t]); sh = w / S0
            K = bs.strike_for_delta(S0, iv, T, long_bucket, r); C0 = float(bs.call_price(S0, K, T, iv, r))
            cost = C0 * cm.single(long_bucket) * sh + config.commission(sh / 100.0, cm)
            Ks = Cs = None
            if short_wing is not None:
                Ks = bs.strike_for_delta(S0, iv, T, short_wing, r); Cs = float(bs.call_price(S0, Ks, T, iv, r))
                cost += Cs * cm.single(short_wing) * sh + config.commission(sh / 100.0, cm)
            S = px.loc[a:b, t].ffill().to_numpy(dtype=float)
            Trem = np.array([(b - d).days / 365.0 for d in path.index])
            mark = bs.call_price(S, K, Trem, iv, r) - C0
            if Ks is not None:
                mark = mark - (bs.call_price(S, Ks, Trem, iv, r) - Cs)
            path = path + sh * mark - cost
            prem_total += (C0 - (Cs or 0.0)) * sh; cost_total += cost; n_ok += 1
        pnl.loc[a:b] = run + path.values; run += float(path.iloc[-1])
        diag.append(dict(entry=a, n=n_ok, premium=prem_total / notional, cost=cost_total / notional))
    return pnl.ffill().fillna(0.0), pd.DataFrame(diag).set_index("entry") if diag else pd.DataFrame()
