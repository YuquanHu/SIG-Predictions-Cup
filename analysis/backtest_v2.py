#!/usr/bin/env python3
"""Backtest of strategy v2 (hold book + fast book) on cached history, from the start of the competition. Read-only: no orders.

    python3 analysis/backtest_data.py        # first: fetch the history (cached in analysis/backtest_cache)
    python3 analysis/backtest_v2.py          # baseline with the live settings
    python3 analysis/backtest_v2.py --grid   # plus FAST_MIN_EDGE 3c / 4c / 5c, each with the volume rules on and off

At every 5-minute step it builds the same inputs main.run_loop builds (quotes, fair values, baselines, volumes, portfolio)
and calls the bot's own planner (planner.make_plan_v2), so the strategy code is the live code. Only the market is simulated:
  SIG quote   last trade price +- HALF_SPREAD (no order book is recorded); skipped when the last trade is over SIG_MAX_AGE old
  SIG depth   SIZE_SHARE of what the market traded in the last hour (at least its average hour over the last day)
  external    Kalshi hourly mid and Polymarket price, as the bot's two fair value sources
  fills       in full at the quote price, up to the depth above (the planner sizes its orders to it)
Not simulated: the write budget, order timeouts and the daily-loss kill switch (the summary says if it would have tripped).
"""
import argparse
import copy
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import gapmodel
from backfill import EXTERNAL_MAX_AGE
from backtest_data import START, load
from backtest_metrics import DAY, summarize_metrics
from books import Ledger
from cooldown import Cooldown
from liquidity import Volume, VolumeStore, describe
from planner import make_plan_v2
from polymarket import setup as polymarket_setup
from portfolio import build_portfolio
from races import iter_contracts, iter_outcomes, load_races
from sig import setup as sig_setup
from strategy import compute_fair_values
from trading import cap_order_sizes

STEP = 300                    # evaluate every 5 minutes
HOUR = 3600
HALF_SPREAD = 0.005           # paid on the way in and again on the way out
SIZE_SHARE = 0.25             # of the market's hourly volume, per step
SIG_MAX_AGE = 2 * HOUR        # an older SIG last trade is no quote
STALE_ENTRY = 15 * 60         # an entry priced off a SIG last trade older than this is reported separately
TRADE_START = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
START_EQUITY = 100_000.0
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_out")
VOLUME_RULES_OFF = {"LIQUIDITY_LOW": 0, "LIQUIDITY_HIGH": 0, "MIN_RECENT_VOLUME": 0}  # every known market takes a full slice


@dataclass
class Lot:
    shares: float = 0.0
    cost: float = 0.0


@dataclass
class FastTrade:
    race: str
    market_id: str
    side: str
    entry_time: float
    tier: str
    quote_age: float = 0.0   # seconds since SIG's last trade when the position was opened
    shares: float = 0.0
    cost: float = 0.0
    sold: float = 0.0
    proceeds: float = 0.0
    exit_time: float | None = None
    reason: str = ""

    @property
    def pnl(self):
        """Realised profit on the shares sold so far."""
        return self.proceeds - self.cost * (self.sold / self.shares) if self.shares else 0.0


