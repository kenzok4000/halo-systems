"""The wildfire valley: terrain, fire spread, smoke, the station's fixes and forecasts, and the alert check."""

import math
import unittest

import numpy as np

from halo_systems.eval.runner import make_controller, make_supervisor, run_one
from halo_systems.eval.scenarios import make
from halo_systems.sim.body import DroneBody
from halo_systems.sim.engine import Simulation
from halo_systems.sim.fire import FUEL_MODELS, Moisture, Period, SpreadGrid, anderson_check
from halo_systems.sim.scenario import Scenario, SensorConfig, Target
from halo_systems.sim.smoke import SmokeConfig, bearing_fix, cluster, fix_is_sound, plume_top, triangulate
from halo_systems.sim.terrain import FOREST, Terrain, valley
from halo_systems.sim.wildfire import AlertCheck, AlertPolicy, area_from_top, buffered_zone, inside, sorensen


def flat_terrain(extent=(-100.0, -100.0, 100.0, 100.0), cell=5.0, cover=FOREST, slope_x=0.0):
    x0, y0, x1, y1 = extent
    nx, ny = int((x1 - x0) / cell) + 1, int((y1 - y0) / cell) + 1
    xs = x0 + np.arange(nx) * cell
    heights = np.tile(slope_x * (xs - x0), (ny, 1))
    return Terrain(x0, y0, cell, heights, np.full((ny, nx), cover), np.zeros((ny, nx)))


class TerrainTest(unittest.TestCase):
    def test_valley_has_ridges_a_gentle_block_and_blocks_sight(self):
        t = valley(np.random.default_rng(3), (-300.0, -250.0, 600.0, 450.0), block=(0.0, 0.0, 300.0, 200.0))
        xs = np.linspace(10, 290, 15)
        inside_block = t.height(*np.meshgrid(xs, np.linspace(10, 190, 10)))
        self.assertLess(np.ptp(inside_block), 90.0)
        self.assertGreater(t.heights.max(), 120.0)
        h, hx, hy = t.follow(np.array([150.0]), np.array([100.0]))
        self.assertTrue(np.all(np.isfinite([h, hx, hy])))
        self.assertGreaterEqual(float(h[0]), float(t.height(150.0, 100.0)))      # the canopy is never below ground
        # From the valley floor at ground level, the far side of a ridge is out of sight.
        low = np.array([[150.0, 100.0, float(t.height(150.0, 100.0)) + 2.0]])
        top_y = -240.0
        far = np.array([[150.0, top_y, float(t.height(150.0, top_y)) - 30.0]])
        self.assertFalse(bool(t.line_of_sight(low, far)[0]))


class FireTest(unittest.TestCase):
    def test_rothermel_matches_anderson_table(self):
        for model, row in anderson_check().items():
            limit = 0.08 if model in (1, 6, 10) else 0.2       # the valley's three fuels, and the rest
            self.assertLessEqual(abs(row["ratio"] - 1.0), limit, f"fuel model {model}: {row['ratio']:.2f} of Anderson's rate")

    def test_spread_is_faster_downwind_and_upslope(self):
        flat = SpreadGrid(flat_terrain(), 5.0)
        a = flat.arrival([(0.0, 0.0, 0.0)], [Period(-10.0, (4.0, 0.0))], 4 * 3600.0)
        iy, ix = flat.cell_of(25.0, 0.0)
        jy, jx = flat.cell_of(-25.0, 0.0)
        self.assertLess(a[iy, ix], a[jy, jx])
        hill = SpreadGrid(flat_terrain(slope_x=0.3), 5.0)
        b = hill.arrival([(0.0, 0.0, 0.0)], [Period(-10.0, (0.0, 0.0))], 4 * 3600.0)
        self.assertTrue(np.isfinite(b[iy, ix]))
        self.assertLess(b[iy, ix], b[jy, jx])

    def test_later_ignitions_survive_earlier_weather_periods(self):
        # An ignition after the first period ends must still burn (it used to be dropped).
        g = SpreadGrid(flat_terrain(), 5.0)
        periods = [Period(-7200.0, (2.0, 0.0)), Period(-3600.0, (2.0, 0.0)), Period(0.0, (3.0, 0.0))]
        a = g.arrival([(0.0, 0.0, 600.0)], periods, 3600.0)
        iy, ix = g.cell_of(0.0, 0.0)
        self.assertEqual(a[iy, ix], 600.0)
        self.assertGreater(int(np.isfinite(a).sum()), 10)

    def test_scale_speeds_spread_in_proportion(self):
        g = SpreadGrid(flat_terrain(), 5.0)
        w = [Period(-10.0, (3.0, 0.0))]
        a = g.arrival([(0.0, 0.0, 0.0)], w, 7200.0)
        b = g.arrival([(0.0, 0.0, 0.0)], w, 7200.0, scale=2.0, cache=False)
        both = np.isfinite(a) & np.isfinite(b) & (a > 0)
        np.testing.assert_allclose(b[both], a[both] / 2.0, rtol=1e-9)

    def test_drier_fuel_burns_faster(self):
        g = SpreadGrid(flat_terrain(), 5.0)
        wet = g.head_rate(Period(0.0, (3.0, 0.0), Moisture(0.12, 0.13, 0.14, 1.0)), 0.0, 0.0)
        dry = g.head_rate(Period(0.0, (3.0, 0.0), Moisture(0.05, 0.06, 0.07, 1.0)), 0.0, 0.0)
        self.assertGreater(dry, wet)
        self.assertIn(10, FUEL_MODELS)


