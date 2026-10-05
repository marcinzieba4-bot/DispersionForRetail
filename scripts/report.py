"""Render results/summary_<TAG>.csv as a markdown table next to the reference
numbers, and plot the equity curves."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd  # noqa: E402

from dispersion.config import REFERENCE  # noqa: E402
from dispersion.data.paths import RESULTS  # noqa: E402

REF_ROWS = {
    "1x_30-10_naked_W": ("1x best structure", "Sharpe 1.36, maxDD -2.9%, Calmar 1.07, gross +2.97%/yr, net +2.2..3.1%"),
    "5x_30-10_naked_W": ("5x naked/SPAN product", "+17.7%/yr, vol 11.5%, Sharpe 1.54, Sortino 3.4, maxDD -13.6%, Calmar 1.31, beta 0.37, worst month -7.0%, 2008 +15%"),
    "5x_30-10_ES_SPAN_W": ("5x ES/MES SPAN (default retail)", "same as 5x naked, ES costs 0.3%"),
    "5x_30-10_regT_1d_W": ("5x Reg-T 1-delta wing", "Sharpe 1.46-1.55, Calmar 1.19-1.28"),
    "100k_5x_half_ES_W": ("$100k rotating half", "+19.6%/yr, Sharpe 1.44, Calmar 1.12"),
    "5x_30-10_spec_headline_costs_W": ("5x with the spec's flat 0.7%/yr per 1x cost", "matches the other session's convention"),
}


def main(tag="PROXY"):
    df = pd.read_csv(RESULTS / f"summary_{tag}.csv", index_col=0)
    cols = ["cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "maxdd_monthly", "calmar_monthly", "calmar_2h", "beta", "worst_month", "y2008", "y2022"]
    out = df[cols].copy()
    for c in ["cagr", "vol", "maxdd", "maxdd_monthly", "worst_month", "y2008", "y2022"]:
        out[c] = out[c].map(lambda x: f"{x:+.1%}")
    for c in ["sharpe", "sortino", "calmar", "calmar_monthly", "calmar_2h", "beta"]:
        out[c] = out[c].map(lambda x: f"{x:.2f}")
    out["reference (spec §8)"] = [REF_ROWS.get(i, ("", ""))[1] for i in out.index]
    md = out.to_markdown()
    (RESULTS / f"summary_{tag}.md").write_text(md)
    print(md)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = pd.read_csv(RESULTS / f"equity_{tag}.csv", index_col=0, parse_dates=True)
        fig, ax = plt.subplots(figsize=(11, 5.5))
        for c in ["1x_30-10_naked_W", "1x_30-10_gs_W", "5x_30-10_ES_SPAN_W", "5x_30-10_regT_1d_W", "100k_5x_half_ES_W"]:
            if c in eq:
                ax.plot(eq.index, eq[c] / eq[c].dropna().iloc[0], label=c, lw=1.2)
        ax.set_yscale("log")
        ax.set_title(f"Mega-cap call dispersion, equity (log) - IV source: {tag}")
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(RESULTS / f"equity_{tag}.png", dpi=120)
        print("saved", RESULTS / f"equity_{tag}.png")
    except Exception as e:  # noqa: BLE001
        print("plot skipped:", e)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "PROXY")
