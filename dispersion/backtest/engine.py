"""Monthly call-dispersion backtest.

Cycle (month-end to month-end, all legs same expiry):
  singles: for each of N names BUY ~30d call, SELL ~10d call, notional 0.9*L*E/N
  index:   SELL ~30d SPY/ES calls, notional 0.9*L*E (optionally + BUY ~1d wing)
  hedge:   weekly, net $-delta of every option leg at entry IV -> SPY shares
  sizing:  reset from equity every month; never scaled by past losses
Pricing: Black-Scholes, flat IV per name at the entry (prior month-end) IV.
Costs:   half-spread % of premium (x1.3), commissions, 1bp hedge turnover.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import bs, config
from ..config import StrategyConfig
from ..data import rates as R
from ..data.iv import IVProvider
from ..data.universe import Universe, month_ends

log = logging.getLogger(__name__)

SPX_PER_SPY = 10.0         # SPX ~ 10 x SPY
ES_MULT, MES_MULT = 50.0, 5.0


@dataclass
class Leg:
    ticker: str
    units: float      # signed share-equivalents (+ long)
    K: float
    iv: float
    S0: float
    bucket: int       # delta bucket (30/10/1) for cost lookup
    is_index: bool
    premium: float    # per unit at entry (positive)


@dataclass
class MonthRecord:
    entry: pd.Timestamp
    expiry: pd.Timestamp
    names: list[str]
    equity_in: float
    net_premium: float
    costs: float
    legs: list[Leg] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class Result:
    equity: pd.Series
    months: list[MonthRecord]
    hedge_turnover: pd.Series
    config: StrategyConfig
    iv_name: str
    coverage: pd.Series

    @property
    def is_proxy(self):
        return self.iv_name.startswith("PROXY")


class Backtest:
    def __init__(self, cfg: StrategyConfig, uni: Universe, iv: IVProvider):
        self.cfg, self.uni, self.iv = cfg, uni, iv
        self.spy = uni.spy.dropna()
        self.me = month_ends(self.spy.index)
        self.me = self.me[(self.me >= pd.Timestamp(cfg.start))]
        if cfg.end:
            self.me = self.me[self.me <= pd.Timestamp(cfg.end)]
        self.rf = R.fedfunds_daily(self.spy.index) if cfg.cash_yield else pd.Series(0.0, index=self.spy.index)

    # ------------------------------------------------------------------ sizing
    def _round_units(self, units: float, S: float, target_notional: float, is_index: bool) -> float:
        """Contract granularity (100 shares; MES/ES in SPY-equivalents)."""
        if not self.cfg.contract_granularity:
            return units
        if is_index and self.cfg.index_instrument == "ES":
            lot = (MES_MULT if abs(units) * S < config.MES_MAX_SHORT_NOTIONAL else ES_MULT) * SPX_PER_SPY
        else:
            lot = 100.0
        n = np.round(units / lot)
        if n == 0:
            if lot * S > config.SMALL_ACCOUNT_SKIP_MULT * target_notional:
                return 0.0
            n = np.sign(units)
        return float(n * lot)

    # ------------------------------------------------------------- one month
    def _open_month(self, i: int, t0: pd.Timestamp, t1: pd.Timestamp, E: float) -> MonthRecord:
        cfg, cm = self.cfg, self.cfg.costs
        names = self.uni.tradable(t0)
        if cfg.rotating_half:
            names = names[(i % 2)::2][: cfg.n_names // 2]
        else:
            names = names[: cfg.n_names]
        rec = MonthRecord(t0, t1, names, E, 0.0, 0.0)
        if len(names) < (config.MIN_NAMES if not cfg.rotating_half else config.MIN_NAMES // 2):
            log.warning("%s: only %d names -> month skipped", t0.date(), len(names))
            return rec
        T = (t1 - t0).days / 365.0
        r = float(self.rf.loc[t0])
        ivs = self.iv.asof(names + ["SPY"], t0)
        book = cfg.book_fraction * cfg.leverage * E
        per_name = book / len(names)
        prem_paid, costs = 0.0, 0.0

        for n in names:
            S0 = float(self.uni.close_adj.loc[t0, n])
            iv = float(ivs.get(n, np.nan))
            if not np.isfinite(S0) or not np.isfinite(iv) or iv <= 0:
                rec.skipped.append(n)
                continue
            units = self._round_units(per_name / S0, S0, per_name, False)
            if units == 0:
                rec.skipped.append(n)
                continue
            n_contr = units * S0 / (100.0 * self.uni.raw_price(n, t0))   # real contract count
            K_l = bs.strike_for_delta(S0, iv, T, cfg.long_delta, r)
            C_l = float(bs.call_price(S0, K_l, T, iv, r))
            rec.legs.append(Leg(n, units, K_l, iv, S0, cfg.long_delta, False, C_l))
            prem_paid += units * C_l
            costs += units * C_l * cm.single(cfg.long_delta) + config.commission(n_contr, cm)
            if cfg.short_wing_delta:
                K_s = bs.strike_for_delta(S0, iv, T, cfg.short_wing_delta, r)
                C_s = float(bs.call_price(S0, K_s, T, iv, r))
                rec.legs.append(Leg(n, -units, K_s, iv, S0, cfg.short_wing_delta, False, C_s))
                prem_paid -= units * C_s
                costs += units * C_s * cm.single(cfg.short_wing_delta) + config.commission(n_contr, cm)

        # index leg, notional matched to what was actually deployed on singles
        deployed = sum(abs(l.units) * l.S0 for l in rec.legs if l.units > 0)
        S0 = float(self.spy.loc[t0])
        iv_s = float(ivs.get("SPY", np.nan))
        if deployed > 0 and np.isfinite(iv_s):
            units = self._round_units(deployed / S0, S0, deployed, True)
            lot = 100.0 if cfg.index_instrument == "SPY" else (MES_MULT if deployed < config.MES_MAX_SHORT_NOTIONAL else ES_MULT) * SPX_PER_SPY
            n_idx = units * S0 / (lot * self.uni.raw_price("SPY", t0))
            K_i = bs.strike_for_delta(S0, iv_s, T, cfg.index_delta, r)
            C_i = float(bs.call_price(S0, K_i, T, iv_s, r))
            rec.legs.append(Leg("SPY", -units, K_i, iv_s, S0, cfg.index_delta, True, C_i))
            prem_paid -= units * C_i
            costs += units * C_i * cm.index() + config.commission(n_idx, cm, index=cfg.index_instrument == "ES")
            if cfg.index_wing_delta:
                K_w = bs.strike_for_delta(S0, iv_s, T, cfg.index_wing_delta, r)
                C_w = float(bs.call_price(S0, K_w, T, iv_s, r))
                rec.legs.append(Leg("SPY", units, K_w, iv_s, S0, cfg.index_wing_delta, True, C_w))
                prem_paid += units * C_w
                costs += units * C_w * cm.index() + config.commission(n_idx, cm, index=cfg.index_instrument == "ES")
        if cm.name.startswith("spec_headline"):
            costs += 0.007 / 12 * book
        rec.net_premium, rec.costs = prem_paid, costs
        return rec

    # ----------------------------------------------------------- simulation
    def run(self) -> Result:
        cfg = self.cfg
        days = self.spy.index
        equity = pd.Series(np.nan, index=days)
        turnover = pd.Series(0.0, index=days)
        E = cfg.equity or 1_000_000.0
        months: list[MonthRecord] = []
        cov = {}
        hedge_every = {"W": 5, "D": 1, None: 10 ** 9}[cfg.hedge_freq]

        for i in range(len(self.me) - 1):
            t0, t1 = self.me[i], self.me[i + 1]
            rec = self._open_month(i, t0, t1, E)
            months.append(rec)
            cov[t0] = len(rec.names) - len(rec.skipped)
            cash = E - rec.net_premium - rec.costs
            legs = rec.legs
            if legs:
                units = np.array([l.units for l in legs])
                K = np.array([l.K for l in legs])
                ivv = np.array([l.iv for l in legs])
                cols = [l.ticker for l in legs]
                path = pd.concat([self.uni.close_adj, self.spy.rename("SPY")], axis=1)[cols]
                path = path.loc[t0:t1].ffill()
            window = days[(days > t0) & (days <= t1)]
            hedge_sh = 0.0
            S_prev = float(self.spy.loc[t0])
            T_total = (t1 - t0).days / 365.0
            r = float(self.rf.loc[t0])
            for k, d in enumerate(window, start=1):
                S_spy = float(self.spy.loc[d])
                cash *= 1 + float(self.rf.loc[d]) / 252.0
                cash += hedge_sh * (S_spy - S_prev)          # hedge P&L (futures-like)
                mark = 0.0
                if legs:
                    T_rem = (t1 - d).days / 365.0
                    S = path.loc[d].to_numpy(dtype=float)
                    S = np.where(np.isfinite(S), S, K)       # dead data -> treat as flat
                    px = bs.call_price(S, K, T_rem, ivv, r)
                    mark = float(np.sum(units * px))
                    if d < t1 and k % hedge_every == 0:
                        dl = bs.call_delta(S, K, T_rem, ivv, r)
                        dollar_delta = float(np.sum(units * dl * S))
                        target = -dollar_delta / S_spy
                        trn = abs(target - hedge_sh) * S_spy
                        cash -= trn * cfg.costs.hedge_bp
                        turnover.loc[d] = trn
                        hedge_sh = target
                if d == t1:                                   # settle expiry & hedge
                    cash += mark
                    mark = 0.0
                    hedge_sh = 0.0
                equity.loc[d] = cash + mark
                S_prev = S_spy
            E = float(equity.loc[t1]) if t1 in equity.index else cash
        equity.loc[self.me[0]] = cfg.equity or 1_000_000.0
        equity = equity.dropna()
        return Result(equity, months, turnover, cfg, self.iv.name, pd.Series(cov))
