"""Run the reference variants and print the comparison table."""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd  # noqa: E402

from dispersion import config  # noqa: E402
from dispersion.backtest import metrics  # noqa: E402
from dispersion.backtest.engine import Backtest  # noqa: E402
from dispersion.data.iv import get_provider  # noqa: E402
from dispersion.data.paths import RESULTS  # noqa: E402
from dispersion.data.universe import load_universe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def variants(args):
    C = config.StrategyConfig
    v = {
        "1x_30-10_naked_W": C(leverage=1),
        "1x_30-10_naked_nohedge": C(leverage=1, hedge_freq=None),
        "1x_30-10_naked_D": C(leverage=1, hedge_freq="D"),
        "1x_longcall_naked_W": C(leverage=1, short_wing_delta=None),
        "1x_30-10_regT_1d_W": C(leverage=1, index_wing_delta=1),
        "1x_30-10_regT_5d_W(rejected)": C(leverage=1, index_wing_delta=5),
        "1x_30-10_gs_W": C(leverage=1, costs=config.INSTITUTIONAL),
        "5x_30-10_naked_W": C(leverage=5),
        "5x_30-10_ES_SPAN_W": C(leverage=5, index_instrument="ES", costs=config.RETAIL_ES),
        "5x_30-10_regT_1d_W": C(leverage=5, index_wing_delta=1),
        "5x_30-10_gs_W": C(leverage=5, costs=config.INSTITUTIONAL),
        "100k_5x_half_ES_W": C(leverage=5, index_instrument="ES", costs=config.RETAIL_ES, rotating_half=True,
                               contract_granularity=True, equity=100_000),
        "3x_30-10_ES_SPAN_W": C(leverage=3, index_instrument="ES", costs=config.RETAIL_ES),
    }
    if args.only:
        v = {k: c for k, c in v.items() if any(s in k for s in args.only)}
    return v


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="substring filters on variant names")
    ap.add_argument("--start", default="2007-01-31")
    ap.add_argument("--end", default=None)
    ap.add_argument("--iv", default="auto")
    args = ap.parse_args()
    config.Z_DELTA.setdefault(5, -1.6449)

    uni = load_universe()
    ivp = get_provider(args.iv, uni.close_adj, uni.spy)
    rows, curves = [], {}
    for name, cfg in variants(args).items():
        cfg = config.StrategyConfig(**{**cfg.__dict__, "start": args.start, "end": args.end})
        res = Backtest(cfg, uni, ivp).run()
        st = metrics.summary(res.equity, uni.spy)
        sh = metrics.second_half(res.equity)
        st.update(calmar_2h=sh["calmar"], sharpe_2h=sh["sharpe"], hedge_turnover_x_book=
                  res.hedge_turnover.resample("ME").sum().mean() / (cfg.book_fraction * cfg.leverage * res.equity.resample("ME").last().mean()))
        yr = metrics.yearly(res.equity)
        st.update({f"y{y}": v for y, v in yr.items()})
        st["variant"] = name
        rows.append(st)
        curves[name] = res.equity
        print(f"{name:32s} {metrics.fmt(st)}  calmar_2h={sh['calmar']:.2f}  2008={yr.get(2008, float('nan')):+.1%}")
    tag = "PROXY" if ivp.is_proxy else "LICENSED"
    df = pd.DataFrame(rows).set_index("variant")
    df.to_csv(RESULTS / f"summary_{tag}.csv")
    pd.DataFrame(curves).to_csv(RESULTS / f"equity_{tag}.csv")
    cov = Backtest(config.StrategyConfig(), uni, ivp).uni.coverage(Backtest(config.StrategyConfig(), uni, ivp).me)
    print("\nIV source:", ivp.name)
    print("universe coverage (names/30): mean=%.1f min=%d" % (cov.mean(), cov.min()))
    print("saved", RESULTS / f"summary_{tag}.csv")
