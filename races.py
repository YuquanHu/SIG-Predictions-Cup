"""Load races.json into the in-memory race table (shape described in plan.md).

A price that could not be fetched is None, never 0. Every fetch clears its quotes first, so a
failed fetch leaves None instead of a stale price.
"""
import json

EXTERNAL_SOURCES = ("kalshi", "poly")  # the sources fair value is built from


def empty_quote():
    return {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "updated": None}


def clear_quote(q):
    q.update(empty_quote())


def quote_available(q):
    """A usable quote has both sides; a midpoint needs a bid and an ask."""
    return q["bid"] is not None and q["ask"] is not None


_warnings = []


def warn(race_id, source, detail):
    """Print a per-race, per-source warning and keep it for drain_warnings()."""
    message = f"  WARNING [{race_id}] {source} unavailable: {detail}"
    print(message)
    _warnings.append(message)


def drain_warnings():
    drained, _warnings[:] = list(_warnings), []
    return drained


def _read_races_file(path):
    with open(path) as f:
        return json.load(f)


def disabled_races(path):
    """{race_id: reason} for races switched off in races.json with "enabled": false."""
    return {race_id: r.get("disabled_reason", "no reason given")
            for race_id, r in _read_races_file(path).items() if r.get("enabled", True) is False}


def load_races(path):
    """The enabled races. A race with "enabled": false is skipped and never fetched."""
    races = {}
    for race_id, r in _read_races_file(path).items():
        if r.get("enabled", True) is False:
            continue
        outcomes = {
            code: {
                "kalshi_id": o["kalshi_id"],
                "poly_id": str(o["poly_id"]).strip() if o.get("poly_id") else None,  # None: not matched on Polymarket yet
                "poly_yes_token": None,  # filled by polymarket.setup
                "kalshi": empty_quote(),
                "poly": empty_quote(),
            }
            for code, o in r["outcomes"].items()
        }
        contracts = [
            {
                "market_id": str(c["market_id"]),
                "pays_if": c["pays_if"],
                "sig_exchange_id": None,  # filled by sig.setup
                "title": None,
                "yes": empty_quote(),
            }
            for c in r["contracts"]
        ]
        races[race_id] = {
            "name": r["name"],
            "group": r["group"],
            "outcomes": outcomes,
            "contracts": contracts,
        }
    return races


def iter_outcomes(races):
    """Yield (race_id, code, outcome)."""
    for race_id, race in races.items():
        for code, o in race["outcomes"].items():
            yield race_id, code, o


def iter_contracts(races):
    """Yield (race_id, contract)."""
    for race_id, race in races.items():
        for c in race["contracts"]:
            yield race_id, c


def available_sources(outcome):
    """Which of Kalshi / Polymarket currently have a usable quote for this outcome."""
    return [s for s in EXTERNAL_SOURCES if quote_available(outcome[s])]


def race_has_data(race):
    """False when no outcome has any usable Kalshi/Polymarket quote; fair value must skip such a race."""
    return any(available_sources(o) for o in race["outcomes"].values())
