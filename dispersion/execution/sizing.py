"""Position sizing, small-account rotation, margin cap and safety rails
(spec sections 2, 3, 5, 10)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config


@dataclass
class SizingPlan:
    equity: float
    leverage: float
    names: list[str]                       # names to trade this month (ranked)
    per_name_notional: float
    index_notional: float
    contracts: dict[str, int] = field(default_factory=dict)   # per-name verticals
    skipped: dict[str, str] = field(default_factory=dict)
    index_contracts: int = 0
    index_instrument: str = "SPY"          # SPY | MES | ES
    notes: list[str] = field(default_factory=list)

    @property
    def deployed_notional(self):
        return sum(self.contracts[n] * 100 * self._px[n] for n in self.contracts)


def is_small_account(equity: float, leverage: float) -> bool:
    return equity < config.SMALL_ACCOUNT_EQUITY * leverage / 5.0


def choose_names(ranked_top30: list[str], equity: float, leverage: float, month_index: int,
                 n_names: int = config.TOP_LIQ) -> tuple[list[str], bool]:
    """Full top-30, or the rotating half (odd ranks one month, even the next)."""
    small = is_small_account(equity, leverage)
    if small:
        return ranked_top30[(month_index % 2)::2][: n_names // 2], True
    return ranked_top30[:n_names], False


def build_plan(equity: float, leverage: float, ranked_top30: list[str], prices: dict[str, float],
               spy_price: float, month_index: int, index_mode: str = "ES") -> SizingPlan:
    """index_mode: 'SPY' (naked PM or Reg-T spread) | 'ES' (SPAN futures options)."""
    names, small = choose_names(ranked_top30, equity, leverage, month_index)
    book = config.BOOK_FRACTION * leverage * equity
    per_name = book / len(names)
    plan = SizingPlan(equity, leverage, names, per_name, book)
    plan._px = prices
    for n in names:
        px = prices.get(n)
        if not px:
            plan.skipped[n] = "no price"
            continue
        one = 100 * px
        k = round(per_name / one)
        if k == 0:
            if one > config.SMALL_ACCOUNT_SKIP_MULT * per_name:
                plan.skipped[n] = f"1 contract (${one:,.0f}) > 2x target (${per_name:,.0f})"
                continue
            k = 1
        plan.contracts[n] = int(k)
    if len(plan.contracts) < (config.MIN_NAMES // 2 if small else config.MIN_NAMES):
        plan.notes.append(f"only {len(plan.contracts)} names sized; breadth floor is {config.MIN_NAMES}")
    deployed = sum(plan.contracts[n] * 100 * prices[n] for n in plan.contracts)
    plan.index_notional = deployed   # notional-matched to what was actually bought
    if index_mode == "SPY":
        plan.index_instrument = "SPY"
        plan.index_contracts = int(round(deployed / (100 * spy_price)))
    else:
        spx = spy_price * 10.0
        if deployed < config.MES_MAX_SHORT_NOTIONAL:
            plan.index_instrument, mult = "MES", 5.0
        else:
            plan.index_instrument, mult = "ES", 50.0
        plan.index_contracts = int(round(deployed / (mult * spx)))
    if small:
        plan.notes.append("small-account mode: rotating half of top-30")
    return plan


@dataclass
class Rails:
    """Safety rails evaluated before every roll / after every margin dry-run."""
    margin_cap: float = config.MARGIN_CAP
    floor_buffer: float = 0.10     # equity must stay above maintenance floor * (1+buffer)

    def leverage_after_margin_check(self, leverage: float, projected_margin: float, equity: float) -> tuple[float, str]:
        usage = projected_margin / equity if equity > 0 else 1.0
        if usage > self.margin_cap:
            return max(leverage - 1, 1), f"margin usage {usage:.0%} > {self.margin_cap:.0%}: cut L {leverage}->{max(leverage - 1, 1)}"
        return leverage, f"margin usage {usage:.0%} ok"

    def must_flatten_short_leg(self, equity: float, maintenance_floor: float) -> bool:
        return equity < maintenance_floor * (1 + self.floor_buffer)

    @staticmethod
    def never_scale_up_after_loss(prev_leverage: float, new_leverage: float, prev_equity: float, equity: float) -> float:
        """No recovery sizing: leverage may not rise in a month that follows a loss."""
        if equity < prev_equity and new_leverage > prev_leverage:
            return prev_leverage
        return new_leverage
