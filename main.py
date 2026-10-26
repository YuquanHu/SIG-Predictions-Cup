#!/usr/bin/env python3
"""The full trading loop, ready for live trading. DRY_RUN is True in config.py, so it only prints what it would do.

    python3 main.py                            # strategy v1 (top 4 by Kelly): loop until STOP file, kill switch, or Ctrl+C
    python3 main.py --strategy v2              # two books: structural hold + fast opportunity (plan.md, end)
    python3 main.py --once                     # run a single loop and exit
    python3 main.py --day-start-equity 100000  # tell the daily-loss kill switch what today's starting equity was

Start-up prints the mode, your balance and the risk limits. With DRY_RUN = False it then requires you to type LIVE.
Each loop: cancel leftovers -> kill switch -> fetch prices and account -> (v2: gap sample, ledger) -> opportunities ->
v1: top 4, exits and swaps; v2: hold picks, fast entries and unwinds -> size -> cap each buy -> place orders -> wait the
order timeout, cancel the rest, read fills from your positions (v2: book them in the ledger) -> log -> wait for the
regime's pause.
"""
import argparse
import time
import traceback

import budget
import config
import logbook
import sig
import sig_trade
from bookkeeping import opportunity_rows, prepare_history, refresh
from books import Ledger
from display import print_fast_report, print_hold_report, print_report
from feeds import fetch_all, setup_all
from parallel import run_all
from planner import depth_targets, make_plan, make_plan_v2
from portfolio import build_portfolio
from races import disabled_races, drain_warnings, load_races
from safety import RunState, check_loss, check_stop, load_day_start, new_warnings, set_day_start, stop_file_exists
from startup import startup_check
from strategy import compute_fair_values, probability_gaps
from trader import DryRunTrader, LiveTrader
from trading import cap_order_sizes, max_order_value


class OrderProblems(Exception):
    """Raised after a loop's logs are written when an order was rejected or never confirmed; counts as an error."""


def run_loop(loop_no, races, state, regime, trader):
    """One pass. Returns the reason trading must stop, else None."""
    started = time.time()
    sig.take_read_count()
    budget.WRITES.take_count()
    if state.orders_may_be_open:
        trader.cancel_open(loop_no)
        state.orders_may_be_open = trader.has_open_orders()
    reason = check_stop(state)
    if reason:
        return reason

    _, account, positions = run_all([lambda: fetch_all(races), sig.fetch_account, sig.fetch_positions])
    fair_values = compute_fair_values(races, time.time())
    for race_id, total in probability_gaps(races, fair_values):
        print(f"  WARNING [{race_id}] outcome probabilities add up to {total:.2f}, not 1: is a candidate missing?")
    cash = account["balance"]
    portfolio = build_portfolio(cash, positions, races, fair_values)
    reason = check_loss(state, portfolio.bankroll)
    if reason:
        return reason

    v2 = config.STRATEGY == "v2"
    now = time.time()
    baselines, ledger_notes = refresh(races, fair_values, positions, state, now) if v2 else ({}, [])

    def plan_for(portfolio):
        if v2:
            return make_plan_v2(races, fair_values, portfolio, state.cooldown, baselines, state.ledger, state.volumes, now)
        return make_plan(races, fair_values, portfolio, state.cooldown)

    plan = plan_for(portfolio)                                         # screen every race on bulk prices
    targets = depth_targets(plan, config.MAX_DEPTH_READS_PER_LOOP)
    if targets:                                                        # real sizes and fresh prices where money moves
        sig.fetch_depth(races, targets)
        portfolio = build_portfolio(cash, positions, races, fair_values)
        plan = plan_for(portfolio)

    to_place, cap_notes = cap_order_sizes(plan.orders, portfolio.bankroll)
    print_report(portfolio, plan.chosen, [o for o in to_place if o.action == "sell"], ledger_notes + plan.notes + cap_notes,
                 state.ledger if v2 else None)
    if v2:
        print_hold_report(plan.buckets, portfolio.bankroll)
        print_fast_report(plan.fast_rows)
    print("\nOrders")
    if not to_place:
        print("  none")
    execution = trader.execute(to_place, regime.order_timeout, positions, loop_no, max_order_value(portfolio.bankroll))
    problems = execution.problems
    if v2:  # book what filled before the next loop's reconcile would adopt it as a grandfathered hold lot
        state.ledger.record_fills(execution.fills, time.time())
        state.ledger.save()
    state.cooldown.record(to_place)
    state.orders_may_be_open = trader.has_open_orders()

    reads, writes, seconds = sig.take_read_count(), budget.WRITES.take_count(), time.time() - started
    print(f"\nloop took {seconds:.1f}s, {reads} SIG reads ({len(targets)} depth reads), {writes} SIG writes")
    logbook.log("opportunities", loop_no, opportunity_rows(  # not every losing candidate
        plan.candidates, baselines, min(config.HOLD_MIN_EDGE, config.FAST_MIN_EDGE) if v2 else config.MIN_EDGE))
    if to_place or problems:  # a quiet loop is not worth a row
        logbook.log("loops", loop_no, [{"regime": config.REGIME, "cash": portfolio.cash, "invested": portfolio.invested,
                                        "bankroll": portfolio.bankroll, "opportunities": len(plan.chosen),
                                        "orders": len(to_place), "sig_reads": reads, "sig_writes": writes,
                                        "seconds": round(seconds, 1), "status": "ok" if not problems else "order problems"}])
    if problems:
        raise OrderProblems(f"{problems} order(s) were rejected, unconfirmed, or could not be cancelled")
    return None


