# Mega-cap Call Dispersion (GS institutional + tastytrade retail)

Systematic options strategy: **long single-name upside convexity, short index
upside convexity**, delta- and notional-matched, hedged weekly. Built from
scratch to the specification in the build prompt: point-in-time universe,
monthly cycle, Black-Scholes flat-IV backtest with explicit retail /
institutional costs, and an execution layer for tastytrade (ES/MES futures
options under SPAN) or a prime broker (naked SPY calls under portfolio margin).

```
dispersion/
  config.py              every parameter of the spec (deltas, sizing, costs, rails)
  bs.py                  Black-Scholes pricing/delta + K = S*exp(-z_d*s + s^2/2) strike rule
  data/constituents.py   point-in-time S&P 500 membership (fja05680/sp500, no survivorship)
  data/prices.py         yfinance daily closes / volume / shares / splits, parquet cache
  data/universe.py       top-100 by market cap -> top-30 by trailing-12m median $ volume
  data/rates.py          FRED FEDFUNDS, VIXCLS, CBOE single-name vol indices
  data/iv.py             IV provider interface, CSV loaders (ORATS/IvyDB), labelled PROXY fallback
  data/volvue.py         VolVue /query client: iv_call_30, iv_put_30, iv_mean_30, iv_skew_30 (cached)
  backtest/engine.py     monthly roll, weekly hedge, costs, leverage, Reg-T / naked / ES variants
  backtest/metrics.py    CAGR, vol, Sharpe, Sortino, maxDD, Calmar, beta, yearly, 2nd-half
  execution/strikes.py   listed-chain strike selection from IV30 (furthest strike for the 1-delta wing)
  execution/sizing.py    0.9*L/N sizing, rotating 15-name half, 2x-contract skip, margin/flatten rails
  execution/orders.py    limit-walk algorithm: mid -> far side in 3-4 steps of 20% half-spread, 15-20 s
  execution/hedge.py     weekly net $-delta at entry IVs -> SPY shares or MES
  execution/broker.py    Broker interface + PaperBroker (synthetic chains/quotes for offline validation)
  execution/tastytrade.py OAuth2, chains, quotes, order & margin dry-run, submit/replace/cancel
  execution/institutional.py naked SPY (or 1-delta Reg-T spread) index ticket, GS cost model
  execution/runner.py    CLI: roll / hedge / status, dry-run first, 60% margin rail
scripts/fetch_data.py    download everything
scripts/run_backtest.py  run the variant grid, write results/summary_<TAG>.csv
scripts/report.py        markdown table vs reference numbers + equity chart
scripts/risk_report.py   VaR/CVaR, skew, drawdown duration, down-beta, crisis windows, yearly table
scripts/run_overlay.py   long SPY + sleeve overlay at 1x/3x/5x
scripts/reconcile.py     accounting-convention reconciliation (costs, additive vs compounded, daily vs monthly DD)
scripts/run_realistic.py most realistic tastytrade cost model, full risk stats across L and account size
tests/                   16 tests: BS/delta targets, strike picks, sizing, rails, hedge, limit walk, engine accounting
```

## Quick start

```bash
pip install -r requirements.txt
python scripts/fetch_data.py          # ~10 min: 976 historical S&P tickers, shares, splits, FRED
python scripts/fetch_volvue.py        # VOLVUE_API_KEY: traded 30d IVs (else the engine uses the PROXY)
python scripts/run_backtest.py        # all variants, 2007-01 .. today
python scripts/report.py VOLVUE       # table + results/equity_VOLVUE.png
python scripts/risk_report.py VOLVUE  # tails, crisis windows, drawdowns -> results/risk_VOLVUE.md
python -m pytest -q
# execution, offline mechanics check (no credentials needed):
python -m dispersion.execution.runner roll  --paper --dry-run --leverage 3 --index-mode ES
python -m dispersion.execution.runner hedge --paper --dry-run
```

## Implied vols: VolVue (traded) is wired in; the proxy stays as a fallback

