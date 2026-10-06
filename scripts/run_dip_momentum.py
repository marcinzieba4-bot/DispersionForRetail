"""Dip-only tranches (money market otherwise) combined with the momentum sleeve (15% trailing stop, with and
without the 200d trend filter): grid over momentum weight and dip leverage, cash yield on all equity."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
D = pd.read_csv(RESULTS / "dip_only_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = D.index
M = pd.read_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
def equity(w_mom, p_mom, L_dip, p_dip):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; qm = p_mom.loc[a:b]; qd = p_dip.loc[a:b]
        seg = E * (c / c.iloc[0]) + w_mom * E / 1e6 * (qm - qm.iloc[0]) + L_dip * E / 1e6 * (qd - qd.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:54s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}", flush=True)
    return st, y
rows = []
for mk, mcol in (("mom trend+trail15", "trend + trailing -15%"), ("mom trail15 only", "trailing -15%")):
    for dk in ("all dips 2 tranches 0.5x", "all dips 1.0x (PW -1%, CO -1%, EV -1%)"):
        print(f"\n=== {mk}  +  {dk} ===")
        rep(f"{mk} 100%, no dips", equity(1.0, M[mcol], 0, D[dk]))
        for w in (0.3, 0.5, 0.7, 1.0):
            for L in (3, 5, 10):
                st, y = rep(f"{mk} {w:.0%} + dips {L}x", equity(w, M[mcol], L, D[dk]))
                rows.append(dict(mom=mk, dips=dk, w_mom=w, L_dip=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "cvar95_m")}, beta=st.get("beta"), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
df = pd.DataFrame(rows); df.to_csv(RESULTS / "dip_momentum_VOLVUE.csv", index=False)
print("\nbest by Calmar:"); print(df.sort_values("calmar", ascending=False).head(8)[["mom", "dips", "w_mom", "L_dip", "cagr", "sharpe", "maxdd", "calmar", "worst_month"]].round(3).to_string())
print("best by Sharpe:"); print(df.sort_values("sharpe", ascending=False).head(6)[["mom", "dips", "w_mom", "L_dip", "cagr", "sharpe", "maxdd", "calmar", "worst_month"]].round(3).to_string())
print("DONE")
