"""The initial-attack model in tools/wildfire_initial_attack.py: the perimeter race, filled perimeters, merged fires."""

import importlib.util
import math
import unittest
from pathlib import Path

import numpy as np

_PATH = Path(__file__).resolve().parents[1] / "tools" / "wildfire_initial_attack.py"
_SPEC = importlib.util.spec_from_file_location("wildfire_initial_attack", _PATH)
ia = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ia)


class RaceTest(unittest.TestCase):
    def test_line_catches_a_slow_fire_and_not_a_fast_one(self):
        steps = np.arange(0.0, 7200.0, 30.0)
        slow = 100.0 + 0.5 * steps            # perimeter grows 0.5 m/s
        fast = 100.0 + 2.0 * steps
        area = 50.0 + steps                   # m2
        t, a, esc = ia.contain(steps, slow, area, 600.0, 1.0, 4.05e4)
        self.assertIsNotNone(t)
        self.assertGreaterEqual(1.0 * (t - 600.0), 100.0 + 0.5 * t)
        self.assertLess(1.0 * (t - 30.0 - 600.0), 100.0 + 0.5 * (t - 30.0))   # the first such step
        self.assertFalse(esc)
        t, a, esc = ia.contain(steps, fast, area, 600.0, 1.0, 4.05e4)
        self.assertIsNone(t)
        self.assertTrue(esc)
        self.assertEqual(a, area[-1])
        t, a, esc = ia.contain(steps, slow, area * 100.0, 600.0, 1.0, 4.05e4)
        self.assertTrue(esc)                                                   # held, but above 4.05 ha

    def test_islands_are_filled_and_open_notches_are_not(self):
        b = np.zeros((12, 12), dtype=bool)
        b[2:9, 2:9] = True
        b[4:6, 4:6] = False
        f = ia.filled(b)
        self.assertTrue(f[4:6, 4:6].all())
        self.assertEqual(int(f.sum()), 49)
        c = np.zeros((10, 10), dtype=bool)
        c[3:7, 3:7] = True
        c[3:5, 5] = False                                                      # a notch open to the outside
        self.assertTrue(np.array_equal(ia.filled(c), c))


class MergeTest(unittest.TestCase):
    def _rows(self, contained):
        return [{"today": {"merged_into": None, "contained_t": c, "escaped": c is None,
                           "smouldering_on_arrival": False},
                 "drones": {"merged_into": None, "contained_t": c, "escaped": c is None,
                            "smouldering_on_arrival": False}} for c in contained]

    def test_an_overrun_fire_joins_the_fire_that_overran_it(self):
        fires = [{"t_active_s": 0.0, "overrun": []}, {"t_active_s": 3000.0, "overrun": [[0, 1200.0]]}]
        rows = self._rows([None, None])
        ia.merge(rows, fires)
        self.assertEqual(rows[1]["today"]["merged_into"], 0)
        self.assertFalse(rows[1]["today"]["escaped"])                          # not an escape of its own
        self.assertTrue(rows[0]["today"]["escaped"])
        # The other fire was held before its front got there: the second fire stays its own.
        rows = self._rows([900.0, None])
        ia.merge(rows, fires)
        self.assertIsNone(rows[1]["today"]["merged_into"])
        self.assertTrue(rows[1]["today"]["escaped"])
        # Its own crew held it first.
        rows = self._rows([None, 1000.0])
        ia.merge(rows, fires)
        self.assertIsNone(rows[1]["today"]["merged_into"])


class SummaryTest(unittest.TestCase):
    def test_contained_sizes_are_only_over_contained_fires(self):
        def row(seed, esc_t, esc_d, ct, cd, at, ad, smo_d):
            arm = lambda esc, c, a, smo: {"escaped": esc, "contained_t": c, "area_m2": a, "merged_into": None,
                                          "smouldering_on_arrival": smo, "area_on_arrival_m2": 10.0}
            return {"seed": seed, "never_spreads": False, "dispatch_t": 5.0, "t_today": 60.0,
                    "today": arm(esc_t, ct, at, False), "drones": arm(esc_d, cd, ad, smo_d)}
        rows = [row(1, True, False, None, 100.0, 5e5, 20.0, True), row(1, True, True, None, None, 7e5, 7e5, False),
                row(2, False, False, 200.0, 50.0, 300.0, 15.0, True)]
        s = ia.summarise(rows, boots=200)
        self.assertEqual((s["escaped_today"], s["escaped_with_drones"]), (2, 1))
        self.assertEqual((s["contained_today"], s["contained_with_drones"]), (1, 2))
        self.assertAlmostEqual(s["contained_ha_today_median"], 0.03)
        self.assertEqual((s["crew_on_smoulder_today"], s["crew_on_smoulder_with_drones"]), (0, 2))
        lo, hi = s["crew_on_smoulder_pct_points_ci95"]
        self.assertTrue(0 <= lo <= hi <= 100)
        self.assertTrue(math.isfinite(s["prevented_pct_points_ci95"][0]))


if __name__ == "__main__":
    unittest.main()
