"""Stage 3 strategy: fair value, edge, Kelly, and picking the top trades. Pure logic, no network."""
import config
from races import iter_contracts, quote_available

EPS = 1e-9  # tolerance for float comparisons against the cent-based limits


def fair_value(outcome, now):
    """Blend the healthy Kalshi and Polymarket midpoints 50/50.

    A source is dropped when it has no two-sided quote, is stale, or has a spread over MAX_SPREAD.
    Returns (value, note). value is None when no source is usable or the two disagree by more than
    MAX_DISAGREE; the note says why.
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
        return None, f"no usable source: {'; '.join(dropped)}"
    if len(mids) == 2 and abs(mids["Kalshi"] - mids["Poly"]) > config.MAX_DISAGREE + EPS:
        return None, f"sources disagree by {abs(mids['Kalshi'] - mids['Poly']):.3f}: {used}"
    return sum(mids.values()) / len(mids), f"{used}{drop_note}"


def _bet_key(race, contract, side):
    """Bets that win in the same world share a key: Dem Yes and Rep No are one bet in a two-way race."""
    outcomes = list(race["outcomes"])
    wins = side == "yes"
    if not wins and len(outcomes) == 2:
        pays_if = next(o for o in outcomes if o != contract["pays_if"])
        return pays_if
    return contract["pays_if"] if wins else f"not {contract['pays_if']}"


def find_opportunities(races, now):
    """Every (SIG contract, Yes/No) candidate with its fair value, edge and Kelly.

    `edge` = fair win probability minus the price paid. Kelly for a binary contract bought at
    price c with win probability q is (q - c) / (1 - c); a Yes costs the ask, a No costs 1 - bid.
    Each candidate gets a "decision" later; contracts with no fair value are returned as skips.
    """
    candidates, skipped = [], []
    for race_id, race in races.items():
        fair = {code: fair_value(o, now) for code, o in race["outcomes"].items()}
        for contract in race["contracts"]:
            value, note = fair[contract["pays_if"]]
            if value is None:
                skipped.append((race_id, contract["market_id"], f"no fair value for {contract['pays_if']}: {note}"))
                continue
            yes = contract["yes"]
            sides = {  # side -> (win probability, price paid, shares available at that price)
                "yes": (value, yes["ask"], yes["ask_size"]),
                "no": (1 - value, None if yes["bid"] is None else 1 - yes["bid"], yes["bid_size"]),
            }
            for side, (win_prob, price, size) in sides.items():
                if price is None or not 0 < price < 1:
                    continue  # nothing to buy on this side
                edge = win_prob - price
                kelly = max(edge, 0) / (1 - price)
                candidates.append({
                    "race": race_id, "market_id": contract["market_id"], "pays_if": contract["pays_if"],
                    "side": side, "price": price, "fair": win_prob, "edge": edge,
                    "kelly": kelly, "half_kelly": kelly / 2, "size": size,
                    "bet": _bet_key(race, contract, side), "fair_note": note, "decision": None,
                })
    return candidates, skipped


def keep_top(candidates, n=config.TOP_N):
    """Pick the trades and record why every candidate was or wasn't picked in c["decision"].

    1. edge must be at least MIN_EDGE;  2. equivalent bets keep only the cheaper one;
    3. one bet per race, the highest Kelly;  4. the best n by Kelly overall.
    """
    eligible = []
    for c in candidates:
        if c["edge"] < config.MIN_EDGE - EPS:
            c["decision"] = f"edge {c['edge'] * 100:.1f}c is below {config.MIN_EDGE * 100:.0f}c"
        else:
            eligible.append(c)

    cheapest = {}
    for c in eligible:
        key = (c["race"], c["bet"])
        if key not in cheapest or c["price"] < cheapest[key]["price"]:
            cheapest[key] = c
    for c in eligible:
        best = cheapest[(c["race"], c["bet"])]
        if c is not best:
            c["decision"] = f"same bet as {best['side'].upper()} {best['market_id']}, which is cheaper"
    eligible = [c for c in eligible if c["decision"] is None]

    best_in_race = {}
    for c in eligible:
        if c["race"] not in best_in_race or c["kelly"] > best_in_race[c["race"]]["kelly"]:
            best_in_race[c["race"]] = c
    for c in eligible:
        best = best_in_race[c["race"]]
        if c is not best:
            c["decision"] = f"{c['race']} already has a better bet ({best['side'].upper()} {best['market_id']})"
    eligible = sorted(best_in_race.values(), key=lambda c: c["kelly"], reverse=True)

    for rank, c in enumerate(eligible, start=1):
        c["decision"] = "TRADE" if rank <= n else f"outside the top {n} by Kelly"
    return [c for c in eligible if c["decision"] == "TRADE"]
