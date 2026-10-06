"""Dip adds with realistic fills (day-of IV on the added single legs, 2x half-spread on add fills), then the
dip-only tranches, the book with adds, and the final mixes with full cash yield."""
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
SL = {"PW": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI), uni, ((0.01, 0.5), (0.02, 0.5))),
      "CO": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO), uni, ((0.01, 0.5), (0.02, 0.5))),
      "EV": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100, ((0.005, 0.5), (0.01, 0.5)))}
old = pd.read_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = old.index
runs = {}
print("=== sleeves, 1x, ex cash: base / adds at entry-IV fills (old) / adds at day-of IV fills + 2x spread (realistic) ===")
for k, (kw, u, ad) in SL.items():
    res = Backtest(C(**{**base, **kw, "cycle_addon": ad}), u, iv).run(); e = res.equity.ffill().reindex(days).ffill(); runs[k] = e - 1e6
    for name, p in ((f"{k} base", old[f"{k}|base"]), (f"{k} adds, entry-IV fills", old[[c for c in old.columns if c.startswith(k + "|add 0.5x")][0]]), (f"{k} adds, realistic fills", runs[k])):
        st = metrics.summary(1e6 + p, uni.spy); y = metrics.yearly(1e6 + p)
        print(f"{name:34s} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} | 2008={y.get(2008):+.1%} 2020={y.get(2020):+.1%} 2022={y.get(2022):+.1%}", flush=True)
book0 = old["PW|base"] + old["CO|base"] + old["EV|base"]
book_add_r = runs["PW"] + runs["CO"] + runs["EV"]; dips_r = book_add_r - book0
old_add = sum(old[[c for c in old.columns if c.startswith(k + "|add 0.5x")][0]] for k in SL); dips_old = old_add - book0
pd.DataFrame({"book_base": book0, "book_adds_realistic": book_add_r, "dips_realistic": dips_r}).to_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv")
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
M5 = pd.read_csv(RESULTS / "momentum_stops_legs_VOLVUE.csv", index_col=0, parse_dates=True)["trend + trailing -15%"].reindex(days).ffill().fillna(0.0)
def equity(parts):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b_ in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b_]; seg = E * (c / c.iloc[0])
        for w, p in parts:
            q = p.loc[a:b_]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        out.loc[a:b_] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b_:] = 0.0; break
    return out.ffill()
def rep(name, e):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:50s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}", flush=True)
    return st
rows = []
print("\n=== dip tranche per cycle (realistic fills), 1x: " + ", ".join(f"{k}: fired {int((runs[k]-old[k+'|base']).reindex(ent[:-1]).diff().ne(0).sum())}" for k in SL) + " ===")
cyc_old = pd.Series([float(dips_old.loc[b_] - dips_old.loc[a]) / 1e6 for a, b_ in zip(ent[:-1], ent[1:])]); cyc_r = pd.Series([float(dips_r.loc[b_] - dips_r.loc[a]) / 1e6 for a, b_ in zip(ent[:-1], ent[1:])])
print(f"all dips per year: entry-IV fills {cyc_old.sum()/19.6:+.2%}  realistic {cyc_r.sum()/19.6:+.2%}  | mean per fire {cyc_old[cyc_old!=0].mean():+.3%} -> {cyc_r[cyc_r!=0].mean():+.3%}")
print("\n=== final mixes, full cash yield, realistic dip fills ===")
for name, parts in (("mom10 100% + dips 20x", [(1.0, M10), (20, dips_r)]), ("mom5 100% + dips 20x", [(1.0, M5), (20, dips_r)]), ("mom10 70% + dips 20x", [(0.7, M10), (20, dips_r)]),
                    ("mom10 100% + book+adds 5x", [(1.0, M10), (5, book_add_r)]), ("mom10 60% + book+adds 4.2x", [(0.6, M10), (4.2, book_add_r)]), ("mom5 100% + book+adds 5x", [(1.0, M5), (5, book_add_r)]),
                    ("mom10 100% + book base 5x", [(1.0, M10), (5, book0)]), ("mom10 30% + book+adds 2.1x", [(0.3, M10), (2.1, book_add_r)]), ("mom5 30% + book+adds 2.1x", [(0.3, M5), (2.1, book_add_r)])):
    st = rep(name, equity(parts)); rows.append(dict(mix=name, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}))
pd.DataFrame(rows).to_csv(RESULTS / "dip_realistic_mix_VOLVUE.csv", index=False)
print("DONE")
