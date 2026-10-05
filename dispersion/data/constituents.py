"""Point-in-time S&P 500 membership.

Source: the public fja05680/sp500 dataset (one row per membership change,
`date,tickers`). We clone it once into the cache and build a daily-stepped
membership table. No survivorship: a name is in the universe on a date only if
it was in the index on that date.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .paths import CACHE

REPO_URL = "https://github.com/fja05680/sp500.git"
CSV_NAME = "S&P 500 Historical Components & Changes (Updated).csv"


def _ensure_repo() -> Path:
    d = CACHE / "sp500_repo"
    if not (d / CSV_NAME).exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", REPO_URL, str(d)], check=True)
    return d / CSV_NAME


def to_yahoo(t: str) -> str:
    """fja05680 uses 'BRK.B'; Yahoo uses 'BRK-B'."""
    return t.replace(".", "-")


@lru_cache(maxsize=1)
def membership_changes() -> pd.DataFrame:
    df = pd.read_csv(_ensure_repo())
    df["date"] = pd.to_datetime(df["date"])
    df["tickers"] = df["tickers"].apply(lambda s: tuple(sorted(to_yahoo(x) for x in s.split(","))))
    return df.sort_values("date").reset_index(drop=True)


def members_on(date) -> list[str]:
    """Index members as of `date` (last change on or before date)."""
    df = membership_changes()
    i = df["date"].searchsorted(pd.Timestamp(date), side="right") - 1
    if i < 0:
        i = 0
    return list(df.loc[i, "tickers"])


def all_tickers(start="2005-01-01") -> list[str]:
    df = membership_changes()
    df = df[df["date"] >= pd.Timestamp(start) - pd.DateOffset(months=14)]
    s: set[str] = set()
    for t in df["tickers"]:
        s.update(t)
    return sorted(s)
