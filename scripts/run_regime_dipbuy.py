"""Dip buying inside the regime book: when a sleeve's cycle is down X% of book notional, add Y x the original
position at that day's marks (entry costs paid, re-hedged). Sleeves at 1x, then the combo with the momentum sleeve."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
b = sig.bcor.rolling(5).mean(); v = sig.vix.rolling(5).mean(); bp = b.rolling(504, min_periods=250).rank(pct=True)
HI = (bp > 0.75).shift(1).fillna(False); LO = ((b < 0.30) & (v < 0.20)).shift(1).fillna(False)
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
PW = dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI)
CO = dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO)
EV = dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book")
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True); days = hb.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
mom = pd.read_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv", index_col=0, parse_dates=True)["trend + trailing -15%"].reindex(days).ffill().fillna(0.0)
es = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)["ES"].reindex(days).ffill().fillna(0.0)
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:50s} cagr={st['cagr']:+.2%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008, np.nan):+.1%} 2020={y.get(2020, np.nan):+.1%} 2022={y.get(2022, np.nan):+.1%} covid={cr.loc['Covid_2020','strategy'] if 'Covid_2020' in cr.index else np.nan:+.1%}{extra}", flush=True)
    return st
ADD = {"base": (), "add 0.5x at -1%": ((0.01, 0.5),), "add 1.0x at -1%": ((0.01, 1.0),), "add 1.0x at -2%": ((0.02, 1.0),),
       "add 0.5x at -1% and 0.5x at -2%": ((0.01, 0.5), (0.02, 0.5)), "add 1.0x at -1% and 1.0x at -2%": ((0.01, 1.0), (0.02, 1.0))}
ADD_EV = {"base": (), "add 0.5x at -0.5%": ((0.005, 0.5),), "add 1.0x at -0.5%": ((0.005, 1.0),), "add 1.0x at -1%": ((0.01, 1.0),), "add 0.5x at -0.5% and 0.5x at -1%": ((0.005, 0.5), (0.01, 0.5))}
runs = {}
print("=== sleeves with dip buying, 1x, ex cash, fixed notional (threshold = % of book notional lost within the cycle) ===")
for sl, kw0, adds in (("PW", PW, ADD), ("CO", CO, ADD), ("EV", EV, ADD_EV)):
    for name, ad in adds.items():
        res = Backtest(C(**{**base, **kw0, "cycle_addon": ad}), uni100 if sl == "EV" else uni, iv).run()
        e = res.equity.ffill().reindex(days).ffill(); runs[(sl, name)] = e - 1e6
        n_add = sum(1 for m in res.months if any(str(x).startswith("ADD") for x in m.skipped)); n_tr = sum(1 for m in res.months if m.legs)
        rep(f"{sl} {name}", e, f" | added in {n_add}/{n_tr} cycles")
pd.DataFrame({f"{a}|{b_}": v for (a, b_), v in runs.items()}).to_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv")
def blend(L, parts, w_mom=0.3, w_es=0.0):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b_ in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b_]; seg = E * (c / c.iloc[0])
        for q in parts:
            qq = q.loc[a:b_]; seg = seg + 0.7 * L * E / 1e6 * (qq - qq.iloc[0])
        qm = mom.loc[a:b_]; seg = seg + w_mom * E / 1e6 * (qm - qm.iloc[0])
        qe = es.loc[a:b_]; seg = seg + w_es * E / 1e6 * (qe - qe.iloc[0])
        out.loc[a:b_] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b_:] = 0.0; break
    return out.ffill()
print("\n=== combo: 30% momentum (trend + trail 15%) + 70% of equity in the regime book at L x, cash on all equity ===")
BOOKS = {
    "book base": [("PW", "base"), ("CO", "base"), ("EV", "base")],
    "PW add 1.0x at -1%": [("PW", "add 1.0x at -1%"), ("CO", "base"), ("EV", "base")],
    "PW add 0.5x at -1% & -2%": [("PW", "add 0.5x at -1% and 0.5x at -2%"), ("CO", "base"), ("EV", "base")],
    "EV add 1.0x at -0.5%": [("PW", "base"), ("CO", "base"), ("EV", "add 1.0x at -0.5%")],
    "EV add 0.5x at -0.5% & -1%": [("PW", "base"), ("CO", "base"), ("EV", "add 0.5x at -0.5% and 0.5x at -1%")],
    "all: PW 1.0x@-1%, CO 1.0x@-1%, EV 1.0x@-0.5%": [("PW", "add 1.0x at -1%"), ("CO", "add 1.0x at -1%"), ("EV", "add 1.0x at -0.5%")],
    "all 2-tranche 0.5x": [("PW", "add 0.5x at -1% and 0.5x at -2%"), ("CO", "add 0.5x at -1% and 0.5x at -2%"), ("EV", "add 0.5x at -0.5% and 0.5x at -1%")],
}
rows = []
for bk, parts in BOOKS.items():
    for L in (3, 4):
        st = rep(f"{bk} L={L}", blend(L, [runs[p] for p in parts])); rows.append(dict(book=bk, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month")}))
pd.DataFrame(rows).to_csv(RESULTS / "regime_dipbuy_combo_VOLVUE.csv", index=False)
print("DONE")
