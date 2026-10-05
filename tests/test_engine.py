"""Engine test on synthetic data: a 3-name universe with lognormal paths. The
check is accounting consistency (no leaks), not performance."""
import numpy as np
import pandas as pd

from dispersion import config
from dispersion.backtest import metrics
from dispersion.backtest.engine import Backtest
from dispersion.data.iv import IVProvider
from dispersion.data.universe import Universe


class FlatIV(IVProvider):
    name = "flat"

    def __init__(self, cols, idx):
        self._p = pd.DataFrame(0.30, index=idx, columns=cols)
        self._p["SPY"] = 0.18

    def panel(self):
        return self._p


def make_universe(seed=0, days=800):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=days)
    cols = [f"N{i}" for i in range(16)]
    r = rng.normal(0, 0.30 / np.sqrt(252), (days, len(cols)))
    px = pd.DataFrame(100 * np.exp(np.cumsum(r, 0)), index=idx, columns=cols)
    spy = pd.Series(300 * np.exp(np.cumsum(rng.normal(0, 0.18 / np.sqrt(252), days))), index=idx)
    vol = pd.DataFrame(1e6, index=idx, columns=cols)
    sh = pd.DataFrame(1e9, index=idx, columns=cols)
    u = Universe(px, px, vol, sh, spy, members_fn=lambda d: cols, liq_window=60)
    return u, FlatIV(cols, idx)


def test_engine_runs_and_accounts(monkeypatch):
    u, iv = make_universe()
    monkeypatch.setattr("dispersion.backtest.engine.R.fedfunds_daily", lambda idx: pd.Series(0.0, index=idx))
    cfg = config.StrategyConfig(leverage=1, start="2020-03-31", end="2021-12-31", cash_yield=False, n_names=16)
    res = Backtest(cfg, u, iv).run()
    assert len(res.months) >= 18
    m = res.months[0]
    assert len(m.legs) == 2 * 16 + 1                 # 16 verticals + naked index
    long_notional = sum(l.units * l.S0 for l in m.legs if l.units > 0 and not l.is_index)
    idx_notional = -sum(l.units * l.S0 for l in m.legs if l.is_index)
    assert abs(long_notional - 0.9 * m.equity_in) < 1e-6
    assert abs(idx_notional - long_notional) < 1e-6   # notional matched
    assert res.equity.notna().all() and (res.equity > 0).all()
    st = metrics.summary(res.equity, u.spy)
    assert np.isfinite(st["sharpe"]) and st["maxdd"] <= 0


def test_leverage_scales_pnl(monkeypatch):
    u, iv = make_universe()
    monkeypatch.setattr("dispersion.backtest.engine.R.fedfunds_daily", lambda idx: pd.Series(0.0, index=idx))
    # commissions are capped per leg (not linear in L), so test with spread-only costs
    nocomm = config.CostModel("nocomm", commission_open=0.0)
    kw = dict(start="2020-03-31", end="2020-12-31", cash_yield=False, n_names=16, hedge_freq=None, costs=nocomm)
    r1 = Backtest(config.StrategyConfig(leverage=1, **kw), u, iv).run()
    r3 = Backtest(config.StrategyConfig(leverage=3, **kw), u, iv).run()
    m1 = metrics.monthly_returns(r1.equity)
    m3 = metrics.monthly_returns(r3.equity)
    assert np.allclose(m3.values, 3 * m1.values, rtol=1e-6, atol=1e-9)


def test_regt_wing_and_rotating_half(monkeypatch):
    u, iv = make_universe()
    monkeypatch.setattr("dispersion.backtest.engine.R.fedfunds_daily", lambda idx: pd.Series(0.0, index=idx))
    cfg = config.StrategyConfig(leverage=5, start="2020-03-31", end="2020-09-30", index_wing_delta=1,
                                rotating_half=True, n_names=16, cash_yield=False)
    res = Backtest(cfg, u, iv).run()
    m0, m1 = res.months[0], res.months[1]
    # odd liquidity ranks one month, even ranks the next (rotation is by rank)
    assert m0.names == u.tradable(m0.entry)[0::2][:8]
    assert m1.names == u.tradable(m1.entry)[1::2][:8]
    assert sum(1 for l in m0.legs if l.is_index) == 2
    wing = [l for l in m0.legs if l.is_index and l.units > 0][0]
    assert wing.bucket == 1 and wing.K > [l for l in m0.legs if l.is_index and l.units < 0][0].K
