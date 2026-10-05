"""Single-name event selling: short ATM straddles (same IV both legs, VolVue
iv_call_30) on names whose 30d IV is >8% above the 60d (event inside the
cycle), weekly delta hedge, vs non-event control and all names."""
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
base = dict(cycle="third_friday", dividends=True, index_notional_scale=0.0, singles_structure="straddle", costs=R)
V = {}
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
for L in (1, 3):
    V[f"{L}x_SHORT_straddle_event_names_100name_universe_hedgedW"] = (C(leverage=L, singles_sign=-1, term_filter="event_only", n_names=100, **base), uni100)
    V[f"{L}x_SHORT_straddle_nonevent_100name_universe_hedgedW"] = (C(leverage=L, singles_sign=-1, term_filter="exclude_event", n_names=100, **base), uni100)
    V[f"{L}x_SHORT_straddle_event_names_only_hedgedW"] = C(leverage=L, singles_sign=-1, term_filter="event_only", **base)
    V[f"{L}x_SHORT_straddle_nonevent_names_hedgedW(control)"] = C(leverage=L, singles_sign=-1, term_filter="exclude_event", **base)
    V[f"{L}x_SHORT_straddle_all_names_hedgedW"] = C(leverage=L, singles_sign=-1, **base)
    V[f"{L}x_LONG_straddle_event_names_hedgedW(sanity)"] = C(leverage=L, singles_sign=+1, term_filter="event_only", **base)
    V[f"{L}x_SHORT_straddle_event_names_unhedged"] = C(leverage=L, singles_sign=-1, term_filter="event_only", hedge_freq=None, **base)
    V[f"{L}x_SHORT_straddle_event_names_hedgedD"] = C(leverage=L, singles_sign=-1, term_filter="event_only", hedge_freq="D", **base)
    V[f"{L}x_SHORT_straddle_event_thresh1.15_hedgedW"] = C(leverage=L, singles_sign=-1, term_filter="event_only", term_thresh=1.15, **base)
rows, yrs, cr = {}, {}, {}
for k, cfg in V.items():
    u = uni
    if isinstance(cfg, tuple):
        cfg, u = cfg
    res = Backtest(cfg, u, iv).run(); e = res.equity; neg = (e <= 0).any(); e = e.where(e > 0).dropna()
    st = metrics.summary(e, u.spy); sh = metrics.second_half(e)
    nn = np.mean([len({l.ticker for l in m.legs if not l.is_index}) for m in res.months]); dep = np.mean([sum(abs(l.units) * l.S0 for l in m.legs if l.kind == "call") / m.equity_in for m in res.months])
    st.update(sharpe_2h=sh["sharpe"], names_per_month=nn, deployed_x_equity=dep, blown=neg)
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, u.spy)["strategy"]
    print(f"{k:56s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} names/mo={nn:.1f} deployed={dep:.2f}x 2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}{' **EQUITY<=0**' if neg else ''}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "event_selling_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "event_selling_years_VOLVUE.csv")
print(pd.DataFrame(cr).T.map(lambda x: f"{x:+.1%}").to_string())
print("DONE")
