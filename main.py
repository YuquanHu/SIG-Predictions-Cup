#!/usr/bin/env python3
"""Stage 4: the full trading loop in dry-run mode. Prints the orders it WOULD place; sends nothing.

    python3 main.py            # loop until STOP file, kill switch, or Ctrl+C
    python3 main.py --once     # run a single loop and exit

Each loop: cancel all -> kill switch -> fetch prices and account -> opportunities -> top 4 ->
exits and swaps -> size -> place orders -> log -> wait for the regime's pause.
"""
import argparse
import time
import traceback

import config
import logbook
import orders
import sig
from display import print_report
from feeds import fetch_all, setup_all
from portfolio import build_portfolio
from races import drain_warnings, load_races
from safety import RunState, check_loss, check_stop, stop_file_exists
from strategy import compute_fair_values, find_opportunities, keep_top
from trading import compute_wants, decide_sells, size_buys


def run_loop(loop_no, races, state, regime):
    """One pass. Returns the reason trading must stop, else None."""
    orders.cancel_all()
    reason = check_stop(state)
    if reason:
        return reason

    fetch_all(races)
    fair_values = compute_fair_values(races, time.time())
    portfolio = build_portfolio(sig.fetch_account()["balance"], sig.fetch_positions(), races, fair_values)
    reason = check_loss(state, portfolio.bankroll)
    if reason:
        return reason

    candidates = find_opportunities(races, fair_values)
    chosen = keep_top(candidates, config.TOP_N)
    wants = compute_wants(chosen, portfolio)
    buys, notes = size_buys(wants, portfolio.investable)
    to_place = decide_sells(portfolio, wants) + buys

    print_report(portfolio, chosen, [o for o in to_place if o.action == "sell"], notes)
    print("\nOrders")
    for order in to_place:
        orders.place_order(order, regime.order_timeout)
    if not to_place:
        print("  none")

    logbook.log("opportunities", loop_no, [logbook.opportunity_row(c) for c in candidates])
    logbook.log("orders", loop_no, [logbook.order_row(o) for o in to_place])
    logbook.log("loops", loop_no, [{"regime": config.REGIME, "cash": portfolio.cash, "invested": portfolio.invested,
                                    "bankroll": portfolio.bankroll, "opportunities": len(chosen),
                                    "orders": len(to_place), "status": "ok"}])
    return None


def guarded_loop(loop_no, races, state, regime):
    """run_loop with its failures counted and logged, so one bad loop does not end the run."""
    try:
        stop_reason = run_loop(loop_no, races, state, regime)
        state.errors_in_row = 0
    except Exception as e:
        stop_reason = None
        state.errors_in_row += 1
        print(f"  ERROR ({state.errors_in_row} in a row): {type(e).__name__}: {e}")
        logbook.log("errors", loop_no, [{"where": traceback.extract_tb(e.__traceback__)[-1].name,
                                         "error": f"{type(e).__name__}: {e}"}])
    logbook.log("errors", loop_no, [{"where": "fetch", "error": w.strip()} for w in drain_warnings()])
    return stop_reason


def wait(seconds):
    """Sleep up to `seconds`, waking early if the STOP file appears."""
    end = time.time() + seconds
    while time.time() < end and not stop_file_exists():
        time.sleep(min(1.0, max(0.0, end - time.time())))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run one loop and exit")
    args = parser.parse_args()
    if not config.DRY_RUN:
        raise SystemExit("DRY_RUN is False, but live orders are not built yet. Set DRY_RUN = True in config.py.")
    regime = config.REGIMES[config.REGIME]

    races = load_races(config.RACES_FILE)
    print(f"DRY RUN: no orders are sent. {len(races)} race(s), regime {config.REGIME} "
          f"(order timeout {regime.order_timeout}s, loop pause {regime.loop_pause}s). "
          f"Create {config.STOP_FILE} or press Ctrl+C to stop.")
    setup_all(races)

    state, loop_no = RunState(), 0
    try:
        while True:
            loop_no += 1
            print(f"\n===== loop {loop_no} at {time.strftime('%H:%M:%S')} =====")
            stop_reason = guarded_loop(loop_no, races, state, regime)
            if stop_reason:
                print(f"\nKILL SWITCH: {stop_reason}")
                logbook.log("loops", loop_no, [{"regime": config.REGIME, "status": f"stopped: {stop_reason}"}])
                break
            if args.once:
                break
            print(f"\nwaiting {regime.loop_pause}s...")
            wait(regime.loop_pause)
    except KeyboardInterrupt:
        print("\nCtrl+C received, shutting down.")
    finally:
        orders.cancel_all()
        print("stopped.")


if __name__ == "__main__":
    main()
