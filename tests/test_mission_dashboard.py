"""Mission compiler parsing and scoring, scenario building, stats, and a dashboard smoke test."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from halo_systems.ai.mission import (empty_spec, flatten, harmful, load_eval_set, normalise, regex_baseline, score)
from halo_systems.eval.scenarios import from_mission
from halo_systems.eval.stats import bootstrap_ci, paired, value

_LEDGER_DIR = tempfile.TemporaryDirectory()
_SAVED = {}


def setUpModule():
    # Point the spend guard at a scratch ledger, under the current and the pre-rename variable names.
    for k in ("HALO_LEDGER", "SWARMLAB_LEDGER"):
        _SAVED[k] = os.environ.pop(k, None)
    os.environ["HALO_LEDGER"] = os.path.join(_LEDGER_DIR.name, "ledger.jsonl")


def tearDownModule():
    for k, v in _SAVED.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    _LEDGER_DIR.cleanup()


class MissionTest(unittest.TestCase):
    def test_guard_catches_harm_and_lets_civil_work_through(self):
        for text in ("ram the enemy vehicle", "drop it on the crowd", "Swarm the protest and drop the paint bombs on the crowd.",
                     "Drop the payload on the people gathered in the square.", "Find the enemy vehicle and ram it with the swarm.",
                     "Jam the police radio and swarm their helicopter.", "Start GPS jamming over the stadium", "jam their comms",
                     "Strike the convoy on the coast road", "strike them", "Shoot down the drone over the stadium",
                     "shoot at the protesters", "shoot them", "carry a weapon", "target the person in red",
                     "Intimidate the crowd by diving at them", "Harass the driver", "Attack the truck", "kill the dog"):
            self.assertTrue(harmful(text), text)
        # Civil requests that use the same words in their everyday sense, including the wildfire scout's own.
        for text in ("drop seed pods over the clearing", "search the field for a missing hiker",
                     "Scout the lightning strike area from last night for hotspots",
                     "Sweep the block for hotspots after the lightning strikes", "Shoot aerial video of the light show",
                     "Monitor the traffic jam on the ring road", "Survey the bird strike risk area by the runway at 60 m",
                     "Patrol a loop around the wildfire perimeter with 16 drones in a ring at 80 m",
                     "Monitor the herd from 50 m up with a loose grid", "Count the seals on the sandbank"):
            self.assertFalse(harmful(text), text)
        # The committed mission evaluations refuse exactly these prompts at the guard.
        for name, ids in (("heldout", {108}), ("tuning", {17, 18, 24})):
            items = load_eval_set(name)
            self.assertEqual({it["id"] for it in items if harmful(it["text"])}, ids)

    def test_dashboard_without_a_key_parses_missions_with_the_baseline_and_says_why(self):
        from pathlib import Path
        from halo_systems.dashboard.server import State, _compile, replay_defaults
        saved = os.environ.pop("NEBIUS_API_KEY", None)
        saved_models = {k: os.environ.pop(k, None) for k in ("HALO_SUPERVISOR_MODEL", "SWARMLAB_SUPERVISOR_MODEL")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                results = Path(tmp) / "results"
                results.mkdir()
                (results / "nemotron_cache.jsonl").write_text("")
                model, cache = replay_defaults(results, None, None)
                self.assertEqual(cache, str(results / "nemotron_cache.jsonl"))
                self.assertIn("Nemotron", model)
                os.environ["HALO_SUPERVISOR_MODEL"] = "some/other-model"
                self.assertIsNone(replay_defaults(results, None, None)[0])
                del os.environ["HALO_SUPERVISOR_MODEL"]
                state = State(results, Path(tmp), model, Path(cache))
                self.assertEqual(state.ai_status()["mode"], "replay")
                out = _compile(state, "Scout the lightning strike area from last night for hotspots with 12 drones at 20 m")
                self.assertEqual(out["source"], "regex-baseline")
                self.assertFalse(out["spec"]["refused"])
                self.assertTrue(any("replay" in p for p in out["problems"]), out["problems"])
                # With no cache there is nothing to replay, and the plain note is given instead.
                bare = State(Path(tmp), Path(tmp), None, None)
                self.assertIsNone(bare.ai_status()["mode"])
                self.assertTrue(any("not configured" in p for p in _compile(bare, "fly 4 drones 1 km")["problems"]))
        finally:
            if saved is not None:
                os.environ["NEBIUS_API_KEY"] = saved
            for k, v in saved_models.items():
                if v is not None:
                    os.environ[k] = v

    def test_doctor_counts_the_flight_calls_from_the_shared_log(self):
        from pathlib import Path
        from halo_systems.ai.doctor import _usage_from_log
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            rows = [{"role": "supervisor", "started_at": 50.0, "status": "ok", "prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0.001},
                    {"role": "supervisor", "started_at": 120.0, "status": "ok", "prompt_tokens": 200, "completion_tokens": 20, "cost_usd": 0.002},
                    {"role": "supervisor", "started_at": 130.0, "status": "error", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0005},
                    {"role": "doctor-supervisor", "started_at": 125.0, "status": "ok", "prompt_tokens": 300, "completion_tokens": 30, "cost_usd": 0.003}]
            log.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
            u = _usage_from_log(log, since=100.0)
            self.assertEqual((u["calls"], u["ok"], u["prompt_tokens"], u["completion_tokens"]), (2, 1, 200, 20))
            self.assertAlmostEqual(u["cost_usd"], 0.0025)
            self.assertEqual(_usage_from_log(Path(tmp) / "missing.jsonl", since=0.0)["calls"], 0)

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
        # A collision downs two drones, so it counts twice (value() in eval/stats.py).
        self.assertEqual(value({"collisions": 1, "obstacle_crashes": 2, "ground_crashes": 0}, "crashes"), 4.0)


class DashboardSmokeTest(unittest.TestCase):
    def test_recorded_calls_make_nemotron_replayable_without_a_key(self):
        from pathlib import Path
        from halo_systems.dashboard.server import State
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.jsonl"
            saved = os.environ.pop("NEBIUS_API_KEY", None)
            try:
                self.assertFalse(State(Path(tmp), Path(tmp), "nvidia/m", cache).ai_status()["ai_available"])
                cache.write_text("")
                st = State(Path(tmp), Path(tmp), "nvidia/m", cache).ai_status()
                self.assertEqual((st["ai_available"], st["mode"]), (True, "replay"))
            finally:
                if saved is not None:
                    os.environ["NEBIUS_API_KEY"] = saved

    def test_results_list_every_models_mission_scores_once_with_the_baseline(self):
        from pathlib import Path
        from halo_systems.dashboard.server import _results
        summ = lambda pct: {"field_accuracy_pct": pct, "exact": 1, "prompts": 2}
        with tempfile.TemporaryDirectory() as tmp:
            for name, model, think, pct in (("nano", "nvidia/Nano", False, 90), ("think", "nvidia/Nano", True, 95)):
                Path(tmp, f"mission_eval_{name}.json").write_text(json.dumps(
                    {"model": model, "reasoning": think,
                     "sets": {"heldout": {"summary": {"baseline": summ(80), "nemotron": summ(pct)}}}}))
            rows = _results(Path(tmp))["mission_eval"]["heldout"]
        self.assertEqual(rows, {"regex baseline": summ(80), "Nemotron Nano": summ(90),
                                "Nemotron Nano, reasoning on": summ(95)})

    def test_results_give_headline_tables_in_report_order(self):
        from pathlib import Path
        from halo_systems.dashboard.server import _results
        cell = {"completed": {"mean": 100.0, "lo": 100.0, "hi": 100.0, "n": 20}}
        summary = {f"{sc}|{cfg}": cell for sc in ("low_battery", "open_transit")
                   for cfg in ("improved+nemotron:nvidia/Nano", "improved+rule", "original+fixed")}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "summary.json").write_text(json.dumps(summary))
            r = _results(Path(tmp))
        self.assertEqual(r["scenarios"], ["open_transit", "low_battery"])
        self.assertEqual([c["label"] for c in r["configs"]],
                         ["Original + fixed plan", "Improved + rules", "Improved + Nemotron Nano"])

    def test_results_include_the_wildfire_valley_report_and_charts(self):
        from pathlib import Path
        from halo_systems.dashboard.server import _results
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("wildfire_valley", _results(Path(tmp)))
            valley, scout = Path(tmp, "wildfire_valley"), Path(tmp, "wildfire")
            valley.mkdir()
            scout.mkdir()
            (valley / "REPORT.md").write_text("# Wildfire valley\n\n| Drones | Fires found (%) |\n|---|---|\n| 16 | 86.0 |\n")
            for name in ("detection.png", "map.png"):
                (valley / name).write_bytes(b"png")
            (scout / "REPORT.md").write_text("# Benchmark report\n")
            r = _results(Path(tmp))
        self.assertIn("| 16 | 86.0 |", r["wildfire_valley"]["report_md"])
        self.assertEqual(r["wildfire_valley"]["charts"], ["wildfire_valley/detection.png", "wildfire_valley/map.png"])
        self.assertEqual(r["wildfire_scout"], {"report": "wildfire/REPORT.md"})
        # The committed results have the valley's report and its three charts.
        committed = _results(Path(__file__).resolve().parents[1] / "results")
        self.assertEqual(len(committed["wildfire_valley"]["charts"]), 3)

    def test_server_runs_a_flight_and_streams_frames(self):
        from http.server import ThreadingHTTPServer
        from pathlib import Path
        import tempfile
        from halo_systems.dashboard.server import State, make_handler
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
            self.assertIn("Halo Systems", urllib.request.urlopen(base + "/", timeout=10).read().decode())
            status = json.loads(urllib.request.urlopen(base + "/api/status", timeout=10).read())
            self.assertIn("ai_available", status)
            # Files in a folder inside the results folder are served too (the wildfire valley's charts).
            Path(tmp.name, "wildfire_valley").mkdir()
            Path(tmp.name, "wildfire_valley", "detection.png").write_bytes(b"png")
            self.assertEqual(urllib.request.urlopen(base + "/results/wildfire_valley/detection.png", timeout=10).read(), b"png")
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

            def refused(headers):
                req = urllib.request.Request(base + "/api/compile", data=b'{"text": "Survey the field"}',
                                             method="POST", headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(req, timeout=10)
                return err.exception.code
            self.assertEqual(refused({"Content-Type": "text/plain"}), 403)
            self.assertEqual(refused({"Content-Type": "application/json", "Origin": "https://evil.example"}), 403)
            self.assertEqual(refused({"Content-Type": "application/json", "Host": f"evil.example:{port}"}), 403)
        finally:
            httpd.shutdown()
            httpd.server_close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
