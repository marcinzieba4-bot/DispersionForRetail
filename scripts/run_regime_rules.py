"""Score explicit as-of regime rules on the additive monthly sleeve P&L; print full-sample and both halves."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.data.paths import RESULTS
from dispersion.data import rates as R
pnl = pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True)
sig = pd.read_parquet(RESULTS / "regime_signals.parquet"); S = sig.reindex(pnl.index, method="ffill")
ff = R.fred("FEDFUNDS").reindex(pnl.index, method="ffill") / 100.0 / 12.0       # cash per month when out of the market
B = {"putwing": pnl.idx_short_put25 + pnl.sn_long_put25, "twowing": pnl.idx_short_straddle + pnl.sn_long_straddle,
     "callvert": pnl.idx_short_call30 + pnl.sn_long_vertical_30_10, "callout": pnl.idx_short_call30 + pnl.sn_long_call30,
     "idx_strad": pnl.idx_short_straddle, "idx_put": pnl.idx_short_put25, "event": pnl.sn_short_straddle_event,
     "sn_call": pnl.sn_long_call30, "sn_put": pnl.sn_long_put25, "idx_call": pnl.idx_short_call30}
def st(p, cash=None):
    p = p.dropna(); eq = (1 + p).cumprod() if cash is None else (1 + p + cash.reindex(p.index)).cumprod()
    dd = (eq / eq.cummax() - 1).min(); ann = p.mean() * 12
    return dict(ann=ann, sharpe=p.mean() / p.std() * np.sqrt(12) if p.std() > 0 else np.nan, maxdd=dd, worst=p.min(), hit=(p > 0).mean())
def show(name, p, inmkt):
    a = st(p); h1 = st(p[:"2016-12-31"]); h2 = st(p["2017":]); ac = st(p, ff * (~inmkt))
    y = p.groupby(p.index.year).sum()
    print(f"{name:58s} in={inmkt.mean():4.0%} ann={a['ann']:+.2%} sh={a['sharpe']:+.2f} dd={a['maxdd']:+.1%} worst={a['worst']:+.1%} | H1 {h1['ann']:+.1%} [{h1['sharpe']:+.2f}] H2 {h2['ann']:+.1%} [{h2['sharpe']:+.2f}] | +cash dd={ac['maxdd']:+.1%} | 2008={y.get(2008,np.nan):+.1%} 2020={y.get(2020,np.nan):+.1%} 2022={y.get(2022,np.nan):+.1%}")
    return dict(rule=name, in_mkt=inmkt.mean(), **a, h1_ann=h1['ann'], h1_sh=h1['sharpe'], h2_ann=h2['ann'], h2_sh=h2['sharpe'], y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022))
rows = []
T = pd.Series(True, index=pnl.index)
print("=== baselines (always in) ===")
for k in ("putwing", "twowing", "callvert", "callout", "idx_strad", "idx_put", "event"):
    rows.append(show(f"{k} always", B[k], T))
print("\n=== single-condition rules on put-wing dispersion ===")
for s, thr in [("cor1m_p", .5), ("cor1m_p", .67), ("cor1m_p", .75), ("bcor_p", .5), ("bcor_p", .67), ("bcor_p", .75), ("cor3m_p", .67)]:
    m = S[s] > thr; rows.append(show(f"putwing if {s}>{thr}", B["putwing"].where(m, 0.0), m))
for s, thr in [("cor1m", .35), ("cor1m", .45), ("bcor", .35), ("bcor", .45)]:
    m = S[s] > thr; rows.append(show(f"putwing if {s}>{thr} (level)", B["putwing"].where(m, 0.0), m))
for thr in (0.0, 0.05):
    m = S.corr_prem > thr; rows.append(show(f"putwing if bcor-rcor21>{thr}", B["putwing"].where(m, 0.0), m))
m = (S.bcor_p > .5) & (S.corr_prem > 0); rows.append(show("putwing if bcor_p>.5 & corr_prem>0", B["putwing"].where(m, 0.0), m))
print("\n=== user's regime: high implied corr AND low vol ===")
for cs, ct, vt in [("cor1m_p", .5, .18), ("cor1m_p", .5, .20), ("bcor_p", .5, .18), ("bcor_p", .5, .20), ("cor1m_p", .67, .20), ("bcor_p", .67, .22)]:
    m = (S[cs] > ct) & (S.vix < vt)
    for k in ("putwing", "twowing", "callvert", "idx_strad"):
        rows.append(show(f"{k} if {cs}>{ct} & vix<{vt}", B[k].where(m, 0.0), m))
print("\n=== short index vol conditioned on corr ===")
for s, thr in [("cor1m_p", .5), ("bcor_p", .5), ("cor1m_p", .67), ("bcor_p", .67)]:
    m = S[s] > thr
    rows.append(show(f"idx_strad if {s}>{thr}", B["idx_strad"].where(m, 0.0), m)); rows.append(show(f"idx_put if {s}>{thr}", B["idx_put"].where(m, 0.0), m))
print("\n=== call dispersion in LOW corr ===")
for s, thr in [("cor1m_p", .33), ("bcor_p", .33), ("cor1m_p", .5), ("bcor", .30), ("cor1m", .30)]:
    m = S[s] < thr
    for k in ("callout", "callvert", "sn_call"):
        rows.append(show(f"{k} if {s}<{thr}", B[k].where(m, 0.0), m))
    m2 = m & (S.vix < .20)
    rows.append(show(f"callout if {s}<{thr} & vix<.20", B["callout"].where(m2, 0.0), m2))
print("\n=== regime switch: corr hi -> put-wing, corr lo -> outright-call dispersion, mid -> cash ===")
for s in ("cor1m_p", "bcor_p"):
    for lo, hi in [(.33, .67), (.5, .5), (.33, .5), (.25, .75)]:
        hi_m = S[s] > hi; lo_m = S[s] < lo
        p = B["putwing"].where(hi_m, 0.0) + B["callout"].where(lo_m, 0.0)
        rows.append(show(f"switch {s}: >{hi} putwing / <{lo} callout", p, hi_m | lo_m))
        p2 = p + B["event"]
        rows.append(show(f"  + event selling always", p2, T))
        p3 = B["putwing"].where(hi_m, 0.0) + B["idx_strad"].where(hi_m, 0.0) * 0.5 + B["callout"].where(lo_m, 0.0)
        rows.append(show(f"  + 0.5x idx straddle in corr-hi", p3, hi_m | lo_m))
pd.DataFrame(rows).to_csv(RESULTS / "regime_rules_VOLVUE.csv", index=False)
