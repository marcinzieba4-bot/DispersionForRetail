"""Honest versions of the regime allocator: (a) walk-forward refit (weights fitted only on data before each year),
(b) a hand-set low-parameter table; both at 1x / 3x / 5x with margin, from the saved atoms."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
A = pd.read_csv(RESULTS / "alloc_atoms_daily_VOLVUE.csv", index_col=0, parse_dates=True); days = A.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
cy = pd.DataFrame({k: [float(A[k].loc[b] - A[k].loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k in A.columns}, index=ent[:-1])
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
b5 = sig.bcor.rolling(5).mean(); bp = b5.rolling(504, min_periods=250).rank(pct=True).shift(1); v5 = sig.vix.rolling(5).mean().shift(1)
spy = uni.spy; tr = (spy > spy.rolling(200).mean()).shift(1)
S = pd.DataFrame({"bp": bp, "vix": v5, "trend": tr}).reindex(cy.index, method="ffill")
S["corr"] = pd.cut(S.bp, [-0.01, 0.33, 0.75, 1.01], labels=["lo", "mid", "hi"]).astype(str); S.loc[S.bp.isna(), "corr"] = "mid"
S["trend"] = S.trend.fillna(True).astype(bool); S["cell"] = S["corr"] + "/" + np.where(S.trend, "up", "down")
cells = ["lo/up", "lo/down", "mid/up", "mid/down", "hi/up", "hi/down"]
PM1 = {"PW": 0.06, "CO": 0.06, "IP": 0.05, "EV": 0.03, "MOM": 0.25, "ES": 0.06}
def fit(idx, min_sh=0.3, target=0.10, cap=1.5):
    W = {}
    for c in cells:
        m = (S.cell == c) & idx
        if m.sum() < 8:
            W[c] = {k: 0.0 for k in cy.columns}; continue
        x = cy.loc[m]; mu = x.mean() * 12; sd = x.std() * np.sqrt(12); sh = mu / sd
        w = {k: (sh[k] / sd[k] if sh[k] > min_sh else 0.0) for k in cy.columns}
        p = (x * pd.Series(w)).sum(axis=1); v = p.std() * np.sqrt(12); s = target / v if v > 0 else 0.0
        W[c] = {k: float(np.clip(w[k] * s, 0, cap)) for k in cy.columns}
    return W
# (a) walk-forward: weights for year Y fitted on all cycles before Y (first fit after 5 years)
wf = {}
for a in cy.index:
    y = a.year
    if y < 2012:
        continue
    W = fit(cy.index < pd.Timestamp(f"{y}-01-01")); wf[a] = W[S.loc[a, "cell"]]
wf = pd.DataFrame(wf).T
# (b) hand-set table: few parameters, economically motivated
SIMPLE = {
    "hi/up":    dict(PW=1.0, CO=0.0, IP=0.5, EV=1.5, MOM=0.0, ES=0.3),
    "hi/down":  dict(PW=1.0, CO=0.0, IP=0.5, EV=1.5, MOM=0.0, ES=0.0),
    "mid/up":   dict(PW=0.0, CO=0.0, IP=0.5, EV=1.5, MOM=0.3, ES=0.3),
    "mid/down": dict(PW=0.5, CO=0.0, IP=0.5, EV=1.5, MOM=0.0, ES=0.0),
    "lo/up":    dict(PW=0.0, CO=1.0, IP=0.0, EV=1.0, MOM=0.3, ES=0.3),
    "lo/down":  dict(PW=0.0, CO=0.0, IP=0.0, EV=1.0, MOM=0.0, ES=0.0),
}
STATIC = {c: dict(PW=0.0, CO=0.0, IP=0.0, EV=0.0, MOM=0.0, ES=0.3) for c in cells}   # 30% ES only, for reference
def equity(L, wfun):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; pm = []
    for a, b in zip(ent[:-1], ent[1:]):
        w = wfun(a)
        if w is None:
            continue
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0])
        for k, wk in w.items():
            q = A[k].loc[a:b]; seg = seg + L * wk * E / 1e6 * (q - q.iloc[0])
        pm.append(L * sum(wk * PM1[k] * (1.5 if k != "ES" else 1.0) for k, wk in w.items()))
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.dropna().ffill(), pd.Series(pm)
def rep(name, e, pm):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:34s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008, np.nan):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy'] if 'Covid_2020' in cr.index else np.nan:+.0%} | PM peak={pm.max():.0%} p95={pm.quantile(.95):.0%}", flush=True)
    return st, y
rows = []
print("=== (a) walk-forward refit allocator, 2012-2026 (weights only from past data), cash yield on all equity ===")
for L in (1, 3, 5):
    e, pm = equity(L, lambda a: wf.loc[a].to_dict() if a in wf.index else None); st, y = rep(f"walk-forward L={L}x", e, pm); rows.append(dict(alloc="walk-forward 2012-26", L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, pm_peak=pm.max()))
e, pm = equity(1, lambda a: STATIC[S.loc[a, "cell"]] if a >= pd.Timestamp("2012-01-01") else None); rep("ref: 30% ES only, 2012-26", e, pm)
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True)["honest_book_1x"] - 1e6
def ref_combo(a0):
    E = 1e6; out = pd.Series(np.nan, index=days)
    for a, b in zip(ent[:-1], ent[1:]):
        if a < a0: continue
        c = cash_acc.loc[a:b]; q = A["ES"].loc[a:b]; w_ = hb.loc[a:b]
        seg = E * (c / c.iloc[0]) + 0.3 * E / 1e6 * (q - q.iloc[0]) + 0.7 * E / 1e6 * 3 * (w_ - w_.iloc[0]); out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
    return out.dropna()
rep("ref: 30% ES + book 3x, 2012-26", ref_combo(pd.Timestamp("2012-01-01")), pd.Series([0.41]))
print("\n=== (b) hand-set table (6 cells, weights fixed by hand), full sample 2007-2026 ===")
for c in cells:
    print(f"   {c:9s} " + " ".join(f"{k}={v:.1f}" for k, v in SIMPLE[c].items()) + f"  gross={sum(SIMPLE[c].values()):.1f}")
for L in (1, 2, 3, 5):
    e, pm = equity(L, lambda a: SIMPLE[S.loc[a, "cell"]]); st, y = rep(f"hand table L={L}x", e, pm); rows.append(dict(alloc="hand table", L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, pm_peak=pm.max()))
    if L in (1, 5): print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
e1, _ = equity(1, lambda a: SIMPLE[S.loc[a, "cell"]]); h = metrics.summary(e1.loc[:"2016-12-31"]); h2 = metrics.summary(e1.loc["2017":])
print(f"   hand table 1x by half: 2007-16 cagr={h['cagr']:+.1%} sharpe={h['sharpe']:.2f} maxdd={h['maxdd']:+.1%} | 2017-26 cagr={h2['cagr']:+.1%} sharpe={h2['sharpe']:.2f} maxdd={h2['maxdd']:+.1%}")
rep("ref: 30% ES + book 3x, 2007-26", ref_combo(ent[0]), pd.Series([0.41]))
pd.DataFrame(rows).to_csv(RESULTS / "alloc_honest_VOLVUE.csv", index=False)
print("DONE")