class SmokeTest(unittest.TestCase):
    def test_plume_size_round_trip(self):
        cfg = SmokeConfig()
        for area in (5.0, 50.0, 500.0, 5000.0):
            self.assertAlmostEqual(area_from_top(float(plume_top(area, cfg)), cfg), area, delta=area * 1e-6)

    def test_triangulation_and_least_squares_fix(self):
        rng = np.random.default_rng(1)
        fire = np.array([300.0, 120.0])
        obs = np.stack([rng.uniform(-100, 100, 12), rng.uniform(-150, 150, 12)], axis=1)
        bearings = np.arctan2(fire[1] - obs[:, 1], fire[0] - obs[:, 0]) + np.radians(2.0) * rng.normal(size=12)
        lines = np.column_stack([obs, bearings, np.arange(12)])
        crossings = triangulate(lines[:, :3])
        found = cluster(crossings, radius_m=40.0, min_points=4)
        self.assertTrue(found)
        fix = bearing_fix(lines, found[0][0], 2.0)
        self.assertLess(math.dist((fix["x"], fix["y"]), fire), 25.0)
        self.assertGreaterEqual(fix["drones"], 6)
        self.assertLess(fix["rms_sigma"], 1.5)

    def test_a_crossing_that_rests_on_one_drone_is_rejected(self):
        # Five drones in a row look at one plume; a sixth drone's bearing to another plume crosses their lines.
        plume = np.array([0.0, -300.0])
        obs = np.array([[x, 0.0] for x in (0.0, 14.0, 28.0, 42.0, 56.0)])
        b = np.arctan2(plume[1] - obs[:, 1], plume[0] - obs[:, 0])
        ghost = np.array([28.0, -100.0])
        other = np.array([[-200.0, -100.0]])
        b_other = np.arctan2(ghost[1] - other[0, 1], ghost[0] - other[0, 0])
        lines = np.column_stack([np.vstack([obs, other]), np.append(b, b_other), np.arange(6)])
        self.assertFalse(fix_is_sound(bearing_fix(lines, ghost, 2.0)))
        # The same five drones plus two more, all looking at the plume, do make a sound fix.
        more = np.array([[-150.0, -50.0], [150.0, -60.0]])
        b_more = np.arctan2(plume[1] - more[:, 1], plume[0] - more[:, 0])
        real = np.column_stack([np.vstack([obs, more]), np.append(b, b_more), np.arange(7)])
        fix = bearing_fix(real, plume + 5.0, 2.0)
        self.assertTrue(fix_is_sound(fix, min_lines=6))
        self.assertLess(math.dist((fix["x"], fix["y"]), plume), 5.0)


