"""Offline tests for the lean logs: files created up front, new warnings only."""
import csv
import os
import tempfile
import unittest
from unittest import mock

import config
import logbook
from safety import RunState, new_warnings


class LogFiles(unittest.TestCase):
    def test_init_creates_every_file_with_its_header(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "LOG_DIR", os.path.join(d, "logs")):
            logbook.init()
            for kind, columns in logbook.COLUMNS.items():
                with open(os.path.join(d, "logs", f"{kind}.csv"), newline="") as f:
                    self.assertEqual(next(csv.reader(f)), columns)

    def test_log_appends_rows_without_repeating_the_header(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "LOG_DIR", d):
            logbook.init()
            logbook.log("errors", 1, [{"where": "fetch", "error": "a"}])
            logbook.log("errors", 2, [{"where": "fetch", "error": "b"}])
            with open(os.path.join(d, "errors.csv"), newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([r["error"] for r in rows], ["a", "b"])

    def test_a_changed_header_renames_the_old_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "LOG_DIR", d):
            with open(os.path.join(d, "errors.csv"), "w") as f:
                f.write("old,columns\n1,2\n")
            logbook.init()
            self.assertTrue(any(n.startswith("errors.old-") for n in os.listdir(d)))


class NewWarnings(unittest.TestCase):
    def test_an_outage_is_logged_once_and_again_only_after_it_clears(self):
        state = RunState()
        self.assertEqual(new_warnings(state, ["a", "b"]), ["a", "b"])
        self.assertEqual(new_warnings(state, ["a", "b"]), [])       # same outage next loop: nothing new
        self.assertEqual(new_warnings(state, ["a", "c"]), ["c"])    # only the new one
        self.assertEqual(new_warnings(state, []), [])               # cleared
        self.assertEqual(new_warnings(state, ["a"]), ["a"])         # came back: logged again


if __name__ == "__main__":
    unittest.main()
