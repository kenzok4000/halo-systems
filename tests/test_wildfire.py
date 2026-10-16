"""The wildfire valley: terrain, fire spread, smoke, the station's fixes and forecasts, and the alert check."""

import math
import unittest

import numpy as np

from halo_systems.eval.runner import make_controller, make_supervisor, run_one
from halo_systems.eval.scenarios import make, wildfire_valley
from halo_systems.sim.body import DroneBody
from halo_systems.sim.detection import COMBUSTION_KG_PER_MJ, FirePower, TodayConfig, fuel_load, today_methods
from halo_systems.sim.engine import Simulation
from halo_systems.sim.fire import FUEL_MODELS, Moisture, Period, SpreadGrid, anderson_check, rothermel, wind_slope_factors
from halo_systems.sim.scenario import Scenario, SensorConfig, Target
from halo_systems.sim.smoke import SmokeConfig, bearing_fix, cluster, fix_is_sound, plume_top, triangulate
from halo_systems.sim.terrain import FOREST, GRASS, Terrain, valley
from halo_systems.sim.wildfire import (AlertCheck, AlertPolicy, angle_diff, area_from_top, bearing_deg, buffered_zone,
                                       compass_of, inside, mean_bearing, sorensen)


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

    def test_wind_and_slope_factors_give_the_grid_head_rate(self):
        # Wind blowing up a uniform slope: the head rate is r0 (1 + phi_w + phi_s), as the grid computes it.
        hill = SpreadGrid(flat_terrain(cover=GRASS, slope_x=0.2), 5.0)
        dry = Moisture()
        phi_w, phi_s = wind_slope_factors(FUEL_MODELS[1], dry, 3.0, 0.2)
        r0 = rothermel(FUEL_MODELS[1], dry)["r0_ft_min"] * 0.3048 / 60.0
        self.assertAlmostEqual(hill.head_rate(Period(0.0, (3.0, 0.0), dry), 0.0, 0.0), r0 * (1.0 + phi_w + phi_s), places=9)
        self.assertEqual(wind_slope_factors(FUEL_MODELS[1], dry, 0.0, 0.2)[0], 0.0)
        self.assertEqual(wind_slope_factors(FUEL_MODELS[1], dry, 3.0, 0.0)[1], 0.0)
        self.assertGreater(wind_slope_factors(FUEL_MODELS[1], dry, 5.0, 0.0)[0], phi_w)

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
        self.assertIn("duplicate of alert 8", dup.reasons[0])              # numbered from 1, as the page shows
        # Automatic alerts only raise a zone's level; Level 5 needs even odds within 15 minutes.
        self.assertFalse(check.check(self.alert, self.forecast, [], zone_level=4).ok)
        self.assertTrue(check.check({**self.alert, "level": 5, "max_prob": 0.6}, self.forecast, [], zone_level=4).ok)
        self.assertFalse(check.check({**self.alert, "level": 5, "max_prob": 0.4}, self.forecast, [], zone_level=4).ok)

    def test_bearings(self):
        self.assertAlmostEqual(bearing_deg(0.0, 1.0), 0.0)
        self.assertAlmostEqual(bearing_deg(1.0, 0.0), 90.0)
        self.assertAlmostEqual(bearing_deg(-1.0, -1.0), 225.0)
        self.assertAlmostEqual(float(angle_diff(350.0, 10.0)), 20.0)
        self.assertAlmostEqual(float(angle_diff(90.0, 270.0)), 180.0)
        self.assertAlmostEqual(mean_bearing([350.0, 10.0]) % 360.0, 0.0, places=6)
        self.assertEqual(compass_of(45.0), "northeast")
        self.assertEqual(compass_of(180.0), "south")

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


