"""The sells the bot leaves resting on SIG between loops. An early exit (EARLY_EXITS) lists all of a fast position's shares at its
structural value (fair - usual gap) from its first entry for EARLY_EXIT_HOURS, re-listed when that value moves more than EARLY_EXIT_BAND
or new shares are bought, then gives way to a patient exit. A patient exit is a sell of fast shares above the bid where one big order fills it
(bigorder.rest_price), left until the position's time stop (FAST_MAX_HOURS after its first entry) plus PATIENT_MAX_MINUTES (and cancelled earlier when it stops making
sense). A step sells a swapped-out position: a limit at the best bid resting SWAP_STEP_MINUTES, then, for what is left, the next bid
level down, until it is sold or the swap stops paying (fast.ended_steps: the step is cancelled and the position kept); a swap of a
position with a patient exit cancels that exit first.
A patient entry (PATIENT_ENTRIES) is a fast or hold buy (a new position or a top-up) resting one tick above the best bid
for PATIENT_ENTRY_MINUTES (shorter or none on a big fast part or hold edge: trading.entry_minutes), then what is left buys at the ask; its fills are booked to the book of the order when the ledger next meets the positions.
A live run keeps the book in RESTING_FILE, so a restart (Ctrl+C keeps the resting orders on SIG) follows the same orders and keeps their queue place.
A position has one resting sell at most. While a sell rests its shares are reserved, so no other exit sells them again: SIG turns a sell larger than the shares held into
the opposite buy."""
import json
import os
import time
from dataclasses import asdict, dataclass, replace

import config
import logbook
from fast import bet_key, first_entry, held_back, holding_to_resolution, leftover, fast_views, structural_price, time_up
from gapmodel import UNCLASSIFIED
from orders import Order
from strategy import EPS


@dataclass
class Resting:
    order: object      # the resting sell (orders.Order)
    remaining: float   # shares of it not yet filled
    deadline: float    # when it is cancelled: a patient exit then sells at the bid, a step moves to the next bid down, an early exit gives way to a patient exit
    kind: str = "patient"  # "patient", "step" (a swapped-out position's sale) or "early"

    @property
    def label(self):
        return f"{LABELS[self.kind]} {self.order.market_id} {self.order.side.upper()}"


def meets(order, resting):
    """True when `order` would trade against the bot's own resting order: the same side of the same market, a buy at or above a resting
    sell, or a sell at or below a resting buy."""
    if (order.market_id, order.side) != (resting.market_id, resting.side) or order.action == resting.action:
        return False
    return order.price >= resting.price - EPS if order.action == "buy" else order.price <= resting.price + EPS


LABELS = {"patient": "patient exit", "step": "swap step", "early": "early exit", "entry": "patient entry"}
ENTRY_MEMORY = 3600  # seconds after its last patient entry ended that a market's count of unfilled bids is kept (past MAX_BIDS or off the rebid spread it buys at the ask)


