"""Combo: long SPY (100% of equity, total return) + the dispersion sleeve as an
overlay at leverage L. Equity is in SPY shares, so the sleeve earns no cash
yield here (cash_yield=False); sleeve sizing resets monthly from total equity.
Daily combo return = SPY daily return + sleeve daily P&L / equity."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from dispersion import config  # noqa: E402
from dispersion.backtest import metrics  # noqa: E402
from dispersion.backtest.engine import Backtest  # noqa: E402
from dispersion.data.iv import get_provider  # noqa: E402
from dispersion.data.paths import RESULTS  # noqa: E402
from dispersion.data.universe import load_universe  # noqa: E402


def combo(sleeve_eq: pd.Series, spy: pd.Series) -> pd.Series:
    s = sleeve_eq.pct_change().fillna(0.0)
    m = spy.reindex(s.index).ffill().pct_change().fillna(0.0)
    return (1 + s + m).cumprod() * 1e6


def main(tag="VOLVUE"):
    uni = load_universe()
    ivp = get_provider("volvue" if tag == "VOLVUE" else "proxy", uni.close_adj, uni.spy)
    C = config.StrategyConfig
    sleeves = {
        "1x_ES": C(leverage=1, index_instrument="ES", costs=config.RETAIL_ES, cash_yield=False),
        "3x_ES": C(leverage=3, index_instrument="ES", costs=config.RETAIL_ES, cash_yield=False),
        "5x_ES": C(leverage=5, index_instrument="ES", costs=config.RETAIL_ES, cash_yield=False),
        "5x_regT_1d": C(leverage=5, index_wing_delta=1, cash_yield=False),
        "5x_GS": C(leverage=5, costs=config.INSTITUTIONAL, cash_yield=False),
    }
    curves = {}
    spy = uni.spy
    first = None
    for k, cfg in sleeves.items():
        res = Backtest(cfg, uni, ivp).run()
        first = first or res.equity.index[0]
        curves[f"SPY+sleeve_{k}"] = combo(res.equity, spy)
        curves[f"sleeve_{k}_nocash"] = res.equity
    spy_eq = spy.loc[first:] / spy.loc[first] * 1e6
    curves = {"SPY_only": spy_eq, **curves}
    rows, cr, yrs = {}, {}, {}
    for k, e in curves.items():
        st = metrics.summary(e, spy)
        sh = metrics.second_half(e)
        st.update(sharpe_2h=sh["sharpe"], calmar_2h=sh["calmar"], maxdd_2h=sh["maxdd"])
        rows[k] = st
        cr[k] = metrics.crisis_table(e, spy)["strategy"]
        yrs[k] = metrics.yearly(e)
    R = pd.DataFrame(rows).T
    R.to_csv(RESULTS / f"overlay_{tag}.csv")
    pd.DataFrame(curves).to_csv(RESULTS / f"overlay_equity_{tag}.csv")
    core = ["cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "dd_duration_days", "beta", "down_beta", "corr",
            "worst_month", "worst_3m", "worst_12m", "var95_m", "cvar95_m", "skew_m", "pct_pos_months", "sharpe_2h", "calmar_2h", "maxdd_2h"]
    T = R[core].copy()
    pct = {"cagr", "vol", "maxdd", "worst_month", "worst_3m", "worst_12m", "var95_m", "cvar95_m", "pct_pos_months", "maxdd_2h"}
    for c in T.columns:
        T[c] = T[c].map((lambda x: f"{x:+.1%}") if c in pct else (lambda x: f"{x:.0f}") if c == "dd_duration_days" else (lambda x: f"{x:.2f}"))
    Cs = pd.DataFrame(cr).T.map(lambda x: f"{x:+.1%}")
    Y = pd.DataFrame(yrs).T
    Y.columns = [str(c) for c in Y.columns]
    Ys = Y.map(lambda x: f"{x:+.1%}" if x == x else "")
    md = (f"# Long SPY + call-dispersion overlay ({tag} IV)\n\n## Risk statistics\n\n{T.to_markdown()}\n\n"
          f"## Crisis windows\n\n{Cs.to_markdown()}\n\n## Calendar years\n\n{Ys.to_markdown()}\n")
    (RESULTS / f"overlay_{tag}.md").write_text(md)
    print(md)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for k in ["SPY_only", "SPY+sleeve_1x_ES", "SPY+sleeve_3x_ES", "SPY+sleeve_5x_ES", "SPY+sleeve_5x_GS"]:
        e = curves[k].dropna()
        axes[0].plot(e.index, e / e.iloc[0], lw=1.1, label=k)
        axes[1].plot(e.index, metrics.drawdown(e), lw=1.0, label=k)
    axes[0].set_yscale("log"); axes[0].set_title(f"Long SPY + dispersion overlay (log), IV = {tag}"); axes[0].grid(alpha=.3); axes[0].legend()
    axes[1].set_title("Drawdown"); axes[1].grid(alpha=.3)
    fig.tight_layout(); fig.savefig(RESULTS / f"overlay_{tag}.png", dpi=120)
    print("saved", RESULTS / f"overlay_{tag}.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "VOLVUE")
