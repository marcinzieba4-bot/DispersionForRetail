"""Regime signals for dispersion: implied correlation (Cboe COR1M/COR3M and a basket estimate from
VolVue single-name IVs), realized correlation, VIX level and vol-spread, all as-of daily."""
from __future__ import annotations
import numpy as np, pandas as pd
from .data.cboe import cboe_index
from .data import rates as R
from .backtest.engine import third_fridays


def _pairwise_corr(ret: pd.DataFrame) -> float:
    c = ret.corr().to_numpy()
    n = c.shape[0]
    if n < 2:
        return np.nan
    return float((np.nansum(c) - n) / (n * (n - 1)))


def basket_implied_corr(iv_s: pd.Series, iv_i: float) -> float:
    """rho such that an equal-weight basket of the names has the index IV: (s_I^2 - sum w^2 s^2) / ((sum w s)^2 - sum w^2 s^2)."""
    s = iv_s.dropna().to_numpy(dtype=float)
    if len(s) < 5 or not np.isfinite(iv_i):
        return np.nan
    w = 1.0 / len(s)
    a = np.sum((w * s) ** 2); b = np.sum(w * s) ** 2
    return float((iv_i ** 2 - a) / (b - a))


def build_signals(uni, iv, field="iv_mean_30", n_names=30, pct_window=504) -> pd.DataFrame:
    """Daily frame: cor1m, cor3m, bcor (basket implied corr), rcor21/rcor63 (realized), vix, spy_iv, sn_iv,
    vol_spread, idx_vrp, sn_vrp, plus trailing-`pct_window` percentile ranks (`*_p`). Membership = top-n liquid
    names as of the last third Friday on/before the date, so the frame is as-of and lookahead-free."""
    px = uni.close_adj; spy = uni.spy.dropna()
    days = spy.index
    panel = iv.field_panel(field) / 100.0 if hasattr(iv, "field_panel") else iv.panel()
    panel = panel.reindex(days).ffill(limit=5)
    cyc = third_fridays(days)
    ret = np.log(px).diff(); rs = np.log(spy).diff()
    rows = {}
    members = []
    for d in days:
        if d in cyc or not members:
            members = uni.tradable(d)[:n_names] if d >= cyc[0] else []
        if len(members) < 10 or d < days[70]:
            continue
        ivs = panel.loc[d, [m for m in members if m in panel.columns]]
        iv_i = panel.at[d, "SPY"] if "SPY" in panel.columns else np.nan
        rows[d] = dict(spy_iv=iv_i, sn_iv=float(ivs.mean()), bcor=basket_implied_corr(ivs, iv_i), n=int(ivs.notna().sum()))
    sig = pd.DataFrame(rows).T.sort_index()
    sig["vol_spread"] = sig.sn_iv - sig.spy_iv
    sig["iv_ratio"] = sig.spy_iv / sig.sn_iv
    # realized correlation / vols at entry dates only (expensive), then ffilled daily
    rc = {}
    for d in sig.index:
        if d not in cyc:
            continue
        mem = uni.tradable(d)[:n_names]
        r21 = ret.loc[:d, mem].tail(21); r63 = ret.loc[:d, mem].tail(63)
        rc[d] = dict(rcor21=_pairwise_corr(r21), rcor63=_pairwise_corr(r63),
                     sn_rv21=float((r21.std() * np.sqrt(252)).mean()), idx_rv21=float(rs.loc[:d].tail(21).std() * np.sqrt(252)))
    sig = sig.join(pd.DataFrame(rc).T.reindex(sig.index).ffill())
    sig["idx_vrp"] = sig.spy_iv - sig.idx_rv21
    sig["sn_vrp"] = sig.sn_iv - sig.sn_rv21
    sig["corr_prem"] = sig.bcor - sig.rcor21
    for k in ("COR1M", "COR3M"):
        c = cboe_index(k)
        sig[k.lower()] = c.reindex(sig.index.union(c.index)).ffill().reindex(sig.index) / 100.0
    vix = R.vix()
    sig["vix"] = vix.reindex(sig.index.union(vix.index)).ffill().reindex(sig.index) / 100.0
    sig["cor_prem_cboe"] = sig.cor1m - sig.rcor21
    for c in ("cor1m", "cor3m", "bcor", "vix", "spy_iv", "sn_iv", "vol_spread", "corr_prem", "cor_prem_cboe", "idx_vrp", "sn_vrp", "rcor21"):
        sig[c + "_p"] = sig[c].rolling(pct_window, min_periods=250).rank(pct=True)
    return sig.astype(float)
