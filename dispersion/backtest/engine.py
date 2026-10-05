"""Monthly call-dispersion backtest.

Cycle (month-end or 3rd-Friday to the next, all legs same expiry):
  singles: for each of N names BUY ~30d call, SELL ~10d call, notional 0.9*L*E/N
  index:   SELL ~30d SPY/ES calls, notional 0.9*L*E; priced by BS flat IV, or
           taken from a Cboe buy-write index (real traded prices, skew included)
  put:     optional long OTM SPY put overlay, priced at iv_put_30 x put_iv_mult
  hedge:   weekly, net $-delta of every option leg at entry IV -> SPY shares
  sizing:  reset from equity every month; never scaled by past losses
Costs:   half-spread % of premium, commissions (capped per leg) + fees, 1bp hedge.
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

SPX_PER_SPY = 10.0
ES_MULT, MES_MULT = 50.0, 5.0
CBOE_K = {"BXMD": None, "BXM": 1.0, "BXY": 1.02}   # strike/S for the hedge delta (None -> model K30)


@dataclass
class Leg:
    ticker: str
    units: float      # signed share-equivalents (+ long)
    K: float
    iv: float
    S0: float
    bucket: int
    is_index: bool
    premium: float    # per unit at entry (positive)
    kind: str = "call"   # call | put | cboe
    q: float = 0.0       # dividend yield used for pricing


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


def third_fridays(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    out = []
    for (y, m), _ in pd.Series(index, index=index).groupby([index.year, index.month]):
        d = pd.Timestamp(y, m, 15)
        tf = d + pd.Timedelta(days=(4 - d.weekday()) % 7)
        i = index.searchsorted(tf, side="right") - 1      # on or before (holiday -> Thursday)
        if i >= 0 and index[i].month == m:
            out.append(index[i])
    return pd.DatetimeIndex(sorted(set(out)))


class Backtest:
    def __init__(self, cfg: StrategyConfig, uni: Universe, iv: IVProvider):
        self.cfg, self.uni, self.iv = cfg, uni, iv
        self.spy = uni.spy.dropna()
        cyc = third_fridays(self.spy.index) if cfg.cycle == "third_friday" else month_ends(self.spy.index)
        self.me = cyc[(cyc >= pd.Timestamp(cfg.start))]
        if cfg.end:
            self.me = self.me[self.me <= pd.Timestamp(cfg.end)]
        self.rf = R.fedfunds_daily(self.spy.index) if cfg.cash_yield else pd.Series(0.0, index=self.spy.index)
        self.q = None
        if cfg.dividends:
            ratio = uni.close_adj / uni.close_raw
            self.q = np.log(ratio / ratio.shift(252)).clip(0, 0.08)      # trailing 12m yield per name
            rs = uni.spy / uni.spy_raw
            self.q_spy = np.log(rs / rs.shift(252)).clip(0, 0.08)
        self.cboe = None
        if cfg.index_leg != "model":
            from ..data.cboe import short_call_pnl_panel
            self.cboe = short_call_pnl_panel(cfg.index_leg, self.spy.index)

    # ------------------------------------------------------------------ sizing
    def _round_units(self, units: float, S: float, target_notional: float, is_index: bool) -> float:
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

    def _index_iv(self, iv_s: float, t0) -> float:
        cfg = self.cfg
        iv_leg = iv_s * cfg.iv_mult_index
        if cfg.index_skew_coeff and hasattr(self.iv, "field_panel"):
            if not hasattr(self, "_spy_skew"):
                self._spy_skew = self.iv.field_panel("iv_skew_30")["SPY"].dropna()
            sk = self._spy_skew.loc[:t0]
            if len(sk):
                iv_leg = max(iv_s - cfg.index_skew_coeff * float(sk.iloc[-1]) / 100.0, cfg.index_skew_floor * iv_s)
        return iv_leg

    # ------------------------------------------------------------- one month
    def _q(self, n, t0):
        if self.q is None:
            return 0.0
        v = float(self.q_spy.loc[:t0].iloc[-1]) if n == "SPY" else float(self.q[n].loc[:t0].iloc[-1]) if n in self.q else 0.0
        return v if np.isfinite(v) else 0.0

    def _open_month(self, i: int, t0: pd.Timestamp, t1: pd.Timestamp, E: float) -> MonthRecord:
        cfg, cm = self.cfg, self.cfg.costs
        names = self.uni.tradable(t0)
        names = names[(i % 2)::2][: cfg.n_names // 2] if cfg.rotating_half else names[: cfg.n_names]
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
            n_contr = units * S0 / (100.0 * self.uni.raw_price(n, t0))
            qn = self._q(n, t0)
            sg = cfg.singles_sign
            K_l = bs.strike_for_delta(S0, iv, T, cfg.long_delta, r - qn)
            C_l = float(bs.call_price(S0, K_l, T, iv * cfg.iv_mult_long, r, qn))
            rec.legs.append(Leg(n, sg * units, K_l, iv * cfg.iv_mult_long, S0, cfg.long_delta, False, C_l, q=qn))
            prem_paid += sg * units * C_l
            costs += units * C_l * cm.single(cfg.long_delta) + config.commission(n_contr, cm)
            if cfg.short_wing_delta:
                K_s = bs.strike_for_delta(S0, iv, T, cfg.short_wing_delta, r - qn)
                C_s = float(bs.call_price(S0, K_s, T, iv * cfg.iv_mult_wing, r, qn))
                rec.legs.append(Leg(n, -sg * units, K_s, iv * cfg.iv_mult_wing, S0, cfg.short_wing_delta, False, C_s, q=qn))
                prem_paid -= sg * units * C_s
                costs += units * C_s * cm.single(cfg.short_wing_delta) + config.commission(n_contr, cm)
        deployed = sum(abs(l.units) * l.S0 for l in rec.legs if l.bucket == cfg.long_delta)
        S0 = float(self.spy.loc[t0])
        iv_s = float(ivs.get("SPY", np.nan))
        if deployed > 0 and np.isfinite(iv_s):
            units = self._round_units(deployed * cfg.index_notional_scale / S0, S0, deployed, True)
            lot = 100.0 if cfg.index_instrument == "SPY" else (MES_MULT if deployed < config.MES_MAX_SHORT_NOTIONAL else ES_MULT) * SPX_PER_SPY
            n_idx = units * S0 / (lot * self.uni.raw_price("SPY", t0))
            is_es = cfg.index_instrument == "ES"
            iv_leg = self._index_iv(iv_s, t0)
            qs = self._q("SPY", t0)
            isg = cfg.index_sign
            K_i = bs.strike_for_delta(S0, iv_s, T, cfg.index_delta, r - qs)
            if cfg.index_leg == "model":
                C_i = float(bs.call_price(S0, K_i, T, iv_leg, r, qs))
                rec.legs.append(Leg("SPY", -isg * units, K_i, iv_leg, S0, cfg.index_delta, True, C_i, q=qs))
                prem_paid -= isg * units * C_i
            else:   # real traded prices from the Cboe index; model premium only for the cost estimate
                kS = CBOE_K[cfg.index_leg]
                K_c = K_i if kS is None else kS * S0
                C_i = float(bs.call_price(S0, K_c, T, iv_s, r, qs))
                rec.legs.append(Leg(cfg.index_leg, -isg * units, K_c, iv_s, S0, cfg.index_delta, True, C_i, kind="cboe", q=qs))
            costs += units * C_i * cm.index() + config.commission(n_idx, cm, index=is_es)
            if cfg.index_wing_delta:
                K_w = bs.strike_for_delta(S0, iv_s, T, cfg.index_wing_delta, r)
                C_w = float(bs.call_price(S0, K_w, T, iv_s * cfg.iv_mult_index_wing, r))
                rec.legs.append(Leg("SPY", units, K_w, iv_s * cfg.iv_mult_index_wing, S0, cfg.index_wing_delta, True, C_w))
                prem_paid += units * C_w
                costs += units * C_w * cm.index() + config.commission(n_idx, cm, index=is_es)
            if cfg.put_delta:
                ivp = iv_s
                if hasattr(self.iv, "field_panel"):
                    try:
                        if not hasattr(self, "_spy_put_iv"):
                            self._spy_put_iv = self.iv.field_panel("iv_put_30")["SPY"].dropna()
                        v = self._spy_put_iv.loc[:t0]
                        ivp = float(v.iloc[-1]) / 100.0 if len(v) else iv_s
                    except Exception:  # noqa: BLE001
                        pass
                iv_put = ivp * cfg.put_iv_mult
                pu = deployed * cfg.put_notional_scale / S0
                K_p = bs.put_strike_for_delta(S0, ivp, T, cfg.put_delta, r)
                P_p = float(bs.put_price(S0, K_p, T, iv_put, r))
                rec.legs.append(Leg("SPY", pu, K_p, iv_put, S0, cfg.put_delta, True, P_p, kind="put"))
                prem_paid += pu * P_p
                costs += pu * P_p * cm.index() + config.commission(pu * S0 / (100.0 * self.uni.raw_price("SPY", t0)), cm)
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
            opt = [l for l in rec.legs if l.kind in ("call", "put")]
            cbo = [l for l in rec.legs if l.kind == "cboe"]
            if opt:
                units = np.array([l.units for l in opt]); K = np.array([l.K for l in opt]); ivv = np.array([l.iv for l in opt])
                qq = np.array([l.q for l in opt])
                is_put = np.array([l.kind == "put" for l in opt])
                cols = [l.ticker for l in opt]
                path = pd.concat([self.uni.close_adj, self.spy.rename("SPY")], axis=1)[cols].loc[t0:t1].ffill()
            if cbo:
                cb = self.cboe.loc[t0:t1]
                base_i, base_t = float(cb["idx"].loc[t0]), float(cb["tr"].loc[t0])
                cb_notional = sum(-l.units * l.S0 for l in cbo)   # >0 when short the call, <0 when long
            window = days[(days > t0) & (days <= t1)]
            hedge_sh, S_prev = 0.0, float(self.spy.loc[t0])
            r = float(self.rf.loc[t0])
            for k, d in enumerate(window, start=1):
                S_spy = float(self.spy.loc[d])
                cash *= 1 + float(self.rf.loc[d]) / 252.0
                cash += hedge_sh * (S_spy - S_prev)
                mark, dollar_delta = 0.0, 0.0
                T_rem = (t1 - d).days / 365.0
                if opt:
                    S = path.loc[d].to_numpy(dtype=float)
                    S = np.where(np.isfinite(S), S, K)
                    px = np.where(is_put, bs.put_price(S, K, T_rem, ivv, r, qq), bs.call_price(S, K, T_rem, ivv, r, qq))
                    mark += float(np.sum(units * px))
                    if d < t1 and k % hedge_every == 0:
                        dl = np.where(is_put, bs.put_delta(S, K, T_rem, ivv, r, qq), bs.call_delta(S, K, T_rem, ivv, r, qq))
                        dollar_delta += float(np.sum(units * dl * S))
                if cbo:
                    pnl = cb_notional * (float(cb["idx"].loc[d]) / base_i - float(cb["tr"].loc[d]) / base_t)
                    mark += pnl
                    if d < t1 and k % hedge_every == 0:
                        for l in cbo:
                            dollar_delta += l.units * float(bs.call_delta(S_spy, l.K, T_rem, l.iv * 0.9, r, l.q)) * S_spy
                if d < t1 and k % hedge_every == 0:
                    target = -dollar_delta / S_spy
                    trn = abs(target - hedge_sh) * S_spy
                    cash -= trn * cfg.costs.hedge_bp
                    turnover.loc[d] = trn
                    hedge_sh = target
                if d == t1:
                    cash += mark
                    mark, hedge_sh = 0.0, 0.0
                equity.loc[d] = cash + mark
                S_prev = S_spy
            E = float(equity.loc[t1]) if t1 in equity.index else cash
        equity.loc[self.me[0]] = cfg.equity or 1_000_000.0
        equity = equity.dropna()
        return Result(equity, months, turnover, cfg, self.iv.name, pd.Series(cov))
