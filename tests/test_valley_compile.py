"""The valley compiler: the night's strike list, the strike filter, the checked plan, the baseline, recorded calls."""

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from halo_systems.ai import valley
from halo_systems.ai.cache import CallCache
from halo_systems.ai.client import CallRecord, ChatResult, NebiusError
from halo_systems.control.squads import cue_lane, lane_ys
from halo_systems.eval.scenarios import (SCOUT_SPACING_M, STRIKE_ERR_M, make, valley_areas, valley_strikes,
                                         wildfire_valley)

MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def strike(i, hours_ago, area, day=None, x=100, y=100):
    clock = 8 * 60 - round(hours_ago * 60)
    day = day or ("today" if clock >= 0 else "yesterday" if clock >= -1440 else "2 days ago")
    return {"id": i, "day": day, "time": f"{(clock // 60) % 24:02d}:{clock % 60:02d}", "hours_ago": hours_ago,
            "x": x, "y": y, "area": area}


LIST = [strike(1, 34.0, "-"), strike(2, 28.5, "N"), strike(3, 11.5, "F"), strike(4, 9.0, "N"), strike(5, 8.5, "-"),
        strike(6, 6.5, "S"), strike(7, 4.0, "N"), strike(8, 3.2, "F")]


class FakeClient:
    def __init__(self, reply: dict):
        self.reply = json.dumps(reply)
        self.calls = 0

    def chat(self, model, messages, **kw):
        self.calls += 1
        rec = CallRecord(call_id="c1", role=kw.get("role", "chat"), model=model, started_at=0.0, latency_s=1.5,
                         status="ok", prompt_tokens=900, completion_tokens=300, total_tokens=1200, cost_usd=0.0002)
        return ChatResult(self.reply, rec, {})


class StrikeListTest(unittest.TestCase):
    def test_one_strike_near_each_fire_and_the_valley_unchanged(self):
        for seed in (2001, 3000):
            plain = wildfire_valley(seed, n=16)
            sc = wildfire_valley(seed, n=16, strikes="all")
            self.assertIsNone(plain.strikes)
            self.assertNotIn("strikes", plain.summary())
            a, b = plain.summary(), sc.summary()
            b.pop("strikes")
            self.assertEqual(a, b)                                    # asking for strikes changes nothing else
            ks = sc.strikes
            self.assertEqual([k["id"] for k in ks], list(range(1, len(ks) + 1)))
            self.assertEqual(ks, sorted(ks, key=lambda k: -k["hours_ago"]))
            for f in sc.wildfire.fires:
                near = min(math.hypot(k["x"] - f.x, k["y"] - f.y) for k in ks if k["hours_ago"] < 24)
                self.assertLess(near, 5 * STRIKE_ERR_M)
            for k in ks:
                clock = 8 * 60 - round(k["hours_ago"] * 60)
                self.assertEqual(k["time"], f"{(clock // 60) % 24:02d}:{clock % 60:02d}")
                self.assertEqual(k["day"], "today" if clock >= 0 else "yesterday" if clock >= -1440 else "2 days ago")
                self.assertFalse(k["cue"])
            self.assertTrue(any(k["hours_ago"] >= 24 for k in ks))
            self.assertEqual([dict(k, cue=False) for k in ks], [dict(k, cue=False) for k in valley_strikes(seed)])
        with self.assertRaises(ValueError):
            wildfire_valley(3000, n=4, strikes=[999])

    def test_squads_start_on_the_lane_nearest_their_strikes(self):
        ks = valley_strikes(3000)
        north = {a.code: a for a in valley_areas()}["N"]
        sw = 2.0 * float(wildfire_valley(0, n=1).sensor.footprint_radius(20.0))
        top = [{"id": k["id"]} for k in ks if k["area"] == "N"]
        plain = wildfire_valley(3000, n=16, squads="auto")
        # Cue on a strike placed by hand near the north edge: the north squad starts on its top lane.
        ys, lanes, _ = lane_ys(north.rect, 6, SCOUT_SPACING_M, sw)
        self.assertEqual(cue_lane(north.rect, 6, SCOUT_SPACING_M, sw, [(100.0, ys[-1] + 1.0)]), lanes - 1)
        self.assertIsNone(cue_lane(north.rect, 6, SCOUT_SPACING_M, sw, [(100.0, 0.0)]))
        cued = wildfire_valley(3000, n=16, squads="auto", strikes=[k["id"] for k in top])
        pts = [(k["x"], k["y"]) for k in ks if k["area"] == "N"]
        want = cue_lane(north.rect, 6, SCOUT_SPACING_M, sw, pts)
        sq = next(s for s in cued.squads if s.code == "N")
        self.assertAlmostEqual(sq.waypoints[0][1], ys[want])
        self.assertEqual([s.waypoints for s in plain.squads if s.code != "N"],
                         [s.waypoints for s in cued.squads if s.code != "N"])
        self.assertEqual(sum(k["cue"] for k in cued.strikes), len(top))


