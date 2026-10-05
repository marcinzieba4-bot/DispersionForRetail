"""FRED series: FEDFUNDS (cash yield on unused equity), VIXCLS, single-name
CBOE vol indices (VXAPLCLS ... used only to calibrate the proxy IV ratio)."""
from __future__ import annotations

import io

import pandas as pd
import requests

from .paths import CACHE

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
SINGLE_NAME_VX = {"AAPL": "VXAPLCLS", "AMZN": "VXAZNCLS", "GOOGL": "VXGOGCLS", "GS": "VXGSCLS", "IBM": "VXIBMCLS"}


def fred(sid: str, force=False) -> pd.Series:
    f = CACHE / f"fred_{sid}.parquet"
    if f.exists() and not force:
        return pd.read_parquet(f)[sid]
    r = requests.get(FRED.format(sid=sid), timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), na_values=".")
    df.columns = ["date", sid]
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")[sid].astype(float)
    s.to_frame().to_parquet(f)
    return s


def fedfunds_daily(index: pd.DatetimeIndex) -> pd.Series:
    """Annualised cash rate (decimal) aligned to a daily index."""
    ff = fred("FEDFUNDS") / 100.0
    return ff.reindex(ff.index.union(index)).ffill().reindex(index).fillna(0.0)


def vix() -> pd.Series:
    return fred("VIXCLS").dropna()
