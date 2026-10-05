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
# option legs each Cboe index holds, for the hedge delta: (kind, strike/S or delta-bucket, sign per unit of index)
CBOE_LEGS = {
    "BXMD": [("call", ("d", 30), -1)], "BXM": [("call", 1.0, -1)], "BXY": [("call", 1.02, -1)],
    "PUT": [("put", 1.0, -1)], "WPUT": [("put", 1.0, -1)], "PUTD": [("put", 1.0, -1)],
    "PPUT": [("put", 0.95, +1)],
    "RXM": [("call", ("d", 25), +1), ("put", ("dp", 25), -1)],
    "CNDR": [("put", ("dp", 20), -1), ("call", ("d", 20), -1), ("put", ("dp", 5), +1), ("call", ("d", 5), +1)],
}


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
    beta: float = 1.0    # hedge weight (beta-weighted hedge)


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
        self.term = None
        if cfg.term_filter != "none" or cfg.perc_filter is not None:
            from ..data.volvue import term_panels
            self.term = term_panels()
            self.iv30_panel = iv.panel() * 100.0
        self.beta = None
        if cfg.beta_hedge:
            r_ = np.log(uni.close_adj).diff(); rs = np.log(uni.spy).diff()
            cov = r_.rolling(252, min_periods=120).cov(rs); var = rs.rolling(252, min_periods=120).var()
            self.beta = cov.div(var, axis=0).clip(0.2, 3.0)
        self.cboe = {}
        from ..data.cboe import short_call_pnl_panel
        for name in {cfg.index_leg} | {x[0] for x in cfg.index_legs}:
            if name != "model":
                self.cboe[name] = short_call_pnl_panel(name, self.spy.index)

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

    @staticmethod
    def _cboe_strike(kk, S0, iv, T, rq):
        if isinstance(kk, tuple):
            return bs.put_strike_for_delta(S0, iv, T, kk[1], rq) if kk[0] == "dp" else bs.strike_for_delta(S0, iv, T, kk[1], rq)
        return kk * S0

    def _cboe_delta(self, name, units, S, T_rem, iv, r, q):
        """Model delta of the index's option legs per unit of index (negative units = short the index position)."""
        d = 0.0
        for kind, kk, sg_ in CBOE_LEGS[name]:
            Kk = self._cboe_strike(kk, S, iv, max(T_rem, 1e-6), r - q)   # strike re-derived at S0 below
            dl = float(bs.put_delta(S, Kk, T_rem, iv, r, q)) if kind == "put" else float(bs.call_delta(S, Kk, T_rem, iv, r, q))
            d += sg_ * dl
        return -units * d * S   # sign: cboe leg units are -sign*units (short index position => +units short)

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
        if cfg.select == "mcap":
            mc_all = self.uni.mcap.loc[:t0].iloc[-1].reindex(names).dropna()
            names = mc_all.sort_values(ascending=False).index.tolist()
        names = names[(i % 2)::2][: cfg.n_names // 2] if cfg.rotating_half else names[: cfg.n_names]
        rec = MonthRecord(t0, t1, names, E, 0.0, 0.0)
        if len(names) < (config.MIN_NAMES if not cfg.rotating_half else config.MIN_NAMES // 2):
            log.warning("%s: only %d names -> month skipped", t0.date(), len(names))
            return rec
        T = (t1 - t0).days / 365.0
        r = float(self.rf.loc[t0])
        ivs = self.iv.asof(names + ["SPY"], t0)
        book = cfg.book_fraction * cfg.leverage * ((cfg.equity or 1_000_000.0) if cfg.fixed_notional else E)
        per_name = book / len(names)
        weights = {n: 1.0 / len(names) for n in names}
        if cfg.weighting in ("mcap", "sqrt_mcap"):
            mc = self.uni.mcap.loc[:t0].iloc[-1].reindex(names)
            mc = mc.where(mc > 0).fillna(mc.median())
            w = np.sqrt(mc) if cfg.weighting == "sqrt_mcap" else mc
            w = w / w.sum()
            for _ in range(10):   # iterative cap
                over = w > cfg.weight_cap
                if not over.any():
                    break
                excess = (w[over] - cfg.weight_cap).sum(); w[over] = cfg.weight_cap
                under = ~over
                if under.any():
                    w[under] += excess * w[under] / w[under].sum()
            weights = w.to_dict()
        prem_paid, costs = 0.0, 0.0
        sign_of: dict[str, int] = {n: cfg.singles_sign for n in names}
        if self.term is not None:
            def asof(panel, n):
                if n not in panel.columns:
                    return np.nan
                v = panel[n].loc[:t0].dropna()
                return float(v.iloc[-1]) if len(v) and (t0 - v.index[-1]).days <= 7 else np.nan
            for n in list(names):
                ratio = asof(self.iv30_panel, n) / asof(self.term["iv_call_60"], n)
                perc = asof(self.term["iv_call_30_perc"], n)
                event = np.isfinite(ratio) and ratio > cfg.term_thresh
                if cfg.term_filter == "exclude_event" and event:
                    sign_of[n] = 0
                elif cfg.term_filter == "event_only" and not event:
                    sign_of[n] = 0
                elif cfg.term_filter == "event_short" and event:
                    sign_of[n] = -cfg.singles_sign
                if cfg.perc_filter is not None and np.isfinite(perc) and perc > cfg.perc_filter and sign_of[n] == cfg.singles_sign:
                    sign_of[n] = 0
            names = [n for n in names if sign_of[n] != 0]   # per-name notional stays book/N of the full list
        for n in (names if cfg.singles_scale > 0 else []):
            S0 = float(self.uni.close_adj.loc[t0, n])
            iv = float(ivs.get(n, np.nan))
            if not np.isfinite(S0) or not np.isfinite(iv) or iv <= 0:
                rec.skipped.append(n)
                continue
            per_name = book * weights.get(n, 1.0 / max(len(names), 1))
            units = self._round_units(per_name / S0, S0, per_name, False)
            if units == 0:
                rec.skipped.append(n)
                continue
            n_contr = units * S0 / (100.0 * self.uni.raw_price(n, t0))
            qn = self._q(n, t0)
            sg = sign_of[n]
            if cfg.singles_structure in ("wings", "put"):
                # long call at long_delta (call IV) + long put at single_put_delta (put IV x skew mult)
                ivput = iv
                if hasattr(self.iv, "field_panel"):
                    if not hasattr(self, "_put_iv_panel"):
                        self._put_iv_panel = self.iv.field_panel("iv_put_30")
                    if n in self._put_iv_panel.columns:
                        vp = self._put_iv_panel[n].loc[:t0].dropna()
                        if len(vp) and (t0 - vp.index[-1]).days <= 7:
                            ivput = float(vp.iloc[-1]) / 100.0
                C_l = 0.0
                if cfg.singles_structure == "wings":
                    K_l = bs.strike_for_delta(S0, iv, T, cfg.long_delta, r - qn)
                    C_l = float(bs.call_price(S0, K_l, T, iv * cfg.iv_mult_long, r, qn))
                    rec.legs.append(Leg(n, sg * units, K_l, iv * cfg.iv_mult_long, S0, cfg.long_delta, False, C_l, q=qn))
                    costs += units * C_l * cm.single(cfg.long_delta) + config.commission(n_contr, cm)
                else:   # puts only: keep a zero-size call marker so the index leg sizes off the per-name notional
                    rec.legs.append(Leg(n, 0.0, S0, iv, S0, cfg.long_delta, False, 0.0, q=qn))
                K_p = bs.put_strike_for_delta(S0, ivput, T, cfg.single_put_delta, r - qn)
                P_l = float(bs.put_price(S0, K_p, T, ivput * cfg.single_put_iv_mult, r, qn))
                rec.legs.append(Leg(n, sg * units, K_p, ivput * cfg.single_put_iv_mult, S0, cfg.single_put_delta, False, P_l, kind="put", q=qn))
                prem_paid += sg * units * (C_l + P_l)
                costs += units * P_l * cm.single(30) + config.commission(n_contr, cm)
                continue
            if cfg.singles_structure == "straddle":
                ivs_ = iv * cfg.straddle_iv_mult
                Kx = S0 * np.exp((r - qn) * T)
                Cc = float(bs.call_price(S0, Kx, T, ivs_, r, qn)); Pp = float(bs.put_price(S0, Kx, T, ivs_, r, qn))
                rec.legs.append(Leg(n, sg * units, Kx, ivs_, S0, 50, False, Cc, q=qn))
                rec.legs.append(Leg(n, sg * units, Kx, ivs_, S0, 50, False, Pp, kind="put", q=qn))
                prem_paid += sg * units * (Cc + Pp)
                costs += units * (Cc + Pp) * cm.single(50) + 2 * config.commission(n_contr, cm)
                continue
            K_l = bs.strike_for_delta(S0, iv, T, cfg.long_delta, r - qn)
            C_l = float(bs.call_price(S0, K_l, T, iv * cfg.iv_mult_long, r, qn))
            bn = float(self.beta[n].loc[:t0].dropna().iloc[-1]) if self.beta is not None and n in self.beta and len(self.beta[n].loc[:t0].dropna()) else 1.0
            rec.legs.append(Leg(n, sg * units, K_l, iv * cfg.iv_mult_long, S0, cfg.long_delta, False, C_l, q=qn, beta=bn))
            prem_paid += sg * units * C_l
            costs += units * C_l * cm.single(cfg.long_delta) + config.commission(n_contr, cm)
            if cfg.short_wing_delta:
                K_s = bs.strike_for_delta(S0, iv, T, cfg.short_wing_delta, r - qn)
                C_s = float(bs.call_price(S0, K_s, T, iv * cfg.iv_mult_wing, r, qn))
                rec.legs.append(Leg(n, -sg * units, K_s, iv * cfg.iv_mult_wing, S0, cfg.short_wing_delta, False, C_s, q=qn, beta=bn))
                prem_paid -= sg * units * C_s
                costs += units * C_s * cm.single(cfg.short_wing_delta) + config.commission(n_contr, cm)
        if cfg.singles_scale > 0:   # one long-side leg per name in every structure
            if cfg.singles_structure == "put":
                deployed = sum(abs(l.units) * l.S0 for l in rec.legs if not l.is_index and l.kind == "put")
            else:
                deployed = sum(abs(l.units) * l.S0 for l in rec.legs if not l.is_index and l.kind == "call"
                               and (cfg.singles_structure != "vertical" or l.bucket == cfg.long_delta))
        else:
            deployed = book
        S0 = float(self.spy.loc[t0])
        iv_s = float(ivs.get("SPY", np.nan))
        if deployed > 0 and np.isfinite(iv_s):
            vscale = 1.0
            if cfg.index_notional_mode == "vega":
                sv = np.nanmedian([l.iv for l in rec.legs if not l.is_index]) if any(not l.is_index for l in rec.legs) else iv_s
                vscale = float(sv / iv_s) if np.isfinite(sv) and iv_s > 0 else 1.0
            units = self._round_units(deployed * cfg.index_notional_scale * vscale / S0, S0, deployed, True)
            lot = 100.0 if cfg.index_instrument == "SPY" else (MES_MULT if deployed < config.MES_MAX_SHORT_NOTIONAL else ES_MULT) * SPX_PER_SPY
            n_idx = units * S0 / (lot * self.uni.raw_price("SPY", t0))
            is_es = cfg.index_instrument == "ES"
            iv_leg = self._index_iv(iv_s, t0)
            qs = self._q("SPY", t0)
            isg = cfg.index_sign
            K_i = bs.strike_for_delta(S0, iv_s, T, cfg.index_delta, r - qs)
            if cfg.index_legs:
                for name, sign, scale in cfg.index_legs:
                    # cost estimate: model premium of the index's option legs
                    C_c = 0.0
                    for kind, kk, sg_ in CBOE_LEGS[name]:
                        Kk = self._cboe_strike(kk, S0, iv_s, T, r - qs)
                        C_c += float(bs.put_price(S0, Kk, T, iv_s, r, qs)) if kind == "put" else float(bs.call_price(S0, Kk, T, iv_s, r, qs))
                        costs += units * scale * (float(bs.put_price(S0, Kk, T, iv_s, r, qs)) if kind == "put" else float(bs.call_price(S0, Kk, T, iv_s, r, qs))) * cm.index() + config.commission(n_idx * scale, cm, index=is_es)
                    rec.legs.append(Leg(name, -sign * units * scale, K_i, iv_s, S0, cfg.index_delta, True, C_c, kind="cboe", q=qs))
            elif cfg.index_leg == "model":
                C_i = float(bs.call_price(S0, K_i, T, iv_leg, r, qs))
                rec.legs.append(Leg("SPY", -isg * units, K_i, iv_leg, S0, cfg.index_delta, True, C_i, q=qs))
                prem_paid -= isg * units * C_i
            else:   # real traded prices from the Cboe index; model premium only for the cost estimate
                kS = CBOE_K[cfg.index_leg]
                K_c = K_i if kS is None else kS * S0
                C_i = float(bs.call_price(S0, K_c, T, iv_s, r, qs))
                rec.legs.append(Leg(cfg.index_leg, -isg * units, K_c, iv_s, S0, cfg.index_delta, True, C_i, kind="cboe", q=qs))
            if not cfg.index_legs:
                costs += units * C_i * cm.index() + config.commission(n_idx, cm, index=is_es)
            if cfg.index_wing_delta:
                K_w = bs.strike_for_delta(S0, iv_s, T, cfg.index_wing_delta, r)
                C_w = float(bs.call_price(S0, K_w, T, iv_s * cfg.iv_mult_index_wing, r))
                rec.legs.append(Leg("SPY", units, K_w, iv_s * cfg.iv_mult_index_wing, S0, cfg.index_wing_delta, True, C_w))
                prem_paid += units * C_w
                costs += units * C_w * cm.index() + config.commission(n_idx, cm, index=is_es)
            if cfg.short_put_delta:
                if not hasattr(self, "_spy_put_iv"):
                    self._spy_put_iv = self.iv.field_panel("iv_put_30")["SPY"].dropna()
                v = self._spy_put_iv.loc[:t0]
                ivp0 = float(v.iloc[-1]) / 100.0 if len(v) else iv_s
                pu = deployed * cfg.put_notional_scale / S0
                K_sp = bs.put_strike_for_delta(S0, ivp0, T, cfg.short_put_delta, r - qs)
                P_sp = float(bs.put_price(S0, K_sp, T, ivp0 * cfg.short_put_iv_mult, r, qs))
                rec.legs.append(Leg("SPY", -pu, K_sp, ivp0 * cfg.short_put_iv_mult, S0, cfg.short_put_delta, True, P_sp, kind="put", q=qs))
                prem_paid -= pu * P_sp
                costs += pu * P_sp * cm.index() + config.commission(pu * S0 / (100.0 * self.uni.raw_price("SPY", t0)), cm)
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
                K_p = bs.put_strike_for_delta(S0, ivp, T, cfg.put_delta, r - qs)
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
                qq = np.array([l.q for l in opt]); bb = np.array([l.beta for l in opt])
                is_put = np.array([l.kind == "put" for l in opt])
                is_idx = np.array([l.is_index for l in opt])
                cols = [l.ticker for l in opt]
                path = pd.concat([self.uni.close_adj, self.spy.rename("SPY")], axis=1)[cols].loc[t0:t1].ffill()
                single_names = sorted({l.ticker for l in opt if not l.is_index})
                name_idx = {n: np.array([c == n for c in cols]) for n in single_names}
                name_px = self.uni.close_adj[single_names].loc[t0:t1].ffill() if single_names else None
            stock_h: dict[str, float] = {}
            S_name_prev = {n: float(name_px[n].loc[t0]) for n in single_names} if opt and single_names else {}
            if cbo:
                cbs = [(self.cboe[l.ticker].loc[t0:t1], -l.units * l.S0) for l in cbo]   # notional >0 when short the call
                cbs = [(c, float(c["idx"].loc[t0]), float(c["tr"].loc[t0]), n) for c, n in cbs]
                T0 = (t1 - t0).days / 365.0
                r0 = float(self.rf.loc[t0])
                cb_strikes = [[self._cboe_strike(kk, l.S0, l.iv, T0, r0 - l.q) for kind, kk, sg_ in CBOE_LEGS[l.ticker]] for l in cbo]
            window = days[(days > t0) & (days <= t1)]
            hedge_sh, S_prev = 0.0, float(self.spy.loc[t0])
            r = float(self.rf.loc[t0])
            for k, d in enumerate(window, start=1):
                S_spy = float(self.spy.loc[d])
                cash *= 1 + float(self.rf.loc[d]) / 252.0
                cash += hedge_sh * (S_spy - S_prev)
                if S_name_prev:
                    row = name_px.loc[d]
                    for n in single_names:
                        Sn = float(row[n])
                        if np.isfinite(Sn):
                            if n in stock_h:
                                cash += stock_h[n] * (Sn - S_name_prev[n])
                            S_name_prev[n] = Sn          # reference price advances every day for every name
                mark, dollar_delta = 0.0, 0.0
                T_rem = (t1 - d).days / 365.0
                if opt:
                    S = path.loc[d].to_numpy(dtype=float)
                    S = np.where(np.isfinite(S), S, K)
                    px = np.where(is_put, bs.put_price(S, K, T_rem, ivv, r, qq), bs.call_price(S, K, T_rem, ivv, r, qq))
                    mark += float(np.sum(units * px))
                    if d < t1 and k % hedge_every == 0:
                        dl = np.where(is_put, bs.put_delta(S, K, T_rem, ivv, r, qq), bs.call_delta(S, K, T_rem, ivv, r, qq))
                        dd_leg = units * dl * S
                        scope = cfg.hedge_scope
                        if scope in ("split", "singles"):
                            # per-name stock hedge of each single's own delta
                            for n in single_names:
                                m_ = name_idx[n]; Sn = float(S[m_][0]) if m_.any() else np.nan
                                if not np.isfinite(Sn) or Sn <= 0:
                                    continue
                                tgt = -float(np.sum(dd_leg[m_])) / Sn
                                trn = abs(tgt - stock_h.get(n, 0.0)) * Sn
                                cash -= trn * cfg.costs.hedge_bp; turnover.loc[d] += trn
                                stock_h[n] = tgt
                            dollar_delta += float(np.sum(dd_leg[is_idx] * bb[is_idx])) if scope == "split" else 0.0
                        elif scope == "index":
                            dollar_delta += float(np.sum(dd_leg[is_idx] * bb[is_idx]))
                        else:
                            dollar_delta += float(np.sum(dd_leg * bb))
                if cbo:
                    for c, bi, bt, n in cbs:
                        mark += n * (float(c["idx"].loc[d]) / bi - float(c["tr"].loc[d]) / bt)
                    if d < t1 and k % hedge_every == 0 and cfg.hedge_scope != "singles":
                        for l, ks in zip(cbo, cb_strikes):
                            dd = 0.0
                            for (kind, kk, sg_), Kk in zip(CBOE_LEGS[l.ticker], ks):
                                dl = float(bs.put_delta(S_spy, Kk, T_rem, l.iv, r, l.q)) if kind == "put" else float(bs.call_delta(S_spy, Kk, T_rem, l.iv * 0.9, r, l.q))
                                dd += sg_ * dl
                            # l.units = -sign*units: a short index position (sign=-1 -> units>0) holds the index's legs short
                            dollar_delta += (-l.units) * dd * S_spy
                if d < t1 and k % hedge_every == 0:
                    target = -dollar_delta / S_spy
                    trn = abs(target - hedge_sh) * S_spy
                    cash -= trn * cfg.costs.hedge_bp
                    turnover.loc[d] += trn
                    hedge_sh = target
                if d == t1:
                    cash += mark
                    if stock_h:   # flatten per-name stock hedges
                        row = name_px.loc[d]
                        for n, h in stock_h.items():
                            Sn = float(row[n])
                            if np.isfinite(Sn):
                                cash -= abs(h) * Sn * cfg.costs.hedge_bp
                        stock_h = {}
                    mark, hedge_sh = 0.0, 0.0
                equity.loc[d] = cash + mark
                S_prev = S_spy
            E = float(equity.loc[t1]) if t1 in equity.index else cash
        equity.loc[self.me[0]] = cfg.equity or 1_000_000.0
        equity = equity.dropna()
        return Result(equity, months, turnover, cfg, self.iv.name, pd.Series(cov))
