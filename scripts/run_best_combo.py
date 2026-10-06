"""The best combo: 10-name momentum (trend + trail 15%) + base regime book + TLT (200d trend) + short TLT straddle
(weekly hedge, IV rank > 50%), scaled; full risk report, PM usage, Reg-T fallback."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
L_ = pd.read_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = L_.index; book0 = L_["book_base"]
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
TLT = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True)["TLT trend 200d"].reindex(days).ffill().fillna(0.0)
STR = pd.read_csv(RESULTS / "tlt_straddle_legs_VOLVUE.csv", index_col=0, parse_dates=True)["short straddle W, only IV pct > 50%"].reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
pm_comp = pd.read_csv(RESULTS / "regime_margin_components_VOLVUE.csv", index_col=0, parse_dates=True)
pm_book = pm_comp[[c for c in pm_comp.columns if "straddle |" not in c]].sum(axis=1).reindex(ent[:-1]).fillna(0.0)
on = lambda p: pd.Series({a: float(p.loc[b] - p.loc[a]) != 0.0 for a, b in zip(ent[:-1], ent[1:])})
tlt_on, str_on = on(TLT), on(STR)
def run(wm, lb, wt, ws, tlt_pm=0.12, regt=False):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; usage = pd.Series(np.nan, index=days)
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0])
        for w, p in ((wm, M10), (lb, book0), (wt, TLT), (ws, STR)):
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        if regt:
            req = E * (0.50 * wm + 0.40 * lb * (1 if float(pm_book.get(a, 0)) > 0 else 0) * 1.0 + tlt_pm * wt * float(tlt_on.get(a, 1)) + 0.20 * ws * float(str_on.get(a, 1)))
        else:
            req = E * (0.25 * wm + 1.5 * float(pm_book.get(a, 0.0)) * lb + tlt_pm * wt * float(tlt_on.get(a, 1)) + 1.5 * 0.07 * ws * float(str_on.get(a, 1)))
        usage.loc[a:b] = (req / seg).values; out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; usage.loc[b:] = 9.9; break
    return out.ffill(), usage.ffill()
def rep(name, e, u):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:46s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} mdd_me={st['maxdd_monthly']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} cvar={st['cvar95_m']:+.1%} skew={st['skew_m']:+.1f} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%} | usage med={u.median():.0%} p95={u.quantile(.95):.0%} max={u.max():.0%}", flush=True)
    return st, y, cr
rows = []
print("=== best combo, scaled: mom 75% + book 3.75x + TLT 38% + straddle 75% at k=1 (PM usage: stocks 25%, TLT 12%, book TIMS x1.5, straddle 7% x1.5) ===")
for k in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
    e, u = run(0.75 * k, 3.75 * k, 0.375 * k, 0.75 * k); st, y, cr = rep(f"k={k:.2f}", e, u)
    rows.append(dict(variant="PM, TLT shares", k=k, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}, pm_med=u.median(), pm_p95=u.quantile(.95), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
print("\n--- same, TLT via /ZB futures (4% margin) ---")
for k in (1.0, 1.25, 1.5):
    e, u = run(0.75 * k, 3.75 * k, 0.375 * k, 0.75 * k, tlt_pm=0.04); st, y, cr = rep(f"k={k:.2f}, TLT futures", e, u)
    rows.append(dict(variant="PM, TLT futures", k=k, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}, pm_med=u.median(), pm_p95=u.quantile(.95), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
print("\n--- without the straddle (reference) ---")
e, u = run(0.75, 3.75, 0.375, 0.0); rep("k=1, no straddle", e, u)
print("\n--- Reg-T account (stocks 50%, book 40% of notional per 1x, TLT shares 50% / futures 4%, straddle 20%) ---")
for wm, lb, wt, ws, tp in ((0.45, 0.75, 0.22, 0.45, 0.50), (0.45, 0.75, 0.22, 0.45, 0.04), (0.6, 1.0, 0.3, 0.6, 0.04), (0.5, 0.75, 0.25, 0.25, 0.04)):
    e, u = run(wm, lb, wt, ws, tlt_pm=tp, regt=True); st, y, cr = rep(f"RegT mom {wm:.0%} book {lb:.2f}x TLT {wt:.0%}{' fut' if tp < 0.1 else ''} str {ws:.0%}", e, u)
    rows.append(dict(variant="RegT", k=np.nan, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}, pm_med=u.median(), pm_p95=u.quantile(.95), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
pd.DataFrame(rows).to_csv(RESULTS / "best_combo_VOLVUE.csv", index=False)
print("\n=== the two headline lines in full ===")
for name, args in (("A: survives PM (k=1): mom 75% book 3.75x TLT 38% str 75%", (0.75, 3.75, 0.375, 0.75, 0.04)), ("B: 20% target (k=1.25): mom 94% book 4.7x TLT 47% str 94%", (0.9375, 4.6875, 0.46875, 0.9375, 0.04))):
    e, u = run(*args); st, y, cr = rep(name, e, u)
    print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
    print("   crises: " + " ".join(f"{i}={r.strategy:+.0%}(spy {r.spy:+.0%})" for i, r in cr.iterrows()))
    print(f"   months with PM usage > 80%: {(u.resample('ME').max() > 0.8).sum()}, > 100%: {(u.resample('ME').max() > 1.0).sum()}; VaR95 m={st['var95_m']:+.1%} pos months={st['pct_pos_months']:.0%} dd_days={st['dd_duration_days']}")
    (e).rename("equity").to_frame().join(u.rename("pm_usage")).to_csv(RESULTS / f"best_combo_{name[0]}_equity_VOLVUE.csv")
print("DONE")
