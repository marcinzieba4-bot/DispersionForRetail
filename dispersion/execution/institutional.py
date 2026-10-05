"""Prime-broker / portfolio-margin flavour: naked short SPY (or SPX) calls for
the index leg, singles identical. Any broker exposing the `Broker` interface
can be plugged in; this module only changes the index-leg construction and the
cost assumptions (~1.5% flat on premium)."""
from __future__ import annotations

from ..config import INSTITUTIONAL
from .orders import Leg, Ticket


def index_ticket(expiry: str, strike: float, contracts: int, root: str = "SPY", wing_strike: float | None = None) -> Ticket:
    from .symbols import occ
    legs = [Leg(occ(root, expiry, strike, True), "Equity Option", "Sell to Open", contracts)]
    if wing_strike:   # Reg-T pairing wing (1-delta); never nearer than ~2-delta
        legs.append(Leg(occ(root, expiry, wing_strike, True), "Equity Option", "Buy to Open", contracts))
    return Ticket(legs, "Credit", f"{root} index call {'spread' if wing_strike else 'naked'}")


COSTS = INSTITUTIONAL
