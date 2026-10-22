"""Pre-signed alert rules with dispatch cards, and crew outputs as map files and size-up messages."""

import json
import unittest
import xml.dom.minidom

import numpy as np

from halo_systems.dashboard.server import _valley_options
from halo_systems.eval.runner import make_controller, make_supervisor, run_one
from halo_systems.eval.scenarios import make
from halo_systems.sim import crew_export
from halo_systems.sim.body import DroneBody
from halo_systems.sim.engine import Simulation
from halo_systems.sim.wildfire import SMOULDER_AREA_M2, AlertCheck, AlertPolicy


class PresignedTest(unittest.TestCase):
    def test_smoulders_go_to_dispatch_and_alerts_name_their_rule(self):
        row, res = run_one("wildfire_valley", 2003, "improved+rule", n=16, keep_result=True, duration_s=120.0,
                           options={"alert_rule": "presigned", "report_v2": True})
        cards = [e for e in res.events if e["kind"] == "dispatch_card"]
        self.assertGreaterEqual(len(cards), 1)
        self.assertTrue(all("(Simulated)" in c["text"] and "simulated" in c["to"] for c in cards))
        self.assertTrue(all("truth" not in c for c in cards))                    # scoring stays off the stream
        new = [c for c in cards if not c["update"]]
        self.assertEqual(len({c["fire"] for c in new}), len(new))                # one new-fire card per fire
        self.assertEqual(row["dispatch_cards"], len(new))
        for a in row["alert_table"]:
            self.assertEqual(a["rule"], f"T{a['level']}")
            self.assertFalse(a["smouldering_only"] and any(d is not None for d in a["drivers"]))
        found = [f for f in row["fire_table"] if f["located"]]
        self.assertTrue(any(f.get("dispatch_t") is not None for f in found))

    def test_the_check_wants_the_named_rule(self):
        pol = AlertPolicy(rule="presigned")
        check = AlertCheck(pol)
        fc = {"id": 3}
        alert = {"message": "x", "channel": "cell broadcast (simulated)", "hazard": "wildfire", "area": "a",
                 "action": "go", "expires": "in 6 hours", "authority": "A", "level": 3,
                 "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]], "phones": [1], "expires_in_s": 6 * 3600,
                 "forecast_id": 3, "max_prob": 0.5, "zone": "F1", "t": 0.0}
        self.assertIn("pre-signed rule T3", " ".join(check.check(alert, fc, []).reasons))
        alert["trigger"] = pol.trigger(3)
        self.assertTrue(check.check(alert, fc, []).ok)
        self.assertIn("3 hours", alert["trigger"]["text"])
        alert["max_prob"] = 0.1
        self.assertFalse(check.check(alert, fc, []).ok)
        self.assertIn("1 hour ", AlertPolicy().trigger(4)["text"] + " ")

    def test_a_fix_that_moves_gets_an_update_card(self):
        sc = make("wildfire_valley", 2001, n=4, options={"alert_rule": "presigned", "report_v2": True})
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=2001,
                         body=body, record_hz=0)
        wf = sim.wildfire
        f0, f1 = wf.fires[0], wf.fires[1]
        loc = {"id": 0, "x": f0.x + 2.0, "y": f0.y, "err_m": 5.0, "area_m2": 10.0, "method": "smoke"}
        fc = {"located": [loc], "brief": []}
        wf._dispatch(sim, fc)
        wf._dispatch(sim, fc)                                                   # nothing new, no card
        loc["x"] = f0.x + 25.0                                                  # moved 23 m: within 30 m, no card
        wf._dispatch(sim, fc)
        self.assertEqual(len(wf.dispatch_log), 1)
        self.assertEqual(wf.dispatch_log[0]["truth"], wf._match_truth(f0.x + 2.0, f0.y, 0.0)[0])
        loc.update(x=f1.x, y=f1.y)                                              # the fix moves onto another fire
        wf._dispatch(sim, fc)
        card = wf.dispatch_log[-1]
        self.assertEqual(len(wf.dispatch_log), 2)
        self.assertTrue(card["update"])
        self.assertGreater(card["moved_m"], 30.0)
        self.assertEqual(card["status"], "smouldering")
        self.assertIn("moved", card["text"])
        self.assertEqual(card["truth"], wf._match_truth(f1.x, f1.y, 0.0)[0])  # scored where the crew is sent
        loc["area_m2"] = 2.0 * SMOULDER_AREA_M2
        wf._dispatch(sim, fc)
        self.assertEqual(wf.dispatch_log[-1]["status"], "spreading")
        loc.update(area_m2=10.0, x=f1.x + 40.0)                                 # smaller estimate, moved again
        wf._dispatch(sim, fc)
        self.assertEqual(len(wf.dispatch_log), 4)
        self.assertEqual(wf.dispatch_log[-1]["status"], "spreading")            # a card never takes spreading back

    def test_committed_rule_is_unchanged_by_default(self):
        sc = make("wildfire_valley", 2003, n=4)
        self.assertEqual(sc.wildfire.alerts.rule, "forecast")
        self.assertNotIn("station", sc.wildfire.summary(sc.world.terrain))