class AlertCheckTest(unittest.TestCase):
    def setUp(self):
        self.policy = AlertPolicy()
        self.phones = np.array([[0.0, 0.0], [10.0, 5.0], [500.0, 500.0]])
        zone = buffered_zone(self.phones[:2], 100.0)
        self.alert = {"id": 0, "t": 100.0, "level": 4, "zone": "F4", "forecast_id": 3, "hazard": "wildfire",
                      "area": "Valley floor 4 (Cedar Flat homes)", "action": "Leave now.",
                      "authority": "Valley Fire District", "channel": "cell broadcast (simulated)",
                      "expires_in_s": 6 * 3600, "expires": "in 6 hours", "polygon": zone.tolist(),
                      "phones": [int(i) for i in np.nonzero(inside(zone, self.phones))[0]], "max_prob": 0.8,
                      "message": "Valley Fire District: Level 4 EVACUATE NOW. Wildfire near zone F4. Leave now."}
        self.forecast = {"id": 3}

    def test_a_good_alert_passes_and_its_zone_holds_its_phones(self):
        self.assertEqual(self.alert["phones"], [0, 1])
        self.assertTrue(AlertCheck(self.policy).check(self.alert, self.forecast, []).ok)

    def test_bad_alerts_are_rejected_with_reasons(self):
        check = AlertCheck(self.policy)
        cases = {
            "message": {"message": "x" * 400},
            "current forecast": {"forecast_id": 2},
            "threshold": {"max_prob": 0.1},
            "expiry": {"expires_in_s": 60},
            "recipients": {"phones": []},
            "simulated": {"channel": "cell broadcast"},
            "level must": {"level": 2},
        }
        for word, change in cases.items():
            result = check.check({**self.alert, **change}, self.forecast, [])
            self.assertFalse(result.ok, word)
            self.assertTrue(any(word in r for r in result.reasons), (word, result.reasons))
        dup = check.check({**self.alert, "t": 300.0}, self.forecast, [{**self.alert, "id": 7}])
        self.assertFalse(dup.ok)
        # Automatic alerts only raise a zone's level; Level 5 needs even odds within 15 minutes.
        self.assertFalse(check.check(self.alert, self.forecast, [], zone_level=4).ok)
        self.assertTrue(check.check({**self.alert, "level": 5, "max_prob": 0.6}, self.forecast, [], zone_level=4).ok)
        self.assertFalse(check.check({**self.alert, "level": 5, "max_prob": 0.4}, self.forecast, [], zone_level=4).ok)

    def test_sorensen(self):
        a = np.array([1, 1, 0, 0], dtype=bool)
        self.assertEqual(sorensen(a, a), 1.0)
        self.assertEqual(sorensen(a, ~a), 0.0)
        self.assertIsNone(sorensen(np.zeros(4, bool), np.zeros(4, bool)))


class CoverTest(unittest.TestCase):
    def test_a_target_under_a_rock_shelf_is_never_seen_from_above(self):
        def scenario(cover):
            return Scenario(name="t", title="t", description="t", n=2, duration_s=30.0,
                            waypoints=[(0.0, 0.0), (150.0, 0.0)],
                            targets=[Target(60.0, 0.0, 2.0, cover=cover)],
                            sensor=SensorConfig(fov_deg=45.0, dwell_s=0.5, area=(0.0, -20.0, 150.0, 20.0)))
        found = {}
        for cover in (0.0, 0.7, 1.0):
            sc = scenario(cover)
            body = DroneBody()
            sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=1, body=body,
                             record_hz=0)
            sim.run()
            found[cover] = sim.target_found_t[0]
        self.assertFalse(math.isnan(found[0.0]))
        self.assertGreaterEqual(found[0.7], found[0.0])
        self.assertTrue(math.isnan(found[1.0]))


