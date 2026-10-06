"""(a) Account size: the regime book at 3.75x with whole-contract rounding at $100k..$3M vs the continuous model.
(b) Without portfolio margin: Reg-T usage ladder for momentum + book + TLT (stocks 50%, TLT 50% or 4% via /ZB
futures, book at its per-cycle Reg-T requirement)."""
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
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
b = sig.bcor.rolling(5).mean(); v = sig.vix.rolling(5).mean(); bp = b.rolling(504, min_periods=250).rank(pct=True)
HI = (bp > 0.75).shift(1).fillna(False); LO = ((b < 0.30) & (v < 0.20)).shift(1).fillna(False)
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
SL = {"PW": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI), uni),
      "CO": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO), uni),
      "EV": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100)}
Lb = 3.75
days = pd.read_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv", index_col=0, parse_dates=True).index
print(f"=== (a) regime book at {Lb}x, whole contracts, by account size (P&L as % of equity, ex cash) ===")
regt1 = {}
rows = []
for E in (None, 100e3, 250e3, 500e3, 1e6, 3e6):
    tot = pd.Series(0.0, index=days); names_tr = {}
    for k, (kw, u) in SL.items():
        cfg = C(**{**base, **kw, "leverage": Lb, "equity": E or 1e6, "contract_granularity": E is not None}); bt = Backtest(cfg, u, iv); res = bt.run()
        e = res.equity.ffill().reindex(days).ffill(); tot = tot + (e - (E or 1e6)) / (E or 1e6)
        tr = [m for m in res.months if m.legs]; names_tr[k] = np.mean([len(m.names) - len([s for s in m.skipped if not str(s).startswith(("ADD", "STOP"))]) for m in tr]) if tr else 0
        if E is None:
            regt1[k] = pd.Series({m.entry: cycle_margin(m, cfg, float(bt.rf.loc[m.entry]))["regt"] / 1e6 / Lb for m in res.months})   # per 1x
    eq = 1e6 * (1 + tot); st = metrics.summary(eq, uni.spy); y = metrics.yearly(eq)
    label = "continuous" if E is None else f"${E/1e3:.0f}k"
    print(f"{label:12s} cagr={st['cagr']:+.2%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} | names traded/cycle: PW {names_tr['PW']:.1f} CO {names_tr['CO']:.1f} EV {names_tr['EV']:.1f} | 2008={y.get(2008):+.1%} 2020={y.get(2020):+.1%} 2022={y.get(2022):+.1%}", flush=True)
    rows.append(dict(equity=label, **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "worst_month")}, **{f"names_{k}": names_tr[k] for k in SL}))
pd.DataFrame(rows).to_csv(RESULTS / "size_granularity_VOLVUE.csv", index=False)
regt_book = sum(regt1.values()).reindex(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index).fillna(0.0)
print(f"\nbook Reg-T per 1x by cycle: median={regt_book.median():.0%} p95={regt_book.quantile(.95):.0%} max={regt_book.max():.0%}")
# (b) Reg-T ladder
L_ = pd.read_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv", index_col=0, parse_dates=True); book0 = L_["book_base"]
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
TLT = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True)["TLT trend 200d"].reindex(days).ffill().fillna(0.0)
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(regt_book.index) + [days[-1]]
tlt_on = pd.Series({a: float(TLT.loc[b_] - TLT.loc[a]) != 0.0 for a, b_ in zip(ent[:-1], ent[1:])})
def run(w_mom, L_book, w_tlt, tlt_m):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; usage = pd.Series(np.nan, index=days)
    for a, b_ in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b_]; seg = E * (c / c.iloc[0])
        for w, p in ((w_mom, M10), (L_book, book0), (w_tlt, TLT)):
            q = p.loc[a:b_]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        req = E * (0.50 * w_mom + float(regt_book.get(a, 0.0)) * L_book + tlt_m * w_tlt * float(tlt_on.get(a, True)))
        usage.loc[a:b_] = (req / seg).values; out.loc[a:b_] = seg.values; E = float(seg.iloc[-1])
    return out.ffill(), usage.ffill()
print("\n=== (b) Reg-T (no portfolio margin): mom w + book L + TLT w, usage = Reg-T requirement / equity ===")
rows = []
for tlt_name, tlt_m in (("TLT shares (50%)", 0.50), ("TLT via /ZB futures (4%)", 0.04)):
    print(f"-- {tlt_name}")
    for wm, lb, wt in ((0.3, 0.5, 0.15), (0.45, 0.75, 0.22), (0.6, 1.0, 0.3), (0.75, 1.25, 0.38), (0.9, 1.5, 0.45), (1.0, 1.0, 0.5), (1.0, 2.0, 0.5), (0.75, 3.75, 0.38), (1.0, 5.0, 0.5)):
        e, u = run(wm, lb, wt, tlt_m); st = metrics.summary(e, uni.spy); y = metrics.yearly(e)
        print(f"mom {wm:.0%} book {lb:.2f}x TLT {wt:.0%}: cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} | RegT usage median={u.median():.0%} p95={u.quantile(.95):.0%} max={u.max():.0%} {'OK' if u.max() <= 1 else 'CALL'}", flush=True)
        rows.append(dict(tlt=tlt_name, w_mom=wm, L_book=lb, w_tlt=wt, **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, regt_med=u.median(), regt_p95=u.quantile(.95), regt_max=u.max()))
pd.DataFrame(rows).to_csv(RESULTS / "regt_ladder_VOLVUE.csv", index=False)
print("DONE")
