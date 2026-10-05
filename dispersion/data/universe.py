"""Point-in-time universe: top-100 S&P members by market cap, then top-30 by
trailing-12m median daily dollar volume (shifted one month, so known at entry).

Market cap = raw close x raw shares outstanding. Yahoo serves share counts only
from ~2015, so earlier dates use the first observation un-split (a share count
backfilled through splits; buybacks/issuance are not captured). That only
affects which names make the *100* cut; the traded 30 are the most liquid, who
are near-invariably in the top-100 anyway.

Known bias: names delisted before today (LEH, BSC, WB, ...) have no Yahoo
prices and therefore cannot enter the universe; `coverage()` reports how many
of each month's 30 slots were filled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import config
from . import constituents, prices as P

log = logging.getLogger(__name__)


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).last().values)


def shares_panel(close_raw: pd.DataFrame, shares: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Daily raw share count aligned to close_raw, backfilled through splits."""
    idx = close_raw.index
    out = pd.DataFrame(np.nan, index=idx, columns=close_raw.columns)
    sp = splits.set_index("ticker") if len(splits) else None
    for t in close_raw.columns:
        if t not in shares.columns:
            continue
        s = shares[t].dropna()
        if s.empty:
            continue
        s = s[~s.index.duplicated()].sort_index()
        ser = s.reindex(idx.union(s.index)).ffill().reindex(idx).to_numpy(dtype=float).copy()
        first = s.index[0]
        # backfill before the first observation, undoing splits after t
        before = idx < first
        if before.any():
            val = np.full(before.sum(), float(s.iloc[0]))
            if sp is not None and t in sp.index:
                rows = sp.loc[[t]]
                for d, r in zip(rows["date"], rows["ratio"]):
                    if r > 0 and d <= first:
                        val[idx[before] < d] /= r
            ser[before] = val
        out[t] = ser
    return out


@dataclass
class Universe:
    close_adj: pd.DataFrame     # dates x tickers (split+div adjusted)
    close_raw: pd.DataFrame
    volume: pd.DataFrame
    shares: pd.DataFrame
    spy: pd.Series              # adjusted SPY close
    spy_raw: pd.Series | None = None   # raw SPY close (contract counts)
    members_fn: callable = constituents.members_on
    liq_window: int = 252
    top_mcap: int = config.TOP_MCAP
    top_liq: int = config.TOP_LIQ

    def __post_init__(self):
        dv = (self.close_raw * self.volume)
        # trailing 12m median daily dollar volume, evaluated at month-ends
        me = month_ends(dv.index)
        med = {}
        for d in me:
            w = dv.loc[:d].tail(self.liq_window)
            if len(w) < min(120, int(0.8 * self.liq_window)):
                continue
            ok = w.notna().sum() >= 0.8 * len(w)
            med[d] = w.median().where(ok)
        self.liq_at_me = pd.DataFrame(med).T.sort_index()
        # shifted one month: value known at entry = previous month-end
        self.liq_known = self.liq_at_me.shift(1)
        self.mcap = self.close_raw * self.shares
        self._cache: dict[pd.Timestamp, list[str]] = {}
        if self.spy_raw is None:
            self.spy_raw = self.spy

    def raw_price(self, ticker: str, date) -> float:
        """Unadjusted close, used only to count contracts (100 shares each)."""
        s = self.spy_raw if ticker == "SPY" else self.close_raw[ticker]
        v = s.loc[:date].iloc[-1] if ticker != "SPY" or date not in s.index else s.loc[date]
        return float(v) if np.isfinite(v) else float(self.close_adj[ticker].loc[date]) if ticker != "SPY" else float(self.spy.loc[date])

    def tradable(self, date) -> list[str]:
        """Ranked (most liquid first) tradable names as of month-end `date`."""
        date = pd.Timestamp(date)
        if date in self._cache:
            return self._cache[date]
        members = [t for t in self.members_fn(date) if t in self.close_adj.columns]
        if date not in self.mcap.index:
            date = self.mcap.index[self.mcap.index.searchsorted(date, side="right") - 1]
        mc = self.mcap.loc[date, members].dropna()
        base = mc.sort_values(ascending=False).head(self.top_mcap).index.tolist()
        if date not in self.liq_known.index:
            self._cache[date] = []
            return []
        liq = self.liq_known.loc[date, base].dropna()
        liq = liq[self.close_adj.loc[date, liq.index].notna()]
        names = liq.sort_values(ascending=False).head(self.top_liq).index.tolist()
        self._cache[date] = names
        return names

    def coverage(self, dates) -> pd.Series:
        return pd.Series({d: len(self.tradable(d)) for d in dates})


def load_universe(start="2004-01-01") -> Universe:
    tick = constituents.all_tickers(start)
    px = P.fetch_prices(tick + ["SPY"], start=start)
    close_adj = P.panel(px, "close_adj")
    close_raw = P.panel(px, "close_raw")
    volume = P.panel(px, "volume")
    spy = close_adj.pop("SPY")
    spy_raw = close_raw.pop("SPY")
    volume = volume.drop(columns="SPY")
    shares = P.fetch_shares([t for t in close_adj.columns])  # cached
    splits = P.fetch_splits(list(close_adj.columns))
    sh = shares_panel(close_raw, shares, splits)
    return Universe(close_adj, close_raw, volume, sh, spy, spy_raw=spy_raw)
