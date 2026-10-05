"""Variants with the index leg taken from Cboe buy-write indices (real traded
prices, skew included) on the 3rd-Friday cycle, plus put-overlay alternatives.
Realistic retail costs, VolVue iv_call_30 for the singles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from dispersion import config  # noqa: E402
from dispersion.backtest import metrics  # noqa: E402
from dispersion.backtest.engine import Backtest, third_fridays  # noqa: E402
from dispersion.data.cboe import cboe_index, sp500_tr  # noqa: E402
from dispersion.data.paths import RESULTS  # noqa: E402
from dispersion.data.universe import load_universe  # noqa: E402
from dispersion.data.volvue import VolVueIV  # noqa: E402


def main():
    uni = load_universe(); iv = VolVueIV("iv_call_30")
    C = config.StrategyConfig; R = config.REALISTIC_ES; RS = config.REALISTIC_SPY
    tf = dict(cycle="third_friday")
    V = {
        "A_5x_model_flatIV_3rdFri": C(leverage=5, index_instrument="ES", costs=R, **tf),
        "B_5x_BXMD_real_30d_call": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXMD", **tf),
        "B_3x_BXMD_real_30d_call": C(leverage=3, index_instrument="ES", costs=R, index_leg="BXMD", **tf),
        "B_1x_BXMD_real_30d_call": C(leverage=1, index_instrument="ES", costs=R, index_leg="BXMD", **tf),
        "B_1x_BXMD_GS_costs": C(leverage=1, costs=config.INSTITUTIONAL, index_leg="BXMD", **tf),
        "C_5x_BXM_ATM_call_deltamatched": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXM", index_notional_scale=0.30 / 0.55, **tf),
        "D_5x_BXY_2pctOTM_deltamatched": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXY", index_notional_scale=0.30 / 0.40, **tf),
        "E_5x_BXM_ATM+long10dput": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXM", index_notional_scale=0.30 / 0.55, put_delta=10, put_iv_mult=1.45, **tf),
        "F_5x_BXM_ATM+long5dput": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXM", index_notional_scale=0.30 / 0.55, put_delta=5, put_iv_mult=1.73, **tf),
        "G_5x_BXMD+long5dput": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXMD", put_delta=5, put_iv_mult=1.73, **tf),
        "H_5x_BXMD_half_index_notional": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXMD", index_notional_scale=0.5, **tf),
        "I_5x_BXMD_no_hedge": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXMD", hedge_freq=None, **tf),
        "J_5x_BXMD_daily_hedge": C(leverage=5, index_instrument="ES", costs=R, index_leg="BXMD", hedge_freq="D", **tf),
    }
    rows, cr, yrs, curves = {}, {}, {}, {}
    for k, cfg in V.items():
        res = Backtest(cfg, uni, iv).run(); e = res.equity
        st = metrics.summary(e, uni.spy); sh = metrics.second_half(e)
        st.update(sharpe_2h=sh["sharpe"], calmar_2h=sh["calmar"], calmar_m_2h=sh["calmar_monthly"])
        rows[k] = st; cr[k] = metrics.crisis_table(e, uni.spy)["strategy"]; yrs[k] = metrics.yearly(e.where(e > 0).dropna()); curves[k] = e
        print(f"{k:34s} {metrics.fmt(st)}  2008={yrs[k].get(2008, np.nan):+.1%} 2020={yrs[k].get(2020, np.nan):+.1%} 2022={yrs[k].get(2022, np.nan):+.1%}")
    # historical skew cost: model short 30d call P&L vs BXMD-realized, per cycle, % of index notional
    cyc = third_fridays(uni.spy.index); cyc = cyc[cyc >= "2007-01-01"]
    bx = cboe_index("BXMD"); tr = sp500_tr(); spy = uni.spy
    real, model = [], []
    P = iv.panel()["SPY"]
    for a, b in zip(cyc[:-1], cyc[1:]):
        try:
            real.append(bx.loc[b] / bx.loc[a] - tr.loc[b] / tr.loc[a])
            S0, S1 = float(spy.loc[a]), float(spy.loc[b]); v = float(P.loc[:a].iloc[-1]); T = (b - a).days / 365
            K = config.__dict__ and __import__("dispersion.bs", fromlist=["bs"]).strike_for_delta(S0, v, T, 30, 0.0)
            C0 = float(__import__("dispersion.bs", fromlist=["bs"]).call_price(S0, K, T, v, 0.0))
            model.append((C0 - max(S1 - K, 0)) / S0)
        except KeyError:
            pass
    real, model = np.array(real), np.array(model)
    skew = pd.DataFrame({"real_BXMD": real, "model_flatIV": model}, index=cyc[1:len(real) + 1])
    skew.to_csv(RESULTS / "index_leg_real_vs_model.csv")
    print("\nShort 30d SPX call, per cycle, %% of index notional: real(BXMD) mean %.3f%%  model(flat IV) mean %.3f%%  -> model overstates by %.2f%%/yr of index notional; corr %.2f" % (
        real.mean() * 100, model.mean() * 100, (model.mean() - real.mean()) * 1200, np.corrcoef(real, model)[0, 1]))
    print("by period: ", {str(y): round(float((skew.model_flatIV - skew.real_BXMD).loc[str(y)].mean() * 1200), 2) for y in range(2007, 2027, 3)})
    df = pd.DataFrame(rows).T; df.to_csv(RESULTS / "real_index_leg_VOLVUE.csv")
    core = ["cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "maxdd_monthly", "calmar_monthly", "beta", "down_beta", "worst_month", "worst_12m", "cvar95_m", "pct_pos_months", "sharpe_2h", "calmar_2h", "calmar_m_2h"]
    T = df[core].copy(); pct = {"cagr", "vol", "maxdd", "maxdd_monthly", "worst_month", "worst_12m", "cvar95_m", "pct_pos_months"}
    for c in T.columns:
        T[c] = T[c].map((lambda x: f"{x:+.1%}") if c in pct else (lambda x: f"{x:.2f}"))
    Cs = pd.DataFrame(cr).T.map(lambda x: f"{x:+.1%}"); Cs.loc["SPY"] = metrics.crisis_table(curves["A_5x_model_flatIV_3rdFri"], uni.spy)["spy"].map(lambda x: f"{x:+.1%}")
    Y = pd.DataFrame(yrs).T; Y.columns = [str(c) for c in Y.columns]; Ys = Y.map(lambda x: f"{x:+.1%}" if x == x else "")
    md = ("# Index leg from Cboe buy-write indices (real prices) + put overlays, 3rd-Friday cycle, realistic retail costs\n\n"
          f"## Risk statistics\n\n{T.to_markdown()}\n\n## Crisis windows\n\n{Cs.to_markdown()}\n\n## Calendar years\n\n{Ys.to_markdown()}\n")
    (RESULTS / "real_index_leg_VOLVUE.md").write_text(md)
    pd.DataFrame(curves).to_csv(RESULTS / "real_index_leg_equity_VOLVUE.csv")


if __name__ == "__main__":
    main()