class ValleyFlightTest(unittest.TestCase):
    def test_short_valley_flight_finds_fires_forecasts_and_checks_alerts(self):
        row, res = run_one("wildfire_valley", 2001, "improved+rule", n=16, keep_result=True, duration_s=60.0)
        self.assertEqual(row["ground_crashes"], 0)
        self.assertEqual(row["collisions"], 0)
        self.assertGreaterEqual(row["fires_located"], 1)
        self.assertGreaterEqual(row["forecasts"], 1)
        kinds = {e["kind"] for e in res.events}
        self.assertIn("smoke_sighted", kinds)
        self.assertIn("forecast", kinds)
        checks = [e for e in res.events if e["kind"] == "alert_check"]
        drafted = [e for e in res.events if e["kind"] == "alert_drafted"]
        self.assertEqual(len(checks), len(drafted))
        for e in drafted:
            self.assertLessEqual(len(e["message"]), 360)
        forecast = next(e for e in res.events if e["kind"] == "forecast")
        g = forecast["grid"]
        self.assertEqual(len(forecast["p50_arrival_s"]), g["nx"] * g["ny"])
        self.assertTrue(forecast["brief"])
        self.assertIn("terrain", res.world)

    def test_thermal_map_scoring_and_event_rate(self):
        sc = make("wildfire_valley", 2001, n=16)
        sc.duration_s = 120.0
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=2001, body=body,
                         record_hz=0)
        res = sim.run()
        wf = sim.wildfire
        # The cameras map burning ground, and what they map is burning (a cell's slip from navigation error aside).
        mapped = np.isfinite(wf.mapped_t)
        self.assertGreater(int(mapped.sum()), 0)
        burnt = np.zeros_like(mapped)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                burnt |= np.roll(np.roll(wf.arrival10 <= wf.mapped_t, dy, axis=0), dx, axis=1)
        self.assertGreaterEqual(float(np.mean(burnt[mapped])), 0.95)
        self.assertGreater(res.metrics["mapped_ha"], 0.0)
        # A point on a spreading fire's burning ground counts as that fire, however far from where it started.
        i = int(np.argmin([f.t_active_s for f in wf.fires]))
        cells = np.flatnonzero((wf.fire_arrival[i] <= sim.t).ravel())
        far = cells[np.argmax(np.hypot(wf._gx[cells] - wf.fires[i].x, wf._gy[cells] - wf.fires[i].y))]
        truth, err = wf._match_truth(float(wf._gx[far]), float(wf._gy[far]), sim.t)
        self.assertEqual(truth, i)
        self.assertLess(err, 1.0)
        # Located fires keep unique ids through merges; smoke events are rate-limited per drone.
        ids = [l["id"] for l in wf.located + wf.merged]
        self.assertEqual(len(ids), len(set(ids)))
        last = {}
        for e in res.events:
            if e["kind"] == "smoke_sighted":
                self.assertGreaterEqual(e["t"] - last.get(e["drone"], -1e9), 10.0 - 1e-6)
                last[e["drone"]] = e["t"]
        for e in res.events:
            if e["kind"] == "alert_drafted":
                self.assertNotIn("about 0 min", e["message"])
                self.assertNotIn("about -", e["message"])
                self.assertIn("Simulated", e["message"])
        # Alerts go out automatically by zone, each one checked, with levels that only rise.
        sent = [a for a in wf.alerts if a["state"] == "delivered"]
        self.assertTrue(sent)
        for a in sent:
            self.assertTrue(a["check"]["ok"])
            self.assertEqual(a["approved_by"], "automatic (fixed rules)")
            self.assertGreater(a["level"], a["raised_from"])
            k = [z["code"] for z in wf.zones].index(a["zone"])
            self.assertEqual(set(a["phones"]), set(np.nonzero(wf.zone_of_phone == k)[0].tolist()))
        # An operator can withdraw an alert and clear a zone; both reach the zone's phones and are checked.
        a = sent[0]
        out = wf.cancel(sim, a["id"])
        self.assertTrue(out["ok"])
        self.assertEqual(a["state"], "cancelled")
        k = [z["code"] for z in wf.zones].index(a["zone"])
        self.assertEqual(wf.zone_level[k], 0)
        self.assertFalse(wf.all_clear(sim, a["zone"])["ok"])                 # nothing left in force there
        other = next((b for b in sent if b["zone"] != a["zone"]), None)
        if other is not None:
            self.assertTrue(wf.all_clear(sim, other["zone"])["ok"])
        self.assertTrue(all(n["check"]["ok"] for n in wf.notices))
        self.assertIn("zones", wf.hello())

    def test_distance_to_a_plume_line(self):
        from halo_systems.sim.wildfire import _to_segment
        self.assertAlmostEqual(_to_segment(5.0, 3.0, 0.0, 0.0, 10.0, 0.0), 3.0)
        self.assertAlmostEqual(_to_segment(-4.0, 3.0, 0.0, 0.0, 10.0, 0.0), 5.0)
        self.assertAlmostEqual(_to_segment(1.0, 1.0, 0.0, 0.0, 0.0, 0.0), math.sqrt(2.0))

    def test_dashboard_hello_carries_the_valley_and_approvals_are_checked(self):
        from halo_systems.dashboard.server import LiveRun
        sc = make("wildfire_valley", 2001, n=4)
        run = LiveRun(sc, "improved+rule", 2001)
        h = run.hello()
        truth = h["wildfire"]["fire_truth"]
        self.assertEqual(len(truth["arrival_s"]), truth["nx"] * truth["ny"])
        cover = h["world"]["landcover"]
        self.assertEqual(len(cover["canopy_m"]), cover["nx"] * cover["ny"])
        self.assertEqual(len(h["scenario"]["wildfire"]["phones"]), len(sc.wildfire.phones))
        self.assertFalse(run.sim.wildfire.approve(run.sim, 0)["ok"])          # nothing drafted yet

    def test_valley_scenario_is_seeded(self):
        a, b = make("wildfire_valley", 2000), make("wildfire_valley", 2000)
        self.assertEqual([f.x for f in a.wildfire.fires], [f.x for f in b.wildfire.fires])
        c = make("wildfire_valley", 2001)
        self.assertNotEqual([f.x for f in a.wildfire.fires], [f.x for f in c.wildfire.fires])
        shelters = [f.shelter for f in a.wildfire.fires]
        self.assertIn("canopy", shelters)
        self.assertIn("overhang", shelters)
        self.assertEqual(len(a.wildfire.overhangs), 1)


if __name__ == "__main__":
    unittest.main()
