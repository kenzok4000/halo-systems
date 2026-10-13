"""Environment settings: the HALO_ names, and the SWARMLAB_ names used before the rename."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from halo_systems.ai.budget import Budget
from halo_systems.settings import env

CLEAN = {k: v for k, v in os.environ.items() if not k.startswith(("HALO_", "SWARMLAB_"))}


class SettingsTest(unittest.TestCase):
    def test_new_name_wins_over_old_name(self):
        with mock.patch.dict(os.environ, {**CLEAN, "HALO_SUPERVISOR_MODEL": "new", "SWARMLAB_SUPERVISOR_MODEL": "old"},
                             clear=True):
            self.assertEqual(env("SUPERVISOR_MODEL"), "new")

    def test_old_name_still_read(self):
        with mock.patch.dict(os.environ, {**CLEAN, "SWARMLAB_SUPERVISOR_MODEL": "old"}, clear=True):
            self.assertEqual(env("SUPERVISOR_MODEL"), "old")

    def test_empty_counts_as_unset(self):
        with mock.patch.dict(os.environ, {**CLEAN, "HALO_BUDGET_USD": "", "SWARMLAB_BUDGET_USD": "5"}, clear=True):
            self.assertEqual(env("BUDGET_USD"), "5")
        with mock.patch.dict(os.environ, CLEAN, clear=True):
            self.assertIsNone(env("BUDGET_USD"))
            self.assertEqual(env("BUDGET_USD", "18"), "18")

    def test_spend_guard_follows_the_old_ledger_variable(self):
        # An environment set up before the rename must keep counting against its existing ledger.
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "ledger.jsonl"
            with mock.patch.dict(os.environ, {**CLEAN, "SWARMLAB_LEDGER": str(ledger), "SWARMLAB_BUDGET_USD": "7"},
                                 clear=True):
                budget = Budget()
            self.assertEqual(budget.path, ledger)
            self.assertEqual(budget.cap_usd, 7.0)


if __name__ == "__main__":
    unittest.main()
