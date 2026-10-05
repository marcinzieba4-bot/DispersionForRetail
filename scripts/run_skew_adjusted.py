"""Skew-adjusted realistic scenario. Per-leg IV multipliers measured on the
CBOE closing chain for the month-end expiry (results/live_engine_vs_market.csv):
singles long 30d ~1.00, short 10d ~1.02 (smile), SPY short 30d ~0.915
(call wing 1.7 pts under ATM), SPY 1d wing ~0.98. Bracketed with a milder
index skew (0.95) since one snapshot in a VIX-16 regime is not a surface history."""
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
    measured = dict(iv_mult_long=1.00, iv_mult_wing=1.02, iv_mult_index=0.915, iv_mult_index_wing=0.98)
    mild = dict(iv_mult_long=1.00, iv_mult_wing=1.02, iv_mult_index=0.95, iv_mult_index_wing=0.98)
    V = {
        "5x_ES_flatIV(realistic costs)": C(leverage=5, index_instrument="ES", costs=R),
        "5x_ES_skew_mild(idx0.95)": C(leverage=5, index_instrument="ES", costs=R, **mild),
        "5x_ES_skew_measured(idx0.915)": C(leverage=5, index_instrument="ES", costs=R, **measured),
        "3x_ES_skew_measured": C(leverage=3, index_instrument="ES", costs=R, **measured),
        "1x_ES_skew_measured": C(leverage=1, index_instrument="ES", costs=R, **measured),
        "5x_regT_1d_skew_measured": C(leverage=5, index_wing_delta=1, costs=config.REALISTIC_SPY, **measured),
        "100k_5x_half_skew_measured": C(leverage=5, index_instrument="ES", costs=R, rotating_half=True, contract_granularity=True, equity=100_000, **measured),
        "5x_ES_skew_measured_x1.3costs": C(leverage=5, index_instrument="ES", costs=config.RETAIL_ES, **measured),
    }
    rows, cr, yrs, curves = {}, {}, {}, {}
    for k, cfg in V.items():
        res = Backtest(cfg, uni, iv).run()
        e = res.equity
        st = metrics.summary(e, uni.spy)
        sh = metrics.second_half(e)
        st.update(sharpe_2h=sh["sharpe"], calmar_2h=sh["calmar"], calmar_m_2h=sh["calmar_monthly"])
        rows[k] = st; cr[k] = metrics.crisis_table(e, uni.spy)["strategy"]; yrs[k] = metrics.yearly(e); curves[k] = e
        print(k, metrics.fmt(st))
    df = pd.DataFrame(rows).T
    df.to_csv(RESULTS / "skew_adjusted_VOLVUE.csv")
    core = ["cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "maxdd_monthly", "calmar_monthly", "beta", "down_beta",
            "worst_month", "worst_12m", "var95_m", "cvar95_m", "pct_pos_months", "sharpe_2h", "calmar_2h", "calmar_m_2h"]
    T = df[core].copy()
    pct = {"cagr", "vol", "maxdd", "maxdd_monthly", "worst_month", "worst_12m", "var95_m", "cvar95_m", "pct_pos_months"}
    for c in T.columns:
        T[c] = T[c].map((lambda x: f"{x:+.1%}") if c in pct else (lambda x: f"{x:.2f}"))
    Cs = pd.DataFrame(cr).T.map(lambda x: f"{x:+.1%}")
    Y = pd.DataFrame(yrs).T; Y.columns = [str(c) for c in Y.columns]
    Ys = Y.map(lambda x: f"{x:+.1%}" if x == x else "")
    md = ("# Skew-adjusted realistic scenario (VolVue iv_call_30, measured per-leg IV ratios)\n\n"
          f"## Risk statistics\n\n{T.to_markdown()}\n\n## Crisis windows\n\n{Cs.to_markdown()}\n\n## Calendar years\n\n{Ys.to_markdown()}\n")
    (RESULTS / "skew_adjusted_VOLVUE.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
