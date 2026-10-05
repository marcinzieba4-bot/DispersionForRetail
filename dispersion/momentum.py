"""Monthly cross-sectional momentum sleeve on point-in-time S&P 500 members: each third Friday buy the
`n` names with the highest (lookback - skip) past return, equal weight, hold to the next third Friday.
P&L on a FIXED notional per cycle (additive, like the option sleeves). Costs: `cost_bp` per side on turnover."""
from __future__ import annotations
import numpy as np, pandas as pd
from .backtest.engine import third_fridays


def momentum_leg(uni, n=5, lookback=252, skip=21, universe="members", vol_scale=False, min_adv=20e6,
                 cost_bp=5e-4, notional=1e6, start="2007-01-01", stop=None, trail=None, sleeve_stop=None, trend=None):
    """stop: per-name exit when the name closes `stop` below its entry price (e.g. 0.15); trail: per-name exit when
    it closes `trail` below its running high since entry; sleeve_stop: exit all names when the sleeve is down that
    much from cycle start; trend: skip the cycle when SPY is below its `trend`-day average. Stops trigger on the
    close and are executed at the NEXT close (gap risk included); a stopped name stays in cash until the next cycle."""
    px = uni.close_adj; days = px.index
    cyc = third_fridays(days); cyc = cyc[cyc >= pd.Timestamp(start)]
    adv = (px * uni.volume).rolling(63, min_periods=40).median()
    pnl = pd.Series(0.0, index=days); run = 0.0; prev_w = pd.Series(dtype=float); hold = {}; cov = {}
    ret = np.log(px).diff()
    for a, b in zip(cyc[:-1], cyc[1:]):
        ia = days.get_loc(a)
        if ia < lookback + 5:
            continue
        mem = [t for t in uni.members_fn(a) if t in px.columns]
        if universe == "top100":
            mc = uni.mcap.loc[:a].iloc[-1].reindex(mem).dropna(); mem = mc.sort_values(ascending=False).head(100).index.tolist()
        p0 = px.iloc[ia - lookback][mem]; p1 = px.iloc[ia - skip][mem]; pa = px.iloc[ia][mem]
        ok = p0.notna() & p1.notna() & pa.notna() & (adv.iloc[ia][mem] > min_adv)
        cov[a] = ok.sum() / max(len(mem), 1)
        mom = (p1 / p0 - 1)[ok]
        if vol_scale:
            v = ret.iloc[ia - lookback:ia][mom.index].std() * np.sqrt(252); mom = mom / v.replace(0, np.nan)
        top = mom.dropna().nlargest(n).index.tolist()
        if len(top) < n:
            continue
        if trend is not None and float(uni.spy.loc[a]) < float(uni.spy.loc[:a].tail(trend).mean()):
            pnl.loc[a:b] = run; prev_w = pd.Series(dtype=float); continue
        w = pd.Series(1.0 / n, index=top); hold[a] = top
        turn = (w.reindex(w.index.union(prev_w.index)).fillna(0) - prev_w.reindex(w.index.union(prev_w.index)).fillna(0)).abs().sum()
        seg = px.loc[a:b, top].ffill(); seg = seg / seg.iloc[0]
        if stop is not None or trail is not None or sleeve_stop is not None:
            seg = seg.copy(); alive = {t: True for t in top}; extra_turn = 0.0; sleeve_dead = False
            hi = seg.iloc[0].copy()
            for i in range(1, len(seg)):
                row = seg.iloc[i]
                if sleeve_dead:
                    seg.iloc[i] = seg.iloc[i - 1]; continue
                for t in top:
                    if not alive[t]:
                        seg.iloc[i, seg.columns.get_loc(t)] = seg.iloc[i - 1][t]      # frozen at the exit value
                prev = seg.iloc[i - 1]
                if sleeve_stop is not None and i >= 2 and float((seg.iloc[i - 1] * w).sum()) - 1.0 <= -sleeve_stop:
                    sleeve_dead = True; extra_turn += float(sum(w[t] * seg.iloc[i][t] for t in top if alive[t]))
                    seg.iloc[i] = seg.iloc[i]           # exit at this close (the trigger was the previous close)
                    continue
                for t in top:
                    if not alive[t]:
                        continue
                    hi[t] = max(hi[t], float(prev[t]))
                    trig = (stop is not None and float(prev[t]) <= 1.0 - stop) or (trail is not None and float(prev[t]) <= hi[t] * (1.0 - trail))
                    if trig and i >= 2:
                        alive[t] = False; extra_turn += w[t] * float(row[t])     # exit at this close
            turn += extra_turn
        path = notional * ((seg * w).sum(axis=1) - 1.0) - notional * turn * cost_bp
        pnl.loc[a:b] = run + path.values; run += float(path.iloc[-1])
        prev_w = w * seg.iloc[-1] / float((seg.iloc[-1] * w).sum())     # drifted weights before the next rebalance
    return pnl.ffill(), hold, pd.Series(cov)
