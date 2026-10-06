"""Hedged TLT straddle variants, standalone and inside the core (mom 75% + book 3.75x + TLT 38%)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.tlt_straddle import tlt_option_leg
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS, CACHE
uni = load_universe()
L_ = pd.read_csv(RESULTS / "dip_realistic_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = L_.index; book0 = L_["book_base"]
M10 = pd.read_csv(RESULTS / "momentum10_legs_VOLVUE.csv", index_col=0, parse_dates=True)["mom10_trend_trail15"].reindex(days).ffill().fillna(0.0)
TLT = pd.read_csv(RESULTS / "hedge_etf_legs_VOLVUE.csv", index_col=0, parse_dates=True)["TLT trend 200d"].reindex(days).ffill().fillna(0.0)
px = pd.read_parquet(CACHE / "tlt_gld.parquet"); px.columns = [c.upper() for c in px.columns]; tlt = px["TLT"].reindex(days).ffill()
vv = pd.read_parquet(CACHE / "volvue_tlt.parquet"); vv["date"] = pd.to_datetime(vv["date"]); vv = vv.set_index("date").sort_index()
iv_atm = vv["iv_mean_30"].astype(float) / 100.0; iv_atm = iv_atm[~iv_atm.index.duplicated()]
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
ivp = iv_atm.rolling(252, min_periods=120).rank(pct=True).reindex(days).ffill()
trend_up = (tlt > tlt.rolling(200).mean())
def equity(parts):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; seg = E * (c / c.iloc[0])
        for w, p in parts:
            q = p.loc[a:b]; seg = seg + w * E / 1e6 * (q - q.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:54s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008):+.0%} 2020={y.get(2020):+.0%} 2022={y.get(2022):+.0%} covid={cr.loc['Covid_2020','strategy']:+.0%}{extra}", flush=True)
    return st
V = {
    "short straddle, unhedged": dict(side=-1, hedge=None), "short straddle, hedge W": dict(side=-1, hedge="W"), "short straddle, hedge D": dict(side=-1, hedge="D"),
    "short straddle, band +/-1%": dict(side=-1, hedge="band", band=0.01), "short straddle, band +/-2%": dict(side=-1, hedge="band", band=0.02),
    "short 25d strangle, hedge W": dict(side=-1, structure="strangle", delta=25, hedge="W"), "short 25d strangle, unhedged": dict(side=-1, structure="strangle", delta=25, hedge=None),
    "short straddle W, only IV pct > 50%": dict(side=-1, hedge="W", mask=ivp > 0.5), "short straddle W, only IV pct > 70%": dict(side=-1, hedge="W", mask=ivp > 0.7),
    "short straddle W, only TLT > 200d": dict(side=-1, hedge="W", mask=trend_up),
    "long straddle, hedge W": dict(side=+1, hedge="W"), "long straddle, hedge D": dict(side=+1, hedge="D"), "long straddle W, only IV pct < 30%": dict(side=+1, hedge="W", mask=ivp < 0.3),
    "short straddle W, IV paid x0.9 (fill 10% under VolVue)": dict(side=-1, hedge="W", iv_mult=0.9),
}
S = {}
print("=== TLT option sleeves, 100% of equity notional, with cash (ex-cash per-1x numbers in the diag) ===")
for k, kw in V.items():
    p, dg = tlt_option_leg(tlt, iv_atm, rf, **kw); S[k] = p
    rep(k, equity([(1.0, p)]), f" | cycles {len(dg)}, prem {dg.prem.mean()*12:.1%}/yr, IV-RV {((dg.iv - dg.rv).mean()*100):+.1f} pts, ex-cash {dg.pnl.sum()/19.6:+.2%}/yr")
pd.DataFrame(S).to_csv(RESULTS / "tlt_straddle_legs_VOLVUE.csv")
cyc = pd.DataFrame({k: [float(v.loc[b] - v.loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k, v in {"mom10": M10, "book": book0, "TLT trend": TLT, "short str W": S["short straddle, hedge W"], "short str band1": S["short straddle, band +/-1%"], "long str W": S["long straddle, hedge W"]}.items()}, index=ent[:-1])
print("\ncycle-P&L correlations:\n" + cyc.corr().round(2).to_string())
print("\n=== inside the core: mom 75% + book 3.75x + TLT(200d) 38%, plus a TLT option sleeve at w of equity ===")
core = [(0.75, M10), (3.75, book0), (0.375, TLT)]
rep("core", equity(core))
rows = []
for k in ("short straddle, hedge W", "short straddle, hedge D", "short straddle, band +/-1%", "short 25d strangle, hedge W", "short straddle W, only IV pct > 50%", "long straddle, hedge W", "long straddle W, only IV pct < 30%"):
    for w in (0.5, 1.0, 2.0):
        st = rep(f"core + {k} {w:.1f}x", equity(core + [(w, S[k])])); rows.append(dict(sleeve=k, w=w, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month")}))
print("\n--- replacing the TLT(200d) leg with the option sleeve ---")
for k in ("short straddle, hedge W", "short straddle, band +/-1%", "long straddle, hedge W"):
    st = rep(f"mom 75% + book 3.75x + {k} 1x (no TLT shares)", equity([(0.75, M10), (3.75, book0), (1.0, S[k])]))
pd.DataFrame(rows).to_csv(RESULTS / "tlt_straddle_mix_VOLVUE.csv", index=False)
print("DONE")
