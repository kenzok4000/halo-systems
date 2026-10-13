"""Report generation: labels, sweep variants and the sections built from rows."""

import json
import tempfile
import unittest
from pathlib import Path

from swarmlab.eval.report import build, label, short_label, split_variants


def row(scenario, config, seed, **kw):
    r = {"scenario": scenario, "config": config, "seed": seed, "n": 16, "time_formed_pct": 90.0, "collisions": 0,
         "obstacle_crashes": 0, "ground_crashes": 0, "near_misses": 0, "completed": True, "completion_time_s": 80.0,
         "energy_wh_per_km": 7.0, "drones_retired": 0, "formation_error_mean_m": 0.5}
    r.update(kw)
    return r


class ReportTest(unittest.TestCase):
    def test_labels_tell_variants_apart(self):
        nano = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
        self.assertNotEqual(label(f"improved+nemotron:{nano}"), label(f"improved+nemotron-think:{nano}"))
        self.assertIn("reasoning on", label(f"improved+nemotron-think:{nano}"))
        self.assertEqual(label("improved-no-cbf+rule"), "Improved without cbf + rule supervisor")

    def test_chart_labels_are_short_and_still_distinct(self):
        nano, sup = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/nemotron-3-super-120b-a12b"
        self.assertEqual(short_label(f"improved+nemotron:{nano}"), "Improved + Nemotron Nano")
        self.assertEqual(short_label(f"improved+nemotron-think:{nano}"), "Improved + Nemotron Nano, reasoning")
        self.assertEqual(short_label(f"improved+nemotron:{sup}"), "Improved + Nemotron Super")
        self.assertEqual(short_label("improved-no-cbf-filter+rule"), "No CBF, state filter + rules")
        self.assertEqual(short_label("original+fixed"), "Original + fixed plan")

    def test_sweeps_are_split_not_averaged(self):
        rows = [row("open_transit", "improved+rule", 0, n=8), row("open_transit", "improved+rule", 0, n=32),
                row("forest_trail", "improved+rule", 0)]
        names = {r["scenario"] for r in split_variants(rows)}
        self.assertEqual(names, {"open_transit, 8 drones", "open_transit, 32 drones", "forest_trail"})

    def test_build_writes_usage_and_separation(self):
        rows = []
        for s in range(5):
            rows.append(row("low_battery", "improved+rule", s, completion_time_s=139.0 + s % 2))
            rows.append(row("low_battery", "improved+nemotron:m", s, completion_time_s=125.0 + s % 2, ai_calls=20,
                            ai_fallbacks=1, ai_latency_mean_s=1.2, ai_latency_p95_s=1.5, ai_tokens=40000,
                            ai_cost_usd=0.005))
        rows.append({"scenario": "low_battery", "config": "improved+nemotron:m", "seed": 9, "n": 16, "error": "boom"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            text = build(path, Path(tmp) / "out").read_text()
        self.assertIn("1 failed runs (listed at the end)", text)
        self.assertIn("## Nemotron usage", text)
        self.assertIn("5 of 100 (5.0%)", text)
        self.assertIn("better on Completion time (s) 139.4 → 125.4", text)
        self.assertIn("boom", text)

    def test_sweep_without_the_middle_config_is_still_compared(self):
        rows = [row("forest_trail", "original+fixed", s, obstacle_crashes=8) for s in range(5)]
        rows += [row("forest_trail", "improved+rule", s) for s in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            text = build(path, Path(tmp) / "out").read_text()
        self.assertIn("Improved + rule supervisor vs Original controller: better on Crashes per run 8 → 0", text)


if __name__ == "__main__":
    unittest.main()
