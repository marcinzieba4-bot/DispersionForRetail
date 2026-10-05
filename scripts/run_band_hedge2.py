"""(1) Short SPX ATM straddle (BXM + PUT, real) with weekly BS delta hedge, and the
stop-and-reverse band rule evaluated weekly. (2) Short ATM put (PUT index, real)
with a one-sided stop hedge: short futures when the close is <= S0(1-a), unwind
when the close is back >= S0; plus weekly delta hedge and no hedge."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import bs, config
from dispersion.backtest import metrics
from dispersion.backtest.engine import third_fridays
from dispersion.data import rates
from dispersion.data.cboe import cboe_index, sp500_tr
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data.paths import RESULTS

uni = load_universe(); spy = uni.spy.dropna(); days = spy.index
rf = rates.fedfunds_daily(days); cash_acc = (1 + rf / 252).cumprod()
def al(s): return s.reindex(days.union(s.index)).ffill().reindex(days)
bxm, put, tr = al(cboe_index("BXM")), al(cboe_index("PUT")), al(sp500_tr())
ivp = VolVueIV("iv_call_30").panel()["SPY"].dropna()
cyc = third_fridays(days); cyc = cyc[cyc >= "2007-01-01"]; cm = config.REALISTIC_SPY


def run(L, structure, hedge, a=0.02, b=0.0, h=1.0, check="D", fut_bp=1e-4):
    E = 1e6; eq = pd.Series(np.nan, index=days); turn = 0.0
    for t0, t1 in zip(cyc[:-1], cyc[1:]):
        S0 = float(spy.loc[t0]); notional = 0.9 * L * E; units = notional / S0
        T = (t1 - t0).days / 365; v = float(ivp.loc[:t0].iloc[-1])
        prem = float(bs.put_price(S0, S0, T, v)) + (float(bs.call_price(S0, S0, T, v)) if structure == "straddle" else 0.0)
        nlegs = 2 if structure == "straddle" else 1
        cash = E - (units * prem * cm.index() + nlegs * config.commission(units / 100, cm))
        p, S_prev = 0.0, S0
        b0, p0, t00, c0 = float(bxm.loc[t0]), float(put.loc[t0]), float(tr.loc[t0]), float(cash_acc.loc[t0])
        window = days[(days > t0) & (days <= t1)]
        for k, d in enumerate(window, 1):
            S = float(spy.loc[d]); cash *= 1 + float(rf.loc[d]) / 252
            cash += p * h * units * (S - S_prev)
            if d < t1 and (check == "D" or k % 5 == 0):
                new = p
                if hedge == "band":
                    up, dn = S0 * (1 + a), S0 * (1 - a)
                    if structure == "straddle":
                        if p == 0: new = 1 if S >= up else (-1 if S <= dn else 0)
                        elif p == 1: new = -1 if S <= dn else (0 if S <= S0 * (1 + b) else 1)
                        else: new = 1 if S >= up else (0 if S >= S0 * (1 - b) else -1)
                    else:   # short put: one-sided
                        if p == 0: new = -1 if S <= dn else 0
                        else: new = 0 if S >= S0 * (1 - b) else -1
                elif hedge == "delta":
                    T_rem = (t1 - d).days / 365
                    new = float(bs.put_delta(S, S0, T_rem, v)) + (float(bs.call_delta(S, S0, T_rem, v)) if structure == "straddle" else 0.0)
                if new != p:
                    tr_ = abs(new - p) * h * units * S; cash -= tr_ * fut_bp; turn += tr_; p = new
            mark = notional * (float(put.loc[d]) / p0 - float(cash_acc.loc[d]) / c0)
            if structure == "straddle":
                mark += notional * (float(bxm.loc[d]) / b0 - float(tr.loc[d]) / t00)
            if d == t1:
                cash += mark; mark = 0.0
                if p: cash -= abs(p) * h * units * S * fut_bp; turn += abs(p) * h * units * S; p = 0.0
            eq.loc[d] = cash + mark; S_prev = S
        E = float(eq.loc[t1])
    eq.loc[cyc[0]] = 1e6
    return eq.dropna(), turn / 1e6 / ((cyc[-1] - cyc[0]).days / 365)


V = {}
for L in (1, 3, 5):
    V[f"STRADDLE_{L}x_weekly_delta_hedge"] = dict(L=L, structure="straddle", hedge="delta", check="W")
for L in (1, 3):
    V[f"STRADDLE_{L}x_daily_delta_hedge"] = dict(L=L, structure="straddle", hedge="delta", check="D")
    V[f"STRADDLE_{L}x_band1pct_start_checked_weekly"] = dict(L=L, structure="straddle", hedge="band", a=0.01, b=0.0, check="W")
    V[f"STRADDLE_{L}x_band2pct_start_checked_weekly"] = dict(L=L, structure="straddle", hedge="band", a=0.02, b=0.0, check="W")
    V[f"PUT_{L}x_unhedged(PUT index)"] = dict(L=L, structure="put", hedge="none")
    V[f"PUT_{L}x_weekly_delta_hedge"] = dict(L=L, structure="put", hedge="delta", check="W")
    V[f"PUT_{L}x_stop2pct_unwind_at_start(as asked)"] = dict(L=L, structure="put", hedge="band", a=0.02, b=0.0)
    V[f"PUT_{L}x_stop2pct_unwind_at_1pct"] = dict(L=L, structure="put", hedge="band", a=0.02, b=0.01)
    V[f"PUT_{L}x_stop1pct_unwind_at_start"] = dict(L=L, structure="put", hedge="band", a=0.01, b=0.0)
    V[f"PUT_{L}x_stop3pct_unwind_at_start"] = dict(L=L, structure="put", hedge="band", a=0.03, b=0.0)
    V[f"PUT_{L}x_stop2pct_start_checked_weekly"] = dict(L=L, structure="put", hedge="band", a=0.02, b=0.0, check="W")
    V[f"PUT_{L}x_stop2pct_start_half_size"] = dict(L=L, structure="put", hedge="band", a=0.02, b=0.0, h=0.5)
rows, yrs, cr = {}, {}, {}
for k, kw in V.items():
    e, to = run(**kw)
    st = metrics.summary(e, spy); sh = metrics.second_half(e); st.update(sharpe_2h=sh["sharpe"], turnover_x_equity_yr=to)
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, spy)["strategy"]
    print(f"{k:44s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} to/yr={to:.1f}x 2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "band_hedge2_VOLVUE.csv"); Y = pd.DataFrame(yrs).T; Y.to_csv(RESULTS / "band_hedge2_years_VOLVUE.csv")
Cs = pd.DataFrame(cr).T; Cs.loc["SPY"] = metrics.crisis_table(spy.loc["2007":], spy)["spy"]; print(Cs.map(lambda x: f"{x:+.1%}").to_string())
Y.columns = [str(c) for c in Y.columns]; print(Y.loc[["STRADDLE_3x_weekly_delta_hedge", "PUT_1x_stop2pct_unwind_at_start(as asked)", "PUT_1x_weekly_delta_hedge"]].map(lambda x: f"{x:+.1%}").to_string())
