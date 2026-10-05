"""Black-Scholes pricing with flat IV per name (backtest convention) and the
delta-target strike rule used both in the backtest and on the live chain."""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from .config import Z_DELTA


def _d1(S, K, T, sigma, r=0.0, q=0.0):
    S, K, T, sigma = (np.asarray(x, dtype=float) for x in (S, K, T, sigma))
    sT = sigma * np.sqrt(np.maximum(T, 1e-12))
    return (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / np.maximum(sT, 1e-12)


def call_price(S, K, T, sigma, r=0.0, q=0.0):
    """Vectorised; T in years. At T<=0 returns intrinsic."""
    S, K, T, sigma = (np.asarray(x, dtype=float) for x in (S, K, T, sigma))
    T = np.maximum(T, 0.0)
    d1 = _d1(S, K, T, sigma, r, q)
    d2 = d1 - sigma * np.sqrt(T)
    px = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return np.where(T <= 0, np.maximum(S - K, 0.0), px)


def call_delta(S, K, T, sigma, r=0.0, q=0.0):
    S, K, T, sigma = (np.asarray(x, dtype=float) for x in (S, K, T, sigma))
    d1 = _d1(S, K, np.maximum(T, 0.0), sigma, r, q)
    return np.where(T <= 0, (S > K).astype(float), np.exp(-q * T) * norm.cdf(d1))


def strike_for_delta(S, iv, T, delta_bucket: int, r=0.0) -> float:
    """K = S * exp(-z_d * s + s^2/2), s = IV * sqrt(T).

    Note the sign: for a call, higher delta -> lower strike, and z_30 = -0.5244
    gives K = S*exp(0.5244 s + s^2/2) > S, i.e. an OTM call. We include the rate
    drift exp(rT) so the forward-based delta matches the pricing function.
    """
    s = float(iv) * np.sqrt(T)
    z = Z_DELTA[delta_bucket]
    return float(S) * np.exp(r * T) * np.exp(-z * s + 0.5 * s * s)


def nearest_listed(target: float, strikes, direction: str = "nearest") -> float:
    """Pick the listed strike nearest the target.  If the target is beyond the
    listed chain (far wing), take the furthest listed strike."""
    ks = np.asarray(sorted(float(k) for k in strikes))
    if len(ks) == 0:
        raise ValueError("empty chain")
    if target >= ks[-1]:
        return float(ks[-1])
    if target <= ks[0]:
        return float(ks[0])
    i = int(np.argmin(np.abs(ks - target)))
    return float(ks[i])