`data/volvue.py` pulls daily `iv_call_30` / `iv_put_30` / `iv_mean_30` /
`iv_skew_30` for every universe ticker plus SPY from the VolVue query API
(`VOLVUE_API_KEY`), caches them in `data/cache/volvue_iv30.parquet`, and
`get_provider("auto")` selects them whenever the cache exists. Coverage is
99.8% of the top-30 slots every month from 2006 (VolVue also carries the
delisted 2008 names, but yfinance has no prices for them, so they still cannot
be traded). The engine prices with `iv_call_30`, the wing we trade.

```bash
export VOLVUE_API_KEY=...
python scripts/fetch_volvue.py            # ~3 min, 679 tickers, 3.15M rows
python scripts/run_backtest.py --iv volvue
python scripts/report.py VOLVUE && python scripts/risk_report.py VOLVUE
```

Data check (`results/iv_diagnostic_volvue_vs_proxy.csv`): VolVue's
cross-sectional median single/SPY 30d IV ratio is 1.48 over 2007-2026 (spec:
1.59 traded, 1.57 break-even), its SPY level sits 1-3 points under VIX as an
ATM 30-day IV should, and call minus put IV is +0.4 pts for SPY and +0.2 pts
for singles. Without the key the engine falls back to a VIX-anchored PROXY,
labelled as such on every output (`results/*_PROXY.*`).

## Results on VolVue IV (2007-01 .. 2026-10, net of retail costs)

![equity](results/equity_VOLVUE.png)

