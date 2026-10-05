"""Long SPY + put-wing dispersion (PWD: short idx 25d put, long single 25d puts, split hedge).
Grid over SPY exposure s and PWD leverage Lp; both resized monthly to live equity;
SPY above 1x financed at FEDFUNDS; cash on any unused equity. Finds best Calmar."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest, third_fridays
from dispersion.data import rates
from dispersion.data.cboe import sp500_tr
from dispersion.data.universe import load_universe
from dispersion.data.volvue import VolVueIV
from dispersion.data.paths import RESULTS

uni = load_universe(); spy = uni.spy.dropna(); days = spy.index; iv = VolVueIV("iv_call_30")
rf = rates.fedfunds_daily(days); tr = sp500_tr().reindex(days.union(sp500_tr().index)).ffill().reindex(days)
r_spx = tr.pct_change().fillna(0.0)
P25 = (("RXM", +1, 1.0), ("BXMD", +1, 1.0)); C = config.StrategyConfig; R = config.REALISTIC_SPY
cyc = third_fridays(days); cyc = cyc[cyc >= "2007-01-01"]; START, END = cyc[0], cyc[-1]


def pwd_daily_ret(mult):
    """Daily P&L of the 1x fixed-notional PWD sleeve as a fraction of E0 (ex cash)."""
    cfg = C(leverage=1, index_legs=P25, singles_structure="put", short_wing_delta=None, single_put_iv_mult=mult,
            cycle="third_friday", dividends=True, costs=R, fixed_notional=True, equity=1e6, hedge_scope="split", cash_yield=False)
    e = Backtest(cfg, uni, iv).run().equity
    return (e.diff() / 1e6).reindex(days).fillna(0.0)


def book(s, Lp, pwd):
    """Compounded: each day's return on live equity = s*r_spx + Lp*pwd + cash/financing."""
    r = s * r_spx + Lp * pwd + (1.0 - s) * rf / 252.0   # (1-s)<0 -> borrowing cost
    e = (1 + r.loc[START:END]).cumprod() * 1e6
    return e


def stats(e):
    st = metrics.summary(e, spy); y = metrics.yearly(e); c = metrics.crisis_table(e, spy)["strategy"]
    st.update(y2008=y.get(2008, np.nan), y2018=y.get(2018, np.nan), y2020=y.get(2020, np.nan), y2022=y.get(2022, np.nan),
              covid=c.get("Covid_2020", np.nan), gfc=c.get("GFC_2008", np.nan))
    return st


pwd = {m: pwd_daily_ret(m) for m in (1.02, 1.05, 1.10)}
print("PWD sleeve 1x ex-cash, 1.05x: ann %.2f%% vol %.2f%%" % (pwd[1.05].loc[START:END].mean() * 252 * 100, pwd[1.05].loc[START:END].std() * np.sqrt(252) * 100))
grid = []
for m in (1.02, 1.05, 1.10):
    for s in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        for Lp in (0, 1, 2, 3, 4, 5, 6, 8, 10):
            if s == 0 and Lp == 0: continue
            st = stats(book(s, Lp, pwd[m])); st.update(mult=m, spy=s, pwd_L=Lp, gross=s + 0.9 * Lp); grid.append(st)
G = pd.DataFrame(grid); G.to_csv(RESULTS / "spy_pwd_grid_VOLVUE.csv", index=False)
cols = ["mult", "spy", "pwd_L", "gross", "cagr", "vol", "sharpe", "maxdd", "calmar", "beta", "worst_month", "y2008", "y2020", "y2022"]
def show(df, title):
    print("\n== " + title); print(df[cols].to_string(index=False, formatters={c: (lambda x: f"{x:+.1%}") for c in ["cagr", "vol", "maxdd", "worst_month", "y2008", "y2020", "y2022"]} | {"sharpe": "{:.2f}".format, "calmar": "{:.2f}".format, "beta": "{:.2f}".format, "gross": "{:.1f}".format}))
g5 = G[G.mult == 1.05]
show(g5[g5.pwd_L == 0], "SPY alone")
show(g5.sort_values("calmar", ascending=False).head(12), "best Calmar, central skew 1.05x")
for lo, hi in ((4.5, 5.5), (7.5, 8.5), (9.0, 10.5)):
    sub = g5[(g5.gross >= lo) & (g5.gross <= hi)].sort_values("calmar", ascending=False).head(5); show(sub, f"gross {lo}-{hi}x, best Calmar (1.05x)")
show(G[(G.mult == 1.02)].sort_values("calmar", ascending=False).head(5), "best Calmar at 1.02x (optimistic skew)")
show(G[(G.mult == 1.10)].sort_values("calmar", ascending=False).head(5), "best Calmar at 1.10x (stressed skew)")
# fixed SPY = 1x: effect of adding PWD leverage
show(g5[g5.spy == 1.0].sort_values("pwd_L"), "SPY 1x + PWD ladder (1.05x)")
