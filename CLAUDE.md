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

Risk limits are in `settings.json` (max_invested 0.60, per race 0.30, position 0.25, hold book 0.30, fast book 0.30, single buy 2% of total equity (cash + positions), daily loss 10%, 5 errors in a row).
The invest cap blocks new buys only and never sells; sells are exempt from the single-order cap. The kill switch and Ctrl+C cancel
every open order on the account; each loop only cancels the bot's own orders.
