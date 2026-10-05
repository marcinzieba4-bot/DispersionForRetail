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

## Results on VolVue IV (2007-01 .. 2026-10, net of the spec's retail costs)

![equity](results/equity_VOLVUE.png)

| variant                      | cagr   | vol    | sharpe | sortino | maxdd  | calmar | calmar_2h | beta | worst_month | 2008   | 2022  | reference (spec section 8) |
|:-----------------------------|:-------|:-------|-------:|--------:|:-------|-------:|----------:|-----:|:------------|:-------|:------|:---------------------------|
| 1x_30-10_naked_W             | +3.8%  | 2.1%   |   1.78 |    2.27 | -5.5%  |   0.69 |      0.81 | 0.04 | -1.3%       | +6.0%  | +0.7% | Sharpe 1.36, maxDD -2.9%, Calmar 1.07, gross +2.97%/yr, net +2.2..3.1% |
| 1x_30-10_naked_nohedge       | +3.1%  | 2.5%   |   1.24 |    1.21 | -4.4%  |   0.70 |      0.73 | -0.01| -2.3%       | +6.3%  | +2.2% | hedge matters |
| 1x_30-10_naked_D             | +3.6%  | 2.1%   |   1.71 |    2.70 | -4.0%  |   0.90 |      1.14 | 0.05 | -1.0%       | +3.5%  | -1.0% | daily: no Sharpe gain, 2x turnover |
| 1x_longcall_naked_W          | +3.4%  | 2.9%   |   1.14 |    1.71 | -6.2%  |   0.55 |      1.09 | 0.03 | -2.0%       | +3.2%  | -1.0% | 30-10 vertical is the structure |
| 1x_30-10_regT_1d_W           | +3.6%  | 2.1%   |   1.69 |    2.18 | -5.5%  |   0.65 |      0.77 | 0.04 | -1.3%       | +5.7%  | +0.6% | ~0.1 Sharpe below naked |
| 1x_30-10_regT_5d_W(rejected) | +2.4%  | 2.0%   |   1.17 |    1.42 | -5.6%  |   0.43 |      0.56 | 0.02 | -1.5%       | +4.4%  | -0.2% | rejected: 5-delta wing |
| 1x_30-10_gs_W                | +4.8%  | 2.1%   |   2.25 |    2.88 | -4.7%  |   1.03 |      1.14 | 0.04 | -1.2%       | +7.5%  | +1.7% | institutional costs |
| 5x_30-10_naked_W             | +12.5% | 10.1%  |   1.21 |    1.53 | -25.9% |   0.48 |      0.47 | 0.20 | -6.6%       | +23.4% | -3.4% | +17.7%/yr, vol 11.5%, Sharpe 1.54, Sortino 3.4, maxDD -13.6%, Calmar 1.31, beta 0.37, worst -7.0%, 2008 +15% |
| 5x_30-10_ES_SPAN_W           | +12.9% | 10.1%  |   1.25 |    1.58 | -25.7% |   0.50 |      0.48 | 0.20 | -6.6%       | +24.1% | -3.1% | default retail product |
| 5x_30-10_regT_1d_W           | +11.1% | 10.0%  |   1.10 |    1.42 | -25.8% |   0.43 |      0.43 | 0.19 | -6.4%       | +21.5% | -4.2% | Sharpe 1.46-1.55, Calmar 1.19-1.28 |
| 5x_30-10_gs_W                | +18.1% | 10.1%  |   1.70 |    2.16 | -22.5% |   0.80 |      0.75 | 0.21 | -6.3%       | +32.0% | +1.1% | |
| 100k_5x_half_ES_W            | +13.4% | 11.9%  |   1.12 |    1.56 | -22.2% |   0.60 |      0.62 | 0.25 | -7.7%       | +13.0% | -6.2% | +19.6%/yr, Sharpe 1.44, Calmar 1.12 |
| 3x_30-10_ES_SPAN_W           | +8.4%  | 6.1%   |   1.35 |    1.72 | -16.1% |   0.52 |      0.53 | 0.12 | -3.9%       | +14.9% | -1.1% | recommended live start |

Full risk tables (VaR/CVaR, skew/kurtosis, drawdown duration, down-market
beta, crisis windows, calendar years) are in `results/risk_VOLVUE.md`;
drawdown paths in `results/drawdown_VOLVUE.png`.

![drawdown](results/drawdown_VOLVUE.png)

What the VolVue run says:

* **Reproduced:** every structural ranking in the spec (weekly hedge > daily
  > none on Sharpe; 30-10 vertical > bare long call; 1-delta wing costs ~0.1
  Sharpe, 5-delta wing is rejected; institutional costs are worth ~0.5 Sharpe),
  near-zero beta (0.04 at 1x, 0.20 at 5x; down-market beta 0.02 / 0.08), the
  crisis-positive profile (2008 is the best year at every leverage: +23..32%
  at 5x; Covid-2020 window -3.5%, 2022 bear -3..-4% at 5x while SPY lost
  18..36%), 5x vol 10.1% vs 11.5%, worst month -6.6% vs -7.0%, and the $100k
  rotating-half version keeping breadth through time.
* **Above the reference at 1x:** Sharpe 1.78 vs 1.36 and net +3.8%/yr vs
  +2.2..3.1%. The spec's 1x reference carries no cash yield; the +1.9%/yr of
  FEDFUNDS on equity explains most of the gap, and VolVue's lower single/SPY
  ratio (1.48 vs 1.59) means the sleeve itself is in line, not richer.
* **Below the reference at 5x on drawdown:** maxDD -25.9% vs -13.6% and Calmar
  0.48 vs 1.31. The drawdown is the 2020-2022 single-name dispersion drought
  (Covid vol crush, then 2022) with leverage compounding it; the same stretch
  costs the reference -8%. The gap is concentrated there, not in a different
  return path, and the second-half Calmar (0.47) is the number to plan on.

Gross/net at 1x on VolVue: retail half-spread costs 1.6%/yr and commissions
0.6%/yr on the spec's per-leg parameters; the spec's "~0.7%/yr retail"
headline corresponds to the 30-delta leg alone.

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