class Account:
    """Cash and cost-basis lots per (market, side, book). Sells take the order's own book first, then the other, as the ledger does."""

    def __init__(self, cash):
        self.cash = cash
        self.lots = {}          # (market_id, side, book) -> Lot
        self.realised = {"hold": 0.0, "fast": 0.0}
        self.marks = {}         # (market_id, side) -> latest sell price seen
        self.open_trades = {}   # (market_id, side) -> FastTrade
        self.trades = []        # closed FastTrades

    def shares(self, market_id, side, book=None):
        return sum(l.shares for (m, s, b), l in self.lots.items() if (m, s) == (market_id, side) and book in (None, b))

    def positions(self):
        """The SIG positions list build_portfolio reads: net shares per (market, side)."""
        keys = {(m, s) for (m, s, _), l in self.lots.items() if l.shares > 0}
        return [{"market_id": m, "side": s, "shares": self.shares(m, s), "current_price": self.marks.get((m, s))} for m, s in keys]

    def buy(self, order, shares, t, entry):
        lot = self.lots.setdefault((order.market_id, order.side, order.book), Lot())
        lot.shares += shares
        lot.cost += shares * order.price
        self.cash -= shares * order.price
        if order.book == "fast":
            key = (order.market_id, order.side)
            trade = self.open_trades.setdefault(key, FastTrade(order.race, order.market_id, order.side, t, *entry))
            trade.shares += shares
            trade.cost += shares * order.price

    def sell(self, order, shares, t):
        left = shares
        for book in (order.book, *(b for b in self.realised if b != order.book)):
            lot = self.lots.get((order.market_id, order.side, book))
            if not lot or lot.shares <= 0 or left <= 0:
                continue
            cut = min(lot.shares, left)
            cost = lot.cost * cut / lot.shares
            self.realised[book] += cut * order.price - cost
            lot.shares -= cut
            lot.cost -= cost
            left -= cut
            self.cash += cut * order.price
            if book == "fast":
                self._book_fast_sale(order, cut, t, lot.shares)

    def _book_fast_sale(self, order, shares, t, remaining):
        trade = self.open_trades.get((order.market_id, order.side))
        if trade is None:
            return
        trade.sold += shares
        trade.proceeds += shares * order.price
        if remaining < 0.5:
            trade.exit_time, trade.reason = t, order.reason
            self.trades.append(self.open_trades.pop((order.market_id, order.side)))

    def execute(self, orders, t, entry_of):
        """Fill the orders at their limit prices; returns [(order, shares)]. entry_of(market_id) -> (liquidity tier, quote age)."""
        fills = []
        for o in orders:
            if o.action == "buy":
                shares = min(o.shares, self.cash / o.price)
                if shares * o.price >= 1.0:
                    self.buy(o, shares, t, entry_of(o.market_id))
                    fills.append((o, shares))
            else:
                shares = min(o.shares, self.shares(o.market_id, o.side))
                if shares > 0:
                    self.sell(o, shares, t)
                    fills.append((o, shares))
        return fills

    def value_and_pnl(self):
        """(total value of the positions at the latest marks, {book: realised + unrealised profit})."""
        value, pnl = 0.0, dict(self.realised)
        for (m, s, book), lot in self.lots.items():
            if lot.shares > 0:
                worth = lot.shares * self.marks.get((m, s), lot.cost / lot.shares)
                value += worth
                pnl[book] += worth - lot.cost
        return value, pnl


@dataclass
class Result:
    name: str
    equity: list = field(default_factory=list)      # [(time, total equity)]
    book_pnl: dict = field(default_factory=lambda: {"hold": [], "fast": []})  # book -> [(time, profit)]
    trades: list = field(default_factory=list)      # FastTrade, closed first then still open
    orders: int = 0
    stale_quotes: int = 0                           # contract-steps with no SIG quote because the last trade was too old
    volume_blocked: set = field(default_factory=set)  # (day, label) fast candidates the volume rules held back
    first_order: float | None = None


class Sim:
    def __init__(self, races, history):
        self.races, self.history = races, history
        self.account = Account(START_EQUITY)
        self.ledger = Ledger()
        self.t = START
        self.cooldown = Cooldown(config.COOLDOWN_SECONDS, clock=lambda: self.t)
        self.gaps = gapmodel.GapStore()
        self.baselines = {}
        self.market_ids = [c["market_id"] for _, c in iter_contracts(races)]
        self.result = Result("")

    def set_external(self, t):
        for _, _, o in iter_outcomes(self.races):
            for source, series in (("kalshi", self.history.kalshi.get(o["kalshi_id"])), ("poly", self.history.poly.get(o["poly_yes_token"]))):
                price = series.at(t, EXTERNAL_MAX_AGE) if series else None
                o[source].update(bid=price, ask=price, updated=t if price is not None else None)

    def depth_shares(self, ch, t, price):
        """Shares the market can take at a step: SIZE_SHARE of its traded value in the last hour, or of its average hour over
        the last day when that is more."""
        hourly = max(ch.traded.between(t - HOUR, t), ch.traded.between(t - DAY, t) / 24)
        return SIZE_SHARE * hourly / max(price, 0.01)

    def set_sig(self, t):
        for _, c in iter_contracts(self.races):
            ch = self.history.contracts.get(c["market_id"])
            price = ch.price.at(t) if ch else None
            fresh = price is not None and t - ch.price.last_time(t) <= SIG_MAX_AGE
            if price is not None and not fresh and t >= TRADE_START:
                self.result.stale_quotes += 1
            if not fresh:
                c["yes"].update(bid=None, ask=None, bid_size=None, ask_size=None, updated=None)
                continue
            bid, ask = max(price - HALF_SPREAD, 0.005), min(price + HALF_SPREAD, 0.995)
            size = self.depth_shares(ch, t, price)
            c["yes"].update(bid=bid, ask=ask, bid_size=size, ask_size=size, updated=t)

    def quote_age(self, market_id, t):
        ch = self.history.contracts.get(market_id)
        last = ch.price.last_time(t) if ch else None
        return t - last if last is not None else 0.0

    def volumes(self, t):
        """The VolumeStore as of t: all-time traded value, and value since the start of the previous UTC day."""
        yesterday = t - t % DAY - DAY
        return VolumeStore({m: Volume(ch.traded.total(t), ch.traded.between(yesterday, t), t) for m, ch in self.history.contracts.items()})

    def update_marks(self):
        """Latest sell price of every held position that has a quote now."""
        for _, c in iter_contracts(self.races):
            y = c["yes"]
            if y["bid"] is not None and y["ask"] is not None:
                self.account.marks[(c["market_id"], "yes")] = y["bid"]
                self.account.marks[(c["market_id"], "no")] = 1 - y["ask"]

    def step(self, t):
        self.t = t
        self.set_external(t)
        self.set_sig(t)
        fair_values = compute_fair_values(self.races, t)
        if t % gapmodel.SAMPLE_SECONDS == 0:
            self.gaps.add_all(gapmodel.current_gaps(self.races, fair_values), t)
            self.baselines = self.gaps.baselines(self.market_ids, t)
        self.update_marks()
        if t >= TRADE_START:
            self.trade(t, fair_values)
            if t % HOUR == 0:
                self.record(t)

    def trade(self, t, fair_values):
        volumes = self.volumes(t)
        portfolio = build_portfolio(self.account.cash, self.account.positions(), self.races, fair_values)
        plan = make_plan_v2(self.races, fair_values, portfolio, self.cooldown, self.baselines, self.ledger, volumes, t)
        orders, _ = cap_order_sizes(plan.orders, portfolio.bankroll)
        for row in plan.fast_rows:  # liquidity.liquidity_blocker's reasons start with "volume" or "only"
            if row.note.startswith(("volume", "only")):
                self.result.volume_blocked.add((t - t % DAY, row.label))
        fills = self.account.execute(orders, t, lambda market_id: (describe(volumes.get(market_id)).split()[0], self.quote_age(market_id, t)))
        if fills:
            self.result.orders += len(fills)
            self.result.first_order = self.result.first_order or t
        self.ledger.record_fills(fills, t)
        self.cooldown.record(orders)

    def record(self, t):
        value, pnl = self.account.value_and_pnl()
        self.result.equity.append((t, self.account.cash + value))
        for book, series in self.result.book_pnl.items():
            series.append((t, pnl[book]))