class PatientBook:
    def __init__(self, path=None):
        self.path = path          # where the book is kept between runs (live runs); None: nowhere
        self.orders = {}          # order id -> Resting
        self._exhausted = set()   # (market id, side) whose patience ran out: the next exit sells at the bid
        self._stepping = {}       # (market id, side) swapped out -> price of its last step (None before the first)
        self._entered = {}        # (market id, side) -> (patient entries in a row that ended without filling in full, when the last ended): ENTRY_MEMORY after that it starts over
        self._settling = {}       # (market id, side) -> (price, book) of a patient entry that ended since the ledger last met the positions

    def save(self):
        """Keep the book on disk, so a restart can follow the orders still resting on SIG (nothing when it has no path)."""
        if not self.path:
            return
        data = {"orders": [{"id": order_id, "order": asdict(r.order), "remaining": r.remaining, "deadline": r.deadline, "kind": r.kind}
                           for order_id, r in self.orders.items()],
                "stepping": [[*k, p] for k, p in self._stepping.items()], "exhausted": [list(k) for k in self._exhausted],
                "entered": [[*k, t, n] for k, (n, t) in self._entered.items()], "settling": [[*k, *s] for k, s in self._settling.items()]}
        with open(self.path + ".tmp", "w") as f:  # written whole then renamed, so a crash cannot leave half a file
            json.dump(data, f, indent=1)
        os.replace(self.path + ".tmp", self.path)

    @classmethod
    def load(cls, path):
        """The book as the last run left it (empty when there is no file). An unreadable file stops the start: an order the bot
        does not know about could be sold twice."""
        book = cls(path)
        if not os.path.exists(path):
            return book
        try:
            with open(path) as f:
                data = json.load(f)
            book.orders = {row["id"]: Resting(Order(**row["order"]), row["remaining"], row["deadline"], row["kind"]) for row in data["orders"]}
            book._stepping = {(m, side): price for m, side, price in data["stepping"]}
            book._exhausted = {(m, side) for m, side in data["exhausted"]}
            book._entered = {(m, side): ((n or [1])[0], t) for m, side, t, *n in data["entered"]}   # a file from before re-bids counts one bid
            book._settling = {(m, side): (price, (which or ["fast"])[0]) for m, side, price, *which in data["settling"]}   # a file from before hold entries has no book: fast
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise SystemExit(f"Cannot read {path} ({type(e).__name__}: {e}), so nothing was started. It lists the orders resting on SIG. "
                             f"Cancel them on SIG, then delete the file.")
        return book

    def adopt(self, resting):
        """Take in {order id: Order} the trader has on SIG that the book was never told about (a loop cut short before `add`)."""
        for order_id, order in resting.items():
            if order_id not in self.orders:
                self.add(order_id, order)

    def clear(self):
        """Forget every order (they were all cancelled)."""
        self.orders.clear()
        self.save()

    def _sells(self):
        return [r for r in self.orders.values() if r.order.action == "sell"]

    def resting_orders(self):
        """[(Order, shares left)] of every order the bot has resting on SIG: the book SIG shows contains them."""
        return [(r.order, r.remaining) for r in self.orders.values()]

    def drop_self_matches(self, orders):
        """(the orders that do not meet one of the bot's own resting orders, a note for each that does). SIG does not trade an account
        with itself, so such an order would sit unfilled until it is cancelled."""
        kept, notes = [], []
        for o in orders:
            met = next((r for r in self.orders.values() if meets(o, r.order)), None)
            if met is None:
                kept.append(o)
            else:
                notes.append(f"{o.action.upper()} {o.side.upper()} {o.market_id} @ {o.price:.3f} not sent: it would trade against the bot's own "
                             f"{met.label} at {met.order.price:.3f}")
        return kept, notes

    def keys(self):
        """The positions with a sell resting."""
        return {(r.order.market_id, r.order.side) for r in self._sells()}

    def reserved(self, market_id, side):
        """Shares of this position a resting sell holds."""
        return sum(r.remaining for r in self._sells() if (r.order.market_id, r.order.side) == (market_id, side))

    def resting_of(self, market_id, side):
        """The resting sell of this position (one at most; the earliest to be cancelled if a failed cancel left two), or None."""
        mine = [r for r in self._sells() if (r.order.market_id, r.order.side) == (market_id, side)]
        return min(mine, key=lambda r: r.deadline, default=None)

    def entries(self, book=None):
        """{(market id, side): Resting} of the patient entries' resting bids (of one book, or both)."""
        return {(r.order.market_id, r.order.side): r for r in self.orders.values()
                if r.kind == "entry" and book in (None, r.order.book)}

    def entry_value(self, book=None):
        """What the resting bids (of one book, or both) would still cost: cash the plan must not spend twice."""
        return sum(r.remaining * r.order.price for r in self.entries(book).values())

    def ended_bids(self, market_id, side):
        """How many patient entries of this market ended in a row without filling in full, the last within ENTRY_MEMORY (trading.patient_bid
        bids again or buys at the ask by it)."""
        return self._entered.get((market_id, side), (0, 0))[0]

    def take_entry_buys(self):
        """{(market id, side): (price, book)} of the markets whose new shares are the patient entries' (resting, or ended since the last call)."""
        out = {**self._settling, **{k: (r.order.price, r.order.book) for k, r in self.entries().items()}}
        self._settling = {}
        return out

    def _ended(self, r, now, filled=False):
        if r.kind == "entry":
            key = (r.order.market_id, r.order.side)
            if filled:  # nothing left to buy: the next top-up starts over
                self._entered.pop(key, None)
            else:
                self._entered[key] = (self.ended_bids(*key) + 1, now)
            self._settling[key] = (r.order.price, r.order.book)

    def exhausted(self, market_id, side):
        return (market_id, side) in self._exhausted

    def step_keys(self):
        return set(self._stepping)

    def step_price(self, market_id, side):
        """The price of the swapped-out position's last step, or None before its first."""
        return self._stepping.get((market_id, side))

    def start_steps(self, keys):
        """Positions swapped out this loop: from the next loop each is sold by steps (a patient exit it has is cancelled first)."""
        for key in keys:
            self._stepping.setdefault(key, None)

    def add(self, order_id, order):
        kind = order.rest_kind or "patient"
        if kind == "step":
            self._stepping[(order.market_id, order.side)] = order.price
        self.orders[order_id] = Resting(order, order.shares, order.rest_until, kind)
        self.save()

    def sync(self, trader, loop_no):
        """Learn from SIG which resting orders are still open and how much of each is left; log the ones that closed."""
        if not self.orders:
            return []
        still_open, notes = trader.open_resting(), []
        for order_id, r in list(self.orders.items()):
            if order_id in still_open:
                r.remaining = still_open[order_id]
                continue
            filled = trader.fill_of(order_id)
            event = "filled" if filled is not None and filled >= r.order.shares else "partial_fill" if filled else "cancelled"
            logbook.log("orders", loop_no, [logbook.order_event(event, r.order, order_id, filled or 0, "resting order closed")])
            notes.append(f"{r.label}: order {order_id} closed, "
                         f"{(filled or 0):,.0f} of {r.order.shares:,.0f} {'bought' if r.order.action == 'buy' else 'sold'} at {r.order.price:.3f}")
            del self.orders[order_id]
            self._ended(r, time.time(), filled=event == "filled")
        return notes

    def _why_cancel(self, r, holding, view, baseline, ledger, now):
        """Why a resting order should not stay on the book, or None."""
        if r.kind == "entry":  # a candidate that stopped qualifying is cancelled from the plan (cancel_kind)
            if not config.PATIENT_ENTRIES:  # kept across a restart that switched it off
                return "patient entries are off"
            return "bid time up: what is left is bid again or bought at the ask" if now >= r.deadline else None
        if holding is None:
            return "the position is gone"
        if r.kind == "step":
            if now >= r.deadline:
                return f"{config.SWAP_STEP_MINUTES} min up: what is left moves to the next bid down"
            return "edge turned negative: selling at the bid" if holding.negative_edge else None
        if (r.order.market_id, r.order.side) in self._stepping:
            return "swapped out for a better edge: selling by steps from the best bid"
        if holding_to_resolution(now):  # an early listing or patient exit: the position is kept to resolution
            return f"holding to resolution until {time.strftime('%d %b %H:%M', time.localtime(config.FAST_HOLD_UNTIL))}"
        if r.kind == "early":
            return self._why_relist(r, holding, view, baseline, now) if config.EARLY_EXITS else "early exits are off"
        if now >= r.deadline:
            return "patience over: selling at the bid"
        if holding.negative_edge:
            return "edge turned negative: selling at the bid"
        if view is None:
            return "no fast shares left"
        if config.EARLY_EXITS or time_up(first_entry(ledger, holding), now):  # a patient exit by age, or past the time stop: whatever its fast part
            return None
        left = leftover(view, baseline)
        if left is None or left > config.PATIENT_TRIGGER - EPS:
            return "fast part is back above the trigger" if left is not None else "no fair value"
        return None

    def _why_relist(self, r, holding, view, baseline, now):
        """Why an early-exit listing should not stay on the book, or None. Re-listed in the same loop unless its time is up."""
        if now >= r.deadline:
            return "early exit over: a patient exit rests until the time stop"
        if holding.negative_edge:
            return "edge turned negative: selling at the bid"
        if view is None:
            return "no fast shares left"
        left = leftover(view, baseline)
        if held_back(left):
            return f"{left * 100:.1f}c of fast part left: still a buy, so the early exit is held back"
        target, listed = structural_price(view, baseline), structural_price(replace(view, fair=r.order.fair), baseline)
        if target is None:
            return "no fair value"
        if abs(target - listed) > config.EARLY_EXIT_BAND + EPS:  # the listed price itself may sit below it, held under the cap or a wall
            return f"structural value moved from {listed:.3f} to {target:.3f}: re-listing"
        new = view.shares - self.reserved(r.order.market_id, r.order.side)
        if new >= 1:
            return f"{new:,.0f} new shares: re-listing all in one order"
        return None

    def cancel_kind(self, kind, trader, why_of, now, loop_no):
        """Cancel the resting orders of this kind ("entry" or "step") on these markets ({(market id, side): why}, from the plan's
        stale_entries or ended_steps). Returns notes."""
        notes = []
        for order_id, r in list(self.orders.items()):
            why = why_of.get((r.order.market_id, r.order.side))
            if r.kind != kind or why is None:
                continue
            if trader.cancel_resting(order_id):
                del self.orders[order_id]
                self._ended(r, now)
                logbook.log("orders", loop_no, [logbook.order_event("cancelled", r.order, order_id, "", why)])
                notes.append(f"{r.label}: cancelled order {order_id} ({why})")
            else:
                notes.append(f"{r.label}: could not cancel order {order_id} ({why}); trying again")
        return notes

    def end_steps(self, trader, why_of, now, loop_no):
        """Stop selling these swapped-out positions ({(market id, side): why}, from fast.FastPlan.ended_steps): their resting step is
        cancelled and the position is kept. One whose cancel failed keeps its steps, so the next loop tries again."""
        notes = self.cancel_kind("step", trader, why_of, now, loop_no)
        for key, why in why_of.items():
            if key not in self.keys():
                self._stepping.pop(key, None)
                notes.append(f"swap of {key[0]} {key[1].upper()} ended, the position is kept ({why})")
        return notes

    def cancel_all(self, trader, loop_no):
        """Cancel every resting order the book knows (a run that ends for good, or a start that no longer follows them)."""
        for order_id, r in list(self.orders.items()):
            if trader.cancel_resting(order_id):
                del self.orders[order_id]
                logbook.log("orders", loop_no, [logbook.order_event("cancelled", r.order, order_id, "", "shutdown")])
                print(f"  cancelled {r.label} order {order_id} (shutdown)")
            else:
                print(f"  WARNING: could not cancel {r.label} order {order_id}; it stays on SIG until its expiry")
        self.save()

    def review(self, trader, portfolio, ledger, baselines, now, loop_no):
        """Before planning: cancel every resting order that has run out of patience or time, was swapped out, or whose position
        changed. A cancelled order's shares are free again, so the plan can sell them in this same loop."""
        holdings = {(h.market_id, h.side): h for h in portfolio.holdings}
        views = {bet_key(v): v for v in fast_views(portfolio, ledger)}  # all fast shares, including the resting ones
        notes = []
        for order_id, r in list(self.orders.items()):
            key = (r.order.market_id, r.order.side)
            why = self._why_cancel(r, holdings.get(key), views.get(key), baselines.get(r.order.market_id, UNCLASSIFIED), ledger, now)
            if why is None:
                continue
            if trader.cancel_resting(order_id):
                del self.orders[order_id]
                self._ended(r, now)
                if now >= r.deadline and r.kind == "patient":
                    self._exhausted.add(key)
                logbook.log("orders", loop_no, [logbook.order_event("cancelled", r.order, order_id, "", why)])
                notes.append(f"{r.label}: cancelled order {order_id} ({why})")
            else:
                notes.append(f"{r.label}: could not cancel order {order_id} ({why}); trying again")
        self._exhausted &= set(holdings)  # a closed position starts fresh next time
        self._stepping = {k: p for k, p in self._stepping.items() if k in views}  # sold: its steps are done
        self._entered = {k: v for k, v in self._entered.items() if now - v[1] < ENTRY_MEMORY}
        return notes
