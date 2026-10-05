"""Strike-selective dispersion: sell the index where the curve is richest
(25-delta put, real: RXM + BXMD; optionally ATM call via BXM), buy singles
where it is cheapest (30-delta calls at call IV, 25-delta puts at ~flat
single-name skew). Both sides hedged separately (per-name stock + SPY),
fixed notional, realistic costs, 3rd-Friday cycle."""
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
P25 = (("RXM", +1, 1.0), ("BXMD", +1, 1.0))                      # short 25d put (long 25d call cancelled by short 30d call)
P25C = (("RXM", +1, 1.0), ("BXMD", +1, 1.0), ("BXM", +1, 1.0))   # + short ATM call
P25DR = (("RXM", +1, 1.0), ("BXMD", +1, 1.0), ("PPUT", +1, 1.0)) # + long 5% OTM put (defined risk)
CNDR = (("CNDR", +1, 1.0),)
base = dict(cycle="third_friday", dividends=True, costs=R, fixed_notional=True, equity=1e6, hedge_scope="split",
            single_put_iv_mult=1.02, short_wing_delta=None)
V = {}
for L in (1, 2):
    V[f"{L}x_idx_short25dput + singles_long30dcalls"] = C(leverage=L, index_legs=P25, singles_structure="vertical", **base)
    V[f"{L}x_idx_short25dput + singles_long25dputs (put-wing dispersion)"] = C(leverage=L, index_legs=P25, singles_structure="put", **base)
    V[f"{L}x_idx_short25dput + singles_long_wings(30dcall+25dput)"] = C(leverage=L, index_legs=P25, singles_structure="wings", **base)
    V[f"{L}x_idx_short25dput+shortATMcall + singles_long_wings"] = C(leverage=L, index_legs=P25C, singles_structure="wings", **base)
    V[f"{L}x_idx_20/5condor + singles_long_wings"] = C(leverage=L, index_legs=CNDR, singles_structure="wings", **base)
    V[f"{L}x_idx_25d/5pct_putspread + singles_long_wings"] = C(leverage=L, index_legs=P25DR, singles_structure="wings", **base)
    V[f"{L}x_idx_short25dput + singles_long_wings, vega-matched"] = C(leverage=L, index_legs=P25, singles_structure="wings", index_notional_mode="vega", **base)
    V[f"{L}x_idx_short25dput + singles_long_wings, book SPY hedge"] = C(leverage=L, index_legs=P25, singles_structure="wings", **{**base, "hedge_scope": "book"})
    V[f"{L}x_idx_short25dput + singles_long_wings, unhedged"] = C(leverage=L, index_legs=P25, singles_structure="wings", hedge_freq=None, **base)
    V[f"{L}x_idx_short25dput + singles_long_wings, exclude event names"] = C(leverage=L, index_legs=P25, singles_structure="wings", term_filter="exclude_event", **base)
    V[f"{L}x_component: idx_short25dput alone, SPY hedged"] = C(leverage=L, index_legs=P25, singles_scale=0.0, **base)
    V[f"{L}x_component: singles_long_wings alone, stock hedged"] = C(leverage=L, index_notional_scale=0.0, singles_structure="wings", **base)
    V[f"{L}x_component: singles_long25dputs alone, stock hedged"] = C(leverage=L, index_notional_scale=0.0, singles_structure="put", **base)
rows, yrs, cr = {}, {}, {}
for k, cfg in V.items():
    res = Backtest(cfg, uni, iv).run(); e = res.equity; neg = (e <= 0).any(); e = e.where(e > 0).dropna()
    st = metrics.summary(e, uni.spy); sh = metrics.second_half(e)
    cost = sum(m.costs for m in res.months) / 1e6 / st["years"]; to = res.hedge_turnover.resample("ME").sum().mean() / 1e6
    st.update(sharpe_2h=sh["sharpe"], cost_yr=cost, turnover_mo=to, blown=neg)
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, uni.spy)["strategy"]
    print(f"{k:64s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} cost={cost:.2%} to/mo={to:.2f}x 2008={yrs[k].get(2008, np.nan):+.1%} 2018={yrs[k].get(2018, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}{' **EQUITY<=0**' if neg else ''}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "curve_dispersion_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "curve_dispersion_years_VOLVUE.csv")
Cs = pd.DataFrame(cr).T; Cs.loc["SPY"] = metrics.crisis_table(uni.spy.loc["2007":], uni.spy)["spy"]; print(Cs.map(lambda x: f"{x:+.1%}").to_string())
print("DONE")