class TodayMethodsTest(unittest.TestCase):
    def test_fire_power_follows_the_fuel_burnt(self):
        arrival = np.array([[0.0, 60.0, 120.0], [180.0, np.inf, 240.0]])
        power = FirePower(arrival, np.full(arrival.shape, 1.5), 100.0, 300.0)
        # by t = 240 s five cells of 100 m^2 at 1.5 kg/m^2 have burnt, all within the last 300 s
        self.assertAlmostEqual(float(power.frp_mw(240.0)), 5 * 150.0 / 300.0 / COMBUSTION_KG_PER_MJ)
        self.assertAlmostEqual(float(power.frp_mw(-1.0)), 0.0)
        self.assertAlmostEqual(float(power.frp_mw(1000.0)), 0.0)          # nothing burns in the last 5 min

    def test_rules_are_seeded_and_never_see_a_fire_before_it_spreads(self):
        sc = make("wildfire_valley", 2001, n=1)
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=2001,
                         body=body, record_hz=0)
        wf, cfg = sim.wildfire, TodayConfig()
        a, b = today_methods(wf, 2001), today_methods(wf, 2001)
        self.assertEqual(a, b)
        self.assertNotEqual(a, today_methods(wf, 2002))                 # the random parts follow the seed
        load = fuel_load(wf)
        for i, (f, row) in enumerate(zip(wf.fires, a)):
            for m, v in row.items():
                if v["t"] is None:
                    continue
                self.assertGreaterEqual(v["t"], f.t_active_s)
                self.assertLessEqual(v["t"], wf.cfg.horizon_s)
                self.assertGreater(v["area_m2"], f.smoulder_area_m2)
            if f.t_active_s + cfg.camera_delay_s <= wf.cfg.horizon_s:
                self.assertAlmostEqual(row["camera"]["t"], round(f.t_active_s + cfg.camera_delay_s, 1))
            if row["geostationary"]["t"] is not None:
                scan = row["geostationary"]["t"] - cfg.geo_latency_s
                power = FirePower(wf.fire_arrival[i], load, wf.grid.cell_m ** 2, cfg.frp_window_s)
                self.assertGreaterEqual(float(power.frp_mw(scan)), cfg.geo_frp_mw)


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
        self.assertEqual(len(row["today"]), len(row["fire_table"]))
        self.assertEqual(set(row["today"][0]), {"geostationary", "polar", "camera", "public", "camera_deployed"})
        # Each brief says which way the head runs and why, and every fire reaches the crews, first as a new fire.
        for b in forecast["brief"]:
            if b["head_bearing_deg"] is None:
                self.assertIn("not expected to", b["text"])
                continue
            self.assertTrue(0.0 <= b["head_bearing_deg"] < 360.0)
            self.assertGreaterEqual(b["cone_deg"], 0.0)
            self.assertEqual(b["direction"], compass_of(b["head_bearing_deg"]))
            self.assertIn("driven by", b["text"])
            self.assertIn(b["drivers"]["main"], ("wind", "slope", "wind and slope", "neither"))
            self.assertAlmostEqual(sum(f["share"] for f in b["drivers"]["fuel_ahead"]), 1.0, delta=0.03)
            self.assertAlmostEqual(sum(b["drivers"]["shares"].values()), 1.0, delta=0.02)
            self.assertEqual([p[0] for p in b["head_track"]], sorted(p[0] for p in b["head_track"]))
            self.assertTrue(set(p[0] for p in b["head_track"]) <= {15, 30, 60, 120})
        crew = [e for e in res.events if e["kind"] == "crew_update"]
        self.assertEqual(row["crew_updates"], len(crew))
        firsts = {}
        for e in crew:
            firsts.setdefault(e["fire"], e)
        self.assertEqual(set(firsts), {b["fire"] for e in res.events if e["kind"] == "forecast" for b in e["brief"]})
        self.assertTrue(all(e["reason"] == "new fire" for e in firsts.values()))
        for r in row["direction_table"]:
            for key in ("err_deg", "wind_only_err_deg", "slope_only_err_deg"):
                self.assertTrue(r[key] is None or 0.0 <= r[key] <= 180.0)
        self.assertEqual(row["direction_n"], len(row["direction_table"]))

    def test_crews_hear_of_changes_beyond_what_they_were_told(self):
        sc = make("wildfire_valley", 2001, n=4)
        body = DroneBody()
        wf = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=2001, body=body,
                        record_hz=0).wildfire

        class Radio:
            def __init__(self, t):
                self.t = t
                self.events = []

            def event(self, kind, drone=None, **detail):
                self.events.append(dict(detail, kind=kind))

        hut = [{"name": "Hut", "eta_min": 60}]
        track = [[15, 5.0, 6.0], [60, 7.0, 8.0]]
        both = [{"name": "Hut", "eta_min": 40}, {"name": "Bridge", "eta_min": 90}]

        def brief(**over):
            b = {"fire": 7, "state": "spreading", "head_bearing_deg": 90.0, "cone_deg": 10.0, "head_m_per_min": 2.0,
                 "direction": "east", "places": hut, "head_from_xy": [1.0, 2.0], "head_xy": [3.0, 4.0],
                 "head_track": track, "headline": "h", "text": "t"}
            b.update(over)
            return b

        def relay(t=100.0, briefs=None, **over):
            radio = Radio(t)
            wf._relay_to_crews(radio, {"id": 0, "brief": [brief(**over)] if briefs is None else briefs})
            self.events = radio.events
            return [e["reason"] for e in radio.events]

        self.assertEqual(relay(), ["new fire"])
        e = self.events[0]
        self.assertEqual((e["kind"], e["fire"], e["forecast"], e["to"]), ("crew_update", 7, 0, wf.cfg.crew.channel))
        for k in ("head_bearing_deg", "cone_deg", "head_m_per_min", "head_from_xy", "head_xy", "head_track", "places",
                  "headline", "text"):
            self.assertEqual(e[k], brief()[k])
        self.assertEqual(relay(), [])                                                 # nothing new
        self.assertEqual(relay(head_bearing_deg=115.0, direction="east-southeast"), [])     # under 30 degrees
        self.assertEqual(relay(head_bearing_deg=130.0, direction="southeast"), ["head turned southeast"])
        self.assertEqual(relay(head_bearing_deg=130.0, head_m_per_min=2.4), [])      # under 0.5 m/min
        self.assertEqual(relay(head_bearing_deg=130.0, head_m_per_min=2.9), [])      # under 1.5 times
        self.assertEqual(relay(head_bearing_deg=130.0, head_m_per_min=4.0), ["head faster"])
        self.assertEqual(relay(head_bearing_deg=130.0, head_m_per_min=4.0, cone_deg=60.0, places=both),
                         ["now reaches Bridge; reaches Hut sooner"])
        # The crews were told the head could be 60 degrees either way, so a 50 degree turn is not news.
        self.assertEqual(relay(head_bearing_deg=180.0, head_m_per_min=4.0, cone_deg=60.0, places=both), [])
        # Half an hour on, a place 30 minutes nearer by the brief's own clock is no sooner.
        later = [{"name": "Hut", "eta_min": 10}, {"name": "Bridge", "eta_min": 60}]
        self.assertEqual(relay(t=1900.0, head_bearing_deg=130.0, head_m_per_min=4.0, cone_deg=60.0, places=later), [])
        self.assertEqual(relay(t=100.0, head_bearing_deg=130.0, head_m_per_min=4.0, cone_deg=60.0, places=later),
                         ["reaches Hut, Bridge sooner"])
        # Direction lost and found; a turn while it is uncertain is not reported.
        self.assertEqual(relay(head_bearing_deg=130.0, head_m_per_min=4.0, cone_deg=95.0, places=later),
                         ["direction now uncertain"])
        self.assertEqual(relay(head_bearing_deg=300.0, head_m_per_min=4.0, cone_deg=95.0, places=later), [])
        self.assertEqual(relay(head_bearing_deg=300.0, direction="west-northwest", head_m_per_min=4.0, cone_deg=40.0,
                               places=later), ["direction now clear: west-northwest"])
        self.assertEqual(relay(state="smouldering", head_bearing_deg=300.0, head_m_per_min=4.0, cone_deg=40.0,
                               places=later), ["now smouldering"])
        self.assertEqual(relay(head_bearing_deg=None, head_m_per_min=0.0, cone_deg=None, places=later),
                         ["now spreading; head no longer advancing; head slower"])
        self.assertEqual(relay(head_m_per_min=0.3, places=later), ["head now advancing"])     # under 0.5 m/min
        # A smouldering fire's head is what it would do if it took off, and the reasons say so.
        self.assertEqual(relay(state="smouldering", head_m_per_min=0.3, places=later), ["now smouldering"])
        self.assertEqual(relay(state="smouldering", head_bearing_deg=None, head_m_per_min=0.0, cone_deg=None,
                               places=later), ["would no longer spread if it takes off"])
        more = later + [{"name": "Ford", "eta_min": 100}]
        self.assertEqual(relay(state="smouldering", head_m_per_min=2.0, direction="east", places=more),
                         ["would now spread if it takes off; would spread faster if it takes off; "
                          "would reach Ford if it takes off"])
        self.assertEqual(relay(state="smouldering", head_bearing_deg=200.0, head_m_per_min=2.0,
                               direction="south-southwest", places=more),
                         ["would run south-southwest if it takes off"])
        # A fire folded into another is withdrawn, once; one that drops out otherwise is no longer tracked.
        wf.merged.append({"id": 7, "merged_into": 2})
        try:
            self.assertEqual(relay(briefs=[]), ["merged into Fire 3"])
            self.assertEqual(self.events[0]["head_bearing_deg"], None)
            self.assertEqual(relay(briefs=[]), [])
        finally:
            wf.merged.pop()
        self.assertEqual(relay(), ["new fire"])
        self.assertEqual(relay(briefs=[]), ["no longer tracked"])

    def test_drivers_add_wind_and_slope_as_vectors(self):
        sc = make("wildfire_valley", 2001, n=4)
        body = DroneBody()
        wf = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=2001, body=body,
                        record_hz=0).wildfire
        g = wf.grid
        tan = np.hypot(g.slope_x, g.slope_y)
        iy, ix = np.unravel_index(int(np.argmax(np.where(g.fuel > 0, tan, 0.0))), tan.shape)
        sx, sy, t = float(g.slope_x[iy, ix]), float(g.slope_y[iy, ix]), float(tan[iy, ix])
        self.assertGreater(t, 0.1)
        loc = {"x": float(g.X[iy, ix]), "y": float(g.Y[iy, ix])}
        moist = Moisture()
        fm = FUEL_MODELS[int(g.fuel[iy, ix])]
        # A wind strong enough to beat the slope by about four to one.
        _, phi_s = wind_slope_factors(fm, moist, 0.0, t)
        speed = next(v for v in np.arange(0.2, 20.0, 0.1) if wind_slope_factors(fm, moist, v, t)[0] >= 4.0 * phi_s)
        none = np.zeros(g.X.shape)
        for sign, want_against in ((1.0, None), (-1.0, "slope")):       # up the slope, then down it
            wind = (sign * speed * sx / t, sign * speed * sy / t)
            d = wf._drivers(loc, none, wind, moist)
            pw, ps = wind_slope_factors(fm, moist, speed, t)
            phi_e = pw + sign * ps
            self.assertEqual(d["main"], "wind")
            self.assertEqual(d["against"], want_against)
            self.assertAlmostEqual(d["shares"]["fuel"], 1.0 / (1.0 + phi_e), delta=0.006)
            self.assertAlmostEqual(d["shares"]["slope"], sign * ps / (1.0 + phi_e), delta=0.006)
            self.assertEqual("working against it" in d["why"], want_against is not None)
        # Across the slope the two add as vectors, not as numbers.
        d = wf._drivers(loc, none, (speed * sy / t, -speed * sx / t), moist)
        self.assertAlmostEqual(d["shares"]["fuel"], 1.0 / (1.0 + math.hypot(pw, ps)), delta=0.006)
        self.assertIsNone(d["against"])

    def test_direction_helpers(self):
        from halo_systems.sim.wildfire import _furthest, bearing_deg, front_owner, modal_bearing
        # Compass bearings stay in [0, 360), even for a hair west of north.
        self.assertEqual(bearing_deg(0.0, 1.0), 0.0)
        self.assertAlmostEqual(bearing_deg(1.0, 0.0), 90.0)
        self.assertEqual(bearing_deg(-1e-17, 1.0), 0.0)          # atan2 gives -0.0...; % 360 alone rounds to 360
        # The mode of two groups, not the empty direction between them.
        self.assertAlmostEqual(modal_bearing([80, 90, 100, 270]), 90.0, places=6)
        self.assertAlmostEqual(modal_bearing([350, 0, 10, 180]) % 360.0, 0.0, places=6)
        # A symmetric tie of furthest cells averages to a straight-ahead bearing, not one of the pair.
        X, Y = np.meshgrid(np.arange(-3, 4) * 10.0, np.arange(0, 7) * 10.0)
        dist = np.where((Y == 30.0) & (np.abs(X) == 10.0), 31.6, 5.0)
        owned = (np.zeros(X.shape, dtype=int), dist, np.zeros(X.shape), np.zeros(X.shape))
        mask = dist > 0
        _, _, _, _, d, b = _furthest(X, Y, owned, mask)
        self.assertAlmostEqual(d, 31.6)
        self.assertAlmostEqual(b, 0.0, places=6)
        # Two heads of the same length, east and west: one of them, not north or south between them; the larger
        # group of cells wins.
        dist = np.where((Y == 30.0) & (np.abs(X) == 30.0), 42.4, 5.0)
        dist[(Y == 20.0) & (X == 30.0)] = 40.0
        owned = (np.zeros(X.shape, dtype=int), dist, np.zeros(X.shape), np.zeros(X.shape))
        _, _, hx, _, d, b = _furthest(X, Y, owned, dist > 0)
        self.assertAlmostEqual(d, 42.4)
        self.assertGreater(hx, 0.0)
        self.assertLess(angle_diff(b, 45.0), 15.0)
        # A patch of burning ground with no located fire within join_m belongs to none.
        X, Y = np.meshgrid(np.arange(0, 30) * 10.0, np.arange(0, 3) * 10.0)
        arrival = np.full(X.shape, np.inf)
        arrival[1, 1] = arrival[1, 25] = 0.0
        own = front_owner(X, Y, arrival, 0.0, [(10.0, 10.0, 0.0)], arrival <= 60.0, 60.0)
        self.assertEqual(int(own[0][1, 1]), 0)
        self.assertEqual(int(own[0][1, 25]), -1)

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
        # Alerts go out automatically by zone, each one checked, with levels that only rise; a raise replaces the
        # zone's earlier alert.
        sent = [a for a in wf.alerts if a["state"] in ("delivered", "superseded")]
        self.assertTrue(sent)
        codes = [z["code"] for z in wf.zones]
        for a in sent:
            self.assertTrue(a["check"]["ok"])
            self.assertEqual(a["approved_by"], "automatic (fixed rules)")
            self.assertGreater(a["level"], a["raised_from"])
            k = codes.index(a["zone"])
            self.assertEqual(set(a["phones"]), set(np.nonzero(wf.zone_of_phone == k)[0].tolist()))
            if a["state"] == "superseded":
                newer = wf.alerts[a["superseded_by"]]
                self.assertEqual(newer["zone"], a["zone"])
                self.assertGreater(newer["level"], a["level"])
        for k, code in enumerate(codes):
            live = [a["level"] for a in sent if a["zone"] == code and a["state"] == "delivered"]
            self.assertLessEqual(len(live), 1)
            self.assertEqual(int(wf.zone_level[k]), live[0] if live else 0)
        n_alerts = len(wf.alerts)
        # A replaced alert cannot be cancelled on its own; cancelling the one in force withdraws the zone's alerts,
        # tells its phones once, and holds the zone at that level (Level 4 at least).
        old = next((a for a in sent if a["state"] == "superseded"), None)
        self.assertIsNotNone(old, "seed 2001 raises a zone within 2 minutes")
        refused = wf.cancel(sim, old["id"])
        self.assertFalse(refused["ok"])
        self.assertIn("newer alert", refused["reason"])
        live = wf.alerts[old["superseded_by"]]
        k = codes.index(live["zone"])
        held = max(4, int(wf.zone_level[k]))
        self.assertTrue(wf.cancel(sim, live["id"])["ok"])
        self.assertTrue(all(a["state"] == "cancelled" for a in wf.alerts if a["zone"] == live["zone"]))
        self.assertEqual(wf.zone_level[k], 0)
        self.assertEqual(sum(1 for n in wf.notices if n["zone"] == live["zone"]), 1)
        self.assertFalse(wf.all_clear(sim, live["zone"])["ok"])              # nothing left in force there
        self.assertFalse(wf.cancel(sim, live["id"])["ok"])
        # All clear closes a zone: its alerts cannot be cancelled afterwards, and the next forecast does not draft
        # (or reject) a new alert there unless the fire gets worse than the level it had.
        other = next((a for a in wf.alerts if a["state"] == "delivered"), None)
        if other is None:
            before = len(wf.alerts)
            wf._draft_alerts(sim, wf.forecasts[-1])
            self.assertFalse(any(a["zone"] == live["zone"] and a["level"] <= held for a in wf.alerts[before:]))
        else:
            k2 = codes.index(other["zone"])
            had = int(wf.zone_level[k2])
            self.assertTrue(wf.all_clear(sim, other["zone"])["ok"])
            self.assertEqual(other["state"], "cleared")
            self.assertFalse(wf.cancel(sim, other["id"])["ok"])
            before = len(wf.alerts)
            wf._draft_alerts(sim, wf.forecasts[-1])
            for a in wf.alerts[before:]:
                if a["zone"] in (other["zone"], live["zone"]):
                    self.assertGreater(a["level"], had if a["zone"] == other["zone"] else held)
                    self.assertEqual(a["state"], "delivered")
        self.assertTrue(all(n["check"]["ok"] for n in wf.notices))
        self.assertGreaterEqual(len(wf.alerts), n_alerts)
        self.assertEqual(res.metrics["alerts_delivered"], len(sent))       # scored as sent, whatever came after
        self.assertIn("zones", wf.hello())

    def test_manual_alerts_raise_a_zone_only_once_approved(self):
        sc = wildfire_valley(2001, n=16, approval="manual")
        sc.duration_s = 60.0
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=2001, body=body,
                         record_hz=0)
        sim.run()
        wf = sim.wildfire
        codes = [z["code"] for z in wf.zones]
        waiting = [a for a in wf.alerts if a["state"] == "checked"]
        self.assertTrue(waiting)
        self.assertTrue(np.all(wf.zone_level == 0))                         # nothing sent, nothing raised
        self.assertFalse(any(a["state"] == "rejected" for a in wf.alerts))  # no duplicate drafts while waiting
        for code in {a["zone"] for a in waiting}:
            self.assertEqual(sum(1 for a in waiting if a["zone"] == code), 1)
        a = waiting[0]
        self.assertFalse(wf.all_clear(sim, a["zone"])["ok"])                # the phones were never alerted
        self.assertTrue(wf.approve(sim, a["id"])["ok"])
        self.assertEqual(int(wf.zone_level[codes.index(a["zone"])]), a["level"])
        if len(waiting) > 1:
            b = waiting[1]
            self.assertTrue(wf.cancel(sim, b["id"])["ok"])
            self.assertEqual(b["state"], "cancelled")
            self.assertEqual(int(wf.zone_level[codes.index(b["zone"])]), 0)

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
