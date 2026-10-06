"""Regime allocator: shuffle weights across sleeves by regime (implied-correlation tercile x SPY 200d trend),
fitted on one half of the sample and tested on the other, then the full-sample table at 1x / 3x / 5x.
Atoms (1x = sleeve notional equal to equity, fixed notional per third-Friday cycle, retail costs, ex cash):
  PW  put-wing dispersion (short SPY 25d put + long single 25d puts, split hedge), unconditional
  CO  outright-call dispersion (short SPY 30d call + long single 30d calls, split hedge), unconditional
  IP  short SPY 25d put alone, delta hedged weekly
  EV  event selling (short straddles on event names, book hedge)
  MOM top-5 12-1 momentum stocks, 15% trailing stop (no trend filter: trend is a regime variable here)
  ES  SPY total return minus cash (futures)"""
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
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True); days = hb.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split")
atoms = {}
for k, kw in {"PW": dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05),
              "CO": dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None),
              "IP": dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_scale=0.0)}.items():
    atoms[k] = (Backtest(C(**{**base, **kw}), uni, iv).run().equity.ffill() - 1e6).reindex(days).ffill().fillna(0.0)
atoms["EV"] = hb["event selling | always"]
atoms["MOM"] = pd.read_csv(RESULTS / "momentum_reentry_legs_VOLVUE.csv", index_col=0, parse_dates=True)["trail 15%, no re-entry"].reindex(days).ffill().fillna(0.0)
atoms["ES"] = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)["ES"].reindex(days).ffill().fillna(0.0)
A = pd.DataFrame(atoms); A.to_csv(RESULTS / "alloc_atoms_daily_VOLVUE.csv")
# cycle P&L per atom, % of 1x notional
cy = pd.DataFrame({k: [float(v.loc[b] - v.loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k, v in atoms.items()}, index=ent[:-1])
# regimes at entry: 5-day smoothed, lagged one day
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
b5 = sig.bcor.rolling(5).mean(); bp = b5.rolling(504, min_periods=250).rank(pct=True).shift(1); v5 = sig.vix.rolling(5).mean().shift(1)
spy = uni.spy; tr = (spy > spy.rolling(200).mean()).shift(1)
S = pd.DataFrame({"bp": bp, "vix": v5, "trend": tr}).reindex(cy.index, method="ffill")
S["corr"] = pd.cut(S.bp, [-0.01, 0.33, 0.75, 1.01], labels=["lo", "mid", "hi"]).astype(str); S.loc[S.bp.isna(), "corr"] = "mid"
S["trend"] = S.trend.fillna(True).astype(bool); S["cell"] = S["corr"] + "/" + np.where(S.trend, "up", "down")
S["vixb"] = np.where(S.vix < 0.20, "vix<20", "vix>=20")
PM1 = {"PW": 0.06, "CO": 0.06, "IP": 0.05, "EV": 0.03, "MOM": 0.25, "ES": 0.06}      # margin per 1x notional (PM, before house factor)
print("=== cycle P&L by regime cell (corr tercile / SPY vs 200d): annualised mean [Sharpe] (n) per atom, 1x notional ===")
cells = ["lo/up", "lo/down", "mid/up", "mid/down", "hi/up", "hi/down"]
for c in cells:
    m = S.cell == c; row = []
    for k in cy.columns:
        x = cy.loc[m, k]; row.append(f"{k} {x.mean()*12:+5.1%} [{x.mean()/x.std()*np.sqrt(12) if x.std()>0 else 0:+.2f}]")
    print(f"{c:9s} n={m.sum():3d} | " + "  ".join(row))
print("\n--- same, by VIX bucket x trend (check) ---")
for c in ["vix<20/up", "vix<20/down", "vix>=20/up", "vix>=20/down"]:
    m = (S.vixb + "/" + np.where(S.trend, "up", "down")) == c; row = []
    for k in cy.columns:
        x = cy.loc[m, k]; row.append(f"{k} {x.mean()*12:+5.1%} [{x.mean()/x.std()*np.sqrt(12) if x.std()>0 else 0:+.2f}]")
    print(f"{c:12s} n={m.sum():3d} | " + "  ".join(row))
def fit(idx, min_sh=0.3, target=0.10, cap=1.5):
    """per cell: weight = Sharpe/vol for atoms with Sharpe > min_sh, scaled so the cell portfolio has `target` annual vol (cap per atom)."""
    W = {}
    for c in cells:
        m = (S.cell == c) & idx
        if m.sum() < 8:
            W[c] = {k: 0.0 for k in cy.columns}; continue
        x = cy.loc[m]; mu = x.mean() * 12; sd = x.std() * np.sqrt(12); sh = mu / sd
        w = {k: (sh[k] / sd[k] if sh[k] > min_sh else 0.0) for k in cy.columns}
        p = (x * pd.Series(w)).sum(axis=1); v = p.std() * np.sqrt(12)
        s = target / v if v > 0 else 0.0
        W[c] = {k: float(np.clip(w[k] * s, 0, cap)) for k in cy.columns}
    return W
def apply(W, idx):
    w = pd.DataFrame([W[c] for c in S.loc[idx, "cell"]], index=S.index[idx])
    return (cy.loc[idx] * w).sum(axis=1), w
def st_m(p):
    p = p.dropna(); eq = (1 + p).cumprod(); dd = (eq / eq.cummax() - 1).min()
    return p.mean() * 12, p.mean() / p.std() * np.sqrt(12) if p.std() > 0 else np.nan, dd, p.min()
h1 = cy.index <= pd.Timestamp("2016-12-31"); h2 = ~h1
print("\n=== out-of-sample check: fit weights on one half, apply to the other (cycle P&L, 1x, ex cash) ===")
for name, fi, te in (("fit 2007-16 -> test 2017-26", h1, h2), ("fit 2017-26 -> test 2007-16", h2, h1)):
    W = fit(fi); p_te, _ = apply(W, te); p_in, _ = apply(W, fi)
    a, sh, dd, wm = st_m(p_te); ai, shi, ddi, _ = st_m(p_in)
    print(f"{name}: in-sample ann={ai:+.1%} sharpe={shi:.2f} maxdd={ddi:+.1%} | OUT-OF-SAMPLE ann={a:+.1%} sharpe={sh:.2f} maxdd={dd:+.1%} worst={wm:+.1%}")
    for c in cells:
        print(f"   {c:9s} " + " ".join(f"{k}={W[c][k]:.2f}" for k in cy.columns))
# static references on the same cycle P&L
print("\n=== static references (cycle P&L, 1x atoms, ex cash) ===")
for name, w in (("honest regime book (PW hi, CO lo, EV)", None), ("MOM alone", {"MOM": 1}), ("ES alone", {"ES": 1}), ("EV alone", {"EV": 1})):
    if w is None:
        p = (cy.PW * (S["corr"] == "hi") + cy.CO * ((S["corr"] == "lo") & (S.vix < 0.20)) + cy.EV)
    else:
        p = sum(cy[k] * v for k, v in w.items())
    a, sh, dd, wm = st_m(p); print(f"{name:40s} ann={a:+.1%} sharpe={sh:.2f} maxdd={dd:+.1%} worst={wm:+.1%}")
# full-sample table and daily equity at leverage
W = fit(np.ones(len(cy), bool))
print("\n=== full-sample regime weight table (per 1x; the deployable table) ===")
for c in cells:
    n = (S.cell == c).sum(); print(f"{c:9s} (n={n:3d}) " + " ".join(f"{k}={W[c][k]:.2f}" for k in cy.columns) + f"   gross={sum(W[c].values()):.2f}  PM/1x={sum(W[c][k]*PM1[k] for k in cy.columns):.0%}")
pd.DataFrame(W).T.to_csv(RESULTS / "alloc_weights_VOLVUE.csv")
def equity(L, w_table):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; pm = []
    for a, b in zip(ent[:-1], ent[1:]):
        w = w_table[S.loc[a, "cell"]]; c = cash_acc.loc[a:b]
        seg = E * (c / c.iloc[0])
        for k, wk in w.items():
            q = A[k].loc[a:b]; seg = seg + L * wk * E / 1e6 * (q - q.iloc[0])
        pm.append(L * sum(wk * PM1[k] * (1.5 if k != "ES" else 1.0) for k, wk in w.items()))
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill(), pd.Series(pm, index=ent[:-1])
rows = {}
print("\n=== regime allocator, daily equity, cash yield on all equity ===")
for L in (1, 2, 3, 5):
    e, pm = equity(L, W); e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    st.update(L=L, pm_peak=pm.max(), pm_p95=pm.quantile(.95), sharpe_2h=metrics.second_half(e)["sharpe"]); rows[L] = st
    print(f"L={L}x cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={st['sharpe_2h']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%} | PM peak={pm.max():.0%} p95={pm.quantile(.95):.0%}")
    if L == 5:
        print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
pd.DataFrame(rows).T.to_csv(RESULTS / "alloc_leverage_VOLVUE.csv")
print("DONE")
