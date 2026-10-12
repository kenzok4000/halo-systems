"""Mission compiler parsing and scoring, scenario building, stats, and a dashboard smoke test."""

import json
import threading
import time
import unittest
import urllib.request

from swarmlab.ai.mission import (empty_spec, flatten, harmful, load_eval_set, normalise, regex_baseline, score)
from swarmlab.eval.scenarios import from_mission
from swarmlab.eval.stats import bootstrap_ci, paired, value


class MissionTest(unittest.TestCase):
    def test_guard_catches_harm_and_lets_civil_work_through(self):
        self.assertTrue(harmful("ram the enemy vehicle"))
        self.assertTrue(harmful("drop it on the crowd"))
        self.assertFalse(harmful("drop seed pods over the clearing"))
        self.assertFalse(harmful("search the field for a missing hiker"))

    def test_normalise_coerces_and_flags_problems(self):
        spec, problems = normalise({"drones": "12", "directive": {"formation": "Line", "spacing_m": "8", "speed_mps": "fast"},
                                    "route": {"type": "survey", "width_m": 300}})
        self.assertEqual(spec["drones"], 12)
        self.assertEqual(spec["directive"]["formation"], "line")
        self.assertIsNone(spec["directive"]["speed_mps"])
        self.assertTrue(any("speed_mps" in p for p in problems))
        _, problems = normalise({"directive": {"formation": "star"}})
        self.assertTrue(any("formation" in p for p in problems))

    def test_scoring_uses_tolerances_and_only_stated_fields(self):
        spec = empty_spec()
        spec["directive"].update(formation="line", spacing_m=8.1)
        s = score(spec, {"formation": "line", "spacing_m": 8.0, "refused": False})
        self.assertTrue(s["exact"])
        s = score(spec, {"formation": "column"})
        self.assertFalse(s["exact"])
        self.assertIn("route_type", flatten(spec))

    def test_eval_sets_are_well_formed(self):
        for name in ("tuning", "heldout"):
            items = load_eval_set(name)
            self.assertGreaterEqual(len(items), 20)
            for it in items:
                self.assertIn("refused", it["gold"])
                keys = set(flatten(empty_spec()))
                self.assertTrue(set(it["gold"]) <= keys, it["id"])

    def test_baseline_parses_units(self):
        spec = regex_baseline("Grid formation, spacing 12 feet, 9 drones, altitude 100 feet, speed 8 m/s, fly 250 m north.")
        self.assertAlmostEqual(spec["directive"]["spacing_m"], 3.66, places=2)
        self.assertAlmostEqual(spec["directive"]["altitude_m"], 30.48, places=2)

    def test_compiled_mission_flies(self):
        spec = regex_baseline("Orbit the tower with a ring of 10 drones at 40 m")
        sc = from_mission(spec, 0)
        self.assertEqual((sc.n, sc.directive.formation, sc.loop), (10, "ring", True))
        refused = empty_spec()
        refused["refused"] = True
        with self.assertRaises(ValueError):
            from_mission(refused)


class StatsTest(unittest.TestCase):
    def test_bootstrap_and_paired(self):
        mean, lo, hi = bootstrap_ci([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(mean, 2.5)
        self.assertLessEqual(lo, mean)
        self.assertGreaterEqual(hi, mean)
        rows = [{"scenario": "s", "config": "a", "seed": k, "time_formed_pct": 50.0} for k in range(5)] + \
               [{"scenario": "s", "config": "b", "seed": k, "time_formed_pct": 60.0 + k} for k in range(5)]
        p = paired(rows, "s", "a", "b", "time_formed_pct")
        self.assertEqual((p["b_better"], p["b_worse"], p["n"]), (5, 0, 5))
        self.assertEqual(value({"collisions": 1, "obstacle_crashes": 2, "ground_crashes": 0}, "crashes"), 4.0)


class DashboardSmokeTest(unittest.TestCase):
    def test_server_runs_a_flight_and_streams_frames(self):
        from http.server import ThreadingHTTPServer
        from pathlib import Path
        import tempfile
        from swarmlab.dashboard.server import State, make_handler
        tmp = tempfile.TemporaryDirectory()
        state = State(Path(tmp.name), Path(tmp.name), None, None)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        try:
            def post(path, body):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                             headers={"Content-Type": "application/json"})
                return json.loads(urllib.request.urlopen(req, timeout=10).read())
            self.assertIn("SwarmLab", urllib.request.urlopen(base + "/", timeout=10).read().decode())
            status = json.loads(urllib.request.urlopen(base + "/api/status", timeout=10).read())
            self.assertIn("ai_available", status)
            run = post("/api/runs", {"scenario": "open_transit", "config": "improved+rule", "seed": 1, "speed": 10})
            post(f"/api/runs/{run['id']}/inject", {"kind": "gust"})
            stream = urllib.request.urlopen(f"{base}/api/runs/{run['id']}/stream", timeout=10)
            frames = 0
            t0 = time.time()
            while frames < 5 and time.time() - t0 < 20:
                line = stream.readline().decode()
                if line.startswith("event: frame"):
                    frames += 1
            stream.close()
            self.assertGreaterEqual(frames, 5)
            post(f"/api/runs/{run['id']}/control", {"stop": True})
            compiled = post("/api/compile", {"text": "Ram the enemy truck"})
            self.assertTrue(compiled["spec"]["refused"])
        finally:
            httpd.shutdown()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
