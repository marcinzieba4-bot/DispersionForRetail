"""Two-wing dispersion: short SPX ATM straddle from real Cboe legs (BXM + PUT),
long single-name ATM straddles at VolVue ATM IV. Hedge variants: net book via
SPY, both sides separately (per-name stock + SPY), one side only, none."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data.paths import RESULTS
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig; R = config.REALISTIC_SPY
IDX = (("BXM", +1, 1.0), ("PUT", +1, 1.0))      # short ATM call + short ATM put, real prices
base = dict(cycle="third_friday", dividends=True, singles_structure="straddle", costs=R)
V = {}
for L in (1, 3):
    V[f"{L}x_disp_long_single_straddles+short_index_straddle_bookhedgeW"] = C(leverage=L, index_legs=IDX, **base)
    V[f"{L}x_disp_split_hedge(per-name stock + SPY)W"] = C(leverage=L, index_legs=IDX, hedge_scope="split", **base)
    V[f"{L}x_disp_split_hedge_daily"] = C(leverage=L, index_legs=IDX, hedge_scope="split", hedge_freq="D", **base)
    V[f"{L}x_disp_hedge_singles_only(per-name stock)W"] = C(leverage=L, index_legs=IDX, hedge_scope="singles", **base)
    V[f"{L}x_disp_hedge_index_only(SPY)W"] = C(leverage=L, index_legs=IDX, hedge_scope="index", **base)
    V[f"{L}x_disp_no_hedge"] = C(leverage=L, index_legs=IDX, hedge_freq=None, **base)
    V[f"{L}x_disp_vega_matched_split_hedgeW"] = C(leverage=L, index_legs=IDX, hedge_scope="split", index_notional_mode="vega", **base)
    V[f"{L}x_disp_split_hedge_exclude_event_namesW"] = C(leverage=L, index_legs=IDX, hedge_scope="split", term_filter="exclude_event", **base)
    V[f"{L}x_REVERSE_disp(short singles, long index)_split_hedgeW"] = C(leverage=L, index_legs=(("BXM", -1, 1.0), ("PUT", -1, 1.0)), singles_sign=-1, hedge_scope="split", **base)
    V[f"{L}x_component_long_single_straddles_only_per-name_hedgedW"] = C(leverage=L, index_notional_scale=0.0, hedge_scope="singles", **base)
    V[f"{L}x_component_short_index_straddle_only_SPYhedgedW"] = C(leverage=L, index_legs=IDX, singles_scale=0.0, **base)
rows, yrs, cr = {}, {}, {}
for k, cfg in V.items():
    res = Backtest(cfg, uni, iv).run(); e = res.equity; neg = (e <= 0).any(); e = e.where(e > 0).dropna()
    st = metrics.summary(e, uni.spy); sh = metrics.second_half(e)
    to = res.hedge_turnover.resample("ME").sum().mean() / e.mean(); cost = sum(m.costs for m in res.months) / e.mean() / st["years"]
    st.update(sharpe_2h=sh["sharpe"], calmar_m_2h=sh["calmar_monthly"], hedge_turnover=to, cost_yr=cost, blown=neg)
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, uni.spy)["strategy"]
    print(f"{k:62s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} to/mo={to:.2f}x cost={cost:.2%} 2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}{' **EQUITY<=0**' if neg else ''}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "dispersion_straddles_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "dispersion_straddles_years_VOLVUE.csv")
print(pd.DataFrame(cr).T.map(lambda x: f"{x:+.1%}").to_string())
print("DONE")
