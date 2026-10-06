"""Scale the chosen mixes up toward a 20%/yr target: momentum (trend + trail 15%) weight and dip-tranche leverage
scaled together, and the full regime-book combo (with dip adds) scaled; margin need per cycle under portfolio margin."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
D = pd.read_csv(RESULTS / "dip_only_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = D.index
M = pd.read_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv", index_col=0, parse_dates=True)["trend + trailing -15%"].reindex(days).ffill().fillna(0.0)
B = pd.read_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv", index_col=0, parse_dates=True)
book_add = B["PW|add 0.5x at -1% and 0.5x at -2%"] + B["CO|add 0.5x at -1% and 0.5x at -2%"] + B["EV|add 0.5x at -0.5% and 0.5x at -1%"]
dips = D["all dips 2 tranches 0.5x"]
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
pm_comp = pd.read_csv(RESULTS / "regime_margin_components_VOLVUE.csv", index_col=0, parse_dates=True)
pm_book = pm_comp[[c for c in pm_comp.columns if "straddle |" not in c]].sum(axis=1)       # per 1x book, by cycle (before adds)
def equity(parts):
    """parts: [(weight, daily P&L series on 1M notional)]"""
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0])
        for w, p in parts:
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e, pm_peak, pm_typ):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:46s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%} | PM peak={pm_peak:.0%} typical={pm_typ:.0%}", flush=True)
    return st, y
rows = []
print("=== A: momentum (trend + trail 15%) w + dips L, scaled together from (30%, 5x); PM: stocks 25% x w, dips 6% x 1.5 x L x 2 (both tranches in) ===")
for w, L in ((0.3, 5), (0.5, 10), (0.6, 10), (0.9, 15), (1.0, 15), (1.2, 20), (1.5, 25), (1.5, 30), (2.0, 30)):
    e = equity([(w, M), (L, dips)]); st, y = rep(f"A mom {w:.0%} + dips {L}x", e, 0.25 * w + 0.06 * 1.5 * L, 0.25 * w)
    rows.append(dict(mix="A", w_mom=w, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}, y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
    if w in (1.0, 1.5) and L in (15, 25): print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
print("\n=== B: momentum w + regime book with dip adds at L (book on 70% of equity in the earlier combo = 0.7 x L here); PM: stocks 25% x w + book 1.5 x pm(cycle) x L x 2 at peak ===")
for w, L in ((0.3, 2.1), (0.5, 3.5), (0.6, 4.2), (0.9, 6.3), (1.0, 7), (1.2, 8.4), (1.5, 10.5)):
    e = equity([(w, M), (L, book_add)]); st, y = rep(f"B mom {w:.0%} + book {L:.1f}x", e, 0.25 * w + 1.5 * pm_book.max() * L * 2, 0.25 * w + 1.5 * pm_book.quantile(.5) * L)
    rows.append(dict(mix="B", w_mom=w, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}, y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
    if w == 1.0: print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
print("\n=== C: momentum fixed at 50% / 100%, dips scaled alone ===")
for w, L in ((0.5, 20), (0.5, 30), (1.0, 20), (1.0, 30), (0.7, 20)):
    e = equity([(w, M), (L, dips)]); st, y = rep(f"C mom {w:.0%} + dips {L}x", e, 0.25 * w + 0.06 * 1.5 * L, 0.25 * w)
    rows.append(dict(mix="C", w_mom=w, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}, y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
pd.DataFrame(rows).to_csv(RESULTS / "target20_VOLVUE.csv", index=False)
print("DONE")
