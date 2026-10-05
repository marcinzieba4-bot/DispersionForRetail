"""Equal vs market-cap weighted single-name legs in the dispersion books.
Index legs from real prices; both sides hedged separately; fixed notional 1x."""
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
P25 = (("RXM", +1, 1.0), ("BXMD", +1, 1.0)); STR = (("BXM", +1, 1.0), ("PUT", +1, 1.0))
base = dict(cycle="third_friday", dividends=True, costs=R, fixed_notional=True, equity=1e6, hedge_scope="split", leverage=1)
books = {
    "spec: long 30-10 verticals vs short idx 30d call (BXMD)": dict(index_leg="BXMD", singles_structure="vertical"),
    "long 30d calls vs short idx 30d call (BXMD)": dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None),
    "long single straddles vs short idx straddle (BXM+PUT)": dict(index_legs=STR, singles_structure="straddle"),
    "put-wing: long single 25d puts vs short idx 25d put (x1.05)": dict(index_legs=P25, singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05),
    "long single wings vs short idx 25d put (x1.05)": dict(index_legs=P25, singles_structure="wings", short_wing_delta=None, single_put_iv_mult=1.05),
    "long 30d calls only, stock-hedged (no index)": dict(index_notional_scale=0.0, singles_structure="vertical", short_wing_delta=None),
}
weightings = {"equal(top30 liq)": dict(), "mcap(top30 liq)": dict(weighting="mcap"), "mcap cap10%(top30 liq)": dict(weighting="mcap", weight_cap=0.10),
              "sqrt_mcap(top30 liq)": dict(weighting="sqrt_mcap"), "mcap(top30 by cap)": dict(weighting="mcap", select="mcap"),
              "mcap(top50 by cap, cap8%)": dict(weighting="mcap", select="mcap", n_names=50, weight_cap=0.08)}
uni50 = load_universe(); uni50.top_liq = 60; uni50._cache = {}
rows, yrs = {}, {}
for bk, bkw in books.items():
    for wk, ww in weightings.items():
        if "top50" in wk and "no index" not in bk and "put-wing" not in bk and "spec" not in bk:
            continue
        u = uni50 if "top50" in wk else uni
        cfg = C(**{**base, **bkw, **ww}); res = Backtest(cfg, u, iv).run(); e = res.equity.where(res.equity > 0).dropna()
        st = metrics.summary(e, u.spy); sh = metrics.second_half(e); st.update(sharpe_2h=sh["sharpe"])
        wmax = np.mean([max(abs(l.units) * l.S0 for l in m.legs if not l.is_index) / max(sum(abs(l.units) * l.S0 for l in m.legs if not l.is_index and l.kind == ("put" if cfg.singles_structure == "put" else "call") and (cfg.singles_structure != "vertical" or l.bucket == 30)), 1) for m in res.months if m.legs])
        k = f"{bk} | {wk}"; rows[k] = st; yrs[k] = metrics.yearly(e)
        print(f"{k:88s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} maxw={wmax:.0%} 2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "weighting_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "weighting_years_VOLVUE.csv")
print("DONE")
