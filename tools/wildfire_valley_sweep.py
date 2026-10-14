"""Fly the wildfire valley on held-out seeds and write one row per flight.

    PYTHONPATH=src python3 tools/wildfire_valley_sweep.py --out results/wildfire_valley/runs.jsonl

Seeds 4000-4019 were never flown while the scenario and the station were built (seeds 2000-2011 and 3000-3019
were used for that), so the station's settings were not tuned on them. Each seed is flown with 1, 4 and 16 drones
under the forecast alert rule, and with 16 drones under the distance rule (the ablation). Rows already in the
output are skipped, so an interrupted sweep resumes.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

CONFIG = "improved+rule"


def fly(job):
    seed, n, rule = job
    from halo_systems.eval.runner import make_controller, make_supervisor
    from halo_systems.eval.scenarios import wildfire_valley
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    try:
        sc = wildfire_valley(seed, n=n, alert_rule=rule)
        body = DroneBody()
        ctrl, sup = CONFIG.split("+")
        sim = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body,
                         record_hz=0)
        t0 = time.perf_counter()
        res = sim.run()
        return {"scenario": "wildfire_valley", "seed": seed, "n": n, "alert_rule": rule, "config": CONFIG,
                "wall_s": round(time.perf_counter() - t0, 1), **res.metrics}
    except Exception as exc:  # one failed flight must not lose the sweep
        return {"scenario": "wildfire_valley", "seed": seed, "n": n, "alert_rule": rule, "config": CONFIG,
                "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/wildfire_valley/runs.jsonl")
    ap.add_argument("--seeds", default="4000-4019")
    ap.add_argument("--sizes", default="1,4,16")
    ap.add_argument("--ablation-sizes", default="16")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    lo, _, hi = a.seeds.partition("-")
    seeds = list(range(int(lo), int(hi or lo) + 1))
    jobs = [(s, n, "forecast") for n in map(int, a.sizes.split(",")) for s in seeds]
    jobs += [(s, n, "distance") for n in map(int, a.ablation_sizes.split(",")) if n for s in seeds]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["seed"], r["n"], r["alert_rule"]))
    jobs = [j for j in jobs if j not in done]
    jobs.sort(key=lambda j: j[1])          # one-drone flights are the longest: start them first
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as pool, out.open("a") as f:
        futures = [pool.submit(fly, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{k}/{len(jobs)} seed {row['seed']} n {row['n']} {row['alert_rule']}"
                  f"{' ERROR ' + row['error'] if 'error' in row else ''} ({time.perf_counter() - t0:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
