"""Index put spreads: real Cboe legs (PUT, PPUT, RXM, CNDR, BXMD) and model
25d/5d spreads at VolVue put IV x measured skew multipliers. Index only."""
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
base = dict(cycle="third_friday", dividends=True, singles_scale=0.0, costs=R)
V = {}
for L in (1, 3):
    # engine convention: sign +1 = hold the option position exactly as the Cboe index does
    V[f"{L}x_PUT_shortATMput_real_unhedged"] = C(leverage=L, index_legs=(("PUT", +1, 1.0),), hedge_freq=None, **base)
    V[f"{L}x_PUT_shortATMput_real_hedgedW"] = C(leverage=L, index_legs=(("PUT", +1, 1.0),), **base)
    V[f"{L}x_ATM/5pctOTM_putspread_real(PUT+PPUT)_unhedged"] = C(leverage=L, index_legs=(("PUT", +1, 1.0), ("PPUT", +1, 1.0)), hedge_freq=None, **base)
    V[f"{L}x_ATM/5pctOTM_putspread_real_hedgedW"] = C(leverage=L, index_legs=(("PUT", +1, 1.0), ("PPUT", +1, 1.0)), **base)
    # short 25d put ~ RXM (short 25d put + long 25d call) + BXMD (short 30d call cancels the long call); + PPUT (long 5% OTM put)
    V[f"{L}x_25d/5pctOTM_putspread_real(RXM,BXMD,PPUT)_unhedged"] = C(leverage=L, index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0), ("PPUT", +1, 1.0)), hedge_freq=None, **base)
    V[f"{L}x_25d/5pctOTM_putspread_real_hedgedW"] = C(leverage=L, index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0), ("PPUT", +1, 1.0)), **base)
    V[f"{L}x_short25dput_real(RXM+BXMD)_hedgedW"] = C(leverage=L, index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), **base)
    V[f"{L}x_CNDR_20/5_ironcondor_real"] = C(leverage=L, index_legs=(("CNDR", +1, 1.0),), hedge_freq=None, **base)
    V[f"{L}x_RXM_riskreversal_real(short25dput+long25dcall)"] = C(leverage=L, index_legs=(("RXM", +1, 1.0),), hedge_freq=None, **base)
    V[f"{L}x_shortATMcall_long30dcall_real(BXM,-BXMD)_hedgedW"] = C(leverage=L, index_legs=(("BXM", +1, 1.0), ("BXMD", -1, 1.0)), **base)
    V[f"{L}x_shortATMcall_long30dcall_real_unhedged"] = C(leverage=L, index_legs=(("BXM", +1, 1.0), ("BXMD", -1, 1.0)), hedge_freq=None, **base)
    V[f"{L}x_MODEL_25d/5d_putspread_skew(1.16,1.73)_unhedged"] = C(leverage=L, index_notional_scale=0.0, short_put_delta=25, short_put_iv_mult=1.16, put_delta=5, put_iv_mult=1.73, hedge_freq=None, **base)
    V[f"{L}x_MODEL_25d/5d_putspread_skew(1.16,1.73)_hedgedW"] = C(leverage=L, index_notional_scale=0.0, short_put_delta=25, short_put_iv_mult=1.16, put_delta=5, put_iv_mult=1.73, **base)
    V[f"{L}x_MODEL_25d/5d_putspread_flatIV_unhedged(upper bound)"] = C(leverage=L, index_notional_scale=0.0, short_put_delta=25, short_put_iv_mult=1.0, put_delta=5, put_iv_mult=1.0, hedge_freq=None, **base)
rows, yrs, cr = {}, {}, {}
for k, cfg in V.items():
    res = Backtest(cfg, uni, iv).run(); e = res.equity; neg = (e <= 0).any(); e = e.where(e > 0).dropna()
    st = metrics.summary(e, uni.spy); sh = metrics.second_half(e); st.update(sharpe_2h=sh["sharpe"], calmar_m_2h=sh["calmar_monthly"], blown=neg)
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, uni.spy)["strategy"]
    print(f"{k:60s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} 2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}{' **EQUITY<=0**' if neg else ''}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "put_spread_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "put_spread_years_VOLVUE.csv")
Cs = pd.DataFrame(cr).T; Cs.loc["SPY"] = metrics.crisis_table(uni.spy.loc["2007":], uni.spy)["spy"]
print(Cs.map(lambda x: f"{x:+.1%}").to_string()); Cs.to_csv(RESULTS / "put_spread_crisis_VOLVUE.csv")
print("DONE")
