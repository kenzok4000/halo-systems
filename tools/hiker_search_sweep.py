"""Search and rescue measured (T11): how soon a swarm finds a missing hiker, against swarm size.

    PYTHONPATH=src python3 tools/hiker_search_sweep.py fly --seeds 9200-9219 --out results/hiker_search/runs.jsonl
    PYTHONPATH=src python3 tools/hiker_search_sweep.py report --runs results/hiker_search/runs.jsonl
    PYTHONPATH=src python3 tools/hiker_search_sweep.py film --seeds 0-9 --out results/hiker_search/film.json

``fly`` flies the ``hiker_search`` scenario (a 400 x 250 m meadow and forest edge, one person-sized heat
signature, half the time under tree crowns) with 1, 4 and 16 drones on each seed, today's controller and the rule
supervisor, plus one secondary row: 1 drone with the planned speed held (no supervisor), because the rule supervisor
may speed a lone drone up to save charge. A rerun skips rows already written. ``report`` writes REPORT.md and
summary.json next to the runs.
``film`` flies the Mission tab's hiker request (as compiled, from the recorded plan's fields) on a few seeds and
lists when the person is found, for choosing the filmed seed.
"""

from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

SIZES = (1, 4, 16)
CONFIG = "improved+rule"
# What ``fly`` flies on every seed: (drones, config). The last row is secondary: one drone at the planned speed,
# no supervisor, so the report can tell the number of lanes apart from the rule supervisor's speed-up.
RUNS = tuple((n, CONFIG) for n in SIZES) + ((1, "improved+fixed"),)
LABELS = {CONFIG: "{n}", "improved+fixed": "{n}, speed held (no supervisor)"}
E1 = "Search the 400 by 250 m meadow for a missing hiker with 12 drones line abreast, 8 m apart, at 30 m."


def _seeds(text: str) -> list[int]:
    a, _, b = text.partition("-")
    return list(range(int(a), int(b or a) + 1))


def fly_one(job):
    seed, n, config = job
    from halo_systems.eval.runner import run_one
    from halo_systems.eval.scenarios import make
    sc = make("hiker_search", seed, n=n)
    p = sc.targets[0]
    # keep_result only hands back the events as well; the flight is the same.
    row, res = run_one("hiker_search", seed, config, n=n, keep_result=True)
    keep = ("targets_found", "first_find_s", "duration_s", "completed", "completion_time_s", "progress_m",
            "area_covered_pct", "coverage_90pct_s", "drones_lost", "drones_retired", "collisions", "energy_wh",
            "time_faster_than_plan_pct", "directive_changes", "wall_s")
    ordered = [sc.directive.speed_mps] + [e["speed_mps"] for e in res.events if e["kind"] == "directive"]
    return {"seed": seed, "n": n, "config": config, "person_cover": p.cover, "under_trees": p.cover > 0.0,
            **{k: row.get(k) for k in keep}, "planned_speed_mps": sc.directive.speed_mps,
            "max_ordered_speed_mps": max(ordered)}


def cmd_fly(a) -> int:
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["seed"], r["n"], r.get("config", CONFIG)))
    jobs = [(s, n, c) for s in _seeds(a.seeds) for n, c in RUNS if (s, n, c) not in done]
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as f:
        futs = [ex.submit(fly_one, j) for j in jobs]
        for k, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"{k}/{len(jobs)} seed {r['seed']} n {r['n']} {r['config']} found {r['targets_found']} at "
                  f"{r['first_find_s']}", flush=True)
    return 0


def _key(n: int, config: str) -> str:
    return str(n) if config == CONFIG else f"{n} {config}"


def quantile(times: list, p: float):
    """Nearest-rank quantile over all flights, a miss counting as never found (inf); None if it lands on a miss."""
    if not times:
        return None
    xs = sorted(times)
    v = xs[max(0, math.ceil(round(p * len(xs), 9)) - 1)]
    return None if math.isinf(v) else v


def mean_speed(r: dict):
    """Distance along the sweep path over the time it took (to the sweep's end, or to the flight's end if unfinished)."""
    t = r["completion_time_s"] if r.get("completed") else r["duration_s"]
    return r["progress_m"] / t if r.get("progress_m") is not None and t else None


def summarise(rows: list[dict]) -> dict:
    out = {}
    for n, config in RUNS:
        rs = [r for r in rows if r["n"] == n and r.get("config", CONFIG) == config]
        found = [r for r in rs if r["targets_found"]]
        # Every flight counts: a miss is never found, so a quantile that lands on one is "not reached".
        t = [r["first_find_s"] / 60.0 if r["targets_found"] else math.inf for r in rs]
        q50, q90 = quantile(t, 0.5), quantile(t, 0.9)
        speeds = [v for v in (mean_speed(r) for r in rs) if v is not None]
        ordered = [r["max_ordered_speed_mps"] for r in rs if r.get("max_ordered_speed_mps") is not None]
        out[_key(n, config)] = {
            "n": n, "config": config, "flights": len(rs), "found": len(found),
            "found_open": sum(1 for r in found if not r["under_trees"]),
            "open": sum(1 for r in rs if not r["under_trees"]),
            "found_under_trees": sum(1 for r in found if r["under_trees"]),
            "under_trees": sum(1 for r in rs if r["under_trees"]),
            "find_min_median": round(q50, 1) if q50 is not None else None,
            "find_min_p90": round(q90, 1) if q90 is not None else None,
            "completed": sum(1 for r in rs if r.get("completed")),
            "drones_retired": sum(r.get("drones_retired") or 0 for r in rs),
            "drones_retired_unfinished": sum(r.get("drones_retired") or 0 for r in rs if not r.get("completed")),
            "mean_speed_mps_median": round(float(np.median(speeds)), 1) if speeds else None,
            "max_ordered_speed_mps": max(ordered) if ordered else None,
            "area_covered_pct_median": round(float(np.median([r["area_covered_pct"] for r in rs])), 1) if rs else None,
            "flight_min_median": round(float(np.median([r["duration_s"] for r in rs])) / 60.0, 1) if rs else None,
            "drones_lost": sum(r["drones_lost"] for r in rs)}
    return out