def run(races, history, name="baseline", overrides=None, end=None):
    """One simulation with `overrides` applied to config (and put back afterwards). Returns a Result."""
    target = lambda k: config if hasattr(config, k) else sys.modules[__name__]   # config settings or this module's HALF_SPREAD etc.
    saved = {k: getattr(target(k), k) for k in (overrides or {})}
    for k, v in (overrides or {}).items():
        setattr(target(k), k, v)
    try:
        sim = Sim(copy.deepcopy(races), history)
        sim.result.name = name
        last = end or max((ch.price.times[-1] for ch in history.contracts.values()), default=START)
        for t in range(START, int(last) + 1, STEP):
            sim.step(t)
        sim.result.trades = sim.account.trades + list(sim.account.open_trades.values())
        return sim.result
    finally:
        for k, v in saved.items():
            setattr(target(k), k, v)


def summarize(result):
    """Metrics for the total and each book (a book's curve is the start equity plus that book's profit), plus trade stats."""
    out = {"name": result.name, "orders": result.orders, "stale_quotes": result.stale_quotes,
           "volume_blocked": len(result.volume_blocked), "first_order": result.first_order}
    if len(result.equity) >= 2:
        out["total"] = summarize_metrics(result.equity)
        for book, series in result.book_pnl.items():
            out[book] = summarize_metrics([(t, START_EQUITY + p) for t, p in series])
    closed = [t for t in result.trades if t.exit_time]
    out["fast_closed"] = len(closed)
    out["fast_open"] = len(result.trades) - len(closed)
    out["fast_win_rate"] = sum(t.pnl > 0 for t in closed) / len(closed) if closed else None
    out["fast_pnl_closed"] = sum(t.pnl for t in closed)
    out["fast_avg_hours"] = sum(t.exit_time - t.entry_time for t in closed) / len(closed) / HOUR if closed else None
    reasons = {}
    for t in closed:
        key = t.reason.split(":")[0].split(" ")[0] if t.reason else "?"
        reasons[key] = reasons.get(key, 0) + 1
    out["fast_exits"] = reasons
    stale = [t for t in closed if t.quote_age > STALE_ENTRY]
    out["fast_stale_entries"] = len(stale)
    out["fast_stale_pnl"] = sum(t.pnl for t in stale)
    return out


def fmt(x, pct=False, digits=2):
    if x is None:
        return "-"
    return f"{x * 100:+.{digits}f}%" if pct else f"{x:,.{digits}f}"


