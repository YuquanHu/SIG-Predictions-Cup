#!/usr/bin/env python3
"""Stage 6: the full trading loop, ready for live trading. DRY_RUN is True in config.py, so it only prints what it would do.

    python3 main.py                            # loop until STOP file, kill switch, or Ctrl+C
    python3 main.py --once                     # run a single loop and exit
    python3 main.py --day-start-equity 100000  # tell the daily-loss kill switch what today's starting equity was

Start-up prints the mode, your balance and the risk limits. With DRY_RUN = False it then requires you to type LIVE.
Each loop: cancel leftovers -> kill switch -> fetch prices and account -> opportunities -> top 4 -> exits and swaps ->
size -> cap each buy -> place orders -> wait the order timeout, cancel the rest, read fills from your positions -> log ->
wait for the regime's pause.
"""
import argparse
import time
import traceback

import budget
import config
import logbook
import sig
import sig_trade
from display import print_report
from feeds import fetch_all, setup_all
from parallel import run_all
from planner import depth_targets, make_plan
from portfolio import build_portfolio
from races import disabled_races, drain_warnings, load_races
from safety import RunState, check_loss, check_stop, load_day_start, new_warnings, set_day_start, stop_file_exists
from startup import startup_check
from strategy import EPS, compute_fair_values, probability_gaps
from trader import DryRunTrader, LiveTrader
from trading import cap_order_sizes


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

    plan = make_plan(races, fair_values, portfolio)                    # screen every race on bulk prices
    targets = depth_targets(plan, config.MAX_DEPTH_READS_PER_LOOP)
    if targets:                                                        # real sizes and fresh prices where money moves
        sig.fetch_depth(races, targets)
        portfolio = build_portfolio(cash, positions, races, fair_values)
        plan = make_plan(races, fair_values, portfolio)

    to_place, cap_notes = cap_order_sizes(plan.orders, portfolio.cash)
    print_report(portfolio, plan.chosen, [o for o in to_place if o.action == "sell"], plan.notes + cap_notes)
    print("\nOrders")
    if not to_place:
        print("  none")
    problems = trader.execute(to_place, regime.order_timeout, positions, loop_no, config.ORDER_CAP_PCT * portfolio.cash)
    state.orders_may_be_open = trader.has_open_orders()

    reads, writes, seconds = sig.take_read_count(), budget.WRITES.take_count(), time.time() - started
    print(f"\nloop took {seconds:.1f}s, {reads} SIG reads ({len(targets)} depth reads), {writes} SIG writes")
    worth_logging = [c for c in plan.candidates if c.edge >= config.MIN_EDGE - EPS]  # not every losing candidate
    logbook.log("opportunities", loop_no, [logbook.opportunity_row(c) for c in worth_logging])
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
    parser.add_argument("--day-start-equity", type=float, metavar="AMOUNT",
                        help="today's starting total equity, for the daily-loss kill switch (default: the first reading today)")
    args = parser.parse_args()
    regime = config.REGIMES[config.REGIME]

    races = load_races(config.RACES_FILE)
    setup_all(races)
    state = RunState()
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
