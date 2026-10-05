"""30-day implied vol provider.

The strategy's critical input is a *traded* 30-day IV per name and for SPY at
each month-end. Licensed sources are loaded from CSV dropped in data/iv/:

    ORATS   (ticker, tradeDate, iv30d | orIv30d)
    VolVue  (symbol|ticker, date, iv_call_30, iv_put_30)      vol points
    IvyDB   (ticker, date, days=30, cp_flag, impl_volatility)  decimal

If none is present the engine falls back to ProxyIV, which is NOT a substitute
for the reference run: it uses VIX (a true implied) for SPY and VIX x a slow
realized-vol ratio for each name (plus the five CBOE single-name indices on
FRED where they exist). The spec is explicit that realized-vol proxies inflate
results (~+7%/yr) by lagging crashes; the proxy here avoids the index-level lag
but still cannot see single-name event premia. Every report built on it is
labelled PROXY.
"""
from __future__ import annotations

import glob
import logging
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from . import rates
from .paths import IV_DIR

log = logging.getLogger(__name__)


class IVProvider(ABC):
    name = "abstract"
    is_proxy = False

    @abstractmethod
    def panel(self) -> pd.DataFrame:
        """Daily dates x tickers, decimal vols (0.25 = 25%). Must include SPY."""

    def asof(self, tickers, date, max_lag_days=7) -> pd.Series:
        p = self.panel()
        date = pd.Timestamp(date)
        i = p.index.searchsorted(date, side="right") - 1
        if i < 0:
            return pd.Series(np.nan, index=tickers)
        d = p.index[i]
        if (date - d).days > max_lag_days:
            return pd.Series(np.nan, index=tickers)
        # use last valid on/before date per ticker (small gaps)
        w = p.loc[:d].tail(max_lag_days + 1)
        out = w.reindex(columns=tickers).ffill().iloc[-1]
        return out


class CSVIV(IVProvider):
    """Long-format CSV loader with column aliases for ORATS / VolVue / IvyDB."""

    ALIASES = {
        "date": ["date", "tradeDate", "trade_date", "quote_date", "asof"],
        "ticker": ["ticker", "symbol", "underlying", "root"],
        "iv": ["iv_call_30", "iv30d", "orIv30d", "iv30", "impl_volatility", "iv_mean_30", "ivmean30"],
    }

    def __init__(self, files: list[str] | None = None, use_call=True):
        self.files = files or sorted(glob.glob(str(IV_DIR / "*.csv")))
        self.use_call = use_call
        self.name = "csv:" + ",".join(f.split("/")[-1] for f in self.files)
        self._panel = None

    def _col(self, df, key):
        for c in self.ALIASES[key]:
            if c in df.columns:
                return c
        raise KeyError(f"no {key} column in {list(df.columns)}")

    def panel(self) -> pd.DataFrame:
        if self._panel is not None:
            return self._panel
        frames = []
        for f in self.files:
            df = pd.read_csv(f)
            if "days" in df.columns:          # IvyDB surface: keep 30d calls
                df = df[df["days"] == 30]
                if "cp_flag" in df.columns:
                    df = df[df["cp_flag"].str.upper().str[0] == ("C" if self.use_call else "P")]
            d, t, v = self._col(df, "date"), self._col(df, "ticker"), self._col(df, "iv")
            x = df[[d, t, v]].copy()
            x.columns = ["date", "ticker", "iv"]
            x["date"] = pd.to_datetime(x["date"])
            x["ticker"] = x["ticker"].str.upper().str.replace(".", "-", regex=False)
            x["iv"] = pd.to_numeric(x["iv"], errors="coerce")
            frames.append(x)
        if not frames:
            raise FileNotFoundError("no IV csv files in data/iv")
        allx = pd.concat(frames)
        if allx["iv"].median() > 3:           # vol points -> decimal
            allx["iv"] = allx["iv"] / 100.0
        self._panel = allx.pivot_table(index="date", columns="ticker", values="iv").sort_index()
        return self._panel


class ProxyIV(IVProvider):
    """VIX-anchored proxy (see module docstring). Labelled PROXY everywhere."""
    is_proxy = True
    name = "PROXY(VIX x realized ratio)"

    def __init__(self, close_adj: pd.DataFrame, spy: pd.Series, window=63, ratio_bounds=(1.0, 3.0),
                 use_fred_single_names=True):
        self.close_adj, self.spy, self.window = close_adj, spy, window
        self.bounds = ratio_bounds
        self.use_fred = use_fred_single_names
        self._panel = None

    def panel(self) -> pd.DataFrame:
        if self._panel is not None:
            return self._panel
        vix = rates.vix() / 100.0
        idx = self.close_adj.index
        vix = vix.reindex(idx.union(vix.index)).ffill().reindex(idx)
        r = np.log(self.close_adj).diff()
        rs = np.log(self.spy).diff()
        rv = r.rolling(self.window, min_periods=int(self.window * 0.8)).std() * np.sqrt(252)
        rvs = rs.rolling(self.window, min_periods=int(self.window * 0.8)).std() * np.sqrt(252)
        ratio = rv.div(rvs, axis=0).clip(*self.bounds)
        iv = ratio.mul(vix, axis=0)
        iv["SPY"] = vix
        if self.use_fred:
            for t, sid in rates.SINGLE_NAME_VX.items():
                if t in iv.columns:
                    s = rates.fred(sid).dropna() / 100.0
                    s = s.reindex(idx.union(s.index)).ffill().reindex(idx)
                    iv[t] = s.where(s.notna(), iv[t])
        self._panel = iv
        return iv


def get_provider(source: str, close_adj=None, spy=None) -> IVProvider:
    if source in ("auto", "volvue", "volvue_put", "volvue_mean"):
        from . import volvue as vv
        if vv.CACHE_FILE.exists() or (source != "auto" and __import__("os").environ.get("VOLVUE_API_KEY")):
            field = {"volvue_put": "iv_put_30", "volvue_mean": "iv_mean_30"}.get(source, "iv_call_30")
            log.info("IV source: VolVue %s", field)
            return vv.VolVueIV(field)
        if source != "auto":
            raise FileNotFoundError("no VolVue cache; run scripts/fetch_volvue.py")
    if source in ("auto", "csv", "orats", "ivydb"):
        files = sorted(glob.glob(str(IV_DIR / "*.csv")))
        if files:
            log.info("IV source: %s", files)
            return CSVIV(files)
        if source != "auto":
            raise FileNotFoundError(f"iv_source={source} but no csv in {IV_DIR}")
    log.warning("No licensed IV data in %s -> using PROXY IV (not for reference reproduction)", IV_DIR)
    return ProxyIV(close_adj, spy)
