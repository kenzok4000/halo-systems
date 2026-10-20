"""Squads per area over the wildfire valley, the operator's orders to move drones, and the opt-in station merge."""

import unittest

import numpy as np

from halo_systems.control.directive import Directive
from halo_systems.control.squads import (Area, allocate, breakpoints, build_squads, canonical, cap_for, circuit,
                                         lanes_for, parse_counts, parse_script)
from halo_systems.eval.runner import make_controller, make_supervisor, run_one
from halo_systems.eval.scenarios import (SCOUT_ALTITUDE_M, SCOUT_SPACING_M, SCOUT_SPEED_MPS, make, valley_areas,
                                         wildfire_valley)
from halo_systems.safety.validator import SafetyValidator
from halo_systems.sim.body import DroneBody
from halo_systems.sim.engine import Simulation

DIRECTIVE = Directive("line", SCOUT_SPACING_M, SCOUT_SPEED_MPS, SCOUT_ALTITUDE_M, 1.0, source="mission")


def swath() -> float:
    return 2.0 * float(wildfire_valley(0, n=1).sensor.footprint_radius(SCOUT_ALTITUDE_M))


class LayoutTest(unittest.TestCase):
    def test_circuits_close_inside_their_area(self):
        sw = swath()
        for a in valley_areas():
            for k in breakpoints(a.height, SCOUT_SPACING_M, sw, lo=3):
                pts, lanes, lw = circuit(a.rect, k, SCOUT_SPACING_M, sw)
                self.assertEqual(lanes % 2, 0)
                self.assertEqual(lanes, lanes_for(k, a.height, SCOUT_SPACING_M, sw))
                self.assertEqual(len(pts), 2 * lanes)
                ys = [y for _, y in pts]
                self.assertGreaterEqual(min(ys) - lw / 2, a.rect[1] - 1e-6)
                self.assertLessEqual(max(ys) + lw / 2, a.rect[3] + 1e-6)
                self.assertLessEqual(k, cap_for(a.height, SCOUT_SPACING_M, sw))
            # Rotated to start on another lane, flown the other way: the same lanes.
            pts2, _, _ = circuit(a.rect, 4, SCOUT_SPACING_M, sw, start_lane=1, eastward=False)
            self.assertEqual(sorted({y for _, y in pts2}), sorted({y for _, y in circuit(a.rect, 4, SCOUT_SPACING_M, sw)[0]}))
            self.assertGreater(pts2[0][0], pts2[1][0])              # flown westward

    def test_default_split_fills_the_floor_first_and_keeps_a_reserve(self):
        sw = swath()
        areas = valley_areas()
        seen = []
        for n in (1, 2, 4, 8, 16, 32, 64):
            got = allocate(n, areas, DIRECTIVE, sw, 720.0)
            self.assertEqual(sum(got.values()), n)
            self.assertGreaterEqual(got["R"], 0)
            for a in areas:
                self.assertLessEqual(got[a.code], cap_for(a.height, SCOUT_SPACING_M, sw))
            seen.append(got)
        self.assertEqual(seen[2], {"F": 4, "N": 0, "S": 0, "R": 0})
        self.assertEqual(seen[4], {"F": 4, "N": 6, "S": 6, "R": 0})
        self.assertGreater(seen[-1]["R"], 0)                          # more drones than the areas can use
        self.assertEqual(seen[0]["F"], 1)                             # too few for a full circuit: the floor
        squads = build_squads(seen[4], areas, DIRECTIVE, sw)
        self.assertEqual([sq.code for sq in squads], ["F", "N", "S"])
        self.assertEqual(sorted(d for sq in squads for d in sq.drones), list(range(16)))
        with self.assertRaises(ValueError):
            build_squads({"F": 40}, areas, DIRECTIVE, sw)            # wider than the floor

    def test_specs_and_scripts_parse(self):
        self.assertEqual(parse_counts("F10-N7-S7"), {"F": 10, "N": 7, "S": 7})
        self.assertEqual(parse_counts("f2,n3"), {"F": 2, "N": 3})
        self.assertEqual(parse_counts("auto"), "auto")
        self.assertIsNone(parse_counts("none"))
        with self.assertRaises(ValueError):
            parse_counts("Fx")
        self.assertEqual(canonical({"F": 4, "N": 6, "S": 6, "R": 0}), "F4-N6-S6-R0")
        self.assertEqual(parse_script("300:move F N 4; 420:move n s 2"),
                         [{"t": 300.0, "op": "move", "from": "F", "to": "N", "k": 4},
                          {"t": 420.0, "op": "move", "from": "N", "to": "S", "k": 2}])
        with self.assertRaises(ValueError):
            parse_script("300:send F N 4")

    def test_validator_checks_squad_orders(self):
        v = SafetyValidator.__new__(SafetyValidator)
        v.log = []
        flying, caps = {"F": 4, "N": 6}, {"F": 21, "N": 22}
        self.assertEqual(v.check_squads({"op": "move", "from": "F", "to": "N", "k": 2}, flying, caps, 0.0, "op"), [])
        self.assertTrue(v.check_squads({"op": "move", "from": "F", "to": "N", "k": 1}, flying, caps, 5.0, "op"))
        self.assertTrue(v.check_squads({"op": "move", "from": "F", "to": "N", "k": 4}, flying, caps, 20.0, "op"))
        self.assertTrue(v.check_squads({"op": "move", "from": "F", "to": "F", "k": 1}, flying, caps, 40.0, "op"))
        self.assertTrue(v.check_squads({"op": "move", "from": "F", "to": "N", "k": 2}, flying, caps, 60.0, "op",
                                       incoming={"N": 15}))                # 6 + 15 on the way + 2 > 22
        self.assertEqual([e["action"] for e in v.log], ["accepted"] + ["rejected"] * 4)
        self.assertTrue(all(e["kind"] == "squads" for e in v.log))


