"""Stop-loss variants for the top-5 momentum sleeve, standalone and inside the combo."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.momentum import momentum_leg
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True)
book = hb["honest_book_1x"] - 1e6; days = book.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
legs = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)
def compounded(p):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        q = p.loc[a:b]; seg = E * (1 + (q - q.iloc[0]) / 1e6); out.loc[a:b] = seg.values; E = max(float(seg.iloc[-1]), 1.0)
    return out.ffill()
def blend(L, w, q, w_book=0.7):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; w_ = book.loc[a:b]; qq = q.loc[a:b]
        seg = E * (c / c.iloc[0]) + w * E / 1e6 * (qq - qq.iloc[0]) + w_book * E / 1e6 * L * (w_ - w_.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:40s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2009={y.get(2009):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}", flush=True)
    return st, y
VAR = {
    "no stop": {}, "name stop -10%": dict(stop=0.10), "name stop -15%": dict(stop=0.15), "name stop -20%": dict(stop=0.20),
    "trailing -10%": dict(trail=0.10), "trailing -15%": dict(trail=0.15), "trailing -20%": dict(trail=0.20),
    "sleeve stop -10%": dict(sleeve_stop=0.10), "sleeve stop -15%": dict(sleeve_stop=0.15),
    "name -15% + sleeve -10%": dict(stop=0.15, sleeve_stop=0.10), "trailing -15% + sleeve -10%": dict(trail=0.15, sleeve_stop=0.10),
    "SPY>200d trend filter": dict(trend=200), "trend + trailing -15%": dict(trend=200, trail=0.15),
}
mom, rows = {}, []
print("=== top-5 12-1 momentum with stops, standalone, 100% of equity compounded, ex cash ===")
for k, kw in VAR.items():
    p, hold, _ = momentum_leg(uni, n=5, **kw); p = p.reindex(days).ffill().fillna(0.0); mom[k] = p
    st, y = rep(k, compounded(p)); rows.append(dict(variant=k, scope="standalone", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, y2008=y.get(2008), y2020=y.get(2020)))
pd.DataFrame(mom).to_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv")
print("\n=== inside the combo: w x equity in the sleeve + 70% of equity in the honest book at L x, cash on all equity ===")
rep("ES 30% + book 3x", blend(3, 0.3, legs["ES"].reindex(days).ffill()))
rep("ES 60% + book 3x", blend(3, 0.6, legs["ES"].reindex(days).ffill()))
for k in ("no stop", "name stop -15%", "trailing -15%", "sleeve stop -10%", "trailing -15% + sleeve -10%", "SPY>200d trend filter", "trend + trailing -15%"):
    for w, L in ((0.3, 3), (0.3, 4), (0.6, 3)):
        st, y = rep(f"mom5 {k} w={w:.0%} L={L}", blend(L, w, mom[k])); rows.append(dict(variant=k, scope=f"w={w} L={L}", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, y2008=y.get(2008), y2020=y.get(2020)))
pd.DataFrame(rows).to_csv(RESULTS / "momentum_stops_VOLVUE.csv", index=False)
print("DONE")
