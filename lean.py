"""Which side of each race the bot is on, by office: printed at the very end of a v2 loop, as bets held and as SUSQies invested."""
from collections import Counter

from display import table_lines
from races import iter_contracts
from strategy import wins_when

OFFICES = ("House", "Senate", "Governor")  # always listed, in this order; any other group in races.json (Control) follows when it has a bet
PARTIES = {"D": "Dem", "R": "Rep"}         # a bet's side is the outcome it wins on; an independent or a "not D" (a No in a race of 3+) is "other"


def tally(races, weighted):
    """{(office, party): total} of ((race id, the outcome the bet wins on), weight) pairs."""
    total = Counter()
    for (race, wins), weight in weighted:
        total[races[race]["group"], PARTIES.get(wins, "other")] += weight
    return total


def grid(label, now, new, offices, parties, fmt):
    """The table lines: a row per office and a total, each cell `held (+new)`."""
    def cell(held, add):
        return f"{fmt(held)} (+{fmt(add)})" if add else fmt(held)

    rows = [(office, *(cell(now[office, p], new[office, p]) for p in parties)) for office in offices]
    rows.append(("total", *(cell(sum(now[o, p] for o in offices), sum(new[o, p] for o in offices)) for p in parties)))
    return table_lines((label, *parties), rows)


def print_lean(races, holdings, orders):
    """Two tables side by side, bets held by office and party and what they are worth (the same value as `invested` in the header), each cell
    `held (+n)`: n counts the new bets this loop's buys (resting bids too) start, the value adds every buy, top-ups too."""
    pays_if = {c["market_id"]: c["pays_if"] for _, c in iter_contracts(races)}
    buys = [((o.race, wins_when(races[o.race], pays_if[o.market_id], o.side)), o.value) for o in orders if o.action == "buy"]
    held = {(h.race, h.wins_when) for h in holdings}
    new_bets = {bet for bet, _ in buys} - held
    value, added = tally(races, [((h.race, h.wins_when), h.value) for h in holdings]), tally(races, buys)
    seen = value + added
    offices = (*OFFICES, *sorted({office for office, _ in seen} - set(OFFICES)))
    parties = (*PARTIES.values(), *(("other",) if any(party == "other" for _, party in seen) else ()))
    counts = grid("bets", tally(races, [(bet, 1) for bet in held]), tally(races, [(bet, 1) for bet in new_bets]), offices, parties, str)
    values = grid("SUSQies", value, added, offices, parties, lambda x: f"{x:,.0f}")
    print("\nSides taken (held, +n = new bets / SUSQies from this loop's buys)")
    width = max(map(len, counts))
    for left, right in zip(counts, values):
        print(f"{left.ljust(width)}    {right.strip()}")
