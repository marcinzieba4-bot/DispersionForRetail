"""Regime-switching dispersion book, confirmed through the engine with trade masks (fixed notional, retail costs).
  corr-hi  (basket implied corr trailing-2y pct > 0.75): put-wing dispersion (short SPY 25d put, long single 25d puts, split hedge)
                                                         + 0.5x short SPY straddle, delta hedged weekly
  corr-lo  (basket implied corr < 0.30 and VIX < 20):    outright-call dispersion (short SPY 30d call, long single 30d calls, split hedge)
  always:                                                event selling (short straddles on event names, book hedge)
  otherwise: cash."""
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
HI = sig.bcor_p > 0.75
LO = (sig.bcor < 0.30) & (sig.vix < 0.20)
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
COMP = {
    "putwing | corr-hi": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI), uni),
    "0.5x idx straddle | corr-hi": (dict(index_legs=(("BXM", +1, 0.5), ("PUT", +1, 0.5)), singles_scale=0.0, trade_mask=HI), uni),
    "callout dispersion | corr-lo": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO), uni),
    "event selling | always": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100),
    "putwing | always (reference)": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05), uni),
}
eq, rows = {}, {}
def rep(name, e, L=1):
    st = metrics.summary(e, uni.spy); y = metrics.yearly(e); sh = metrics.second_half(e)
    rows[name] = dict(st, L=L, sharpe_2h=sh["sharpe"], y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022))
    print(f"{name:44s} L={L} cagr={st['cagr']:+.2%} vol={st['vol']:.1%} sharpe={st['sharpe']:+.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} sh2h={sh['sharpe']:+.2f} 2008={y.get(2008,np.nan):+.1%} 2020={y.get(2020,np.nan):+.1%} 2022={y.get(2022,np.nan):+.1%}", flush=True)
for k, (kw, u) in COMP.items():
    res = Backtest(C(**{**base, **kw}), u, iv).run()
    e = res.equity.ffill(); eq[k] = e; rep(k, e)
    print(f"   months traded: {sum(1 for m in res.months if m.legs)} / {len(res.months)}")
pnl = pd.DataFrame({k: v - 1e6 for k, v in eq.items()})
book = pnl[["putwing | corr-hi", "0.5x idx straddle | corr-hi", "callout dispersion | corr-lo", "event selling | always"]].sum(axis=1)
cash = (1 + R.fedfunds_daily(book.index) / 252.0).cumprod()
print("\n=== regime book (sum of components; fixed notional so P&L is additive) ===")
for L in (1, 2, 3, 5):
    e = 1e6 + L * book; rep(f"regime book ex cash", e, L)
    e2 = 1e6 * cash + L * book; rep(f"regime book + cash yield", e2, L)
# engine check that leverage scaling holds (commission caps aside)
res3 = Backtest(C(**{**base, **COMP["putwing | corr-hi"][0], "leverage": 3}), uni, iv).run()
e3 = res3.equity.ffill(); print("\nengine 3x putwing|corr-hi vs 3x scaled 1x: final P&L %.0f vs %.0f" % (e3.iloc[-1] - 1e6, 3 * (eq["putwing | corr-hi"].iloc[-1] - 1e6)))
pd.DataFrame(rows).T.to_csv(RESULTS / "regime_book_VOLVUE.csv")
(1e6 + book).rename("regime_book_1x_excash").to_frame().join(pnl).to_csv(RESULTS / "regime_book_equity_VOLVUE.csv")
print("DONE")
