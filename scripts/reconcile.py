"""Reconcile the compounded daily-marked engine with alternative accounting
conventions: additive (non-compounded) returns, monthly-granular drawdown,
lighter cost models, put/mean IV. Explains which choices move Calmar."""
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
from dispersion.data.volvue import VolVueIV  # noqa: E402


def stats_block(mr: pd.Series, daily_eq: pd.Series | None, spy_m: pd.Series, L_scale=1.0):
    """mr = monthly simple returns of the sleeve at L=1 (or already at L)."""
    out = {}
    # compounded, monthly granularity
    eq_m = (1 + mr).cumprod()
    out["cagr_cmp"] = eq_m.iloc[-1] ** (12 / len(mr)) - 1
    out["maxdd_cmp_monthly"] = (eq_m / eq_m.cummax() - 1).min()
    if daily_eq is not None:
        out["maxdd_cmp_daily"] = metrics.drawdown(daily_eq).min()
    out["calmar_cmp_m"] = out["cagr_cmp"] / abs(out["maxdd_cmp_monthly"])
    if daily_eq is not None:
        out["calmar_cmp_d"] = out["cagr_cmp"] / abs(out["maxdd_cmp_daily"])
    # additive (non-compounded): equity = 1 + cumsum(r)
    add = mr.cumsum()
    out["mean_ann_add"] = mr.mean() * 12
    out["maxdd_add"] = (add - add.cummax()).min()
    out["calmar_add"] = out["mean_ann_add"] / abs(out["maxdd_add"])
    out["vol"] = mr.std() * np.sqrt(12)
    out["sharpe"] = mr.mean() / mr.std() * np.sqrt(12)
    out["worst_m"] = mr.min()
    j = mr.index.intersection(spy_m.index)
    out["beta"] = np.cov(mr[j], spy_m[j])[0, 1] / spy_m[j].var()
    # halves
    h = len(mr) // 2
    for tag, sl in (("1h", mr.iloc[:h]), ("2h", mr.iloc[h:])):
        e = (1 + sl).cumprod(); a = sl.cumsum()
        out[f"calmar_cmp_{tag}"] = (e.iloc[-1] ** (12 / len(sl)) - 1) / abs((e / e.cummax() - 1).min())
        out[f"calmar_add_{tag}"] = (sl.mean() * 12) / abs((a - a.cummax()).min())
    return out


def main():
    uni = load_universe()
    spy_m = metrics.monthly_returns(uni.spy)
    rows = {}
    C = config.StrategyConfig
    base_kw = dict(index_instrument="ES", costs=config.RETAIL_ES)
    ivc = VolVueIV("iv_call_30")

    def run(name, cfg, ivp=ivc, drag_per_1x=0.0):
        res = Backtest(cfg, uni, ivp).run()
        mr = metrics.monthly_returns(res.equity)
        if drag_per_1x:
            mr = mr - drag_per_1x * cfg.leverage / 12
        rows[name] = stats_block(mr, res.equity if not drag_per_1x else None, spy_m)
        rows[name]["L"] = cfg.leverage
        print(name, {k: round(v, 3) for k, v in rows[name].items()})
        return mr

    # 1) leverage ladder, full engine (compounded, monthly reset, full retail costs)
    for L in (1, 3, 5, 8, 10):
        run(f"full_costs_L{L}", C(leverage=L, **base_kw))
    # 2) cost conventions at 5x
    run("5x_no_commission", C(leverage=5, index_instrument="ES", costs=config.CostModel("es_nocomm", index_opt=0.003, commission_open=0.0)))
    run("5x_no_1.3_mult_no_comm", C(leverage=5, index_instrument="ES", costs=config.CostModel("es_nomult", index_opt=0.003, commission_open=0.0, mult=1.0)))
    run("5x_half_spread_on_net_vertical", C(leverage=5, index_instrument="ES", costs=config.CostModel("es_half", single_30d=0.015, single_10d=0.04, index_opt=0.003, commission_open=0.0)))
    zero = config.CostModel("zero", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    run("5x_gross_zero_costs", C(leverage=5, costs=zero))
    run("5x_spec_headline_0.7pct_per_1x", C(leverage=5, costs=zero), drag_per_1x=0.007)
    run("5x_spec_headline_0.7pct_no_cash", C(leverage=5, costs=zero, cash_yield=False), drag_per_1x=0.007)
    # 3) IV field sensitivity at 5x, full costs
    run("5x_iv_put_30", C(leverage=5, **base_kw), ivp=VolVueIV("iv_put_30"))
    run("5x_iv_mean_30", C(leverage=5, **base_kw), ivp=VolVueIV("iv_mean_30"))
    # 4) no cash yield (pure sleeve)
    run("5x_full_costs_no_cash", C(leverage=5, cash_yield=False, **base_kw))
    df = pd.DataFrame(rows).T
    df.to_csv(RESULTS / "reconcile_VOLVUE.csv")
    cols = ["L", "cagr_cmp", "mean_ann_add", "vol", "sharpe", "beta", "worst_m", "maxdd_cmp_daily", "maxdd_cmp_monthly", "maxdd_add",
            "calmar_cmp_d", "calmar_cmp_m", "calmar_add", "calmar_cmp_1h", "calmar_cmp_2h", "calmar_add_1h", "calmar_add_2h"]
    T = df[cols].copy()
    pct = {"cagr_cmp", "mean_ann_add", "vol", "worst_m", "maxdd_cmp_daily", "maxdd_cmp_monthly", "maxdd_add"}
    for c in T.columns:
        T[c] = T[c].map((lambda x: f"{x:+.1%}" if x == x else "") if c in pct else (lambda x: f"{x:.0f}") if c == "L" else (lambda x: f"{x:.2f}" if x == x else ""))
    md = "# Reconciliation of accounting conventions (VolVue iv_call_30)\n\n" + T.to_markdown() + "\n"
    (RESULTS / "reconcile_VOLVUE.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
