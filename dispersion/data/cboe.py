"""Cboe S&P 500 option-writing indices (real traded settlement prices) and the
S&P 500 total return, for a market-priced index leg.

  BXMD: writes the 30-delta SPX call monthly (3rd Friday), held to expiry
  BXM : ATM call      BXY: 2% OTM call      PUT: ATM put-write     CLL: collar
Files: data/cache/cboe/<IDX>.csv (DATE,<IDX>), SP500TR.csv from yfinance.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from .paths import CACHE

DIR = CACHE / "cboe"
URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{idx}_History.csv"


def cboe_index(idx: str) -> pd.Series:
    DIR.mkdir(exist_ok=True)
    f = DIR / f"{idx}.csv"
    if not f.exists():
        r = requests.get(URL.format(idx=idx), timeout=60, allow_redirects=True)
        r.raise_for_status()
        f.write_text(r.text)
    df = pd.read_csv(f)
    df.columns = ["date", "px"]
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["px"].astype(float).sort_index()


def sp500_tr() -> pd.Series:
    f = DIR / "SP500TR.csv"
    if not f.exists():
        import yfinance as yf
        d = yf.download("^SP500TR", start="1990-01-01", progress=False, auto_adjust=False)["Close"]
        d.to_csv(f)
    df = pd.read_csv(f, index_col=0, parse_dates=True).iloc[:, 0]
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    return df.astype(float).dropna().sort_index()


def short_call_pnl_panel(idx: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Daily levels aligned to `index` (ffilled): columns [idx, tr]."""
    a = cboe_index(idx).reindex(index.union(cboe_index(idx).index)).ffill().reindex(index)
    b = sp500_tr().reindex(index.union(sp500_tr().index)).ffill().reindex(index)
    return pd.DataFrame({"idx": a, "tr": b})
