"""Which book owns how many shares of each position. SIG reports one net position per market and side, but the hold book and
the fast book can both hold the same one, so the bot keeps its own list of lots, saved in .ledger.json between runs."""
import json
import os
import time
from dataclasses import asdict, dataclass

import config
from strategy import EPS

BOOKS = ("hold", "fast")


@dataclass
class Lot:
    book: str
    market_id: str
    side: str
    shares: float
    entry_price: float
    entry_time: float
    bucket: str | None = None       # hold lots: the bucket this position was placed in (keeps its bucket inside the band)
    grandfathered: bool = False     # adopted from SIG, not bought by the book (kept, never sold just to fit the rules)


@dataclass
class Freeze:
    since: float   # the first loop that saw the bet that far down
    until: float   # TOPUP_FREEZE_HOURS after the last one
    why: str


def _pick_list(saved):
    """A bucket's saved picks as [(market_id, side), ...]; a ledger saved before 2026-11-03 holds one [market_id, side] pair."""
    return [tuple(saved)] if saved and isinstance(saved[0], str) else [tuple(p) for p in saved]


class Ledger:
    def __init__(self, lots=None, picks=None, frozen=None):
        self.lots = list(lots or [])
        self.picks = dict(picks or {})  # hold bucket -> [(market_id, side), ...] chosen last time
        self.frozen = dict(frozen or {})  # (race, wins_when) -> Freeze: no top-up of that bet by either book (freeze_falls)

    def lots_of(self, market_id, side, book=None):
        return [l for l in self.lots if l.market_id == market_id and l.side == side and (book is None or l.book == book)]

    def shares(self, market_id, side, book=None):
        return sum(l.shares for l in self.lots_of(market_id, side, book))

    def entry_price(self, market_id, side, book=None):
        """The share-weighted entry price of the lots, or None without any."""
        lots = self.lots_of(market_id, side, book)
        shares = sum(l.shares for l in lots)
        return sum(l.shares * l.entry_price for l in lots) / shares if shares else None

    def pick_bucket(self, market_id, side):
        """The bucket whose pick this contract is (a hold buy takes it), or None."""
        return next((b for b, keys in self.picks.items() if (market_id, side) in keys), None)

    def bucket_of(self, market_id, side):
        return next((l.bucket for l in self.lots_of(market_id, side, "hold") if l.bucket), None)

    def freeze_falls(self, holdings, now):
        """Freeze every bet held TOPUP_FREEZE_DROP or more below its average entry, until TOPUP_FREEZE_HOURS after the last call
        that saw it that far down, and forget the freezes that ran out. Keyed by the bet, so the equivalent market is frozen too.
        Returns a note per freeze that started at `now` (a loop plans twice with the same `now`; both plans say so)."""
        self.frozen = {bet: f for bet, f in self.frozen.items() if f.until > now}
        for h in holdings:
            entry = self.entry_price(h.market_id, h.side)
            if h.sell_price is None or entry is None or entry - h.sell_price < config.TOPUP_FREEZE_DROP - EPS:
                continue
            until = now + config.TOPUP_FREEZE_HOURS * 3600
            why = (f"frozen: {h.side.upper()} {h.market_id} sells at {h.sell_price:.3f}, {(entry - h.sell_price) * 100:.1f}c below its "
                   f"entry {entry:.3f}; no top-up until {time.strftime('%d %b %H:%M', time.localtime(until))}")
            old = self.frozen.get((h.race, h.wins_when))
            self.frozen[(h.race, h.wins_when)] = Freeze(old.since if old else now, until, why)
        return [f"{race} {f.why}" for (race, _), f in self.frozen.items() if f.since == now]

    def add(self, lot):
        self.lots.append(lot)

    def reconcile(self, positions, now=None, entry_buys=None):
        """Make the lots agree with the SIG positions and return a note for everything that changed.
        Shares nobody owns become a grandfathered hold lot, or a lot of the entry's book at the bid's price in a market where a patient
        entry rests or just ended (`entry_buys`: {(market id, side): (price, book)}); fewer shares than the lots (you sold by hand) shrink the
        lots, fast first; a position that is gone drops its lots."""
        entry_buys = entry_buys or {}
        now = now or time.time()
        held = {(p["market_id"], p["side"]): p for p in positions}
        notes = []
        for key in {(l.market_id, l.side) for l in self.lots} | set(held):
            market_id, side = key
            actual = held[key]["shares"] if key in held else 0.0
            diff = actual - self.shares(market_id, side)
            label = f"{side.upper()} {market_id}"
            if diff > 0.5 and key in entry_buys:
                price, book = entry_buys[key]
                self.add(Lot(book, market_id, side, diff, price, now, bucket=self.pick_bucket(market_id, side) if book == "hold" else None))
                notes.append(f"{label}: {diff:,.0f} shares bought by the patient entry, booked to the {book} book")
            elif diff > 0.5:
                self.add(Lot("hold", market_id, side, diff, held[key]["avg_price"], now, grandfathered=True))
                notes.append(f"{label}: {diff:,.0f} shares nobody owned were adopted as a grandfathered hold lot")
            elif diff < -0.5:
                self._shrink(market_id, side, -diff, "fast")  # sold by hand: the fast book's lots shrink first
                notes.append(f"{label}: SIG shows {-diff:,.0f} fewer shares than the ledger (sold by hand?); lots reduced")
        self._prune()
        return notes

    def record_fills(self, fills, now=None):
        """Make the lots agree with what orders just filled: [(Order, shares)]. A buy adds a lot to the order's book (a hold
        buy takes the bucket whose pick it is); a sale takes shares off the order's own book first, then the other one."""
        now = now or time.time()
        for order, shares in fills:
            if order.action == "buy":
                self.add(Lot(order.book, order.market_id, order.side, shares, order.price, now,
                             bucket=self.pick_bucket(order.market_id, order.side) if order.book == "hold" else None))
            else:
                self._shrink(order.market_id, order.side, shares, order.book)
        self._prune()

    def _prune(self):
        self.lots = [l for l in self.lots if l.shares > 0.5]

    def _shrink(self, market_id, side, shares, first):
        """Take `shares` off the lots of one position: the `first` book's lots, newest first, then the other book's."""
        for book in (first, *(b for b in BOOKS if b != first)):
            for lot in sorted(self.lots_of(market_id, side, book), key=lambda l: l.entry_time, reverse=True):
                cut = min(lot.shares, shares)
                lot.shares -= cut
                shares -= cut

    def save(self, path=None):
        path = path or config.LEDGER_FILE
        with open(path + ".tmp", "w") as f:  # written whole then renamed, so a crash cannot leave half a file
            json.dump({"lots": [asdict(l) for l in self.lots], "picks": self.picks,
                       "frozen": [[race, wins, asdict(f)] for (race, wins), f in self.frozen.items()]}, f, indent=1)
        os.replace(path + ".tmp", path)

    @classmethod
    def load(cls, path=None):
        path = path or config.LEDGER_FILE
        if not os.path.exists(path):
            return cls()
        with open(path) as f:
            data = json.load(f)
        return cls([Lot(**row) for row in data["lots"]], {b: _pick_list(p) for b, p in data["picks"].items()},
                   {(race, wins): Freeze(**f) for race, wins, f in data.get("frozen", [])})


def book_value(holdings, ledger, book):
    """What a book holds of these holdings, valued at each one's sell price: the value split by the book's share of its lots."""
    total = 0.0
    for h in holdings:
        owned = ledger.shares(h.market_id, h.side)
        if owned > 0:
            total += h.value * ledger.shares(h.market_id, h.side, book) / owned
    return total
