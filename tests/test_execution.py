import datetime as dt

from dispersion import config
from dispersion.execution import sizing, strikes, symbols
from dispersion.execution.broker import PaperBroker, Position
from dispersion.execution.hedge import book_dollar_delta, hedge_order
from dispersion.execution.orders import Leg, Quote, Ticket, walk_prices, work_order


def test_occ_symbol():
    assert symbols.occ("SPY", "2022-11-18", 400) == "SPY   221118C00400000"
    assert symbols.occ("AAPL", "2022-06-17", 150, call=False) == "AAPL  220617P00150000"
    o = symbols.parse_occ("SPXW  220520C04025000")
    assert o == {"root": "SPXW", "expiry": "2022-05-20", "call": True, "strike": 4025.0}


def test_walk_prices_debit_and_credit():
    q = Quote(1.00, 1.20)             # mid 1.10, half-spread 0.10
    d = walk_prices(q, "Debit")
    c = walk_prices(q, "Credit")
    assert d == [1.10, 1.12, 1.14, 1.16]
    assert c == [1.10, 1.08, 1.06, 1.04]


def test_work_order_fills_on_second_step():
    calls = []
    state = {"n": 0}

    def submit(t, p):
        calls.append(("submit", p)); return "o1"

    def status(oid):
        state["n"] += 1
        return "Filled" if state["n"] >= 2 else "Live"

    def replace(oid, p):
        calls.append(("replace", p)); return oid

    def cancel(oid):
        calls.append(("cancel", None))

    t = Ticket([Leg("X", "Equity Option", "Buy to Open", 1)], "Debit", "t")
    out = work_order(submit, status, replace, cancel, t, Quote(1.0, 1.2), sleep=lambda s: None)
    assert out.status == "filled" and out.fill_price == 1.12
    assert ("cancel", None) not in calls


def test_pick_vertical_from_chain():
    chain = list(range(90, 131, 5))
    lo, hi = strikes.pick_vertical(100.0, 0.3, 30 / 365, chain)
    assert lo.strike < hi.strike
    assert abs(lo.model_delta - 0.30) < 0.08 and hi.model_delta < lo.model_delta


def test_sizing_small_account_rotation_and_skip():
    names = [f"N{i}" for i in range(30)]
    prices = {n: 100.0 for n in names}
    prices["N1"] = 5000.0            # one contract = $500k >> target
    plan = sizing.build_plan(50_000, 5, names, prices, 500.0, month_index=1, index_mode="ES")
    assert plan.names == names[1::2]                # odd month -> even indices (ranks 2,4,...)
    assert "N1" in plan.skipped
    assert all(k >= 1 for k in plan.contracts.values())
    assert plan.index_instrument == "MES"
    assert plan.index_contracts == round(plan.index_notional / (5 * 5000))
    plan2 = sizing.build_plan(50_000, 5, names, prices, 500.0, month_index=2, index_mode="ES")
    assert plan2.names == names[0::2]
    big = sizing.build_plan(2_000_000, 5, names, prices, 500.0, month_index=2, index_mode="ES")
    assert len(big.names) == 30 and big.index_instrument == "ES"


def test_rails():
    r = sizing.Rails()
    assert r.leverage_after_margin_check(5, 70_000, 100_000)[0] == 4
    assert r.leverage_after_margin_check(5, 50_000, 100_000)[0] == 5
    assert r.must_flatten_short_leg(100_000, 95_000)
    assert sizing.Rails.never_scale_up_after_loss(3, 5, 100_000, 90_000) == 3
    assert sizing.Rails.never_scale_up_after_loss(3, 5, 100_000, 110_000) == 5


def test_hedge_neutralises_book_delta():
    today = dt.date(2026, 1, 15)
    pos = [Position("AAPL  260220C00250000", "Equity Option", 10, 100, "AAPL", 250.0, "2026-02-20", True, 0.3),
           Position("SPY   260220C00620000", "Equity Option", -4, 100, "SPY", 620.0, "2026-02-20", True, 0.18)]
    prices = {"AAPL": 255.0, "SPY": 610.0}
    dd = book_dollar_delta(pos, prices, today)
    h = hedge_order(pos, prices, today, "SPY", 0)
    assert abs(h.dollar_delta_after) <= 0.5 * 610 + 1e-6
    assert (h.quantity < 0) == (dd > 0)


def test_paper_broker_roundtrip():
    b = PaperBroker(50_000)
    t = Ticket([Leg("AAPL  260220C00250000", "Equity Option", "Buy to Open", 2),
                Leg("AAPL  260220C00270000", "Equity Option", "Sell to Open", 2)], "Debit", "aapl vert")
    oid = b.submit(t, 1.5)
    assert b.status(oid) == "Filled"
    q = {p.symbol: p.quantity for p in b.positions()}
    assert q["AAPL  260220C00250000"] == 2 and q["AAPL  260220C00270000"] == -2