class SmoulderAlertsTest(unittest.TestCase):
    """Pre-signed rules whose Level 3 trigger also counts smoulders (smoulder_alerts)."""

    def _station(self, **options):
        sc = make("wildfire_valley", 2001, n=4, options=options)
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("fixed"), seed=2001,
                         body=body, record_hz=0)
        return sim, sim.wildfire

    @staticmethod
    def _smoulder_forecast(wf, near):
        """A forecast with one located smoulder: nothing spreading reaches a phone, but if the smoulder took off
        now it would reach the phones ``near`` in an hour."""
        members, n = 20, len(wf.phones)
        act, pot = np.full((members, n), np.inf), np.full((members, n), np.inf)
        pot[:, near] = 3600.0
        return {"id": 0, "t": 0.0, "active": {"phone_arrivals": act}, "potential": {"phone_arrivals": pot},
                "located": []}

    def test_level_3_counts_the_smoulder_only_when_asked(self):
        for options, want in (({"alert_rule": "forecast"}, 3), ({"alert_rule": "presigned"}, 0),
                              ({"alert_rule": "presigned", "smoulder_alerts": True}, 3)):
            sim, wf = self._station(**options)
            near = np.nonzero(wf.zone_of_phone == wf.zone_of_phone[0])[0]
            fc = self._smoulder_forecast(wf, near)
            level, prob, _ = wf._zone_targets(fc)
            self.assertTrue(np.all(level[near] == want), options)
            wf.forecasts.append(fc)
            wf._draft_alerts(sim, fc)
            sent = [a for a in wf.alerts if a["state"] == "delivered"]
            self.assertEqual(len(sent), 1 if want else 0, options)
            if want and options["alert_rule"] == "presigned":
                trig = sent[0]["trigger"]
                self.assertEqual(trig["rule"], "T3")
                self.assertIn("smouldering", trig["text"])
                self.assertTrue(sent[0]["check"]["ok"])

    def test_triggers_summary_and_validation(self):
        pol = AlertPolicy(rule="presigned", smoulder_alerts=True)
        self.assertIn("smouldering", pol.trigger(3)["text"])
        self.assertEqual(pol.trigger(4), AlertPolicy(rule="presigned").trigger(4))   # evacuations: spreading only
        self.assertEqual(pol.trigger(5), AlertPolicy(rule="presigned").trigger(5))
        self.assertNotIn("smouldering", AlertPolicy(rule="presigned").trigger(3)["text"])
        sc = make("wildfire_valley", 2001, n=4, options={"alert_rule": "presigned", "smoulder_alerts": True})
        self.assertTrue(sc.wildfire.summary(sc.world.terrain)["smoulder_alerts"])
        sc = make("wildfire_valley", 2001, n=4, options={"alert_rule": "presigned"})
        self.assertNotIn("smoulder_alerts", sc.wildfire.summary(sc.world.terrain))   # older runs stay identical
        with self.assertRaises(ValueError):
            self._station(alert_rule="forecast", smoulder_alerts=True)

    def test_dashboard_presigned_counts_smoulders_unless_told_not_to(self):
        self.assertTrue(_valley_options({"alert_rule": "presigned"})["smoulder_alerts"])        # the entry's design
        self.assertTrue(_valley_options({"alert_rule": "presigned", "smoulder_alerts": True})["smoulder_alerts"])
        self.assertNotIn("smoulder_alerts", _valley_options({"alert_rule": "presigned", "smoulder_alerts": False}))
        self.assertNotIn("smoulder_alerts", _valley_options({"alert_rule": "forecast", "smoulder_alerts": True}))
        self.assertNotIn("smoulder_alerts", _valley_options({}))


class CrewExportTest(unittest.TestCase):
    def test_the_cone_is_the_brief_s_half_width_either_side(self):
        # The brief's cone_deg is scored and drawn as plus or minus; the map file's sector must match.
        pts = crew_export._cone((0.0, 0.0), 90.0, 20.0, 100.0)
        edge = pts[1:-1]
        bearings = sorted((np.degrees(np.arctan2(x, y)) % 360.0) for x, y in edge)
        self.assertAlmostEqual(bearings[0], 70.0, places=6)
        self.assertAlmostEqual(bearings[-1], 110.0, places=6)

    def test_briefs_become_map_files_and_short_messages(self):
        sc = make("wildfire_valley", 2003, n=16, duration_s=120.0)
        body = DroneBody()
        sim = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=2003,
                         body=body, record_hz=0)
        sim.run()
        fc = sim.wildfire.forecasts[-1]
        g = crew_export.geojson(fc, (37.0, -119.0), sc.wildfire.places)
        json.dumps(g)
        kinds = [f["properties"]["kind"] for f in g["features"]]
        self.assertEqual(kinds.count("fire"), len(fc["brief"]))
        for f in g["features"]:
            self.assertTrue(f["properties"]["simulated"])
            lon, lat = f["geometry"]["coordinates"] if f["geometry"]["type"] == "Point" else \
                f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "LineString" else \
                f["geometry"]["coordinates"][0][0]
            self.assertAlmostEqual(lat, 37.0, delta=0.02)
            self.assertAlmostEqual(lon, -119.0, delta=0.02)
            if f["geometry"]["type"] == "Polygon":
                ring = f["geometry"]["coordinates"][0]
                self.assertEqual(ring[0], ring[-1])
            if f["properties"]["kind"] == "fire":
                radio = f["properties"]["radio"]
                self.assertTrue(radio.startswith("Wildland fire."))
                self.assertLessEqual(len(radio), 360)
                self.assertIn("initial attack requested", radio)
        xml.dom.minidom.parseString(crew_export.kml(fc, places=sc.wildfire.places))
        # 100 m north and east of the anchor
        lon, lat = crew_export.lonlat(100.0, 100.0, (0.0, 0.0))
        self.assertAlmostEqual(lat * crew_export.M_PER_DEG_LAT, 100.0, delta=0.02)
        self.assertAlmostEqual(lon * crew_export.M_PER_DEG_LAT, 100.0, delta=0.02)
        self.assertGreater(SMOULDER_AREA_M2, 0)


if __name__ == "__main__":
    unittest.main()
