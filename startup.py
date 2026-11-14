"""The check at start-up: show the mode, the balance and the limits, and for live trading require typing LIVE."""
import time
from datetime import date

import config
from guard import read_json
from hold import roi_floor
from trading import max_order_value


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
    own = len(state.patient.orders)
    print(f"  open orders now   : {len(open_orders)}" + (f"  ({own} of them resting orders from the last run, followed again; the others are left alone. "
                                                         f"The kill switch cancels ALL of them, Ctrl+C none)" if open_orders and live else ""))
    print(f"  strategy          : {config.STRATEGY} ({'two books: structural hold + fast opportunity' if config.STRATEGY == 'v2' else 'top 4 by Kelly with swaps'})")
    print("  risk limits")
    print(f"    max invested    : {config.INVEST_CAP:.0%} of total equity  (blocks new buys only; never sells)")
    print(f"    per race        : {config.RACE_CAP:.0%} of total equity ({config.SINGLE_SOURCE_RACE_CAP:.0%} when the fair value "
          f"rests on one source)")
    print(f"    single order    : {config.ORDER_CAP_PCT:.1%} of total equity = {max_order_value(equity):,.2f} per buy "
          f"(sells are not capped)")
    print(f"    orders per loop : {config.MAX_ORDERS_PER_LOOP}, sells first")
    if config.STRATEGY == "v2":
        print(f"    books           : hold {config.HOLD_BOOK_PCT:.0%} + fast {config.FAST_BOOK_PCT:.0%} of total equity; position cap {config.POSITION_CAP:.0%}")
        print(f"    hold entry      : structural, edge at least {config.HOLD_MIN_EDGE * 100:.0f}c, or any classified market with an edge of "
              f"{config.HOLD_BIG_EDGE * 100:.0f}c+; best z (edge / sqrt(p(1-p)), edge per unit of risk)")
        print("    hold slots      : " + ", ".join(f"{name} {config.HOLD_SLOTS[name]} x {config.HOLD_BOOK_PCT * share / config.HOLD_SLOTS[name]:.1%}"
                                             for name, _, _, share in config.BUCKETS[::-1]) + " of equity each; the bets the hold book holds take their slots first and keep them; an empty slot stays cash")
        print(f"    hold min z      : {config.HOLD_MIN_Z:.2f} in every bucket, so a minimum ROI of "
              + ", ".join(f"{roi_floor(p):.0%} at p {p:g}" for p in (0.95, 0.75, 0.5, 0.3)) + f"; switch for {config.HOLD_SWITCH_Z:.2f} more z")
        print(f"    hold swaps      : loop 1 builds the book from the bets held; from loop 2 a new bet with {config.HOLD_SWAP_Z:.2f} more z "
              f"than the weakest held bet (those over the slots first) replaces it, and that bet is sold at the bid")
        print(f"    top-up freeze   : no buy of a bet held {config.TOPUP_FREEZE_DROP * 100:.0f}c+ below its entry, until "
              f"{config.TOPUP_FREEZE_HOURS}h after it was last that far down; nothing is sold")
        print(f"    fast entry      : {config.FAST_MIN_EDGE * 100:.0f}c beyond the usual gap, biggest fast part first; up to {config.FAST_MAX_POSITIONS} positions of "
              f"{config.FAST_POSITION_PCT:.0%} of the fast book; time stop {config.FAST_MAX_HOURS}h; a swap needs {config.FAST_SWAP_Z:.2f} more z of fast part")
        print(f"    fast liquidity  : traded volume under {config.MIN_RECENT_VOLUME:,} since yesterday: no entry; under {config.LIQUIDITY_HIGH / 1e6:.0f}M: slice x{config.MEDIUM_SLICE_FACTOR:g}")
        print(f"    patient exits   : {'ON' if config.PATIENT_EXITS else 'OFF'}"
              + (f" (a fast position whose fast part falls below {config.PATIENT_TRIGGER * 100:.0f}c rests one sell above the bid where a big order ({config.BIG_ORDER_WEEK_SHARE:.0%} of the 7-day volume) fills it, until its "
                 f"{config.FAST_MAX_HOURS}h time stop plus {config.PATIENT_MAX_MINUTES} min, then sells at the bid; a swapped-out position, "
                 f"resting or not, sells from the best bid one bid level lower every {config.SWAP_STEP_MINUTES} min)" if config.PATIENT_EXITS else ""))
        print(f"    patient entries : {'ON' if config.PATIENT_ENTRIES and config.PATIENT_EXITS else 'OFF'}"
              + (f" (a fast or hold buy (new or top-up) on a spread over 0.5c bids one tick above the best bid for {config.PATIENT_ENTRY_MINUTES} min, "
                 f"{config.PATIENT_ENTRY_SHORT_MINUTES} min for a fast part (hold: edge) over {config.PATIENT_ENTRY_SHORT_PART * 100:g}c, not at all over "
                 f"{config.PATIENT_ENTRY_MAX_PART * 100:g}c, then buys at the ask, except that on a spread over {config.PATIENT_REBID_SPREAD * 100:g}c it bids again, "
                 f"up to {config.PATIENT_MAX_BIDS} bids in a row)" if config.PATIENT_ENTRIES and config.PATIENT_EXITS else
                 " (needs patient exits on)" if config.PATIENT_ENTRIES else ""))
        print(f"    early exits     : {'ON' if config.EARLY_EXITS and config.PATIENT_EXITS else 'OFF'}"
              + (f" (from its first entry a fast position lists all its fast shares at the structural value, fair - usual gap, at most "
                 f"{config.EARLY_EXIT_MAX_TICKS} ticks above the best ask and in front of any liquidity wall, for "
                 f"{config.EARLY_EXIT_HOURS}h, re-listed when it moves more than {config.EARLY_EXIT_BAND * 100:.0f}c, held back while its fast "
                 f"part left is {config.FAST_MIN_EDGE * 100:g}c or more; then the patient exit until the time stop)" if config.EARLY_EXITS and config.PATIENT_EXITS else
                 " (needs patient exits on)" if config.EARLY_EXITS else ""))
        when = lambda t: time.strftime("%d %b %H:%M", time.localtime(t))
        print("    usual gap       : " + (f"FROZEN at {when(config.BASELINE_FROZEN_AT)} (the {config.HISTORY_HOURS}h before it)"
                                         if config.BASELINE_FROZEN_AT else f"the last {config.HISTORY_HOURS}h"))
        if config.FAST_HOLD_UNTIL:
            print(f"    hold window     : until {when(config.FAST_HOLD_UNTIL)} the fast book sells only on a negative edge or a swap "
                  f"(swap margin z {config.FAST_SWAP_Z:.2f}); resting early and patient exits are cancelled")
        print("    thin markets    : " + ", ".join(f"fast part {floor * 100:.0f}c+: slice x{share:g}" for floor, share in reversed(config.THIN_SLICES))
              + f" (volume under {config.LIQUIDITY_LOW / 1e6:.0f}M)")
    else:
        print(f"    minimum edge    : {config.MIN_EDGE * 100:.0f}c")
    baseline = state.day_start_equity if state.day == date.today() else None
    print(f"  kill switch       : stops the bot and cancels all orders if total equity falls {config.MAX_DAILY_LOSS:.0%} "
          f"from the start of the day, or {config.MAX_CONSECUTIVE_ERRORS} loops in a row fail")
    print("    start-of-day equity: " + (f"{baseline:,.2f} (trips below {baseline * (1 - config.MAX_DAILY_LOSS):,.2f})"
                                          if baseline else f"not set yet; the first reading today ({equity:,.2f}) will be used"))
    print(f"    STOP file       : create {config.STOP_FILE} to stop it")
    print(f"  races             : {n_races} enabled, {n_disabled} disabled; regime {config.REGIME} "
          f"(order timeout {regime.order_timeout}s, loop pause {regime.loop_pause}s)")
    if config.REGIME == "election":
        calls, paused = read_json(config.CALLS_FILE, {}), read_json(config.PAUSE_FILE, [])
        print(f"    election guard  : ON. Polymarket fetched over {config.ELECTION_STALE_SECONDS}s ago skips the race (Kalshi is optional: "
              f"without it the race trades on Polymarket); a fair move over "
              f"{config.ELECTION_BREAKER_MOVE * 100:.0f}c in {config.ELECTION_BREAKER_SECONDS}s that only Kalshi backs pauses it "
              f"{config.ELECTION_BREAKER_MINUTES} min; a changed fair is used after {config.ELECTION_PERSIST_LOOPS} loops within "
              f"{config.ELECTION_PERSIST_BAND * 100:.0f}c; z less {config.ELECTION_K:g} x its uncertainty; buys in races not called are "
              f"{config.ELECTION_UNCALLED_SIZE:.0%} of the order cap")
        print(f"    called / paused : {len(calls)} called in calls.json {sorted(calls)}, {len(paused)} paused in pause.json {sorted(paused)}")
    else:
        print("    election guard  : off (it runs when REGIME is election)")
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
