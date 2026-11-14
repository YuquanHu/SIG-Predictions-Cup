# Updates log

What I found, what went wrong, and how it was fixed. Each entry says what is still open.

---

## 2026-11-04: "Top opportunities" is the last table of a loop, ranked by raw edge

**Changed (display only):** `display.print_report` no longer prints the opportunities; `display.print_opportunities` does, and `main.run_loop` calls it last
(after the orders, the loop line and, in v2, the sides table). Rows are sorted by raw edge (`Opportunity.edge`, fair less price, before any election haircut),
biggest first, whatever order the planner ranks them in (v1: Kelly; v2: the wants). Which opportunities appear, and what the bot trades, is unchanged.
Tests: `Report` in `tests/test_stage4.py` (3 mutations caught).


## 2026-11-04: Polymarket is the fair value's source; Kalshi dropping out is silent

**Why:** a Kalshi market that settles early (or a failed request) left an outcome with a Polymarket quote only, and the election guard skipped the
whole race ("a source is older than 15s or missing") exactly when the result was known; `kalshi.py` also printed and logged a warning per outcome.
**Changed:** `guard.fresh_sources` now returns the sources an outcome needs that are missing: Polymarket when the outcome has a market there (Kalshi is never
needed), else every source it has. A race with no fresh Kalshi trades on Polymarket alone; one without fresh Polymarket is still skipped, even with
Kalshi. The breaker no longer pauses a move that Polymarket alone backs (persistence still holds a changed fair for 2 loops); a Kalshi-only outcome is
still paused. `kalshi.fetch_prices` says nothing when the request fails, a ticker is not returned or a market has no two-sided quote: the outcome has no Kalshi
quote and the fair value's note shows it was dropped. Both-sources behaviour is unchanged (50/50 blend, 4c agreement).
**Not changed:** the race cap for a fair resting on one source (`SINGLE_SOURCE_RACE_CAP` 15%, set by you) still applies to these races, as does the half order
size in uncalled races. A total Kalshi outage is now invisible apart from the fair notes. Tests: `KalshiDropsOut` in `tests/test_guard.py` and the Kalshi
fetch tests in `tests/test_coverage_rules.py` (6 mutations caught).


## 2026-11-04: wider entry spreads, and a count of the sides taken

**Why:** election night spreads can go from tight to wide fast, and the best edges sit in the wide-spread markets. **Changed (config only):**
`HOLD_MAX_SPREAD` 3c -> 5c (the hold book holds to resolution, so the exit spread is not paid) and `FAST_MAX_SPREAD` 2c -> 3c (the fast book also holds to
resolution until `FAST_HOLD_UNTIL`, but a swap or negative-edge exit still sells at the bid). The Kalshi/Polymarket limits (`MAX_SPREAD`, `MAX_DISAGREE`,
`ELECTION_STALE_SECONDS`) are unchanged: they are the only check on the fair value. `HOLD_MAX_SPREAD` is now pinned in `tests/__init__.py` at its old 3c.
**Added:** `lean.print_lean`, the last thing a v2 loop prints: bets held by office (House, Senate, Governor, then any other group such as Control) and
party (Dem, Rep, and "other" only when an independent or a No in a race of three or more is held), each cell `held (+n)` for the new bets this loop's
buys or resting bids start. A bet is (race, the outcome it wins on), so Dem Yes and Rep No on one race count once, a top-up adds nothing and sells are
ignored (a bet sold this loop still shows as held). In a dry run nothing fills, so the `(+n)` is what the loop would start. A second table sits next to
it in SUSQies: the held bets' value (shares x sell price, the same as `invested` in the header) by office and party, each cell `held (+buys)` where the
buys add every buy's value, top-ups too. Tests: `tests/test_lean.py` (11 mutations caught).


## 2026-11-03: patient entries are bid again on a wide spread

