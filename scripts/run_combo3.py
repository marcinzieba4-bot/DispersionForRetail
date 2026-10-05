"""Fixed-notional combos (every cycle trades w * 0.9 * L * E0, never shrunk after
a loss): short ATM put with one-sided 2% stop, short ATM straddle with weekly
delta hedge, long single-name 30-delta calls (outright or 30-10 vertical),
hedged weekly with SPY or unhedged. Equity = E0 + cumulative P&L + cash on E0."""
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

E0 = 1e6
uni = load_universe(); spy = uni.spy.dropna(); days = spy.index
rf = rates.fedfunds_daily(days); cash_acc = (1 + rf / 252).cumprod()
def al(s): return s.reindex(days.union(s.index)).ffill().reindex(days)
bxm, put, tr = al(cboe_index("BXM")), al(cboe_index("PUT")), al(sp500_tr())
iv = VolVueIV("iv_call_30"); ivp = iv.panel()["SPY"].dropna()
cyc = third_fridays(days); cyc = cyc[cyc >= "2007-01-01"]; cm = config.REALISTIC_SPY; FUT_BP = 1e-4
START, END = cyc[0], cyc[-1]


def index_sleeve(N, kind, a=0.02, b=0.0):
    """Daily $ P&L of a fixed-notional N sleeve: 'put_stop' or 'straddle_delta'."""
    pnl = pd.Series(0.0, index=days)
    for t0, t1 in zip(cyc[:-1], cyc[1:]):
        S0 = float(spy.loc[t0]); u = N / S0; T = (t1 - t0).days / 365; v = float(ivp.loc[:t0].iloc[-1])
        if kind == "put_stop":
            cost = u * float(bs.put_price(S0, S0, T, v)) * cm.index() + config.commission(u / 100, cm)
        else:
            cost = u * float(bs.put_price(S0, S0, T, v) + bs.call_price(S0, S0, T, v)) * cm.index() + 2 * config.commission(u / 100, cm)
        p, S_prev, prev_mark = 0.0, S0, 0.0
        b0, p0, t00, c0 = float(bxm.loc[t0]), float(put.loc[t0]), float(tr.loc[t0]), float(cash_acc.loc[t0])
        window = days[(days > t0) & (days <= t1)]
        first = True
        for k, d in enumerate(window, 1):
            S = float(spy.loc[d]); day = p * u * (S - S_prev) - (cost if first else 0.0); first = False
            if d < t1:
                if kind == "put_stop":
                    new = (-1 if S <= S0 * (1 - a) else 0) if p == 0 else (0 if S >= S0 * (1 - b) else -1)
                elif k % 5 == 0:
                    T_rem = (t1 - d).days / 365
                    new = float(bs.put_delta(S, S0, T_rem, v) + bs.call_delta(S, S0, T_rem, v))
                else:
                    new = p
                if new != p:
                    day -= abs(new - p) * u * S * FUT_BP; p = new
            mark = N * (float(put.loc[d]) / p0 - float(cash_acc.loc[d]) / c0)
            if kind != "put_stop":
                mark += N * (float(bxm.loc[d]) / b0 - float(tr.loc[d]) / t00)
            day += mark - prev_mark; prev_mark = mark
            if d == t1 and p:
                day -= abs(p) * u * S * FUT_BP; p = 0.0
            pnl.loc[d] += day; S_prev = S
    return pnl


def singles_sleeve(L_eff, wing, hedge):
    """Daily $ P&L of long single-name 30d calls (wing=None) or 30-10 verticals, fixed notional 0.9*L_eff*E0."""
    cfg = config.StrategyConfig(leverage=L_eff, costs=cm, cycle="third_friday", dividends=True, index_notional_scale=0.0,
                                short_wing_delta=wing, hedge_freq=hedge, cash_yield=False, fixed_notional=True, equity=E0)
    e = Backtest(cfg, uni, iv).run().equity
    return e.diff().reindex(days).fillna(0.0)


