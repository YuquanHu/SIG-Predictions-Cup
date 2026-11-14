"""Stage 3 strategy: fair value, edge, Kelly, and picking the top trades. Pure logic, no network."""
import math
from dataclasses import dataclass, replace

import config
from races import quote_available

EPS = 1e-9  # tolerance for float comparisons against the cent-based limits


@dataclass(frozen=True)
class Fair:
    value: float | None  # None: no fair value, and `note` says why
    note: str
    sources: int = 0     # how many of Kalshi and Polymarket the value is built from
    raw: float | None = None  # the value before the race was scaled to add up to 1 (None: not scaled)
    uncertainty: float = 0.0  # how far the value may be off (election guard, guard.uncertainty); 0 outside the election regime


@dataclass
class Opportunity:
    """One way to buy a SIG contract: Yes at the ask or No at 1 - bid."""
    race: str
    market_id: str
    pays_if: str
    side: str          # "yes" or "no"
    price: float       # cost per share
    fair: float        # fair probability that this side wins
    size: float | None  # shares available at `price`
    wins_when: str     # outcome whose win makes this bet pay; equivalent bets share it
    sources: int = 2   # how many price sources the fair value rests on; one source gets a smaller race cap
    decision: str | None = None
    uncertainty: float = 0.0  # of the fair value (Fair.uncertainty)

    @property
    def edge(self):
        return self.fair - self.price

    @property
    def kelly(self):
        """Binary Kelly for a bet costing `price` with win probability `fair`: (q - c) / (1 - c)."""
        return max(self.edge, 0) / (1 - self.price)

    @property
    def expected_return(self):
        return self.edge / self.price

    @property
    def adj_edge(self):
        """The edge less ELECTION_K x the fair value's uncertainty: what is left once the fair is trusted no further than it can be."""
        return self.edge - config.ELECTION_K * self.uncertainty

    @property
    def z(self):
        return z_score(self.fair, self.price, config.ELECTION_K * self.uncertainty)

    @property
    def half_kelly(self):
        return self.kelly / 2


def z_score(fair, price, haircut=0.0):
    """Edge per unit of the bet's own risk: (fair - price - haircut) / sqrt(fair (1 - fair)). A share bought at `price` returns 1/price - 1 with
    probability `fair` and -1 otherwise, so this is the ROI over the SD of the return (sqrt(fair (1 - fair)) / price): the per-bet
    Sharpe, the same at any price. Unlike the ROI it does not favour a longshot whose small edge rides on a big chance of losing."""
    return (fair - price - haircut) / math.sqrt(max(fair * (1 - fair), 1e-6))


def fair_value(outcome, now):
    """Blend the healthy Kalshi and Polymarket midpoints 50/50.

    A source is dropped when it has no two-sided quote, is stale, or has a spread over MAX_SPREAD.
    The value is None when no source is usable or the two disagree by more than MAX_DISAGREE.
    """
    mids, dropped = {}, []
    for name, key in (("Kalshi", "kalshi"), ("Poly", "poly")):
        q = outcome[key]
        if not quote_available(q):
            dropped.append(f"{name} no quote")
        elif now - q["updated"] > config.STALE_SECONDS:
            dropped.append(f"{name} stale ({now - q['updated']:.0f}s)")
        elif q["ask"] - q["bid"] > config.MAX_SPREAD + EPS:
            dropped.append(f"{name} spread {q['ask'] - q['bid']:.3f}")
        else:
            mids[name] = (q["bid"] + q["ask"]) / 2

    used = ", ".join(f"{n} {m:.3f}" for n, m in mids.items())
    drop_note = f" (dropped: {'; '.join(dropped)})" if dropped else ""
    if not mids:
        return Fair(None, f"no usable source: {'; '.join(dropped)}")
    if len(mids) == 2 and abs(mids["Kalshi"] - mids["Poly"]) > config.MAX_DISAGREE + EPS:
        return Fair(None, f"sources disagree by {abs(mids['Kalshi'] - mids['Poly']):.3f}: {used}")
    return Fair(sum(mids.values()) / len(mids), f"{used}{drop_note}", len(mids))


