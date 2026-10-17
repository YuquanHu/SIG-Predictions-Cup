"""What we hold, valued at what selling would return."""
from dataclasses import dataclass

from strategy import EPS, wins_when


@dataclass
class Holding:
    market_id: str
    side: str
    shares: float
    value: float              # shares x sell price (SIG's own price when there is no bid to sell into)
    race: str | None          # None: the market is not in races.json, so it cannot be managed
    wins_when: str | None
    sell_price: float | None  # what selling returns per share right now
    sell_depth: float | None  # shares the book will take at that price
    fair: float | None        # fair probability that the held side wins

    @property
    def exp_return(self):
        """Remaining expected return if kept: (fair - sell price) / sell price."""
        if self.fair is None or not self.sell_price:
            return None
        return (self.fair - self.sell_price) / self.sell_price

    @property
    def negative_edge(self):
        return self.exp_return is not None and self.fair < self.sell_price - EPS


def build_holdings(positions, races, fair_values):
    contracts = {c["market_id"]: (race_id, c) for race_id, race in races.items() for c in race["contracts"]}
    holdings = []
    for p in positions:
        if p["market_id"] not in contracts:
            holdings.append(Holding(p["market_id"], p["side"], p["shares"],
                                    p["shares"] * (p["current_price"] or 0), None, None, None, None, None))
            continue
        race_id, c = contracts[p["market_id"]]
        yes = c["yes"]
        if p["side"] == "yes":
            sell_price, depth = yes["bid"], yes["bid_size"]
        else:  # selling a No is buying the Yes back at the ask
            sell_price, depth = (None if yes["ask"] is None else 1 - yes["ask"]), yes["ask_size"]
        fair_yes = fair_values[(race_id, c["pays_if"])].value
        fair = None if fair_yes is None else (fair_yes if p["side"] == "yes" else 1 - fair_yes)
        mark = sell_price if sell_price is not None else (p["current_price"] or 0)
        holdings.append(Holding(p["market_id"], p["side"], p["shares"], p["shares"] * mark, race_id,
                                wins_when(races[race_id], c["pays_if"], p["side"]), sell_price, depth, fair))
    return holdings
