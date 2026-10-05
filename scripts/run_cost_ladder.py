"""Every candidate book under retail / GS / tight-institutional / zero costs, ex cash yield."""
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
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig; CM = config.CostModel
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
COSTS = {
    "retail": config.REALISTIC_SPY,
    "GS 1.5% flat": CM("gs15", 0.015, 0.015, 0.015, 0.015, 0.015, 1e-4, 0, 0, 1.0),
    "GS 0.75% flat": CM("gs075", 0.0075, 0.0075, 0.0075, 0.0075, 0.0075, 1e-4, 0, 0, 1.0),
    "zero": CM("zero", 0, 0, 0, 0, 0, 0, 0, 0, 1.0),
}
P25 = (("RXM", +1, 1.0), ("BXMD", +1, 1.0)); STR = (("BXM", +1, 1.0), ("PUT", +1, 1.0))
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False)
BOOKS = {
    "spec call book (30-10 verticals vs idx 30d call)": (dict(index_leg="BXMD", singles_structure="vertical", hedge_scope="split"), uni),
    "two-wing straddle dispersion": (dict(index_legs=STR, singles_structure="straddle", hedge_scope="split"), uni),
    "put-wing dispersion (x1.05)": (dict(index_legs=P25, singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, hedge_scope="split"), uni),
    "put-wing dispersion, ex-event (x1.05)": (dict(index_legs=P25, singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, hedge_scope="split", term_filter="exclude_event"), uni),
    "event selling: short straddles on event names (100 names)": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100),
    "short idx 25d put, hedged": (dict(index_legs=P25, singles_scale=0.0), uni),
    "short idx straddle, delta hedged W": (dict(index_legs=STR, singles_scale=0.0), uni),
}
rows, yrs = {}, {}
for bk, (bw, u) in BOOKS.items():
    for ck, cm in COSTS.items():
        for L in ((1, 3) if ck == "GS 1.5% flat" else (1,)):
            cfg = C(leverage=L, costs=cm, **{**base, **bw}); res = Backtest(cfg, u, iv).run(); e = res.equity.where(res.equity > 0).dropna()
            st = metrics.summary(e, u.spy); sh = metrics.second_half(e); cost = sum(m.costs for m in res.months) / 1e6 / st["years"]
            st.update(sharpe_2h=sh["sharpe"], cost_yr=cost, book=bk, costs=ck, L=L); k = f"{bk} | {ck} | {L}x"; rows[k] = st; yrs[k] = metrics.yearly(e)
            print(f"{bk[:44]:44s} | {ck:13s} | {L}x  cagr={st['cagr']:+.2%} vol={st['vol']:.1%} sharpe={st['sharpe']:+.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} cost/yr={cost:.2%} sh2h={sh['sharpe']:+.2f} 2008={yrs[k].get(2008, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "cost_ladder_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "cost_ladder_years_VOLVUE.csv")
print("DONE")
