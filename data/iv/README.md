# Licensed 30-day implied vol goes here

Drop one or more CSV files in this directory and the backtest switches from the
PROXY IV to the licensed data automatically (`--iv auto`, the default).
Long format, one row per (date, ticker); column names are auto-detected:

| vendor        | date column                 | ticker column       | iv column                          | units      |
|---------------|-----------------------------|---------------------|------------------------------------|------------|
| ORATS         | `tradeDate`                 | `ticker`            | `iv30d` or `orIv30d`               | decimal    |
| VolVue        | `date`                      | `symbol` / `ticker` | `iv_call_30` (or `iv_put_30`)      | vol points |
| IvyDB         | `date`                      | `ticker`            | `impl_volatility` (`days`=30, `cp_flag`=C) | decimal |
| generic       | `date`                      | `ticker`            | `iv30`                             | either     |

Values above 3 are treated as vol points and divided by 100. SPY must be
present. Daily or month-end frequency both work (the engine takes the last
value on or before each month-end, up to 7 days stale).
