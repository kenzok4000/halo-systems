"""Aggregation and paired comparisons for benchmark rows (numpy only).

Confidence intervals are percentile bootstraps over seeds (10,000
resamples, fixed generator seed so reports are reproducible). Paired
comparisons use the same seeds for both configurations, which the split
random streams make meaningful: both face the same wind, noise and layout.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

# Metric -> (label, better direction: -1 lower is better, +1 higher is better)
METRICS = {
    "time_formed_pct": ("Time in formation (%)", +1),
    "formation_error_mean_m": ("Formation error, mean (m)", -1),
    "formation_error_p95_m": ("Formation error, 95th pct (m)", -1),
    "crashes": ("Crashes per run", -1),
    "near_misses": ("Near misses per run", -1),
    "min_separation_m": ("Closest approach (m)", +1),
    "completed": ("Missions completed (%)", +1),
    "completion_time_s": ("Completion time (s)", -1),
    "energy_wh_per_km": ("Energy (Wh per drone-km)", -1),
    "drones_retired": ("Drones retired (low battery)", -1),
    "transition_settle_s": ("Formation change time (s)", -1),
    "recovery_time_s": ("Recovery after disturbance (s)", -1),
    "directive_changes": ("Directive changes", 0),
    "time_faster_than_plan_pct": ("Time faster than planned (%)", 0),
    "ctrl_ms_mean": ("Controller time per step (ms)", -1),
    "ai_calls": ("Model calls", 0),
    "ai_fallbacks": ("Fallbacks to rules", -1),
    "ai_latency_mean_s": ("Model latency, mean (s)", -1),
    # Scouting scenarios (a ground sensor and targets to find).
    "targets_found_pct": ("Targets found (%)", +1),
    "first_find_s": ("Time to first find (s)", -1),
    "all_found_s": ("Time to find all (s)", -1),
    "area_covered_pct": ("Area covered (%)", +1),
    "coverage_90pct_s": ("Time to 90% coverage (s)", -1),
    "coverage_ha_per_min": ("Coverage rate (ha/min)", +1),
}
SCOUT_METRICS = ("targets_found_pct", "first_find_s", "all_found_s", "area_covered_pct", "coverage_90pct_s",
                 "coverage_ha_per_min")


def value(row: dict, metric: str):
    if metric == "crashes":
        return float(row.get("collisions", 0) * 2 + row.get("obstacle_crashes", 0) + row.get("ground_crashes", 0))
    v = row.get(metric)
    if v is None:
        return None
    if isinstance(v, bool):
        return 100.0 if v else 0.0
    return float(v)


def bootstrap_ci(x, stat=np.mean, n=10000, alpha=0.05, seed=0):
    x = np.asarray([v for v in x if v is not None and not (isinstance(v, float) and math.isnan(v))], dtype=float)
    if len(x) == 0:
        return None, None, None
    if len(x) == 1:
        return float(x[0]), float(x[0]), float(x[0])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    stats = stat(x[idx], axis=1)
    return float(stat(x)), float(np.percentile(stats, 100 * alpha / 2)), float(np.percentile(stats, 100 * (1 - alpha / 2)))


def group(rows, keys=("scenario", "config")):
    out = defaultdict(list)
    for r in rows:
        if "error" in r:
            continue
        out[tuple(r.get(k) for k in keys)].append(r)
    return out


def summarise(rows, metrics=None) -> dict:
    """{(scenario, config): {metric: {mean, lo, hi, n}}}"""
    metrics = metrics or list(METRICS)
    out = {}
    for key, rs in group(rows).items():
        out[key] = {}
        for m in metrics:
            vals = [value(r, m) for r in rs]
            vals = [v for v in vals if v is not None]
            mean, lo, hi = bootstrap_ci(vals)
            out[key][m] = {"mean": mean, "lo": lo, "hi": hi, "n": len(vals)}
    return out


def paired(rows, scenario: str, a: str, b: str, metric: str) -> dict | None:
    """Difference b - a over seeds both have, with a bootstrap CI and win/tie/loss for b."""
    ra = {r["seed"]: value(r, metric) for r in rows if r.get("scenario") == scenario and r.get("config") == a and "error" not in r}
    rb = {r["seed"]: value(r, metric) for r in rows if r.get("scenario") == scenario and r.get("config") == b and "error" not in r}
    seeds = sorted(s for s in set(ra) & set(rb) if ra[s] is not None and rb[s] is not None)
    if not seeds:
        return None
    d = np.array([rb[s] - ra[s] for s in seeds])
    mean, lo, hi = bootstrap_ci(d)
    sign = METRICS.get(metric, ("", -1))[1] or -1
    tol = 1e-9 + (0.01 * np.mean(np.abs([ra[s] for s in seeds])) if metric not in ("crashes", "completed") else 0.0)
    better = int(np.sum(sign * d > tol))
    worse = int(np.sum(sign * d < -tol))
    return {"mean_diff": mean, "lo": lo, "hi": hi, "n": len(seeds), "b_better": better, "b_worse": worse,
            "ties": len(seeds) - better - worse, "a_mean": float(np.mean([ra[s] for s in seeds])),
            "b_mean": float(np.mean([rb[s] for s in seeds]))}
