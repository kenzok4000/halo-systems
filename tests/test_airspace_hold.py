"""Aircraft inbound: the operator's airspace hold and resume, from the validator to the plan, engine and dashboard.

Crewed firefighting aircraft (air tankers, helicopters, a lead plane) entering the area need the drones out of their
way: a hold stops every flying drone where it is and lowers it to a hold altitude until the operator resumes.
"""

import json
import math
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from halo_systems.control.directive import Directive
from halo_systems.control.formations import Path as Route
from halo_systems.control.plan import ALT_RATE_MPS, HOLD_DECEL_MPS2, LEADER_ACCEL, YAW_ACCEL, FormationPlan, ramp
from halo_systems.control.squads import TRANSIT_LIFT_M, Area, SquadPlan, build_squads, parse_script
from halo_systems.eval.runner import make_controller, make_supervisor
from halo_systems.eval.scenarios import make
from halo_systems.safety.validator import HOLD_ALTITUDE_M, SafetyValidator
from halo_systems.sim.body import DroneBody
from halo_systems.sim.engine import ACTIVE, Simulation
from halo_systems.sim.scenario import ScriptedDirective

DT = 0.02
HOLD_AT, RESUME_AT, SNAP_AT = 6.0, 18.0, 26.0
SCRIPT = [{"t": HOLD_AT, "op": "hold"}, {"t": RESUME_AT, "op": "resume"}]


def transit(script=None, n=6, route_m=160.0):
    """open_transit on a shorter route (160 m), so a flight with a 12 s hold finishes in under a minute."""
    return make("open_transit", 1, n=n, waypoints=[(0.0, 0.0), (route_m, 0.0)], duration_s=60.0,
                airspace_script=list(script or []))


def simulation(sc, config="improved+fixed", seed=1, record_hz=0.0):
    ctrl, sup = config.split("+")
    body = DroneBody()
    return Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body,
                      record_hz=record_hz)


def step_to(sim, t):
    while not sim.ended and sim.t < t - 1e-9:
        sim.step()


def ramp_time(start, goal, within):
    """Seconds the plan's altitude ramp takes from ``start`` (at rest) to within ``within`` of ``goal``."""
    alt, rate, t = start, 0.0, 0.0
    while abs(alt - goal) > within:
        alt, rate, _ = ramp(alt, rate, goal, DT)
        t += DT
    return t


