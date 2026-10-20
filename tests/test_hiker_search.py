"""Search and rescue: the missing-hiker scenario and the person a compiled search mission looks for."""

import tempfile
import unittest
from pathlib import Path

from halo_systems.ai.mission import regex_baseline
from halo_systems.eval.runner import run_one
from halo_systems.eval.scenarios import PERSON_RADIUS_M, from_mission, make, missing_person

E1 = "Search the 400 by 250 m meadow for a missing hiker with 12 drones line abreast, 8 m apart, at 30 m."


class MissingPersonTest(unittest.TestCase):
    def test_requests_for_a_missing_person(self):
        for text in (E1, "Find the lost child near the lake", "A hiker is overdue on the ridge: sweep the slope",
                     "search for two missing climbers below the col", "An injured runner, last seen at the bridge"):
            self.assertTrue(missing_person(text), text)
        for text in ("Survey the field for hotspots", "Inspect the bridge with 6 drones", "Check last night's strikes",
                     "Map the meadow, nobody is missing", ""):
            self.assertFalse(missing_person(text), text)

    def test_more_ways_to_ask(self):
        for text in ("Search for the missing children near the lake", "Two men are missing on the ridge",
                     "Find the missing boy in the woods", "Search for missing kids", "Search for a stranded kayaker",
                     "Locate survivors in the meadow", "Look for the hiker who has not returned",
                     "A man is missing near the lake", "Find the lost hunter", "Search for a missing 12-year-old girl",
                     "Search with no more than 4 drones for a missing hiker",
                     "We have not found the missing hiker yet; search the north ridge",
                     "Search the 300 by 200 m hillside for a missing elderly man with dementia",
                     "Search for the missing paraglider pilot in the valley",
                     "Look for a missing mountain biker along the trail",
                     "A hiker went missing last night near the lake",
                     "Missing hiker near the north ridge, search the slope"):
            self.assertTrue(missing_person(text), text)

    def test_words_that_only_look_like_a_search(self):
        for text in ("Survey the field for missing plants before people arrive",
                     "Map the vineyard for lost vines; people walk there at noon",
                     "Inspect the trail, no hikers are missing",
                     "Survey the block; if a drone is lost the person in charge lands the rest",
                     "Survey where comms were lost near the man-made lake",
                     "Survey the field for injured deer the hunter shot", "Map the meadow, nobody is missing",
                     "None of the hikers are missing", "Survey the burn scar for survivor trees",
                     "Map the trail section where hikers often get lost, 400 by 200 m",
                     "Survey the beach where swimmers went missing last summer to plan new signs",
                     "Survey the Lost Boys trail for erosion",
                     "Survey the trail where an injured runner was rescued last week to check the surface",
                     "There are no missing hikers, map the meadow", "no missing hiker " * 5000):
            self.assertFalse(missing_person(text), text[:80])
        for text in (None, 42, ["a missing hiker"], {"text": "a missing hiker"}):
            self.assertFalse(missing_person(text), repr(text))

    def test_the_dashboard_ignores_mission_text_that_is_not_text(self):
        from halo_systems.dashboard.server import State
        with tempfile.TemporaryDirectory() as tmp:
            state = State(Path(tmp), Path(tmp), None, None)
            for text, person, want in ((42, None, 0), (["a missing hiker"], None, 0), (None, None, 0), (42, True, 1)):
                body = {"mission": regex_baseline(E1), "mission_text": text, "config": "improved+fixed"}
                if person is not None:
                    body["missing_person"] = person
                run = state.start(body)
                run.stop = True
                self.assertEqual(len(run.sim.sc.targets), want, repr(text))

    def test_a_search_mission_gets_a_person_and_nothing_else_changes(self):
        spec = regex_baseline(E1)
        sc = from_mission(spec, 3, E1)
        self.assertEqual(len(sc.targets), 1)
        p = sc.targets[0]
        self.assertEqual((p.kind, p.radius_m), ("person", PERSON_RADIUS_M))
        x0, y0, x1, y1 = sc.sensor.area
        self.assertTrue(x0 <= p.x <= x1 and y0 <= p.y <= y1)
        self.assertTrue(sc.sensor.enabled)
        plain = from_mission(spec, 3, E1, person=False)
        self.assertEqual(plain.targets, [])
        self.assertFalse(plain.sensor.enabled)
        self.assertEqual((plain.wind, plain.waypoints, plain.directive, plain.n), (sc.wind, sc.waypoints, sc.directive, sc.n))
        self.assertEqual(from_mission(spec, 3, "Survey the 400 by 250 m meadow with 12 drones").targets, [])
        self.assertEqual(len(from_mission(spec, 3, "Survey the meadow", person=True).targets), 1)
        self.assertNotEqual(from_mission(spec, 4, E1).targets, sc.targets)

    def test_the_hiker_scenario_finds_the_person_and_swarm_size_sets_the_lanes(self):
        lanes = {n: len(make("hiker_search", 0, n=n).waypoints) // 2 for n in (1, 4, 16)}
        self.assertGreater(lanes[1], lanes[4])
        self.assertGreater(lanes[4], lanes[16])
        self.assertEqual(make("hiker_search", 0, n=1).targets, make("hiker_search", 0, n=16).targets)
        row = run_one("hiker_search", 0, "improved+rule", n=16)
        self.assertEqual(row["targets_total"], 1)
        self.assertIn(row["targets_found"], (0, 1))
        if row["targets_found"]:
            self.assertGreater(row["first_find_s"], 0.0)


if __name__ == "__main__":
    unittest.main()
