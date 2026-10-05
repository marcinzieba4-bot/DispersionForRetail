"""30/70 blend, rebalanced every third-Friday cycle: 30% of equity as ES futures exposure (regime-gated),
70% of equity as the regime book's capital at L x fixed notional, cash yield (FEDFUNDS) on all equity
(futures tie up no cash; ES carry = SPY total return minus the cash rate). ES cost: 1 bp on turnover."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
eqf = pd.read_csv(RESULTS / "regime_book_equity_VOLVUE.csv", index_col=0, parse_dates=True)
book = eqf["regime_book_1x_excash"] - 1e6
days = book.index; spy = uni.spy.reindex(days).ffill()
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
sig = pd.read_parquet(RESULTS / "regime_signals.parquet").reindex(days, method="ffill")
ent = [d for d in pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index if d in days] + [days[-1]]
HI = sig.bcor_p > 0.75; LO = (sig.bcor < 0.30) & (sig.vix < 0.20)
# how SPY behaves by regime at entry (forward cycle return, annualized)
fwd = pd.Series({a: float(spy.loc[b] / spy.loc[a] - 1) for a, b in zip(ent[:-1], ent[1:])})
reg = pd.Series(np.where(HI.loc[fwd.index], "corr-hi", np.where(LO.loc[fwd.index], "corr-lo", "mid")), index=fwd.index)
print("SPY forward-cycle return by regime at entry (ann. mean / ann. vol / n):")
for k, g in fwd.groupby(reg):
    print(f"  {k:8s} {g.mean()*12:+.1%} / {g.std()*np.sqrt(12):.1%} / {len(g)}")
vb = pd.cut(sig.vix.loc[fwd.index], [0, .15, .20, .25, .30, 9], labels=["<15", "15-20", "20-25", "25-30", ">30"])
print("SPY forward-cycle return by VIX at entry:")
for k, g in fwd.groupby(vb, observed=True):
    print(f"  {k:6s} {g.mean()*12:+.1%} / {g.std()*np.sqrt(12):.1%} / {len(g)}")
GATES = {
    "ES always": pd.Series(True, index=days),
    "ES off in corr-hi": ~HI,
    "ES only if VIX<20": sig.vix < 0.20,
    "ES off in corr-hi & off if VIX>25": (~HI) & (sig.vix < 0.25),
    "ES only in corr-lo": LO,
}
def blend(L, gate, w_es=0.3, w_book=0.7, es_bp=1e-4):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; es_prev = 0.0
    for a, b in zip(ent[:-1], ent[1:]):
        on = bool(gate.loc[a]); es_n = w_es * E if on else 0.0
        cost = abs(es_n - es_prev) * es_bp
        s = spy.loc[a:b]; w = book.loc[a:b]; c = cash_acc.loc[a:b]
        seg = (E - cost) * (c / c.iloc[0]) + es_n * ((s / s.iloc[0] - 1) - (c / c.iloc[0] - 1)) + w_book * E / 1e6 * L * (w - w.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1]); es_prev = es_n * float(s.iloc[-1] / s.iloc[0])
        if E <= 0:
            out.loc[b:] = 0.0; break
    return out.ffill()
rows = {}
def rep(name, e):
    e = e.where(e > 0).dropna()
    st = metrics.summary(e, uni.spy); st["sharpe_2h"] = metrics.second_half(e)["sharpe"]; rows[name] = st
    y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:52s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={st['sharpe_2h']:.2f} | 2008={y.get(2008,np.nan):+.1%} 2020={y.get(2020,np.nan):+.1%} 2022={y.get(2022,np.nan):+.1%} covid={cr.loc['Covid_2020','strategy']:+.1%}")
print("\n=== cash yield on all equity; 30% ES (gated) + 70% capital in the regime book at L x ===")
rep("cash only", 1e6 * cash_acc)
rep("30% ES always + 70% cash (no book)", blend(0, GATES["ES always"]))
for L in (3, 5, 10):
    print(f"\n-- book {L}x on the 70% (= {0.7*L:.1f}x equity in book notional)")
    rep(f"70% book {L}x, no ES", blend(L, pd.Series(False, index=days)))
    for g, m in GATES.items():
        rep(f"30% {g} + 70% book {L}x", blend(L, m))
pd.DataFrame(rows).T.to_csv(RESULTS / "regime_blend_VOLVUE.csv")
e10 = blend(10, GATES["ES off in corr-hi"]); print("\nyearly, 30% ES off in corr-hi + 70% book 10x: " + " ".join(f"{k}={v:+.0%}" for k, v in metrics.yearly(e10).items()))
print("DONE")
