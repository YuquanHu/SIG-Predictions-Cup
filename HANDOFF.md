# Handoff (2026-10-25)

Read `CLAUDE.md` and `plan.md` first (standing rules: never set DRY_RUN False, send a real order, or run `main.py` live yourself; never print the API key; review code changes against `claude skills/thermo-nuclear-code-quality-review/SKILL.md` and say briefly what the review found). `updates.md` is the dated log of what was found and why.

## 1. Goal and "done"
Bot compares SIG (thesuper.market, SUSQies) prices to a fair value from Kalshi + Polymarket and trades the gap. v2 = two books: structural **hold** book (buckets, ROI pick) + **fast** book (edge beyond the usual gap). "Done" = v2 runs live safely with the user's chosen settings, with the open decisions below resolved. All four v2 build stages in `plan.md` are DONE; the user is now running / preparing a live run.

## 2. State of the code (238 tests pass: `python3 -m unittest discover -s tests -t .`)
- **User has gone live**: `config.py` has `DRY_RUN = False`; `settings.json` is max_invested 0.70, hold/fast books 0.20/0.20, order cap 0.01 (user's values, leave them). Live fast buy seen in `logs/orders.csv`: FL-GOV 300 Republican YES, 257 sh @ 0.795, fast lot in `.ledger.json`. Regime `slow` is 30s timeout / 30s pause.
- This session added: `fast.py` (fast book), `trader.Execution` (execute returns `.problems` and `.fills`), `Ledger.record_fills`, `trading.bet_room` / `swap_out` / `sell_order`, `display.print_fast_report`, `opportunities.csv` column `fast_part`, v2 start-up lines, `tests/test_fast.py`, `test_stage4.py`, `test_normalize.py`.
- **Latest change (fair value fix, after the FL-GOV question):** `strategy.normalize` + `compute_fair_values` scale each race's outcome fair values to sum to 1 (skipped if an outcome has no value or total is >`config.PROBABILITY_TOLERANCE` 0.05 from 1); `Fair.raw` keeps the unscaled value for `probability_gaps`. `backfill.race_gaps` fetches a whole race and scales at each sample time (replaced `contract_gaps`). History file is now `.gap_history_norm.csv` (old `.gap_history.csv` ignored, not deleted); it was fully back-filled (43,590 rows).
- **Tests now independent of settings:** `tests/__init__.py` pins `TEST_CONFIG` (limits, DRY_RUN True, STRATEGY v1, edges) for test runs only. Do not "fix" failing tests by editing `settings.json`.
- **The running bot process has old code** (before the fair value fix): it must be restarted (`python3 main.py --strategy v2`, user types LIVE).

## 3. Decisions and why (incl. rejected)
- Fast book gates each bet on its own fast part = edge - max(usual gap on that side, 0); it deliberately does not use Kelly. Entry also needs fast part - spread > 1c (else a fresh entry counts as "caught up" and is sold at a loss at once).
- `keep_best` can't be used for fast dedupe: it filters on `c.decision`, which the hold book already set (bug found; fast.py dedupes locally in `rank_entries`).
- Risk must pay: a riskier hold bucket only picks if its ROI beats the best pick of every more certain bucket (`hold.choose_picks`).
- Fast book has no `rules_checked` gate (hold only). Grandfathered positions are never topped up or sold to fit rules; user sells down RI-SEN 388 and DE-SEN 386 by hand.
- Read/write budgets 90/25 (limits 100/30) kept as a safety margin (SIG counts differently, other scripts share the account, retries, cancel-all must never be blocked).
- Logs: `loops.csv` only on loops with orders, order problems, or kill-switch stop; `orders.csv` only on order events (dry run: `would_place`). User was told; offered a heartbeat row, not chosen.
- Rejected/not done: stricter "pick the cheaper equivalent bet first, then gate" (would have skipped the FL-GOV trade); deleting the old gap history (file renamed instead).

## 4. Open questions / blockers
1. **Equivalent-bet choice (user hasn't answered):** FL-GOV 300 Rep YES (0.795, edge 2.2c now) vs 299 Dem NO (0.78, edge 3.7c). With fair values now equal, the edges still differ because SIG's two markets sum to 1.02 and each has its own usual gap (299: -1.5c, 300: -0.3c). Options: (a) narrow: gate as now, but if one bet in the group qualifies, execute through the cheaper member; (b) stricter: pick the cheaper first, then gate; (c) leave. Implement in `fast.rank_entries`.
2. x = `FAST_MIN_EDGE` (config 3c; 5c was planned; replay supports 3c on one event).
3. Which of 10 independent-candidate races to mark `"rules_checked": true` in `races.json` (hold book only).
4. Fast unwind / time stop / swap have never run live; watch the first fast position close (`.ledger.json` lots, `logs/orders.csv` `book` column). A crash between a fill and the ledger save leaves the shares as a grandfathered hold lot.
5. Ctrl+C / kill switch / crash sends account-wide cancel-all, which also cancels the user's hand-placed open orders (4 open at last check).

## 5. Exact next step
Ask the user which option for question 1 (a/b/c). If (a): in `fast.rank_entries`, after `entering` is built, for each equivalent group (`(c.race, c.wins_when)`) replace the winner with the lowest `c.price` member that is not blocked (`entry_blocker` other than the fast-part test), keep sizing on its own `parts` value; add tests to `tests/test_fast.py` (FL-GOV numbers: 299 NO 0.78 vs 300 YES 0.795), run the suite, review, log in `updates.md`, then tell the user to restart the live bot.
