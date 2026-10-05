from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CACHE = DATA / "cache"
IV_DIR = DATA / "iv"
RESULTS = ROOT / "results"
for _p in (CACHE, IV_DIR, RESULTS):
    _p.mkdir(parents=True, exist_ok=True)
