"""Short SPX ATM straddle (real prices: BXM + PUT) with a stop-and-reverse
futures hedge: go long 1 unit of futures when spot closes >= S0*(1+a), short 1
unit when <= S0*(1-a); unwind when spot closes back through S0*(1+-b)
(b=0: back to the starting point). Daily closes; monthly 3rd-Friday cycle;
sizing 0.9*L*E; realistic costs; FEDFUNDS on equity."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import bs, config
from dispersion.backtest import metrics
from dispersion.backtest.engine import third_fridays, Backtest
from dispersion.data import rates
from dispersion.data.cboe import cboe_index, sp500_tr
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data.paths import RESULTS

uni = load_universe(); spy = uni.spy.dropna(); days = spy.index
rf = rates.fedfunds_daily(days)
cash_acc = (1 + rf / 252).cumprod()
bxm = cboe_index("BXM").reindex(days.union(cboe_index("BXM").index)).ffill().reindex(days)
put = cboe_index("PUT").reindex(days.union(cboe_index("PUT").index)).ffill().reindex(days)
tr = sp500_tr().reindex(days.union(sp500_tr().index)).ffill().reindex(days)
ivp = VolVueIV("iv_call_30").panel()["SPY"].dropna()
cyc = third_fridays(days); cyc = cyc[cyc >= "2007-01-01"]
cm = config.REALISTIC_SPY


def run(L, a, b, h, hedge="band", fut_bp=1e-4):
    E = 1e6; eq = pd.Series(np.nan, index=days); turn = 0.0
    for t0, t1 in zip(cyc[:-1], cyc[1:]):
        S0 = float(spy.loc[t0]); notional = 0.9 * L * E; units = notional / S0
        T = (t1 - t0).days / 365; v = float(ivp.loc[:t0].iloc[-1]) if len(ivp.loc[:t0]) else 0.2
        prem = float(bs.call_price(S0, S0, T, v) + bs.put_price(S0, S0, T, v))
        cost = units * prem * cm.index() + 2 * config.commission(units / 100, cm)
        cash = E - cost
        p, p_px, S_prev = 0, None, S0
        b0, p0, t00, c0 = float(bxm.loc[t0]), float(put.loc[t0]), float(tr.loc[t0]), float(cash_acc.loc[t0])
        window = days[(days > t0) & (days <= t1)]
        for k, d in enumerate(window, 1):
            S = float(spy.loc[d]); cash *= 1 + float(rf.loc[d]) / 252
            cash += p * h * units * (S - S_prev)                       # futures overlay P&L
            if d < t1:
                if hedge == "band":
                    up, dn = S0 * (1 + a), S0 * (1 - a)
                    new = p
                    if p == 0:
                        new = 1 if S >= up else (-1 if S <= dn else 0)
                    elif p == 1:
                        new = -1 if S <= dn else (0 if S <= S0 * (1 + b) else 1)
                    else:
                        new = 1 if S >= up else (0 if S >= S0 * (1 - b) else -1)
                    if new != p:
                        tr_ = abs(new - p) * h * units * S; cash -= tr_ * fut_bp; turn += tr_; p = new
                elif hedge == "delta" and k % 5 == 0:
                    T_rem = (t1 - d).days / 365
                    dl = float(bs.call_delta(S, S0, T_rem, v) + bs.put_delta(S, S0, T_rem, v))
                    new = -(-dl)          # short straddle delta is -dl; hedge = +dl units... as fraction of units
                    newp = dl             # continuous position (fraction of units)
                    tr_ = abs(newp - p) * h * units * S; cash -= tr_ * fut_bp; turn += tr_; p = newp
            mark = notional * ((float(bxm.loc[d]) / b0 - float(tr.loc[d]) / t00) + (float(put.loc[d]) / p0 - float(cash_acc.loc[d]) / c0))
            if d == t1:
                cash += mark; mark = 0.0
                if p:
                    cash -= abs(p) * h * units * S * fut_bp; turn += abs(p) * h * units * S; p = 0
            eq.loc[d] = cash + mark; S_prev = S
        E = float(eq.loc[t1])
    eq.loc[cyc[0]] = 1e6
    return eq.dropna(), turn / 1e6 / ((cyc[-1] - cyc[0]).days / 365)


rows, yrs, cr = {}, {}, {}
V = {}
for L in (1, 3):
    V[f"{L}x_unhedged"] = (L, 0, 0, 0, "none")
    V[f"{L}x_delta_hedge_weekly(BS delta)"] = (L, 0, 0, 1.0, "delta")
    V[f"{L}x_band_1pct_unwind_at_start"] = (L, 0.01, 0.0, 1.0, "band")
    V[f"{L}x_band_1pct_unwind_at_0.5pct"] = (L, 0.01, 0.005, 1.0, "band")
    V[f"{L}x_band_1pct_unwind_at_1pct(symmetric)"] = (L, 0.01, 0.01, 1.0, "band")
    V[f"{L}x_band_0.5pct_unwind_at_start"] = (L, 0.005, 0.0, 1.0, "band")
    V[f"{L}x_band_2pct_unwind_at_start"] = (L, 0.02, 0.0, 1.0, "band")
    V[f"{L}x_band_2pct_unwind_at_1pct"] = (L, 0.02, 0.01, 1.0, "band")
    V[f"{L}x_band_1pct_unwind_at_start_half_size"] = (L, 0.01, 0.0, 0.5, "band")
    V[f"{L}x_band_3pct_unwind_at_start"] = (L, 0.03, 0.0, 1.0, "band")
for k, (L, a, b, h, hg) in V.items():
    e, to = run(L, a, b, h, hedge=hg)
    st = metrics.summary(e, spy); sh = metrics.second_half(e); st.update(sharpe_2h=sh["sharpe"], turnover_x_equity_yr=to)
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, spy)["strategy"]
    print(f"{k:42s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} fut_turnover/yr={to:.1f}x 2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "band_hedge_VOLVUE.csv"); pd.DataFrame(yrs).T.to_csv(RESULTS / "band_hedge_years_VOLVUE.csv")
Cs = pd.DataFrame(cr).T; Cs.loc["SPY"] = metrics.crisis_table(spy.loc["2007":], spy)["spy"]; print(Cs.map(lambda x: f"{x:+.1%}").to_string())