Two drawdown columns: `maxdd` on daily marked equity (what a margin desk sees),
`maxdd_m` on month-end equity (the convention behind the spec's reference).

| variant                        | cagr   | vol   | sharpe | sortino | maxdd  | calmar | maxdd_m | calmar_m | calmar_2h | beta | worst_m | 2008   | 2022  | reference (spec section 8) |
|:-------------------------------|:-------|:------|-------:|--------:|:-------|-------:|:--------|---------:|----------:|-----:|:--------|:-------|:------|:---------------------------|
| 1x_30-10_naked_W               | +4.0%  | 2.1%  |   1.85 |    2.38 | -5.4%  |   0.73 | -3.4%   |     1.15 |      0.83 | 0.04 | -1.2%   | +6.2%  | +0.8% | Sharpe 1.36, maxDD -2.9%, Calmar 1.07, gross +2.97%/yr, net +2.2..3.1% |
| 1x_30-10_naked_nohedge         | +3.2%  | 2.5%  |   1.30 |    1.23 | -4.3%  |   0.74 | -2.6%   |     1.23 |      0.75 | -0.01| -2.3%   | +6.6%  | +2.3% | hedge adds ~0.5 Sharpe |
| 1x_30-10_naked_D               | +3.7%  | 2.1%  |   1.78 |    2.84 | -3.9%  |   0.95 | -3.2%   |     1.16 |      1.17 | 0.05 | -1.0%   | +3.7%  | -0.9% | daily: no Sharpe gain |
| 1x_longcall_naked_W            | +3.5%  | 2.9%  |   1.17 |    1.76 | -6.0%  |   0.57 | -5.5%   |     0.62 |      1.11 | 0.03 | -2.0%   | +3.4%  | -1.0% | 30-10 vertical is the structure |
| 1x_30-10_regT_1d_W             | +3.7%  | 2.1%  |   1.77 |    2.29 | -5.4%  |   0.69 | -3.5%   |     1.08 |      0.80 | 0.04 | -1.3%   | +6.0%  | +0.6% | ~0.1 Sharpe below naked |
| 1x_30-10_regT_5d_W(rejected)   | +2.5%  | 2.0%  |   1.26 |    1.55 | -5.5%  |   0.46 | -3.9%   |     0.65 |      0.58 | 0.02 | -1.4%   | +4.7%  | -0.2% | rejected: 5-delta wing |
| 1x_30-10_gs_W                  | +4.8%  | 2.1%  |   2.25 |    2.88 | -4.7%  |   1.03 | -2.7%   |     1.77 |      1.14 | 0.04 | -1.2%   | +7.5%  | +1.7% | institutional costs |
| 5x_30-10_naked_W               | +14.8% | 10.1% |   1.42 |    1.81 | -24.9% |   0.60 | -16.4%  |     0.90 |      0.55 | 0.21 | -6.5%   | +26.8% | -2.3% | +17.7%/yr, vol 11.5%, Sharpe 1.54, Sortino 3.4, maxDD -13.6%, Calmar 1.31, beta 0.37, worst -7.0%, 2008 +15% |
| 5x_30-10_ES_SPAN_W             | +15.0% | 10.1% |   1.43 |    1.83 | -24.7% |   0.61 | -16.3%  |     0.92 |      0.55 | 0.21 | -6.4%   | +27.0% | -2.1% | default retail product |
| 5x_30-10_regT_1d_W             | +13.7% | 10.0% |   1.34 |    1.76 | -24.6% |   0.56 | -16.6%  |     0.82 |      0.51 | 0.19 | -6.3%   | +25.3% | -3.0% | Sharpe 1.46-1.55, Calmar 1.19-1.28 |
| 5x_30-10_gs_W                  | +18.1% | 10.1% |   1.70 |    2.16 | -22.5% |   0.80 | -14.1%  |     1.28 |      0.75 | 0.21 | -6.3%   | +32.0% | +1.1% | |
| 5x_30-10_spec_headline_costs_W | +17.8% | 10.1% |   1.67 |    2.21 | -21.9% |   0.82 | -13.5%  |     1.32 |      0.76 | 0.21 | -6.3%   | +33.5% | +1.7% | flat 0.7%/yr per 1x: reproduces the reference |
| 100k_5x_half_ES_W              | +14.7% | 11.9% |   1.21 |    1.68 | -21.1% |   0.70 | -15.2%  |     0.97 |      0.70 | 0.25 | -7.7%   | +13.5% | -5.4% | +19.6%/yr, Sharpe 1.44, Calmar 1.12 |
| 3x_30-10_ES_SPAN_W             | +9.4%  | 6.1%  |   1.50 |    1.88 | -15.5% |   0.60 | -10.0%  |     0.94 |      0.58 | 0.12 | -3.8%   | +16.2% | -0.6% | recommended live start |

Full risk tables (VaR/CVaR, skew/kurtosis, drawdown duration, down-market
beta, crisis windows, calendar years) are in `results/risk_VOLVUE.md`;
drawdown paths in `results/drawdown_VOLVUE.png`.

![drawdown](results/drawdown_VOLVUE.png)

### Most realistic retail scenario

`scripts/run_realistic.py` -> `results/realistic_VOLVUE.md` / `.png`. Costs:
per-leg half-spreads as specified without the 1.3 exit/roll multiplier (hold
to expiry), tastytrade commissions $1/contract capped at $10/leg, $0.15
clearing+exchange per contract uncapped, ES/MES options $3/contract all-in,
1bp hedge turnover. At 5x on ES/MES: +15.9%/yr, vol 10.1%, Sharpe 1.51,
maxDD -23.8% daily / -15.4% month-end, Calmar 0.67 / 1.03, beta 0.21,
worst month -6.4%, 2008 +28%, all-in cost 4.8%/yr of equity.

### The index leg priced from real trades: the edge disappears

The short 30-delta SPX call can be taken from actual settlement prices
instead of a model: Cboe's BXMD index writes exactly that option every 3rd
Friday since 1986 (BXM at-the-money, BXY 2% OTM), and its return minus the
S&P 500 total return is the realised P&L of the leg, skew included.
`index_leg="BXMD"` in the engine does this (cycle on 3rd Fridays so the
dates align; the singles still use VolVue IV, which the live-chain check
showed prices them within 2%). Scripts: `run_real_index_leg.py`,
`results/index_leg_real_vs_model.csv`, `results/real_index_leg_VOLVUE.md`,
`results/reverse_VOLVUE.csv`, `results/put_overlay_VOLVUE.csv`.

Short 30-delta SPX call held to expiry, 2007-2026, per cycle as % of notional:
real (BXMD) **-0.24%** = -2.9%/yr; flat-IV model **+0.04%** = +0.5%/yr;
correlation of the two series 0.93, and the model exceeds the real leg in 19 of
20 calendar years (1.5-8%/yr). The implied premium actually collected is 0.68
of the model's, against 0.84 measured on today's low-skew chain. The reason is
simple: the SPX 30-delta call trades at roughly realised vol (ATM 15.2 - skew
1.7 vs realised 13.5), so there is no premium in it to sell; the flat-IV engine
sells it at ATM vol and books the ATM variance premium that the market does
not pay on that strike.

Realistic costs, dividends on, 3rd-Friday cycle, VolVue singles:

| variant | CAGR | Sharpe | maxDD daily | 2008 | 2022 |
|---|---|---|---|---|---|
| 5x, index leg model flat IV (as specified) | +11.0% | 1.06 | -23.5% | +36% | -6% |
| 5x, index leg BXMD real | -5.6% | -0.38 | -81% | -11% | -31% |
| 3x, index leg BXMD real | -2.7% | -0.31 | -61% | -5% | -19% |
| 1x, index leg BXMD real | +0.1% | 0.06 | -24% | -1% | -6% |
| 1x, BXMD real, institutional costs | +0.3% | 0.12 | -23% | 0% | -5% |
| 5x, BXM ATM real, delta-matched | -8.0% | -0.71 | -87% | -21% | -28% |
| 5x, singles only (long 30-10 verticals, hedged) | -6.0% | -0.39 | -79% | -29% | -2% |
| 5x, reversed (sell verticals, buy BXMD call) | -4.0% | -0.27 | -63% | -6% | +27% |
| 1x, reversed | +0.4% | 0.16 | -13% | 0% | +7% |
| 3x, BXM ATM real + long 10d put at 1.45x IV | -12.2% | -1.04 | -93% | -16% | -31% |
| 3x, BXMD real + long 5d put at 1.73x IV | -16.2% | -1.39 | -97% | -17% | -40% |

Reading: the single-name call vertical is close to fairly priced (long and
short of it both lose roughly the cost load), the index call wing carries no
premium, and buying OTM puts at their real skew (10-delta at 1.45x ATM, 5-delta
at 1.73x, 0.3-0.6% of equity per month at 3x) only adds a known negative-carry
leg. Variance-risk-premium check on VolVue IV: singles +2.3 pts, SPY +2.3 pts
at the money, with the single/SPY ratio at 1.48 against the spec's 1.57
break-even; the "richness" the thesis needs is not in the data.

### Short ATM call + long 30-delta call on SPY, delta-hedged (real prices)

BXM minus BXMD is this spread's realised P&L with the stock exposure
cancelling exactly; the engine adds the weekly hedge (`index_legs`,
`singles_scale=0`). Unhedged, 2007-2026: -0.21% of notional per cycle
(-2.6%/yr, correlation -0.78 with SPX, i.e. a short-delta bet that lost in a
bull market). Hedged weekly, realistic costs, cash yield included: 1x
+1.4%/yr Sharpe 0.62, 3x +1.0%, 5x +0.4% with a -41% drawdown; ex cash the
sleeve is about -0.5%/yr per turn. Hedge turnover is 0.4x equity per month at
1x and 1.9x at 5x, and daily hedging is worse (-0.9% at 5x, 4.2x turnover).
Each leg alone, hedged, is also flat: short ATM (BXM) -1.7%/yr at 5x, short
30-delta (BXMD) -0.8%/yr. The spread is crisis-positive (2022 +21%, Covid
+7% at 5x) and bleeds in melt-ups, but the ATM variance premium the trade is
meant to collect is consumed by gamma cost, hedge error and spreads. Tables:
`results/atm_vs_30d_spread_VOLVUE.csv`, `results/atm_vs_30d_call_spread_real.csv`.

### Attempts to make it work (all on real index prices, realistic costs, 3x)

`results/filters_VOLVUE.csv`, `results/event_filter_diagnostic.csv`,
`scripts/fetch_volvue_term.py` (iv_call_20/60, 52-week percentile).

| change | singles-only hedged | dispersion, BXMD index leg |
|---|---|---|
| none | -2.9%/yr | -2.7%/yr |
| beta-weighted hedge | -3.2% (beta 0.00) | -2.9% |
| exclude earnings-event names (iv30/iv60 > 1.08, ~4 of 30) | -2.2% | -1.9% |
| short the vertical on event names, long the rest | -2.1% | -2.0% |
| long only names with 30d IV percentile <= 50 | -0.3% | +0.9% |
| long only names with percentile <= 30 | -0.7% | |
| short all verticals | -0.9% | |

Cash yield (~1.9%/yr) is inside every figure, so none of these books earns
its own carry. The event premium is real (event names carry +2.8 pts of IV
over subsequent realised vs +1.9 for the rest) but it is 4 names a month and
worth under 1%/yr; IV-percentile conditioning is the strongest signal and is
still below the retail cost load (~1.2%/yr per turn). The beta hedge only
lowers beta. Nothing tested turns the call-side book positive.

### Put spreads on the index, from real Cboe legs (`results/put_spread_VOLVUE.csv`)

Cboe's PUT (short ATM put), PPUT (long 5% OTM put on the index), RXM (short
25-delta put + long 25-delta call), CNDR (20/5 iron condor) and BXMD give the
put wing from actual settlements; the engine combines them per cycle
(`index_legs`, sign +1 = hold the index's position) and adds the weekly
hedge. 2007-2026, realistic costs, cash yield (~1.9%/yr) included:

| index-only structure | 1x CAGR / Sharpe / maxDD / 2008 | 3x CAGR / Sharpe / maxDD |
|---|---|---|
| short ATM put, unhedged (PUT) | +6.6% / 0.72 / -34% / -24% (beta 0.54) | +9.6% / 0.49 / -81% |
| short ATM put, hedged weekly | +3.9% / 0.75 / -15% / -3% | +7.7% / 0.55 / -47% |
| short 25d put, hedged (RXM + BXMD) | +3.5% / 0.90 / -11% / -1% | +6.8% / 0.62 / -30% |
| **25d / 5%-OTM put spread, hedged** (RXM + BXMD + PPUT) | **+0.9% / 0.58 / -7% / +4%** | -0.4% / -0.05 / -24% |
| ATM / 5%-OTM put spread, unhedged | +3.8% / 0.74 / -15% / -7% | +7.2% / 0.53 / -46% |
| 20/5 iron condor (CNDR) | +0.8% / 0.17 / -18% / -4% | -2.2% / 0.00 / -59% |
| model 25d/5d spread at measured skew (1.16x, 1.73x) | -0.3% unhedged, -1.2% hedged | -4.5% / -6.6% |
| short ATM call + long 30d call, hedged (BXM - BXMD) | +1.3% / 0.55 / -12% | +0.6% / 0.12 / -32% |

The premium is in the short put and it is crash risk: the 5%-OTM wing (a
10-15 delta put at one month) costs ~2.6%/yr of notional, i.e. almost all of
the 25-delta put's hedged premium, so the defined-risk spread is flat ex
cash. The ATM/30-delta call-spread row corrects an earlier run of this
README that had the structure reversed; the conclusion (flat) is unchanged.

### Single-name event selling (`results/event_selling_VOLVUE.csv`, `..._scaling_VOLVUE.csv`)

Short ATM straddles (both legs at VolVue's validated ATM IV) on names whose
30-day IV sits >8% above the 60-day (an earnings event inside the cycle),
weekly hedge, per-name notional 0.9L/N, realistic costs (1.5% ATM
half-spread, capped commissions, fees), 3rd-Friday cycle:

| book (100 most liquid S&P names, ~12 event names/month) | CAGR | Sharpe | maxDD | worst month | 2008 | 2020 | 2022 | cost/yr | deployed |
|---|---|---|---|---|---|---|---|---|---|
| 1x | +2.3% | 1.92 | -3.0% | -1.2% | +3.2% | +1.1% | +3.1% | 0.2% | 0.11x |
| 3x | +3.5% | 1.11 | -9.2% | -3.6% | +5.9% | +2.7% | +6.0% | 0.6% | 0.34x |
| 5x | +4.8% | 0.93 | -16.2% | -6.0% | +8.6% | +4.2% | +9.0% | 1.0% | 0.56x |
| 8x | +6.6% | 0.82 | -27.2% | -9.5% | +12.9% | +6.6% | +13.6% | 1.5% | 0.90x |
| 3x, 3% ATM half-spread | +3.0% | 0.96 | -9.8% | | | | | 1.1% | |
| 5x, no cash yield | +2.9% | 0.59 | -20.3% | | | | | | |
| control: non-event names, 3x | +2.2% | 0.24 | -35.8% | -17.3% | -3.2% | -19.0% | -18.0% | | 2.5x |
| sanity: long straddles on event names, 3x | -1.5% | -0.27 | -32.7% | | | | | | |

This is the only positive, crisis-robust edge found in the whole study: ex
cash about 1%/yr per turn of leverage, positive in 19 of 20 years, positive
in 2008, 2020 and 2022, beta ~0, and the control (same structure on
non-event names) is flat with five times the drawdown. It is small and
capacity-limited (12 names, 0.56x of equity deployed at 5x), the event flag
is a term-structure proxy rather than an earnings calendar, and single-name
straddles are undefined-risk shorts that need a margin plan; but it is real
in the data.

### Are the numbers real? A critical check (read this before trading)

Three things were measured rather than assumed (`results/live_*.csv`,
`results/skew_adjusted_VOLVUE.md`, `results/skew_timevarying_VOLVUE.csv`).

**1. The VolVue IV series is sound.** 3.15M daily rows, 679 tickers, no stale
runs, zero-day staleness at every month-end entry, call-put IV within +-4 pts
for 90% of observations. Against the independent CBOE single-name vol indices
(AAPL, AMZN, GS, IBM) and VIX it correlates 0.96-0.99 in level and sits ~2 pts
below, which is where an ATM 30-day IV should sit relative to a
variance-strip index. Its 30-day level matches the live chain's ATM IV on the
month-end expiry within +-10% name by name (SPY 13.0 vs 13.6). The ATM level
is not the problem.

**2. The single-name legs price realistically; the index leg does not.**
On the CBOE closing chain for the Oct-30 month-end expiry, at the engine's own
strikes, market mid / engine price was: long 30d call 1.00 (median), short 10d
call 1.10 (the call smile pays us more than modelled, as the spec says),
vertical debit 0.98. Half-spreads were 3.8% at 30d and 7.3% at 10d against the
spec's 3% / 8%, and open interest at the 10d strike a median 258 contracts,
thin but fine for retail size. **The short 30-delta SPY call, however, traded
at 0.84 of the engine's price**: the real call wing is 1.7 vol points under
ATM (chain 11.9 vs VolVue 13.0) and a flat-IV engine collects ~16% more index
premium than the market pays. The spec states the opposite bias for index
wings; that is true for the 1-delta wing (market 0.87 of model, cheaper to
buy) but not for the 30-delta leg we sell every month, which is the whole
short side of the book.

**3. That one bias is first-order.** Re-pricing the index leg at the measured
ratio, everything else unchanged (realistic costs, 5x, ES/MES):

| index 30d call IV vs ATM | CAGR | Sharpe | maxDD daily / monthly | Calmar daily / monthly | 2008 | 2022 |
|---|---|---|---|---|---|---|
| flat (engine as specified) | +15.9% | 1.51 | -23.8% / -15.4% | 0.67 / 1.03 | +28% | -1% |
| 0.95 (mild) | +12.3% | 1.21 | -26.4% / -18.3% | 0.47 / 0.67 | +23% | -4% |
| 0.915 (measured today) | +8.7% | 0.89 | -28.8% / -21.2% | 0.30 / 0.41 | +17% | -8% |
| time-varying, half of today's calibration | +6.5% | 0.68 | -30.8% / -24.6% | 0.21 / 0.26 | +19% | -8% |
| time-varying, today's calibration (0.39 x VolVue skew) | -1.3% | -0.09 | -59% / -57% | n/a | +8% | -15% |

Today is a low-skew day (VolVue SPY `iv_skew_30` 4.4 vs a 2006-2026 median of
8.7 and 13.4 in 2008), so the measured 0.915 is probably the mild end of the
historical bias. The time-varying rows scale the call-wing discount with that
skew history; the linear extrapolation from one day is itself uncertain (in
high-skew regimes the put wing steepens more than the call wing flattens), so
treat them as a range, not a point. The range runs from "half the headline" to
"no edge at all". The structural conclusions (weekly hedge, 30-10 vertical,
breadth, crisis-positive 2008) survive in every row; the level does not.

**4. Quoting and execution at tastytrade.** Equity-option spreads and fees are
realistic as modelled. ES options at 0.3% half-spread are fair for ES; MES
options are thinner (expect 1-2x that, and partial fills on the walk). The
month-end cycle lands on weekly expiries, whose 10-delta strikes carry a
fraction of the monthly's open interest; the 3rd-Friday monthly would fill
better at the cost of a 3-week instead of month-end cycle. The sandbox
serves no quotes, so none of the fill assumptions have been validated against
live tastytrade markets; that is what the L=1-3 live period in the spec is
for. Margin: SPAN on $4.5M of short ES calls is roughly 25-40% of a $1M
account in calm markets and will trip the 60% rail in stress; naked SPY calls
under portfolio margin at 5x are unlikely to fit under the rail at all.

**What would make the numbers real.** A per-delta IV history for SPX/SPY and
the names (ORATS `dlt30Iv`/`dlt10Iv`, IvyDB surface, or VolVue if it exposes
delta-bucket fields). The engine already prices every leg at its own IV
(`iv_mult_*`, `index_skew_coeff`); with a real call-wing series those knobs
become data instead of a sensitivity. Until then the honest statement is:
Sharpe 0.7-1.2 and Calmar 0.2-0.5 at 5x on daily marks, with the flat-IV
+15.9% as an upper bound.

### Reconciliation with the reference numbers

`scripts/reconcile.py` (table in `results/reconcile_VOLVUE.md`) isolates the
conventions. On the same VolVue data and engine, the reference's 5x headline
(+17.7%/yr, maxDD -13.6%, Calmar 1.31, second-half Calmar ~1.05-1.1) is
reproduced to within rounding by the `spec_headline_costs` row: costs as a
flat 0.7%/yr per turn of leverage and drawdown on month-end equity
(+17.8%, -13.5%, 1.32, 2nd-half 0.76 daily / 1.21 monthly). The strategy,
data and P&L path agree; the differences are:

* **Cost convention.** Applying the spec's per-leg half-spreads (3% / 8% /
  0.3% x 1.3) to every leg costs 1.6%/yr per 1x; the spec's own "~0.7%/yr
  retail" headline is the 30-delta leg alone. At 5x that is an 8%/yr swing
  in CAGR and the whole Calmar gap. The 1.3 multiplier is meant for ITM
  exits and rolls, which a hold-to-expiry book rarely does, so 1.0-1.3%/yr
  per 1x is the defensible middle (`5x_no_1.3_mult_no_comm`: +16.6%,
  Calmar 1.10 monthly).
* **Commissions.** The first version charged $1/contract uncapped; tastytrade
  caps at $10 per leg, so a $1M book at 5x was overcharged ~4x. Fixed;
  it lifted 5x CAGR from +12.9% to +15.0%.
* **Drawdown granularity.** Daily marks make every max drawdown ~1.5x the
  month-end figure (-24.7% vs -16.3% at 5x). Both are reported; the daily
  one is what a margin call is computed on.
* **Calmar flat in leverage** holds in this engine too (0.60 / 0.61 / 0.51 /
  0.52 daily at 3x / 5x / 8x / 10x), so it is not evidence of either
  convention.
* **Beta 0.21 vs 0.37** and vol 10.1% vs 11.5% remain the one unexplained
  residual; it is consistent with the reference hedging less exactly or using
  a different IV field, and it does not move Calmar.

### Long SPY + sleeve overlay

`scripts/run_overlay.py` layers the sleeve on a 100% long-SPY book (equity in
SPY shares, so the sleeve earns no cash yield; sized monthly off total equity).
Tables in `results/overlay_VOLVUE.md`, chart `results/overlay_VOLVUE.png`.
On VolVue IV, 2007-2026: SPY alone +10.9%/yr, Sharpe 0.75, maxDD -55%;
SPY + 3x sleeve +17.3%, Sharpe 0.98, maxDD -52%; SPY + 5x sleeve +21.6%,
Sharpe 1.05, maxDD -51%, beta 1.19. The overlay adds return almost
one-for-one but barely touches the equity drawdown: the sleeve's 2008 gain
(+13..23%) offsets a third of SPY's loss, while 2020-2022 the sleeve and SPY
draw down together.

### Earlier PROXY-IV run (no licensed data)

Kept for comparison in `results/summary_PROXY.*`: the proxy gave the same
structural rankings but +2.4%/yr, Sharpe 1.00 at 1x and only +5%/yr at 5x,
confirming the spec's warning that proxied IVs are not a substitute.

## Mechanics implemented (spec sections)

* **Universe (2):** PIT S&P membership -> top-100 by market cap (raw close x
  shares; Yahoo shares history starts 2015, earlier dates back-fill through
  splits) -> top-30 by trailing-12m median daily dollar volume, shifted one
  month. Breadth floor 13 names (skip the month below it). No other filters.
* **Cycle (3):** month-end to month-end, one expiry. Long 30d / short 10d call
  per name at 0.9*L*E/N; short 30d index calls notional-matched to what was
  actually deployed; naked (PM / SPAN) or 1-delta wing (Reg-T). MES below
  $300k short notional, ES above. Strike rule
  `K = S*exp(-z_d*s + s^2/2)`, z = -0.5244 / -1.2816 / -2.3263; live chains
  take the nearest listed strike, furthest listed for an off-chain 1-delta wing.
* **Hedge (4):** every 5th trading day, sum of BS dollar deltas at entry IV
  with remaining time, neutralised in SPY shares (backtest) or SPY / MES (live).
  Measured turnover ~0.3x book/month.
* **Sizing (5):** reset from equity monthly; `Rails.never_scale_up_after_loss`
  blocks recovery sizing; margin > 60% of equity cuts L by one at the next roll;
  equity under floor + 10% buffer flattens the short leg first. Small accounts
  (< $276k x L/5) trade the rotating 15-name half and skip any name whose single
  contract exceeds 2x its target notional.
* **Costs (6):** half-spread % of premium x1.3 (1.5/3/8/15% singles by delta,
  0.5% SPY, 0.3% ES/MES), 1bp of hedge turnover, $1/contract to open on
  real (unadjusted) contract counts, GS 1.5% flat. Cash on equity at FEDFUNDS.
* **Execution (10):** limit at mid walked toward the far side in 4 steps of 20%
  of the half-spread, 15-20 s apart; verticals as single 2-leg tickets; every
  ticket dry-run first; whole-roll margin dry-run (`POST /margin/accounts/{acct}/dry-run`)
  before any submit; unique `external-identifier` on every order and a
  live-orders lookup before any resubmit (tastytrade does not deduplicate).

## Going live on tastytrade

1. Create an OAuth app and a personal grant on my.tastytrade.com (sandbox and
   production are separate apps). Export `TT_ENV=sandbox|prod`,
   `TT_CLIENT_SECRET`, `TT_REFRESH_TOKEN`, optionally `TT_ACCOUNT`.
   The sandbox serves no market data: set `TT_PROD_REFRESH_TOKEN` /
   `TT_PROD_CLIENT_SECRET` and quotes come from production while orders go to
   the sandbox (sandbox fills are synthetic: limits under $3 fill, above never).
2. `python -m dispersion.execution.runner status`
3. On the last trading day of the month:
   `python -m dispersion.execution.runner roll --leverage 3 --index-mode ES --dry-run`
   then without `--dry-run`. Compare realized entry debits/credits against the
   `model` block (BS at the live ATM-implied IV30) for 3+ months at L=1-3 before L=5.
4. Weekly: `python -m dispersion.execution.runner hedge --index-mode ES`.
5. `--index-mode SPY` for a portfolio-margin account (naked SPY calls),
   `--index-mode SPY_REGT` for a Reg-T account (30-delta / 1-delta credit spread).

## Rejected by the spec and deliberately absent

Short puts anywhere; put-side ladders, ATM short legs, strangles;
momentum / top-10 name selection; regime filters on the short index leg;
un-rebalanced drift; loss-scaled sizing; index wings nearer than ~2-delta.
