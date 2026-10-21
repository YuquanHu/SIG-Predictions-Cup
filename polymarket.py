"""Polymarket public market data (no auth): Gamma for ids, CLOB for books."""
import json
import time

import requests

import config
from races import clear_quote, iter_outcomes, quote_available, warn

NAME = "Polymarket"


def setup(races):
    """Resolve each Gamma market id to its Yes CLOB token id (one request)."""
    outcomes = {}  # poly_id -> (race_id, code, outcome)
    for race_id, code, o in iter_outcomes(races):
        if o["poly_id"]:  # outcomes with no Polymarket match are skipped without a warning
            outcomes[o["poly_id"]] = (race_id, code, o)
    if not outcomes:
        return
    try:
        resp = requests.get(
            f"{config.POLY_GAMMA_BASE}/markets",
            params=[("id", pid) for pid in outcomes],
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        for race_id, code, o in outcomes.values():
            warn(race_id, NAME, f"{code} ({o['poly_id']}): market lookup failed: {e}")
        return

    for m in markets:
        entry = outcomes.get(str(m["id"]))
        if entry is None:
            continue
        race_id, code, o = entry
        try:
            labels = json.loads(m["outcomes"])
            o["poly_yes_token"] = json.loads(m["clobTokenIds"])[labels.index("Yes")]
        except (KeyError, ValueError, IndexError) as e:
            warn(race_id, NAME, f"{code} ({m['id']}): no Yes token in market data: {e}")
    for race_id, code, o in outcomes.values():
        if o["poly_yes_token"] is None:
            warn(race_id, NAME, f"{code} ({o['poly_id']}): market not found")


def _post_books(tokens):
    resp = requests.post(
        f"{config.POLY_CLOB_BASE}/books",
        json=[{"token_id": t} for t in tokens],
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_books(tokens):
    """Books for all tokens in one request. If the batch fails, retry one token at a time so a
    single bad market only loses itself. Returns (books, errors by token)."""
    try:
        return _post_books(tokens), {}
    except Exception:
        books, errors = [], {}
        for t in tokens:
            try:
                books += _post_books([t])
            except Exception as e:
                errors[t] = e
        return books, errors


def fetch_prices(races):
    """One batched CLOB books request for every Yes token."""
    for _, _, o in iter_outcomes(races):
        clear_quote(o["poly"])
    if any(o["poly_yes_token"] is None for _, _, o in iter_outcomes(races)):
        setup(races)  # retry the lookup; it warns by itself for markets still missing
    outcomes = {o["poly_yes_token"]: (race_id, code, o)
                for race_id, code, o in iter_outcomes(races) if o["poly_yes_token"]}
    if not outcomes:
        return

    books, errors = _fetch_books(list(outcomes))
    for token, e in errors.items():
        race_id, code, o = outcomes[token]
        warn(race_id, NAME, f"{code} ({o['poly_id']}): book request failed: {e}")

    now = time.time()
    seen = set()
    for book in books:
        entry = outcomes.get(book.get("asset_id"))
        if entry is None:
            continue
        seen.add(book["asset_id"])
        race_id, code, o = entry
        # CLOB levels are not ordered best-first, so pick the extremes explicitly.
        best_bid = max(book.get("bids", []), key=lambda lvl: float(lvl["price"]), default=None)
        best_ask = min(book.get("asks", []), key=lambda lvl: float(lvl["price"]), default=None)
        o["poly"].update(
            bid=float(best_bid["price"]) if best_bid else None,
            bid_size=float(best_bid["size"]) if best_bid else None,
            ask=float(best_ask["price"]) if best_ask else None,
            ask_size=float(best_ask["size"]) if best_ask else None,
            updated=now,
        )
        if not quote_available(o["poly"]):
            warn(race_id, NAME, f"{code} ({o['poly_id']}): no two-sided quote")
    for token, (race_id, code, o) in outcomes.items():
        if token not in seen and token not in errors:
            warn(race_id, NAME, f"{code} ({o['poly_id']}): no book returned")
