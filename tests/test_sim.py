"""Physics, world, scenarios, engine determinism and metrics."""

import dataclasses
import itertools
import math
import unittest

import numpy as np

from swarmlab.control.assignment import hungarian
from swarmlab.control.formations import SHAPES, Path, formation_offsets
from swarmlab.control.improved import ImprovedController
from swarmlab.control.original import OriginalController
from swarmlab.eval.metrics import _first_formed
from swarmlab.eval.scenarios import SCENARIOS, make
from swarmlab.sim.body import GRAVITY, DroneBody
from swarmlab.sim.engine import Simulation
from swarmlab.sim.physics import closest_approach, integrate
from swarmlab.sim.world import Box, Cylinder, World


class PhysicsTest(unittest.TestCase):
    def setUp(self):
        self.body = DroneBody()

    def test_hover_thrust_holds_still(self):
        n = 3
        p = np.zeros((n, 3)) + [0, 0, 10]
        v = np.zeros((n, 3))
        hover = np.tile([0, 0, self.body.mass_kg * GRAVITY], (n, 1))
        for _ in range(100):
            p, v, thrust, _ = integrate(p, v, hover, hover, np.zeros((n, 3)), np.ones(n, bool), self.body, 0.02)
        np.testing.assert_allclose(v, 0.0, atol=1e-9)
        np.testing.assert_allclose(p[:, 2], 10.0, atol=1e-9)

    def test_unpowered_drone_falls_and_drag_opposes_motion(self):
        p = np.array([[0.0, 0.0, 50.0]])
        v = np.array([[10.0, 0.0, 0.0]])
        thrust = np.zeros((1, 3))
        p2, v2, _, a = integrate(p, v, thrust, thrust, np.zeros((1, 3)), np.zeros(1, bool), self.body, 0.02)
        self.assertLess(a[0, 2], 0)
        self.assertLess(a[0, 0], 0)  # drag slows the forward motion
        self.assertLess(v2[0, 0], 10.0)

    def test_motor_lag_is_first_order(self):
        cmd = np.array([[0.0, 0.0, 20.0]])
        thrust = np.zeros((1, 3))
        p, v = np.zeros((1, 3)), np.zeros((1, 3))
        _, _, thrust, _ = integrate(p, v, thrust, cmd, np.zeros((1, 3)), np.ones(1, bool), self.body, 0.02)
        self.assertAlmostEqual(thrust[0, 2], 20.0 * 0.02 / self.body.motor_tau_s, places=6)

    def test_power_model(self):
        self.assertAlmostEqual(self.body.hover_endurance_min, 17.0, delta=0.5)
        speeds = np.linspace(1, 20, 50)
        power = self.body.level_power_w(speeds)
        self.assertLess(power.min(), self.body.hover_power_w)  # translational lift lowers power at moderate speed
        self.assertGreater(power[-1], power.min())               # parasitic drag wins at high speed
        econ = self.body.economical_speed_mps()
        self.assertTrue(8.0 < econ < 20.0)

    def test_swept_closest_approach_catches_fast_crossing(self):
        p0 = np.array([[-1.0, 0, 10], [1.0, 0, 10]])
        p1 = np.array([[1.0, 0, 10], [-1.0, 0, 10]])  # the two pass through each other within one step
        d = closest_approach(p0, p1, np.ones(2, bool))
        self.assertLess(d[0, 1], 1e-9)
        self.assertTrue(math.isinf(d[0, 0]))


class AssignmentTest(unittest.TestCase):
    def test_hungarian_matches_brute_force(self):
        rng = np.random.default_rng(1)
        for n, m in [(5, 5), (4, 6), (6, 4)]:
            for _ in range(10):
                cost = rng.uniform(0, 10, (n, m))
                cols = hungarian(cost)
                got = sum(cost[i, c] for i, c in enumerate(cols) if c >= 0)
                if n <= m:
                    best = min(sum(cost[i, c] for i, c in enumerate(perm)) for perm in itertools.permutations(range(m), n))
                else:
                    best = min(sum(cost[r, j] for j, r in enumerate(perm)) for perm in itertools.permutations(range(n), m))
                self.assertAlmostEqual(got, best, places=9)


