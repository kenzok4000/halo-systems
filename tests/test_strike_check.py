"""The strike-check team: its box, its orders, its pickers and a short flight."""

import json
import unittest

import numpy as np

from halo_systems.control.directive import Directive
from halo_systems.control.squads import Area, SquadPlan, build_squads
from halo_systems.eval.runner import make_controller, make_supervisor
from halo_systems.eval.scenarios import (SCOUT_ALTITUDE_M, SCOUT_SPACING_M, SCOUT_SPEED_MPS, VALLEY_EXTENT,
                                         wildfire_valley)
from halo_systems.safety.validator import SafetyValidator
from halo_systems.sim.body import DroneBody
from halo_systems.sim.engine import Simulation
from halo_systems.ai.client import NebiusError
from halo_systems.sim.strike_check import CHECK_BOX_M, CHECK_K, CHECK_LIFT_M, check_box
from halo_systems.supervisor.strikes import NearestPicker, NemotronPicker, RulePicker, parse_reply, user_message

DIRECTIVE = Directive("line", SCOUT_SPACING_M, SCOUT_SPEED_MPS, SCOUT_ALTITUDE_M, 1.0, source="mission")


def row(i, **kw):
    r = {"id": i, "night": "last night", "hours_ago": 8.0, "patrolled": True, "dist_m": 300, "ground": "scrub",
         "slope_pct": 10, "seen_pct": 0, "fire_near_m": None, "smoke_lines": 0, "smoke_drones": 0, "people_m": 400}
    r.update(kw)
    return r


CTX = {"t_min": 3.0, "left_min": 9.0, "team_flying": 3, "look_min": 1.9, "transit_mps": 8.0, "done": [], "located": 2}


class BoxAndOrderTest(unittest.TestCase):
    def test_the_box_and_the_line_round_it_stay_inside_the_valley(self):
        x0, y0, x1, y1 = VALLEY_EXTENT
        reach = 0.5 * (CHECK_K - 1) * SCOUT_SPACING_M + 0.5 * 16.6      # the line's half-width past the box
        for x, y in [(x0, y0), (x1, y1), (250.0, 150.0), (x0 - 50.0, 100.0)]:
            b = check_box(x, y, VALLEY_EXTENT, spacing=SCOUT_SPACING_M, swath=16.6)
            self.assertAlmostEqual(b[2] - b[0], CHECK_BOX_M)
            self.assertTrue(x0 + reach < b[0] and b[2] < x1 - reach and y0 + reach < b[1] and b[3] < y1 - reach)
        b = check_box(250.0, 150.0, VALLEY_EXTENT)
        self.assertEqual(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), (250.0, 150.0))

    def test_the_validator_takes_only_listed_unchecked_strikes(self):
        v = SafetyValidator(0.25)
        ok = v.check_strike({"op": "check", "strike": 3}, {1, 2, 3}, {1}, 3, 10.0, "rule")
        self.assertEqual(ok, [])
        self.assertTrue(v.check_strike({"op": "check", "strike": 1}, {1, 2, 3}, {1}, 3, 11.0, "rule"))
        self.assertTrue(v.check_strike({"op": "check", "strike": 9}, {1, 2, 3}, set(), 3, 12.0, "rule"))
        self.assertTrue(v.check_strike({"op": "check", "strike": True}, {1, 2, 3}, set(), 3, 12.0, "rule"))
        self.assertTrue(v.check_strike({"op": "check", "strike": 2}, {1, 2, 3}, set(), 0, 13.0, "rule"))
        self.assertTrue(v.check_strike({"op": "check", "strike": 2}, {1, 2, 3}, set(), 3, 14.0, "rule",
                                       airspace_hold=True))
        self.assertEqual([e["action"] for e in v.log if e.get("kind") == "strike"],
                         ["accepted"] + ["rejected"] * 5)


