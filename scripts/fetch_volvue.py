"""Pull daily 30-day IVs for every ticker in the price cache + SPY from VolVue."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dispersion.data import volvue  # noqa: E402
from dispersion.data.paths import CACHE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if __name__ == "__main__":
    tick = sorted(p.stem for p in (CACHE / "prices").glob("*.parquet"))
    tick = ["SPY"] + [t for t in tick if t != "SPY"]
    df = volvue.fetch_history(tick, start="2006-01-01")
    v = df.dropna(subset=["iv_call_30"])
    print("rows", len(df), "tickers with IV", v["ticker"].nunique(), "range", v["date"].min(), v["date"].max())
