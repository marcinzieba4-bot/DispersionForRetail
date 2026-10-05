"""Risk statistics for every variant in results/equity_<TAG>.csv:
distribution of monthly returns, tail/crisis behaviour, drawdowns, yearly table."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from dispersion.backtest import metrics  # noqa: E402
from dispersion.data.paths import CACHE, RESULTS  # noqa: E402


def load_spy():
    return pd.read_parquet(CACHE / "prices" / "SPY.parquet")["close_adj"].tz_localize(None)


def main(tag):
    eq = pd.read_csv(RESULTS / f"equity_{tag}.csv", index_col=0, parse_dates=True)
    spy = load_spy().reindex(eq.index).ffill()
    rows, crises, years = {}, {}, {}
    for v in eq.columns:
        e = eq[v].dropna()
        st = metrics.summary(e, spy)
        sh = metrics.second_half(e)
        st["sharpe_2h"], st["calmar_2h"], st["maxdd_2h"] = sh["sharpe"], sh["calmar"], sh["maxdd"]
        rows[v] = st
        crises[v] = metrics.crisis_table(e, spy)["strategy"]
        years[v] = metrics.yearly(e)
    R = pd.DataFrame(rows).T
    core = ["cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "dd_duration_days", "beta", "down_beta",
            "worst_month", "worst_3m", "worst_12m", "var95_m", "cvar95_m", "skew_m", "kurt_m", "pct_pos_months",
            "avg_in_spy_down_m", "avg_in_spy_worst10", "sharpe_2h", "calmar_2h", "maxdd_2h"]
    T = R[core].copy()
    pct = ["cagr", "vol", "maxdd", "worst_month", "worst_3m", "worst_12m", "var95_m", "cvar95_m", "pct_pos_months",
           "avg_in_spy_down_m", "avg_in_spy_worst10", "maxdd_2h"]
    for c in T.columns:
        T[c] = T[c].map((lambda x: f"{x:+.1%}") if c in pct else (lambda x: f"{x:.0f}") if c == "dd_duration_days" else (lambda x: f"{x:.2f}"))
    C = pd.DataFrame(crises).T
    Cs = C.map(lambda x: f"{x:+.1%}")
    spy_c = metrics.crisis_table(eq.iloc[:, 0].dropna(), spy)["spy"].map(lambda x: f"{x:+.1%}")
    Cs.loc["SPY"] = spy_c
    Y = pd.DataFrame(years).T
    Y.columns = [str(c) for c in Y.columns]
    Ys = Y.map(lambda x: f"{x:+.1%}" if x == x else "")
    md = (f"# Risk statistics ({tag} IV)\n\n## Distribution & tails (monthly returns)\n\n{T.to_markdown()}\n\n"
          f"## Crisis windows (strategy total return over the window; last row = SPY)\n\n{Cs.to_markdown()}\n\n"
          f"## Calendar years\n\n{Ys.to_markdown()}\n")
    (RESULTS / f"risk_{tag}.md").write_text(md)
    R.to_csv(RESULTS / f"risk_{tag}.csv")
    print(md)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
        for v in ["1x_30-10_naked_W", "5x_30-10_ES_SPAN_W", "5x_30-10_regT_1d_W", "100k_5x_half_ES_W", "3x_30-10_ES_SPAN_W"]:
            if v in eq:
                e = eq[v].dropna()
                axes[0].plot(e.index, e / e.iloc[0], lw=1.1, label=v)
                axes[1].plot(e.index, metrics.drawdown(e), lw=1.0, label=v)
        axes[0].set_yscale("log"); axes[0].set_title(f"Equity (log), IV = {tag}"); axes[0].grid(alpha=.3); axes[0].legend()
        axes[1].set_title("Drawdown"); axes[1].grid(alpha=.3)
        fig.tight_layout(); fig.savefig(RESULTS / f"drawdown_{tag}.png", dpi=120)
        print("saved", RESULTS / f"drawdown_{tag}.png")
    except Exception as e:  # noqa: BLE001
        print("plot skipped", e)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "VOLVUE")