def _min(v) -> str:
    return "not reached" if v is None else f"{v} min"


def cmd_report(a) -> int:
    path = Path(a.runs)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    seeds = sorted({r["seed"] for r in rows})
    s = summarise(rows)
    lines = ["# Missing hiker: found against swarm size\n",
             f"Seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} searches, "
             f"{'held out: never flown before this run' if seeds[0] >= 9200 else 'development seeds'}), each flown with "
             f"{', '.join(map(str, SIZES))} drones: today's controller and rule supervisor, line abreast at 20 m with "
             "thermal cameras looking down, one sweep of a 400 x 250 m meadow and forest edge. The person (a heat "
             "signature 0.8 m across) lies in the open half the time and under 30 to 80% tree crown otherwise; the "
             "same seed puts the person in the same place whatever the swarm size. A find needs half a second of the "
             "person in some camera's view, longer when partly hidden. Simulated; no model in the loop.\n",
             "The rule supervisor may speed a lone drone up to save charge: when the rest of the sweep at the planned "
             "6 m/s would need more than 90% of the usable charge, it orders the cruise speed that costs least per "
             "kilometre, which can be faster. A faster pass gives a person under tree cover less time in view. The "
             "secondary row flies one drone on the same seeds with the planned speed held (today's controller, no "
             "supervisor), to tell the number of lanes apart from the speed-up.\n",
             "Time to find counts every flight, a miss as never found; a median or 90% that lands on a miss is \"not "
             "reached\". Mean speed is the distance along the sweep path over the time to the sweep's end (or to the "
             "flight's end, if the sweep was not finished); fastest ordered is the highest speed any plan ordered. A "
             "flight ends 5 s after the sweep, or when the last drone retires at its battery reserve.\n",
             "| Drones | Found | In the open | Under trees | Time to find (median) | 90% found within | Sweeps completed "
             "| Mean speed (median) | Fastest ordered | Flight took (median) | Drones retired | Drones lost |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, config in RUNS:
        x = s[_key(n, config)]
        if not x["flights"]:
            continue
        lines.append(f"| {LABELS[config].format(n=n)} | {x['found']} of {x['flights']} | {x['found_open']} of {x['open']} | "
                     f"{x['found_under_trees']} of {x['under_trees']} | {_min(x['find_min_median'])} | "
                     f"{_min(x['find_min_p90'])} | {x['completed']} of {x['flights']} | "
                     f"{'n/a' if x['mean_speed_mps_median'] is None else str(x['mean_speed_mps_median']) + ' m/s'} | "
                     f"{'n/a' if x['max_ordered_speed_mps'] is None else str(x['max_ordered_speed_mps']) + ' m/s'} | "
                     f"{x['flight_min_median']} min | {x['drones_retired']} | {x['drones_lost']} |")
    unfinished = [(n, c) for n, c in RUNS if s[_key(n, c)]["completed"] < s[_key(n, c)]["flights"]]
    if unfinished:
        lines.append("")
    for n, config in unfinished:
        x = s[_key(n, config)]
        lines.append(f"Row \"{LABELS[config].format(n=n)}\": the sweep was not finished in {x['flights'] - x['completed']} "
                     f"of {x['flights']} flights (drones retired at the battery reserve in those flights: "
                     f"{x['drones_retired_unfinished']}); the "
                     f"median flight covered {x['area_covered_pct_median']}% of the area.")
    (path.parent / "REPORT.md").write_text("\n".join(lines) + "\n")
    (path.parent / "summary.json").write_text(json.dumps(s, indent=1))
    print("\n".join(lines))
    return 0


def film_one(seed):
    from halo_systems.ai.mission import regex_baseline
    from halo_systems.eval.runner import make_controller, make_supervisor
    from halo_systems.eval.scenarios import from_mission
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    sc = from_mission(regex_baseline(E1), seed, E1)
    body = DroneBody()
    res = Simulation(sc, make_controller("improved", body, sc.world), make_supervisor("rule"), seed=seed, body=body,
                     record_hz=0).run()
    found = [e for e in res.events if e["kind"] == "target_found"]
    p = sc.targets[0]
    return {"seed": seed, "person": {"x": round(p.x, 1), "y": round(p.y, 1), "cover": round(p.cover, 2)},
            "found_t": found[0]["t"] if found else None, "by_drone": found[0].get("drone") if found else None,
            "flight_s": res.metrics["duration_s"]}


def cmd_film(a) -> int:
    with ProcessPoolExecutor(a.workers) as ex:
        out = list(ex.map(film_one, _seeds(a.seeds)))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"text": E1, "plan": "regex baseline (the same fields as the recorded Nemotron "
                                       "plan: 12 drones, line, 8 m, 30 m, 400 x 250 m survey)", "flights": out}, indent=1))
    for r in out:
        print(r)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("fly")
    s.add_argument("--seeds", default="9200-9219")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", default="results/hiker_search/runs.jsonl")
    s.set_defaults(fn=cmd_fly)
    s = sub.add_parser("report")
    s.add_argument("--runs", default="results/hiker_search/runs.jsonl")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("film")
    s.add_argument("--seeds", default="0-9")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", default="results/hiker_search/film.json")
    s.set_defaults(fn=cmd_film)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
