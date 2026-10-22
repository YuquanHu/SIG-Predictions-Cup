"""Offline tests for Polymarket chunking: no network."""
import json
import unittest
from unittest import mock

import polymarket
from races import drain_warnings, empty_quote


def races_with(n):
    outcomes = {f"O{i}": {"kalshi_id": f"K{i}", "poly_id": str(1000 + i), "poly_yes_token": None,
                          "kalshi": empty_quote(), "poly": empty_quote()} for i in range(n)}
    outcomes["NOPOLY"] = {"kalshi_id": "KX", "poly_id": None, "poly_yes_token": None,
                          "kalshi": empty_quote(), "poly": empty_quote()}
    return {"R": {"name": "R", "group": "Senate", "outcomes": outcomes, "contracts": []}}


def gamma_reply(params):
    ids = [v for k, v in params if k == "id"]
    resp = mock.Mock()
    resp.json.return_value = [{"id": i, "outcomes": json.dumps(["Yes", "No"]),
                               "clobTokenIds": json.dumps([f"tok{i}", f"no{i}"])} for i in ids]
    return resp


class Chunking(unittest.TestCase):
    def setUp(self):
        drain_warnings()

    def test_setup_sends_ids_in_chunks(self):
        races = races_with(120)
        with mock.patch("polymarket.requests.get", side_effect=lambda url, params, timeout: gamma_reply(params)) as get:
            polymarket.setup(races)
        sizes = [len([p for p in c.kwargs["params"] if p[0] == "id"]) for c in get.call_args_list]
        self.assertEqual(sizes, [50, 50, 20])
        tokens = [o["poly_yes_token"] for o in races["R"]["outcomes"].values() if o["poly_id"]]
        self.assertTrue(all(tokens))
        self.assertEqual(drain_warnings(), [])

    def test_a_failing_chunk_only_loses_its_own_markets(self):
        races = races_with(60)
        def reply(url, params, timeout):
            if ("id", "1000") in params:
                raise polymarket.requests.ConnectionError("down")
            return gamma_reply(params)
        with mock.patch("polymarket.requests.get", side_effect=reply):
            polymarket.setup(races)
        resolved = sum(1 for o in races["R"]["outcomes"].values() if o["poly_yes_token"])
        self.assertEqual(resolved, 10)
        self.assertEqual(len(drain_warnings()), 50)  # one per lost market, no duplicate "not found"

    def test_fetch_does_not_rerun_setup_for_outcomes_without_a_poly_id(self):
        races = races_with(2)
        for o in races["R"]["outcomes"].values():
            if o["poly_id"]:
                o["poly_yes_token"] = f"tok{o['poly_id']}"
        with mock.patch("polymarket.setup") as setup, mock.patch("polymarket._post_books", return_value=[]):
            polymarket.fetch_prices(races)
        setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