def braking_m(vz, accel=8.0):
    """How far a drone climbing at ``vz`` rises while braking at the controllers' acceleration limit."""
    return max(vz, 0.0) ** 2 / (2 * accel)


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.v = SafetyValidator(0.3)

    def check(self, order, t=100.0, since=None, held=None, directive_alt=20.0, current=None):
        return self.v.check_airspace(order, held_since_s=since, hold_altitude_m=held,
                                     directive_altitude_m=directive_alt, t=t, source="operator",
                                     current_altitude_m=current)

    def test_unknown_orders_and_resume_rules(self):
        self.assertEqual(self.check({"op": "climb"}).action, "rejected")
        self.assertEqual(self.check({}).action, "rejected")
        self.assertEqual(self.check("hold").action, "rejected")
        r = self.check({"op": "resume"})                                   # not holding
        self.assertEqual(r.action, "rejected")
        self.assertIn("not holding", r.violations[0])
        r = self.check({"op": "resume"}, t=102.0, since=100.0, held=10.0)  # 2 s after the hold began: chatter
        self.assertEqual(r.action, "rejected")
        self.assertIn("after the hold began", r.violations[0])
        self.assertTrue(self.check({"op": "resume"}, t=103.0, since=100.0, held=10.0).ok)
        # Step times are sums of 0.02 s: 4.02 - 1.02 is 2.9999999999999996, still a resume 3 s after the hold.
        self.assertTrue(self.check({"op": "resume"}, t=4.02, since=1.02, held=10.0).ok)
        self.assertFalse(self.check({"op": "resume"}, t=4.0, since=1.02, held=10.0).ok)

    def test_hold_altitude_rules(self):
        r = self.check({"op": "hold"})
        self.assertEqual((r.action, r.altitude_m, r.violations), ("accepted", HOLD_ALTITUDE_M, []))
        for bad in ("12", True, False, [10], math.inf, -math.inf, math.nan, 10 ** 400, -10 ** 400):
            self.assertEqual(self.check({"op": "hold", "altitude_m": bad}).action, "rejected", bad)
            self.assertEqual(self.v.log[-1]["action"], "rejected")
        self.assertIn("not finite", self.check({"op": "hold", "altitude_m": 10 ** 400}).violations[0])
        self.assertEqual(self.check({"op": "hold", "altitude_m": 12}).altitude_m, 12.0)
        r = self.check({"op": "hold", "altitude_m": 35.0})                 # a hold never climbs
        self.assertEqual((r.action, r.altitude_m), ("clamped", 20.0))
        self.assertIn("never climbs", r.violations[0])
        r = self.check({"op": "hold", "altitude_m": 12.0}, directive_alt=8.0)
        self.assertEqual((r.action, r.altitude_m), ("clamped", 8.0))
        r = self.check({"op": "hold", "altitude_m": 2.0})                  # below the 5 m floor
        self.assertEqual((r.action, r.altitude_m), ("clamped", 5.0))
        # Nor above where the swarm is now (still climbing to a new directive altitude, or climbing back after a
        # hold), though never below the floor.
        r = self.check({"op": "hold", "altitude_m": 15.0}, current=12.06)
        self.assertEqual((r.action, r.altitude_m), ("clamped", 12.06))
        self.assertIn("swarm's altitude now", r.violations[0])
        self.assertIn("never climbs", r.violations[0])
        self.assertEqual(self.check({"op": "hold"}, current=12.06).action, "accepted")
        self.assertEqual(self.check({"op": "hold"}, current=25.0).altitude_m, 10.0)
        self.assertEqual(self.check({"op": "hold", "altitude_m": 8.0}, current=2.0).altitude_m, 5.0)
        # While holding, only a lower hold is accepted.
        self.assertTrue(self.check({"op": "hold", "altitude_m": 7.0}, since=90.0, held=10.0).ok)
        for alt in (10.0, 15.0, None):
            r = self.check({"op": "hold", "altitude_m": alt} if alt else {"op": "hold"}, since=90.0, held=10.0)
            self.assertEqual(r.action, "rejected")
            self.assertIn("already holding", r.violations[-1])

    def test_a_hold_is_never_refused_for_timing_and_is_logged(self):
        d = Directive("line", 6.0, 6.0, 20.0, 1.0)
        self.assertTrue(self.v.check(d.with_(speed_mps=4.0), d, 50.0, "operator").ok)    # a plan change at 50 s
        r = self.check({"op": "hold"}, t=50.1)
        self.assertTrue(r.ok)
        self.assertEqual(self.v.last_change_t, 50.0)                      # the directive rate limit is untouched
        self.assertFalse(self.v.check(d, d.with_(speed_mps=4.0), 50.2, "rule").ok)    # and still holds
        entries = [e for e in self.v.log if e.get("kind") == "airspace"]
        self.assertEqual(len(entries), 1)
        self.assertEqual({k: entries[0][k] for k in ("t", "source", "action", "altitude_m")},
                         {"t": 50.1, "source": "operator", "action": "accepted", "altitude_m": 10.0})
        self.assertEqual(entries[0]["order"], {"op": "hold"})
        self.check({"op": "nope"}, t=51.0)
        self.assertEqual(self.v.log[-1]["action"], "rejected")
        self.assertIsNone(self.v.log[-1]["altitude_m"])


