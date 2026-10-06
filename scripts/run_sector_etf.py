"""Swap the 10-stock momentum sleeve for trend-filtered sector ETF baskets (9 SPDR sectors, 2004+):
 (a) top-3 / top-4 sector momentum (12-1), SPY > 200d filter, 15% trailing stop;
 (b) each sector held only while above its own 200d, equal weight over those in trend;
 (c) top-3 momentum among sectors above their own 200d.
Then the combined book re-run (vol-path marks, k ladder, PM: ETFs 15%)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS, CACHE
uni = load_universe()
V = pd.read_csv(RESULTS / "volpath_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = V.index; book_v = V["book_volpath"]
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
TLT = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True)["TLT trend 200d"].reindex(days).ffill().fillna(0.0)
STR = pd.read_csv(RESULTS / "tlt_straddle_legs_VOLVUE.csv", index_col=0, parse_dates=True)["short straddle W, only IV pct > 50%"].reindex(days).ffill().fillna(0.0)
ES = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)["ES"].reindex(days).ffill().fillna(0.0)
px = pd.read_parquet(CACHE / "sector_etfs.parquet").reindex(days).ffill(); spy = uni.spy.reindex(days).ffill()
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
pm_comp = pd.read_csv(RESULTS / "regime_margin_components_VOLVUE.csv", index_col=0, parse_dates=True)
pm_book = pm_comp[[c for c in pm_comp.columns if "straddle |" not in c]].sum(axis=1).reindex(ent[:-1]).fillna(0.0)
def basket_leg(select, trail=0.15, cost_bp=2e-4, notional=1e6):
    """select(a) -> list of tickers (equal weight) for the cycle starting at a; trailing stop per ETF."""
    pnl = pd.Series(np.nan, index=days); pnl.iloc[0] = 0.0; run = 0.0; stops = 0; held = []
    prev = {}
    for a, b in zip(ent[:-1], ent[1:]):
        names = select(a); held.append(len(names))
        if not names:
            pnl.loc[a:b] = run; prev = {}; continue
        w = 1.0 / len(names); seg = px.loc[a:b, names] / px.loc[a, names]; val = seg.copy()
        if trail:
            for t in names:
                hi = 1.0; dead = False
                for i in range(1, len(seg)):
                    if dead: val.iloc[i, val.columns.get_loc(t)] = val.iloc[i - 1][t]; continue
                    p0 = float(seg.iloc[i - 1][t]); hi = max(hi, p0)
                    if i >= 2 and p0 <= hi * (1 - trail): dead = True; stops += 1
        turn = sum(abs(w - prev.get(t, 0.0)) for t in names) + sum(v for t, v in prev.items() if t not in names)
        path = notional * ((val * w).sum(axis=1) - 1.0) - notional * turn * cost_bp
        pnl.loc[a:b] = run + path.values; run += float(path.iloc[-1]); prev = {t: w for t in names}
    return pnl.ffill().fillna(0.0), stops, float(np.mean(held))
ma_spy = spy.rolling(200).mean(); ma = px.rolling(200).mean()
def mom(a, n, own_trend=False, spy_trend=True):
    i = days.get_loc(a)
    if i < 260 or (spy_trend and float(spy.iloc[i]) < float(ma_spy.iloc[i])): return []
    r = px.iloc[i - 21] / px.iloc[i - 252] - 1
    if own_trend: r = r[px.iloc[i] > ma.iloc[i]]
    return r.dropna().nlargest(n).index.tolist()
def trend_basket(a):
    i = days.get_loc(a)
    if i < 260: return []
    return [t for t in px.columns if float(px.iloc[i][t]) > float(ma.iloc[i][t])]
SLEEVES = {
    "sector mom top3, SPY>200d, trail 15%": lambda a: mom(a, 3),
    "sector mom top4, SPY>200d, trail 15%": lambda a: mom(a, 4),
    "sector mom top3, own 200d + SPY>200d": lambda a: mom(a, 3, own_trend=True),
    "sector mom top3, own 200d only": lambda a: mom(a, 3, own_trend=True, spy_trend=False),
    "all sectors above own 200d, eq-wt": trend_basket,
    "sector mom top3, no filter": lambda a: mom(a, 3, spy_trend=False),
}
def equity(parts, k=1.0):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; usage = pd.Series(np.nan, index=days)
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0]); req = 0.0
        for w, p, m in parts:
            q = p.loc[a:b]; seg = seg + k * w * E / 1e6 * (q - q.iloc[0]); req += k * w * m * (1.0 if float(q.iloc[-1] - q.iloc[0]) != 0 or m == 0 else 0.0)
        req += k * 1.5 * float(pm_book.get(a, 0.0)) * next((w for w, p, m in parts if p is book_v), 0.0)
        usage.loc[a:b] = req; out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill(), usage.ffill()
def rep(name, e, u=None):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:52s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}" + (f" | PM med={u.median():.0%} max={u.max():.0%}" if u is not None else ""), flush=True)
    return st, y
S = {}
print("=== equity sleeves alone, 100% of equity, with cash ===")
rep("10-stock momentum, trend + trail 15%", equity([(1.0, M10, 0.25)])[0])
rep("ES (SPY TR - cash)", equity([(1.0, ES, 0.06)])[0])
for k, sel in SLEEVES.items():
    p, stops, held = basket_leg(sel); S[k] = p; rep(k, equity([(1.0, p, 0.15)])[0]); print(f"      avg ETFs held {held:.1f}, stops fired {stops}")
pd.DataFrame(S).to_csv(RESULTS / "sector_etf_legs_VOLVUE.csv")
cyc = pd.DataFrame({k: [float(v.loc[b] - v.loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k, v in {"mom10": M10, "sec top3": S["sector mom top3, SPY>200d, trail 15%"], "trend basket": S["all sectors above own 200d, eq-wt"], "book": book_v, "TLT": TLT, "STR": STR}.items()}, index=ent[:-1])
print("\ncycle-P&L correlations:\n" + cyc.corr().round(2).to_string())
rows = []
print("\n=== combined book (vol-path marks): equity sleeve 75% + book 3.75x + TLT 38% + straddle 75%, k=1; PM: stocks 25%, ETFs 15%, TLT 4% (futures), straddle 10.5% ===")
for name, p, m in (("10 stocks (line A)", M10, 0.25), ("sector mom top3", S["sector mom top3, SPY>200d, trail 15%"], 0.15), ("sector mom top4", S["sector mom top4, SPY>200d, trail 15%"], 0.15), ("sector mom top3 + own 200d", S["sector mom top3, own 200d + SPY>200d"], 0.15), ("all sectors above own 200d", S["all sectors above own 200d, eq-wt"], 0.15), ("ES", ES, 0.06)):
    for wq in (0.75, 1.0):
        e, u = equity([(wq, p, m), (3.75, book_v, 0.0), (0.375, TLT, 0.04), (0.75, STR, 0.105)]); st, y = rep(f"{name} {wq:.0%}", e, u)
        rows.append(dict(sleeve=name, w_eq=wq, k=1.0, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, pm_med=u.median(), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
print("\n=== ladder with the sector basket (top3, SPY>200d, trail 15%) at 100% of equity ===")
for k in (0.75, 1.0, 1.25, 1.5):
    e, u = equity([(1.0, S["sector mom top3, SPY>200d, trail 15%"], 0.15), (3.75, book_v, 0.0), (0.375, TLT, 0.04), (0.75, STR, 0.105)], k=k); st, y = rep(f"sector top3 100%, k={k}", e, u)
    rows.append(dict(sleeve="sector mom top3", w_eq=1.0, k=k, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, pm_med=u.median(), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
    if k == 1.0: print("   yearly: " + " ".join(f"{kk}={v:+.0%}" for kk, v in y.items()))
pd.DataFrame(rows).to_csv(RESULTS / "sector_etf_mix_VOLVUE.csv", index=False)
print("DONE")
