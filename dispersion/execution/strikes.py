"""Live strike selection from a listed chain (spec section 3)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import bs


@dataclass(frozen=True)
class Pick:
    delta_bucket: int
    target: float       # continuous BS strike
    strike: float       # listed strike chosen
    model_delta: float  # BS delta of the listed strike at IV30
    model_price: float  # BS price per share at IV30


def pick_strike(S: float, iv30: float, T: float, delta_bucket: int, listed_strikes, r: float = 0.0) -> Pick:
    """Nearest listed strike to K = S*exp(-z_d*s + s^2/2). For the 1-delta wing
    a target beyond the chain takes the furthest listed strike."""
    target = bs.strike_for_delta(S, iv30, T, delta_bucket, r)
    k = bs.nearest_listed(target, listed_strikes)
    return Pick(delta_bucket, target, k,
                float(bs.call_delta(S, k, T, iv30, r)), float(bs.call_price(S, k, T, iv30, r)))


def pick_vertical(S, iv30, T, listed_strikes, long_bucket=30, short_bucket=10, r=0.0) -> tuple[Pick, Pick]:
    lo = pick_strike(S, iv30, T, long_bucket, listed_strikes, r)
    hi = pick_strike(S, iv30, T, short_bucket, listed_strikes, r)
    if hi.strike <= lo.strike:  # coarse chain: step the wing one strike up
        ks = sorted(float(k) for k in listed_strikes if float(k) > lo.strike)
        if not ks:
            raise ValueError("no strike above the long strike for the wing")
        hi = Pick(short_bucket, hi.target, ks[0], float(bs.call_delta(S, ks[0], T, iv30, r)),
                  float(bs.call_price(S, ks[0], T, iv30, r)))
    return lo, hi


def years_to(expiry, now) -> float:
    return max((np.datetime64(expiry) - np.datetime64(now)) / np.timedelta64(1, "D"), 0.5) / 365.0
