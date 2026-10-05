"""Momentum sleeve (5 best 12-1 momentum S&P names, monthly) standalone and as the equity leg of the combo."""
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
    """equity path compounding the fixed-notional sleeve P&L cycle by cycle (what 100% of equity in the sleeve does)."""
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        q = p.loc[a:b]; seg = E * (1 + (q - q.iloc[0]) / 1e6); out.loc[a:b] = seg.values; E = max(float(seg.iloc[-1]), 1.0)
    return out.ffill()
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:44s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2009={y.get(2009):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}{extra}", flush=True)
    return st
print("=== momentum variants, standalone, 100% of equity compounded, ex cash (point-in-time S&P members, ADV > $20M, 5 bp/side) ===")
VAR = {
    "mom 12-1, top 5": dict(n=5), "mom 12-1, top 10": dict(n=10), "mom 12-1, top 20": dict(n=20),
    "mom 6-1, top 5": dict(n=5, lookback=126), "mom 12-1 vol-scaled, top 5": dict(n=5, vol_scale=True),
    "mom 12-1, top 5, top-100 mcap only": dict(n=5, universe="top100"), "mom 12-1, top 10, top-100 mcap only": dict(n=10, universe="top100"),
}
mom = {}
for k, kw in VAR.items():
    p, hold, cov = momentum_leg(uni, **kw); p = p.reindex(days).ffill().fillna(0.0); mom[k] = p
    rep(k, compounded(p), f" | coverage {cov.iloc[:24].mean():.0%}->{cov.iloc[-24:].mean():.0%}")
rep("ES (TR - cash)", compounded(legs["ES"].reindex(days).ffill()))
pd.DataFrame(mom).to_csv(RESULTS / "momentum_legs_VOLVUE.csv")
_, hold, _ = momentum_leg(uni, n=5)
print("\nlast 6 picks:", {str(k.date()): v for k, v in list(hold.items())[-6:]})
turn = np.mean([len(set(a) - set(b)) for a, b in zip(list(hold.values())[1:], list(hold.values())[:-1])])
print(f"avg names replaced per month: {turn:.1f} of 5")
pm_book = pd.read_csv(RESULTS / "regime_margin_components_VOLVUE.csv", index_col=0, parse_dates=True)
pm_b = pm_book[[c for c in pm_book.columns if "straddle |" not in c]].sum(axis=1).max()
def blend(L, w, q, w_book=0.7):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; w_ = book.loc[a:b]; qq = q.loc[a:b]
        seg = E * (c / c.iloc[0]) + w * E / 1e6 * (qq - qq.iloc[0]) + w_book * E / 1e6 * L * (w_ - w_.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
print("\n=== combo: w x equity in the leg + 70% of equity in the honest book at L x, cash on all equity ===")
rows = []
for lk, q in [("ES", legs["ES"].reindex(days).ffill()), ("mom 12-1 top 5", mom["mom 12-1, top 5"]), ("mom 12-1 top 10", mom["mom 12-1, top 10"]), ("mom 12-1 top 5, top-100 mcap", mom["mom 12-1, top 5, top-100 mcap only"])]:
    for w in (0.3, 0.45, 0.6):
        for L in (0, 3, 4, 10):
            e = blend(L, w, q); st = rep(f"{lk} w={w:.0%} L={L}", e)
            pm = 0.7 * L * pm_b * 1.5 + w * (0.06 if lk == "ES" else 0.25); y = metrics.yearly(e.where(e > 0).dropna())
            rows.append(dict(leg=lk, w=w, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month")}, beta=st.get("beta", np.nan), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022), pm_peak=pm))
pd.DataFrame(rows).to_csv(RESULTS / "momentum_combo_VOLVUE.csv", index=False)
print("DONE")
