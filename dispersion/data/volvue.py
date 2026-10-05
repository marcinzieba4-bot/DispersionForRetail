"""VolVue 30-day implied vol feed.

GET https://api.volvue.com/query?apiKey=..&format=csv&data=<json>
  data = {startDate, endDate, lastDateOnly, tickers[], fields[], conditions[], order[], limit}
Fields used: iv_call_30, iv_put_30, iv_mean_30, iv_skew_30 (vol points).
Key from VOLVUE_API_KEY. Bulk history is cached in data/cache/volvue_iv30.parquet.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time

import pandas as pd
import requests

from .iv import IVProvider
from .paths import CACHE

log = logging.getLogger(__name__)
URL = "https://api.volvue.com/query"
FIELDS = ["ticker", "date", "iv_call_30", "iv_put_30", "iv_mean_30", "iv_skew_30"]
CACHE_FILE = CACHE / "volvue_iv30.parquet"


def query(tickers: list[str], start: str, end: str, fields=FIELDS, retries=4) -> pd.DataFrame:
    key = os.environ.get("VOLVUE_API_KEY")
    if not key:
        raise RuntimeError("VOLVUE_API_KEY not set")
    data = {"startDate": start, "endDate": end, "lastDateOnly": False, "tickers": tickers, "fields": fields,
            "conditions": [], "order": [{"field": "ticker", "direction": "asc"}, {"field": "date", "direction": "asc"}],
            "limit": None}
    for a in range(retries):
        try:
            r = requests.get(URL, params={"apiKey": key, "format": "csv", "data": json.dumps(data)}, timeout=300)
            if r.status_code == 429:
                time.sleep(5 * (a + 1))
                continue
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
        except (requests.RequestException, ValueError) as e:
            log.warning("volvue query failed (%s) attempt %d", e, a)
            time.sleep(2 ** a)
    raise RuntimeError("volvue query failed")


def fetch_history(tickers: list[str], start="2006-01-01", end=None, batch=25, force=False) -> pd.DataFrame:
    """Long-format daily IV for every ticker; incremental on-disk cache."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    have = pd.read_parquet(CACHE_FILE) if CACHE_FILE.exists() and not force else pd.DataFrame(columns=FIELDS)
    done = set(have["ticker"].unique()) if len(have) else set()
    todo = [t.replace("-", ".") if False else t for t in tickers if t not in done]
    frames = [have] if len(have) else []
    for i in range(0, len(todo), batch):
        b = todo[i:i + batch]
        log.info("volvue %d..%d of %d", i, i + len(b), len(todo))
        df = query(b, start, end)
        if len(df):
            frames.append(df)
        # mark empties so we do not refetch forever
        for t in b:
            if t not in set(df["ticker"]) if len(df) else True:
                frames.append(pd.DataFrame({"ticker": [t], "date": [pd.Timestamp(start)]}))
        out = pd.concat(frames, ignore_index=True)
        out.to_parquet(CACHE_FILE)
    out = pd.concat(frames, ignore_index=True) if frames else have
    return out


class VolVueIV(IVProvider):
    """Traded 30-day IV from VolVue. `field` = iv_call_30 (what we trade) by default."""
    name = "VolVue(iv_call_30)"
    is_proxy = False

    def __init__(self, field="iv_call_30", long_df: pd.DataFrame | None = None):
        self.field = field
        self.name = f"VolVue({field})"
        self._long = long_df
        self._panel = None

    def long(self) -> pd.DataFrame:
        if self._long is None:
            if not CACHE_FILE.exists():
                raise FileNotFoundError("run scripts/fetch_volvue.py first")
            self._long = pd.read_parquet(CACHE_FILE)
        return self._long

    def panel(self) -> pd.DataFrame:
        if self._panel is None:
            d = self.long().dropna(subset=[self.field])
            p = d.pivot_table(index="date", columns="ticker", values=self.field).sort_index() / 100.0
            p.columns = [c.replace(".", "-") for c in p.columns]
            self._panel = p
        return self._panel

    def field_panel(self, field: str) -> pd.DataFrame:
        d = self.long().dropna(subset=[field])
        p = d.pivot_table(index="date", columns="ticker", values=field).sort_index()
        p.columns = [c.replace(".", "-") for c in p.columns]
        return p