class PlanTest(unittest.TestCase):
    def test_hold_brakes_descends_and_resume_climbs_before_moving_on(self):
        for pace in (True, False):
            with self.subTest(pace=pace):
                plan = FormationPlan(Route([(0.0, 0.0), (400.0, 0.0)]), Directive("line", 6.0, 6.0, 20.0, 1.0), 4,
                                     reassign=pace, blend=pace, pace=pace)
                t = 0.0
                for _ in range(500):
                    plan.advance(DT, t)
                    t += DT
                v0, s0 = plan.speed_now, plan.s
                self.assertAlmostEqual(v0, 6.0)
                plan.hold(10.0)
                speeds, alts, vz = [v0], [20.0], []
                for _ in range(600):
                    plan.advance(DT, t)
                    t += DT
                    speeds.append(plan.speed_now)
                    alts.append(plan.centre()[2])
                    vz.append(plan.slots()[1][0, 2])
                    self.assertFalse(plan.done())
                self.assertGreaterEqual(np.diff(speeds).min(), -HOLD_DECEL_MPS2 * DT - 1e-9)
                self.assertEqual(speeds[-1], 0.0)
                self.assertLessEqual(plan.s, s0 + v0 ** 2 / (2 * HOLD_DECEL_MPS2) + 0.1)
                self.assertEqual(alts[-1], 10.0)
                self.assertLessEqual(np.abs(np.diff(alts)).max(), ALT_RATE_MPS * DT + 1e-9)
                self.assertAlmostEqual(min(vz), -ALT_RATE_MPS)       # the slot carries the vertical rate
                self.assertEqual(plan.heading_rate, 0.0)
                frozen = plan.s
                plan.resume()
                climbed = None
                for k in range(1000):
                    plan.advance(DT, t)
                    t += DT
                    if climbed is None:
                        if plan.centre()[2] < 20.0:
                            self.assertEqual(plan.s, frozen)         # climbs back before moving on
                            continue
                        climbed = k
                    speeds.append(plan.speed_now)
                self.assertIsNotNone(climbed)
                self.assertGreater(plan.s, frozen)
                self.assertLessEqual(np.diff(speeds).max(), LEADER_ACCEL * DT + 1e-9)
                self.assertAlmostEqual(plan.speed_now, 6.0)
                self.assertIsNone(plan.alt_now)                      # flying the directive's altitude again

    @staticmethod
    def flown(pace, route=((0.0, 0.0), (400.0, 0.0)), until_s=None, altitude=20.0):
        plan = FormationPlan(Route(list(route)), Directive("line", 6.0, 6.0, altitude, 1.0), 4,
                             reassign=pace, blend=pace, pace=pace)
        t = 0.0
        while (plan.s < until_s) if until_s is not None else (t < 10.0 - 1e-9):
            plan.advance(DT, t)
            t += DT
        return plan, t

    def test_a_directive_change_during_a_hold_is_in_force_but_the_hold_wins_until_resume(self):
        for pace in (True, False):
            with self.subTest(pace=pace):
                plan, t = self.flown(pace)
                plan.hold(10.0)
                for _ in range(500):
                    plan.advance(DT, t)
                    t += DT
                frozen, target = plan.s, plan.target.copy()
                # A supervisor's (or the operator's) new plan: faster, lower than the hold, another formation.
                plan.set_directive(Directive("grid", 8.0, 8.0, 8.0, 1.0), None, t)
                self.assertFalse(np.array_equal(plan.target, target))
                for _ in range(500):
                    plan.advance(DT, t)
                    t += DT
                    self.assertEqual(plan.speed_now, 0.0)
                self.assertEqual(plan.s, frozen)
                self.assertEqual(plan.centre()[2], 8.0)                  # min(hold, the directive in force)
                plan.resume()
                for _ in range(1500):
                    plan.advance(DT, t)
                    t += DT
                self.assertIsNone(plan.alt_now)
                self.assertEqual(plan.centre()[2], 8.0)
                self.assertAlmostEqual(plan.speed_now, 8.0)
                self.assertGreater(plan.s, frozen + 50.0)

    def test_a_hold_keeps_the_heading_even_mid_turn(self):
        for pace in (True, False):
            with self.subTest(pace=pace):
                plan, t = self.flown(pace, route=((0.0, 0.0), (100.0, 0.0), (100.0, 200.0)), until_s=101.0)
                self.assertGreater(abs(plan.heading_rate), 0.01)      # turning when the order comes
                plan.hold(10.0)
                rates, headings = [plan.heading_rate], []
                for _ in range(500):
                    plan.advance(DT, t)
                    t += DT
                    rates.append(plan.heading_rate)
                    headings.append(plan.heading)
                if pace:
                    self.assertLessEqual(np.abs(np.diff(rates)).max(), YAW_ACCEL * DT + 1e-9)
                else:
                    self.assertEqual(rates[1], 0.0)
                self.assertEqual(rates[-1], 0.0)
                settled = headings[-1]
                self.assertLess(abs(settled), math.pi / 2 - 0.3)       # stopped turning well short of the new leg
                plan.resume()
                while plan.alt_now is not None:
                    plan.advance(DT, t)
                    t += DT
                    self.assertEqual(plan.heading, settled)            # until the climb back is complete

    def test_a_hold_starts_where_the_drones_are_and_never_climbs(self):
        # The directive was just raised from 20 m to 40 m and the drones are on their way up, at 24 m.
        plan, t = self.flown(True, altitude=40.0)
        pos, _ = plan.slots()
        pos[:, 2] = 24.0
        self.assertEqual(plan.altitude_now(pos), 24.0)
        active = np.array([True, True, False, True])
        pos[2, 2] = 3.0                                               # a drone that is not flying does not count
        self.assertEqual(plan.altitude_now(pos, active), 24.0)
        plan.hold(10.0, pos, active)
        alts = []
        for _ in range(500):
            plan.advance(DT, t)
            t += DT
            alts.append(plan.centre()[2])
        self.assertLessEqual(max(alts), 24.0)
        self.assertEqual(alts[-1], 10.0)
        # Climbing back after the resume, a new hold stops the climb at once: the plan's altitude never rises again.
        plan.resume()
        while plan.alt_rate < ALT_RATE_MPS - 1e-9:
            plan.advance(DT, t)
            t += DT
        at_order = plan.altitude_now()
        plan.hold(at_order)
        self.assertEqual(plan.alt_rate, 0.0)
        for _ in range(200):
            plan.advance(DT, t)
            t += DT
            self.assertLessEqual(plan.centre()[2], at_order)


    def test_drones_in_transit_descend_from_where_they_are_and_never_climb(self):
        d = Directive("line", 6.0, 6.0, 20.0, 1.0)
        areas = [Area("A", "west", (0.0, 0.0, 200.0, 100.0)), Area("B", "east", (300.0, 0.0, 500.0, 100.0))]
        plan = SquadPlan(build_squads({"A": 3, "B": 3}, areas, d, 20.0), d, 6, reassign=True, blend=True,
                         pace=True, swath=20.0)
        t = 0.0
        pos, _ = plan.slots()
        plan.move([0, 1], 1, pos, t)                     # two drones on their way from A to B
        for _ in range(50):
            plan.advance(DT, t)
            t += DT
        layer = 20.0 + TRANSIT_LIFT_M
        self.assertEqual(plan.transit[0]["pos"][2], layer)
        pos, _ = plan.slots()
        pos[0, 2], pos[1, 2] = 24.0, 12.0                # one still climbing to the layer, one below the hold
        plan.hold(15.0, pos)
        self.assertEqual((plan.transit[0]["alt"], plan.transit[1]["alt"]), (24.0, 12.0))
        xy = {k: plan.transit[k]["pos"][:2].copy() for k in (0, 1)}
        top = {0: 24.0, 1: 12.0}
        for _ in range(400):
            plan.advance(DT, t)
            t += DT
            for k in (0, 1):
                self.assertLessEqual(plan.transit[k]["pos"][2], top[k])
                self.assertTrue(np.array_equal(plan.transit[k]["pos"][:2], xy[k]))
        self.assertEqual((plan.transit[0]["alt"], plan.transit[1]["alt"]), (15.0, 12.0))
        # Climbing back after the resume, a new hold stops their climb at once.
        plan.resume()
        while plan.transit[1]["rate"] < 1.0:
            plan.advance(DT, t)
            t += DT
        top = {k: plan.transit[k]["alt"] for k in (0, 1)}
        plan.hold(15.0)
        for _ in range(200):
            plan.advance(DT, t)
            t += DT
            for k in (0, 1):
                self.assertLessEqual(plan.transit[k]["alt"], top[k])
        plan.resume()
        for _ in range(1500):
            plan.advance(DT, t)
            t += DT
        self.assertTrue(all("alt" not in plan.transit.get(k, {}) for k in (0, 1)))    # back on their way


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = sim = simulation(transit(SCRIPT), record_hz=1.0)
        step_to(sim, RESUME_AT - 1.0)
        cls.hold_speeds = np.linalg.norm(sim.v[sim.active_mask()], axis=1)
        step_to(sim, SNAP_AT)
        cls.snap = sim.p.copy(), sim.v.copy(), list(sim.events)
        sim.run()
        cls.res = sim.result()

    def test_scripted_hold_stops_lowers_and_resumes_the_sweep(self):
        sim, res = self.sim, self.res
        m = res.metrics
        self.assertTrue(m["completed"])
        self.assertEqual(m["collisions"] + m["obstacle_crashes"] + m["ground_crashes"], 0)
        hold = m["airspace_holds"]
        self.assertEqual(len(hold), 1)
        h = hold[0]
        self.assertEqual((h["from_s"], h["to_s"], h["altitude_m"], h["source"], h["flying_at_start"]),
                         (HOLD_AT, RESUME_AT, 10.0, "operator", 6))
        self.assertIsNotNone(h["clear_s"])
        self.assertLess(h["clear_s"], RESUME_AT - HOLD_AT)
        # No sooner than the plan's own descent allows (the slot must be nearly at rest for the drones to be slower
        # than 0.5 m/s), and measured from the order.
        self.assertGreaterEqual(h["clear_s"], ramp_time(20.0, 10.0, 0.1) - 0.3)
        self.assertEqual(h["clear_from_s"], HOLD_AT)
        # Every flying drone is stopped, not just down at the hold altitude.
        self.assertEqual(len(self.hold_speeds), 6)
        self.assertLess(self.hold_speeds.max(), 0.5)
        self.assertGreater(h["charge_used_wh"], 0.0)
        self.assertEqual(h["retired_during"], 0)
        self.assertEqual(m["airspace_held_s"], RESUME_AT - HOLD_AT)
        self.assertEqual(m["airspace_clear_s_max"], h["clear_s"])
        # The leader stops within braking distance and stays there until it has climbed back after resume.
        t = np.asarray(res.series["t"])
        s = np.asarray(res.series["progress_m"])
        v = np.asarray(res.series["leader_speed"])
        i0 = int(np.searchsorted(t, HOLD_AT - 1e-9)) - 1
        s0, v0 = s[i0], v[i0]
        during = (t >= HOLD_AT) & (t <= RESUME_AT + 1.0)
        self.assertLessEqual(s[during].max(), s0 + v0 ** 2 / (2 * HOLD_DECEL_MPS2) + 0.1)
        still = (t >= HOLD_AT + v0 / HOLD_DECEL_MPS2 + 0.1) & (t <= RESUME_AT + 1.0)
        self.assertEqual(np.ptp(s[still]), 0.0)
        self.assertGreater(s[-1], s0 + 50.0)
        # Stopped at the hold altitude before the resume: frames are 1 s apart.
        f = next(f for f in res.frames if f["t"] == RESUME_AT - 1.0)
        act = np.asarray(f["s"]) == ACTIVE
        z = np.asarray(f["p"])[act, 2]
        self.assertLessEqual(np.abs(z - 10.0).max(), 1.5)
        self.assertTrue(f["airspace"] == {"holding": True, "altitude_m": 10.0, "since_s": HOLD_AT})
        self.assertTrue(all("airspace" in f for f in res.frames))
        self.assertFalse(res.frames[-1]["airspace"]["holding"])
        # Back at the directive's altitude and speed afterwards.
        self.assertLessEqual(np.abs(np.asarray(res.frames[-1]["p"])[:, 2] - 20.0).max(), 1.5)
        self.assertAlmostEqual(v[int(np.searchsorted(t, RESUME_AT + 15.0))], 6.0)
        kinds = [e["kind"] for e in res.events]
        self.assertEqual((kinds.count("airspace_hold"), kinds.count("airspace_resume")), (1, 1))
        resume = next(e for e in res.events if e["kind"] == "airspace_resume")
        self.assertEqual(resume["held_s"], RESUME_AT - HOLD_AT)
        log = [e for e in res.validator_log if e.get("kind") == "airspace"]
        self.assertEqual([(e["t"], e["order"]["op"], e["action"]) for e in log],
                         [(HOLD_AT, "hold", "accepted"), (RESUME_AT, "resume", "accepted")])
        self.assertIn("airspace", sim.summary())
        self.assertEqual(res.scenario["airspace_script"], SCRIPT)

    def test_an_injected_hold_flies_the_same_as_the_scripted_one(self):
        sim = simulation(transit(), record_hz=1.0)
        step_to(sim, HOLD_AT - DT)
        info = sim.inject("aircraft", op="hold")
        self.assertEqual((info["kind"], info["t"], info["op"], info["by"]), ("aircraft", HOLD_AT, "hold", "operator"))
        self.assertIn("airspace", sim.frame())
        step_to(sim, RESUME_AT - DT)
        sim.inject("aircraft", op="resume")
        step_to(sim, SNAP_AT)
        p, v, events = self.snap
        self.assertTrue(np.array_equal(sim.p, p))
        self.assertTrue(np.array_equal(sim.v, v))
        self.assertEqual([e for e in sim.events if e["kind"] != "injected"], events)
        self.assertEqual(sim.holds, [h for h in self.sim.holds])
        injected = [e for e in sim.events if e["kind"] == "injected"]
        self.assertEqual([(e["what"], e["op"]) for e in injected], [("aircraft", "hold"), ("aircraft", "resume")])

    def test_a_hold_ordered_ahead_waits_for_its_time_and_keeps_a_finished_flight_going(self):
        sim = simulation(transit(route_m=60.0))
        while sim.done_t is None:
            sim.step()
        at = round(sim.t + 1.0, 2)
        info = sim.inject("aircraft", op="hold", at_s=at)            # aircraft due in a second
        self.assertEqual(info["t"], at)
        step_to(sim, at - DT)
        self.assertEqual(sim.holds, [])
        step_to(sim, at)
        self.assertEqual(sim.holds[0]["from_s"], at)
        step_to(sim, sim.done_t + 8.0)                                 # end_when_done would end it at done_t + 5
        self.assertFalse(sim.ended)
        self.assertTrue(sim.ctrl.plan.holding)
        sim.inject("aircraft", op="resume")
        sim.run()
        self.assertTrue(sim.ended)
        self.assertIsNotNone(sim.holds[0]["to_s"])
        self.assertGreaterEqual(sim.t, sim.holds[0]["to_s"])

    def test_a_lower_hold_and_a_hold_while_climbing_back(self):
        """A second, lower hold (a second aircraft pass) counts clear_s from its own order; a hold while climbing
        back after a resume is clamped to the altitude the swarm is at, so it never climbs on."""
        sim = simulation(transit([{"t": 6.0, "op": "hold"}, {"t": 14.0, "op": "hold", "altitude_m": 7.0},
                                  {"t": 22.0, "op": "resume"}, {"t": 24.0, "op": "hold", "altitude_m": 15.0}]))
        step_to(sim, 14.0 - DT)
        first = sim.holds[0]["clear_s"]
        self.assertIsNotNone(first)
        step_to(sim, 14.0)
        h = sim.holds[0]
        self.assertEqual((h["altitude_m"], h["clear_s"], h["clear_from_s"]), (7.0, None, 14.0))
        step_to(sim, 22.0 - DT)
        self.assertGreaterEqual(h["clear_s"], ramp_time(10.0, 7.0, 1.5) - 0.2)
        self.assertLess(h["clear_s"], 8.0)
        step_to(sim, 24.0 - DT)
        alt_then = sim.ctrl.plan.altitude_now()
        self.assertLess(alt_then, 15.0)                                 # still climbing back
        z0, vz0 = sim.p[:, 2].max(), sim.v[:, 2].max()
        z = z0
        while sim.t < 30.0 - 1e-9:
            sim.step()
            z = max(z, sim.p[:, 2].max())
        self.assertLessEqual(z, z0 + braking_m(vz0) + 0.3)
        e = [e for e in sim.validator.log if e.get("kind") == "airspace"][-1]
        self.assertEqual(e["action"], "clamped")
        self.assertIn("never climbs", e["violations"][0])
        self.assertEqual(e["altitude_m"], round(alt_then, 2))
        self.assertEqual(sim.holds[1]["altitude_m"], round(alt_then, 2))
        m = sim.result().metrics
        self.assertEqual(m["airspace_clear_s_max"], max(h["clear_s"], sim.holds[1]["clear_s"]))

    def test_a_hold_just_after_a_climb_order_starts_from_where_the_drones_are(self):
        """The directive is raised from 20 m to 40 m and a hold follows a second later: the drones brake their
        climb at once (their acceleration limit sets how far they rise) instead of climbing on toward 40 m."""
        sc = transit([{"t": 6.0, "op": "hold"}])
        sc.script = [ScriptedDirective(5.0, {"altitude_m": 40.0})]
        sim = simulation(sc)
        step_to(sim, 6.0 - DT)
        z0, vz0 = sim.p[:, 2].max(), sim.v[:, 2].max()
        self.assertGreater(vz0, 2.0)                                    # on the way up
        z = z0
        while sim.t < 14.0 - 1e-9:
            sim.step()
            z = max(z, sim.p[:, 2].max())
        self.assertLessEqual(z, z0 + braking_m(vz0) + 0.5)
        self.assertLess(z, 30.0)
        self.assertEqual(sim.holds[0]["altitude_m"], 10.0)
        self.assertLessEqual(np.abs(sim.p[:, 2] - 10.0).max(), 1.5)

    def test_a_run_without_orders_has_no_airspace_keys_and_bad_injections_raise(self):
        sc = transit()
        self.assertNotIn("airspace_script", sc.summary())
        sim = simulation(sc, record_hz=10.0)
        step_to(sim, 0.5)
        self.assertNotIn("airspace", sim.summary())
        self.assertNotIn("airspace", sim.frame())
        self.assertFalse(any("airspace" in f for f in sim.frames))
        self.assertFalse(any(k.startswith("airspace") for k in sim.result().metrics))
        for kw in ({"op": None}, {"op": 3}, {"op": "hold", "altitude_m": "low"}, {"op": "hold", "altitude_m": True},
                   {"op": "hold", "at_s": "soon"}, {"op": "hold", "at_s": math.nan}, {"op": "hold", "at_s": 10 ** 400}):
            with self.assertRaises(ValueError):
                sim.inject("aircraft", **kw)
        self.assertNotIn("airspace", sim.frame())
        # An order the validator rejects is logged and rejected; from then on the run reports its airspace state.
        sim.inject("aircraft", op="climb")
        sim.inject("aircraft", op="resume")
        sim.inject("aircraft", op="hold", altitude_m=10 ** 400)        # too large for a float: rejected, no crash
        step_to(sim, sim.t + 0.1)
        rejected = [e for e in sim.events if e["kind"] == "airspace_rejected"]
        self.assertEqual([e["op"] for e in rejected], ["climb", "resume", "hold"])
        self.assertEqual(rejected[-1]["violations"], ["altitude_m is not finite"])
        self.assertEqual([e["action"] for e in sim.validator.log if e.get("kind") == "airspace"], ["rejected"] * 3)
        self.assertEqual(sim.frame()["airspace"], {"holding": False, "altitude_m": None, "since_s": None})
        self.assertEqual(sim.result().metrics["airspace_holds"], [])

    def test_a_squad_order_with_a_bad_time_is_refused_before_it_is_scheduled(self):
        sc = make("wildfire_valley", 2001, n=6, duration_s=4.0, options={"squads": "F3-N3"})
        sim = simulation(sc, seed=2001)
        for bad in (10 ** 400, float("nan"), float("inf"), "soon", [3]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                sim.inject("squad", op="move", **{"from": "F", "to": "N", "k": 1}, at_s=bad)
        self.assertEqual(sim._squad_orders, [])
        info = sim.inject("squad", op="move", **{"from": "F", "to": "N", "k": 1}, at_s=2)
        self.assertEqual(sim._squad_orders[0]["t"], 2.0)
        self.assertEqual(info["op"], "move")

    def test_a_hold_freezes_every_squad_and_drones_in_transit(self):
        # A move at 13 s, during the hold (refused), and another at 15 s, while climbing back (flown).
        sc = make("wildfire_valley", 2001, n=6, duration_s=24.0,
                  options={"squads": "F3-N3",
                           "squad_script": parse_script("3:move F N 1; 13:move N F 1; 15:move F N 1")},
                  airspace_script=[{"t": 5.0, "op": "hold", "altitude_m": 15.0}, {"t": 14.0, "op": "resume"}])
        sim = simulation(sc, seed=2001)
        plan = sim.ctrl.plan

        def height(d):
            return sim.p[d, 2] - sc.world.terrain.follow(sim.p[d:d + 1, 0], sim.p[d:d + 1, 1])[0][0]
        step_to(sim, 5.0 - DT)
        self.assertEqual(len(plan.transit), 1)
        mover = next(iter(plan.transit))
        h0 = height(mover)                                   # still climbing to its transit layer (+10 m)
        step_to(sim, 5.0)
        self.assertTrue(plan.holding)
        xy0 = plan.transit[mover]["pos"][:2].copy()
        s_hold = [p.s for p in plan.parts]
        rise = []
        while sim.t < 8.5 - 1e-9:                            # 3 s of braking from 6 m/s, then still
            sim.step()
            rise.append((height(mover), plan.transit[mover]["alt"]))
        # The drone in transit descends from where it is: its slot never climbs (1 m: navigation error), and the
        # drone itself rises only while braking its climb (and its flight across) at the acceleration limit.
        self.assertLessEqual(max(a for _, a in rise), h0 + 1.0)
        self.assertLessEqual(max(h for h, _ in rise), h0 + 3.0)
        s_still = [p.s for p in plan.parts]
        for a, b in zip(s_hold, s_still):
            self.assertLessEqual(b - a, 6.0 ** 2 / (2 * HOLD_DECEL_MPS2) + 0.1)
        step_to(sim, 13.9)
        self.assertEqual([p.s for p in plan.parts], s_still)
        self.assertTrue(np.array_equal(plan.transit[mover]["pos"][:2], xy0))
        self.assertTrue(np.array_equal(plan.transit[mover]["vel"][:2], [0.0, 0.0]))
        act = sim.active_mask()
        ground = sc.world.terrain.follow(sim.p[act, 0], sim.p[act, 1])[0]
        self.assertLessEqual(np.abs(sim.p[act, 2] - ground - 15.0).max(), 1.5)   # the transit layer is capped too
        self.assertLess(np.linalg.norm(sim.v[act], axis=1).max(), 0.5)           # and every drone is stopped
        rejected = [e for e in sim.events if e["kind"] == "squad_change_rejected"]
        self.assertEqual([e["reasons"] for e in rejected], [["airspace hold in force"]])
        self.assertEqual(sim.validator.log[-1]["kind"], "squads")
        centres = [p.centre()[:2] for p in plan.parts]
        # The move at 15 s re-plans the squads while they climb back: their altitude keeps ramping, never jumps.
        z = [[p.centre()[2] for p in plan.parts]]
        while sim.t < 20.0 - 1e-9:
            sim.step()
            z.append([p.centre()[2] for p in plan.parts])
        self.assertIn("squad_changed", [e["kind"] for e in sim.events if e["t"] == 15.0])
        self.assertLessEqual(np.abs(np.diff(np.asarray(z), axis=0)).max(), ALT_RATE_MPS * DT + 1e-9)
        sim.run()
        self.assertTrue(all(np.linalg.norm(p.centre()[:2] - c) > 1.0 for p, c in zip(plan.parts, centres)))
        self.assertTrue(mover not in plan.transit or not np.array_equal(plan.transit[mover]["pos"][:2], xy0))
        m = sim.result().metrics
        self.assertEqual(m["collisions"] + m["obstacle_crashes"] + m["ground_crashes"], 0)
        self.assertIsNotNone(m["airspace_holds"][0]["clear_s"])


class DashboardTest(unittest.TestCase):
    def test_the_inject_route_takes_aircraft_orders(self):
        from http.server import ThreadingHTTPServer
        from halo_systems.dashboard.server import State, make_handler
        tmp = tempfile.TemporaryDirectory()
        state = State(Path(tmp.name), Path(tmp.name), None, None)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def post(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=10).read())
        try:
            run = post("/api/runs", {"scenario": "open_transit", "n": 4, "config": "improved+fixed", "seed": 1,
                                     "speed": 10})
            info = post(f"/api/runs/{run['id']}/inject", {"kind": "aircraft", "op": "hold"})
            self.assertEqual((info["kind"], info["op"]), ("aircraft", "hold"))
            with self.assertRaises(urllib.error.HTTPError) as err:
                post(f"/api/runs/{run['id']}/inject", {"kind": "aircraft", "op": "hold", "altitude_m": "low"})
            self.assertEqual(err.exception.code, 400)
            sim = state.runs[run["id"]].sim
            t0 = time.time()
            while not sim.holds and time.time() - t0 < 10:
                time.sleep(0.05)
            self.assertEqual(sim.holds[0]["altitude_m"], 10.0)
            post(f"/api/runs/{run['id']}/control", {"stop": True})
        finally:
            httpd.shutdown()
            httpd.server_close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
