"""Download / refresh every raw input: prices, shares, splits, FRED."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dispersion.data import constituents, prices, rates  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if __name__ == "__main__":
    tick = constituents.all_tickers("2005-01-01")
    print("tickers in PIT membership since 2004:", len(tick))
    px = prices.fetch_prices(tick + ["SPY"], start="2004-01-01")
    print("tickers with price data:", len(px))
    for sid in ["FEDFUNDS", "VIXCLS", *rates.SINGLE_NAME_VX.values()]:
        s = rates.fred(sid)
        print(sid, s.dropna().index.min().date(), s.dropna().index.max().date())
    live = sorted(set(constituents.members_on("2026-09-30")) | set(constituents.members_on("2015-12-31")))
    live = [t for t in live if t in px]
    sh = prices.fetch_shares(live)
    print("shares history for", sh.shape[1], "names")
    sp = prices.fetch_splits(list(px.keys()))
    print("split rows:", len(sp))
