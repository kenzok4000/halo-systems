"""Aircraft inbound: fly the wildfire valley with and without an airspace hold, and report what the hold cost.

    PYTHONPATH=src python3 tools/airspace_hold_demo.py --out results/airspace_hold

When crewed firefighting aircraft (air tankers, helicopters, a lead plane) come into the valley, the operator orders
the swarm to hold: every flying drone stops where it is and descends to a low hold altitude, out of the aircraft's
way, until the operator resumes the sweep. This tool flies one development seed (3000) twice with 16 drones,
today's controller and the rule supervisor (improved+rule): once without a hold, and once with a hold ordered
during the sweep and resumed five minutes later. It writes summary.json and REPORT.md: the validator's log lines
for the orders, how long until every drone was stopped at the hold altitude, the charge used while holding, drones
retired, how much later the sweep finished, and the fires located in each flight. One seed is a demonstration of
the control, not a measurement.

The hold is ordered 10 minutes into the flight when the sweep lasts that long; a 16-drone sweep of the valley floor
is shorter, so then the hold is ordered halfway through the planned sweep (route length at the nominal mission speed)
instead; the report says which, and where along the route the swarm actually was. Both flights get the same time
limit: the scenario's own limit plus the hold's length, so the held flight can finish its sweep; a flight still
ends 5 s after its sweep is complete.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

CONFIG = "improved+rule"
SEED = 3000            # a development seed (2000-2011 and 3000-3019 were used to build the valley)
HOLD_AT_S = 600.0      # the order 10 minutes into the flight, when the sweep lasts that long
HOLD_S = 300.0         # resumed 5 minutes later
PAST_STOP_M = 50.0     # the delay is measured this far past the point where the held swarm stopped

ROW_KEYS = ("completed", "completion_time_s", "duration_s", "progress_m", "drones_retired", "drones_lost",
            "drones_flying_end", "collisions", "obstacle_crashes", "ground_crashes", "near_misses", "min_separation_m",
            "energy_wh", "targets_found", "targets_total", "area_covered_pct", "fires_total", "fires_located",
            "fires_located_by_smoke", "fires_located_by_thermal", "false_locations", "all_located_s",
            "airspace_held_s", "airspace_clear_s_max", "airspace_holds")


def timeline(seed: int, n: int, hold_at: float | None, hold_s: float) -> dict:
    """When to order the hold and resume, from the scenario's own plan (route length and mission speed)."""
    from halo_systems.control.formations import Path as Route
    from halo_systems.eval.scenarios import wildfire_valley
    sc = wildfire_valley(seed, n=n)
    route = Route(sc.waypoints, loop=sc.loop).length
    planned = route / sc.directive.speed_mps
    note = None
    if hold_at is None:
        if planned > HOLD_AT_S + 60.0:
            hold_at = HOLD_AT_S
        else:
            hold_at = 10.0 * round(0.5 * planned / 10.0)
            note = (f"the {n}-drone sweep is planned as about {planned / 60:.1f} min of flying ({route:.0f} m at the "
                    f"nominal {sc.directive.speed_mps:g} m/s), so an order 10 minutes in would come after the sweep; "
                    f"the hold was ordered at {hold_at:.0f} s instead, halfway through the planned sweep, and "
                    f"resumed {hold_s / 60:.0f} minutes later as planned")
    return {"route_m": round(route, 1), "mission_speed_mps": sc.directive.speed_mps, "planned_sweep_s": round(planned, 1),
            "scenario_limit_s": round(sc.duration_s, 1), "duration_limit_s": round(sc.duration_s + hold_s, 1),
            "hold_at_s": hold_at, "resume_at_s": hold_at + hold_s, "hold_s": hold_s,
            "hold_altitude_m": None, "note": note}


