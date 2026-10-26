# Updates log

What I found, what went wrong, and how it was fixed. Each entry says what is still open.

---

## 2026-10-24: first live run lost money to churn (swap floor, buy check, cooldown)

**Problem.** After the first live run (14:02-14:12, `normal` regime, 19 loops) my bankroll went from about 102,012 to 101,928, a loss of
about 84. That is about 0.65% of the 12.9k traded, over 27 orders, every one of which filled at once.

**What the logs showed** (`logs/orders.csv`, `logs/loops.csv`):
- It was **not market impact.** The MT-SEN average fill was 0.9443 against a 0.945 limit, so the book was not walked.
- The loss was **crossing the spread over and over.** SIG marks a position near the mid, so every buy at the ask is
  about 0.4-0.5c under water at once (RI-GOV bought at 0.910, marked 0.9058). About 14k shares x about 0.006 matches the 84.
  It is a paper loss that comes back at resolution if the edges are real, but churn turns it into a real cost each time.
- **Dust swaps.** The bot sat on the 60% invest cap, so each small price move triggered "sell a few NH-SEN" (9, 7, 5 shares)
  and "buy a few MT-SEN" (14, 23 shares) in alternate loops. Each one paid the spread for almost no gain.
- **A swap sold but never bought (loop 14).** It sold 1,100 MT-SEN at 0.94 "to swap into HI-GOV YES". The HI-GOV buy never
  happened, and the next loop bought MT-SEN back at 0.945. That is a pure loss of about 5.5. Cause: a swap sells in one loop and buys
  in the next, and the sell was decided without checking that the replacement buy could happen.

**Fixes** (all in the strategy logic; `SWAP_MARGIN` stays 1% as in plan.md):
1. **Minimum swap size.** `config.MIN_SWAP_PCT = 0.01`. A swap must sell at least 1% of total equity (about 1,020 today).
   If the worst holding is too small, the next-worst is tried. Exit sells (negative edge) have no floor, so the bot can
   always get out of a bad position.
2. **Check the buy before selling.** `trading._swap_out` only sells for a swap if the proceeds buy at least `MIN_ORDER_VALUE` (10)
   of the new bet, after the shares on offer and the single-order cap. `buy_shares()` is shared with `size_buys`.
3. **Per-market cooldown.** New `cooldown.py`; `config.COOLDOWN_SECONDS = 300`, held in `RunState.cooldown`. After an order in a
   market, the **opposite** direction there is blocked for 5 minutes: a just-bought market is not swapped out and a just-sold
   one is not bought back. The same direction is never blocked, because building a position takes several buys. Negative-edge exits
   ignore the cooldown. The planner prints a "cooldown" note for a buy it skips.

**Other changes the same day**
- The single-order cap is now **2% of total equity (cash + positions)**, not cash. The setting is renamed
  `max_order_pct_of_equity` in `settings.json`; the old key is rejected at start-up. Today that is 2,044.59 per buy (it was 840 on cash).
  One function, `trading.max_order_value`, is used by the cap, the live order check and the start-up screen.
- Removed `trading.decide_sells`, a wrapper only the tests used.
- `test_dry_run_orders_and_cancels_are_counted` failed once `config.DRY_RUN` was set to False. It now forces `DRY_RUN = True` itself.
- 127 tests pass (7 new: swap floor, dust, thin replacement buy, next-worst fallback, cooldown on a swap and on a buy, exits ignore the cooldown).
- A dry-run loop on the real account after the changes planned **no orders**. The same positions had produced 1-2 orders every loop before.

**Mistake to avoid.** `config.py` had `DRY_RUN = False`, and I ran `./main.py --once` to check my work. It stopped at the
`LIVE` prompt (no terminal), so nothing was sent and `orders.csv` did not change. It should not have been run. To check the bot with
`DRY_RUN = False` in the file, force it in the process: `config.DRY_RUN = True` before `import main`.

**Still open**
- The cooldown lives in memory, so restarting `main.py` forgets it.
- A swap still sells in one loop and buys in the next. Placing the buy in the same loop would be tighter, but it spends
  proceeds before the sell has filled, so it needs more care.
- `SWAP_MARGIN` was not raised. If churn continues, try 3% (one line in `config.py`).
- Untested on the real API: `POST /orders/cancel-all` with an empty body (the kill-switch path), and the order-status read right after a cancel.
- Posting at the bid instead of lifting the ask would save most of the half-spread, but fills become uncertain inside the order timeout.

---

## Stage 6: live-trading preparation (before 2026-10-24; committed as `e684eed`)

- **Start-up check** (`startup.py`): prints mode, balance, equity, open orders and every limit; LIVE mode needs the exact word `LIVE`.
- **Single-order cap** on buys (sells exempt), later changed to equity (see above).
- **Partial fills** (`trader.py`, `LiveTrader`): wait for the timeout, cancel the rest, read fills from SIG positions and cross-check
  `GET /orders/{id}`. A mismatch prints a warning and writes `errors.csv`. Netting is handled: buying Yes against a No position
  shrinks the No position.
- **Kill switch** (`safety.py`): daily loss over 10% of start-of-day equity (stored in `.day_start.json`) or 5 loops in a row with errors.
  It cancels every open order on the account. Ctrl+C and the STOP file do the same.
- **Order log:** one row for each placed / filled / partial_fill / cancelled / rejected / unconfirmed / cancel_failed / cancel_all event.
- **Limits** in `settings.json`: max_invested 0.60, per race 0.30, daily loss 0.10, 5 errors in a row. The invest cap blocks new
  buys only and never sells. Kelly uses total equity.
