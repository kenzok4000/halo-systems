"""The valley test engineer (ai/valley_tester.py): the test conditions, scoring and searchers, without flying."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from halo_systems.ai import valley_tester as vt
from halo_systems.ai.cache import CallCache
from halo_systems.ai.client import NebiusError
from halo_systems.eval.scenarios import make


def _committed(seed, n=16):
    return make("wildfire_valley", seed, n=n, options={"alert_rule": "presigned", "approval": "auto", "report_v2": True})


def _ang(w):
    return math.degrees(math.atan2(w[1], w[0]))


class BuildTest(unittest.TestCase):
    def test_the_drawn_day_is_the_committed_valley(self):
        sc, ref = vt.build(vt.DEFAULTS, 3000), _committed(3000)
        for f in ("weather", "weather_forecast", "fires", "smoke", "heterogeneity_sigma", "alerts", "phones", "zones"):
            self.assertEqual(getattr(sc.wildfire, f), getattr(ref.wildfire, f), f)
        self.assertEqual(sc.wind, ref.wind)
        self.assertEqual(sc.n, 16)
        self.assertEqual(sc.wildfire.alerts.rule, "presigned")
        self.assertEqual(vt.build({}, 3000).wildfire, sc.wildfire)          # missing conditions are the drawn day

    def test_wind_scale_and_a_shift_the_forecast_does_not_know(self):
        ref = _committed(3001).wildfire
        sc = vt.build({**vt.DEFAULTS, "wind_scale": 2.0, "wind_shift_deg": 45.0}, 3001)
        for r, t, rf, tf in zip(ref.weather, sc.wildfire.weather, ref.weather_forecast, sc.wildfire.weather_forecast):
            self.assertAlmostEqual(math.hypot(*t.wind_mps), 2.0 * math.hypot(*r.wind_mps), places=9)
            turn = (_ang(t.wind_mps) - _ang(r.wind_mps) + 180.0) % 360.0 - 180.0
            self.assertAlmostEqual(turn, 45.0 if r.start_s >= 3600.0 else 0.0, places=6)
            # The forecast is the drawn forecast for the windier day: twice the wind, no shift.
            self.assertAlmostEqual(tf.wind_mps[0], 2.0 * rf.wind_mps[0], places=9)
            self.assertAlmostEqual(tf.wind_mps[1], 2.0 * rf.wind_mps[1], places=9)
            self.assertEqual(t.moisture, r.moisture)
        self.assertAlmostEqual(sc.wind.mean_mps[0], 2.0 * _committed(3001).wind.mean_mps[0])

    def test_a_perfect_forecast_and_drier_fuel(self):
        sc = vt.build({**vt.DEFAULTS, "forecast_error": 0.0, "moisture_scale": 0.7}, 3002)
        ref = _committed(3002).wildfire
        for r, t, tf in zip(ref.weather, sc.wildfire.weather, sc.wildfire.weather_forecast):
            self.assertAlmostEqual(tf.wind_mps[0], t.wind_mps[0], places=9)
            self.assertAlmostEqual(tf.wind_mps[1], t.wind_mps[1], places=9)
            self.assertAlmostEqual(t.moisture.d1, 0.7 * r.moisture.d1)
            self.assertAlmostEqual(tf.moisture.d1, t.moisture.d1)
            self.assertEqual(t.moisture.live, r.moisture.live)
        big = vt.build({**vt.DEFAULTS, "forecast_error": 3.0}, 3002).wildfire
        e1 = [abs(_ang(f.wind_mps) - _ang(t.wind_mps)) for t, f in zip(ref.weather, ref.weather_forecast)]
        e3 = [abs(_ang(f.wind_mps) - _ang(t.wind_mps)) for t, f in zip(big.weather, big.weather_forecast)]
        for a, b in zip(e1, e3):
            if a < 30.0:
                self.assertAlmostEqual(b, 3.0 * a, places=6)

    def test_smoulder_time_haze_patchiness_and_drones(self):
        ref = _committed(3003, n=8).wildfire
        sc = vt.build({**vt.DEFAULTS, "drones": 8, "smoulder_scale": 0.5, "visibility_m": 900.0,
                       "smoke_range_scale": 0.5, "patchiness": 0.6}, 3003)
        self.assertEqual(sc.n, 8)
        for r, f in zip(ref.fires, sc.wildfire.fires):
            self.assertAlmostEqual(f.t_active_s, r.t_active_s * (0.5 if r.t_active_s > 0 else 1.0))
            self.assertEqual((f.x, f.y, f.shelter), (r.x, r.y, r.shelter))
        self.assertEqual(sc.wildfire.smoke.visibility_m, 900.0)
        self.assertAlmostEqual(sc.wildfire.smoke.r50_ref_m, 0.5 * ref.smoke.r50_ref_m)
        self.assertEqual(sc.wildfire.heterogeneity_sigma, 0.6)
        self.assertEqual(sc.wildfire.phones, ref.phones)


class ConditionsTest(unittest.TestCase):
    def test_clip_and_stress_level(self):
        p = vt.clip_params({"drones": 99.6, "wind_scale": "x", "wind_shift_deg": float("nan"), "bogus": 1,
                            "moisture_scale": 0.1})
        self.assertEqual(p["drones"], 32)
        self.assertEqual(p["wind_scale"], 1.0)
        self.assertEqual(p["wind_shift_deg"], 0.0)
        self.assertEqual(p["moisture_scale"], 0.6)
        self.assertNotIn("bogus", p)
        self.assertEqual(vt.clip_params("not a dict"), vt.clip_params(vt.DEFAULTS))
        hard = {k: {"low": SPACE[0], "high": SPACE[1], "abs": SPACE[1]}[vt.HARD_END[k]] if k in vt.HARD_END else SPACE[0]
                for k, SPACE in vt.SPACE.items()}
        easy = {k: {"low": SPACE[1], "high": SPACE[0], "abs": 0.0}[vt.HARD_END[k]] if k in vt.HARD_END else SPACE[1]
                for k, SPACE in vt.SPACE.items()}
        self.assertEqual(vt.stress_level(hard), 1.0)
        self.assertEqual(vt.stress_level({**hard, "wind_shift_deg": -90.0}), 1.0)   # either way round
        self.assertEqual(vt.stress_level(easy), 0.0)
        self.assertTrue(0.2 < vt.stress_level(vt.DEFAULTS) < 0.35)

    def test_what_went_wrong_and_severity(self):
        ok = {"phones_missed": 0, "fires_spreading_missed": [], "alerts_from_false_fires": 0, "needless_evacuations": [],
              "drones_lost": 0, "alerts_not_justified": 2, "false_locations_standing": 1, "lead_time_min_p10": 60.0}
        self.assertEqual(vt.what(ok), set())
        self.assertEqual(vt.severity(ok), 3.0)
        bad = {**ok, "phones_missed": 3, "fires_spreading_missed": [1], "needless_evacuations": ["N2"],
               "lead_time_min_p10": 10.0}
        self.assertEqual(vt.what(bad), {"late-warning", "missed-fire", "needless-evacuation"})
        self.assertEqual(vt.severity(bad), 4 * 3 + 10 + 3 + 2 + 1 + 2.0)
        h = [{"params": {**vt.DEFAULTS, "wind_scale": 1.8, "drones": 6}, "outcome": {"kinds": sorted(vt.what(bad))}},
             {"params": vt.DEFAULTS, "outcome": {"kinds": []}}]
        self.assertEqual(vt.failure_kinds(h), {"late-warning:few-drones+windy", "missed-fire:few-drones+windy",
                                               "needless-evacuation:few-drones+windy"})

    def test_failures_the_conditions_caused(self):
        def h(seed, kinds, **p):
            return {"i": 0, "seed": seed, "why": "", "params": {**vt.DEFAULTS, **p},
                    "outcome": {"kinds": kinds, "failed": bool(kinds), "severity": 1.0, "phones_missed": 0}}
        drawn = {1: ["late-warning"], 2: []}
        hist = [h(1, ["late-warning"]), h(1, ["late-warning", "needless-evacuation"], wind_scale=1.8), h(2, ["late-warning"])]
        self.assertEqual(vt.caused(hist[0], drawn), set())                 # the valley fails that way anyway
        self.assertEqual(vt.caused(hist[1], drawn), {"needless-evacuation"})
        self.assertEqual(vt.caused(hist[2], drawn), {"late-warning"})
        s = vt.summarise_search(hist, drawn)
        self.assertEqual((s["failures"], s["caused_failures"], s["caused_kinds"]), (3, 2, 2))
        self.assertEqual(s["mildest_caused_stress"], vt.stress_level(vt.DEFAULTS))
        self.assertNotIn("caused_failures", vt.summarise_search(hist))

    def test_each_test_index_has_one_valley_for_every_searcher(self):
        self.assertEqual(vt.valley_seed(0, 0), 9100)
        self.assertEqual(vt.valley_seed(1, 23), 9173)
        with self.assertRaises(ValueError):
            vt.valley_seed(0, 50)


class _Record:
    def __init__(self, n):
        self.n = n

    def to_dict(self):
        return {"call_id": f"c{self.n}", "status": "ok", "cost_usd": 0.001}


class _Reply:
    def __init__(self, content, n):
        self.content, self.record = content, _Record(n)


class StubClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, model, messages, **kw):
        self.calls.append({"model": model, "messages": messages, **kw})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return _Reply(r, len(self.calls))


class SearcherTest(unittest.TestCase):
    def test_random_and_corner_searches_repeat_with_their_seed(self):
        a, b = vt.RandomSearch(1).propose([], 3), vt.RandomSearch(1).propose([], 3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, vt.RandomSearch(2).propose([], 3))
        for prop in vt.CornerSearch(0).propose([], 6):
            self.assertTrue(all(prop["params"][k] in (lo, hi) for k, (lo, hi, _, _) in vt.SPACE.items()))

    def test_the_model_proposes_and_a_recorded_search_replays_with_no_key(self):
        reply = json.dumps({"tests": [{"params": {"wind_shift_deg": 80, "drones": 6}, "why": "wind turns after landing"}]})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.jsonl"
            client = StubClient([reply])
            s = vt.NemotronSearch(client, "nvidia/nano", seed=0, cache=CallCache(path, "record"))
            props = s.propose([], 2)
            self.assertEqual(props[0]["params"]["wind_shift_deg"], 80.0)
            self.assertEqual(props[0]["params"]["wind_scale"], 1.0)          # unnamed conditions stay the drawn day
            self.assertEqual(props[0]["why"], "wind turns after landing")
            self.assertTrue(props[1]["why"].startswith("random top-up"))
            self.assertEqual(client.calls[0]["role"], "valley-test-engineer")
            again = vt.NemotronSearch(None, "nvidia/nano", seed=0, cache=CallCache(path, "replay"), strict=True)
            self.assertEqual(again.propose([], 2), props)
            self.assertTrue(again.calls[0]["replayed"])
            with self.assertRaises(NebiusError):                              # a new request is never called live
                again.propose([{"params": vt.DEFAULTS, "outcome": {"kinds": [], "severity": 0.0, "phones_reached_3h": 0,
                                "phones_missed": 0, "fires_spreading_missed": [], "alerts_from_false_fires": 0,
                                "needless_evacuations": [], "alerts_not_justified": 0, "lead_time_min_p10": None}}], 2)

    def test_bad_replies_top_up_and_a_dead_key_stops_a_strict_search(self):
        for bad in ("not json", json.dumps(["a list"]), json.dumps({"tests": {"params": {}}})):
            props = vt.NemotronSearch(StubClient([bad]), "m").propose([], 2)
            self.assertEqual(len(props), 2)
            self.assertTrue(all(p["why"].startswith("random top-up") for p in props))
        topped = vt.NemotronSearch(StubClient(["not json"]), "m", seed=0).propose([], 4)
        self.assertNotEqual([p["params"] for p in topped], [p["params"] for p in vt.RandomSearch(0).propose([], 4)])
        dead = StubClient([NebiusError("no key", fatal=True)])
        self.assertEqual(len(vt.NemotronSearch(dead, "m").propose([], 2)), 2)
        with self.assertRaises(NebiusError):
            vt.NemotronSearch(StubClient([NebiusError("no key", fatal=True)]), "m", strict=True).propose([], 2)

    def test_unreadable_proposals_are_topped_up_and_counted(self):
        reply = json.dumps({"tests": [
            {"drones": 6, "wind_scale": 1.8, "why": "flat, not under params"},
            {"params": "drones=6", "why": "a string"},
            {"params": [6, 1.8], "why": "a list"},
            {"why": "no params"},
            {"params": {"visibility": 600, "wind_shift": 80}, "why": "misspelt names"},
            {"params": {"drones": 10 ** 400}, "why": "a number too big for a float"}]})
        props = vt.NemotronSearch(StubClient([reply]), "m").propose([], 6)
        self.assertEqual(len(props), 6)
        self.assertTrue(all(p["why"].startswith("random top-up") for p in props))
        hist = [{"i": i, "seed": 1, "params": p["params"], "why": p["why"],
                 "outcome": {"kinds": [], "failed": False, "severity": 0.0, "phones_missed": 0}} for i, p in enumerate(props)]
        self.assertEqual(vt.summarise_search(hist)["random_top_ups"], 6)
        self.assertEqual(vt.clip_params({"drones": 10 ** 400})["drones"], 16)
        ok = vt.NemotronSearch(StubClient([json.dumps({"tests": [{"params": {"drones": 6}, "why": "few"}]})]), "m")
        prop = ok.propose([], 1)[0]
        self.assertEqual((prop["params"]["drones"], prop["why"]), (6, "few"))
        self.assertIn("wind_scale", prop["defaulted"])
        self.assertNotIn("drones", prop["defaulted"])

    def test_two_searches_with_the_same_first_prompt_each_make_a_call(self):
        reply = json.dumps({"tests": [{"params": {"drones": 6}, "why": "s"}]})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.jsonl"
            c0, c1 = StubClient([reply]), StubClient([reply.replace('"s"', '"t"')])
            p0 = vt.NemotronSearch(c0, "m", cache=CallCache(path, "record"), salt={"search": 0, "base": 9100}).propose([], 1)
            p1 = vt.NemotronSearch(c1, "m", cache=CallCache(path, "record"), salt={"search": 1, "base": 9100}).propose([], 1)
            self.assertEqual((len(c0.calls), len(c1.calls)), (1, 1))
            self.assertEqual((p0[0]["why"], p1[0]["why"]), ("s", "t"))
            self.assertEqual(c0.calls[0]["messages"], c1.calls[0]["messages"])    # the prompt itself is unchanged
            r0 = vt.NemotronSearch(None, "m", cache=CallCache(path, "replay"), salt={"search": 0, "base": 9100},
                                   strict=True).propose([], 1)
            self.assertEqual(r0, p0)

    def test_a_failed_call_is_recorded_with_its_cost_and_replays_the_same_way(self):
        rec = _Record(1)
        err = NebiusError("reply cut off at max_tokens", 200, record=rec)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.jsonl"
            s = vt.NemotronSearch(StubClient([err]), "m", seed=3, cache=CallCache(path, "record"), strict=True)
            props = s.propose([], 2)
            self.assertTrue(all(p["why"].startswith("random top-up") for p in props))
            self.assertEqual(len(s.calls), 1)
            self.assertEqual((s.calls[0]["status"], s.calls[0]["cost_usd"]), ("error", 0.001))
            again = vt.NemotronSearch(None, "m", seed=3, cache=CallCache(path, "replay"), strict=True)
            self.assertEqual(again.propose([], 2), props)                     # no live call, the same top-ups
            self.assertTrue(again.calls[0]["replayed"])
            self.assertEqual(again.calls[0]["status"], "error")
        # Not the model's reply (an HTTP error, a timeout): a strict search stops, and nothing is recorded.
        for status in (402, 429, None):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "cache.jsonl"
                s = vt.NemotronSearch(StubClient([NebiusError("HTTP or network", status, record=_Record(1))]), "m",
                                      cache=CallCache(path, "record"), strict=True)
                with self.assertRaises(NebiusError):
                    s.propose([], 2)
                self.assertFalse(path.exists() and path.read_text().strip())
                self.assertEqual(s.calls[0]["cost_usd"], 0.001)
        loose = vt.NemotronSearch(StubClient([NebiusError("HTTP 500", 500)]), "m")
        self.assertEqual(len(loose.propose([], 2)), 2)                    # without strict it tops up

    def test_conditions_given_but_unreadable_stay_the_drawn_day_and_are_listed(self):
        reply = json.dumps({"tests": [{"params": {"drones": 8, "visibility_m": "900 m", "wind_scale": None,
                                                  "patchiness": True}, "why": "haze\nand few drones"}]})
        prop = vt.NemotronSearch(StubClient([reply]), "m").propose([], 1)[0]
        self.assertEqual(prop["params"]["drones"], 8)
        self.assertEqual((prop["params"]["visibility_m"], prop["params"]["wind_scale"], prop["params"]["patchiness"]),
                         (2500.0, 1.0, 0.3))
        self.assertTrue({"visibility_m", "wind_scale", "patchiness"} <= set(prop["defaulted"]))
        self.assertNotIn("drones", prop["defaulted"])
        self.assertEqual(prop["why"], "haze and few drones")


class PairedRecheckTest(unittest.TestCase):
    def test_delivery_lags_come_from_their_own_stream(self):
        import numpy as np

        class FakeStation:
            def __init__(self):
                self.rng = np.random.default_rng(1)

            def _broadcast(self, phones, level, t, t_auto):
                return [float(self.rng.uniform(0, 1)) for _ in phones]

        a, b = FakeStation(), FakeStation()
        vt._own_delivery_stream(b, seed=5)
        lags = b._broadcast([1, 2, 3], 4, 0.0, 0.0)
        self.assertEqual(len(lags), 3)
        self.assertEqual(b.rng.uniform(), a.rng.uniform())               # the station's own stream is untouched
        c = FakeStation()
        vt._own_delivery_stream(c, seed=5)
        self.assertEqual(c._broadcast([1, 2, 3], 4, 0.0, 0.0), lags)       # and the lags repeat with the seed


def _stress_tool():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "tools" / "wildfire_valley_stress.py"
    spec = importlib.util.spec_from_file_location("wildfire_valley_stress", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReportTest(unittest.TestCase):
    def _out(self, kinds, phones=0):
        return {"kinds": kinds, "failed": bool(kinds), "severity": 4.0 * phones, "phones_missed": phones}

    def _results(self):
        def h(i, seed, kinds, why="", **p):
            return {"i": i, "seed": seed, "why": why, "params": vt.clip_params({**vt.DEFAULTS, **p}),
                    "outcome": self._out(kinds, 2 if "late-warning" in kinds else 0)}
        nem = {"searcher": "nemotron", "search": 0, "recorded_cost_usd": 0.004, "model_cost_usd": 0.004,
               "history": [h(0, 1, ["late-warning"], why="few drones\nand haze", drones=5),
                           h(1, 2, ["needless-evacuation"], why="a | b", wind_scale=1.9)]}
        rnd = {"searcher": "random", "search": 0, "model_cost_usd": 0.0, "history": [h(0, 1, []), h(1, 2, ["late-warning"])]}
        drawn = {"searcher": "drawn-day", "search": None, "model_cost_usd": 0.0,
                 "history": [h(0, 1, ["late-warning"]), h(1, 2, [])]}
        return [nem, rnd, drawn]

    def test_the_report_counts_caused_failures_and_reads_a_paired_recheck(self):
        tool = _stress_tool()
        results = self._results()
        x = lambda f, s, kinds, p, t: {"file": f, "searcher": s, "i": 0, "seed": 1, "params": vt.DEFAULTS,
                                       "as_searched": self._out(kinds, 2), "presigned_paired": self._out(kinds, p),
                                       "today_rule": self._out(["late-warning"] if t else [], t)}
        recheck = {"paired": True, "tests": [x("nemotron_0.json", "nemotron", ["late-warning"], 2, 0),
                                             x("drawn_day.json", "drawn-day", ["late-warning"], 2, 2)]}
        text = tool.report(results, recheck)
        rows = {}
        for line in text.splitlines():                                       # the first table: by searcher
            if line.startswith("| "):
                rows.setdefault(line.split("|")[1].strip(), line)
        self.assertIn("| nemotron | 1 | 2 | 1 | 1 |", rows["nemotron"])      # only the needless evacuation is new
        self.assertIn("USD 0.0040", rows["nemotron"])
        self.assertIn("| random | 1 | 2 | 1 |", rows["random"])
        self.assertIn("few drones and haze", text)                           # a newline cannot break the table
        self.assertIn("a / b", text)
        self.assertIn("(drawn-day tests included)", text)
        self.assertIn("Phones warned late: 4 under pre-signed rules, 2 under today's rule", text)
        recheck["tests"] = recheck["tests"][:1]
        self.assertNotIn("(drawn-day tests included)", tool.report(results, recheck))
        old = {"tests": [{k: v for k, v in t.items() if k != "presigned_paired"} for t in recheck["tests"]]}
        with self.assertRaises(ValueError):
            tool.report(results, old)


if __name__ == "__main__":
    unittest.main()