def fly(job):
    seed, n, script, limit = job
    from halo_systems.eval.runner import make_controller, make_supervisor
    from halo_systems.eval.scenarios import make
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    sc = make("wildfire_valley", seed, n=n, duration_s=limit, airspace_script=script)
    body = DroneBody()
    ctrl, sup = CONFIG.split("+")
    sim = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body,
                     record_hz=0)
    t0 = time.perf_counter()
    res = sim.run()
    m = res.metrics
    row = {k: m[k] for k in ROW_KEYS if k in m}
    row["fire_table"] = [{k: f[k] for k in ("shelter", "located", "t", "by")} for f in m.get("fire_table", [])]
    row["battery_end_mean_pct"] = round(float((100 * sim.battery_wh[sim.active_mask()] / sim.capacity_wh).mean()), 1) \
        if sim.active_mask().any() else None
    row["capacity_wh"] = sim.capacity_wh
    row["wall_s"] = round(time.perf_counter() - t0, 1)
    row["validator_log"] = [e for e in res.validator_log if e.get("kind") == "airspace"]
    row["events"] = [e for e in res.events if e["kind"].startswith("airspace")]
    row["retired_events"] = [e for e in res.events if e["kind"] == "retired_low_battery"]
    # Leader progress and speed every 0.1 s, for the comparison in main(); not written to summary.json.
    row["_series"] = {k: [round(float(x), 3) for x in res.series[k][::5]] for k in ("t", "progress_m", "leader_speed")}
    return row


def at_time(series: dict, t: float) -> dict:
    """Leader progress and speed at time t (the last sample at or before it)."""
    i = max(int(np.searchsorted(np.asarray(series["t"]), t + 1e-9)) - 1, 0)
    return {"t": series["t"][i], "progress_m": series["progress_m"][i], "leader_speed_mps": series["leader_speed"][i]}


def time_at(series: dict, progress: float) -> float | None:
    """First time the leader reached ``progress`` metres along the route (None: never)."""
    s = np.asarray(series["progress_m"])
    i = int(np.argmax(s >= progress))
    return float(series["t"][i]) if s[i] >= progress else None