- Live handling was only ever tested against a fake client. Real writes had only come from `test_trade.py` (1 share, "yes" typed first).
- Bugs fixed on the way: a failed cancel was counted twice; immediately filled orders were tracked as open; a position-vs-order mismatch
  was silent; the smoke test hung on `input()`.

## Earlier stages: things we discovered

**SIG API**
- Limits: 100 reads and 30 writes a minute. The bot budgets 90 reads and 25 writes, using a sliding window (`budget.py`).
- A bulk price call plus order-book reads only for the contracts about to trade took a loop from **186 reads to about 8**
  and from 7.4s to 1.2-2.6s.
- 503 on GETs is retryable. A 502 `ORDER_STATUS_UNKNOWN` is retried with the same idempotency key, so an order can never go out twice.
- Buying the opposite side of a held position nets it. Prices are side-relative, on a 0.005 tick. `cancel-all` with no body cancels
  every open order, not just the bot's own.
- Balance was rounded too early once (44,411.485); fixed.

**Kalshi / Polymarket**
- Polymarket Gamma returned HTTP 422 for about 180 ids at once; ids are now fetched in chunks of 50. CLOB books are chunked too.
  Best bid/ask is computed as max/min over the book.
- Kalshi races are found by event title, not ticker prefix. Many House races were missing Kalshi ids at first; all were filled in and checked.
- Polymarket being blocked was not circumvented.

**Fair value and sizing**
- Fair value is the average of the Kalshi and Polymarket midpoints. Quotes are dropped when stale (over 60s), wide (over 3c) or
  missing. No fair value if the two disagree by more than 4c.
- A single source is allowed but capped at 15% of equity per race.
- A long-shot with an ask of 3c or less and no bid is treated as bid 0.
- Dem Yes and Rep No are the same bet only in two-candidate races. A race with an independent gets an extra outcome X
  (Idaho Senate: the Democrat dropped out, which was a real edge that the old merge rule would have hidden).
- Sizing: half Kelly, scaled to the 60% invest cap, 30% per race, 10 minimum order, 1% swap margin. Swaps happen only when no investable
  cash is left. Negative-edge exits always happen.

**Regimes (order timeout / loop pause):** slow 30s / 60s, normal 10s / 20s, election 3s / 5s. Election sits close to the write limit, which is why there is a write guard and a cap of 6 orders per loop.

**Races:** 117 in `races.json`, 115 enabled. FL-SEN and AK-GOV are disabled with a reason in the file.

**Tooling notes**
- macOS has no `timeout` command, and BSD `sed -i` needs an extension argument.
- Logs are lean CSVs in `logs/`: `orders.csv`, `loops.csv`, `opportunities.csv`, `errors.csv`.



## 2026-10-24 After some thought
2 things. 
1, Wheter if Kelly criterion is useful at all.
2, The continuous mispricing of the Rhode Island Senate 
3, Implications for portfolio construction


Firstly, 
I think with good reason that the Kelly criterion is actually not that usefulf or this competition. Since Kelly criterion maximises the long-term expected geometric growth rate (or the expected value of the logarithm) of wealth and capital. The key word is LONG TERM. Since this competition closes on Nov 4th and the contracts resolve the same day, we do not get the chance to play the long term game with multiple bets. Therefore we should. be looking to maximise excpected value instead. This can come from developing better models than the market which I don't have time for right now, or by still using the same Poly/Kalshi stat arb, espcially doing it fast to capture mispricings. 


Secondly,
The Rhode Island Senate have been mispriced for quite some time. And this is also true for some otehr markets, although this senate has the deepest book. This could be a few reasons icnlduing SIG intentionally mispricing it or it could be rements of the past and no one can swallow the 1 million or so susqies to break through. Either way, this mispricing will last for now,  which means this opprotunity will be here for a while. This can be thought of as a structural mispricing. I could free up capital by selling out my position to trade, and then buying back in later either before it moves or when the competiton is about to end. It is wise to monitor the liquidity wheter or not if i decide to free up my capital fully or partially , and jump in ASAP when it moves, and minimise slippage. These stale markets could also be a place to park cash instead. 


Thirdly, a better way to construct a profolio would be holding undervalued positions compared to real markets that move and correct, so I can recycle the capital rather than get locked into an good edge on paper that never converges to fair value before the end of the competition. Towards the end of the competition, my strategy should pick up a few long shots that spikes from real markets and if they come close to the actual probability, I should be fine with selling and holding some cash if there are no more opportunities. 

or A structural holds book and a fast trades book.



25th october
I have implmented the 2 book approach, fast vs holds, after spotting the sturctural mispricings that wont go away. 

The issue now is that some markets thats not mispriced may be a dead market, that is low liquidity and trading volume. So my fast strategy bot should avoid thoe markets, unless the edge is so high, its bigger than the rhode island senate edge. 

The next step is to think about what happens when a illiquid market becomes extremely underpriced, should that be a position in the fast book or the hold book?
I still wnat to capture edges in the illiquid markets. So when the fast edg clears 5c and 10c, i will allow an entry of 1/8 and 1/4 of liquidity. 

26th october

I want to make sure that when i exit my position that is not urgent i will get good fills. 
So i will rest my asks on the orderbook to capture big orders.

i define big = 4% of markets last 7-day volume, capped at 50k 