"""(1) 10-name momentum sleeve (trend + trail 15%) vs 5 names in the scaled mixes; (2) regime book vs dip-only vs
book-with-adds next to momentum at matched leverage; (3) tastytrade-realistic haircuts on the chosen lines."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.momentum import momentum_leg
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
D = pd.read_csv(RESULTS / "dip_only_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = D.index
B = pd.read_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv", index_col=0, parse_dates=True)
book0 = B["PW|base"] + B["CO|base"] + B["EV|base"]
book_add = B["PW|add 0.5x at -1% and 0.5x at -2%"] + B["CO|add 0.5x at -1% and 0.5x at -2%"] + B["EV|add 0.5x at -0.5% and 0.5x at -1%"]
dips = D["all dips 2 tranches 0.5x"]
M5 = pd.read_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv", index_col=0, parse_dates=True)["trend + trailing -15%"].reindex(days).ffill().fillna(0.0)
p10, _, _ = momentum_leg(uni, n=10, trend=200, trail=0.15); M10 = p10.reindex(days).ffill().fillna(0.0)
p10b, _, _ = momentum_leg(uni, n=10, trail=0.15); M10b = p10b.reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
def equity(parts, cash_mult=1.0, extra_cost_yr=0.0):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; g = 1 + (c / c.iloc[0] - 1) * cash_mult
        seg = E * g
        for w, p in parts:
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        seg = seg - E * extra_cost_yr * (b - a).days / 365.0 * np.linspace(0, 1, len(seg))
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:52s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}{extra}", flush=True)
    return st
rows = []
def row(tag, name, st): rows.append(dict(block=tag, name=name, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}))
print("=== (1) momentum sleeve alone, 100% of equity, with cash ===")
for k, m in (("mom5 trend+trail", M5), ("mom10 trend+trail", M10), ("mom10 trail only", M10b)):
    row("sleeve", k, rep(k, equity([(1.0, m)])))
print("\n=== (1b) scaled mixes with 10 names vs 5 names ===")
for k, m in (("mom5", M5), ("mom10", M10)):
    for w, L in ((0.3, 5), (0.7, 20), (1.0, 15), (1.0, 20), (1.2, 20)):
        row("mix A/C", f"{k} {w:.0%} + dips {L}x", rep(f"{k} {w:.0%} + dips {L}x", equity([(w, m), (L, dips)])))
    for w, L in ((0.6, 4.2), (0.9, 6.3)):
        row("mix B", f"{k} {w:.0%} + book+adds {L}x", rep(f"{k} {w:.0%} + book+adds {L}x", equity([(w, m), (L, book_add)])))
print("\n=== (2) book vs dips vs both, each next to mom10 100%, option leverage chosen for ~20% CAGR; and alone at matched 10% vol ===")
cyc = pd.DataFrame({k: [float(v.loc[b] - v.loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k, v in {"book": book0, "dips": dips, "book+adds": book_add, "mom10": M10}.items()}, index=ent[:-1])
print("cycle-P&L correlations:\n" + cyc.corr().round(2).to_string())
for k, p, L in (("book base", book0, 7), ("dips only", dips, 20), ("book + adds", book_add, 5)):
    row("book-vs-dips", f"mom10 100% + {k} {L}x", rep(f"mom10 100% + {k} {L}x", equity([(1.0, M10), (L, p)])))
for k, p in (("book base", book0), ("dips only", dips), ("book + adds", book_add)):
    e1 = equity([(1.0, p)]); v = metrics.summary(e1)["vol"]; L = 0.10 / v
    row("alone-10%vol", f"{k} alone at 10% vol (L={L:.1f})", rep(f"{k} alone at 10% vol (L={L:.1f})", equity([(L, p)])))
print("\n=== (3) tastytrade-realistic haircuts: dip tranches x0.5 (entry-IV fills), option spreads +0.5%/yr per 1x of option notional, cash yield x0.5 ===")
for name, parts, opt_notional in (("mom10 100% + dips 20x", [(1.0, M10), (20, dips)], 20 * 0.5),      # dips are in ~half the cycles
                                   ("mom10 70% + dips 20x", [(0.7, M10), (20, dips)], 20 * 0.5),
                                   ("mom10 100% + book+adds 5x", [(1.0, M10), (5, book_add)], 5),
                                   ("mom10 60% + book+adds 4.2x", [(0.6, M10), (4.2, book_add)], 4.2),
                                   ("mom5 100% + dips 20x", [(1.0, M5), (20, dips)], 20 * 0.5)):
    st0 = rep(f"{name} (model)", equity(parts))
    parts_h = [(w, (p if p is not dips else p * 0.5) if p is not book_add else (book0 + 0.5 * (book_add - book0))) for w, p in parts]
    st1 = rep(f"{name} (haircut)", equity(parts_h, cash_mult=0.5, extra_cost_yr=0.005 * opt_notional))
    row("haircut", f"{name} model", st0); row("haircut", f"{name} haircut", st1)
pd.DataFrame(rows).to_csv(RESULTS / "final_mix_VOLVUE.csv", index=False)
pd.DataFrame({"mom10_trend_trail15": M10, "mom10_trail15": M10b}).to_csv(RESULTS / "momentum10_legs_VOLVUE.csv")
print("DONE")