def delays(no_hold: dict, hold: dict, tl: dict) -> dict:
    """Where the held swarm was when the hold was ordered, where it stopped, when it moved again, and how far behind
    the flight without a hold it was at points along the rest of the route."""
    a, b = no_hold["_series"], hold["_series"]
    order = at_time(b, tl["hold_at_s"])
    stop = at_time(b, tl["resume_at_s"])["progress_m"]
    t = np.asarray(b["t"])
    s = np.asarray(b["progress_m"])
    moving = np.nonzero((t > tl["resume_at_s"]) & (s > stop + 0.01))[0]
    out = {"at_order": order, "route_m": tl["route_m"], "stopped_at_m": round(stop, 1),
           "moved_again_s": round(float(t[moving[0]]), 2) if len(moving) else None, "behind_s": {}}
    first = stop + PAST_STOP_M
    for x in [first] + [float(v) for v in range(int(first // 100 + 1) * 100, int(tl["route_m"]), 100)]:
        ta, tb = time_at(a, x), time_at(b, x)
        if ta is not None and tb is not None:
            out["behind_s"][f"{x:.0f}"] = round(tb - ta, 2)
    return out


def fmt(x, unit="", nd=1):
    if x is None:
        return "none"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:.{nd}f}{unit}"
    return f"{x}{unit}"


def done(row: dict) -> str:
    return f"yes, at {row['completion_time_s']:.0f} s" if row.get("completed") else "no"


def report(summary: dict) -> str:
    tl, a, b = summary["timeline"], summary["flights"]["no_hold"], summary["flights"]["hold"]
    h = b["airspace_holds"][0] if b.get("airspace_holds") else None
    n = summary["n"]
    lines = [
        "# Aircraft inbound: an airspace hold over the wildfire valley",
        "",
        f"One development seed ({summary['seed']}), {n} drones, today's controller and the rule supervisor "
        f"({summary['config']}), flown twice: without a hold, and with the operator's \"aircraft inbound\" hold. "
        "This is a demonstration of the control on one seed, not a measurement: other seeds, swarm sizes and hold "
        "times will give other numbers.",
        "",
        "What the hold does: when crewed firefighting aircraft (air tankers, helicopters, a lead plane) come into the "
        "valley, the operator orders a hold. The order passes the same safety validator as every directive and is "
        "logged like one. Every flying drone stops where it is and descends to the hold altitude (10 m above the "
        "surface the drones follow, tree crowns included, unless the operator gives a lower one), clear of the "
        "aircraft's working height. The ground station, smoke watch and alerts keep running. On resume the swarm "
        "climbs back to its working altitude and carries on the sweep from where it stopped.",
        "",
        "## Timeline",
        "",
        f"* Planned sweep: {tl['route_m']:.0f} m at the nominal {tl['mission_speed_mps']:g} m/s, about "
        f"{tl['planned_sweep_s'] / 60:.1f} min of flying.",
        f"* Hold ordered at {tl['hold_at_s']:.0f} s, resume ordered at {tl['resume_at_s']:.0f} s "
        f"({tl['hold_s'] / 60:.0f} min).",
    ]
    if tl["note"]:
        lines.append(f"* Why then: {tl['note']}.")
    pos = summary.get("progress")
    if pos:
        o = pos["at_order"]
        lines.append(f"* Where the swarm was: at {tl['hold_at_s']:.0f} s its leader was {o['progress_m']:.0f} m along "
                     f"the {pos['route_m']:.0f} m route ({100 * o['progress_m'] / pos['route_m']:.0f}%), flying at "
                     f"{o['leader_speed_mps']:.1f} m/s"
                     + (f"; the flight without a hold took {a['completion_time_s']:.0f} s to sweep the whole route."
                        if a.get("completion_time_s") is not None else "."))
    lines += [
        f"* Time limit for both flights: {tl['duration_limit_s']:.0f} s (the scenario's {tl['scenario_limit_s']:.0f} s "
        "plus the hold), so the held flight can finish its sweep; each flight still ends 5 s after its sweep is "
        "complete.",
        "",
        "## The orders in the validator's log",
        "",
        "```",
        *[json.dumps(e) for e in b["validator_log"]],
        "```",
        "",
        "## The hold",
        "",
    ]
    if h:
        per_drone = h["charge_used_wh"] / max(h["flying_at_start"], 1)
        lines += [
            f"* Held from {h['from_s']:.0f} s to {fmt(h['to_s'], ' s', 0)} at {h['altitude_m']:g} m above the followed "
            f"surface, ordered by the {h['source']}; {h['flying_at_start']} drones flying when it began.",
            f"* Every flying drone was within 1.5 m of the hold altitude and slower than 0.5 m/s "
            f"{fmt(h['clear_s'], ' s')} after the order.",
            f"* Charge used while holding: {h['charge_used_wh']:.1f} Wh by the flying drones, about {per_drone:.2f} Wh "
            f"each ({100 * per_drone / b['capacity_wh']:.1f}% of one battery).",
            f"* Drones retired at the battery reserve during the hold: {h['retired_during']}.",
        ]
    else:
        lines.append("* No hold was recorded (the order was rejected); see the validator's log above.")
    lines += [
        "",
        "## With and without the hold",
        "",
        "| | without hold | with hold |",
        "|---|---|---|",
        f"| sweep complete | {done(a)} | {done(b)} |",
        f"| fires located (of {a.get('fires_total')}) | {a.get('fires_located')} | {b.get('fires_located')} |",
        f"| by smoke / by thermal camera | {a.get('fires_located_by_smoke')} / {a.get('fires_located_by_thermal')} | "
        f"{b.get('fires_located_by_smoke')} / {b.get('fires_located_by_thermal')} |",
        f"| hotspots seen by the cameras | {a.get('targets_found')} | {b.get('targets_found')} |",
        f"| area covered | {fmt(a.get('area_covered_pct'), '%')} | {fmt(b.get('area_covered_pct'), '%')} |",
        f"| drones retired at the reserve | {a['drones_retired']} | {b['drones_retired']} |",
        f"| mean battery left (flying drones) | {fmt(a['battery_end_mean_pct'], '%')} | "
        f"{fmt(b['battery_end_mean_pct'], '%')} |",
        f"| energy used | {a['energy_wh']:.0f} Wh | {b['energy_wh']:.0f} Wh |",
        f"| collisions, obstacle and ground strikes | {a['collisions']}, {a['obstacle_crashes']}, {a['ground_crashes']} | "
        f"{b['collisions']}, {b['obstacle_crashes']}, {b['ground_crashes']} |",
        "",
    ]
    d = summary["difference"]
    if d["sweep_finished_later_s"] is not None:
        lines.append(f"The sweep finished {d['sweep_finished_later_s']:.1f} s later with the hold.")
    else:
        lines.append("The held flight did not finish its sweep within the time limit.")
    if pos and pos["behind_s"]:
        first_x, first_d = next(iter(pos["behind_s"].items()))
        moved = (f" and moved again at {pos['moved_again_s']:.1f} s, {pos['moved_again_s'] - tl['resume_at_s']:.1f} s "
                 "after the resume order, once it had climbed back" if pos["moved_again_s"] is not None else "")
        lines += [
            "",
            f"The held swarm stopped {pos['stopped_at_m']:.0f} m along the route{moved}. {PAST_STOP_M:.0f} m past "
            f"that point ({first_x} m) it was {first_d:.1f} s behind the flight without a hold: the "
            f"{tl['hold_s']:.0f} s held plus {first_d - tl['hold_s']:.1f} s for braking, climbing back and speeding "
            "up again (the descent to the hold altitude happened within the held time).",
        ]
        later = list(pos["behind_s"].items())[1:]
        if later:
            lines += ["", "Further along the route it was "
                      + ", ".join(f"{v:.1f} s behind at {k} m" for k, v in later) + "."]
        end = d["sweep_finished_later_s"]
        if end is not None and abs(end - first_d) >= 0.5:
            lines.append(f"By the end of the sweep the gap was {end:.1f} s: the rest of the sweep was flown about "
                         f"{tl['hold_s'] / 60:.0f} minutes later than in the flight without a hold, and its pace was "
                         "not exactly the same.")
        elif end is not None:
            lines.append(f"By the end of the sweep the gap was {end:.1f} s, about the same.")
    lines += [
        "",
        "Fires per flight (shelter: located at, by):",
        "",
        "| fire | without hold | with hold |",
        "|---|---|---|",
    ]
    for i, (fa, fb) in enumerate(zip(a["fire_table"], b["fire_table"]), 1):
        def cell(f):
            return f"{f['t']:.0f} s, {f['by']}" if f["located"] else "not located"
        lines.append(f"| {i} ({fa['shelter']}) | {cell(fa)} | {cell(fb)} |")
    lines += [
        "",
        "The held flight runs longer, so its station has more time to see smoke from fires that start to spread later; "
        "a difference in fires located between the two flights reflects that as well as the hold itself.",
        "",
        f"Made by `tools/airspace_hold_demo.py` (wall time {a['wall_s']:.0f} s and {b['wall_s']:.0f} s).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/airspace_hold")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--hold-at", type=float, default=None, help="seconds into the flight (default: see the docstring)")
    ap.add_argument("--hold-s", type=float, default=HOLD_S)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    if 4000 <= a.seed <= 9999:
        raise SystemExit("seeds 4000-9999 are held out; use a development seed")
    tl = timeline(a.seed, a.n, a.hold_at, a.hold_s)
    script = [{"t": tl["hold_at_s"], "op": "hold"}, {"t": tl["resume_at_s"], "op": "resume"}]
    jobs = [(a.seed, a.n, [], tl["duration_limit_s"]), (a.seed, a.n, script, tl["duration_limit_s"])]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=max(1, min(2, a.workers))) as pool:
        no_hold, hold = pool.map(fly, jobs)
    held = hold.get("airspace_holds") or []
    if held:
        tl["hold_altitude_m"] = held[0]["altitude_m"]
    progress = delays(no_hold, hold, tl)
    no_hold.pop("_series")
    hold.pop("_series")
    later = (hold["completion_time_s"] - no_hold["completion_time_s"]
             if hold.get("completion_time_s") is not None and no_hold.get("completion_time_s") is not None else None)
    summary = {
        "what": "wildfire valley flown without and with an airspace hold (crewed firefighting aircraft inbound)",
        "scenario": "wildfire_valley", "seed": a.seed, "n": a.n, "config": CONFIG,
        "caveat": "one development seed: a demonstration of the control, not a measurement",
        "timeline": tl, "orders": script, "progress": progress,
        "flights": {"no_hold": no_hold, "hold": hold},
        "difference": {
            "sweep_finished_later_s": round(later, 2) if later is not None else None,
            "fires_located": {"no_hold": no_hold.get("fires_located"), "hold": hold.get("fires_located")},
            "drones_retired": {"no_hold": no_hold["drones_retired"], "hold": hold["drones_retired"]},
            "energy_wh": {"no_hold": no_hold["energy_wh"], "hold": hold["energy_wh"]},
        },
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    (out / "REPORT.md").write_text(report(summary))
    print(f"wrote {out}/summary.json and REPORT.md ({time.perf_counter() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
