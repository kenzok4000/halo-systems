"""Initial attack: how many of the valley's fires escape the first crew, reported by today's methods or by the drones.

    PYTHONPATH=src python3 tools/wildfire_initial_attack.py fly --seeds 9020-9039 --out results/wildfire_initial_attack/flights.jsonl
    PYTHONPATH=src python3 tools/wildfire_initial_attack.py model --flights A.jsonl,B.jsonl --out results/wildfire_initial_attack

A perimeter race, the simplest published containment rule (Mees 1985; the rule of the US Forest Service's NFMAS
initial attack analysis, and the one Fried and Fried 2010 build on): a fire is contained at the first time t after
the crew arrives at which the line it has built, r x (t - arrival), reaches the fire's perimeter P(t). The fire keeps
burning freely until then (line does not slow it), which Fried and Fried note can overstate final size; the rule is
the same for both arms, so the comparison is fair. Nothing here was tuned on these seeds: every number is fixed below
with its source, before the measurement.

Per true fire (five per valley):

* **Reported.** Today: the earliest of today's methods as deployed (sim.detection: the geostationary and polar
  satellites, a camera network that sees a fire with CAL FIRE's 2024 share, public calls). With the drones: the
  earlier of that and the drones' card to fire dispatch for this fire (16 drones on today's flight, pre-signed
  alert rules, so a newly located fire, smouldering or not, goes to dispatch as a card).
* **Crew on scene.** The report plus a response time drawn per fire from a lognormal with median 30 min and
  sigma 1.0 (interquartile 15 to 59 min, 90th percentile 108 min), the same draw in both arms. Median 30 min:
  the 30-minute standard response threshold (Haight and Fried 2007), Reimer et al. (2019)'s 30 min for remote
  human-caused fires, and Alberta's helitack median of 24 min. Fixed times of 15, 30, 60 and 120 min are the
  sensitivity rows (Reimer et al. 2019's grid).
* **Line built.** One Type 3 engine with three people, the NWCG initial-action rates by fuel model (NWCG 2021
  republication of PMS 210, engine table): grass (fuel model 1) 24 chains/h, scrub (6) 12, forest (10) 12; one
  chain is 20.1168 m. The fuel is the cover at the ignition point. Other crews are sensitivity rows: a Type 6
  engine with two (12, 6, 8), a five-person helitack crew at the per-person initial-action rates (4.0, 1.0, 1.0
  per person), a 20-person Type 2 crew's sustained direct line (Broyles 2011, Table 2: 9.5, 7, 6.8), and the base
  rate halved and doubled (Haven et al. 1982 report a 22-fold spread in measured rates).
* **Perimeter.** From the true spread (the same truth the flight is scored against, run on to 12 h, the last
  forecast weather period holding after 4 h): the edges between burnt and unburnt 10 m cells, times 10 m times
  pi/4 (the expected length of a smooth curve drawn through a square grid), and never less than the circle of
  the same area. A smouldering fire is its smoulder patch.
* **Escaped.** Not contained, or larger than 4.05 ha (10 acres, CAL FIRE's initial attack goal: 95% of fires
  contained at 10 acres or less) when contained, or still burning free 12 h after the flight starts. 2 ha
  (Alberta's size at first action) and 20 ha are sensitivity rows. The valley is 130 ha, so large-fire sizes
  (121 ha and up) are not modelled.

Measured: escapes with today's reports and with the drones' added, over all fires and paired by valley (a bootstrap
over valleys for the confidence interval). An earlier report can only bring the crew sooner, so in this model the
drones never cause an escape; the question is how many they prevent.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

import numpy as np

CHAIN_M = 20.1168
HORIZON_S = 12 * 3600.0
STEP_S = 30.0
RT_MEDIAN_MIN, RT_SIGMA = 30.0, 1.0
ESCAPE_HA = 4.05
TODAY = ("geostationary", "polar", "camera_deployed", "public")
# chains per hour by cover: grass (fuel model 1), scrub (6), forest (10)
CREWS = {
    "engine3": {"label": "Type 3 engine, 3 people (base)", "grass": 24.0, "scrub": 12.0, "forest": 12.0},
    "engine6": {"label": "Type 6 engine, 2 people", "grass": 12.0, "scrub": 6.0, "forest": 8.0},
    "helitack5": {"label": "Helitack crew, 5 people", "grass": 20.0, "scrub": 5.0, "forest": 5.0},
    "crew20": {"label": "20-person Type 2 crew, sustained", "grass": 9.5, "scrub": 7.0, "forest": 6.8},
}
CONFIG = "improved+rule"
N = 16


# ----------------------------------------------------------------------------- flights
def fly_one(seed: int) -> dict:
    from halo_systems.eval.runner import run_one
    try:
        row = run_one("wildfire_valley", seed, CONFIG, n=N,
                      options={"alert_rule": "presigned", "approval": "auto", "report_v2": True})
        return {"seed": seed, "alert_rule": "presigned", "approval": "auto", **row}
    except Exception as exc:  # one failed flight must not lose the sweep
        return {"seed": seed, "error": f"{type(exc).__name__}: {exc}"}


def cmd_fly(a) -> int:
    lo, _, hi = a.seeds.partition("-")
    seeds = list(range(int(lo), int(hi or lo) + 1))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["seed"] for l in out.read_text().splitlines() if l.strip() and "error" not in l} \
        if out.exists() else set()
    jobs = [s for s in seeds if s not in done]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as pool, out.open("a") as f:
        for k, fut in enumerate(as_completed([pool.submit(fly_one, s) for s in jobs]), 1):
            row = fut.result()
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{k}/{len(jobs)} seed {row['seed']}{' ERROR ' + row['error'] if 'error' in row else ''} "
                  f"({time.perf_counter() - t0:.0f} s)", flush=True)
    return 0


# ----------------------------------------------------------------------------- truth to 12 h
def truth(seed: int) -> dict:
    """The seed's true fires run on to HORIZON_S, and today's report times over that horizon."""
    from halo_systems.eval.scenarios import make
    from halo_systems.sim.detection import today_methods
    from halo_systems.sim.terrain import COVER_NAMES
    from halo_systems.sim.wildfire import Wildfire
    sc = make("wildfire_valley", seed, n=N)
    sc = dataclasses.replace(sc, wildfire=dataclasses.replace(sc.wildfire, horizon_s=HORIZON_S))
    wf = Wildfire(SimpleNamespace(sc=sc, seed=seed, n=sc.n))
    today = today_methods(wf, seed)
    steps = np.arange(-3600.0, HORIZON_S + STEP_S / 2, STEP_S)
    fires = []
    cell = wf.grid.cell_m
    for i, f in enumerate(wf.fires):
        arr = wf.fire_arrival[i]
        per, area = [], []
        for t in steps:
            burnt = arr <= t if t >= f.t_active_s else np.zeros(arr.shape, dtype=bool)
            a_cells = float(np.count_nonzero(burnt)) * cell * cell
            a = max(f.smoulder_area_m2, a_cells)
            if a_cells > 0:
                b = np.pad(burnt, 1).astype(np.int8)
                edges = int(np.count_nonzero(np.diff(b, axis=0))) + int(np.count_nonzero(np.diff(b, axis=1)))
                p = edges * cell * math.pi / 4.0
            else:
                p = 0.0
            per.append(max(p, 2.0 * math.sqrt(math.pi * a)))
            area.append(a)
        cover = COVER_NAMES[int(wf.terrain.cover_at(f.x, f.y))]
        fires.append({"fire": i, "t_active_s": f.t_active_s, "shelter": f.shelter, "cover": cover,
                      "today": {m: today[i][m]["t"] for m in today[i]}, "per_m": per, "area_m2": area})
    return {"seed": seed, "steps": steps.tolist(), "fires": fires}


def contain(steps, per, area, t_arr: float, rate_m_s: float, escape_m2: float):
    """(contained at t or None, area then, escaped) under the perimeter race."""
    steps = np.asarray(steps)
    per = np.asarray(per)
    area = np.asarray(area)
    ok = (steps >= t_arr) & (rate_m_s * (steps - t_arr) >= per)
    if not ok.any():
        return None, float(area[-1]), True
    k = int(np.argmax(ok))
    return float(steps[k]), float(area[k]), bool(area[k] > escape_m2)


# ----------------------------------------------------------------------------- the model
def model_rows(flights: list[dict], truths: dict, *, crew="engine3", rt_fixed_min=None, rate_scale=1.0,
               escape_ha=ESCAPE_HA, today_set=TODAY) -> list[dict]:
    out = []
    for fl in flights:
        seed = fl["seed"]
        tr = truths[seed]
        rng = np.random.default_rng(np.random.SeedSequence([seed, 4040]))
        rts = rng.lognormal(math.log(RT_MEDIAN_MIN), RT_SIGMA, size=len(tr["fires"])) * 60.0
        for f, row, rt in zip(tr["fires"], fl["fire_table"], rts):
            rt = rt if rt_fixed_min is None else rt_fixed_min * 60.0
            ts = [f["today"][m] for m in today_set if f["today"].get(m) is not None]
            t_today = min(ts) if ts else math.inf
            d = row.get("dispatch_t")
            t_drone = min(t_today, d) if d is not None else t_today
            rate = CREWS[crew][f["cover"]] * CHAIN_M / 3600.0 * rate_scale
            res = {"seed": seed, "fire": f["fire"], "cover": f["cover"], "shelter": f["shelter"],
                   "t_active_s": f["t_active_s"], "rt_s": round(rt, 1), "dispatch_t": d,
                   "t_today": None if math.isinf(t_today) else t_today}
            for arm, t in (("today", t_today), ("drones", t_drone)):
                if math.isinf(t):
                    tc, a, esc = None, f["area_m2"][-1], True
                else:
                    tc, a, esc = contain(tr["steps"], f["per_m"], f["area_m2"], t + rt, rate, escape_ha * 1e4)
                res[arm] = {"report_t": None if math.isinf(t) else round(t, 1), "contained_t": tc,
                            "area_m2": round(a, 1), "escaped": esc}
            out.append(res)
    return out


def summarise(rows: list[dict], boots: int = 10000) -> dict:
    seeds = sorted({r["seed"] for r in rows})
    by = {s: [r for r in rows if r["seed"] == s] for s in seeds}
    esc_t = sum(r["today"]["escaped"] for r in rows)
    esc_d = sum(r["drones"]["escaped"] for r in rows)
    per_seed = np.array([[sum(r["today"]["escaped"] for r in by[s]), sum(r["drones"]["escaped"] for r in by[s])]
                         for s in seeds], dtype=float)
    rng = np.random.default_rng(29)
    idx = rng.integers(0, len(seeds), size=(boots, len(seeds)))
    diff = (per_seed[idx, 0].sum(1) - per_seed[idx, 1].sum(1)) / (len(seeds) * 5)
    n = len(rows)
    a_t = np.array([r["today"]["area_m2"] for r in rows]) / 1e4
    a_d = np.array([r["drones"]["area_m2"] for r in rows]) / 1e4
    return {"fires": n, "valleys": len(seeds),
            "escaped_today": esc_t, "escaped_with_drones": esc_d,
            "escaped_today_pct": round(100 * esc_t / n, 1), "escaped_with_drones_pct": round(100 * esc_d / n, 1),
            "prevented": esc_t - esc_d,
            "prevented_pct_points_ci95": [round(100 * float(np.percentile(diff, 2.5)), 1),
                                          round(100 * float(np.percentile(diff, 97.5)), 1)],
            "caused": sum(1 for r in rows if r["drones"]["escaped"] and not r["today"]["escaped"]),
            "area_ha_today_median": round(float(np.median(a_t)), 3),
            "area_ha_drones_median": round(float(np.median(a_d)), 3),
            "drones_reported_first": sum(1 for r in rows if r["dispatch_t"] is not None and
                                         (r["t_today"] is None or r["dispatch_t"] < r["t_today"]))}


def cmd_model(a) -> int:
    flights = []
    for p in a.flights.split(","):
        for line in Path(p).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if "error" in r:
                    raise SystemExit(f"flight {r['seed']} failed: {r['error']}")
                if r.get("alert_rule") == "presigned" and r.get("approval", "auto") == "auto":
                    flights.append(r)
    seeds = sorted({r["seed"] for r in flights})
    if len(seeds) != len(flights):
        raise SystemExit("more than one pre-signed flight for a seed")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "truth.json.gz"
    import gzip
    truths = {}
    if cache.exists():
        truths = {int(k): v for k, v in json.loads(gzip.decompress(cache.read_bytes())).items()}
    todo = [s for s in seeds if s not in truths]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for tr in pool.map(truth, todo):
            truths[tr["seed"]] = tr
            print(f"truth for seed {tr['seed']}", flush=True)
    cache.write_bytes(gzip.compress(json.dumps({str(k): v for k, v in truths.items()}).encode()))
    base = model_rows(flights, truths)
    with (out / "fires.jsonl").open("w") as f:
        for r in base:
            f.write(json.dumps(r) + "\n")
    summary = {"seeds": [seeds[0], seeds[-1]], "base": summarise(base), "sensitivity": {}}
    variants = {f"response fixed {m} min": {"rt_fixed_min": m} for m in (15, 30, 60, 120)}
    variants |= {f"crew: {c['label']}": {"crew": k} for k, c in CREWS.items() if k != "engine3"}
    variants |= {"line rate halved": {"rate_scale": 0.5}, "line rate doubled": {"rate_scale": 2.0},
                 "escape above 2 ha": {"escape_ha": 2.0}, "escape above 20 ha": {"escape_ha": 20.0},
                 "today with a camera that sees every fire": {"today_set": TODAY + ("camera",)}}
    for name, kw in variants.items():
        summary["sensitivity"][name] = summarise(model_rows(flights, truths, **kw), boots=2000)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    s = summary["base"]
    lines = ["# Initial attack: fires that escape the first crew\n",
             f"Seeds {seeds[0]}-{seeds[-1]} ({len(seeds)} valleys, {s['fires']} fires), never used to set anything "
             "here. Made by `tools/wildfire_initial_attack.py`; the model and every number in it are in its docstring.\n",
             "| | Today's reports | With the drones' reports |", "|---|---|---|",
             f"| Fires escaped (base) | {s['escaped_today']} of {s['fires']} ({s['escaped_today_pct']}%) | "
             f"{s['escaped_with_drones']} ({s['escaped_with_drones_pct']}%) |",
             f"| Median area when contained, ha | {s['area_ha_today_median']} | {s['area_ha_drones_median']} |",
             f"\nPrevented: {s['prevented']} escapes ({s['prevented_pct_points_ci95'][0]} to "
             f"{s['prevented_pct_points_ci95'][1]} percentage points, 95% bootstrap over valleys). The drones reported "
             f"{s['drones_reported_first']} fires before any of today's methods.\n",
             "| Sensitivity | Escaped today | With drones | Prevented |", "|---|---|---|---|"]
    for name, v in summary["sensitivity"].items():
        lines.append(f"| {name} | {v['escaped_today']} ({v['escaped_today_pct']}%) | {v['escaped_with_drones']} "
                     f"({v['escaped_with_drones_pct']}%) | {v['prevented']} |")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fly")
    f.add_argument("--seeds", default="9020-9039")
    f.add_argument("--out", default="results/wildfire_initial_attack/flights.jsonl")
    f.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    m = sub.add_parser("model")
    m.add_argument("--flights", required=True, help="comma-separated rows files with pre-signed, automatic flights")
    m.add_argument("--out", default="results/wildfire_initial_attack")
    m.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    return cmd_fly(a) if a.cmd == "fly" else cmd_model(a)


if __name__ == "__main__":
    raise SystemExit(main())
