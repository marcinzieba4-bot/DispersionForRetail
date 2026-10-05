import numpy as np

from dispersion import bs


def test_delta_targets_hit():
    S, iv, T = 100.0, 0.25, 30 / 365
    for d, target in [(30, 0.30), (10, 0.10), (1, 0.01)]:
        K = bs.strike_for_delta(S, iv, T, d)
        assert K > S
        assert abs(float(bs.call_delta(S, K, T, iv)) - target) < 1e-3


def test_strike_ordering():
    S, iv, T = 100.0, 0.4, 30 / 365
    k30, k10, k1 = (bs.strike_for_delta(S, iv, T, d) for d in (30, 10, 1))
    assert k30 < k10 < k1


def test_price_intrinsic_at_expiry_and_monotone():
    assert float(bs.call_price(110, 100, 0.0, 0.3)) == 10.0
    assert float(bs.call_price(90, 100, 0.0, 0.3)) == 0.0
    p = [float(bs.call_price(100, 100, 0.1, s)) for s in (0.1, 0.2, 0.3)]
    assert p[0] < p[1] < p[2]


def test_nearest_listed_and_far_wing():
    chain = [95, 100, 105, 110, 115]
    assert bs.nearest_listed(104.2, chain) == 105
    assert bs.nearest_listed(140.0, chain) == 115   # beyond chain -> furthest listed
    assert bs.nearest_listed(50.0, chain) == 95


def test_vectorised():
    S = np.array([100.0, 100.0])
    K = np.array([100.0, 120.0])
    px = bs.call_price(S, K, 0.1, 0.3)
    assert px.shape == (2,) and px[0] > px[1]
