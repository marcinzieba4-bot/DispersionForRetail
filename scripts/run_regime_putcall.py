"""Replace the ES leg of the combo with option structures priced off real Cboe indices, unhedged:
   PUT (+1)                 short ATM SPX put, cash-collateralised
   PUT (+1) + BXMD (-1)     short ATM put + long 30d call  (the asked-for structure)
   RXM (+1)                 short 25d put + long 25d call (Cboe risk reversal)
   BXMD (+1) on SPX TR      covered call (long index + short 30d call): the engine's BXMD overlay plus ES
Each leg is run through the engine (fixed notional, no delta hedge, retail costs), then blended:
w x equity in the leg, 70% of equity as the honest regime book at L x, cash yield on all equity, rebalanced each cycle."""
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
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="book", hedge_freq=None, singles_scale=0.0)
LEGS = {
    "short ATM put (PUT)": (("PUT", +1, 1.0),),
    "short ATM put + long 30d call": (("PUT", +1, 1.0), ("BXMD", -1, 1.0)),
    "short 25d put + long 25d call (RXM)": (("RXM", +1, 1.0),),
    "covered call overlay (BXMD) + ES": (("BXMD", +1, 1.0),),
}
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True)
book = hb["honest_book_1x"] - 1e6; days = book.index
spy = uni.spy.reindex(days).ffill(); rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
es_pnl = pd.Series(0.0, index=days); run = 0.0                 # ES on a FIXED 1M notional per cycle, carry = TR - cash
for a, b in zip(ent[:-1], ent[1:]):
    s_ = spy.loc[a:b]; c_ = cash_acc.loc[a:b]
    seg = run + 1e6 * ((s_ / s_.iloc[0] - 1) - (c_ / c_.iloc[0] - 1)); es_pnl.loc[a:b] = seg.values; run = float(seg.iloc[-1])
leg_pnl, leg_pm, leg_regt = {"ES": es_pnl}, {"ES": 0.0}, {"ES": 0.0}
for k, legs in LEGS.items():
    cfg = C(**{**base, "index_legs": legs}); bt = Backtest(cfg, uni, iv); res = bt.run()
    p = (res.equity.ffill() - 1e6).reindex(days).ffill()
    if "+ ES" in k:
        p = p + es_pnl
    leg_pnl[k] = p
    mg = pd.DataFrame({rec.entry: cycle_margin(rec, cfg, float(bt.rf.loc[rec.entry])) for rec in res.months}).T / 1e6
    leg_pm[k] = float(mg.pm.max()); leg_regt[k] = float(mg.regt.max())
print("=== equity-replacement legs on their own, 100% of equity notional, ex cash (ES = SPY TR - cash) ===")
for k, p in leg_pnl.items():
    e = 1e6 + p; st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{k:38s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} down_beta={st.get('down_beta',np.nan):.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%} | PM peak/1x={leg_pm[k]:.0%} RegT peak/1x={leg_regt[k]:.0%}", flush=True)
pm_book = pd.read_csv(RESULTS / "regime_margin_components_VOLVUE.csv", index_col=0, parse_dates=True)
pm_book1 = pm_book[[c for c in pm_book.columns if "straddle |" not in c]].sum(axis=1).max()
def blend(L, w, p_leg, w_book=0.7):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; w_ = book.loc[a:b]; q = p_leg.loc[a:b]
        seg = E * (c / c.iloc[0]) + w * E / 1e6 * (q - q.iloc[0]) + w_book * E / 1e6 * L * (w_ - w_.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
rows = []
print("\n=== combo: w x equity in the leg + 70% of equity in the honest book at L x, cash on all equity ===")
for k, p in leg_pnl.items():
    for w in (0.3, 0.45, 0.6):
        for L in (0, 3, 4):
            e = blend(L, w, p).where(lambda x: x > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e)
            pm = 0.7 * L * pm_book1 * 1.5 + w * (leg_pm[k] * 1.5 if k != "ES" else 0.06) + (w * 0.06 if "+ ES" in k else 0)
            rows.append(dict(leg=k, w=w, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month")}, beta=st.get("beta", np.nan), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022), pm_peak=pm))
            print(f"{k:38s} w={w:.0%} L={L} | cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} | PM peak={pm:.0%}")
pd.DataFrame(rows).to_csv(RESULTS / "regime_putcall_VOLVUE.csv", index=False)
pd.DataFrame(leg_pnl).to_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv")
print("DONE")
