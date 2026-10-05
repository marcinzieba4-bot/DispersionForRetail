"""Sleeve P&L for regime analysis: fixed notional, ex cash, retail costs, third-Friday cycle.
Fixed notional makes monthly P&L additive, so any ratio/regime combination of sleeves is a sum of monthly rows."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest.engine import Backtest
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data.paths import RESULTS
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
SLEEVES = {
    "idx_short_straddle": dict(index_legs=(("BXM", +1, 1.0), ("PUT", +1, 1.0)), singles_scale=0.0),
    "idx_short_call30": dict(index_leg="BXMD", singles_scale=0.0),
    "idx_short_put25": dict(index_legs=(("RXM", +1, 1.0),), singles_scale=0.0),
    "sn_long_straddle": dict(index_notional_scale=0.0, singles_structure="straddle"),
    "sn_long_put25": dict(index_notional_scale=0.0, singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05),
    "sn_long_vertical_30_10": dict(index_notional_scale=0.0, singles_structure="vertical"),
    "sn_long_call30": dict(index_notional_scale=0.0, singles_structure="vertical", short_wing_delta=None),
    "sn_short_straddle_event": dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"),
}
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
pnl = {}
for k, kw in SLEEVES.items():
    u = uni100 if kw.get("n_names") == 100 else uni
    res = Backtest(C(**{**base, **kw}), u, iv).run()
    ent = [m.entry for m in res.months] + [res.months[-1].expiry]
    e = res.equity.ffill()
    p = pd.Series([float(e.loc[ent[i + 1]] - e.loc[ent[i]]) for i in range(len(ent) - 1)], index=ent[:-1]) / 1e6
    pnl[k] = p
    print(f"{k:26s} mean/m={p.mean():+.3%} sd={p.std():.2%} sharpe={p.mean()/p.std()*np.sqrt(12):+.2f} min={p.min():+.1%} n={len(p)}", flush=True)
pd.DataFrame(pnl).to_csv(RESULTS / "regime_sleeves_VOLVUE.csv")
print("DONE")
