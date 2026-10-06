"""Critical checks on the combined book (line A, k=1): attribution, sub-period stability, bootstrap, and
sensitivity to the choices that were made in-sample."""
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
B = pd.read_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv", index_col=0, parse_dates=True)
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
M10b = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trail15"].reindex(days).ffill().fillna(0.0)
H = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True); TLT = H["TLT trend 200d"].reindex(days).ffill().fillna(0.0); TLTbh = H["TLT buy & hold"].reindex(days).ffill().fillna(0.0)
S_ = pd.read_csv(RESULTS / "tlt_straddle_legs_VOLVUE.csv", index_col=0, parse_dates=True); STR = S_["short straddle W, only IV pct > 50%"].reindex(days).ffill().fillna(0.0); STRu = S_["short straddle, hedge W"].reindex(days).ffill().fillna(0.0)
ES = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)["ES"].reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
def equity(parts, cash=1.0, start=None, end=None):
    E = 1e6; out = pd.Series(np.nan, index=days); first = True
    for a, b in zip(ent[:-1], ent[1:]):
        if (start and a < pd.Timestamp(start)) or (end and a > pd.Timestamp(end)): continue
        if first: out.loc[a] = E; first = False
        c = cash_acc.loc[a:b]; seg = E * (1 + (c / c.iloc[0] - 1) * cash)
        for w, p in parts:
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
    return out.dropna().ffill()
def st(e):
    s = metrics.summary(e, uni.spy); return s
A = [(0.75, M10), (3.75, book0), (0.375, TLT), (0.75, STR)]; NAMES = ["momentum 75%", "book 3.75x", "TLT 38%", "straddle 75%"]
e = equity(A); sA = st(e); print(f"line A: cagr={sA['cagr']:+.1%} sharpe={sA['sharpe']:.2f} maxdd={sA['maxdd']:+.1%}")
print("\n=== 1. attribution: each sleeve alone at its weight (with cash), and the combo without it ===")
for i, name in enumerate(NAMES):
    part = A[i]; s1 = st(equity([part])); s2 = st(equity([p for j, p in enumerate(A) if j != i]))
    print(f"{name:14s} alone: cagr={s1['cagr']:+.1%} sharpe={s1['sharpe']:.2f} maxdd={s1['maxdd']:+.1%} | combo without it: cagr={s2['cagr']:+.1%} sharpe={s2['sharpe']:.2f} maxdd={s2['maxdd']:+.1%}")
s0 = st(equity(A, cash=0.0)); print(f"cash yield contribution: {sA['cagr'] - s0['cagr']:+.1%}/yr (ex-cash cagr {s0['cagr']:+.1%}, sharpe {s0['sharpe']:.2f})")
print("\n=== 2. sub-periods ===")
for a, b in (("2007-01-01", "2012-12-31"), ("2013-01-01", "2019-12-31"), ("2020-01-01", "2026-12-31"), ("2007-01-01", "2016-12-31"), ("2017-01-01", "2026-12-31"), ("2007-01-01", "2022-12-31")):
    s = st(equity(A, start=a, end=b)); print(f"{a[:4]}-{b[:4]}: cagr={s['cagr']:+.1%} vol={s['vol']:.1%} sharpe={s['sharpe']:.2f} maxdd={s['maxdd']:+.1%} calmar={s['calmar']:.2f}")
mr = metrics.monthly_returns(e); roll = mr.rolling(36).apply(lambda x: x.mean() / x.std() * np.sqrt(12))
print(f"rolling 36m Sharpe: min={roll.min():.2f} 10th pct={roll.quantile(.1):.2f} median={roll.median():.2f}; months with 36m Sharpe < 0.5: {(roll < 0.5).sum()} of {roll.notna().sum()}")
print("\n=== 3. block bootstrap of monthly returns (6-month blocks, 2000 draws): 90% intervals ===")
rng = np.random.default_rng(0); x = mr.to_numpy(); n = len(x); bl = 6; sh = []; dd = []; cg = []
for _ in range(2000):
    idx = np.concatenate([np.arange(i, i + bl) % n for i in rng.integers(0, n, n // bl + 1)])[:n]
    r = x[idx]; eq = np.cumprod(1 + r); sh.append(r.mean() / r.std() * np.sqrt(12)); dd.append((eq / np.maximum.accumulate(eq) - 1).min()); cg.append(eq[-1] ** (12 / n) - 1)
print(f"Sharpe 5-50-95%: {np.percentile(sh, 5):.2f} / {np.percentile(sh, 50):.2f} / {np.percentile(sh, 95):.2f} | CAGR: {np.percentile(cg, 5):+.1%} / {np.percentile(cg, 50):+.1%} / {np.percentile(cg, 95):+.1%} | maxDD: {np.percentile(dd, 5):+.1%} / {np.percentile(dd, 50):+.1%} / {np.percentile(dd, 95):+.1%}")
print("\n=== 4. sensitivity to the in-sample choices (swap one choice for its plain alternative) ===")
for name, parts in (("momentum without trend filter", [(0.75, M10b), (3.75, book0), (0.375, TLT), (0.75, STR)]),
                    ("TLT buy & hold instead of 200d", [(0.75, M10), (3.75, book0), (0.375, TLTbh), (0.75, STR)]),
                    ("straddle without IV-rank filter", [(0.75, M10), (3.75, book0), (0.375, TLT), (0.75, STRu)]),
                    ("book unconditional (no regime masks)", [(0.75, M10), (3.75, B["PW|base"] * 0 + (pd.read_csv(RESULTS / 'alloc_atoms_daily_VOLVUE.csv', index_col=0, parse_dates=True)[['PW', 'CO', 'EV']].sum(axis=1).reindex(days).ffill().fillna(0.0))), (0.375, TLT), (0.75, STR)]),
                    ("ES 75% instead of momentum", [(0.75, ES), (3.75, book0), (0.375, TLT), (0.75, STR)]),
                    ("all four plain alternatives", [(0.75, M10b), (3.75, pd.read_csv(RESULTS / 'alloc_atoms_daily_VOLVUE.csv', index_col=0, parse_dates=True)[['PW', 'CO', 'EV']].sum(axis=1).reindex(days).ffill().fillna(0.0)), (0.375, TLTbh), (0.75, STRu)])):
    s = st(equity(parts)); print(f"{name:40s} cagr={s['cagr']:+.1%} sharpe={s['sharpe']:.2f} maxdd={s['maxdd']:+.1%} calmar={s['calmar']:.2f} worst_m={s['worst_month']:+.1%}")
print("\n=== 5. tail dependence: combo monthly return in SPY's 10 worst months and in the 10 worst months of each sleeve ===")
sm = metrics.monthly_returns(uni.spy.reindex(days).ffill()); j = mr.index.intersection(sm.index)
print(f"SPY worst 10 months: combo mean {mr[sm[j].nsmallest(10).index].mean():+.1%}, SPY mean {sm[j].nsmallest(10).mean():+.1%}")
for name, p in (("momentum", M10), ("book", book0), ("TLT", TLT), ("straddle", STR)):
    pm = metrics.monthly_returns(1e6 + p.reindex(days).ffill()); w = pm.nsmallest(10).index
    print(f"{name:9s} worst 10 months: sleeve mean {pm[w].mean():+.1%} (1x), combo mean {mr[w].mean():+.1%}")
print("DONE")
