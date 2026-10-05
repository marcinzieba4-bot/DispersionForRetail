"""Momentum sleeve as long ATM calls (and ATM / 20d call spreads) on the 5 momentum names, standalone and in the combo."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.momentum import momentum_leg
from dispersion.momentum_options import momentum_option_leg
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe(); ivp = VolVueIV("iv_call_30").panel()
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True)
book = hb["honest_book_1x"] - 1e6; days = book.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
legs = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)
def compounded(p):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        q = p.loc[a:b]; seg = E * (1 + (q - q.iloc[0]) / 1e6); out.loc[a:b] = seg.values; E = max(float(seg.iloc[-1]), 1.0)
    return out.ffill()
def blend(L, w, q, w_book=0.7):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; w_ = book.loc[a:b]; qq = q.loc[a:b]
        seg = E * (c / c.iloc[0]) + w * E / 1e6 * (qq - qq.iloc[0]) + w_book * E / 1e6 * L * (w_ - w_.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:52s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}{extra}", flush=True)
    return st, y
sl = {}
p_stock, hold, _ = momentum_leg(uni, n=5); sl["stock, no stop"] = p_stock.reindex(days).ffill().fillna(0.0)
p_tr, hold_tr, _ = momentum_leg(uni, n=5, trend=200, trail=0.15); sl["stock, trend + trail 15%"] = p_tr.reindex(days).ffill().fillna(0.0)
print("=== momentum sleeve as options, standalone, 100% of equity compounded, ex cash (fixed notional = the stock sleeve's share count) ===")
rows = []
for k, (h, wing, expo) in {
    "ATM calls": (hold, None, 1.0), "ATM calls, 2x share count (delta-matched)": (hold, None, 2.0),
    "ATM / 20d call spread": (hold, 20, 1.0), "ATM / 20d call spread, 2x": (hold, 20, 2.0),
    "ATM calls, trend filter": (hold_tr, None, 1.0), "ATM / 20d spread, trend filter": (hold_tr, 20, 1.0),
}.items():
    p, dg = momentum_option_leg(uni, ivp, h, short_wing=wing, exposure=expo); p = p.reindex(days).ffill().fillna(0.0); sl[k] = p
    st, y = rep(k, compounded(p), f" | names priced {dg.n.mean():.1f}/5, premium {dg.premium.mean()*12:.0%}/yr, costs {dg.cost.mean()*12:.1%}/yr")
    rows.append(dict(variant=k, scope="standalone", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, y2008=y.get(2008), y2020=y.get(2020)))
for k in ("stock, no stop", "stock, trend + trail 15%"):
    st, y = rep(k, compounded(sl[k])); rows.append(dict(variant=k, scope="standalone", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, y2008=y.get(2008), y2020=y.get(2020)))
pd.DataFrame(sl).to_csv(RESULTS / "momentum_options_legs_VOLVUE.csv")
print("\n=== inside the combo: w x equity in the sleeve + 70% of equity in the honest book at L x, cash on all equity ===")
rep("ES 30% + book 3x", blend(3, 0.3, legs["ES"].reindex(days).ffill()))
for k in sl:
    for w, L in ((0.3, 3), (0.6, 3), (0.6, 4)):
        st, y = rep(f"{k} w={w:.0%} L={L}", blend(L, w, sl[k])); rows.append(dict(variant=k, scope=f"w={w} L={L}", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, y2008=y.get(2008), y2020=y.get(2020)))
pd.DataFrame(rows).to_csv(RESULTS / "momentum_options_VOLVUE.csv", index=False)
print("DONE")
