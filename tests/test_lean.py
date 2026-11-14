"""Offline tests for the end-of-loop count of Dem and Rep sides by office. Run: python3 -m unittest discover -s tests -t ."""
import contextlib
import io
import re
import unittest

from lean import print_lean
from orders import Order
from portfolio import Holding
from strategy import wins_when


def race(group, codes=("D", "R")):
    return {"group": group, "outcomes": {c: {} for c in codes},
            "contracts": [{"market_id": f"{group}-{c}", "pays_if": c} for c in codes]}


RACES = {"H1": race("House"), "H2": race("House"), "S1": race("Senate"), "G1": race("Governor"), "C1": race("Control"),
         "S3": race("Senate", ("D", "R", "I"))}


def held(race_id, code, side="yes"):
    """A holding on the contract that pays if `code` wins."""
    return Holding(race_id, f"{RACES[race_id]['group']}-{code}", side, wins_when(RACES[race_id], code, side), 10, 5.0, 0.5, 100, 0.6)


def buy(race_id, code, side="yes", action="buy"):
    return Order(action, side, race_id, f"{RACES[race_id]['group']}-{code}", 10, 0.5, 0.6, "t")


def tables(holdings=(), orders=()):
    """(bets table, SUSQies table) as {row: cells}; the header row is under the table's label ("bets", "SUSQies")."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        print_lean(RACES, list(holdings), list(orders))
    lines = [re.split(r"\s{2,}", line.strip()) for line in out.getvalue().splitlines() if line.strip()][1:]
    half = len(lines[0]) // 2   # the left table starts with its label, then the columns; the right one the same
    return tuple({row[start]: row[start + 1:start + half] for row in lines} for start in (0, half))


def lean(holdings=(), orders=()):
    counts, _ = tables(holdings, orders)
    header = counts.pop("bets")
    return counts, header


class Counts(unittest.TestCase):
    def test_held_bets_are_counted_by_office_and_party(self):
        rows, header = lean([held("H1", "D"), held("H2", "D"), held("S1", "R"), held("G1", "R")])
        self.assertEqual(header, ["Dem", "Rep"])
        self.assertEqual((rows["House"], rows["Senate"], rows["Governor"], rows["total"]), (["2", "0"], ["0", "1"], ["0", "1"], ["2", "2"]))

    def test_the_offices_are_listed_even_with_no_bet(self):
        rows, _ = lean()
        self.assertEqual(list(rows), ["House", "Senate", "Governor", "total"])
        self.assertEqual(rows["total"], ["0", "0"])

    def test_a_no_on_rep_is_a_dem_side_and_counts_once_with_the_yes_on_dem(self):
        rows, _ = lean([held("H1", "R", "no"), held("H1", "D")])
        self.assertEqual(rows["House"], ["1", "0"])

    def test_a_no_in_a_race_of_three_is_other_and_a_group_beyond_the_three_offices_follows(self):
        rows, header = lean([held("S3", "D", "no"), held("C1", "D")])
        self.assertEqual(header, ["Dem", "Rep", "other"])
        self.assertEqual((rows["Senate"], rows["Control"], list(rows)[-2:]), (["0", "0", "1"], ["1", "0", "0"], ["Control", "total"]))


class Values(unittest.TestCase):
    def test_the_second_table_has_the_same_rows_and_columns_in_susqies_next_to_the_first(self):
        [h1, h2] = [held("H1", "D"), held("S1", "R")]
        bets, values = tables([h1, h2])
        self.assertEqual((bets.pop("bets"), values.pop("SUSQies"), list(values)), (["Dem", "Rep"], ["Dem", "Rep"], list(bets)))
        self.assertEqual((values["House"], values["Senate"], values["total"]), ([f"{h1.value:,.0f}", "0"], ["0", f"{h2.value:,.0f}"],
                                                                                 [f"{h1.value:,.0f}", f"{h2.value:,.0f}"]))

    def test_a_top_up_adds_to_the_value_but_not_to_the_bets(self):
        h = held("H1", "D")
        bets, values = tables([h], [buy("H1", "D")])
        self.assertEqual((bets["House"][0], values["House"][0]), ("1", f"{h.value:,.0f} (+{buy('H1', 'D').value:,.0f})"))

    def test_a_buy_adds_its_own_value_in_the_bet_s_party_and_office_and_a_sell_adds_nothing(self):
        bets, values = tables(orders=[buy("H1", "R", "no"), buy("G1", "R", action="sell")])
        self.assertEqual((values["House"], values["Governor"], values["total"]), ([f"0 (+{buy('H1', 'R', 'no').value:,.0f})", "0"], ["0", "0"],
                                                                                   [f"0 (+{buy('H1', 'R', 'no').value:,.0f})", "0"]))


class NewBets(unittest.TestCase):
    def test_a_buy_in_a_bet_not_held_shows_as_plus_one_and_a_top_up_or_a_sell_does_not(self):
        rows, _ = lean([held("H1", "D")], [buy("H2", "R"), buy("H1", "D"), buy("S1", "D", action="sell")])
        self.assertEqual((rows["House"], rows["Senate"], rows["total"]), (["1", "0 (+1)"], ["0", "0"], ["1", "0 (+1)"]))

    def test_a_buy_of_the_same_bet_on_the_other_contract_is_not_new(self):
        rows, _ = lean([held("H1", "D")], [buy("H1", "R", "no")])
        self.assertEqual(rows["House"], ["1", "0"])

    def test_two_buys_of_one_bet_count_once(self):
        rows, _ = lean(orders=[buy("H1", "D"), buy("H1", "R", "no")])
        self.assertEqual(rows["House"], ["0 (+1)", "0"])


if __name__ == "__main__":
    unittest.main()
