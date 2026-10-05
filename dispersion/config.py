"""Every parameter of the specification in one place."""
from __future__ import annotations

from dataclasses import dataclass, field

# --- strike targets (call deltas) as Black-Scholes z-scores ------------------
Z_DELTA = {30: -0.5244, 10: -1.2816, 1: -2.3263}   # K = S*exp(-z*s + s^2/2)
DELTA_TARGETS = {30: 0.30, 10: 0.10, 1: 0.01}

# --- universe -----------------------------------------------------------------
TOP_MCAP = 100          # S&P 500 top-100 by market cap, point in time
TOP_LIQ = 30            # top-30 of those by trailing 12m median daily $ volume
MIN_NAMES = 13          # never run fewer names than this (breadth is the engine)
LIQ_WINDOW_MONTHS = 12

# --- sizing -------------------------------------------------------------------
BOOK_FRACTION = 0.9     # notional per side = 0.9 * L * equity
LEVERAGE_LIVE_START = 3
LEVERAGE_RECOMMENDED = 5
LEVERAGE_CAP = 8
MARGIN_CAP = 0.60       # projected margin usage cap (fraction of equity)
SMALL_ACCOUNT_EQUITY = 276_000   # x L/5: below this use the rotating half
SMALL_ACCOUNT_SKIP_MULT = 2.0    # skip name if 1 contract > 2x target notional
MES_MAX_SHORT_NOTIONAL = 300_000 # MES below, ES above

# --- costs (half-spread as % of premium, x1.3 for ITM exits/rolls) ------------
HALF_SPREAD_MULT = 1.3


@dataclass(frozen=True)
class CostModel:
    name: str
    single_atm: float = 0.015
    single_30d: float = 0.03
    single_10d: float = 0.08
    single_1d: float = 0.15
    index_opt: float = 0.005     # SPY options
    hedge_bp: float = 1e-4       # 1bp of hedge turnover
    commission_open: float = 1.0 # $/contract to open (tastytrade); $0 to close
    commission_close: float = 0.0
    commission_cap: float = 10.0 # tastytrade caps commissions at $10 per leg per order
    fee_per_contract: float = 0.0  # clearing/exchange/regulatory fees, uncapped, per contract opened
    index_fee_per_contract: float = 0.0  # futures-option all-in fee per contract (ES ~$4, MES ~$2)
    mult: float = HALF_SPREAD_MULT

    def single(self, delta_bucket: int) -> float:
        return {30: self.single_30d, 10: self.single_10d, 1: self.single_1d, 50: self.single_atm}[delta_bucket] * self.mult

    def index(self) -> float:
        return self.index_opt * self.mult


RETAIL_SPY = CostModel("retail_spy")
RETAIL_ES = CostModel("retail_es", index_opt=0.003)
INSTITUTIONAL = CostModel("gs", single_atm=0.015, single_30d=0.015, single_10d=0.015, single_1d=0.015,
                          index_opt=0.015, commission_open=0.0, commission_close=0.0)
SPEC_HEADLINE = CostModel("spec_headline_0.7pct", 0, 0, 0, 0, 0, 1e-4, 0, 0, 1.0)  # + flat 0.7%/yr per 1x in engine


def commission(n_contracts: float, cm: CostModel, index: bool = False) -> float:
    """Per-leg open commission with the broker's per-leg cap (one ticket per name
    per month) plus uncapped clearing/exchange fees."""
    c = min(n_contracts * cm.commission_open, cm.commission_cap) if cm.commission_open else 0.0
    return c + n_contracts * (cm.index_fee_per_contract if index else cm.fee_per_contract)


# Most realistic retail model: per-leg half-spreads as specified, no 1.3 exit/roll
# multiplier (hold to expiry), tastytrade commissions capped per leg, clearing
# and exchange fees uncapped, ES/MES futures-option fees all-in.
REALISTIC_ES = CostModel("realistic_es", index_opt=0.003, mult=1.0, fee_per_contract=0.15, index_fee_per_contract=3.0)
REALISTIC_SPY = CostModel("realistic_spy", mult=1.0, fee_per_contract=0.15)


@dataclass(frozen=True)
class StrategyConfig:
    """Knobs that define a backtest / live variant."""
    leverage: float = 1.0
    n_names: int = TOP_LIQ
    long_delta: int = 30            # long single-name call
    short_wing_delta: int | None = 10  # short single-name call (None = outright long call)
    index_delta: int = 30           # short index call
    index_wing_delta: int | None = None  # None = naked (PM / SPAN); 1 = Reg-T credit spread
    index_instrument: str = "SPY"   # "SPY" | "ES"
    hedge_freq: str = "W"           # "W" weekly, "D" daily, None = no hedge
    rotating_half: bool = False     # small-account mode: 15 of 30, alternating
    costs: CostModel = field(default_factory=lambda: RETAIL_SPY)
    start: str = "2007-01-31"
    end: str | None = None
    cash_yield: bool = True         # FEDFUNDS on unused equity
    book_fraction: float = BOOK_FRACTION
    iv_source: str = "auto"         # "auto" | "proxy" | "orats" | "volvue" | "ivydb"
    contract_granularity: bool = False  # round to whole contracts given equity (needs equity)
    equity: float | None = None     # starting equity $ (only matters with granularity)
    # skew sensitivity: multiply the flat IV30 per leg when PRICING (strikes still from IV30)
    iv_mult_long: float = 1.0       # single-name 30d call bought
    iv_mult_wing: float = 1.0       # single-name 10d call sold
    iv_mult_index: float = 1.0      # index 30d call sold
    iv_mult_index_wing: float = 1.0 # index 1d call bought (Reg-T)
    # time-varying index call-wing discount: iv_leg = iv30 - coeff * iv_skew_30(SPY,t)/100, floored at floor*iv30
    index_skew_coeff: float = 0.0   # 0.39 = today's measured 1.7pt discount / VolVue SPY skew 4.4
    index_skew_floor: float = 0.75


REFERENCE = {
    "1x_best": dict(sharpe=1.36, maxdd=-0.029, calmar=1.07, net_cagr=(0.022, 0.031), gross_cagr=0.0297),
    "5x_naked": dict(cagr=0.177, vol=0.115, sharpe=1.54, sortino=3.4, maxdd=-0.136, calmar=1.31,
                     beta=0.37, worst_month=-0.070, y2008=0.15),
    "5x_regt": dict(sharpe=(1.46, 1.55), calmar=(1.19, 1.28)),
    "100k_half": dict(cagr=0.196, sharpe=1.44, calmar=1.12),
}
