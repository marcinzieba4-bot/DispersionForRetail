"""TLT and GLD sleeves with trailing stops (and optional 200d trend filter) added to the chosen mix:
10-name momentum (trend + trail 15%) + base regime book at 5x. Fixed notional per third-Friday cycle, stop
signalled on the close and executed at the next close, cash until the next cycle, 2 bp per side."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.backtest.engine import third_fridays
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS, CACHE
uni = load_universe()
px = pd.read_parquet(CACHE / "tlt_gld.parquet"); px.columns = [c.upper() for c in px.columns]
L = pd.read_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = L.index
px = px.reindex(days).ffill()
book0 = L["book_base"]; book_add = L["book_adds_realistic"]
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
M5 = pd.read_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv", index_col=0, parse_dates=True)["trend + trailing -15%"].reindex(days).ffill().fillna(0.0)
ES = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)["ES"].reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
def etf_leg(s, trail=None, trend=None, cost_bp=2e-4, notional=1e6):
    """fixed-notional long ETF per cycle; trailing stop from the running high since entry; trend: skip cycle if below its MA."""
    pnl = pd.Series(np.nan, index=days); pnl.iloc[0] = 0.0; run = 0.0; stops = 0; n = 0
    ma = s.rolling(trend).mean() if trend else None
    for a, b in zip(ent[:-1], ent[1:]):
        if trend and float(s.loc[a]) < float(ma.loc[a]):
            pnl.loc[a:b] = run; continue
        seg = s.loc[a:b] / float(s.loc[a]); n += 1
        val = seg.copy()
        if trail is not None:
            hi = 1.0; dead = False
            for i in range(1, len(seg)):
                if dead:
                    val.iloc[i] = val.iloc[i - 1]; continue
                prev = float(seg.iloc[i - 1]); hi = max(hi, prev)
                if i >= 2 and prev <= hi * (1 - trail):
                    val.iloc[i] = float(seg.iloc[i]); dead = True; stops += 1
        path = notional * (val - 1.0) - notional * cost_bp * 2
        pnl.loc[a:b] = run + path.values; run += float(path.iloc[-1])
    return pnl.ffill(), stops, n
def equity(parts):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0])
        for w, p in parts:
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:52s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}{extra}", flush=True)
    return st
H = {}
print("=== hedge sleeves alone, 100% of equity, with cash ===")
for t in ("TLT", "GLD"):
    for name, kw in (("buy & hold", {}), ("trail 10%", dict(trail=0.10)), ("trail 15%", dict(trail=0.15)), ("trend 200d", dict(trend=200)), ("trend 200d + trail 15%", dict(trend=200, trail=0.15)), ("trend 200d + trail 10%", dict(trend=200, trail=0.10))):
        p, stops, n = etf_leg(px[t], **kw); H[f"{t} {name}"] = p
        rep(f"{t} {name}", equity([(1.0, p)]), f" | stopped {stops}/{n} cycles")
pd.DataFrame(H).to_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv")
# correlations of cycle P&L with the core sleeves
cyc = pd.DataFrame({k: [float(v.loc[b] - v.loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k, v in {"mom10": M10, "book": book0, "ES": ES, "TLT trail15": H["TLT trail 15%"], "GLD trail15": H["GLD trail 15%"], "TLT trend+trail": H["TLT trend 200d + trail 15%"], "GLD trend+trail": H["GLD trend 200d + trail 15%"]}.items()}, index=ent[:-1])
print("\ncycle-P&L correlations:\n" + cyc.corr().round(2).to_string())
rows = []
print("\n=== core: mom10 100% + book base 5x, plus TLT / GLD sleeves at w of equity (full cash yield, realistic) ===")
core = [(1.0, M10), (5, book0)]
rep("core: mom10 100% + book 5x", equity(core))
for hk in ("trail 15%", "trend 200d + trail 15%", "trail 10%", "trend 200d + trail 10%"):
    for wt, wg in ((0.3, 0.0), (0.0, 0.3), (0.3, 0.3), (0.5, 0.5), (0.0, 0.5), (0.5, 0.0), (1.0, 1.0)):
        st = rep(f"core + TLT {hk} {wt:.0%} + GLD {hk} {wg:.0%}", equity(core + [(wt, H[f"TLT {hk}"]), (wg, H[f"GLD {hk}"])]))
        rows.append(dict(core="mom10 100% + book 5x", hedge=hk, w_tlt=wt, w_gld=wg, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, beta=st.get("beta")))
print("\n=== lower-leverage core: mom10 60% + book 3x ===")
core2 = [(0.6, M10), (3, book0)]
rep("core2: mom10 60% + book 3x", equity(core2))
for hk in ("trail 15%", "trend 200d + trail 15%"):
    for wt, wg in ((0.3, 0.3), (0.5, 0.5), (0.0, 0.5), (0.5, 0.0)):
        st = rep(f"core2 + TLT {hk} {wt:.0%} + GLD {hk} {wg:.0%}", equity(core2 + [(wt, H[f"TLT {hk}"]), (wg, H[f"GLD {hk}"])]))
        rows.append(dict(core="mom10 60% + book 3x", hedge=hk, w_tlt=wt, w_gld=wg, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, beta=st.get("beta")))
df = pd.DataFrame(rows); df.to_csv(RESULTS / "hedge_etf_mix_VOLVUE.csv", index=False)
print("\nbest by Calmar:"); print(df.sort_values("calmar", ascending=False).head(8)[["core", "hedge", "w_tlt", "w_gld", "cagr", "sharpe", "maxdd", "calmar", "worst_month"]].round(3).to_string())
print("DONE")