class PickerTest(unittest.TestCase):
    def test_nearest_takes_last_nights_nearest(self):
        t = [row(1, dist_m=100, night="night before", hours_ago=30.0), row(2, dist_m=500), row(3, dist_m=200)]
        self.assertEqual(NearestPicker().pick(t, CTX)["strike"], 3)

    def test_the_rule_puts_unplaced_smoke_first_and_explained_strikes_last(self):
        t = [row(1, dist_m=100, fire_near_m=60), row(2, dist_m=600, smoke_lines=9, smoke_drones=3),
             row(3, dist_m=200, seen_pct=95), row(4, dist_m=400), row(5, dist_m=500, patrolled=False),
             row(6, dist_m=50, night="night before", hours_ago=30.0)]
        self.assertEqual(RulePicker().pick(t, CTX)["strike"], 2)          # smoke nobody has placed
        t = [r for r in t if r["id"] != 2]
        self.assertEqual(RulePicker().pick(t, CTX)["strike"], 3)          # then last night's, nearest first
        t = [r for r in t if r["id"] not in (3, 4, 5)]
        self.assertEqual(RulePicker().pick(t, CTX)["strike"], 6)          # then the night before's
        t = [row(1, dist_m=100, fire_near_m=60), row(6, dist_m=900, night="night before", hours_ago=30.0)]
        self.assertEqual(RulePicker().pick(t, CTX)["strike"], 6)

    def test_nemotron_reply_must_name_an_offered_strike(self):
        class Reply:
            def __init__(self, text):
                self.content = text
                self.record = type("R", (), {"to_dict": lambda s: {"latency_s": 2.5, "cost_usd": 0.001}})()

        class Fake:
            def __init__(self, text):
                self.text, self.messages = text, None

            def chat(self, model, messages, **kw):
                self.messages = messages
                return Reply(self.text)

        t = [row(4), row(7)]
        got = NemotronPicker(client=Fake('{"strike": 7, "why": "smoke near it"}')).pick(t, CTX)
        self.assertEqual((got["strike"], got["valid"], got["latency_s"]), (7, True, 2.5))
        got = NemotronPicker(client=Fake('{"strike": 5, "why": "?"}')).pick(t, CTX)
        self.assertEqual((got["strike"], got["valid"]), (None, False))
        got = NemotronPicker(client=Fake("no idea")).pick(t, CTX)
        self.assertEqual((got["strike"], got["valid"]), (None, False))
        got = NemotronPicker(client=Fake('{"strike": null, "why": "nothing likely left"}')).pick(t, CTX)
        self.assertEqual((got["strike"], got["valid"]), (None, True))
        fake = Fake('{"strike": "4", "why": "x"}')
        self.assertEqual(NemotronPicker(client=fake).pick(t, CTX)["strike"], 4)
        # The model sees the table and the patrol's state, never which strikes started fires.
        text = fake.messages[1]["content"]
        self.assertIn('"id":4', text)
        self.assertNotIn("near_fire", text)
        self.assertEqual(text, user_message(t, CTX))

    def test_odd_replies_are_unusable_never_a_crash(self):
        rec = {"latency_s": 1.0}
        for text in ['{"strike": 7.0}', '{"strike": "\u0661"}', '{"strike": "9' + '9' * 5000 + '"}', "[7]",
                     '{"strike": {"id": 7}}', '{"strike": true}', "", None, "[" * 5000]:
            got = parse_reply(text, {7}, rec, "live")
            self.assertIn(got["strike"], (7, None), text)
            self.assertLessEqual(len(got["why"]), 200)
        self.assertEqual(parse_reply('{"strike": 7.0}', {7}, rec, "live")["strike"], 7)

    def test_a_failed_call_is_unusable_and_replays_the_same_way(self):
        import tempfile, os
        from halo_systems.ai.client import CallRecord

        class Failing:
            calls = 0

            def chat(self, *a, **kw):
                Failing.calls += 1
                raise NebiusError("reply cut off at max_tokens", record=CallRecord("x", "strike-picker", "m", 0.0,
                                                                                   latency_s=4.0))

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache.jsonl")
            got = NemotronPicker(client=Failing(), cache_path=path).pick([row(4)], CTX)
            self.assertEqual((got["strike"], got["valid"], got["latency_s"]), (None, False, 4.0))
            again = NemotronPicker(cache_path=path, cache_mode="replay").pick([row(4)], CTX)
            self.assertEqual((again["strike"], again["valid"], again["how"]), (None, False, "replayed"))
            self.assertEqual(Failing.calls, 1)

        class Broke:
            def chat(self, *a, **kw):
                raise NebiusError("budget cap", fatal=True)

        with self.assertRaises(NebiusError):
            NemotronPicker(client=Broke()).pick([row(4)], CTX)
        with self.assertRaises(ValueError):
            NemotronPicker(cache_mode="replay")


class RetargetTest(unittest.TestCase):
    def test_a_retargeted_team_travels_line_abreast_and_joins_its_new_box(self):
        sw = 16.6
        areas = [Area("F", "floor", (0.0, 0.0, 500.0, 300.0)), Area("C", "check", check_box(250, 150, VALLEY_EXTENT, k=3))]
        sq = build_squads({"F": 4, "C": 3}, areas, DIRECTIVE, sw)
        sq[1].alt_offset_m = CHECK_LIFT_M
        plan = SquadPlan(sq, DIRECTIVE, 7, reassign=True, blend=True, pace=True, swath=sw)
        self.assertAlmostEqual(plan.parts[1].centre()[2], SCOUT_ALTITUDE_M + CHECK_LIFT_M)
        pos, _ = plan.slots()
        sent = plan.retarget(1, check_box(-200.0, 400.0, VALLEY_EXTENT, k=3), pos, 0.0)
        self.assertEqual(sorted(sent), [4, 5, 6])
        self.assertTrue(all(plan.squad_of[d] == -1 for d in sent))
        aims = [plan.parts[1].centre()[:2] + plan.transit[d]["offset"] for d in sent]
        gaps = [np.linalg.norm(a - b) for i, a in enumerate(aims) for b in aims[i + 1:]]
        self.assertGreaterEqual(min(gaps), SCOUT_SPACING_M - 1e-6)
        # The team joins together (one join, not one drone at a time across the others' paths), and its drones
        # keep their spacing on the way and while taking their slots.
        joins, closest = [], np.inf
        for step in range(640):
            plan.advance(0.25, step * 0.25)
            pos, _ = plan.slots()
            closest = min(closest, min(np.linalg.norm(pos[a] - pos[b]) for a, b in ((4, 5), (4, 6), (5, 6))))
            done = plan.join_arrivals(pos, step * 0.25)
            if done:
                joins.append(sorted(d for d, _ in done))
        self.assertFalse(plan.transit)
        self.assertEqual(joins, [[4, 5, 6]])
        self.assertEqual(sorted(plan.squads[1].drones), [4, 5, 6])
        self.assertGreaterEqual(closest, 10.0)