**Changed (user's rule, both books):** a patient bid that ended without filling in full (timed out, part filled, or cancelled) used to make the
market buy at the ask for an hour. Now, while the SIG spread is over `PATIENT_REBID_SPREAD` (1.5c) and the bet has had fewer than
`PATIENT_MAX_BIDS` (3) bids in a row, it is bid again; the third bid is followed by the ask and an hour without a bid (`ENTRY_MEMORY`). A bid that
fills in full starts the count over. The gap limit is the existing `PATIENT_ENTRY_MAX_PART` (15c: above it no bid at all), and the bids last
10 min under 10c and 5 min from 10c to 15c. `PatientBook._entered` is now `(bids ended unfilled, when the last ended)` (a saved file with only a time counts one bid;
`ended_bids` replaces `entered`). **Spread read:** the second plan of a loop uses fresh order books with the bot's own orders taken out
(`sig.fetch_depth`), so a re-bid sees the true spread, not one narrowed by its own old bid. **Costs to watch:** a bid that does not fill holds a fast
slot and its budget for up to 3 x 10 min and cannot be swapped out (only held positions can); the hold slot is idle meanwhile. 522 tests; 9 mutation checks, all caught.

---

## 2026-11-03: patient entries for the hold book too

**Changed (user's request):** with `PATIENT_ENTRIES` on (still False), a hold buy (new or top-up) on a spread over one tick rests one tick above
the best bid like a fast one. The shared rule moved to `trading.py` (`entry_minutes`, `patient_bid`, `rest_want`). **Bands:** the wait follows the
hold bet's *edge* (fair - ask): up to 10c 10 min, 10-15c 5 min, over 15c none (`PATIENT_ENTRY_*`); the fast book still uses its fast part.
**Rules:** one bid per market (a hold buy is not bid where a fast bid rests or is planned this loop, and the reverse); a resting hold bid keeps the
pick's slot, gets no second buy and counts against the hold budget (`PatientBook.entry_value("hold")`); it is cancelled when its bet is no longer a
pick, is frozen, or its edge passes 15c (`hold.stale_bids`, joined to the fast book's in `Plan.stale_entries`). **Booking:** fills become lots of the
bid's book (`Ledger.reconcile(entry_buys)`, `PatientBook.take_entry_buys`: (price, book)); hold lots take the pick's bucket. `.resting.json` from before
loads its settled entries as fast. The order-closed note now says "bought" for a bid. **Untested live:** only the fake client; the same open risk as the fast
entries (a bid can miss a gap if SIG catches up, here for a hold edge). 513 tests; 9 mutation checks, all caught.

---

## 2026-11-03: hold report gets an edge column, shorter notes

**Changed (display and wording only):** the hold report has an `edge` column just before `ROI` (fair minus the ask for a pick, minus the sell
price for a held bet). The notes are shorter: "buy" (the amount is in `to buy`), "no buy: held of target, limit", "z 0.20 is under the 0.25
minimum (ROI 12%, 33% needed)", "loop 1: held bets only", "N of M slots empty: cash", "no candidate: cash". The bucket name is no longer
repeated in a decision (it has its own column). Tests follow the new wording.

---

## 2026-11-03: patient entry bands widened

**Changed (user's rule, config only):** the fast part bands are now up to 10c: bid rests 10 min (`PATIENT_ENTRY_MINUTES`); 10-15c: 5 min;
over 15c (`PATIENT_ENTRY_MAX_PART`): no patient entry, buys at the ask at once (a resting bid is cancelled once its part passes 15c).
Was 8c / 10c (`PATIENT_ENTRY_SHORT_PART` 0.08 -> 0.10, `PATIENT_ENTRY_MAX_PART` 0.10 -> 0.15). `PATIENT_ENTRIES` is still False; the bands
only apply once you switch it (and `PATIENT_EXITS`) on. A wider band means a bid can miss a bigger gap if SIG catches up before it fills.

---

## 2026-11-03: HOLD_MIN_Z back to 0.25 (config only; edge floor stays 5c)

User's call, later the same day. Minimum edge / ROI now: p 0.99 5.0c / 5.3%, 0.95 5.4c / 6.1%, 0.90 7.5c / 9.1%, 0.75 10.8c / 16.9%,
0.50 12.5c / 33.3%, 0.30 11.5c / 61.8%, 0.20 10.0c / 100%. Held bets keep their slots; nothing is sold by this change.

## 2026-11-03: hold minimums raised (HOLD_MIN_Z 0.30, HOLD_MIN_EDGE 5c)

**Why:** the sure-bet minimums were too low across the board (3c / 3.1% ROI at p 0.99), and at 99% z is oversensitive: a 0.2c fair difference
moved z by 0.13 (RI-SEN 388 vs VA-SEN 364 swap). **Changed:** `HOLD_MIN_Z` 0.25 -> 0.30 and `HOLD_MIN_EDGE` 0.03 -> 0.05 (config only).
The edge floor binds only above p ~0.95, so the other buckets are set by z alone. Minimum edge / ROI: p 0.99 5.0c / 5.3%, 0.95 6.5c / 7.4%,
0.90 9.0c / 11.1%, 0.75 13.0c / 20.9%, 0.50 15.0c / 42.9%, 0.30 13.7c / 84.6%, 0.20 12.0c / 150%. `hold.roi_floor` now includes the edge floor.
Held bets below the new minimum are not topped up but not sold either (sells come from swaps and the negative-edge exit only). A higher
sure-bet minimum can raise the ROI hurdle that riskier buckets must beat (dominance rule).
**Open:** the z oversensitivity at 99% is not fixed (a cap on p in the divisor was offered).

---

## 2026-11-03: election-night guard on the fair values (guard.py)

**Proposal reviewed:** nine per-loop rules for election night. Kept, adapted or dropped against the code:
- *Freshness 15s:* neither feed has a quote timestamp (`updated=now` is the fetch time; Kalshi's `updated_time` was days old), so it is the fetch
  age. A fetch of all 115 races takes 0.7s, so the rule only bites when a source fails to fetch (Kalshi connection resets are common in errors.csv).
  A race is skipped only when a source the outcome is meant to have is stale or missing; an outcome with one exchange id by design still trades.
- *Agreement 4c:* already `MAX_DISAGREE`.
- *Breaker (pause any >15c move for 3 min):* changed. A real call moves both sources, so it would have cost the first three minutes of every
  call. And with two sources that agree within 4c, a one-source spike already has no fair value, so the breaker only covers an outcome with a
  single source.
- *Persistence, calls.json, pause.json, z_adj, half size:* added (`guard.py`, `Fair.uncertainty`, `Opportunity.adj_edge`, `cap_order_sizes(scale)`).
- *q_max and a 2s timeout:* not added. Orders are already limits at the planned ask, and logged fills take 3s or more, so 2s would cancel fills.
- *Exits (5c for 5 min):* not changed. A 5-minute wait is long if a real adverse call collapses the SIG bid.

**Hand files:** `calls.json` {"RI-SEN": "D"} (the call is ignored with a note unless a source has the outcome at 0.90 or more, so a typo cannot
become a full-size buy) and `pause.json` ["TX-15-HOUSE"] (no buys, swaps or automated sells there). A missing file is silent; a broken or
wrongly shaped one is ignored with a note in the loop output, so you are not left thinking a race is paused.
**Replay:** scripted prices: a one-source spike gives no fair value, a two-source jump is used on its 2nd loop (0.55 -> 0.90), a single-source
jump pauses 3 min, a call gives 0.99, a call the markets contradict is ignored, a pause removes the fair. Live prices: 240 of 240 outcomes
pass, quote ages 0.3s at most, uncertainty median 1c, p90 2c. Tests: `tests/test_guard.py` (config values swept) and 13 mutation checks.

**Watch:** after a real 35c move s is 17.5c for the next 60s (0.5 x the move), which cuts the edge by that much; lower `ELECTION_MOVE_WEIGHT`
if it makes the bot too shy right after a call. Persistence delays a real move by 2 loops (about 20s).

---

## 2026-11-03: the hold book swaps held bets (HOLD_SWAP_Z 0.25) and builds from the held bets in loop 1

**Changed (reverses "held bets are never replaced"):** in loop 1 of a run the hold book is built from the bets it holds alone: each
bucket's held bets, best z at the sell price first, take its `HOLD_SLOTS` (the rest are "over the slots": kept, never topped up, nothing
sold for it) and no new bet is considered. From loop 2 a new bet fills a free slot, or replaces the weakest held bet (those over the
slots first) when its z is `HOLD_SWAP_Z` (0.25, as the fast margin) above that bet's z at its sell price; the replaced bet's hold lots
(not a fast position in the same bet) are sold at the bid with a `swap:` reason. Not swapped: a bet frozen by the top-up freeze, a dust
sale (under `MIN_SWAP_PCT` of equity) and a market bought within the cooldown. The displaced bets' books are read in the first pass
(`Plan.fast_depth`), or a bulk price with no bid depth would look like dust and the sale would never happen. `HOLD_SWITCH_Z` (0.10) is
still the margin between two new bets. `main.run_loop` passes `first_loop=loop_no == 1`.

**Replay (real positions, 106k equity):** loop 1: sure bet RI-SEN, OK-SEN, AR-SEN kept; NY-GOV, HOUSE-CONTROL, AL-GOV over the slots
(held, no top-up); TX-15 and NE-SEN held. Loop 2 with the books read: sells HOUSE-CONTROL 152 (9,940 sh, z 0.47) for DE-SEN 386
(z 1.24) and NY-GOV 184 (2,184 sh, z 0.69) for VA-SEN 364 (z 1.16); AL-GOV and AR-SEN are dust, so they stay and the bucket holds
more than its slots. Tests: `HoldSwaps`, `PlanSwaps` in `test_hold` (margins swept from config), eight mutation checks.

**Open:** a swap sells at the bid on a 4-5c spread and the new bet buys at the ask; the buy is limited by `max_invested` and the hold
budget (37.5%), so a sale can free cash that the book does not spend at once.

---

## 2026-11-03: the fast swap margin is in z (FAST_SWAP_Z 0.25)

**Changed:** a fast swap (and the step check that ends one) compares the z of the fast part, `fast.part_z(fair, part)` =
`strategy.z_score(fair, fair - part)`, and needs `FAST_SWAP_Z` = 0.25 more z than the worst held position. It replaces
`FAST_SWAP_MARGIN` (3c of fast part), which was the same number of cents on a 0.95 bet and a coinflip. Entries are still ranked by
the fast part in cents. **Why 0.25:** a swap pays the spread twice (about 2.5c) and each fair carries about 1.5c of noise (two of them
about 2c), so the gain must be at least 4.5c: 0.2 z on a 0.95 bet, 0.15 on a coinflip, 0.7 on a 0.99 bet. 0.25 is 5.4c at 0.95, 8c at
0.8, 12c at 0.5, and above the cost everywhere short of the near-certain end. **Hold book:** `HOLD_SWITCH_Z` stays 0.10: it only
chooses between bets not yet held (nothing is sold), and a held bet is never replaced. v1 keeps `SWAP_MARGIN` as an ROI.
Tests: `test_fast.Swap` and `test_swap_steps` are built from `FAST_SWAP_Z` (swept), a coinflip with more cents but less z is swapped
out for a safer entry; each mutation-checked.

---

## 2026-11-03: held bets come first in the hold book (RI-SEN, OK-SEN)

**Found:** RI-SEN 388 and OK-SEN 383 (sure bets, fair 0.99 / 0.985) were not in the hold book. Only an eligible candidate could
take a slot, and tonight's spreads (5-6c, over the 3c `HOLD_MAX_SPREAD`) made both ineligible; RI-SEN's cheaper equivalent contract
(387 YES, the same D win) was not counted as held; and a challenger with HOLD_SWITCH_Z more z could take a held bet's slot.

**Changed:** `hold.held_bets` lists the hold book's bets by bet (race + winning outcome, so any equivalent contract), in the bucket
their lots were placed in. They take their bucket's slots first and are never replaced, whether or not they qualify this loop; one is
topped up only through a qualifying candidate of the same bet (any of its contracts), otherwise its report row says why ("held, no
top-up: spread 5.0c is too wide"). New bets get the slots left (`fill_slots`, last loop's picks first, HOLD_SWITCH_Z between new
bets). The planner passes the held bets and the candidates (for the reasons) to `choose_picks` and `hold_wants`; the hold report
shows chance, ROI and z of a held bet at its sell price.

**Replay (real positions, 03:xx):** sure bet RI-SEN (via 387), AR-SEN, NY-GOV, HOUSE-CONTROL as picks, AL-GOV (not structural) and
OK-SEN (spread 5c) held without a top-up: 6 held bets in 3 slots, so no new sure bet enters. Favourite: TX-15 and NE-SEN held,
3 slots empty. The hold book still buys nothing (budget 37.5%).

---

## 2026-11-03: several hold positions per bucket (equal-risk slots)

**Found:** one pick per bucket could take the whole bucket: the favourite bucket's 13.8k (hold 0.4 x 33% x 104.4k) in one House race
(TX-15). A `find_trades.py` sample (00:5x, 3 Nov) had 42 hold-eligible candidates: 26 sure bets with z >= 0.25, 3 favourites (only
TX-15 beating the sure bets' ROI), no coinflip, 2 unlikely (neither qualifying) and no longshot.

**Changed:** `HOLD_SLOTS` = sure bet 3, favourite 5, coinflip 4, unlikely 6, longshot 6. Each position's target is one slot,
`HOLD_BOOK_PCT x bucket share / slots`, and an empty slot stays cash. The counts give each bucket about the same dollar risk:
slots ~ (the bucket's SD with one bet / the sure bet's)^2, the SD per SUSQie being sqrt(p(1-p)) / price at the minimum-z price
(favourite 5.2, coinflip 4.2, unlikely 6.4, longshot 6.0; the sure bet gets 3 for single-race jump risk).
- `hold.choose_picks` returns lists. `fill_slots` takes held bets first (a hand buy too, once the ledger adopts it), then last
  loop's picks, then the best z; in a full bucket a challenger takes the weakest pick's slot only with `HOLD_SWITCH_Z` more z (the
  weakest is not sold, just no longer topped up). The riskier-bucket hurdle is the best ROI among the safer buckets' picks.
- `hold_wants`: a row per pick at its slot, then a row for the bucket's empty slots. `Ledger.picks` holds lists; a ledger saved with
  one pair per bucket still loads. The start-up check and the hold report show the slots.

**Replay (fresh prices, empty ledger, 104.4k):** sure bet RI-SEN 388, DE-SEN 386, VA-SEN 364 at 5.6k each; favourite TX-15 243 at 2.8k
plus 4 empty slots (11.0k cash); unlikely SENATE-CONTROL 153 (now 0.15, z 0.27) at 0.7k plus 5 empty slots; coinflip and longshot cash.
With the real ledger NY-GOV 184 (held, z 0.61) gives its slot to VA-SEN (z 1.36). Tests: `test_hold.Slots` (3 and 5 slots, from config),
the old-format load in `test_books`; mutation-checked (a hard-coded slot count, no held priority, the lowest instead of the best hurdle).

**Still open:** the hold book is at about 72.8k against its 41.8k budget, so it buys nothing until that changes.

---

## 2026-11-03: hold picks by edge per unit of risk (z); fast book by fast part in cents

**Found (dry run 23:27, 2 Nov):** both books ranked by ROI (edge / price), which favours longshots. The hold book's "unlikely" pick was
SENATE-CONTROL 153 No at 0.195 (fair 0.27, ROI 38.5%), and it was also the fast book's top buy (4.3c / 0.195 = 22%, ahead of TX-15's
13.3c at 0.74); ME-SEN at 0.21 had swapped out AR-SEN the same way. The minimum ROIs (6/10/15/20/25%) asked for less edge per unit of
risk the riskier the bucket: z 0.25, 0.16, 0.13, 0.11, 0.07 at the bucket midpoints.

**Changed:**
- `Opportunity.z` = edge / sqrt(p (1 - p)): the ROI over the SD of the bet's return per SUSQie (sqrt(p(1-p)) / price), the per-bet Sharpe.
- `hold.choose_picks`: a candidate qualifies with z >= `HOLD_MIN_Z` (0.25, the old sure-bet standard) in every bucket, a bucket picks
  its best z, and a held pick is switched only for `HOLD_SWITCH_Z` (0.10) more z. The minimum ROI is now a function of the fair probability,
  `hold.roi_floor(p) = Z sqrt(p(1-p)) / (p - Z sqrt(p(1-p)))`: 2.6% at 0.99, 6.1% at 0.95, 9.1% at 0.9, 16.9% at 0.75, 33% at 0.5, 62% at
  0.3, 300% at 0.1 (faster than geometric towards longshots). The riskier-bucket rule (more ROI than every more certain pick) stays.
  `HOLD_MIN_ROI` and `HOLD_SWITCH_MARGIN` are gone.
- Fast book: entries, swaps and the step check use the fast part in cents (`held_roi` -> `held_part`), and a swap needs `FAST_SWAP_MARGIN`
  (3c) more; `swap_out` takes the margin, so v1 keeps `SWAP_MARGIN` as an ROI. ROI would be the dollar-best order if the parts were exact;
  the noise of a fair value is about the same in cents at any price, so ranking by cents keeps a 4c longshot part from going first.
- Reports and the start-up check show z and the minimum ROI it implies. The fast report has the hold report's chance, ROI and z columns too (at the ask for a candidate, at the
  sell price for a held position); both use `strategy.z_score`.

**Replay of the 23:27 loop:** hold picks sure bet RI-SEN 388 (z 1.60), favourite TX-15 243 (z 0.50), unlikely none (153: z 0.169, would
need a 70% ROI at a 27% fair). Fast order: MA-GOV 371 13.7c, AR-SEN 254 13.5c, TX-15 243 13.3c ...; 153 from 1st to 37th of 38.
Tests: `test_hold.MinimumZ` (built from `HOLD_MIN_Z`, run at 0.10/0.25/0.40), the switch tests from `HOLD_SWITCH_Z`, a fast ranking and a
cents swap test, `test_swap_steps` in cents from `FAST_SWAP_MARGIN`; each was mutation-checked against a hard-coded value.

**Still open:** the fair value's own error is not in z (a thin House market's 0.894 is less sure than a Senate 0.99); correlated bets are
weighed one by one.

---

## 2026-11-02: a swap's step sale stops once the swap no longer pays (AR-SEN 254)

**Found (live, 20:49-21:22):** ME-SEN NO 270 (4.8c beyond the usual gap at 0.21: 22.9%) swapped out AR-SEN NO 254 (10.8c at 0.86: 12.6%).
The steps then walked AR-SEN down every 5 minutes (0.86, 0.845, 0.84, 0.835, 0.83) and only a negative edge could stop them: they went on
after ME-SEN had been bought and then swapped out itself (21:02, for SC-01-HOUSE NO 241 at 14.8%), and while each lower price made AR-SEN
the better bet (16.7% at 0.83, a 13.8c edge). About 5,300 of 6,324 fast shares sold at 0.83-0.86 against a fair of 0.968.

**Changed:** `fast.ended_steps`: each loop a stepping position is weighed at the price its step sells at (a resting step's own price, else
`next_step_price`) against the best fast entry of another bet; below `SWAP_MARGIN` ahead the steps end ("swap no longer pays"). The plan
names them (`ended_steps`) and `PatientBook.end_steps` cancels the resting step and drops the step state (a failed cancel tries again next
loop); the position keeps its slot. `PatientBook.cancel_entries` became `cancel_kind("entry" | "step")`. Replayed on the logged loops the
AR-SEN sale would have ended after its first step at 0.86 (223 shares). Tests: `tests/test_swap_steps.py`, built from `config.SWAP_MARGIN`
and run at margins of 0-25%; the step tests in `test_patient_exits.py` now have a rival entry. `tests/__init__.py` stops the run when a
pinned setting is missing from `config.py` (a 20:22 save had dropped `BASELINE_FROZEN_AT` and `FAST_HOLD_UNTIL`, and the start-up check
crashed on them while the pinned tests passed).

**Still open:** the swap itself still ranks by fast part / price, which favours cheap contracts (a 4.8c part at 0.21 beat a 10.8c part at
0.86); ranking by Kelly growth is not done. A swap still sells the whole position whatever the new buy needs.

---

## 2026-11-02: election window (SIG marks favourites down; hold them to resolution)

**Found (log analysis, 2 Nov):** from about 01:00 SIG marked favourites down 5-12c in many unrelated races (AR-GOV 158 0.910 -> 0.855,
KY-SEN 266 0.905 -> 0.835, WV-SEN 296 0.915 -> 0.880) while the Kalshi/Polymarket fair stayed flat (0.96-0.98). Fast candidates of 4c+
went from 1-3 per loop to 15-27, the best above 10c; median fast part 5c -> 7c. It drifts over many hours, so it is an edge earned at
resolution, not a lag that closes in hours. SIG spreads were not wider (one read: median 1c over all 233 markets and over the 27 candidates).
Four problems:
- the Mac slept (idle sleep on battery from 15:02, `pmset` log): 20-50 loops an hour instead of ~160, gaps up to 24 min;
- every fast exit that day was a swap (~13), with a 1% `SWAP_MARGIN` between near-identical candidates, each selling at the bid and stepping
  down: roughly -0.4k realised (an estimate; fills at placement are not logged as fill events);
- the 48h usual gap would absorb the shift overnight, the fast parts would collapse without SIG moving, and the fast book would read caught up;
- an early listing (structural value, at most 3 ticks over the best ask) is not repriced when SIG alone moves, so with favourites 10c under
  fair it would sit at ask + 1.5c and a bounce would fill it, giving up ~8-10c a share of value to resolution.

**Changed:**
- `main.keep_awake`: on macOS the bot starts `caffeinate -ims -w <pid>` (ends with the bot). It still needs power and an open lid.
- `BASELINE_FROZEN_AT` ("2026-11-02 00:00", local; `None` = live): `GapStore.as_of` takes the baselines from the 48h before it, `prune`
  keeps that window, and `count` and the start-up back-fill look at it too (a restart that finds it short of samples back-fills it from its
  start). Samples are still taken. On the real data the usual gaps stay at 0-3c, so the fast parts stay at most of the edge.
- `FAST_HOLD_UNTIL` ("2026-11-05 12:00", local; `None` = off): until then `fast.unwinds` plans no exit (no time stop, caught-up, patient
  or early exit) and `PatientBook._why_cancel` cancels a resting early listing or patient exit ("holding to resolution until ...").
  The negative-edge exit, swaps (by steps), top-ups, the top-up freeze and the kill switch still apply. After the time the usual rules return.
- `SWAP_MARGIN` 0.01 -> 0.03: a swap's round trip costs about 1.5-3% on a 0.85 favourite.

**Open:** the two times are my choices (the freeze just before the shift; the hold until after most US calls); change them in config.py.
Repricing early listings upward, or a value-to-resolution floor, was not built: no early listing exists in the window. Election night
itself is still manual: `REGIME = "election"` around 10:00 on 4 Nov, with the Mac awake. The frozen window was down to 115 samples on 2 Nov
19:00 and the running (old) bot deletes about 4 an hour, so restart soon; if it drops under 96 the start-up back-fill rebuilds it.

---

## 2026-11-01: top-up freeze after a 10c fall (no stop loss)

**Question (user):** is there a stop loss? No price stop: a position is sold on a loss only when its edge turns negative (fair below the sell
price), by the fast book's 24h time stop, or by the daily-loss kill switch (`max_daily_loss` is 0.5 in `settings.json`). When SIG and
Polymarket/Kalshi fall together nothing sells, and that is on purpose: the edge is still there, and a 0.80 favourite at 0.60 is still more
likely to win. Selling would lock the loss in and pay the spread.

**Changed (user's choice of the options offered):** a falling-knife freeze, not a stop. `Ledger.freeze_falls` (books.py), run at the start of
each v2 plan, freezes a bet whose sell price is `TOPUP_FREEZE_DROP` (10c) or more under its average entry (all lots of both books) until
`TOPUP_FREEZE_HOURS` (6) after the last loop it was that far down. A frozen bet (keyed by race and outcome, so Dem Yes and Rep No both) gets:
- hold: `hold_wants` keeps it as the bucket's pick but with want 0 and the note `frozen: ... no top-up until ...` (another market does not take
  the bucket meanwhile);
- fast: `entry_blocker` returns the same reason, so no slot, budget or swap is used, and a resting patient-entry bid on it is cancelled.
Nothing is ever sold for it; the negative-edge exit, the time stop and the kill switch are unchanged. The freezes are saved in `.ledger.json`
(`frozen`; an older file loads with none), so a restart keeps them. A plan that starts one says so in the notes. v1 is unchanged.
On today's ledger no position is anywhere near 10c down (the worst is ME-02 221 at about 2c), so nothing freezes on the next start.

**Open:** 10c and 6h are my first values, not measured. The trim-to-new-bucket and hard stop options were not built (user's choice).

---

## 2026-11-01: a big edge enters the hold book without a structural gap

**Found (OK-SEN 383 No):** fair 0.98 against a price of 0.845-0.855, a 12.5-13.5c edge (ROI 14.6-16%), and neither book bought it. The hold book
marked it "not structural" before looking at the edge: its usual gap is 7.8c (hurdle 6.2c) but the 48h 10th percentile of the gap was 5.1c, because
the gap jumped to 13.7c only in the last hours. The fast book had the slot problem, not a rule problem: its 4 positions (235, 260, 241, 318) all
returned more than 383's fast part (5.7c over 0.845, about 6%) minus the 1c `SWAP_MARGIN`.

**Changed:** `HOLD_BIG_EDGE = 0.10` in `config.py`. `hold.eligible` now lets a classified market in when it is structural **or** its executable edge
is at least that: a position held to resolution earns the whole fair - price edge, whether or not SIG's gap lasts. Every other filter is unchanged
(the side the usual gap favours, `HOLD_MIN_EDGE`, spread, rules checked, cooldown), and so is the ranking: the bucket pick is by ROI, the
`HOLD_MIN_ROI` floor and the riskier-bucket hurdle apply, and a held pick is replaced only by `HOLD_SWITCH_MARGIN` more ROI. A market with no
classified gap (under 96 samples) is still never a hold entry. On the real data 383 is now eligible (ROI 14.6%) and, in the sure bet bucket, beats
RI-SEN 388's 10.9% by more than the 3% switch margin, so it becomes the sure bet pick.

**Open:** 10c is a first value of mine, not measured (358's edge of about 8.7c stays out). The risk is a fair value that is wrong because of a brief
jump, held with no catch-up exit; the hold book still sells only when the edge turns negative. The fast book may buy the same market in its own slice.

---

## 2026-10-31: minimum ROI per hold bucket

**Changed (user's rule):** the hold book picks a bucket's best candidate only if its ROI (edge / price) reaches the bucket's floor, `HOLD_MIN_ROI` in
`config.py`: sure bet 6%, favourite 10%, coinflip 15%, unlikely 20%, longshot 25%. The old rule stays on top: a riskier bucket must also beat the best
pick of every more certain bucket. A candidate below its floor gets the decision "ROI x% is below the N% minimum" and the report says the bucket stays cash.
- A position already held whose ROI falls below its floor stops being the pick, so it is no longer topped up. It is not sold: the hold book still
  sells only on a negative edge.
- On the 11:10 loop's numbers: the sure bet pick DE-SEN 386 (6.3%) still qualifies. The favourite pick MS-SEN 272 (6.8%) does not: no favourite
  reaches 10% (best is 8.3%), so the favourite bucket would stay cash and 272 would get no top-ups.
- The start-up check lists the floors. Tests pin every floor to 0 (`tests/__init__.py`) so the older tests keep their meaning; `MinimumRoi` in
  `tests/test_hold.py` uses the real ones. Takes effect after a restart.

---

## 2026-10-31: patient entries also for top-ups

**Changed (user's rule):** a top-up of a position already held now bids one tick above the best bid for the same 10/5 min, like a new position (it
used to buy at the ask, which is why NE-SEN 280, held since 23:30, never got a patient entry). `fast.patient_bid` no longer checks that the
position is held. Unchanged: a spread of one tick buys at the ask (NE-SEN's 0.5c spread: bid 0.61, ask 0.615, so nothing to improve), a fast part
over 10c buys at the ask, and once a market's bid ended it buys at the ask for an hour. A held position with a bid resting gets no second buy and
the bid keeps a slot, budget and cash like any patient entry. Its fills are booked as fast lots, as before. Takes effect after a restart.

---

## 2026-10-31: the bot traded against its own orders (NE-SEN 280); early exits held back while a position is still a buy

**Found:** NE-SEN 280 NO. The early exit rested 8,830 shares at 0.615 (1,459 filled, 7,371 left) while the bot placed a buy of 1,329 at 0.615 every
loop from 53 to 59, and cancelled it unfilled each time (14 SIG writes for nothing). The opportunity log showed exactly 7,371 shares available
at 0.615: the bot's own sell. SIG's order book includes the account's own resting orders and nothing took them out, so a resting sell became the
best ask, the bot saw an edge against itself, and SIG does not trade an account with itself. The same flaw applies to the bid side.
(A later read of the live book showed a 4,715 sell at 0.63 and a 1,680 bid at 0.595 inside the book's levels for this market; they are not in
`logs/orders.csv`, so they are orders placed by hand, not the bot's. Orders placed by hand are not hidden by this fix.)
**Second finding:** with fair 0.674 and a usual gap of 0 the bot bought at 0.615 (5.9c of fast part) while listing everything at 0.60-0.63
(held within 3 ticks of the ask), and every top-up cancelled and re-placed the whole listing (4 times in 3 minutes), losing its queue place.

**Changed:**
- `sig.fetch_depth(races, markets, own)` takes the bot's resting orders out of the book it reads (`PatientBook.resting_orders()`: remaining shares at
  the order's level, a NO sell at p being a Yes bid at 1 - p; live runs only). The best bid/ask and their sizes are worked out again, so the
  re-plan after the depth read sees the real ask.
- `PatientBook.drop_self_matches`: an order that would trade against one of the bot's own resting orders (a buy at or above its own sell, a sell
  at or below its own buy, same side of the same market) is not sent; the report says why. Covers a hold-book buy next to a fast listing.
- **Early exits are held back** (`fast.held_back`) while a position's fast part left is `FAST_MIN_EDGE` or more: no listing, and a resting listing is
  cancelled ("still a buy, so the early exit is held back"). The listing starts once SIG has moved to within that of fair. The bot buys or sells a
  position, never both, so top-ups no longer re-list. A listing may flap at exactly the edge (cancel when the part is back at 4c, list again under it).
- Bulk screening prices (`/exchanges/prices`) still include own orders; a market that matters is depth-read, which fixes them.
Unit tests only (`tests/test_own_orders.py`, `tests/test_patient_exits.py`); takes effect after a restart.

---

## 2026-10-31: Ctrl+C keeps the resting orders; a restart follows them again

**Changed (user's rule):** a restart for an update used to cancel every order and lose every queue place. Now:
- **Ctrl+C** leaves the resting orders (early/patient exits, steps, entry bids) on SIG. Only the short-lived orders of a loop cut short are cancelled.
- **STOP file, daily-loss limit, 5 errors in a row, a crash** still cancel every order on the account, then empty the file (kept if cancel-all did not confirm).
- **`--once`** cancels the bot's own orders as before.
- A live run keeps the `PatientBook` in `.resting.json` (git-ignored; saved when an order is added, at the end of each loop and at shutdown). The next
  start loads it, tells `LiveTrader.resting` about the orders, and the normal loop does the rest: `sync` logs what closed while the bot was down, a
  still-open order is kept, `fast_plan` skips positions with a resting sell (no second sell, which SIG would turn into the opposite buy), and the
  existing re-list rules (fair moves 1c, new shares, deadline, negative edge) update it. Adopted orders are kept as they are: a rule changed in the
  update only reaches them when one of those happens.
- If the restart switched a rule off, the adopted order is cancelled: early exits off cancels `early` orders, patient entries off cancels bids;
  a start that does not follow resting orders (v1, `PATIENT_EXITS` off) cancels the recorded ones.
- An unreadable `.resting.json` stops the start (an order the bot does not know could be sold twice). `LiveTrader.open_resting` now forgets
  closed orders, and `DryRunTrader` exposes `resting`.
- While the bot is down nobody re-lists or cancels: SIG drops each order at its expiry (its time + 5 min).
Unit tests only (`tests/test_restart.py`); the running bot still cancels everything on Ctrl+C until it is restarted with this code.

---

## 2026-10-31: patient entries wait less, or not at all, on a big fast part

**Changed (user's rule):** a patient entry's bid can miss a big gap: SIG may catch up before it fills. Now (`fast.entry_minutes`):
- Fast part up to 8c: the bid rests `PATIENT_ENTRY_MINUTES` (10) as before.
- Over 8c (`PATIENT_ENTRY_SHORT_PART`): it rests `PATIENT_ENTRY_SHORT_MINUTES` (5).
- Over 10c (`PATIENT_ENTRY_MAX_PART`): no patient entry, it buys at the ask at once.
- A bid already resting whose fast part rises over 10c is cancelled (note "fast part N c: buying at the ask") and the next loop buys at the ask.
  A bid placed for 10 min whose part rises to 8-10c keeps its 10 min.
- The cancel reason of a resting bid is now carried in `FastPlan.stale_entries` ({key: why}).
Unit tests only; takes effect after a restart.

## 2026-10-30: early-exit listings stop in front of a liquidity wall, at most 3 ticks above the best ask

**Changed (user's rule):** an early exit used to list at the structural value however far above the book. Now (`bigorder.wall_price`):
- At most `EARLY_EXIT_MAX_TICKS` (3) ticks above the best ask, and never above the structural value.
- A liquidity wall is an ask level worth 1,000+ SUSQ (`WALL_MIN_VALUE`) and 3x (`WALL_RATIO`) the nearest non-empty level (the one below; for the
  best ask, the one above). The first wall within 3 ticks: list one tick in front of it, if that is at least the average fast entry price + 1c
  (`EARLY_EXIT_MIN_PROFIT`); otherwise the wall is ignored (list at the cap). Example 151: best ask 0.885 (9k), about 1M at 0.89: listed at 0.885.
- The best ask itself a wall: one tick below it if that clears entry + 1c (on a one-tick spread that is the bid: it sells at once), else at the
  wall if that clears it, else the cap.
- No book levels: no listing that loop ("early exit, waiting").
- (fix, same day) Levels worth under 1,000 SUSQ are skipped altogether: they are no wall and nothing a wall is compared with. Before, market 151
  listed at 0.875 because the best ask counted as a wall next to a tiny level at 0.88; on that book it now lists at 0.88, in front of 0.885.
- Re-listing now compares the structural value with its value at listing (from the order's fair), not with the listed price, which often sits
  below it on purpose. A wall appearing or vanishing under a resting listing does not re-list it; the wall is worked out again at each re-list.
- Checked on 15 saved books with the real entry prices: no listing under entry + 1c; one (VA-07) would sell at the bid, 2.5c over entry.
Unit tests only (370 pass). Takes effect after a restart.

## 2026-10-29: a full fast book never swapped (KS-SEN 357 kept its slot with a negative fast part)

**Found:** the fast book was full (KS-SEN 357, SD-GOV 309, SC-GOV 193, WY-SEN 366). FL-GOV YES 300 qualified as a fast entry for
300+ loops (4.5c fast part, 6.8% return) and KS-SEN's return was negative, but no swap happened. `swap_out` checks that the sale's proceeds really
buy at least `MIN_ORDER_VALUE` of the candidate, using the shares on offer at the ask; those come only from a depth read. With a full book the depth
reads went to the held markets, never to the candidate, so its size stayed unknown (0 shares), the swap failed, and no buy was planned that would
have earned it a read.
**Fixed:** with a full book the two best new candidates' books are read too (`SWAP_READS`), before the held ones. One or two more SIG reads a loop
while the book is full. Takes effect after a restart.

## 2026-10-29: patient entries (off by default: `PATIENT_ENTRIES = False` in `config.py`, needs `PATIENT_EXITS`)

**Built (user's rule):** a new fast position on a SIG spread wider than 0.5c first rests its buy one tick above the best bid (the spread
tightens by one level) for `PATIENT_ENTRY_MINUTES` (10), then what is left buys at the ask as usual. On a 0.5c spread it buys at the ask at once.
- Size as a normal entry (slice, cash, 3% single-order cap), not limited by the shares on offer at the ask.
- Once per market an hour: after its bid ends the market buys at the ask. (It first applied to new positions only; since 2026-10-31 top-ups bid too.)
- While it rests the bid takes its slot, its fast-book budget and its cash (SIG does not hold cash for resting buys, so the plan deducts it), and the
  market gets no second buy. Report: "patient entry, 7m left", note "patient entry: N bid at P (ask Q)".
- Cancelled early when its candidate stops qualifying as a fast entry (fast part under 4c, fair value gone, spread over 2c, ...).
- Fills: the trader does not follow a resting order's fills, so the ledger books new shares in that market as a fast lot at the bid's price, while it
  rests and once more after it ends (fills just before the cancel). Elsewhere unexplained shares are still a grandfathered hold lot.
- In the loop its 10 minutes end, the ask buy can be sized before the last fills show up in the positions: it may buy a little over the slice once.
- Shutdown cancels it with the other resting orders. Unit tests only (358 pass).

## 2026-10-29: early exits (off by default: `EARLY_EXITS = False` in `config.py`); a patient exit is one order again

**Built (user's rule), with `EARLY_EXITS` and `PATIENT_EXITS` on, by age from a fast position's first entry:**
- 0 to `EARLY_EXIT_HOURS` (24h): all its fast shares are listed in one sell at the structural value, fair - usual gap (the price where the fast
  part is used up), on the tick. Cancelled and re-listed in the same loop when the structural value moves more than `EARLY_EXIT_BAND` (1c) from the
  listed price, or when a top-up adds shares (top-ups continue in this phase; the listing does not start the 5-minute cooldown). Cancelled with no
  fair value at all (both sources down or disagreeing; one source alone is still a fair value) and listed again when it is back ("early exit,
  waiting"). A structural value at or under the bid fills at once (SIG has caught up). The 1c caught-up rule and the 3c patient trigger do not apply.
- 24h to the time stop (`FAST_MAX_HOURS`, 48) plus `PATIENT_MAX_MINUTES`: a patient exit at the big-order price, whatever the fast part (it is not
  cancelled when the fast part comes back above 3c). Then the sale at the bid, as before.
- Swaps, negative edge and a gone position cancel either listing as before. Report status: "early exit, 23h 10m left".
- One resting sell per position, never two: a position with a sell resting (or whose cancel failed) gets no other sell until it is gone.
- Patient exits rest as one order again (the two-half split, `bigorder.split_rest`, is removed), at the price one big order fills, the big order
  being 3% of the 7-day volume.
- Every shutdown now cancels the resting sells (STOP file too; before only Ctrl+C, the kill switch and a crash did): they are followed in memory
  only, and a listing left on SIG would make the next run sell the same shares twice.
- Writes: one per listing, two per re-list. Only tested with unit tests and the fake trader (344 pass).

## 2026-10-29: patient-exit big order is now 3% of the 7-day volume (was 4%)

**Changed (user's rule):** `BIG_ORDER_WEEK_SHARE = 0.03` in `config.py` (still between 500 and 50,000 SUSQies). A smaller big order means a resting
sell sits a little closer to the bid, so it fills more often but gains less per fill. Tests keep 4% pinned in `tests/__init__.py`. Takes effect after a restart.

## 2026-10-29: a patient exit rests in two halves (half and full big order)

**Changed (user's rule):** a patient exit used to rest all its shares at one price, the highest a big order (4% of the 7-day volume, 500 to 50,000)
still fills completely. Now it rests two sells (`bigorder.split_rest`): half the shares at the highest price a big order of half that size fills,
and the other half at the highest price a full big order fills, counting the first half as queued ahead of it (so the second is never lower).
One share rests whole at the full big order's price. Both halves share the same deadline and cancel rules; the report note reads
"resting 1,000 at 0.905, 1,000 at 0.915". A patient exit now costs two SIG writes to place and two to cancel. Unit tests only (331 pass).

## 2026-10-29: RI-SEN was not the sure-bet pick although the hold book holds 35,681 NO 388

**Found:** the sure-bet bucket's pick was WY-SEN YES 366 (ROI 9.7%), recorded in the ledger, with no WY-SEN shares held. RI-SEN NO 388 (9.8%), the
hold book's biggest position (grandfathered lot), lost because a bucket keeps its recorded pick unless beaten by `HOLD_SWITCH_MARGIN` (3%).
**Fixed:** `hold.sitting_pick`: the pick a bucket keeps is the eligible bet the hold book already holds the most of (switching away from it costs
a sale); the recorded pick only counts while nothing is held. On the 2026-10-29 03:18 prices and the live ledger the sure-bet pick is now RI-SEN NO 388.
It holds about 32k against a bucket target of about 21k (40% of the 50% hold book), so it buys nothing more; nothing is sold either.
Takes effect after a restart.

## 2026-10-29: a better edge swaps out a resting patient exit; swaps sell by 5-minute steps down the bids

**Changed (user's rule):** before, a fast position resting in a patient exit freed its slot, so a better edge entered without a swap but only with free cash,
while the money stayed in the resting sell (up to the time stop plus an hour). Now, with `PATIENT_EXITS` on:
- A resting position keeps its slot. With all slots taken (resting or not), a new edge must beat the worst position's return by `SWAP_MARGIN` (1%), as
  any swap; a resting position can be that worst one. A new edge that beats none of them by 1% does not enter (before, it entered beside them).
- The swapped-out position is not sold in that loop. From the next loop: its patient exit is cancelled ("swapped out for a better edge"), then it sells
  by steps: a limit at the best bid that rests `SWAP_STEP_MINUTES` (5); what is left after 5 minutes (unfilled or partly filled) moves to the next bid
  level below the last price (one tick lower when the book levels are unknown), and so on until it is sold. A step is only cancelled early by a
  negative edge (the hold exit then sells everything at the bid) or the position being gone. Report status: "swap exit, 3m left" / "swap exit".
- A patient exit placed this loop also keeps its slot, so a fresh one can no longer let a fifth position in for one loop.
- With `PATIENT_EXITS` off nothing changes: a swap sells at once into the bids within 1c.
- The step state is in memory (`PatientBook._stepping`): after a restart a half-sold swap is treated as an ordinary fast position again.
- Only tested with unit tests and the fake client (328 pass). Each step is one cancel and one order a position every 5 minutes.

## 2026-10-28: a patient exit now rests until the time stop, then gets one more hour

**Changed (user's rule):** a resting patient sell used to be given up after `PATIENT_MAX_MINUTES` (60). It now rests until the position's time stop
(`FAST_MAX_HOURS` from its first entry, 48h today) plus `PATIENT_MAX_MINUTES`; only then is it cancelled and the position sold at the bid.
- The time stop is a patient exit too: a position past its time limit rests its sell for the hour first (before, it sold at the bid at once). It still
  sells at the bid at once when it cannot rest (no 7-day volume or book levels).
- Past the time stop the resting order is not cancelled because the fast part recovered; the position is sold whatever its fast part.
- Earlier cancels are unchanged: negative edge, fast part back at 3c or more (before the time stop), no fast shares, position gone.
- `Order.rest_until` (epoch seconds) replaces the `resting` flag; it is the one deadline for the book, the SIG `expirationDate` (+5 min) and the
  report timer ("patient exit, 47h 30m left"). `PatientBook.add(order_id, order)` lost its `now`.
**Risks:** an order can sit on SIG for about two days, so its price (set when it was placed) is not updated, and if the bot dies it stays until
SIG expires it. Start-up cancel-all clears it on a restart. Capital in a resting position stays committed and its slot is counted as free for new entries.
**Needs:** a restart of `main.py`. 315 tests pass at FAST_MAX_HOURS 1, 5, 48 and 100.

---

## 2026-10-28: positions resting for a patient exit vanished from the fast report

**Found:** NE-GOV and TX-23 disappeared from the Fast book table once their sell rested: every share was reserved for the resting order, so the
planner had no shares left to list.
**Fixed:** the report lists all of a position's fast shares, resting ones included. New columns: `held for` (since the first entry) and `status`
("patient exit, 42m left": time until the order is cancelled and the position sold at the bid); the note shows the shares resting and their price.
A patient exit that cannot rest yet shows "patient exit, waiting" in the same column.
**Needs:** a restart of `main.py`.

---

## 2026-10-28: the usual gap now counts by its size, whichever way it points

**Found:** TX-23 YES 245 had a usual gap of -1.25c (SIG Yes usually a little expensive) and a 3.5c Yes edge, so the fast part was the whole 3.5c and the
fast book would enter. A market that is usually off fair value is mispriced either way, so a swing back from the gap is no new mispricing.
**Fixed:** `gapmodel.fast_part` = edge - |usual gap on that side| (it was edge - max(usual, 0)). TX-23 is now 3.5 - 1.25 = 2.25c: no entry. The same
rule applies to held positions: their fast part left is lower when the usual gap points the other way, so they reach the exit (below 3c with patient
exits, 1c without) sooner. A usual gap inside the 1c noise tolerance is still 0. Tests updated; `FAST_MAX_POSITIONS` and `FAST_MAX_SPREAD` are now pinned
in `tests/__init__.py` (the config change to 4 and 2c had broken two tests). 309 tests pass.
**Display too:** `Baseline.for_side(side)` is now `Baseline.usual()` (a size, no side), `fast_part(baseline, edge)` and `caught_up(baseline, exit_edge)` lost their
unused `side`, and the fast report and the opportunity log show the usual gap as a size with no sign.
**Needs:** a restart of `main.py`. **Open:** the backtest figures in `analysis/backtest_out` were made with the old rule.

---

## 2026-10-27: patient exit placed nothing for IA-GOV 168 (stale volume cache)

**Found:** with `PATIENT_EXITS = True` the fast position IA-GOV YES 168 (fast part below 3c) rested no sell. A resting sell needs the market's 7-day
volume (it sizes the big order), and `.volume_cache.json` was written before that field existed: every entry had `week = 0`, and entries are
only re-read after 12 hours. With no week volume the exit cannot rest and, above the 1c rule, silently waits.
**Fixed:** `Volume.week` is `None` when unknown; a cache file's `week` of 0 loads as unknown and counts as stale, so start-up re-reads those
markets (211 of 233). A patient exit that cannot rest now says so in the fast report ("cannot rest yet ... waiting"). 309 tests pass.
**Needs:** a restart of `main.py` (the running bot has the old code). **Open:** a held fast position is not read ahead of the others when
the loop refreshes volumes (2 per loop); start-up reads them all.

---

## 2026-10-27: patient exits built, off by default (`PATIENT_EXITS = False` in `config.py`)

**What it does (switch ON, fast book only):** a fast position whose remaining fast part drops below 3c (`PATIENT_TRIGGER`; the old rule was 1c or less) rests its sell above the bid instead of selling at it. Price = the farthest level in the held side's book that one big order (4% of the market's 7-day volume, between 500 and 50,000 SUSQies) still fills completely, counting the asks queued ahead; if the sell alone is bigger than the big order, one tick above the bid. It stays up to 60 minutes (`PATIENT_MAX_MINUTES`), then it is cancelled and the position sells at the bid. It is also cancelled at once when the edge turns negative, the 24h time stop is due, or the fast part is back at 3c or more. The time stop, swap and negative-edge exits never rest. While it rests the shares are reserved, so no other exit sells them twice.
**If it cannot rest** (no book levels or no week volume yet) a position below 3c but above 1c waits; one at 1c or less sells at the bid as before.
**Swap pricing (always on, switch or not):** a swap now sells into the bids within 1c of the best bid and is priced at their average (`SWAP_BID_BAND`); held ROI, proceeds and the margin test use that average, and only that many shares are sold. Order books are read 10 levels deep (`DEPTH_LEVELS`), and the held fast markets are read when the fast book is full and a candidate waits (the live logs show no swap ever happened, because held positions had no depth in the first pass).
**Safety:** resting sells carry an `expirationDate` (patience + 5 min), so SIG drops them if the bot is gone; start-up cancel-all, the kill switch and Ctrl+C cancel them too (the bot starts with an empty book). A failed cancel keeps the shares reserved and retries next loop.
**Not known:** the study on the tape put the gain at about +2k on 25 days at best and no fill at all from big-order prints (see the entry below); live fills may differ. The rest price can sit far above the market (up to 0.995 when the position is small next to a big order).
**Tests:** 308 pass (`tests/test_bigorder.py`, `tests/test_patient_exits.py`). To try it: set `PATIENT_EXITS = True`, restart, and watch `logs/orders.csv` (`placed` with "rests on the book", then `filled` / `cancelled`) and your open orders on SIG.

---

## 2026-10-26: study of patient exits (resting the sell above the bid), no live code yet

**Question:** would resting the caught-up / time-stop sells above the bid, to catch a big buyer, beat selling at the bid? Study on SIG's trade tape (`analysis/tape_data.py` caches it, `analysis/patient_exit_study.py` runs it; output in `analysis/backtest_out/patient_exit_study.txt`). Backups before any change: `~/Desktop/SIG-bot-backup-2026-10-26.tar.gz` and git branch `backup-before-patient-exits` (48221a9); work is on branch `patient-exits`.
**Big-order size:** order groups (same-timestamp fills) scale with a market's 7-day volume (p99 slope 0.6, max slope 1.1, r about 0.85-0.9) and barely with lifetime volume (r 0.07-0.34). Over 33 markets with a 7-day tape, 4% of the week (cap 50k) gives at least one big order a week in 28, at least 3 in 14, at least 7 in 1; a flat 50k gives at least one in 11.
**Result on the 70 caught-up and time-stop exits of the baseline backtest (windows of 60 / 30 min):** a sell that only big-order prints can fill was filled in **0 of 70** exits. Resting 1 tick above the bid: any print reaches it in 77% (caught up) / 62% (time stop) of exits, +1,695 / +319 SUSQies in total versus selling at once; 2 ticks +997 / +552; 3 ticks +468 / +177; 4+ ticks about nothing (highest price touched in a window: median 0 ticks, 90th percentile 2). The cost of just waiting (drift) is -168 / -134.
**Why this is an upper bound:** the backtest's bid is last trade minus 0.5c (a 1c spread; SIG's real spread is mostly 0.5c), so 'a tick above the bid' there is the last trade price and any later print at that price counts as a fill; no queue position; no book history. The 1-tick gain is the part most affected by those assumptions.
**Open:** whether to build the live feature. Weekly tapes were fetched for only 33 of 233 markets (the fetch was stopped to leave SIG reads to the bot; rerun `python3 analysis/tape_data.py` to complete it).

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