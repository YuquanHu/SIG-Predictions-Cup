Trading Bot Plan: Review and Functions

STATUS (2026-10-24): steps 0-6 below are Strategy v1, which main.py runs today. Strategy v2 (two books: structural hold + fast opportunity)
is described at the end of this file and is being built in stages. Once Stages 2-3 are in, v2 replaces the sizing, top-4 and swap rules of steps 3-5;
fetching, fair value, order execution, safety and logging stay as they are. Limits and timings in this file now match config.py and settings.json.
Steps in detail

0.	Cancel all open orders at the start of every loop, and get the filled share count from your SIG positions
1.	Test calls just reads fetch data, place a tiny order, cancel it, then buy and sell one small position. Make this one, markets number: 388 (Will the Republican Party win the Rhode Island Senate? ) on SIG’s site and buy a single yes contract then sell it at market, both at limit order at best current price. 
2.	Use a Data structure (hash table) for storing race object, then make the initial api call on Polymarket, Kalshi and SIG to fill it up. Each subsequent call just calls price and not everything else.
3.	Calculate the edge after each call by comparing with my fair value function, and use a list to store edges that makes it past a 4% edge (4 cents of edge) and beats the current minimum. Then convert the edge into Kelly criterion. Keep the top 4 kelly only in that list, so if the list is full and a better edge arrived then replace the lowest with the current edge. Then sort as needed. This should be logged and later displayed on a dashboard.
   [Superseded by Strategy v2 (see the end of this file) once Stages 2-3 are built. Today's code still follows this step.]
4.	Sort the list by Kelly. biggest first. Size = half Kelly × bankroll, investing at most 80% of total Susqies so 20% stays as cash. Subtract what is already held in that bet, so the bot doesn't keep buying every loop. Treat Dem Yes and Rep No as the same bet: buy only the cheaper one, and count both toward one limit per race.
   [Superseded by Strategy v2 (see the end of this file) once Stages 2-3 are built. Today's code still follows this step.]
5.	Decided whether or not to swap current position for new ones or use cash.
Value a held position at its sell price (the bid), since that's what selling returns. Its remaining expected return is (fair − bid) ÷ bid.
Only swap when there is no investable cash left. With cash available, just buy the new opportunity.
Swap if the new opportunity's expected return beats the held position's by at least 1%.
Extra exit rule: if a held position's fair value falls below its sell price, its edge is negative, so sell it whether anything better is available. If my current portfolio is already holding the best positions then do nothing and repeat the loop. \ This should be logged and later displayed on a dashboard.
   [Superseded by Strategy v2 (see the end of this file) once Stages 2-3 are built. Today's code still follows this step.]

6.	Execute the trade at a limit price which the edge was calculate. IF it doesn’t fill within the order time out limit, cancel, If it partially fill, cancel what didn’t fill. The timer is temporary and should be updated when entering a different regime, such as debate night or election night. This should be logged and later displayed on a dashboard. Loop pause. 




Other notes:
- every transaction should be logged and able to be displayed later. The dashboard comes later and don’t build it now. The dashboard  takes CSV file per type (opportunities, orders, fills, errors), with columns like time, race, market, side, price, fair value, edge, shares.
 
Position sizing per-race cap (30% of bankroll); single position cap 25% (v2); single order 2% of total equity; max invested 60% of total equity (settings.json)
quote staleness limit (60 seconds)
kill-switch limits (stop if down 10% in a day, or 5 errors in a row)
Regime: 
Regime	Order timeout	Loop pause	When
Slow	30 seconds	30 seconds (was 60 until Stage 4)	Overnight US time, weekends, nothing happening
Normal	10 seconds	20 seconds	Most days, polls and news coming out
Election night	3 seconds	5 seconds	Results coming in, prices moving fast



Functions (not exhaustive) 
1.	Fair value: for one outcome, take Kalshi's and Polymarket's midpoints (50/50 weighting), drop any source that is stale, failed, or has a spread over 3¢. Return nothing if no source is usable, or if the two disagree by more than about 4¢. The data should come from the Race object. 

2.	Kill switch to top all trading 

3.	Dry run, True or False, with it on, it only logs the transactions but not execute them. 

Setup
•	Load races: read the config file and create a race object for each entry, with its IDs, its group and empty prices.
•	Load setting: Includes the regime, so slow, normal or election. Also include Dry Run, where it only logs trades but not execute.
Data
•	Fetch SIG prices: get the bid, ask and available size for every SIG market, and store them in the matching race with the current time.
•	Fetch Kalshi prices: the same for Kalshi.
•	Fetch Polymarket prices: the same for Polymarket.
•	Fetch account: get current cash and every position held (market, side, shares, average price).
Strategy
•	fair_value(outcome): blend healthy Kalshi and Polymarket midpoints; return nothing if neither is usable or they disagree by more than 4¢.
•	find_opportunities(): for every SIG market, compute the edge of buying Yes and No; keep edges of 4¢ or more, with their price, edge, Kelly and available size.
•	Size trade: for one opportunity, target = half Kelly × bankroll, scaled down if all targets together exceed 80%. Subtract what is already held, then cap by the race limit, the 80% investment limit and the shares available at that price. Return the number of shares to buy, or zero.
•	keep_top(list, n=4): keep only the best 4 by Kelly, one per race (cheaper of Dem Yes / Rep No).
•	Value held position: for each position owned, its fair value, its sell price, and its remaining expected return if kept.
•	Decide swaps: if cash is used up and a new opportunity beats the worst held position by at least 1%, mark that position to sell. Also mark any position with negative edge to sell.
Execution
•	Place order: send a limit order (market, side, shares, price). In dry-run mode, only log it.
•	Cancel unfilled: after the order time limit, cancel whatever part of an order didn't fill.
•	Cancel all: cancel every open order. Used by the kill switch.
Safety and records
•	Check kill switch: return true if the stop file exists, or if losses or errors have gone past their limits.
•	Log: append a line to a file for every opportunity found, order placed, fill and error.
test_connection(): fetch prices read-only; place a tiny order and cancel it; buy then sell one small position in market 388; log every step.






Main loop and logic:
load settings and races loop: cancel all → check kill switch → fetch prices and account → find opportunities → keep top 4 → exits and swaps → size → place and cancel orders → log → wait the regime’s loop pause. On exit, cancel all.

Build in stages: (1) read-only data fetching, (2) full logic in dry run, (3) live. Stop after each stage so I can test. DRY_RUN defaults to True.”

API docs
•	Kalshi: https://docs.kalshi.com 
•	Polymarket: https://docs.polymarket.com
•	SIG: attached to as a json file
•	SIG api key leave as a placeholder, I will change it later, the other 2 don’t need api keys.


Here is an example of a race In python

races = {
    "RI-SEN": {
        # ---- fixed: loaded once from races.json ----
        "name": "Rhode Island Senate",
        "group": "Senate",

        # ---- one entry per possible winner ----
        "outcomes": {
            "D": {
                "kalshi_id": "SENATERI-26-D"
                "poly_id": " 630911”
                # live, updated every loop
                "kalshi": {"bid": 0.988, "ask": 0.993, "updated": 1792038000.0},
                "poly":   {"bid": 0.969, "ask": 0.988, "updated": 1792038001.2},
            },
            "R": {
                "kalshi_id": "SENATERI-26-R",
                "poly_id": " 630912",
                "kalshi": {"bid": 0.007, "ask": 0.010, "updated": 1792038000.0},
                "poly":   {"bid": 0.004, "ask": 0.029, "updated": 1792038001.2},
            },
        },

        # ---- the SIG markets for this race ----
        "contracts": [
            {
                "market_id": "387",       # "Will the Democratic Party win...?"
                "pays_if": "D",
                # live SIG Yes prices (No price = 1 − Yes price)
                "yes": {"bid": 0.845, "ask": 0.850,
                        "bid_size": 15000, "ask_size": 12000,
                        "updated": 1792038000.5},
            },
            {
                "market_id": "388",       # "Will the Republican Party win...?"
                "pays_if": "R",
                "yes": {"bid": 0.135, "ask": 0.140,
                        "bid_size": 21226, "ask_size": 9000,
                        "updated": 1792038000.5},
            },
        ],
    },
    # "NH-SEN": { ... same shape ... },
}




==========================================================================================
STRATEGY V2: TWO BOOKS (STRUCTURAL HOLD + FAST OPPORTUNITY)
==========================================================================================

Why
SIG prices lag the real markets in two different ways. Some gaps last for days: SIG prices a Republican win in Rhode Island's Senate race at about 13% where Kalshi and
Polymarket say about 1.3%, and it has stayed that way. Others open when a real market jumps and close over hours: on Oct 21 Kalshi, then Polymarket, moved Nevada's Democratic
governor price by 4 cents and SIG took 3 to 7 hours to follow. One rule for both kinds trades badly (the first live run lost about 84 to spread churn), so the money is split.

Books
- Structural hold book: 30% of total equity. Collects gaps that will not go away.
- Fast opportunity book: 30% of total equity. Buys lagging SIG markets after a real market moves, and sells when SIG has caught up.
- Both sit inside the 60% max invested cap (settings.json). The cap blocks new buys only and never sells.

The gap, and when it is measured
- Gap = fair value of Yes minus SIG's Yes mid price (positive: SIG Yes is cheap, so the Yes side has the edge; negative: the No side has it).
- The gap is sampled on the clock at :00, :15, :30 and :45 into a 48-hour history (192 samples), kept in .gap_history_norm.csv across restarts.
- At start-up the history is back-filled from the Kalshi, Polymarket and SIG price histories before any trading. After that the sample is taken on the first loop after each quarter hour.
  The current gap is recomputed from live quotes on every loop.
- A gap of 0 to 1 cent counts as no mispricing (noise).

Is a market structural? (decided by the median)
  M          = |median(gap over 48h)|, on the side the gap favours
  hurdle     = min(0.8 * M, M - 0.01)
  structural = M >= 3 cents
               and 10th percentile of the 48h gap >= hurdle
               and median(gap over the last 2h) >= hurdle
  fast part  = executable edge now - M      (M by its size, whichever way the usual gap points; 2026-10-28)
- A market needs at least 96 of its 192 samples, otherwise it is unclassified and gets no fast entry (a structural gap would otherwise look like a fresh fast move).
- A market can be both: a market that sits at 4 cents of edge and spikes to 8 on news has 4 cents structural and 4 cents fast.

Structural hold book
- Five buckets by the fair probability of the side being bought. One position per bucket, chosen by expected value per dollar (ROI = edge / price). No Kelly.
  (2026-11-03) Chosen by z = edge / sqrt(p (1 - p)) instead: the ROI over the SD of the bet's return (the per-bet Sharpe), the same at any price.
  ROI favoured longshots (SENATE-CONTROL 153 No at 0.195 was a pick with a 38.5% ROI and z 0.17).
  (2026-11-03) Up to `HOLD_SLOTS` positions per bucket (sure bet 3, favourite 5, coinflip 4, unlikely 6, longshot 6), each an equal
  slot of the bucket's share; an empty slot stays cash. The counts give every bucket about the same dollar risk: slots ~ (the bucket's
  SD with one bet / the sure bet's)^2. Held bets (hand buys too, once adopted; any contract of the same bet) take their slots first,
  best z at the sell price first, and are topped up only when they qualify; the ones over the slots are never topped up. Loop 1 of a run
  is built from the held bets alone; from loop 2 a new bet fills a free slot, or replaces the weakest held bet (those over the slots first)
  when it has `HOLD_SWAP_Z` (0.25) more z, and that bet's hold lots are sold at the bid. Frozen bets, dust sales and a market bought a
  moment ago are not swapped.

  Bucket       Chance       Share of hold book    Share of total equity
  Longshot     under 20%    2%                    0.6%
  Unlikely     20 to 40%    10%                   3%
  Coinflip     40 to 60%    15%                   4.5%
  Favourite    60 to 90%    33%                   9.9%
  Sure bet     90% and up   40%                   12%

- Minimum ROI per bucket (`HOLD_MIN_ROI`, added 2026-10-31): sure bet 6%, favourite 10%, coinflip 15%, unlikely 20%, longshot 25%. A candidate below its bucket's floor is never picked, so the bucket stays cash.
  (2026-11-03) Replaced by one minimum z, `HOLD_MIN_Z` = 0.25, in every bucket: the minimum ROI is then `Z sqrt(p(1-p)) / (p - Z sqrt(p(1-p)))`
  (`hold.roi_floor`): 6% at p 0.95, 17% at 0.75, 33% at 0.5, 62% at 0.3, 300% at 0.1.
  (2026-11-03, later) Raised to `HOLD_MIN_Z` 0.30 and `HOLD_MIN_EDGE` 5c (was 3c; it binds only above p ~0.95): 5.3% at p 0.99, 7% at 0.95, 21% at 0.75, 43% at 0.5, 85% at 0.3. The old table asked for less edge per unit of risk
  the riskier the bucket (z 0.25 -> 0.07 at the midpoints).
  (2026-11-03, latest) `HOLD_MIN_Z` back to 0.25, `HOLD_MIN_EDGE` stays 5c: 5.3% at p 0.99, 6.1% at 0.95, 9.1% at 0.90, 16.9% at 0.75, 33.3% at 0.5, 61.8% at 0.3.
- Risk must pay: a riskier bucket only picks a candidate whose ROI is above the best pick of every more certain bucket (taking more risk for the same or less return is pointless). Otherwise the bucket stays in cash.
- Entry: the market is structural (or, since 2026-11-01, has an executable edge of `HOLD_BIG_EDGE`, 10 cents: held to resolution the whole edge is earned, so a gap that only just opened still counts; it needs a classified gap and the side the usual gap favours), the executable edge (fair minus the ask, or bid minus fair for No) is at least `HOLD_MIN_EDGE` (5 cents since 2026-11-03, 3 before), and the book has enough shares on offer.
  A race with three or more outcomes (an independent candidate) needs "rules_checked": true in races.json first, because SIG's wording for the party contract
  may or may not count an independent (the Montana Senate case).
- Size: the bucket's share minus what is already held, limited by the 2%-of-equity single order and the 25% single position cap.
- Staying power: the held pick is replaced only when a challenger beats its ROI by 3% (config), bucket edges have a 2% band, and the cooldown applies, so ranking noise does not cause churn.
  (2026-11-03) By 0.10 more z (`HOLD_SWITCH_Z`).
- Exit: only when the edge turns negative (fair value falls below the sell price). The invest cap never forces a sale.
- Top-up freeze (2026-11-01, both books): a bet held `TOPUP_FREEZE_DROP` (10c) or more below its average entry, at its sell price, is not bought by
  either book until `TOPUP_FREEZE_HOURS` (6) after the last loop it was that far down. Nothing is sold: if fair fell with SIG the position keeps its
  edge, and selling would only lock the loss in. What it stops is averaging down when SIG falls and fair does not, which can be a stale fair value.
  The hold pick stays the pick (want 0), so the bucket does not fill with another market meanwhile. Kept in `.ledger.json` (`frozen`).
- Positions held before v2 are grandfathered: kept as hold positions, not topped up, not sold just to fit one-per-bucket.
- A bucket with no structural candidate stays in cash (open decision below).

Fast opportunity book
- Enter when fast part >= x cents (config FAST_MIN_EDGE, 3c in config now; 5 cents was planned, see open decisions) and the SIG spread is at most 3c (FAST_MAX_SPREAD) and fast part minus the spread is still above the 1c catch-up line. Rank by fast part / price.
  (2026-11-03) Rank by the fast part itself, in cents, and swap only for `FAST_SWAP_Z` (0.25) more z of the fast part (fast part /
  sqrt(p (1 - p)), the hold book's z: a swap's cost is cents, but fair-value noise is worth fewer z on a longshot than on a near-certain bet): the noise of a fair value
  is about the same in cents at any price, so 4c on a 0.20 longshot ranked first by ROI was mostly noise.
- At most 5 positions, each at most 20% of the fast book (a fixed slice, 6% of equity), still limited by the 2% single order, the 25% position cap, the 30% race cap and the shares on offer. Never the other side of a market the bot already holds. No `rules_checked` gate (that is hold only).
- Unwind when SIG has caught up to the structural gap, not to fair value: when (fair - sell price) - M falls to 1 cent or less.
- Also unwind after 24 hours, or early when the book is full and a better fast candidate beats the worst held one by 1% (the swap floor, the check that the replacement buy
  can happen, and the cooldown all apply).
- (2026-11-02) Election window: SIG marked favourites down while Kalshi/Polymarket stayed put, an edge earned at resolution. Until
  `FAST_HOLD_UNTIL` (5 Nov 12:00) the fast book sells only on a negative edge or a swap (no time stop, caught-up, patient or early exit;
  resting ones are cancelled), the usual gap is frozen at `BASELINE_FROZEN_AT` (2 Nov 00:00, before the shift), and `SWAP_MARGIN` is 3%.
- (2026-10-29) A position resting for a patient exit keeps its slot and can be swapped out; with patient exits on, a swap cancels that exit and sells by
  steps: a limit at the best bid resting 5 minutes (SWAP_STEP_MINUTES), then the next bid level down for what is left, until sold.
  (2026-11-02) Each loop the steps go on only while the best fast entry of another bet has FAST_SWAP_Z more z (of the fast part, since 2026-11-03) than the
  position still does at the step's price; a step down raises what it earns, so the sale can end itself. Then the resting step is cancelled
  and the position is kept, slot included.
- (2026-10-29) Early exits (EARLY_EXITS, off by default): from entry all fast shares are listed at the structural value (fair - usual gap) for 24h,
  re-listed when it moves more than 1c or after a top-up, held back while the fast part left is still the entry edge or more (the bot buys or sells a position, never both); then a patient exit until the time stop (+60 min), then the bid. One resting sell per position.

Shared safeguards
- 2% of total equity per order, 25% per position, 30% per race, 60% invested, kill switch (10% daily loss or 5 errors in a row), STOP file, 5-minute cooldown against flip-flopping.
- A ledger (ledger.json) records the lots of each book, because SIG reports only one net position per market and both books can hold the same market.
- Loop timing: normal 10s timeout / 20s pause is the default; the slow regime pause is 30s (was 60s) so a fast move is noticed within about a minute overnight. Reads stay at about 11 a loop at most, so slow and normal sit far under the budget.

Build stages (one at a time; each stops for review; DRY_RUN stays True and the user types LIVE)
  Stage 1  Gap model, history back-fill, structural report, replay            DONE (155 tests pass)
  Stage 2  Ledger and hold book (buckets, ROI pick, grandfathering)            DONE (183 tests pass; run with main.py --strategy v2, v1 stays the default)
  Stage 3  Fast book (entry, unwind, time stop, early swap, fills returned)    DONE (223 tests pass; part of main.py --strategy v2)
  Stage 4  Display and logs polish, timings, docs, live checklist                DONE (231 tests pass)

Stage 1 findings (2026-10-24)
- 233 contracts classified: 63 structural, 126 with a gap of 1 cent or more that is not structural, 44 with no mispricing.
- Biggest usual gaps: RI-SEN 388 No 11.9c, RI-SEN 387 Yes 10.9c, RI-GOV 385 No 8.2c, TN-SEN 362 No 7.5c.
- All 63 structural markets are in two buckets: 52 sure bets and 11 favourites. None are in longshot, unlikely or coinflip.
- No market has a 5-cent fast part today; the best is HI-GOV 301 Yes at +2.6c.
- Replay, half-spread 0.5c: at x = 3c the bot would have bought Nevada Yes at 11:40 ET on Oct 21 and exited at 15:40 ET, +3.5c per share in 4 hours.
  At x = 4c and 5c Nevada would not have traded; Maine had no entries at any x. One real event, so treat it as a hint.
- Replay bug fixed: a usual gap pointing the other way must not count as a fast part (now floored at 0).

Stage 2 findings (2026-10-24)
- Decided: existing positions are grandfathered and the user sells down RI-SEN 388 (30.4% of equity) and DE-SEN 386 (20.7%) by hand; an empty bucket stays cash.
- Those two positions already put the hold book at about 57% of equity against its 30% budget, so the hold book buys nothing until they are sold down.
  The dry run shows: sure bet pick RI-SEN 388 NO (ROI 12.6%, holds 31,042 vs a target of 12,266), favourite pick MS-SEN 272 NO (ROI 6.3%, nothing held), the other three buckets
  have no structural candidate, no orders. The fast book has about 2.7k of room under the 60% cap until the sell-down.
- The ledger (.ledger.json) adopted the six positions as grandfathered hold lots and remembers each bucket's pick; selling by hand shrinks the lots on the next loop.
- Races with an independent candidate (10 races) are kept out of the hold book until "rules_checked": true is set in races.json (RI-GOV 385 and MT-SEN 274 are in that group today).

Stage 3 findings (2026-10-24)
- Dry run on the real account: one fast candidate, HI-GOV 301 Yes (fast part +3.0c at x = 3c, usual gap +4.8c); the fast book would buy up to a 6,135 slice, cut to the 1,243 of cash the 60% cap leaves.
- A dry run never creates fast lots (nothing fills), so the unwind, time stop and swap only run in tests until the first live fills. The ledger books fills after each loop's orders (`Ledger.record_fills`).

Stage 4 findings (2026-10-24)
- v2 start-up check shows the books, hold and fast entry rules instead of the v1 minimum edge. The v2 report shows ROI instead of Kelly and a hold / fast share split per holding. `opportunities.csv` has a new `fast_part` column (use it to calibrate x).
- Fast liquidity check (2026-10-25, `liquidity.py`): a fast entry needs known traded volume, at least 5,000 SUSQies since yesterday (UTC) and 1M all time; 1M to 5M gets half a slice, 5M+ a full one. Volumes come from SIG's daily candles, cached in `.volume_cache.json` (12h). See updates.md.
- Backtest (2026-10-26): `analysis/backtest_data.py` (cache), `analysis/backtest_v2.py` (replays `make_plan_v2` from Oct 1 on 5-minute steps, `--grid` / `--robust`), `backtest_metrics.py`, `backtest_plots.py`. Results and caveats in updates.md.
- Patient exits (2026-10-27, off by default): `config.PATIENT_EXITS`; `bigorder.py` (big-order size, rest price, exit fill), `patient.py` (PatientBook), resting orders in `trader.py`; swap pricing walks the bids within 1c. See updates.md.
- The slow regime now pauses 30s. Reads per loop are unchanged (about 11 at most); writes are only spent on orders.

Live checklist for v2 (you run it; I never run main.py live)
  1. Tests pass: python3 -m unittest discover -s tests -t .  (expect 231).
  2. Sell RI-SEN 388 and DE-SEN 386 down by hand first (or accept that the hold book buys nothing until you do). The ledger notices on the next loop.
  3. python3 structural_report.py: markets you care about have 96+ samples and look sane.
  4. python3 main.py --strategy v2 --once with DRY_RUN True: read the Hold book and Fast book tables, the Orders section, and logs/orders.csv (book column).
  5. Decide x (config FAST_MIN_EDGE) and which independent-candidate races to mark "rules_checked".
  6. Only then set DRY_RUN = False yourself and start with the slow regime and a quiet market. Type LIVE at the check.
  7. Watch the first fills: the ledger (.ledger.json) should gain a "fast" or "hold" lot for each fill, and the next loop should show no "adopted" note. The unwind, time stop and swap have only run in unit tests, so watch the first fast position until it closes.
  8. Keep the STOP file command ready (touch STOP). The kill switch (STOP file, daily loss, 5 errors in a row) cancels every open order on the account; Ctrl+C leaves the resting orders on SIG and the next start follows them again (.resting.json).

Open decisions
1. x: config is at 3 cents now (5 was planned); the replay supports 3 and shows nothing at 4 or 5 on the one real event. Calibrate on more events before going live.
2. Which of the 10 independent-candidate races to mark "rules_checked": true after reading SIG's wording on its website.

Fair value is scaled per race (added after Stage 4): the fair values of a race's outcomes are divided by their total so they add up to exactly 1 and both sides of the same bet
(Dem Yes / Rep No) share one fair value. It is not done when an outcome has no value or the total is more than 5c from 1 (a candidate is probably missing; the warning still prints on the unscaled numbers).
The back-filled history is scaled the same way at every sample time, and lives in .gap_history_norm.csv (the old .gap_history.csv held unscaled gaps and is no longer read).

Where the code and numbers live: config.py (BUCKETS, REGIMES, FAST_MIN_EDGE, STRUCTURAL_MIN_GAP, NOISE_TOLERANCE, HISTORY_HOURS), settings.json, gapmodel.py,
backfill.py, structural_report.py, analysis/replay_fast.py, and updates.md for the dated log of what was found and why.

## Election-night guard (2026-11-03)
`guard.FairGuard.apply` runs after `compute_fair_values` only when `REGIME` is `election` (main.run_loop). Per race: `pause.json` (no fair value),
`calls.json` (the called outcome at 0.99 if a source has it at 0.90+; full size; skips the rest), freshness (Polymarket fetched within 15s, else the race is
skipped; Kalshi is optional, 2026-11-04: without it the race trades on Polymarket), the breaker (a fair move over 15c in 60s backed only by Kalshi pauses it 3 min; with two
sources the 4c agreement rule already voids a one-source spike, and Polymarket alone is trusted), and persistence (a changed fair is used after 2 loops within 3c). Each fair
carries an uncertainty s = max(1c, source disagreement, half the widest source spread, 0.5 x its 60s move); `Opportunity.z` and the fast
gate use `edge - ELECTION_K x s`. Buys in races not called get `ELECTION_UNCALLED_SIZE` (0.5) of the order cap. Exits and the 4s election order
timeout are unchanged.
