"""Full risk report for the regime book ex cash (1x, 3x) and the 30% SPY + 70% regime-book blend."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data.paths import RESULTS
uni = load_universe(); spy = uni.spy.dropna()
eqf = pd.read_csv(RESULTS / "regime_book_equity_VOLVUE.csv", index_col=0, parse_dates=True)
book = eqf["regime_book_1x_excash"] - 1e6                       # additive $ P&L on 1M fixed notional, ex cash
spy = spy.reindex(book.index).ffill(); spy_tr = spy / spy.iloc[0]    # adjusted close = total return
BOOKS = {
    "regime book ex cash 1x": 1e6 + book,
    "regime book ex cash 3x": 1e6 + 3 * book,
    "SPY (buy & hold, TR)": 1e6 * spy_tr,
    "30% SPY b&h + 70% regime book 1x": 0.3e6 * spy_tr + 0.7e6 + 0.7 * book,
    "30% SPY b&h + 70% regime book 3x": 0.3e6 * spy_tr + 0.7e6 + 0.7 * 3 * book,
}
# monthly-rebalanced 30/70 blend: each third-Friday cycle, 30% of equity in SPY, 70% as the regime book's capital at L x fixed notional
def rebalanced(L):
    ent = [d for d in book.index if d in set(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index)] + [book.index[-1]]
    E = 1e6; out = pd.Series(np.nan, index=book.index); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        w = book.loc[a:b]; s = spy.loc[a:b]
        seg = 0.3 * E * (s / s.iloc[0]) + 0.7 * E + 0.7 * E / 1e6 * L * (w - w.iloc[0])
        out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
    return out.ffill()
BOOKS["30/70 rebalanced monthly, book 1x"] = rebalanced(1)
BOOKS["30/70 rebalanced monthly, book 3x"] = rebalanced(3)
rows = {}
for k, e in BOOKS.items():
    st = metrics.summary(e, uni.spy); st.update(sharpe_2h=metrics.second_half(e)["sharpe"]); rows[k] = st
    y = metrics.yearly(e); c = metrics.crisis_table(e, uni.spy)
    print(f"\n=== {k} ===")
    print(metrics.fmt(st))
    print("  " + " ".join(f"{kk}={v:+.1%}" for kk, v in y.items()))
    print("  crises: " + " ".join(f"{i}={r.strategy:+.1%}(spy {r.spy:+.1%})" for i, r in c.iterrows()))
pd.DataFrame(rows).T.to_csv(RESULTS / "regime_risk_VOLVUE.csv")
print("DONE")
