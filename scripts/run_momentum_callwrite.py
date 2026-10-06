"""Momentum stocks + trailing stop + short SPY 20-delta call (income overlay), standalone and inside the combo.
The 20d SPY call has no Cboe real-price index, so it is priced two ways: (a) the model at the skew-adjusted
VolVue SPY IV (index_skew_coeff=0.39, the calibration measured against live quotes), unhedged, and (b) the
model 30d call under the same settings compared with the real BXMD overlay to show how much the model
overstates a short index call; the BXMD 30d overlay itself is shown as the real-price alternative."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.momentum import momentum_leg
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig
hb = pd.read_csv(RESULTS / "regime_book_honest_no_straddle_equity_VOLVUE.csv", index_col=0, parse_dates=True)
book = hb["honest_book_1x"] - 1e6; days = book.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
legs = pd.read_csv(RESULTS / "regime_putcall_legs_VOLVUE.csv", index_col=0, parse_dates=True)
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="book", hedge_freq=None, singles_scale=0.0)
ov = {}
for k, kw in {"model 20d call, skew-adj": dict(index_leg="model", index_delta=20, index_skew_coeff=0.39),
              "model 30d call, skew-adj": dict(index_leg="model", index_delta=30, index_skew_coeff=0.39),
              "model 30d call, flat IV": dict(index_leg="model", index_delta=30),
              "BXMD 30d call (real)": dict(index_leg="BXMD")}.items():
    res = Backtest(C(**{**base, **kw}), uni, iv).run(); ov[k] = (res.equity.ffill() - 1e6).reindex(days).ffill().fillna(0.0)
    prem = np.mean([m.net_premium for m in res.months if m.legs]) / 1e6
    print(f"{k:28s} overlay P&L {ov[k].iloc[-1]/1e6/19.6:+.2%}/yr on 1x notional, avg premium collected {prem*12:.1%}/yr", flush=True)
ratio = ov["BXMD 30d call (real)"].iloc[-1] / ov["model 30d call, skew-adj"].iloc[-1] if ov["model 30d call, skew-adj"].iloc[-1] else np.nan
print(f"real BXMD / model 30d skew-adj cumulative P&L ratio: {ratio:.2f}")
def compounded(p):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        q = p.loc[a:b]; seg = E * (1 + (q - q.iloc[0]) / 1e6); out.loc[a:b] = seg.values; E = max(float(seg.iloc[-1]), 1.0)
    return out.ffill()
def blend(L, w, q, w_book=0.7):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; w_ = book.loc[a:b]; qq = q.loc[a:b]
        seg = E * (c / c.iloc[0]) + w * E / 1e6 * (qq - qq.iloc[0]) + w_book * E / 1e6 * L * (w_ - w_.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:58s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}", flush=True)
    return st, y
p_tr, _, _ = momentum_leg(uni, n=5, trail=0.15); p_tr = p_tr.reindex(days).ffill().fillna(0.0)
p_tt, _, _ = momentum_leg(uni, n=5, trend=200, trail=0.15); p_tt = p_tt.reindex(days).ffill().fillna(0.0)
SL = {"mom5 trail 15%": p_tr, "mom5 trend + trail 15%": p_tt}
for base_k, p in list(SL.items()):
    for ok in ("model 20d call, skew-adj", "BXMD 30d call (real)"):
        for sc in (1.0, 0.5):
            SL[f"{base_k} + short SPY {ok.split(' ')[1] if 'model' in ok else 'BXMD 30d'} x{sc:.1f}"] = p + sc * ov[ok]
rows = []
print("\n=== standalone, 100% of equity compounded, ex cash ===")
for k, p in SL.items():
    st, y = rep(k, compounded(p)); rows.append(dict(variant=k, scope="standalone", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, beta=st.get("beta"), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
for k in ("model 20d call, skew-adj", "BXMD 30d call (real)"):
    rep(f"overlay alone: short SPY {k}", compounded(ov[k]))
print("\n=== inside the combo: w x equity in the sleeve + 70% of equity in the honest book at L x, cash on all equity ===")
rep("ES 30% + book 3x", blend(3, 0.3, legs["ES"].reindex(days).ffill()))
for k, p in SL.items():
    for w, L in ((0.3, 3), (0.6, 3), (0.6, 4)):
        st, y = rep(f"{k} w={w:.0%} L={L}", blend(L, w, p)); rows.append(dict(variant=k, scope=f"w={w} L={L}", **{c: st[c] for c in ("cagr", "vol", "sharpe", "maxdd", "calmar", "worst_month")}, beta=st.get("beta"), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
pd.DataFrame(rows).to_csv(RESULTS / "momentum_callwrite_VOLVUE.csv", index=False)
pd.DataFrame({**SL, **{f"overlay {k}": v for k, v in ov.items()}}).to_csv(RESULTS / "momentum_callwrite_legs_VOLVUE.csv")
print("DONE")
