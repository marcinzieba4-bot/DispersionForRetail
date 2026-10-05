"""Regime analysis: monthly sleeve P&L (results/regime_sleeves_VOLVUE.csv) conditioned on entry-date signals."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.data.paths import RESULTS
pnl = pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True)
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
S = sig.reindex(pnl.index, method="ffill")
BOOKS = {
    "two-wing straddle dispersion": pnl.idx_short_straddle + pnl.sn_long_straddle,
    "put-wing dispersion": pnl.idx_short_put25 + pnl.sn_long_put25,
    "call dispersion (verticals)": pnl.idx_short_call30 + pnl.sn_long_vertical_30_10,
    "call dispersion (outright 30d)": pnl.idx_short_call30 + pnl.sn_long_call30,
    "short idx straddle": pnl.idx_short_straddle, "short idx put25": pnl.idx_short_put25, "short idx call30": pnl.idx_short_call30,
    "long sn straddles": pnl.sn_long_straddle, "long sn put25": pnl.sn_long_put25, "long sn call30": pnl.sn_long_call30,
    "event short straddles": pnl.sn_short_straddle_event,
}
def stats(p):
    p = p.dropna()
    return dict(n=len(p), ann=p.mean() * 12, sharpe=p.mean() / p.std() * np.sqrt(12) if p.std() > 0 else np.nan, hit=(p > 0).mean(), worst=p.min())
print("=== unconditional (monthly P&L, % of 1M fixed notional, ex cash, retail costs) ===")
for b, p in BOOKS.items():
    st = stats(p); print(f"{b:32s} ann={st['ann']:+.2%} sharpe={st['sharpe']:+.2f} hit={st['hit']:.0%} worst={st['worst']:+.1%}")
SIGS = ["cor1m", "cor3m", "bcor", "cor1m_p", "bcor_p", "corr_prem", "cor_prem_cboe", "rcor21", "vix", "vix_p", "spy_iv", "vol_spread", "vol_spread_p", "idx_vrp", "sn_vrp"]
rows = []
print("\n=== annualized mean P&L by signal quartile at entry (Q1 low .. Q4 high); sharpe in brackets ===")
for b, p in BOOKS.items():
    print(f"\n-- {b}")
    for s in SIGS:
        x = S[s]; ok = x.notna() & p.notna()
        q = pd.qcut(x[ok].rank(method="first"), 4, labels=False)
        out = []
        for k in range(4):
            pk = p[ok][q == k]; out.append(f"{pk.mean()*12:+.1%} [{pk.mean()/pk.std()*np.sqrt(12):+.2f}]")
            rows.append(dict(book=b, signal=s, q=k + 1, ann=pk.mean() * 12, sharpe=pk.mean() / pk.std() * np.sqrt(12), n=len(pk)))
        print(f"  {s:14s} " + "  ".join(out))
pd.DataFrame(rows).to_csv(RESULTS / "regime_quartiles_VOLVUE.csv", index=False)
# cross tab: implied corr (COR1M) percentile x VIX level
print("\n=== cross-tab: COR1M trailing-2y percentile (rows: <33%, 33-67%, >67%) x VIX (cols: <15, 15-20, 20-25, >25): ann P&L [sharpe] (n) ===")
cp = pd.cut(S.cor1m_p, [0, 1 / 3, 2 / 3, 1.0001], labels=["corr lo", "corr mid", "corr hi"])
vb = pd.cut(S.vix, [0, 0.15, 0.20, 0.25, 9], labels=["vix<15", "15-20", "20-25", ">25"])
for b in ["two-wing straddle dispersion", "put-wing dispersion", "call dispersion (verticals)", "short idx straddle", "long sn straddles", "short idx put25", "event short straddles"]:
    p = BOOKS[b]; print(f"\n-- {b}")
    for c in cp.cat.categories:
        cells = []
        for v in vb.cat.categories:
            pk = p[(cp == c) & (vb == v)].dropna()
            cells.append(f"{pk.mean()*12:+.1%} [{(pk.mean()/pk.std()*np.sqrt(12)) if len(pk)>3 else np.nan:+.2f}] ({len(pk)})" if len(pk) else "   --   ")
        print(f"  {c:9s} " + " | ".join(cells))
print("DONE")