class SquadFlightTest(unittest.TestCase):
    def test_the_valley_is_the_same_whatever_flies_it(self):
        a = wildfire_valley(2001, n=16)
        b = wildfire_valley(2001, n=16, squads="auto", patrol_s=600.0)
        self.assertEqual([(f.x, f.y, f.t_active_s) for f in a.wildfire.fires],
                         [(f.x, f.y, f.t_active_s) for f in b.wildfire.fires])
        self.assertEqual(b.n, 16)
        self.assertEqual(b.duration_s, 600.0)
        self.assertEqual(b.squad_counts, {"F": 4, "N": 6, "S": 6, "R": 0})
        self.assertIn("squads", b.summary())
        self.assertNotIn("squads", a.summary())
        c = wildfire_valley(2001, n=64, squads="auto")
        self.assertEqual(c.n, 64 - c.squad_counts["R"])                  # the reserve does not fly
        f = wildfire_valley(2001, n=16, squads="auto:F")
        self.assertEqual({k: v for k, v in f.squad_counts.items() if k != "R"}, {"F": 11, "N": 0, "S": 0})

    def test_an_operator_moves_drones_between_squads(self):
        script = parse_script("10:move F N 1; 12:move N F 30")          # the second is too soon and too many
        row, res = run_one("wildfire_valley", 2001, "improved+fixed", n=8, keep_result=True, record_hz=1.0,
                           options={"squads": "F4-N4", "squad_script": script}, duration_s=80.0)
        self.assertEqual(row["collisions"] + row["obstacle_crashes"] + row["ground_crashes"], 0)
        self.assertEqual(row["drones_retired"], 0)
        self.assertEqual(row["squads"], "F4-N4-S0-R0")
        kinds = [e["kind"] for e in res.events]
        self.assertEqual(kinds.count("squad_changed"), 1)
        self.assertEqual(kinds.count("squad_change_rejected"), 1)
        changed = next(e for e in res.events if e["kind"] == "squad_changed")
        moved = changed["drones"]
        self.assertEqual(len(moved), 1)
        self.assertEqual([s["n"] for s in changed["squads"]], [3, 4])   # the mover has left F, not yet joined N
        joined = [e for e in res.events if e["kind"] == "squad_joined"]
        self.assertEqual([e["drone"] for e in joined], moved)
        self.assertEqual(joined[0]["squad_now"]["n"], 5)
        frames = res.frames
        self.assertIn("sq", frames[-1])
        self.assertEqual(frames[-1]["sq"].count(1), 5)
        self.assertEqual(len(frames[-1]["squads"]), 2)
        # In transit the mover belongs to no squad.
        mid = next(f for f in frames if f["t"] > changed["t"])
        self.assertEqual(mid["sq"][moved[0]], -1)
        self.assertEqual(row["squad_changes"], 1)

    def test_rule_supervisor_flies_squads(self):
        row = run_one("wildfire_valley", 2002, "improved+rule", n=16, options={"squads": "auto"}, duration_s=40.0)
        self.assertEqual(row["collisions"] + row["obstacle_crashes"] + row["ground_crashes"], 0)
        self.assertEqual(row["squads"], "F4-N6-S6-R0")


