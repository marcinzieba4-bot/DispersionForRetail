"""Critical review of the sector-ETF combo (line S: sector top-3 100% + book 3.75x + TLT 38% + straddle 75%, vol-path marks)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS, CACHE
uni = load_universe()
V = pd.read_csv(RESULTS / "volpath_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = V.index; book = V["book_volpath"]
A_ = pd.read_csv(RESULTS / "alloc_atoms_daily_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0); book_u = A_[["PW", "CO", "EV"]].sum(axis=1)
SEC = pd.read_csv(RESULTS / "sector_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0)
S3 = SEC["sector mom top3, SPY>200d, trail 15%"]; S3n = SEC["sector mom top3, no filter"]; S4 = SEC["sector mom top4, SPY>200d, trail 15%"]; S3o = SEC["sector mom top3, own 200d + SPY>200d"]
H = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0); TLT = H["TLT trend 200d"]; TLTbh = H["TLT buy & hold"]
T_ = pd.read_csv(RESULTS / "tlt_straddle_legs_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0); STR = T_["short straddle W, only IV pct > 50%"]; STRu = T_["short straddle, hedge W"]
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
st = lambda e: metrics.summary(e, uni.spy)
L = [(1.0, S3), (3.75, book), (0.375, TLT), (0.75, STR)]; NAMES = ["sector top3 100%", "book 3.75x", "TLT 38%", "straddle 75%"]
e = equity(L); sL = st(e); print(f"line S: cagr={sL['cagr']:+.1%} sharpe={sL['sharpe']:.2f} maxdd={sL['maxdd']:+.1%} calmar={sL['calmar']:.2f}")
print("\n=== 1. attribution ===")
for i, name in enumerate(NAMES):
    s1 = st(equity([L[i]])); s2 = st(equity([p for j, p in enumerate(L) if j != i]))
    print(f"{name:18s} alone: cagr={s1['cagr']:+.1%} sharpe={s1['sharpe']:.2f} maxdd={s1['maxdd']:+.1%} | without it: cagr={s2['cagr']:+.1%} sharpe={s2['sharpe']:.2f} maxdd={s2['maxdd']:+.1%} calmar={s2['calmar']:.2f}")
s0 = st(equity(L, cash=0.0)); print(f"cash yield: {sL['cagr'] - s0['cagr']:+.1%}/yr (ex-cash cagr {s0['cagr']:+.1%}, sharpe {s0['sharpe']:.2f})")
print("\n=== 2. sub-periods ===")
for a, b in (("2007-01-01", "2012-12-31"), ("2013-01-01", "2019-12-31"), ("2020-01-01", "2026-12-31"), ("2007-01-01", "2016-12-31"), ("2017-01-01", "2026-12-31"), ("2007-01-01", "2022-12-31"), ("2023-01-01", "2026-12-31")):
    s = st(equity(L, start=a, end=b)); print(f"{a[:4]}-{b[:4]}: cagr={s['cagr']:+.1%} vol={s['vol']:.1%} sharpe={s['sharpe']:.2f} maxdd={s['maxdd']:+.1%} calmar={s['calmar']:.2f}")
mr = metrics.monthly_returns(e); roll = mr.rolling(36).apply(lambda x: x.mean() / x.std() * np.sqrt(12))
print(f"rolling 36m Sharpe: min={roll.min():.2f} 10th={roll.quantile(.1):.2f} median={roll.median():.2f}; months < 0.5: {(roll < 0.5).sum()} of {roll.notna().sum()}")
print("\n=== 3. block bootstrap (6m blocks, 2000 draws), 5/50/95 ===")
rng = np.random.default_rng(0); x = mr.to_numpy(); n = len(x); bl = 6; sh = []; dd = []; cg = []
for _ in range(2000):
    idx = np.concatenate([np.arange(i, i + bl) % n for i in rng.integers(0, n, n // bl + 1)])[:n]
    r = x[idx]; eq = np.cumprod(1 + r); sh.append(r.mean() / r.std() * np.sqrt(12)); dd.append((eq / np.maximum.accumulate(eq) - 1).min()); cg.append(eq[-1] ** (12 / n) - 1)
print(f"Sharpe {np.percentile(sh, 5):.2f} / {np.percentile(sh, 50):.2f} / {np.percentile(sh, 95):.2f} | CAGR {np.percentile(cg, 5):+.1%} / {np.percentile(cg, 50):+.1%} / {np.percentile(cg, 95):+.1%} | maxDD {np.percentile(dd, 5):+.1%} / {np.percentile(dd, 50):+.1%} / {np.percentile(dd, 95):+.1%}")
print("\n=== 4. sensitivity to the in-sample choices ===")
for name, parts in (("sector basket without the SPY 200d filter", [(1.0, S3n), (3.75, book), (0.375, TLT), (0.75, STR)]),
                    ("sector top 4 instead of 3", [(1.0, S4), (3.75, book), (0.375, TLT), (0.75, STR)]),
                    ("sector top 3 + own 200d", [(1.0, S3o), (3.75, book), (0.375, TLT), (0.75, STR)]),
                    ("book unconditional (no correlation regimes)", [(1.0, S3), (3.75, book_u), (0.375, TLT), (0.75, STR)]),
                    ("TLT buy & hold instead of 200d", [(1.0, S3), (3.75, book), (0.375, TLTbh), (0.75, STR)]),
                    ("straddle without IV-rank filter", [(1.0, S3), (3.75, book), (0.375, TLT), (0.75, STRu)]),
                    ("ES 100% instead of the sector basket", [(1.0, ES), (3.75, book), (0.375, TLT), (0.75, STR)]),
                    ("all plain alternatives", [(1.0, S3n), (3.75, book_u), (0.375, TLTbh), (0.75, STRu)])):
    s = st(equity(parts)); print(f"{name:44s} cagr={s['cagr']:+.1%} sharpe={s['sharpe']:.2f} maxdd={s['maxdd']:+.1%} calmar={s['calmar']:.2f} worst_m={s['worst_month']:+.1%}")
print("\n=== 5. tails ===")
sm = metrics.monthly_returns(uni.spy.reindex(days).ffill()); j = mr.index.intersection(sm.index)
print(f"SPY worst 10 months: combo mean {mr[sm[j].nsmallest(10).index].mean():+.1%} (SPY {sm[j].nsmallest(10).mean():+.1%})")
for name, p in (("sector basket", S3), ("book", book), ("TLT", TLT), ("straddle", STR)):
    pm = metrics.monthly_returns(1e6 + p); w = pm.nsmallest(10).index; print(f"{name:14s} worst 10 months: sleeve {pm[w].mean():+.1%} (1x), combo {mr[w].mean():+.1%}")
print("combo worst 5 months:", ", ".join(f"{d.strftime('%Y-%m')} {v:+.1%}" for d, v in mr.nsmallest(5).items()))
# which sleeve caused each of the combo's worst months
parts_m = {n: metrics.monthly_returns(1e6 + w * p) for n, (w, p) in zip(NAMES, L)}
for d in mr.nsmallest(5).index:
    print("   " + d.strftime('%Y-%m') + ": " + " ".join(f"{n}={parts_m[n].get(d, np.nan):+.1%}" for n in NAMES))
print("DONE")
