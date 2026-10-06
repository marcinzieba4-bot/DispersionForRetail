"""Momentum (10 names, trend + trail 15%) + base regime book + TLT (200d trend filter), scaled up to the highest
leverage a tastytrade portfolio-margin account survives. Survival = portfolio margin requirement (TIMS-style
stress x1.5 house factor; stocks 25%, TLT 12%) never exceeds the daily equity (no margin call). Also shown: the
highest scale that stays under a 60% usage rail. Full cash yield, realistic costs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
L = pd.read_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = L.index
book0 = L["book_base"]
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
H = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True); TLT = H["TLT trend 200d"].reindex(days).ffill().fillna(0.0)
TLT_T = H["TLT trend 200d + trail 10%"].reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
pm_comp = pd.read_csv(RESULTS / "regime_margin_components_VOLVUE.csv", index_col=0, parse_dates=True)
pm_book = pm_comp[[c for c in pm_comp.columns if "straddle |" not in c]].sum(axis=1).reindex(ent[:-1]).fillna(0.0)   # per 1x book, by cycle
tlt_on = pd.Series({a: float(TLT.loc[b] - TLT.loc[a]) != 0.0 for a, b in zip(ent[:-1], ent[1:])})
def run(w_mom, L_book, w_tlt, tlt=TLT, house=1.5):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; usage = pd.Series(np.nan, index=days)
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0])
        for w, p in ((w_mom, M10), (L_book, book0), (w_tlt, tlt)):
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        req = E * (0.25 * w_mom + house * float(pm_book.get(a, 0.0)) * L_book + 0.12 * w_tlt * float(tlt_on.get(a, True)))
        usage.loc[a:b] = (req / seg).values
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; usage.loc[b:] = 9.9; break
    return out.ffill(), usage.ffill()
def rep(name, e, u, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:40s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%} | PM usage median={u.median():.0%} p95={u.quantile(.95):.0%} max={u.max():.0%}{extra}", flush=True)
    return st, y
rows = []
BASES = {"A: mom 100% + book 5x + TLT 50%": (1.0, 5.0, 0.5), "B: mom 60% + book 3x + TLT 30%": (0.6, 3.0, 0.3), "C: mom 100% + book 5x + TLT 100%": (1.0, 5.0, 1.0), "D: mom 100% + book 3x + TLT 50%": (1.0, 3.0, 0.5)}
for bk, (wm, lb, wt) in BASES.items():
    print(f"\n=== {bk}, scaled by k ===")
    best100 = best60 = None
    for k in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
        e, u = run(wm * k, lb * k, wt * k)
        st, y = rep(f"k={k:.2f}: mom {wm*k:.0%} book {lb*k:.1f}x TLT {wt*k:.0%}", e, u)
        rows.append(dict(base=bk, k=k, w_mom=wm * k, L_book=lb * k, w_tlt=wt * k, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, pm_med=u.median(), pm_p95=u.quantile(.95), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
        if u.max() <= 1.0: best100 = k
        if u.max() <= 0.6: best60 = k
    print(f"   highest k with PM usage always <= 100%: {best100}   | always <= 60% rail: {best60}")
pd.DataFrame(rows).to_csv(RESULTS / "tlt_maxlev_VOLVUE.csv", index=False)
print("\n=== the survivor lines in detail ===")
for name, (wm, lb, wt, tlt) in {"A at k=1.25": (1.25, 6.25, 0.625, TLT), "A at k=1.5": (1.5, 7.5, 0.75, TLT), "A at k=1.5, TLT trend + trail 10%": (1.5, 7.5, 0.75, TLT_T), "B at k=2": (1.2, 6.0, 0.6, TLT)}.items():
    e, u = run(wm, lb, wt, tlt); st, y = rep(name, e, u)
    print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
    print(f"   usage: cycles with usage > 60%: {(u.resample('ME').max() > 0.6).sum()} months, > 80%: {(u.resample('ME').max() > 0.8).sum()} months, > 100%: {(u.resample('ME').max() > 1.0).sum()} months")
print("DONE")