class StationMergeTest(unittest.TestCase):
    def _sim(self, station):
        sc = make("wildfire_valley", 2001, n=4, options={"station": station, "report_v2": True})
        body = DroneBody()
        return Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=2001,
                          body=body, record_hz=0)

    def test_tight_merge_keeps_a_neighbouring_hotspot_as_its_own_fire(self):
        out = {}
        for station in ("legacy", "tight"):
            sim = self._sim(station)
            wf = sim.wildfire
            f0 = wf.fires[0]
            wf._fix_src, wf._fix_votes = [0], [0, 0, 0]
            wf._locate(sim, f0.x + 3.0, f0.y, 6.0, "smoke", None)
            wf._fix_src, wf._fix_votes = None, []
            wf._own_src = [9]
            wf._locate(sim, f0.x + 45.0, f0.y, 2.0, "thermal", 3.0)      # 42 m from the smoke fix
            wf._own_src = None
            out[station] = wf
        self.assertEqual(len(out["legacy"].located), 1)                  # moved onto the hotspot
        self.assertEqual(len(out["tight"].located), 2)                   # outside 3 x (6 + 2) + 5 = 29 m
        self.assertEqual(out["tight"].located[1]["own"], [(9, 0.0, "thermal")])
        self.assertEqual(out["tight"].located[0]["own"], [(0, 0.0, "smoke")])
        # Within the errors it is the same fire, upgraded, and re-scored where it now sits.
        sim = self._sim("tight")
        wf = sim.wildfire
        f0 = wf.fires[0]
        wf._fix_src, wf._fix_votes = [0], [0, 0, 0]
        wf._locate(sim, f0.x + 3.0, f0.y, 6.0, "smoke", None)
        wf._own_src = [0]
        wf._locate(sim, f0.x + 1.0, f0.y, 2.0, "thermal", 3.0)
        self.assertEqual(len(wf.located), 1)
        self.assertEqual(wf.located[0]["method"], "thermal")
        self.assertEqual(wf.located[0]["truth"], 0)

    def test_report_v2_adds_keys_and_legacy_rows_do_not_change(self):
        a = run_one("wildfire_valley", 2001, "improved+fixed", n=4, duration_s=30.0)
        b = run_one("wildfire_valley", 2001, "improved+fixed", n=4, duration_s=30.0, options={"report_v2": True})
        for k in ("fires_found_own", "fire_classes", "fires_found_own_smouldering"):
            self.assertNotIn(k, a)
            self.assertIn(k, b)
        self.assertEqual(sum(b["fire_classes"].values()), b["fires_total"])
        self.assertTrue(all("miss" in f and "own_t" in f and "camera_t" in f for f in b["fire_table"]))
        self.assertEqual(a["fires_located"], b["fires_located"])
        with self.assertRaises(ValueError):
            run_one("wildfire_valley", 2001, "improved+fixed", n=4, duration_s=1.0, options={"station": "loose"})


if __name__ == "__main__":
    unittest.main()