def normalize(values):
    """{outcome: probability} scaled to add up to exactly 1, so Dem Yes and Rep No (and every other pair of equivalent
    bets) get the same fair value. Left as it is when an outcome has no value or the total is further than
    PROBABILITY_TOLERANCE from 1: then a candidate is probably missing and scaling would hide it."""
    total = sum(values.values()) if None not in values.values() else None
    if total is None or abs(total - 1) > config.PROBABILITY_TOLERANCE:
        return values
    return {code: v / total for code, v in values.items()}


def compute_fair_values(races, now):
    """{(race_id, outcome code): Fair} for every outcome, each race scaled to add up to 1 (see normalize)."""
    fair = {}
    for race_id, race in races.items():
        raw = {code: fair_value(o, now) for code, o in race["outcomes"].items()}
        scaled = normalize({code: f.value for code, f in raw.items()})
        for code, f in raw.items():
            fair[(race_id, code)] = f if scaled[code] == f.value else replace(f, value=scaled[code], raw=f.value)
    return fair


def wins_when(race, pays_if, side):
    """Dem Yes and Rep No win in the same world, so in a two-way race they share this value."""
    if side == "yes":
        return pays_if
    others = [code for code in race["outcomes"] if code != pays_if]
    return others[0] if len(others) == 1 else f"not {pays_if}"


def find_opportunities(races, fair_values):
    """Every Yes/No candidate on every SIG contract whose outcome has a fair value."""
    found = []
    for race_id, race in races.items():
        for contract in race["contracts"]:
            fv = fair_values[(race_id, contract["pays_if"])]
            fair = fv.value
            if fair is None:
                continue
            yes = contract["yes"]
            no_price = None if yes["bid"] is None else 1 - yes["bid"]
            for side, win_prob, price, size in (("yes", fair, yes["ask"], yes["ask_size"]),
                                                ("no", 1 - fair, no_price, yes["bid_size"])):
                if price is not None and 0 < price < 1:
                    found.append(Opportunity(race_id, contract["market_id"], contract["pays_if"], side,
                                             price, win_prob, size,
                                             wins_when(race, contract["pays_if"], side), fv.sources, uncertainty=fv.uncertainty))
    return found


def keep_best(candidates, group, score, reason):
    """Keep the highest-scoring candidate in each group; the rest get `reason(winner)` as their decision."""
    winners = {}
    for c in candidates:
        if group(c) not in winners or score(c) > score(winners[group(c)]):
            winners[group(c)] = c
    for c in candidates:
        if c is not winners[group(c)]:
            c.decision = reason(winners[group(c)])
    return [c for c in candidates if c.decision is None]


def keep_top(candidates, n):
    """Pick the trades and record why every candidate was or wasn't picked in `decision`.

    1. edge at least MIN_EDGE;  2. equivalent bets keep only the cheaper one;
    3. one bet per race, the highest Kelly;  4. the best n by Kelly overall.
    """
    eligible = []
    for c in candidates:
        if c.edge < config.MIN_EDGE - EPS:
            c.decision = f"edge {c.edge * 100:.1f}c is below {config.MIN_EDGE * 100:.0f}c"
        else:
            eligible.append(c)

    eligible = keep_best(eligible, lambda c: (c.race, c.wins_when), lambda c: -c.price,
                          lambda w: f"same bet as {w.side.upper()} {w.market_id}, which is cheaper")
    eligible = keep_best(eligible, lambda c: c.race, lambda c: c.kelly,
                          lambda w: f"{w.race} already has a better bet ({w.side.upper()} {w.market_id})")

    eligible.sort(key=lambda c: c.kelly, reverse=True)
    for rank, c in enumerate(eligible, start=1):
        c.decision = "TRADE" if rank <= n else f"outside the top {n} by Kelly"
    return eligible[:n]


def probability_gaps(races, fair_values, tolerance=config.PROBABILITY_TOLERANCE):
    """[(race_id, total)] for races whose outcome fair values (before scaling) do not add up to about 1: a sign that a
    candidate is missing from races.json (or that a price is off). Skips races with an outcome lacking a fair value."""
    gaps = []
    for race_id, race in races.items():
        fairs = [fair_values[(race_id, code)] for code in race["outcomes"]]
        values = [f.value if f.raw is None else f.raw for f in fairs]
        if None not in values and abs(sum(values) - 1) > tolerance:
            gaps.append((race_id, sum(values)))
    return gaps
