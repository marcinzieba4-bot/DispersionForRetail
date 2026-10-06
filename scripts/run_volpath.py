"""Vol-path marks (daily VolVue IV on single-name / model legs, strikes fixed) for the regime book and the TLT
straddle; the combined book re-run; then portfolio vol targeting and a drawdown circuit breaker."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.backtest.margin import cycle_margin
from dispersion.tlt_straddle import tlt_option_leg
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS, CACHE
uni = load_universe(); iv = VolVueIV("iv_call_30"); C = config.StrategyConfig
uni100 = load_universe(); uni100.top_liq = 100; uni100._cache = {}
sig = pd.read_parquet(RESULTS / "regime_signals.parquet")
b = sig.bcor.rolling(5).mean(); v = sig.vix.rolling(5).mean(); bp = b.rolling(504, min_periods=250).rank(pct=True)
HI = (bp > 0.75).shift(1).fillna(False); LO = ((b < 0.30) & (v < 0.20)).shift(1).fillna(False)
base = dict(cycle="third_friday", dividends=True, fixed_notional=True, equity=1e6, cash_yield=False, costs=config.REALISTIC_SPY, hedge_scope="split", vol_path_marks=True)
SL = {"PW": (dict(index_legs=(("RXM", +1, 1.0), ("BXMD", +1, 1.0)), singles_structure="put", short_wing_delta=None, single_put_iv_mult=1.05, trade_mask=HI), uni),
      "CO": (dict(index_leg="BXMD", singles_structure="vertical", short_wing_delta=None, trade_mask=LO), uni),
      "EV": (dict(index_notional_scale=0.0, singles_structure="straddle", singles_sign=-1, term_filter="event_only", n_names=100, hedge_scope="book"), uni100)}
old = pd.read_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = old.index
runs, pmv = {}, {}
print("=== regime book sleeves, 1x ex cash: entry-IV marks vs vol-path marks ===")
for k, (kw, u) in SL.items():
    cfg = C(**{**base, **kw}); bt = Backtest(cfg, u, iv); res = bt.run(); e = res.equity.ffill().reindex(days).ffill(); runs[k] = e - 1e6
    pmv[k] = pd.Series({m.entry: cycle_margin(m, cfg, float(bt.rf.loc[m.entry]))["pm"] / 1e6 for m in res.months})
    for name, p in ((f"{k} entry-IV marks", old[f"{k}|base"]), (f"{k} vol-path marks", runs[k])):
        st = metrics.summary(1e6 + p, uni.spy); y = metrics.yearly(1e6 + p)
        print(f"{name:24s} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%} | 2008={y.get(2008):+.1%} 2020={y.get(2020):+.1%} 2022={y.get(2022):+.1%} covid={metrics.crisis_table(1e6+p, uni.spy).loc['Covid_2020','strategy']:+.1%}", flush=True)
book_v = runs["PW"] + runs["CO"] + runs["EV"]; book_o = old["PW|base"] + old["CO|base"] + old["EV|base"]
pd.DataFrame({"book_volpath": book_v}).join(pd.DataFrame({f"{k}_volpath": v for k, v in runs.items()})).to_csv(RESULTS / "volpath_legs_VOLVUE.csv")
# TLT straddle with vol-path marks
px = pd.read_parquet(CACHE / "tlt_gld.parquet"); px.columns = [c.upper() for c in px.columns]; tlt = px["TLT"].reindex(days).ffill()
vv = pd.read_parquet(CACHE / "volvue_tlt.parquet"); vv["date"] = pd.to_datetime(vv["date"]); vv = vv.set_index("date").sort_index()
iv_atm = vv["iv_mean_30"].astype(float) / 100.0; iv_atm = iv_atm[~iv_atm.index.duplicated()]
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ivp = iv_atm.rolling(252, min_periods=120).rank(pct=True).reindex(days).ffill()
STR_o = pd.read_csv(RESULTS / "tlt_straddle_legs_VOLVUE.csv", index_col=0, parse_dates=True)["short straddle W, only IV pct > 50%"].reindex(days).ffill().fillna(0.0)
STR_v, _ = tlt_option_leg(tlt, iv_atm, rf, side=-1, hedge="W", mask=ivp > 0.5, vol_path=True); STR_v = STR_v.reindex(days).ffill().fillna(0.0)
for name, p in (("TLT straddle entry-IV", STR_o), ("TLT straddle vol-path", STR_v)):
    st = metrics.summary(1e6 + p, uni.spy); print(f"{name:24s} cagr={st['cagr']:+.2%} sharpe={st['sharpe']:.2f} maxdd={st['maxdd']:+.1%} worst_m={st['worst_month']:+.1%}")
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
TLT = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True)["TLT trend 200d"].reindex(days).ffill().fillna(0.0)
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
pm_book = sum(pmv.values()).reindex(ent[:-1]).fillna(0.0); tlt_on = pd.Series({a: float(TLT.loc[b_] - TLT.loc[a]) != 0 for a, b_ in zip(ent[:-1], ent[1:])}); str_on = pd.Series({a: float(STR_v.loc[b_] - STR_v.loc[a]) != 0 for a, b_ in zip(ent[:-1], ent[1:])})
def run(parts, k_fn=None, breaker=None):
    """parts: [(w, series)]; k_fn(date, equity_hist) -> scale; breaker: (dd, factor) halve-type rule."""
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E; usage = pd.Series(np.nan, index=days); kk = pd.Series(np.nan, index=days); peak = E
    for a, b_ in zip(ent[:-1], ent[1:]):
        k = 1.0 if k_fn is None else k_fn(a, out.loc[:a].dropna())
        if breaker is not None and E < peak * (1 - breaker[0]): k *= breaker[1]
        c = cash_acc.loc[a:b_]; seg = E * (c / c.iloc[0])
        for w, p in parts:
            q = p.loc[a:b_]; seg = seg + k * w * E / 1e6 * (q - q.iloc[0])
        req = E * k * (0.25 * 0.75 + 1.5 * float(pm_book.get(a, 0.0)) * 3.75 + 0.04 * 0.375 * float(tlt_on.get(a, 1)) + 1.5 * 0.07 * 0.75 * float(str_on.get(a, 1)))
        usage.loc[a:b_] = (req / seg).values; kk.loc[a:b_] = k; out.loc[a:b_] = seg.values; E = float(seg.iloc[-1]); peak = max(peak, float(seg.max()))
        if E <= 0: out.loc[b_:] = 0.0; break
    return out.ffill(), usage.ffill(), kk.ffill()
def rep(name, e, u, kk=None):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:44s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} worst_12m={st['worst_12m']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%} | usage med={u.median():.0%} p95={u.quantile(.95):.0%} max={u.max():.0%}" + (f" | k med={kk.median():.2f} min={kk.min():.2f} max={kk.max():.2f}" if kk is not None else ""), flush=True)
    return st, y
A_o = [(0.75, M10), (3.75, book_o), (0.375, TLT), (0.75, STR_o)]; A_v = [(0.75, M10), (3.75, book_v), (0.375, TLT), (0.75, STR_v)]
print("\n=== combined book, k=1 ===")
rep("line A, entry-IV marks", *run(A_o)[:2]); e_v, u_v, _ = run(A_v); rep("line A, vol-path marks", e_v, u_v)
rows = []
print("\n=== vol targeting on the vol-path book: k = target / trailing realised vol of the book (EWMA 63d), capped ===")
def make_kfn(target, cap, span=63):
    def kf(a, hist):
        if len(hist) < 70: return 1.0
        r = np.log(hist).diff().dropna(); vol = float(r.ewm(span=span).std().iloc[-1] * np.sqrt(252))
        return float(np.clip(target / vol if vol > 0 else 1.0, 0.25, cap))
    return kf
for target, cap in ((0.12, 1.5), (0.15, 1.5), (0.15, 2.0), (0.18, 2.0), (0.20, 2.5)):
    e, u, kk = run(A_v, k_fn=make_kfn(target, cap)); st, y = rep(f"vol target {target:.0%}, cap {cap}x", e, u, kk)
    rows.append(dict(rule=f"vt {target:.0%} cap {cap}", **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, pm_med=u.median(), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
print("\n--- drawdown circuit breaker: halve exposure while 20% (or 15%) below the equity peak ---")
for dd, f in ((0.20, 0.5), (0.15, 0.5), (0.10, 0.5)):
    e, u, kk = run(A_v, breaker=(dd, f)); st, y = rep(f"breaker: x{f} below -{dd:.0%}", e, u, kk)
    rows.append(dict(rule=f"breaker {dd:.0%} x{f}", **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, pm_med=u.median(), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
print("\n--- both: vol target 15% cap 2x + breaker 20% ---")
e, u, kk = run(A_v, k_fn=make_kfn(0.15, 2.0), breaker=(0.20, 0.5)); st, y = rep("vt 15% cap 2x + breaker 20%", e, u, kk)
print("   yearly: " + " ".join(f"{k}={v:+.0%}" for k, v in y.items()))
rows.append(dict(rule="vt 15% cap 2 + breaker 20%", **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "worst_12m")}, pm_med=u.median(), pm_max=u.max(), y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
pd.DataFrame(rows).to_csv(RESULTS / "volpath_voltarget_VOLVUE.csv", index=False)
e_v.rename("equity").to_frame().join(u_v.rename("pm_usage")).to_csv(RESULTS / "best_combo_A_volpath_equity_VOLVUE.csv")
print("DONE")