class FilterAndPlanTest(unittest.TestCase):
    def test_filters_pick_the_strikes_asked_for(self):
        sel = lambda when, where="any", areas=None: valley.select_strikes(          # noqa: E731
            {"strikes": {"when": when, "where": where}, "areas": areas}, LIST)
        self.assertEqual(sel("all"), [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(sel("last_night"), [3, 4, 5, 6, 7, 8])
        self.assertEqual(sel("night_before"), [1, 2])
        self.assertEqual(sel("today"), [6, 7, 8])                    # 01:30, 04:00, 04:48
        self.assertEqual(sel("since 22:00"), [4, 5, 6, 7, 8])        # 20:30 is before it
        self.assertEqual(sel("since 02:00"), [7, 8])
        self.assertEqual(sel("within 4"), [7, 8])
        self.assertEqual(sel("last_night", "named", ["N"]), [4, 7])
        self.assertEqual(sel("all", "outside"), [1, 5])
        self.assertIsNone(valley.select_strikes({"strikes": None}, LIST))

    def test_replies_are_checked_and_two_misreadings_repaired(self):
        spec, problems = valley.normalise({"drones": "16", "areas": ["north", "Z"], "per_area": {"N": 16},
                                           "patrol_min": -3, "strikes": {"when": "since 25:00", "where": "named"}})
        self.assertEqual(spec["drones"], 16)
        self.assertEqual(spec["areas"], ["N"])
        self.assertIsNone(spec["per_area"])                          # one area's number was the total
        self.assertIsNone(spec["patrol_min"])
        self.assertEqual(spec["strikes"], {"when": "all", "where": "named"})
        self.assertEqual(len(problems), 4)
        spec, problems = valley.normalise({"areas": "all", "strikes": {"when": "within 5", "where": "named"}})
        self.assertEqual(spec["areas"], ["F", "N", "S"])
        spec, problems = valley.normalise({"strikes": {"when": "Since 1:30", "where": "named"}})
        self.assertEqual(spec["strikes"], {"when": "since 01:30", "where": "any"})   # no area named
        self.assertEqual(problems, ["strikes tied to areas, but no area named: strikes anywhere"])

    def test_the_plan_follows_fixed_rules_and_lists_what_it_changed(self):
        p = valley.plan({**valley.empty_spec(), "drones": 16, "areas": ["N"],
                         "strikes": {"when": "last_night", "where": "named"}}, LIST)
        self.assertEqual(p["squads"], "F0-N12-S0-R4")
        self.assertEqual(p["strike_ids"], [4, 7])
        self.assertEqual(p["problems"], [])
        self.assertEqual(valley.scenario_options(p), {"squads": "F0-N12-S0-R4", "patrol_s": 720.0, "strikes": [4, 7]})
        # No area named: the areas that hold the chosen strikes fly; a strike outside every area is listed.
        p = valley.plan({**valley.empty_spec(), "drones": 12, "strikes": {"when": "last_night", "where": "any"}}, LIST)
        self.assertEqual(p["areas"], ["F", "N", "S"])
        self.assertEqual(p["uncovered"], [5])
        p = valley.plan({**valley.empty_spec(), "strikes": {"when": "within 4", "where": "any"}}, LIST)
        self.assertEqual(p["areas"], ["F", "N"])
        # The operator's numbers, a line too wide for its area, too many drones, too long a patrol.
        p = valley.plan({**valley.empty_spec(), "drones": 90, "per_area": {"F": 30, "S": 5}, "patrol_min": 40}, LIST)
        self.assertEqual(p["counts"]["S"], 5)
        self.assertEqual(p["counts"]["F"], 21)
        self.assertEqual(p["drones"], 64)
        self.assertEqual(p["patrol_min"], 15.0)
        self.assertEqual(len(p["problems"]), 3)
        self.assertEqual(sum(p["counts"].values()), 64)
        self.assertTrue(valley.plan({**valley.empty_spec(), "refused": True}, LIST)["refused"])

    def test_the_patrol_flies(self):
        p = valley.plan({**valley.empty_spec(), "drones": 8, "areas": ["F", "N"],
                         "strikes": {"when": "last_night", "where": "named"}}, valley_strikes(3001))
        sc = make("wildfire_valley", 3001, options=valley.scenario_options(p))
        self.assertEqual(sc.squad_counts, {"F": 4, "N": 4, "S": 0, "R": 0})
        self.assertEqual(sorted(k["id"] for k in sc.strikes if k["cue"]), p["strike_ids"])


class BaselineAndScoreTest(unittest.TestCase):
    def test_baseline_reads_plain_requests(self):
        b = valley.regex_baseline("check last night's strikes in the north block with 16 drones")
        self.assertEqual((b["drones"], b["areas"], b["per_area"]), (16, ["N"], None))
        self.assertEqual(b["strikes"], {"when": "last_night", "where": "named"})
        b = valley.regex_baseline("six on the floor and 4 drones on the south side, any lightning after 10pm, 9 minutes")
        self.assertEqual(b["per_area"], {"F": 6, "S": 4})
        self.assertIsNone(b["drones"])
        self.assertEqual(b["patrol_min"], 9.0)
        self.assertEqual(b["strikes"], {"when": "since 22:00", "where": "any"})
        self.assertEqual(valley.regex_baseline("strikes in the past 5 hours")["strikes"]["when"], "within 5")
        self.assertIsNone(valley.regex_baseline("strikes in the past 5 hours")["patrol_min"])
        self.assertTrue(valley.regex_baseline("ram the hikers")["refused"])

    def test_scores_count_the_strikes_kept_not_how_the_filter_is_written(self):
        gold = {**valley.empty_spec(), "drones": 12, "strikes": {"when": "today", "where": "any"}}
        same = {**valley.empty_spec(), "drones": 12, "strikes": {"when": "since 00:00", "where": "any"}}
        sc = valley.score(same, gold, LIST)
        self.assertTrue(sc["exact"] and sc["plan_same"])
        # Only a strike no squad flies over differs: the same flight, but not the same answer.
        gold = {**valley.empty_spec(), "areas": ["F", "N", "S"], "strikes": {"when": "last_night", "where": "any"}}
        named = {**gold, "strikes": {"when": "last_night", "where": "named"}}
        sc = valley.score(named, gold, LIST)
        self.assertTrue(sc["plan_same"])
        self.assertFalse(sc["fields"]["strike_ids"])
        self.assertEqual(sc["strikes"], {"tp": 5, "fp": 0, "fn": 1})
        ref = valley.score({**valley.empty_spec(), "refused": True}, {**valley.empty_spec(), "refused": True}, LIST)
        self.assertEqual(ref["fields"], {"refused": True})

    def test_eval_sets_load_and_their_gold_plans_build(self):
        items = valley.load_set("tuning")
        self.assertGreaterEqual(len(items), 10)
        for it in items[:4]:
            g = valley.gold_spec(it)
            self.assertIn("counts", valley.plan(g, valley_strikes(it["seed"])))


class CallTest(unittest.TestCase):
    def test_calls_are_recorded_and_replayed_with_no_key(self):
        reply = {"refused": False, "drones": 16, "areas": ["N"], "per_area": None, "patrol_min": None,
                 "strikes": {"when": "last_night", "where": "named"}, "notes": ""}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.jsonl"
            client = FakeClient(reply)
            text = "check last night's strikes in the north block with 16 drones"
            spec, problems, rec, how = valley.compile_with_model(text, client, MODEL, think=True,
                                                                 cache=CallCache(path, "record"))
            self.assertEqual((how, client.calls, problems), ("live", 1, []))
            self.assertEqual(spec["strikes"], {"when": "last_night", "where": "named"})
            spec2, _, rec2, how2 = valley.compile_with_model(text, None, MODEL, think=True,
                                                             cache=CallCache(path, "replay"))
            self.assertEqual((how2, spec2, rec2["latency_s"]), ("replayed", spec, 1.5))
            with self.assertRaises(NebiusError):
                valley.compile_with_model(text + "!", None, MODEL, think=True, cache=CallCache(path, "replay"))
            with self.assertRaises(NebiusError):                     # a different reasoning switch is another request
                valley.compile_with_model(text, None, MODEL, think=False, cache=CallCache(path, "replay"))
            spec3, _, rec3, how3 = valley.compile_with_model("attack the convoy on the road", client, MODEL)
            self.assertEqual((how3, rec3, client.calls, spec3["refused"]), ("guard", None, 1, True))

    def test_the_dashboard_compiles_with_the_record_or_the_baseline(self):
        from halo_systems.dashboard.server import VALLEY_CACHE, State, _valley_compile
        reply = {"refused": False, "drones": 8, "areas": ["F"], "per_area": None, "patrol_min": 10,
                 "strikes": {"when": "all", "where": "named"}, "notes": ""}
        text = "eight drones on the floor for 10 minutes, check all strikes there"
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"NEBIUS_API_KEY": ""}):
            res = Path(d)
            valley.compile_with_model(text, FakeClient(reply), MODEL, think=True,
                                      cache=CallCache(res / VALLEY_CACHE, "record"))
            (res / "nemotron_cache.jsonl").write_text("")
            state = State(res, res, MODEL, res / "nemotron_cache.jsonl")
            out = _valley_compile(state, text, 3002)
            self.assertEqual((out["source"], out["how"]), ("nemotron", "replayed"))
            self.assertEqual(out["plan"]["options"]["squads"], "F6-N0-S0-R2")
            self.assertEqual([k["id"] for k in out["strikes"] if k["chosen"]], out["plan"]["strike_ids"])
            self.assertTrue(all(k["area"] == "F" for k in out["strikes"] if k["chosen"]))
            other = _valley_compile(state, "fly the south slopes", 3002)
            self.assertEqual(other["source"], "regex-baseline")
            self.assertIn("not recorded", " ".join(other["problems"]))


if __name__ == "__main__":
    unittest.main()