def report(summary):
    first = summary["first_order"]
    lines = [f"== {summary['name']} ==  " + (f"orders filled {summary['orders']}, first order "
                                              f"{datetime.fromtimestamp(first, timezone.utc):%b %d %H:%M} UTC" if first else "no orders")]
    for key, label in (("total", "total"), ("hold", "hold book (marked, not settled)"), ("fast", "fast book")):
        m = summary.get(key)
        if not m:
            continue
        d = m["max_drawdown"]
        lines.append(f"  {label:32s} gain {fmt(m['gain'], digits=0):>9} SUSQies ({fmt(m['return_pct'], True)}), vol/day {fmt(m['volatility_daily'], True)}, "
                     f"Sharpe {fmt(m['sharpe_annual'])} (annualised, {m['days']} days), max drawdown {fmt(d['value'], digits=0)} ({fmt(-d['pct'], True)})")
    lines.append(f"  fast trades: {summary['fast_closed']} closed ({fmt(summary['fast_pnl_closed'], digits=0)} SUSQies realised, win rate "
                 f"{fmt(summary['fast_win_rate'] and summary['fast_win_rate'] * 100, digits=0)}%, average hold {fmt(summary['fast_avg_hours'], digits=1)}h), {summary['fast_open']} still open; "
                 f"exits {summary['fast_exits']}")
    lines.append(f"  entries priced off a SIG last trade over {STALE_ENTRY // 60} min old: {summary['fast_stale_entries']} of "
                 f"{summary['fast_closed']} closed trades, {fmt(summary['fast_stale_pnl'], digits=0)} of the realised profit")
    lines.append(f"  volume rules held back {summary['volume_blocked']} (day, bet) fast candidates; {summary['stale_quotes']} contract-steps had no SIG quote (last trade over {SIG_MAX_AGE // HOUR}h old)")
    total = summary.get("total")
    if total and total["worst_day"] is not None and total["worst_day"] <= -config.MAX_DAILY_LOSS:
        lines.append(f"  NOTE: a day fell {fmt(-total['worst_day'], True, 1)}: the live daily-loss kill switch ({config.MAX_DAILY_LOSS:.0%}) would have stopped the bot")
    return "\n".join(lines)


def grid():
    """The runs to compare: the live settings, then each FAST_MIN_EDGE with the volume rules on and off."""
    runs = [("baseline (live settings)", {})]
    for edge in (0.03, 0.04, 0.05):
        runs.append((f"edge {edge * 100:.0f}c, volume rules on", {"FAST_MIN_EDGE": edge}))
        runs.append((f"edge {edge * 100:.0f}c, volume rules off", {"FAST_MIN_EDGE": edge, **VOLUME_RULES_OFF}))
    return runs


def robustness():
    """The live settings under harsher fills: a wider half spread, only fresh SIG prices, and both."""
    return [("baseline (live settings)", {}), ("half spread 1c", {"HALF_SPREAD": 0.01}),
            ("fresh SIG price only (30 min)", {"SIG_MAX_AGE": 1800}),
            ("half spread 1c + fresh SIG price", {"HALF_SPREAD": 0.01, "SIG_MAX_AGE": 1800})]


def write_outputs(results, summaries):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write("\n\n".join(report(s) for s in summaries) + "\n")
    for r in results:
        slug = "".join(ch if ch.isalnum() else "_" for ch in r.name).strip("_")
        with open(os.path.join(OUT_DIR, f"equity_{slug}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "equity", "hold_pnl", "fast_pnl"])
            for (t, e), (_, h), (_, fa) in zip(r.equity, r.book_pnl["hold"], r.book_pnl["fast"]):
                w.writerow([t, round(e, 2), round(h, 2), round(fa, 2)])
    with open(os.path.join(OUT_DIR, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "race", "market", "side", "entry_time", "exit_time", "tier", "shares", "cost", "pnl", "reason"])
        for r in results:
            for t in r.trades:
                w.writerow([r.name, t.race, t.market_id, t.side, int(t.entry_time), int(t.exit_time) if t.exit_time else "",
                            t.tier, round(t.shares, 1), round(t.cost, 2), round(t.pnl, 2), t.reason])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid", action="store_true", help="also run FAST_MIN_EDGE 3/4/5c with the volume rules on and off")
    parser.add_argument("--robust", action="store_true", help="also run the live settings with a 1c half spread and with only fresh SIG prices")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    races = load_races(config.RACES_FILE)
    sig_setup(races)
    polymarket_setup(races)
    history = load(races)
    print(f"History: {len(history.contracts)} SIG contracts, {len(history.kalshi)} Kalshi and {len(history.poly)} Polymarket series.")
    results, summaries = [], []
    for name, overrides in (grid() if args.grid else robustness() if args.robust else grid()[:1]):
        print(f"\nRunning {name}...", flush=True)
        result = run(races, history, name, overrides)
        results.append(result)
        summaries.append(summarize(result))
        print(report(summaries[-1]))
    write_outputs(results, summaries)
    if not args.no_plots:
        from backtest_plots import plot_all
        plot_all(results, summaries, OUT_DIR)
    print(f"\nWrote {OUT_DIR}")


if __name__ == "__main__":
    main()
