"""Honest regime book (5-day-smoothed signals, lagged one trading day) confirmed through the engine with
per-cycle margin, then the ES + book combo grid: ES exposure 30/45/60% of equity (60% = 2x on a 30% sleeve),
ES always or only in corr-lo, book leverage 2..10x on 70% of equity, cash yield on all equity.
Feasibility: peak portfolio margin (TIMS-style stress x 1.5 house factor) + ES initial margin (6% of notional) <= 60% of equity."""
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
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
COMP = {
    "putwing | corr-hi": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI), uni),
    "0.5x idx straddle | corr-hi": (dict(index_legs=(("BXM", +1, 0.5), ("PUT", +1, 0.5)), singles_scale=0.0, trade_mask=HI), uni),
    "callout dispersion | corr-lo": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO), uni),
    "event selling | always": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100),
}
eq, marg = {}, {}
for k, (kw, u) in COMP.items():
    cfg = C(**{**base, **kw}); bt = Backtest(cfg, u, iv); res = bt.run(); eq[k] = res.equity.ffill()
    marg[k] = pd.DataFrame({rec.entry: cycle_margin(rec, cfg, float(bt.rf.loc[rec.entry])) for rec in res.months}).T
    st = metrics.summary(eq[k], uni.spy)
    print(f"{k:30s} traded={sum(1 for m in res.months if m.legs)} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%}", flush=True)
days = next(iter(eq.values())).index
VARIANTS = {"full": list(COMP), "no_straddle": [k for k in COMP if "straddle |" not in k]}
variant = sys.argv[1] if len(sys.argv) > 1 else "full"
keys = VARIANTS[variant]
book = sum(eq[k] - 1e6 for k in keys)
pm1 = sum(marg[k].pm for k in keys) / 1e6; regt1 = sum(marg[k].regt for k in keys) / 1e6     # per 1x equity, by cycle
print(f"\n[{variant}] composite margin per 1x: pm mean={pm1.mean():.0%} p95={pm1.quantile(.95):.0%} max={pm1.max():.0%} | regt mean={regt1.mean():.0%} max={regt1.max():.0%}")
st = metrics.summary(1e6 + book, uni.spy); print(f"[{variant}] book 1x ex cash: cagr={st['cagr']:+.2%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} sh2h={metrics.second_half(1e6 + book)['sharpe']:.2f}")
(1e6 + book).rename("honest_book_1x").to_frame().join(pd.DataFrame({k: eq[k] - 1e6 for k in keys})).to_csv(RESULTS / f"regime_book_honest_{variant}_equity_VOLVUE.csv")
pd.DataFrame({k: marg[k].pm / 1e6 for k in COMP}).to_csv(RESULTS / "regime_margin_components_VOLVUE.csv")
spy = uni.spy.reindex(days).ffill(); rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(marg["event selling | always"].index) + [days[-1]]
def blend(L, w_es, gate, w_book=0.7, es_bp=1e-4):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; es_prev = 0.0
    for a, bb in zip(ent[:-1], ent[1:]):
        es_n = w_es * E if bool(gate.loc[a]) else 0.0; cost = abs(es_n - es_prev) * es_bp
        s = spy.loc[a:bb]; w = book.loc[a:bb]; c = cash_acc.loc[a:bb]
        seg = (E - cost) * (c / c.iloc[0]) + es_n * ((s / s.iloc[0] - 1) - (c / c.iloc[0] - 1)) + w_book * E / 1e6 * L * (w - w.iloc[0])
        out.loc[a:bb] = seg.values; E = float(seg.iloc[-1]); es_prev = es_n * float(s.iloc[-1] / s.iloc[0])
        if E <= 0: out.loc[bb:] = 0.0; break
    return out.ffill()
GATES = {"always": pd.Series(True, index=days), "corr-lo": LO.reindex(days).ffill().fillna(False)}
rows = []
print("\n=== combo grid (cash yield on all equity; honest book) ===")
for w_es in (0.0, 0.3, 0.45, 0.6):
    for g, gm in GATES.items():
        if w_es == 0 and g != "always": continue
        for L in (0, 2, 3, 4, 5, 7, 10):
            if w_es == 0 and L == 0: continue
            e = blend(L, w_es, gm).where(lambda x: x > 0).dropna()
            st = metrics.summary(e, uni.spy); y = metrics.yearly(e)
            pm_peak = 0.7 * L * pm1.max() * 1.5 + w_es * 0.06; pm_typ = 0.7 * L * pm1.quantile(.95) * 1.5 + w_es * 0.06
            regt_peak = 0.7 * L * regt1.max() + w_es * 0.06
            rows.append(dict(es=w_es, gate=g, L=L, **{k: st[k] for k in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "cvar95_m")}, beta=st.get("beta", np.nan),
                             y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022), pm_peak=pm_peak, pm_p95=pm_typ, regt_peak=regt_peak, feasible_pm60=pm_peak <= 0.60))
            print(f"ES {w_es:.0%} {g:7s} book {L:2d}x | cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} | PM peak={pm_peak:.0%} p95={pm_typ:.0%} RegT peak={regt_peak:.0%} {'OK' if pm_peak<=.6 else 'X'}")
df = pd.DataFrame(rows); df.to_csv(RESULTS / f"regime_combo_{variant}_VOLVUE.csv", index=False)
ok = df[df.feasible_pm60 & (df.L > 0)]
print("\nbest by Calmar, PM peak <= 60%:"); print(ok.sort_values("calmar", ascending=False).head(6)[["es", "gate", "L", "cagr", "sharpe", "maxdd", "calmar", "worst_month", "pm_peak"]].round(3).to_string())
print("best by Sharpe, PM peak <= 60%:"); print(ok.sort_values("sharpe", ascending=False).head(6)[["es", "gate", "L", "cagr", "sharpe", "maxdd", "calmar", "worst_month", "pm_peak"]].round(3).to_string())
print("DONE")
