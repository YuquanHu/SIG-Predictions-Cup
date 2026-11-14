# SIG Predictions Cup trading bot

Read `plan.md` before any work. It defines the stages; build one stage at a time and stop after each so the user can test. `DRY_RUN` defaults to True and no orders are placed until the live stage.

## Code quality review (standing instruction)

After writing or changing code, and before reporting it as done, review the changes against
`claude skills/thermo-nuclear-code-quality-review/SKILL.md`. Read that file in full each session.
Fix the structural problems it flags (spaghetti branching, needless wrappers, logic in the wrong
module, duplicated helpers, vague data shapes), keeping behavior the same, and re-run the affected
script. In the final message, say briefly what the review found and changed.

## Live trading (stage 6)

`DRY_RUN = True` in `config.py` stays the default. Never set it to False, send a real order, or run `main.py` live yourself:
the user does that, after typing `LIVE` at the start-up check. Live order handling (`trader.py`, `LiveTrader`) has only ever been
tested against a fake client; real writes have only been made by `test_trade.py` (1 share, "yes" typed before each).

The default strategy is v1 (top 4 by Kelly). `main.py --strategy v2` runs the two-book strategy (structural hold + fast opportunity, see the end of `plan.md`); its ledger of lots is `.ledger.json` and its gap history `.gap_history_norm.csv` (both git-ignored). `LiveTrader.execute` returns the fills and v2 books them in the ledger; a dry run never fills, so the fast unwind paths are only exercised by unit tests until live.

Patient exits (`PATIENT_EXITS` in `config.py`, default False) are the one case where the bot leaves an order on SIG between loops: a fast
position whose remaining fast part falls below 3c rests one sell above the bid (where one big order, 3% of the 7-day volume, fills it) until its time stop (`FAST_MAX_HOURS` from its first entry) plus
`PATIENT_MAX_MINUTES`, then it sells at the bid (`bigorder.py`, `patient.py`, `LiveTrader` resting orders, whose `expirationDate` is that time plus
5 minutes, a safety net that can be days away). With the switch on, a swap also rests: a swapped-out fast position (resting or not; a resting
position keeps its slot) has its patient exit cancelled and sells by steps, a limit at the best bid for `SWAP_STEP_MINUTES` (5), then the next bid
level down for what is left (`PatientBook` step state), each loop only while the best fast entry still has `FAST_SWAP_Z` more z than the
position does at the step's price (`fast.ended_steps`; else the step is cancelled and the position kept, `PatientBook.end_steps`). Early exits (`EARLY_EXITS`, default False, needs `PATIENT_EXITS`): from its
first entry a fast position lists all its fast shares at the structural value (fair - usual gap), at most 3 ticks above the best ask and just in
front of a liquidity wall (an ask level of 1,000+ SUSQ and 3x its neighbour) when that is 1c+ over the entry price (`bigorder.wall_price`), for
`EARLY_EXIT_HOURS` (24), re-listed when the structural value moves more than 1c from its value at listing or new shares are bought, held back (not listed, a resting listing cancelled) while the fast part left is still `FAST_MIN_EDGE` or more, since the bot buys or sells a position, never both, then a patient exit rests until the time stop whatever the fast part. SIG's order book contains the bot's own resting orders: `sig.fetch_depth` takes them out (`PatientBook.resting_orders`) and `PatientBook.drop_self_matches` drops an order that would trade against one. One resting
sell per position, never two. A live run keeps the book in `.resting.json` (git-ignored): Ctrl+C leaves the resting orders on SIG so they keep their queue place, and the next start re-adopts them (`PatientBook.load`, `close_down` in `main.py`); the STOP file, the daily-loss limit, 5 errors in a row and a crash cancel every order and empty the file. Never turn `EARLY_EXITS` on yourself either. Patient entries (`PATIENT_ENTRIES`, default False, needs `PATIENT_EXITS`): a fast or hold buy
(a new position or a top-up) on a spread over 0.5c rests its buy one tick above the best bid for `PATIENT_ENTRY_MINUTES` (10; 5 for a fast part, hold: edge, over 10c; none over 15c, and a resting bid is cancelled once its gap passes 15c, its hold bet is no longer a pick or is frozen), then buys at the ask, except that on a spread over `PATIENT_REBID_SPREAD` (1.5c) a bid that ended unfilled is bid again, up to `PATIENT_MAX_BIDS` (3) in a row (a full fill starts over; after the last the market buys at the ask and does not bid for an hour); one bid per market; its fills reach the
ledger as lots of the bid's book through `Ledger.reconcile`'s `entry_buys` (hold lots take the bucket of the pick). Never turn it on yourself. They have only ever been tested against the fake client. Never turn the switch on yourself.

Risk limits are in `settings.json` (max_invested 0.60, per race 0.30, position 0.25, hold book 0.30, fast book 0.30, single buy 2% of total equity (cash + positions), daily loss 10%, 5 errors in a row).
Election window (2026-11-02, `config.py`): `BASELINE_FROZEN_AT` freezes the usual gap at 2 Nov 00:00 and `FAST_HOLD_UNTIL` (5 Nov 12:00)
makes the fast book hold to resolution (exits only on a negative edge or a swap); both are `None` when not in use. The bot starts `caffeinate` on macOS.
The hold book picks by z = edge / sqrt(p(1-p)) with one minimum `HOLD_MIN_Z` in every bucket, up to `HOLD_SLOTS` positions per bucket (loop 1 of a run builds the book from the held bets alone, hand buys too; from loop 2 a new bet with `HOLD_SWAP_Z` more z than the weakest held bet, those over the slots first, replaces it and the held bet is sold at the bid; a frozen bet is never sold for it; an empty slot stays cash); the fast book ranks and swaps by the fast part in cents
(swaps by z: `FAST_SWAP_Z`; `SWAP_MARGIN` is v1's ROI margin). Election guard (`guard.py`, only when `REGIME = "election"`): Polymarket fetched over 15s ago skips the race (Kalshi is optional and silent: when it settles or drops out the race trades on Polymarket alone), a changed fair is used after 2 loops within 3c, a fair move over 15c backed only by Kalshi pauses the race 3 min, `z` takes `ELECTION_K` x the fair's uncertainty off the edge, buys in races not in `calls.json` get half the order cap, and `pause.json` leaves races alone. `calls.json` ({"RI-SEN": "D"}) and `pause.json` (["TX-15-HOUSE"]) are hand-edited and read every loop. The exit rule is unchanged.
There is no price stop loss: v2 freezes top-ups (both books) of a bet held 10c+ below its average entry for 6h after it was last that far down
(`TOPUP_FREEZE_*`, `Ledger.freeze_falls`, kept in `.ledger.json`) and sells nothing for it. The invest cap blocks new buys only and never sells; sells are exempt from the single-order cap. The kill switch (not Ctrl+C) cancels
every open order on the account; each loop only cancels the bot's own orders.
