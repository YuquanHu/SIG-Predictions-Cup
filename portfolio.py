"""What we hold, valued at what selling would return."""
from dataclasses import dataclass

import config
from strategy import EPS, wins_when


@dataclass
class Holding:
    race: str
    market_id: str
    side: str
    wins_when: str            # outcome whose win makes this bet pay; shared by equivalent bets
    shares: float
    value: float              # shares x sell price (SIG's own price when there is no bid to sell into)
    sell_price: float | None  # what selling returns per share right now
    sell_depth: float | None  # shares the book will take at that price
    fair: float | None        # fair probability that the held side wins
    sell_ladder: list | None = None  # [(price, shares)] the held side's bids, best first: what an exit sells into
    rest_ladder: list | None = None  # [(price, shares)] the held side's asks, best first: where a resting sell would queue

    @property
    def exp_return(self):
        """Remaining expected return if kept: (fair - sell price) / sell price."""
        if self.fair is None or not self.sell_price:
            return None
        return (self.fair - self.sell_price) / self.sell_price

    @property
    def negative_edge(self):
        return self.exp_return is not None and self.fair < self.sell_price - EPS


@dataclass
class Portfolio:
    cash: float
    holdings: list[Holding]
    outside_value: float  # positions in markets that are not in races.json: counted, never traded

    @property
    def invested(self):
        return sum(h.value for h in self.holdings) + self.outside_value

    @property
    def bankroll(self):
        return self.cash + self.invested

    @property
    def investable(self):
        """Cash still free to invest: room under the investment cap, limited by actual cash."""
        return max(0.0, min(config.INVEST_CAP * self.bankroll - self.invested, self.cash))

    def held_in_bet(self, race, wins_when):
        return sum(h.value for h in self.holdings if h.race == race and h.wins_when == wins_when)

    def held_in_race(self, race):
        return sum(h.value for h in self.holdings if h.race == race)


def build_portfolio(cash, positions, races, fair_values):
    contracts = {c["market_id"]: (race_id, c) for race_id, race in races.items() for c in race["contracts"]}
    holdings, outside_value = [], 0.0
    for p in positions:
        if p["market_id"] not in contracts:
            outside_value += p["shares"] * (p["current_price"] or 0)
            continue
        race_id, c = contracts[p["market_id"]]
        yes = c["yes"]
        bids, asks = yes["bid_levels"], yes["ask_levels"]
        if p["side"] == "yes":
            sell_price, depth, sell_ladder, rest_ladder = yes["bid"], yes["bid_size"], bids, asks
        else:  # selling a No is buying the Yes back at the ask; the No book is the Yes book mirrored
            sell_price, depth = (None if yes["ask"] is None else 1 - yes["ask"]), yes["ask_size"]
            sell_ladder = None if asks is None else [(round(1 - price, 3), qty) for price, qty in asks]
            rest_ladder = None if bids is None else [(round(1 - price, 3), qty) for price, qty in bids]
        fair_yes = fair_values[(race_id, c["pays_if"])].value
        fair = None if fair_yes is None else (fair_yes if p["side"] == "yes" else 1 - fair_yes)
        mark = sell_price if sell_price is not None else (p["current_price"] or 0)
        holdings.append(Holding(race_id, p["market_id"], p["side"], wins_when(races[race_id], c["pays_if"], p["side"]),
                                p["shares"], p["shares"] * mark, sell_price, depth, fair, sell_ladder, rest_ladder))
    return Portfolio(cash, holdings, outside_value)
