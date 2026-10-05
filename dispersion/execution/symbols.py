"""OCC / futures-option symbology helpers (tastytrade conventions)."""
from __future__ import annotations

import datetime as dt

MONTH_CODES = "FGHJKMNQUVXZ"


def occ(root: str, expiry: str, strike: float, call: bool = True) -> str:
    """'SPY   221118C00400000' -- root padded to 6, yymmdd, C/P, strike*1000 8 digits."""
    d = dt.date.fromisoformat(expiry)
    return f"{root:<6}{d.strftime('%y%m%d')}{'C' if call else 'P'}{int(round(strike * 1000)):08d}"


def parse_occ(sym: str) -> dict:
    root = sym[:6].strip()
    d = dt.datetime.strptime(sym[6:12], "%y%m%d").date()
    return {"root": root, "expiry": d.isoformat(), "call": sym[12] == "C", "strike": int(sym[13:21]) / 1000.0}


def es_future(expiry_month: dt.date, mes: bool = False) -> str:
    """Quarterly ES/MES contract on or after the month (H, M, U, Z)."""
    q = [3, 6, 9, 12]
    m = next(x for x in q if x >= expiry_month.month) if expiry_month.month <= 12 else 3
    y = expiry_month.year if m >= expiry_month.month else expiry_month.year + 1
    # roll to next quarterly if within ~8 days of the 3rd Friday of the front month
    return f"/{'MES' if mes else 'ES'}{MONTH_CODES[m - 1]}{str(y)[-1]}"


def future_option(fut: str, option_root: str, expiry: str, strike: float, call: bool = True) -> str:
    """'./ESZ2 EW3Z2 221216C3800' style symbol."""
    d = dt.date.fromisoformat(expiry)
    k = f"{strike:g}"
    return f".{fut} {option_root} {d.strftime('%y%m%d')}{'C' if call else 'P'}{k}"