class TeamMotionTest(unittest.TestCase):
    def test_the_team_keeps_its_height_and_its_new_box_waits_for_it(self):
        sc = wildfire_valley(3001, n=8, patrol_s=240.0, report_v2=True, checks="rule")
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=3001,
                         body=body, record_hz=0)
        plan = sim.ctrl.plan
        team = [q.code for q in plan.squads].index("C")
        pos, _ = plan.slots()
        before = pos[plan.squads[team].drones, 2].copy()
        sent = plan.retarget(team, check_box(-200.0, 400.0, VALLEY_EXTENT), pos, 0.0)
        c0 = plan.parts[team].centre().copy()
        plan.advance(0.25, 0.0)
        self.assertTrue(np.allclose(plan.parts[team].centre(), c0))   # held at the circuit's start while empty
        after, _ = plan.slots()
        # In transit the team stays at its own height (CHECK_LIFT_M over the squads), not the lower transit layer.
        self.assertLess(float(np.max(np.abs(after[sent, 2] - before))), 1.0)

    def test_the_team_is_not_moved_by_hand(self):
        v = SafetyValidator(0.5)
        flying, caps = {"F": 4, "C": 4}, {"F": 8, "C": 8}
        self.assertTrue(v.check_squads({"op": "move", "from": "F", "to": "C", "k": 1}, flying, caps, 10.0, "operator"))
        self.assertTrue(v.check_squads({"op": "move", "from": "C", "to": "F", "k": 1}, flying, caps, 30.0, "operator"))


class FlightTest(unittest.TestCase):
    def test_a_short_patrol_checks_a_strike_and_reports_it(self):
        sc = wildfire_valley(3001, n=8, patrol_s=240.0, report_v2=True, checks="rule")
        self.assertEqual(sc.squad_counts["C"], CHECK_K)
        self.assertEqual(sum(len(q.drones) for q in sc.squads), sc.n)
        self.assertEqual(sc.n, 8)
        self.assertIn("strike_check", sc.summary())
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=3001,
                         body=body, record_hz=0)
        m = sim.run().metrics
        self.assertGreaterEqual(m["strike_checks"], 1)
        self.assertEqual(m["strike_picker"], "rule")
        self.assertEqual(m["collisions"] + m["obstacle_crashes"] + m["ground_crashes"], 0)
        first = m["strike_check_table"][0]
        self.assertIsNotNone(first["t_look"])
        self.assertEqual([e["action"] for e in sim.validator.log if e.get("kind") == "strike"][0], "accepted")
        json.dumps(m)                                  # the row is plain JSON
        # What the cameras have seen is kept over the whole valley, so the team's own looks outside the area squads'
        # block count in the table's seen_pct.
        x0, y0, x1, y1 = sc.sensor.area
        gx, gy = sim.checker._seen_x, sim.checker._seen_y
        inside = ((gx >= x0) & (gx <= x1))[:, None] & ((gy >= y0) & (gy <= y1))[None, :]
        self.assertTrue((sim.checker.seen & ~inside).any())
        self.assertEqual(sim.checker.seen[inside].sum() > 0, sim.coverage.any())

    def test_every_drone_flies_and_a_hold_rules_no_strike_out(self):
        for n in (8, 12, 16):
            sc = wildfire_valley(3001, n=n, checks="rule")
            self.assertEqual((sc.n, sum(len(q.drones) for q in sc.squads), sc.squad_counts.get("R", 0)), (n, n, 0))
        sc = wildfire_valley(3001, n=8, patrol_s=200.0, report_v2=True, checks="rule")
        sc = __import__("dataclasses").replace(sc, airspace_script=[{"t": 0.5, "op": "hold"}, {"t": 15.0, "op": "resume"}])
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=3001,
                         body=body, record_hz=0)
        sim.run()
        rej = [e for e in sim.validator.log if e.get("kind") == "strike" and e["action"] == "rejected"]
        self.assertLessEqual(len(rej), 1)
        self.assertFalse(sim.checker.refused)

    def test_without_checks_the_valley_is_unchanged(self):
        a = wildfire_valley(3001, n=16, squads="auto", strikes="all").summary()
        b = wildfire_valley(3001, n=16, squads="auto").summary()
        a.pop("strikes")
        self.assertEqual(a, b)
        self.assertNotIn("strike_check", a)


if __name__ == "__main__":
    unittest.main()
