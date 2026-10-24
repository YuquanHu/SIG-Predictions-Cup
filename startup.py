"""The check at start-up: show the mode, the balance and the limits, and for live trading require typing LIVE."""
from datetime import date

import config


def positions_value(positions):
    return sum(p["shares"] * (p["current_price"] or 0) for p in positions)


def startup_check(mode, account, positions, open_orders, state, n_races, n_disabled, regime, ask=input):
    """Print the facts a person needs before real orders go out. In LIVE mode, anything other than typing LIVE exits."""
    cash = account["balance"]
    marked = positions_value(positions)
    equity = cash + marked
    live = mode == "LIVE"
    line = "=" * 74
    print(f"\n{line}\n  STARTUP CHECK\n{line}")
    print(f"  mode              : {'LIVE - REAL ORDERS WILL BE SENT TO SIG' if live else 'DRY RUN - no orders are sent'}")
    print(f"  SIG account       : {account.get('username')}")
    print(f"  cash balance      : {cash:,.2f}")
    print(f"  positions (SIG)   : {marked:,.2f}  ({len(positions)} open)")
    print(f"  total equity      : {equity:,.2f}")
    print(f"  open orders now   : {len(open_orders)}" + ("  (left alone; the kill switch and shutdown cancel ALL of them)"
                                                         if open_orders and live else ""))
    print("  risk limits")
    print(f"    max invested    : {config.INVEST_CAP:.0%} of total equity  (blocks new buys only; never sells)")
    print(f"    per race        : {config.RACE_CAP:.0%} of total equity ({config.SINGLE_SOURCE_RACE_CAP:.0%} when the fair value "
          f"rests on one source)")
    print(f"    single order    : {config.ORDER_CAP_PCT:.1%} of cash balance = {config.ORDER_CAP_PCT * cash:,.2f} per buy "
          f"(sells are not capped)")
    print(f"    orders per loop : {config.MAX_ORDERS_PER_LOOP}, sells first")
    print(f"    minimum edge    : {config.MIN_EDGE * 100:.0f}c")
    baseline = state.day_start_equity if state.day == date.today() else None
    print(f"  kill switch       : stops the bot and cancels all orders if total equity falls {config.MAX_DAILY_LOSS:.0%} "
          f"from the start of the day, or {config.MAX_CONSECUTIVE_ERRORS} loops in a row fail")
    print("    start-of-day equity: " + (f"{baseline:,.2f} (trips below {baseline * (1 - config.MAX_DAILY_LOSS):,.2f})"
                                          if baseline else f"not set yet; the first reading today ({equity:,.2f}) will be used"))
    print(f"    STOP file       : create {config.STOP_FILE} to stop it")
    print(f"  races             : {n_races} enabled, {n_disabled} disabled; regime {config.REGIME} "
          f"(order timeout {regime.order_timeout}s, loop pause {regime.loop_pause}s)")
    print(line)
    if not live:
        return
    try:
        typed = ask("  LIVE trading sends real orders. Type LIVE (capitals) to continue: ").strip()
    except EOFError:
        typed = ""
    if typed != "LIVE":
        raise SystemExit("Not confirmed (you did not type LIVE). Nothing was sent.")
    print("  confirmed.\n")
