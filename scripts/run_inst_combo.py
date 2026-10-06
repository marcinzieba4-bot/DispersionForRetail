"""Does institutional execution improve the sector-ETF combo? Regime book sleeves re-run at institutional cost
models (vol-path marks), TLT straddle at a tighter spread, ETF legs at 1 bp; combo risk stats vs retail."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.tlt_straddle import tlt_option_leg
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS, CACHE
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig; CM = config.CostModel
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
b = sig.bcor.rolling(5).mean(); v = sig.vix.rolling(5).mean(); bp = b.rolling(504, min_periods=250).rank(pct=True)
HI = (bp > 0.75).shift(1).fillna(False); LO = ((b < 0.30) & (v < 0.20)).shift(1).fillna(False)
COSTS = {"retail (tastytrade)": config.REALISTIC_SPY,
         "institutional 1.5% flat": CM("gs15", 0.015, 0.015, 0.015, 0.015, 0.003, 5e-5, 0, 0, 1.0),
         "institutional 0.75% flat": CM("gs075", 0.0075, 0.0075, 0.0075, 0.0075, 0.002, 5e-5, 0, 0, 1.0),
         "zero": CM("zero", 0, 0, 0, 0, 0, 0, 0, 0, 1.0)}
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, hedge_scope="split", vol_path_marks=True)
SL = {"PW": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI), uni),
      "CO": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO), uni),
      "EV": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100)}
V = pd.read_csv(RESULTS / "volpath_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = V.index
books = {"retail (tastytrade)": V["book_volpath"]}
print("=== regime book (1x, ex cash, vol-path marks) by cost model ===")
for ck, cm in COSTS.items():
    if ck in books: p = books[ck]
    else:
        p = sum((Backtest(C(**{**base, **kw, "costs": cm}), u, iv).run().equity.ffill().reindex(days).ffill() - 1e6) for kw, u in SL.values()); books[ck] = p
    st = metrics.summary(1e6 + p, uni.spy); print(f"{ck:26s} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%}", flush=True)
pd.DataFrame(books).to_csv(RESULTS / "inst_book_legs_VOLVUE.csv")
px = pd.read_parquet(CACHE / "tlt_gld.parquet"); px.columns = [c.upper() for c in px.columns]; tlt = px["TLT"].reindex(days).ffill()
vv = pd.read_parquet(CACHE / "volvue_tlt.parquet"); vv["date"] = pd.to_datetime(vv["date"]); vv = vv.set_index("date").sort_index()
iv_atm = vv["iv_mean_30"].astype(float) / 100.0; iv_atm = iv_atm[~iv_atm.index.duplicated()]
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod(); ivp = iv_atm.rolling(252, min_periods=120).rank(pct=True).reindex(days).ffill()
strs = {}
for ck, hs, hb in (("retail (tastytrade)", 0.015, 1e-4), ("institutional 1.5% flat", 0.015, 5e-5), ("institutional 0.75% flat", 0.0075, 5e-5), ("zero", 0.0, 0.0)):
    p, _ = tlt_option_leg(tlt, iv_atm, rf, side=-1, hedge="W", mask=ivp > 0.5, vol_path=True, half_spread=hs, hedge_bp=hb); strs[ck] = p.reindex(days).ffill().fillna(0.0)
    st = metrics.summary(1e6 + strs[ck], uni.spy); print(f"TLT straddle {ck:26s} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f}")
SEC = pd.read_csv(RESULTS / "sector_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0)["sector mom top3, SPY>200d, trail 15%"]
TLT = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True).reindex(days).ffill().fillna(0.0)["TLT trend 200d"]
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
def equity(parts, k=1.0):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b_ in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b_]; seg = E * (c / c.iloc[0])
        for w, p in parts:
            q = p.loc[a:b_]; seg = seg + k * w * E / 1e6 * (q - q.iloc[0])
        out.loc[a:b_] = seg.values; E = float(seg.iloc[-1])
    return out.ffill()
def rep(name, e):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:36s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} cvar={st['cvar95_m']:+.1%} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}", flush=True)
    return st
rows = []
print("\n=== combo: sector top3 100% + book 3.75x + TLT 38% + straddle 75%, by cost model (k=1 and k=1.25) ===")
for ck in COSTS:
    for k in (1.0, 1.25):
        st = rep(f"{ck} k={k}", equity([(1.0, SEC), (3.75, books[ck]), (0.375, TLT), (0.75, strs[ck])], k=k))
        rows.append(dict(costs=ck, k=k, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m", "cvar95_m")}))
pd.DataFrame(rows).to_csv(RESULTS / "inst_combo_VOLVUE.csv", index=False)
print("DONE")