def stats(pnl_total, label):
    e = (E0 + pnl_total.cumsum() + E0 * (cash_acc / float(cash_acc.loc[START]) - 1.0)).loc[START:END]
    e = e.where(e > 0).dropna()
    st = metrics.summary(e, spy); sh = metrics.second_half(e); st.update(sharpe_2h=sh["sharpe"])
    y = metrics.yearly(e); c = metrics.crisis_table(e, spy)["strategy"]
    print(f"{label:52s} {metrics.fmt(st)} sh2h={sh['sharpe']:.2f} 2008={y.get(2008, np.nan):+.1%} 2018={y.get(2018, np.nan):+.1%} 2020={y.get(2020, np.nan):+.1%} 2022={y.get(2022, np.nan):+.1%}", flush=True)
    return st, y, c


rows, yrs, cr = {}, {}, {}
# unit sleeves at notional 0.9*E0 (L=1 equivalents); combos scale linearly in fixed-notional mode
P = index_sleeve(0.9 * E0, "put_stop"); P1 = index_sleeve(0.9 * E0, "put_stop", b=0.01); Sd = index_sleeve(0.9 * E0, "straddle_delta")
C30h = singles_sleeve(1.0, None, "W"); C30u = singles_sleeve(1.0, None, None); V3010h = singles_sleeve(1.0, 10, "W")
sleeves = {"put_stop2%": P, "put_stop2%_unwind-1%": P1, "straddle_deltaW": Sd, "long30dcalls_hedgedW": C30h, "long30dcalls_unhedged": C30u, "long30-10vert_hedgedW": V3010h}
for k, s_ in sleeves.items():
    rows[f"1x_{k}_FIXED"], yrs[f"1x_{k}_FIXED"], cr[f"1x_{k}_FIXED"] = stats(s_, f"1x sleeve {k} (fixed notional)")
for L in (1, 2, 3):
    combos = {
        f"{L}x_50/50_putstop+straddle_FIXED": L * (0.5 * P + 0.5 * Sd),
        f"{L}x_50/50_putstop(-1%)+straddle_FIXED": L * (0.5 * P1 + 0.5 * Sd),
        f"{L}x_33/33/33_putstop+straddle+long30dcalls_hedged_FIXED": L * (P + Sd + C30h) / 3,
        f"{L}x_33/33/33_putstop+straddle+long30dcalls_unhedged_FIXED": L * (P + Sd + C30u) / 3,
        f"{L}x_33/33/33_putstop+straddle+long30-10vert_hedged_FIXED": L * (P + Sd + V3010h) / 3,
        f"{L}x_40/40/20_putstop+straddle+long30dcalls_unhedged_FIXED": L * (0.4 * P + 0.4 * Sd + 0.2 * C30u),
        f"{L}x_33/33/33_putstop(-1%)+straddle+long30dcalls_unhedged_FIXED": L * (P1 + Sd + C30u) / 3,
    }
    for k, s_ in combos.items():
        rows[k], yrs[k], cr[k] = stats(s_, k)
pd.DataFrame(rows).T.to_csv(RESULTS / "combo3_fixed_VOLVUE.csv"); Y = pd.DataFrame(yrs).T; Y.to_csv(RESULTS / "combo3_fixed_years_VOLVUE.csv")
Cs = pd.DataFrame(cr).T; Cs.loc["SPY"] = metrics.crisis_table(spy.loc["2007":], spy)["spy"]; print(Cs.map(lambda x: f"{x:+.1%}").to_string())
Y.columns = [str(c) for c in Y.columns]; print(Y.loc[[k for k in Y.index if k.startswith(("1x_33/33/33_putstop+straddle+long30dcalls", "2x_33/33/33_putstop+straddle+long30dcalls_unhedged"))]].map(lambda x: f"{x:+.1%}").to_string())
# sleeve correlations (monthly)
M = pd.DataFrame({k: v.resample("ME").sum() for k, v in sleeves.items()}).loc[START:END]
print("\nmonthly sleeve correlations:\n", M.corr().round(2).to_string())
