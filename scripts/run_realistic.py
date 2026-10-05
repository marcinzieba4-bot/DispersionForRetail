"""Most realistic retail scenario (tastytrade): capped commissions + fees,
per-leg half-spreads without the 1.3 multiplier, ES/MES SPAN index leg.
Full risk statistics, daily and month-end drawdowns, crisis windows, years."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd  # noqa: E402

from dispersion import config  # noqa: E402
from dispersion.backtest import metrics  # noqa: E402
from dispersion.backtest.engine import Backtest  # noqa: E402
from dispersion.data.paths import RESULTS  # noqa: E402
from dispersion.data.universe import load_universe  # noqa: E402
from dispersion.data.volvue import VolVueIV  # noqa: E402


def main():
    uni = load_universe()
    iv = VolVueIV("iv_call_30")
    C = config.StrategyConfig
    R = config.REALISTIC_ES
    V = {
        "1x_ES_realistic": C(leverage=1, index_instrument="ES", costs=R),
        "3x_ES_realistic": C(leverage=3, index_instrument="ES", costs=R),
        "5x_ES_realistic": C(leverage=5, index_instrument="ES", costs=R),
        "8x_ES_realistic": C(leverage=8, index_instrument="ES", costs=R),
        "5x_SPY_regT_1d_realistic": C(leverage=5, index_wing_delta=1, costs=config.REALISTIC_SPY),
        "100k_5x_half_ES_realistic": C(leverage=5, index_instrument="ES", costs=R, rotating_half=True,
                                       contract_granularity=True, equity=100_000),
        "250k_5x_full_ES_realistic": C(leverage=5, index_instrument="ES", costs=R, contract_granularity=True, equity=250_000),
        "5x_ES_spec_per_leg_x1.3(conservative)": C(leverage=5, index_instrument="ES", costs=config.RETAIL_ES),
    }
    rows, cr, yrs, curves, costs = {}, {}, {}, {}, {}
    for k, cfg in V.items():
        res = Backtest(cfg, uni, iv).run()
        e = res.equity
        st = metrics.summary(e, uni.spy)
        sh = metrics.second_half(e)
        st.update(sharpe_2h=sh["sharpe"], calmar_2h=sh["calmar"], calmar_m_2h=sh["calmar_monthly"], maxdd_2h=sh["maxdd"])
        tot_cost = sum(m.costs for m in res.months)
        st["cost_pct_equity_yr"] = tot_cost / e.mean() / st["years"]
        rows[k] = st
        cr[k] = metrics.crisis_table(e, uni.spy)["strategy"]
        yrs[k] = metrics.yearly(e)
        curves[k] = e
        print(k, metrics.fmt(st), f"cost/yr={st['cost_pct_equity_yr']:.2%}")
    df = pd.DataFrame(rows).T
    df.to_csv(RESULTS / "realistic_VOLVUE.csv")
    pd.DataFrame(curves).to_csv(RESULTS / "realistic_equity_VOLVUE.csv")
    core = ["cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "maxdd_monthly", "calmar_monthly", "dd_duration_days", "beta", "down_beta",
            "worst_month", "worst_3m", "worst_12m", "var95_m", "cvar95_m", "skew_m", "kurt_m", "pct_pos_months", "avg_in_spy_worst10",
            "sharpe_2h", "calmar_2h", "calmar_m_2h", "maxdd_2h", "cost_pct_equity_yr"]
    T = df[core].copy()
    pct = {"cagr", "vol", "maxdd", "maxdd_monthly", "worst_month", "worst_3m", "worst_12m", "var95_m", "cvar95_m", "pct_pos_months",
           "avg_in_spy_worst10", "maxdd_2h", "cost_pct_equity_yr"}
    for c in T.columns:
        T[c] = T[c].map((lambda x: f"{x:+.1%}") if c in pct else (lambda x: f"{x:.0f}") if c == "dd_duration_days" else (lambda x: f"{x:.2f}"))
    Cs = pd.DataFrame(cr).T.map(lambda x: f"{x:+.1%}")
    Cs.loc["SPY"] = metrics.crisis_table(curves["1x_ES_realistic"], uni.spy)["spy"].map(lambda x: f"{x:+.1%}")
    Y = pd.DataFrame(yrs).T
    Y.columns = [str(c) for c in Y.columns]
    Ys = Y.map(lambda x: f"{x:+.1%}" if x == x else "")
    md = ("# Most realistic retail scenario (tastytrade, VolVue iv_call_30)\n\n"
          "Costs: per-leg half-spreads 3% (30d) / 8% (10d) / 0.3% (ES-MES) of premium, no 1.3 multiplier "
          "(hold to expiry); commissions $1/contract capped $10/leg, $0.15/contract clearing+exchange; "
          "ES/MES options $3/contract all-in; 1bp hedge turnover; FEDFUNDS on equity.\n\n"
          f"## Risk statistics\n\n{T.to_markdown()}\n\n## Crisis windows\n\n{Cs.to_markdown()}\n\n## Calendar years\n\n{Ys.to_markdown()}\n")
    (RESULTS / "realistic_VOLVUE.md").write_text(md)
    print(md)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for k in ["1x_ES_realistic", "3x_ES_realistic", "5x_ES_realistic", "100k_5x_half_ES_realistic", "5x_SPY_regT_1d_realistic"]:
        e = curves[k]
        axes[0].plot(e.index, e / e.iloc[0], lw=1.1, label=k)
        axes[1].plot(e.index, metrics.drawdown(e), lw=1.0)
    axes[0].set_yscale("log"); axes[0].set_title("Realistic retail costs, VolVue IV: equity (log)"); axes[0].grid(alpha=.3); axes[0].legend()
    axes[1].set_title("Drawdown (daily marks)"); axes[1].grid(alpha=.3)
    fig.tight_layout(); fig.savefig(RESULTS / "realistic_VOLVUE.png", dpi=120)


if __name__ == "__main__":
    main()
