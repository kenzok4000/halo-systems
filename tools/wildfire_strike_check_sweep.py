"""Fly the wildfire valley with and without a strike-check team and write one row per flight.

    PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 3000-3011 --sizes 8,16 \
        --arms none,nearest,rule --out /tmp/strike_dev.jsonl

Arms, each flying all n drones (8 or 16) for the same 12-minute patrol (results/strike_checks/PREREG.md has the
plan):

* none: squads per area, the default split of all n (the squads sweep's arm D); the strike list is attached but
  cues nothing, so this is the same flight as D.
* nearest, rule, nemotron: a strike-check team (sim/strike_check.py) of 4 drones, plus any the area split of the
  other n - 4 would keep in reserve, and the default split of the rest over the areas; the team's next strike is
  picked by "nearest first", the hand-written triage rule, or Nemotron (supervisor/strikes.py).

Nemotron flights record every reply in ``--cache`` (and replay from it with ``--cache-mode replay``); their calls
go through the Nebius client, so the shared ledger and its budget cap (HALO_LEDGER, HALO_BUDGET_USD) apply.
Ground station legacy, controller improved, no directive supervisor, report_v2 scoring, as the squads sweep.
Rows already in the output are skipped, so an interrupted sweep resumes.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PATROL_S = 720.0
CONFIG = "improved+fixed"


def scenario(seed: int, n: int, arm: str, cache: str | None, cache_mode: str, think: bool):
    from halo_systems.eval.scenarios import wildfire_valley
    if arm == "none":
        return wildfire_valley(seed, n=n, squads="auto", patrol_s=PATROL_S, report_v2=True, strikes="all")
    checks = {"picker": arm}
    if arm == "nemotron":
        if not cache:
            raise ValueError("Nemotron flights need --cache, so they can be replayed")
        checks.update(cache=cache, cache_mode=cache_mode, think=think)
    return wildfire_valley(seed, n=n, patrol_s=PATROL_S, report_v2=True, checks=checks)


def fly(job):
    seed, n, arm, cache, cache_mode, think = job
    head = {"scenario": "wildfire_valley", "seed": seed, "n": n, "arm": arm, "config": CONFIG, "patrol_s": PATROL_S,
            **({"think": think, "cache_mode": cache_mode} if arm == "nemotron" else {})}
    try:
        from halo_systems.eval.runner import make_controller, make_supervisor
        from halo_systems.sim.body import DroneBody
        from halo_systems.sim.engine import Simulation
        sc = scenario(seed, n, arm, cache, cache_mode, think)
        body = DroneBody()
        ctrl, sup = CONFIG.split("+")
        sim = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body,
                         record_hz=0)
        t0 = time.perf_counter()
        res = sim.run()
        if sc.n != n:
            raise RuntimeError(f"{sc.n} drones fly, not {n}")
        return {**head, "flying": sc.n, "squads": "-".join(f"{c}{k}" for c, k in sc.squad_counts.items() if k),
                "wall_s": round(time.perf_counter() - t0, 1), **res.metrics}
    except Exception as exc:  # one failed flight must not lose the sweep
        return {**head, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--sizes", default="8,16")
    ap.add_argument("--arms", default="none,nearest,rule,nemotron")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=None, help="Nemotron reply cache (JSON lines)")
    ap.add_argument("--cache-mode", default="record", choices=("record", "replay"))
    ap.add_argument("--no-think", action="store_true", help="Nemotron without its reasoning mode")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    seeds = []
    for part in a.seeds.split(","):
        lo, _, hi = part.partition("-")
        seeds += list(range(int(lo), int(hi or lo) + 1))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["seed"], r["n"], r["arm"]))
    jobs = [(s, n, arm, a.cache, a.cache_mode, not a.no_think) for n in map(int, a.sizes.split(",")) for s in seeds
            for arm in a.arms.split(",") if (s, n, arm) not in done]
    print(f"{len(jobs)} flights to fly ({len(done)} already done)", flush=True)
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
        futs = [ex.submit(fly, j) for j in jobs]
        for f in as_completed(futs):
            row = f.result()
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(row["seed"], row["n"], row["arm"], row.get("error") or
                  f"classes {row.get('fire_classes')} checks {row.get('strike_checks')} "
                  f"fire strikes {row.get('strike_checks_fire')} unfound {row.get('strike_checks_fire_unfound')}",
                  flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