def guarded_loop(loop_no, races, state, regime, trader):
    """run_loop with its failures counted and logged, so one bad loop does not end the run."""
    try:
        stop_reason = run_loop(loop_no, races, state, regime, trader)
        state.errors_in_row = 0
    except Exception as e:
        stop_reason = None
        state.errors_in_row += 1
        print(f"  ERROR ({state.errors_in_row} in a row): {type(e).__name__}: {e}")
        logbook.log("errors", loop_no, [{"where": traceback.extract_tb(e.__traceback__)[-1].name,
                                         "error": f"{type(e).__name__}: {e}"}])
    logbook.log("errors", loop_no, [{"where": "fetch", "error": w.strip()}
                                    for w in new_warnings(state, drain_warnings())])
    return stop_reason


def wait(seconds):
    """Sleep up to `seconds`, waking early if the STOP file appears."""
    end = time.time() + seconds
    while time.time() < end and not stop_file_exists():
        time.sleep(min(1.0, max(0.0, end - time.time())))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run one loop and exit")
    parser.add_argument("--strategy", choices=("v1", "v2"), help="v1: top 4 by Kelly (default); v2: two books (plan.md, end)")
    parser.add_argument("--day-start-equity", type=float, metavar="AMOUNT",
                        help="today's starting total equity, for the daily-loss kill switch (default: the first reading today)")
    args = parser.parse_args()
    if args.strategy:
        config.STRATEGY = args.strategy
    regime = config.REGIMES[config.REGIME]

    races = load_races(config.RACES_FILE)
    setup_all(races)
    state = RunState()
    if config.STRATEGY == "v2":
        state.ledger = Ledger.load()
        prepare_history(races, state)
    load_day_start(state)
    if args.day_start_equity:
        set_day_start(state, args.day_start_equity)
    try:
        account, positions, open_orders = sig.fetch_account(), sig.fetch_positions(), sig.fetch_open_orders()
    except Exception as e:
        raise SystemExit(f"Cannot read your SIG account, so nothing was started: {e}")

    live = not config.DRY_RUN
    trader = LiveTrader(sig_trade.Client(verbose=False), races) if live else DryRunTrader()
    startup_check(trader.mode, account, positions, open_orders, state, len(races),
                  len(disabled_races(config.RACES_FILE)), regime)

    logbook.init()
    loop_no, abnormal_exit = 0, False
    try:
        while True:
            loop_no += 1
            print(f"\n===== loop {loop_no} at {time.strftime('%H:%M:%S')} =====")
            stop_reason = guarded_loop(loop_no, races, state, regime, trader)
            if stop_reason:
                print(f"\nKILL SWITCH: {stop_reason}")
                logbook.log("loops", loop_no, [{"regime": config.REGIME, "status": f"stopped: {stop_reason}"}])
                abnormal_exit = True
                break
            if args.once:
                break
            print(f"\nwaiting {regime.loop_pause}s...")
            wait(regime.loop_pause)
    except KeyboardInterrupt:
        print("\nCtrl+C received, shutting down.")
        abnormal_exit = True
    except BaseException:
        abnormal_exit = True
        raise
    finally:
        if abnormal_exit:
            trader.cancel_everything()  # kill switch, Ctrl+C or crash: cancel every open order on the account
        elif trader.has_open_orders():
            trader.cancel_open(loop_no)
        print("stopped.")


if __name__ == "__main__":
    main()
