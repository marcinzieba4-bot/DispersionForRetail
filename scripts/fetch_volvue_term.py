"""Pull iv_call_20 / iv_call_60 / iv_call_30_perc / iv_call_30_rank for the event-premium and IV-regime filters."""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from dispersion.data import volvue
from dispersion.data.paths import CACHE
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
FIELDS = ["ticker", "date", "iv_call_20", "iv_call_60", "iv_call_30_perc", "iv_call_30_rank", "hv_cc_20"]
OUT = CACHE / "volvue_term.parquet"
if __name__ == "__main__":
    tick = sorted(p.stem for p in (CACHE / "prices").glob("*.parquet"))
    tick = ["SPY"] + [t for t in tick if t != "SPY"]
    frames = []
    for i in range(0, len(tick), 25):
        b = tick[i:i + 25]
        logging.info("term %d..%d of %d", i, i + len(b), len(tick))
        df = volvue.query(b, "2006-01-01", pd.Timestamp.today().strftime("%Y-%m-%d"), fields=FIELDS)
        if len(df):
            frames.append(df)
        pd.concat(frames, ignore_index=True).to_parquet(OUT)
    print("done", OUT, sum(len(f) for f in frames))
