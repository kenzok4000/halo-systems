"""Fly the wildfire valley with squads of each size and write one row per flight.

    PYTHONPATH=src python3 tools/wildfire_valley_squads_sweep.py --seeds 8000-8019 --arms A,B,D \
        --sizes 4,8,16,32,64 --stations legacy --out results/wildfire_valley_squads/runs.jsonl

These are the flags of the committed held-out run (PREREG.md); seeds 8000-8019 are now spent.

Three ways to fly n drones, on the same seeds (results/wildfire_valley_squads/PREREG.md has the plan):

* A, today's flight: one line abreast sweeps the valley floor once and lands (squads=None).
* B, floor patrol: one squad patrols the valley floor on a closed circuit for the patrol (12 minutes); drones
  that do not fit across the floor stay in reserve.
* D, squads per area: the default split ("auto") over the valley floor and the north and south slopes, each
  squad patrolling its own area for the patrol; drones left over stay in reserve.

By default each is flown with the ground station as committed ("legacy") and with the tighter thermal-to-smoke
merge ("tight"). The held-out run flew the legacy station only (--stations legacy): on the development seeds the
tight merge changed no flight's result. Every row carries the own-evidence scoring keys (report_v2). All flights
use the improved controller with no supervisor (improved+fixed), so the squads are compared without a
supervisor's changes.
Rows already in the output are skipped, so an interrupted sweep resumes.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

CONFIG = "improved+fixed"
PATROL_S = 720.0


def options(arm: str, n: int, station: str, patrol_s: float) -> dict:
    opts = {"station": station, "report_v2": True}
    if arm == "B":
        opts.update(squads="auto:F", patrol_s=patrol_s)
    elif arm == "D":
        opts.update(squads="auto", patrol_s=patrol_s)
    elif arm != "A":
        raise ValueError(f"unknown arm {arm!r}")
    return opts


def split_of(arm: str, n: int, patrol_s: float) -> tuple:
    """What actually flies: today's line of n, or the drones per area (the reserve is not flown)."""
    if arm == "A":
        return ("line", n)
    from halo_systems.control.directive import Directive
    from halo_systems.control.squads import allocate
    from halo_systems.eval.scenarios import (SCOUT_ALTITUDE_M, SCOUT_SPACING_M, SCOUT_SPEED_MPS, valley_areas,
                                             wildfire_valley)
    global _SWATH
    if _SWATH is None:
        _SWATH = 2.0 * float(wildfire_valley(0, n=1).sensor.footprint_radius(SCOUT_ALTITUDE_M))
    directive = Directive("line", SCOUT_SPACING_M, SCOUT_SPEED_MPS, SCOUT_ALTITUDE_M, 1.0, source="mission")
    areas = valley_areas() if arm == "D" else [a for a in valley_areas() if a.code == "F"]
    got = allocate(n, areas, directive, _SWATH, patrol_s)
    return ("squads", patrol_s) + tuple((a.code, got.get(a.code, 0)) for a in valley_areas())


_SWATH = None


def fly(job):
    """One flight; returns its rows, one per (n, arm) that flies exactly this (the reserve aside)."""
    seed, n, arm, station, patrol_s, also = job
    from halo_systems.eval.runner import make_controller, make_supervisor
    from halo_systems.eval.scenarios import wildfire_valley
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    heads = [{"scenario": "wildfire_valley", "seed": seed, "n": nn, "arm": aa, "station": station, "config": CONFIG,
              "patrol_s": patrol_s if aa != "A" else None} for nn, aa in [(n, arm)] + also]
    try:
        sc = wildfire_valley(seed, n=n, **options(arm, n, station, patrol_s))
        body = DroneBody()
        ctrl, sup = CONFIG.split("+")
        sim = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body,
                         record_hz=0)
        t0 = time.perf_counter()
        res = sim.run()
        m = dict(res.metrics)
        wall = round(time.perf_counter() - t0, 1)
    except Exception as exc:  # one failed flight must not lose the sweep
        return [{**h, "error": f"{type(exc).__name__}: {exc}"} for h in heads]
    rows = []
    for h in heads:
        row = {**h, "flying": sc.n, "wall_s": wall, **m}
        if (h["n"], h["arm"]) != (n, arm):
            # The same flight: only the reserve differs, and it does not fly.
            row["reserve"] = h["n"] - sc.n
            row["squads"] = "-".join(f"{c}{k}" for c, k in
                                     [(c, sc.squad_counts.get(c, 0)) for c in sc.squad_counts if c != "R"]
                                     + [("R", h["n"] - sc.n)])
            row["same_flight_as"] = {"n": n, "arm": arm}
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/wildfire_valley_squads/runs.jsonl")
    ap.add_argument("--seeds", default="8000-8019")
    ap.add_argument("--sizes", default="4,8,16,32,64")
    ap.add_argument("--arms", default="A,B,D")
    ap.add_argument("--stations", default="legacy,tight")
    ap.add_argument("--patrol-min", type=float, default=PATROL_S / 60.0)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    seeds = []
    for part in a.seeds.split(","):
        lo, _, hi = part.partition("-")
        seeds += list(range(int(lo), int(hi or lo) + 1))
    patrol_s = 60.0 * a.patrol_min
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["seed"], r["n"], r["arm"], r["station"], r["patrol_s"]))
    # One flight per (seed, station, what flies): arms and sizes that fly the same squads share it.
    groups: dict = {}
    for n in map(int, a.sizes.split(",")):
        for arm in a.arms.split(","):
            key = split_of(arm, n, patrol_s)
            for st in a.stations.split(","):
                for s in seeds:
                    if (s, n, arm, st, patrol_s if arm != "A" else None) not in done:
                        groups.setdefault((s, st, key), []).append((n, arm))
    jobs = []
    for (s, st, key), members in groups.items():
        members.sort(key=lambda m: (m[1], m[0]))
        (n, arm), also = members[0], members[1:]
        jobs.append((s, n, arm, st, patrol_s, also))
    # Longest first: the biggest patrols, then today's flight with few drones (a long single pass).
    jobs.sort(key=lambda j: (j[2] == "A", j[1] if j[2] == "A" else -j[1]))
    print(f"{len(jobs)} flights for {sum(1 + len(j[5]) for j in jobs)} rows", flush=True)
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as pool, out.open("a") as f:
        futures = [pool.submit(fly, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):
            rows = fut.result()
            for row in rows:
                f.write(json.dumps(row) + "\n")
            f.flush()
            row = rows[0]
            print(f"{k}/{len(jobs)} seed {row['seed']} n {row['n']} {row['arm']} {row['station']}"
                  f"{' (+' + str(len(rows) - 1) + ' rows)' if len(rows) > 1 else ''}"
                  f"{' ERROR ' + row['error'] if 'error' in row else ''} ({time.perf_counter() - t0:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