class GeometryTest(unittest.TestCase):
    def test_formations_are_centred_with_requested_spacing(self):
        for shape in SHAPES:
            off = formation_offsets(shape, 9, 5.0)
            np.testing.assert_allclose(off.mean(axis=0), 0.0, atol=1e-9)
            d = np.linalg.norm(off[:, None] - off[None], axis=2) + np.eye(9) * 1e9
            self.assertGreaterEqual(d.min(), 5.0 - 1e-6, shape)

    def test_rounded_path_keeps_length_close_and_turns_smoothly(self):
        path = Path([(0, 0), (100, 0), (100, 100)], corner_radius=15.0)
        self.assertTrue(190 < path.length < 200)
        headings = [path.heading_at(s) for s in np.linspace(0, path.length, 400)]
        self.assertLess(np.max(np.abs(np.diff(headings))), 0.1)

    def test_corridor_width(self):
        world = World(cylinders=[Cylinder(50, 6, 0.5, 30), Cylinder(60, -5, 0.5, 30), Cylinder(400, 1, 0.5, 30)])
        width = world.free_width_ahead(np.array([0, 0, 10.0]), np.array([1.0, 0, 0]), 100, 10)
        self.assertAlmostEqual(width, (6 - 0.5) + (5 - 0.5), places=6)
        low = World(cylinders=[Cylinder(50, 2, 0.5, 5)])  # shorter than the flight altitude
        self.assertEqual(low.free_width_ahead(np.array([0, 0, 10.0]), np.array([1.0, 0, 0]), 100, 10), 200.0)
        street = World(boxes=[Box((20, 8, 0), (60, 30, 40)), Box((20, -30, 0), (60, -9, 40))])
        self.assertAlmostEqual(street.free_width_ahead(np.array([0, 0, 10.0]), np.array([1.0, 0, 0]), 100, 10), 17.0)

    def test_clearance_inside_box_is_negative(self):
        world = World(boxes=[Box((0, 0, 0), (10, 10, 10))])
        d, away = world.clearance(np.array([[5.0, 1.0, 5.0], [15.0, 5.0, 5.0]]))
        self.assertLess(d[0], 0)
        self.assertAlmostEqual(d[1], 5.0)
        np.testing.assert_allclose(away[1], [1, 0, 0])


class ScenarioTest(unittest.TestCase):
    def test_every_scenario_builds_and_seeds_change_the_layout(self):
        for name in SCENARIOS:
            a, b = make(name, 0), make(name, 1000)
            self.assertEqual(a.name, name)
            self.assertNotEqual(repr(a), repr(b), name)

    def test_overrides(self):
        sc = make("open_transit", 0, n=6, duration_s=5.0)
        self.assertEqual((sc.n, sc.duration_s), (6, 5.0))


def short(name="open_transit", n=6, duration=6.0):
    return make(name, 3, n=n, duration_s=duration)


class EngineTest(unittest.TestCase):
    def run_sim(self, ctrl_cls, seed=3, scenario=None):
        sc = scenario or short()
        body = DroneBody()
        return Simulation(sc, ctrl_cls(body, sc.world), seed=seed, body=body, record_hz=0).run()

    def test_same_seed_same_result(self):
        a = self.run_sim(ImprovedController)
        b = self.run_sim(ImprovedController)
        ma = {k: v for k, v in a.metrics.items() if not k.startswith("ctrl_ms")}
        mb = {k: v for k, v in b.metrics.items() if not k.startswith("ctrl_ms")}
        self.assertEqual(ma, mb)

    def test_paired_streams_give_both_controllers_the_same_wind(self):
        sc = short()
        body = DroneBody()
        s1 = Simulation(sc, OriginalController(body, sc.world), seed=5, body=body, record_hz=0)
        s2 = Simulation(sc, ImprovedController(body, sc.world), seed=5, body=body, record_hz=0)
        for _ in range(50):
            s1.step()
            s2.step()
        np.testing.assert_allclose(s1.wind.at(s1.t), s2.wind.at(s2.t))

    def test_open_transit_is_safe_and_formed(self):
        for cls in (OriginalController, ImprovedController):
            m = self.run_sim(cls).metrics
            self.assertEqual(m["collisions"], 0, cls.name)
            self.assertLess(m["formation_error_steady_m"], 1.0, cls.name)

    def test_motor_failure_is_counted_and_drone_falls(self):
        sc = dataclasses.replace(short(duration=8.0), failures=[__import__("swarmlab.sim.scenario", fromlist=["x"]).MotorFailure(1.0, 2)])
        r = self.run_sim(ImprovedController, scenario=sc)
        self.assertEqual(r.metrics["drones_lost"], 1)
        self.assertTrue(any(e["kind"] == "motor_failure" for e in r.events))

    def test_summary_has_the_fields_supervisors_use(self):
        sc = make("forest_trail", 0)
        body = DroneBody()
        sim = Simulation(sc, ImprovedController(body, sc.world), seed=0, body=body, record_hz=0)
        for _ in range(60):
            sim.step()
        s = sim.summary()
        for key in ("mission", "mission_directive", "current_directive", "environment", "energy", "limits",
                    "formation_widths_at_current_spacing_m"):
            self.assertIn(key, s)
        self.assertIsNotNone(s["environment"]["free_corridor_width_m"])


class MetricsTest(unittest.TestCase):
    def test_first_formed_needs_a_sustained_hold(self):
        t = np.arange(0, 10, 0.1)
        err = np.where(t < 3, 5.0, 0.5)
        err[(t > 4) & (t < 4.5)] = 2.0  # brief break restarts the hold
        self.assertAlmostEqual(_first_formed(t, err, 0.0), 4.5, places=6)
        self.assertIsNone(_first_formed(t, np.full_like(t, 3.0), 0.0))


if __name__ == "__main__":
    unittest.main()
