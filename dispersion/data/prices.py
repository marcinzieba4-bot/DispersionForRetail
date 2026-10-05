"""Daily prices via yfinance, cached to parquet.

We keep raw close (for market cap = raw close x raw shares), adjusted close
(for returns / option P&L on a split- and dividend-consistent path) and share
volume (dollar volume = raw close x volume).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .paths import CACHE

log = logging.getLogger(__name__)

FIELDS = ("close_raw", "close_adj", "volume")


def _download_chunk(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for attempt in range(4):
        try:
            raw = yf.download(tickers, start=start, end=end, auto_adjust=False, progress=False,
                              group_by="column", threads=True)
            break
        except Exception as e:  # network hiccup
            log.warning("yfinance chunk failed (%s), retry %d", e, attempt)
            time.sleep(2 ** attempt)
    else:
        return out
    if raw is None or raw.empty:
        return out
    if not isinstance(raw.columns, pd.MultiIndex):
        raw.columns = pd.MultiIndex.from_product([raw.columns, tickers])
    for t in tickers:
        try:
            c = raw["Close"][t]
            a = raw["Adj Close"][t]
            v = raw["Volume"][t]
        except KeyError:
            continue
        df = pd.DataFrame({"close_raw": c, "close_adj": a, "volume": v}).dropna(how="all")
        if df["close_adj"].notna().sum() < 20:
            continue
        out[t] = df
    return out


def fetch_prices(tickers: list[str], start="2005-01-01", end=None, chunk=80, force=False) -> dict[str, pd.DataFrame]:
    """Return {ticker: DataFrame[close_raw, close_adj, volume]} with on-disk cache."""
    end = end or (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    pdir = CACHE / "prices"
    pdir.mkdir(exist_ok=True)
    missing_marker = pdir / "_missing.txt"
    missing = set(missing_marker.read_text().split()) if missing_marker.exists() else set()
    out: dict[str, pd.DataFrame] = {}
    todo: list[str] = []
    for t in tickers:
        f = pdir / f"{t}.parquet"
        if f.exists() and not force:
            out[t] = pd.read_parquet(f)
        elif t in missing and not force:
            continue
        else:
            todo.append(t)
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        log.info("downloading %d..%d of %d", i, i + len(batch), len(todo))
        got = _download_chunk(batch, start, end)
        for t in batch:
            if t in got:
                got[t].to_parquet(pdir / f"{t}.parquet")
                out[t] = got[t]
            else:
                missing.add(t)
        missing_marker.write_text("\n".join(sorted(missing)))
    return out


def panel(prices: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    """Wide DataFrame (dates x tickers) of one field."""
    cols = {t: df[field] for t, df in prices.items()}
    p = pd.DataFrame(cols).sort_index()
    p.index = pd.DatetimeIndex(p.index).tz_localize(None)
    return p


def fetch_shares(tickers: list[str], force=False) -> pd.DataFrame:
    """Historical shares outstanding (raw, not split adjusted) from Yahoo.

    Yahoo only serves ~2015+. For earlier dates we back-fill the earliest
    observation after undoing splits (see universe.market_cap). Names with no
    data get NaN and are ranked by dollar volume instead.
    """
    import yfinance as yf

    f = CACHE / "shares.parquet"
    if f.exists() and not force:
        return pd.read_parquet(f)
    series = {}
    for t in tickers:
        try:
            s = yf.Ticker(t).get_shares_full(start="2000-01-01")
            if s is not None and len(s):
                s = s[~s.index.duplicated(keep="last")]
                s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
                series[t] = s.astype(float)
        except Exception as e:  # noqa: BLE001
            log.debug("shares %s: %s", t, e)
    sh = pd.DataFrame(series).sort_index()
    sh.to_parquet(f)
    return sh


def fetch_splits(tickers: list[str], force=False) -> pd.DataFrame:
    import yfinance as yf

    f = CACHE / "splits.parquet"
    if f.exists() and not force:
        return pd.read_parquet(f)
    rows = []
    for t in tickers:
        try:
            s = yf.Ticker(t).splits
            for d, r in s.items():
                rows.append((t, pd.Timestamp(d).tz_localize(None).normalize(), float(r)))
        except Exception:  # noqa: BLE001
            pass
    sp = pd.DataFrame(rows, columns=["ticker", "date", "ratio"])
    sp.to_parquet(f)
    return sp
