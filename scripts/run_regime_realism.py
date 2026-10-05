"""Realism checks for the regime book: (a) per-cycle Reg-T and portfolio margin of each component at 1x,
(b) components re-run with the regime signal lagged one trading day."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.backtest.margin import cycle_margin
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data.paths import RESULTS
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
HI = sig.bcor_p > 0.75; LO = (sig.bcor < 0.30) & (sig.vix < 0.20)
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
COMP = {
    "putwing | corr-hi": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05), uni, HI),
    "0.5x idx straddle | corr-hi": (dict(index_legs=(("BXM", +1, 0.5), ("PUT", +1, 0.5)), singles_scale=0.0), uni, HI),
    "callout dispersion | corr-lo": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None), uni, LO),
    "event selling | always": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100, None),
}
marg, eq, eq_lag = {}, {}, {}
for k, (kw, u, m) in COMP.items():
    cfg = C(**{**base, **kw, "trade_mask": m}); bt = Backtest(cfg, u, iv); res = bt.run(); eq[k] = res.equity.ffill()
    rows = {}
    for rec in res.months:
        r = float(bt.rf.loc[rec.entry]); mg = cycle_margin(rec, cfg, r); rows[rec.entry] = mg
    marg[k] = pd.DataFrame(rows).T
    traded = marg[k][marg[k].opt_notional > 0]
    print(f"{k:30s} months traded={len(traded)}  per 1x equity when on: regt mean={traded.regt.mean()/1e6:.0%} max={traded.regt.max()/1e6:.0%} | pm mean={traded.pm.mean()/1e6:.0%} max={traded.pm.max()/1e6:.0%} | opt notional={traded.opt_notional.mean()/1e6:.0%} stock hedge={traded.stock_hedge.mean()/1e6:.0%}", flush=True)
    if m is not None:
        cfg2 = C(**{**base, **kw, "trade_mask": m.shift(1)}); eq_lag[k] = Backtest(cfg2, u, iv).run().equity.ffill()
    else:
        eq_lag[k] = eq[k]
book = sum(e - 1e6 for e in eq.values()); book_lag = sum(e - 1e6 for e in eq_lag.values())
for name, e in (("regime book 1x ex cash", 1e6 + book), ("regime book 1x ex cash, signal lagged 1 day", 1e6 + book_lag)):
    st = metrics.summary(e, uni.spy); y = metrics.yearly(e)
    print(f"{name:48s} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} 2008={y.get(2008):+.1%} 2020={y.get(2020):+.1%} 2022={y.get(2022):+.1%}")
tot = sum(m[["regt", "pm"]] for m in marg.values()); tot["opt_notional"] = sum(m.opt_notional for m in marg.values())
tot = tot / 1e6
print(f"\ncomposite regime book per 1x equity: regt mean={tot.regt.mean():.0%} p95={tot.regt.quantile(.95):.0%} max={tot.regt.max():.0%} | pm mean={tot.pm.mean():.0%} p95={tot.pm.quantile(.95):.0%} max={tot.pm.max():.0%} | opt notional max={tot.opt_notional.max():.0%}")
print("peak-margin months (pm):"); print(tot.sort_values("pm", ascending=False).head(5).round(3).to_string())
tot.to_csv(RESULTS / "regime_margin_VOLVUE.csv")
pd.DataFrame({"book_lag1": 1e6 + book_lag}).to_csv(RESULTS / "regime_book_lag_VOLVUE.csv")
print("DONE")
