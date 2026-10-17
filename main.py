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
from portfolio import build_holdings
from races import drain_warnings, load_races
from safety import RunState, check_kill_switch, stop_file_exists
from strategy import compute_fair_values, find_opportunities, keep_top
from trading import compute_wants, decide_sells, size_buys


def run_loop(loop_no, races, state, regime):
    """One pass. Returns the kill-switch reason if trading must stop, else None."""
    orders.cancel_all()
    reason = check_kill_switch(state)
    if reason:
        return reason

    fetch_all(races)
    account = sig.fetch_account()
    if account["balance"] is None:
        raise RuntimeError("SIG returned no cash balance")
    cash = account["balance"]
    fair_values = compute_fair_values(races, time.time())
    holdings = build_holdings(sig.fetch_positions(), races, fair_values)
    invested = sum(h.value for h in holdings)
    bankroll = cash + invested
    reason = check_kill_switch(state, bankroll)
    if reason:
        return reason

    candidates = find_opportunities(races, fair_values)
    chosen = keep_top(candidates, config.TOP_N)
    wants = compute_wants(chosen, holdings, bankroll)
    investable = max(0.0, min(config.INVEST_CAP * bankroll - invested, cash))
    sells = decide_sells(holdings, wants, investable)
    buys, notes = size_buys(wants, investable)

    print_report(cash, invested, bankroll, investable, chosen, holdings, sells, notes)
    print("\nOrders")
    for order in sells + buys:
        orders.place_order(order, regime.order_timeout)
    if not sells + buys:
        print("  none")

    logbook.log("opportunities", loop_no, [logbook.opportunity_row(c) for c in candidates])
    logbook.log("orders", loop_no, [logbook.order_row(o) for o in sells + buys])
    logbook.log("loops", loop_no, [{"regime": config.REGIME, "cash": cash, "invested": invested,
                                    "bankroll": bankroll, "opportunities": len(chosen),
                                    "orders": len(sells + buys), "status": "ok"}])
    return None


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
