"""Performance statistics on a daily equity curve."""
from __future__ import annotations

import numpy as np
import pandas as pd


def monthly_returns(equity: pd.Series) -> pd.Series:
    me = equity.groupby([equity.index.year, equity.index.month]).last()
    me.index = pd.to_datetime([f"{y}-{m:02d}-01" for y, m in me.index]) + pd.offsets.MonthEnd(0)
    return me.pct_change().dropna()


def drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def summary(equity: pd.Series, spy: pd.Series | None = None, rf: pd.Series | None = None) -> dict:
    eq = equity.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    mr = monthly_returns(eq)
    vol = mr.std() * np.sqrt(12)
    sharpe = mr.mean() / mr.std() * np.sqrt(12) if mr.std() > 0 else np.nan
    down = mr[mr < 0]
    sortino = mr.mean() * 12 / (np.sqrt((down ** 2).mean()) * np.sqrt(12)) if len(down) else np.nan
    dd = drawdown(eq)
    maxdd = dd.min()
    calmar = cagr / abs(maxdd) if maxdd < 0 else np.nan
    out = dict(cagr=cagr, vol=vol, sharpe=sharpe, sortino=sortino, maxdd=maxdd, calmar=calmar,
               worst_month=mr.min(), best_month=mr.max(), months=len(mr), years=yrs,
               pct_pos_months=(mr > 0).mean())
    if spy is not None:
        sm = monthly_returns(spy.reindex(eq.index).ffill())
        j = mr.index.intersection(sm.index)
        if len(j) > 12:
            cov = np.cov(mr[j], sm[j])
            out["beta"] = cov[0, 1] / cov[1, 1]
            out["corr"] = np.corrcoef(mr[j], sm[j])[0, 1]
    return out


def yearly(equity: pd.Series) -> pd.Series:
    ye = equity.groupby(equity.index.year).last()
    first = equity.iloc[0]
    prev = pd.concat([pd.Series([first], index=[ye.index[0] - 1]), ye]).shift(1).iloc[1:]
    return (ye / prev.values - 1.0)


def second_half(equity: pd.Series) -> dict:
    n = len(equity)
    return summary(equity.iloc[n // 2:])


def fmt(stats: dict) -> str:
    keys = [("cagr", "{:+.1%}"), ("vol", "{:.1%}"), ("sharpe", "{:.2f}"), ("sortino", "{:.2f}"),
            ("maxdd", "{:+.1%}"), ("calmar", "{:.2f}"), ("beta", "{:.2f}"), ("worst_month", "{:+.1%}")]
    return "  ".join(f"{k}={f.format(stats[k])}" for k, f in keys if k in stats and stats[k] == stats[k])
