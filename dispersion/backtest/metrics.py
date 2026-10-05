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
    # drawdown duration: longest peak-to-recovery stretch in calendar days
    under = dd < 0
    longest, cur, start = 0, 0, None
    for d, u in under.items():
        if u and start is None:
            start = d
        if not u and start is not None:
            longest = max(longest, (d - start).days)
            start = None
    if start is not None:
        longest = max(longest, (dd.index[-1] - start).days)
    out = dict(cagr=cagr, vol=vol, sharpe=sharpe, sortino=sortino, maxdd=maxdd, calmar=calmar,
               worst_month=mr.min(), best_month=mr.max(), months=len(mr), years=yrs,
               pct_pos_months=(mr > 0).mean(),
               var95_m=mr.quantile(0.05), cvar95_m=mr[mr <= mr.quantile(0.05)].mean(),
               skew_m=mr.skew(), kurt_m=mr.kurt(), dd_duration_days=longest,
               worst_3m=(1 + mr).rolling(3).apply(np.prod, raw=True).min() - 1 if len(mr) >= 3 else np.nan,
               worst_12m=(1 + mr).rolling(12).apply(np.prod, raw=True).min() - 1 if len(mr) >= 12 else np.nan)
    if spy is not None:
        sm = monthly_returns(spy.reindex(eq.index).ffill())
        j = mr.index.intersection(sm.index)
        if len(j) > 12:
            cov = np.cov(mr[j], sm[j])
            out["beta"] = cov[0, 1] / cov[1, 1]
            out["corr"] = np.corrcoef(mr[j], sm[j])[0, 1]
            dn = j[sm[j] < 0]
            if len(dn) > 6:
                c2 = np.cov(mr[dn], sm[dn])
                out["down_beta"] = c2[0, 1] / c2[1, 1]
                out["avg_in_spy_down_m"] = mr[dn].mean()
            worst_spy = sm[j].nsmallest(10).index
            out["avg_in_spy_worst10"] = mr[worst_spy].mean()
    return out


CRISIS_WINDOWS = {"GFC_2008": ("2008-01-01", "2008-12-31"), "Aug2011": ("2011-07-01", "2011-09-30"),
                  "Feb2018_volmageddon": ("2018-01-26", "2018-02-28"), "Q4_2018": ("2018-10-01", "2018-12-31"),
                  "Covid_2020": ("2020-02-19", "2020-03-31"), "Bear_2022": ("2022-01-01", "2022-12-31"),
                  "Apr2025_tariffs": ("2025-04-01", "2025-04-30")}


def crisis_table(equity: pd.Series, spy: pd.Series) -> pd.DataFrame:
    rows = {}
    for k, (a, b) in CRISIS_WINDOWS.items():
        e = equity.loc[a:b]
        s = spy.loc[a:b]
        if len(e) > 2:
            rows[k] = {"strategy": e.iloc[-1] / e.iloc[0] - 1, "spy": s.iloc[-1] / s.iloc[0] - 1}
    return pd.DataFrame(rows).T


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
