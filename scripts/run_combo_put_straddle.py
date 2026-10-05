"""50/50 combo: short ATM put with one-sided stop hedge + short ATM straddle with
weekly delta hedge. Real prices (PUT, BXM), SPY close path for the overlays,
each sleeve sized to w * 0.9 * L * E every 3rd-Friday cycle."""
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
FUT_BP = 1e-4


def run(L, w_put, w_str, a=0.02, b=0.0):
    """Returns equity series and per-cycle sleeve returns (for correlation)."""
    E = 1e6; eq = pd.Series(np.nan, index=days); sl = []
    for t0, t1 in zip(cyc[:-1], cyc[1:]):
        S0 = float(spy.loc[t0]); T = (t1 - t0).days / 365; v = float(ivp.loc[:t0].iloc[-1])
        n_put, n_str = w_put * 0.9 * L * E, w_str * 0.9 * L * E
        u_put, u_str = n_put / S0, n_str / S0
        cost = 0.0
        if u_put: cost += u_put * float(bs.put_price(S0, S0, T, v)) * cm.index() + config.commission(u_put / 100, cm)
        if u_str: cost += u_str * float(bs.put_price(S0, S0, T, v) + bs.call_price(S0, S0, T, v)) * cm.index() + 2 * config.commission(u_str / 100, cm)
        cash = E - cost; p_put, p_str, S_prev = 0.0, 0.0, S0
        b0, p0, t00, c0 = float(bxm.loc[t0]), float(put.loc[t0]), float(tr.loc[t0]), float(cash_acc.loc[t0])
        pnl_put = pnl_str = 0.0
        window = days[(days > t0) & (days <= t1)]
        for k, d in enumerate(window, 1):
            S = float(spy.loc[d]); cash *= 1 + float(rf.loc[d]) / 252
            h1 = p_put * u_put * (S - S_prev); h2 = p_str * u_str * (S - S_prev); cash += h1 + h2; pnl_put += h1; pnl_str += h2
            if d < t1:
                if u_put:   # one-sided stop on the short put
                    new = (-1 if S <= S0 * (1 - a) else 0) if p_put == 0 else (0 if S >= S0 * (1 - b) else -1)
                    if new != p_put:
                        t_ = abs(new - p_put) * u_put * S; cash -= t_ * FUT_BP; pnl_put -= t_ * FUT_BP; p_put = new
                if u_str and k % 5 == 0:   # weekly delta hedge on the straddle
                    T_rem = (t1 - d).days / 365
                    new = float(bs.put_delta(S, S0, T_rem, v) + bs.call_delta(S, S0, T_rem, v))
                    t_ = abs(new - p_str) * u_str * S; cash -= t_ * FUT_BP; pnl_str -= t_ * FUT_BP; p_str = new
            m_put = n_put * (float(put.loc[d]) / p0 - float(cash_acc.loc[d]) / c0)
            m_str = n_str * ((float(put.loc[d]) / p0 - float(cash_acc.loc[d]) / c0) + (float(bxm.loc[d]) / b0 - float(tr.loc[d]) / t00))
            mark = m_put + m_str
            if d == t1:
                cash += mark; pnl_put += m_put; pnl_str += m_str; mark = 0.0
                for p_, u_ in ((p_put, u_put), (p_str, u_str)):
                    if p_: cash -= abs(p_) * u_ * S * FUT_BP
                p_put = p_str = 0.0
            eq.loc[d] = cash + mark; S_prev = S
        sl.append((t1, pnl_put / E if w_put else np.nan, pnl_str / E if w_str else np.nan))
        E = float(eq.loc[t1])
    eq.loc[cyc[0]] = 1e6
    return eq.dropna(), pd.DataFrame(sl, columns=["date", "put", "straddle"]).set_index("date")


V = {}
for L in (1, 2, 3, 5):
    V[f"{L}x_COMBO_50/50_putstop2%+straddle_deltaW"] = dict(L=L, w_put=0.5, w_str=0.5)
    V[f"{L}x_put_stop2%_alone"] = dict(L=L, w_put=1.0, w_str=0.0)
    V[f"{L}x_straddle_deltaW_alone"] = dict(L=L, w_put=0.0, w_str=1.0)
for L in (1, 2, 3):
    V[f"{L}x_COMBO_50/50_putstop2%unwind-1%+straddle"] = dict(L=L, w_put=0.5, w_str=0.5, a=0.02, b=0.01)
    V[f"{L}x_COMBO_50/50_putstop3%+straddle"] = dict(L=L, w_put=0.5, w_str=0.5, a=0.03, b=0.0)
    V[f"{L}x_COMBO_70/30_putstop2%+straddle"] = dict(L=L, w_put=0.7, w_str=0.3)
rows, yrs, cr = {}, {}, {}
for k, kw in V.items():
    e, sl = run(**kw)
    st = metrics.summary(e, spy); sh = metrics.second_half(e); st.update(sharpe_2h=sh["sharpe"], calmar_m_2h=sh["calmar_monthly"])
    if kw["w_put"] and kw["w_str"]:
        st["sleeve_corr"] = sl["put"].corr(sl["straddle"])
    rows[k] = st; yrs[k] = metrics.yearly(e); cr[k] = metrics.crisis_table(e, spy)["strategy"]
    print(f"{k:46s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} corr={st.get('sleeve_corr', float('nan')):.2f} 2008={yrs[k].get(2008, np.nan):+.1%} 2018={yrs[k].get(2018, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}", flush=True)
pd.DataFrame(rows).T.to_csv(RESULTS / "combo_put_straddle_VOLVUE.csv"); Y = pd.DataFrame(yrs).T; Y.to_csv(RESULTS / "combo_put_straddle_years_VOLVUE.csv")
Cs = pd.DataFrame(cr).T; Cs.loc["SPY"] = metrics.crisis_table(spy.loc["2007":], spy)["spy"]; print(Cs.map(lambda x: f"{x:+.1%}").to_string())
Y.columns = [str(c) for c in Y.columns]; print(Y.loc[[k for k in Y.index if "COMBO_50/50_putstop2%+straddle_deltaW" in k]].map(lambda x: f"{x:+.1%}").to_string())
