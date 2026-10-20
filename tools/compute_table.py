"""Where each loop runs, and what it costs in compute (T9): results/compute/REPORT.md and compute.json.

    PYTHONPATH=src python3 tools/compute_table.py --out results/compute

Numbers come from three places, each named in the report:

* development valleys (3000-3003, 16 drones, today's flight, pre-signed alerts), flown here one at a time with a
  timer around the station's once-a-second step, each spread forecast, each rule-supervisor decision, the state
  summary it reads and each safety-validator check; the controller times its own step on every flight
  (``ctrl_ms_mean``, ``ctrl_ms_p95``);
* the controller's time per step with 1, 4 and 16 drones, from the committed valley sweep (seeds 4000-4019,
  results/wildfire_valley/runs.jsonl); the sweep flew its flights in parallel, so these depend on the load;
* the Token Factory calls, from the committed valley-compile results on the held-out requests (results/valley_eval).

It also shows what happens when the cloud link drops, with no key and no spend: the valley compiler's call fails
and the regex baseline reads the request instead, and the Nemotron supervisor's calls fail and the rule
supervisor flies on. Timings are from this machine (``platform`` in compute.json), not from a drone or a Jetson.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np

DEV_SEEDS = (3000, 3001, 3002, 3003)


def controller_rows(path: Path) -> dict:
    """Controller time per step by swarm size, from the committed sweep (tools/wildfire_valley_sweep.py).

    The sweep flew its flights in parallel (one process per core by default), so these times depend on the
    machine's load: compare them with each other, not with flights timed one at a time.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    out = {}
    for n in sorted({r["n"] for r in rows if r.get("config") == "improved+rule"}):
        rs = [r for r in rows if r.get("config") == "improved+rule" and r["n"] == n and r.get("ctrl_ms_mean")]
        out[str(n)] = {"flights": len(rs), "seeds": f"{min(r['seed'] for r in rs)}-{max(r['seed'] for r in rs)}",
                       "alert_rules": sorted({str(r.get("alert_rule")) for r in rs}),
                       "ms_per_step_mean_median": round(float(np.median([r["ctrl_ms_mean"] for r in rs])), 2),
                       "ms_per_step_p95_median": round(float(np.median([r["ctrl_ms_p95"] for r in rs])), 2)}
    return out


def _timed(fn, out: list):
    """``fn``, also appending each call's wall time in ms to ``out``. Same arguments, same return value."""
    def timed(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            out.append(1000.0 * (time.perf_counter() - t0))
    return timed


def _fly_timed(seed: int) -> dict:
    from halo_systems.eval.runner import make_controller, make_supervisor, split_config
    from halo_systems.eval.scenarios import make
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    sc = make("wildfire_valley", seed, n=16, options={"alert_rule": "presigned", "approval": "auto"})
    ctrl, sup = split_config("improved+rule")
    body = DroneBody()
    sim = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body, record_hz=0)
    wf = sim.wildfire
    ms = {k: [] for k in ("station", "forecast", "summary", "decide", "validator")}
    # Instance attributes, so only this flight is timed; the wrappers do not change what the flight does.
    wf._station, wf._forecast = _timed(wf._station, ms["station"]), _timed(wf._forecast, ms["forecast"])
    # The rule supervisor's slow loop (Simulation._supervise, outside the controller's timer): the state summary
    # it reads, its decision, and the safety validator's check of each change it proposes.
    sim.summary = _timed(sim.summary, ms["summary"])
    sim.sup.decide = _timed(sim.sup.decide, ms["decide"])
    sim.validator.check = _timed(sim.validator.check, ms["validator"])
    t0 = time.perf_counter()
    m = sim.run().metrics
    return {"seed": seed, "flight_s": m["duration_s"], "wall_s": round(time.perf_counter() - t0, 1),
            "station_steps": len(ms["station"]), "forecasts": len(ms["forecast"]),
            "supervisor_calls": len(ms["decide"]), "validator_checks": len(ms["validator"]),
            "ctrl_ms_mean": m["ctrl_ms_mean"], "ctrl_ms_p95": m["ctrl_ms_p95"],
            "supervisor_period_s": sc.supervisor_period_s, "forecast_every_s": wf.cfg.forecast.every_s,
            "forecast_min_gap_s": wf.cfg.forecast.min_gap_s, **{f"{k}_ms": v for k, v in ms.items()}}


def _ms(name: str, xs: list, nd: int = 2) -> dict:
    """Median, p95 and longest of per-call times in ms, as ``<name>_ms_median`` and so on; None with no calls."""
    a = np.asarray(xs, dtype=float)
    if not len(a):
        return {f"{name}_ms_{k}": None for k in ("median", "p95", "max")}
    return {f"{name}_ms_median": round(float(np.median(a)), nd), f"{name}_ms_p95": round(float(np.percentile(a, 95)), nd),
            f"{name}_ms_max": round(float(a.max()), nd)}


def _load_avg():
    """The machine's 1-minute load average, or None where there is none."""
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return None


def station_timing(seeds=DEV_SEEDS) -> dict:
    load0 = _load_avg()
    flights = [_fly_timed(s) for s in seeds]              # one at a time, so flights do not share the cores
    load1 = _load_avg()

    def pooled(key):
        return [x for f in flights for x in f[key]]

    st = pooled("station_ms")        # a station step that ran a forecast includes it
    return {"seeds": list(seeds), "load_avg_1min": {"before": load0, "after": load1},
            "flights": [{k: v for k, v in f.items() if not k.endswith("_ms")} for f in flights],
            # Each flight's own controller times (all 16 drones in one call per step), as in controller_rows.
            "ctrl_ms_mean_median": round(float(np.median([f["ctrl_ms_mean"] for f in flights])), 2),
            "ctrl_ms_p95_median": round(float(np.median([f["ctrl_ms_p95"] for f in flights])), 2),
            "station_steps": len(st), **_ms("station", st),
            "forecasts": sum(f["forecasts"] for f in flights), **_ms("forecast", pooled("forecast_ms"), 1),
            "supervisor_calls": sum(f["supervisor_calls"] for f in flights),
            **_ms("supervisor", pooled("decide_ms"), 3), **_ms("summary", pooled("summary_ms"), 3),
            "validator_checks": sum(f["validator_checks"] for f in flights), **_ms("validator", pooled("validator_ms"), 3),
            "busy_share_pct": round(100.0 * sum(st) / 1000.0 / sum(f["flight_s"] for f in flights), 2)}


def token_factory(results: Path) -> dict:
    out = {}
    for name in ("nano_think", "nano"):
        p = results / "valley_eval" / f"{name}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        lat = [r["nemotron"]["record"]["latency_s"] for r in d["sets"]["heldout"]["rows"]
               if (r.get("nemotron") or {}).get("how") == "live" and (r["nemotron"].get("record") or {}).get("latency_s")]
        out[name] = {"model": d["model"], "reasoning": d["reasoning"], "calls": len(lat),
                     "latency_s_median": round(float(np.median(lat)), 2) if lat else None,
                     "latency_s_p95": round(float(np.percentile(lat, 95)), 2) if lat else None}
    return out


class _DeadLink:
    """A transport whose every request fails as a dropped network link would."""

    def __call__(self, url, headers, body, timeout):
        raise OSError("network unreachable (simulated link drop)")


def link_drop() -> dict:
    """The valley compiler and the Nemotron supervisor with the cloud link down (no key needed, nothing spent)."""
    import tempfile
    from halo_systems.ai.client import NebiusClient, NebiusError
    from halo_systems.ai.valley import compile_with_model, plan, regex_baseline
    from halo_systems.eval.runner import make_controller
    from halo_systems.eval.scenarios import make, valley_strikes
    from halo_systems.sim.body import DroneBody
    from halo_systems.sim.engine import Simulation
    from halo_systems.supervisor.nemotron import NemotronSupervisor
    out = {}
    with tempfile.TemporaryDirectory() as d:
        old = {k: os.environ.get(k) for k in ("HALO_LEDGER", "SWARMLAB_LEDGER")}
        # Nothing leaves the machine, but the client books each failed attempt at its worst case: keep those
        # entries out of the real ledger.
        os.environ["HALO_LEDGER"] = str(Path(d) / "ledger.jsonl")
        os.environ.pop("SWARMLAB_LEDGER", None)
        try:
            client = NebiusClient(api_key="link-drop-demo-not-a-key-000000000000", transport=_DeadLink(),
                                  sleep=lambda s: None, max_retries=1, timeout_s=5.0)
            text = "We had dry lightning last night. Put 16 drones over the whole valley for 12 minutes and check last night's strikes."
            try:
                compile_with_model(text, client, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", think=True)
                out["valley_compile"] = {"request": text, "error": "the call did not fail"}
            except NebiusError as exc:
                spec = regex_baseline(text)
                p = plan(spec, valley_strikes(4012))
                out["valley_compile"] = {"request": text, "call": f"failed: {exc.reason}", "fallback": "regex baseline",
                                         "spec": spec, "squads": p.get("squads"), "strike_ids": p.get("strike_ids")}
            sc = make("forest_trail", 0)
            body = DroneBody()
            sup = NemotronSupervisor(client=client, model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", strict=False)
            m = Simulation(sc, make_controller("improved", body, sc.world), sup, seed=0, body=body, record_hz=0).run().metrics
            out["supervisor"] = {"scenario": "forest_trail", **{k: m.get(k) for k in (
                "completed", "ai_calls", "ai_fallbacks", "directive_changes", "collisions", "drones_lost")}}
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return out


def _q(d: dict, k: str):
    """``d[k]``, or "?" when it is missing or None."""
    return "?" if d.get(k) is None else d[k]


def write_report(c: dict) -> str:
    sweep, st, tf = c["controller_sweep"], c["station"], c["token_factory"]
    one, all16, f0 = sweep.get("1", {}), sweep.get("16", {}), (st["flights"] or [{}])[0]
    nt = tf.get("nano_think", {})
    load = st.get("load_avg_1min") or {}
    seeds = ", ".join(map(str, st["seeds"]))
    every = f"{f0['forecast_every_s'] / 60:g} min" if f0.get("forecast_every_s") else "?"
    gap = f"{f0['forecast_min_gap_s']:g} s" if f0.get("forecast_min_gap_s") else "?"
    period = f"{f0['supervisor_period_s']:g} s" if f0.get("supervisor_period_s") else "?"
    lines = [
        "# Where each loop runs\n",
        f"Measured on {c['platform']} (the development machine, not a drone or a Jetson). The controller, the "
        f"rule supervisor and the station were timed on development valleys {seeds} (16 drones), flown one at a time "
        f"(1-minute load average {_q(load, 'before')} before, {_q(load, 'after')} after). The controller's time with "
        "1, 4 and 16 drones also comes from the committed valley sweep, which flew its flights in parallel (below). "
        "Token Factory latencies are from the scored valley-compile calls (results/valley_eval).\n",
        "| Loop | Where it runs | How often | Measured here | When the cloud link drops |",
        "|---|---|---|---|---|",
        f"| Flight control: each drone's controller with its collision-avoidance safety filter | On each drone (simulated "
        f"here, all 16 in one process) | 50 Hz (every 20 ms) | All 16 drones in one vectorised call: "
        f"{_q(st, 'ctrl_ms_mean_median')} ms per step (p95 {_q(st, 'ctrl_ms_p95_median')} ms). In the parallel sweep "
        f"(below), one drone's controller alone: {_q(one, 'ms_per_step_mean_median')} ms per step (p95 "
        f"{_q(one, 'ms_per_step_p95_median')} ms), against {_q(all16, 'ms_per_step_mean_median')} ms for all 16 | "
        "Unaffected: it never uses the link |",
        f"| Rule supervisor: formation, spacing and speed, every change through the safety validator | Ground station | "
        f"Every {period} | Decision: median {_q(st, 'supervisor_ms_median')} ms, p95 "
        f"{_q(st, 'supervisor_ms_p95')} ms ({st.get('supervisor_calls', 0)} calls); building the state summary it reads: "
        f"median {_q(st, 'summary_ms_median')} ms, p95 {_q(st, 'summary_ms_p95')} ms; validator check: median "
        f"{_q(st, 'validator_ms_median')} ms, p95 {_q(st, 'validator_ms_p95')} ms ({st.get('validator_checks', 0)} "
        "checks) | Unaffected |",
        f"| Station: smoke bearings, thermal finds, located fires, cards to fire dispatch | Ground station (a Jetson "
        f"Orin Nano is the candidate) | Once a second | Median {_q(st, 'station_ms_median')} ms, p95 "
        f"{_q(st, 'station_ms_p95')} ms per step, steps that ran a forecast included (busy {_q(st, 'busy_share_pct')}% "
        "of the flight) | Unaffected |",
        f"| Spread forecast (Rothermel ensemble), crew briefs, pre-signed alert checks | Ground station | Once a fire "
        f"is located: every {every}, sooner when the picture changes (at least {gap} apart) | Median "
        f"{_q(st, 'forecast_ms_median')} ms, p95 {_q(st, 'forecast_ms_p95')} ms, longest "
        f"{_q(st, 'forecast_ms_max')} ms per forecast ({st.get('forecasts', 0)} forecasts) | Unaffected: no model in "
        "any alert or card |",
        f"| Valley compile: plain words to a patrol plan (Nemotron 3 Nano, reasoning) | Nebius Token Factory | Once per "
        f"job | Median {_q(nt, 'latency_s_median')} s, p95 {_q(nt, 'latency_s_p95')} s ({nt.get('calls', 0)} "
        "calls) | The regex baseline reads the request (below); the operator checks the plan |",
        "| Test engineer: proposes stress tests (Nemotron) | Token Factory, before deployment | Per batch of tests | "
        "Not timed here (results/valley_stress) | Not in flight |",
        "",
        "## Controller time per step\n",
        "The controller computes every drone's command in one vectorised call per 20 ms step.\n",
        "| Drones | Flights | Where from | ms per step (median of flight means) | p95 (median of flight p95s) |",
        "|---|---|---|---|---|",
        f"| 16 | {len(st['flights'])} | development valleys {seeds}, flown one at a time here | "
        f"{_q(st, 'ctrl_ms_mean_median')} | {_q(st, 'ctrl_ms_p95_median')} |",
    ]
    for n in sorted(sweep, key=int):
        r = sweep[n]
        rules = r.get("alert_rules", [])
        lines.append(f"| {n} | {r['flights']} | committed sweep, seeds {r['seeds']}, alert "
                     f"rule{'s' if len(rules) > 1 else ''} {', '.join(rules)}, flown in parallel | "
                     f"{r['ms_per_step_mean_median']} | {r['ms_per_step_p95_median']} |")
    lines.append("")
    note = ("The sweep (tools/wildfire_valley_sweep.py) flew its flights in parallel, one process per core by default, "
            "so its times depend on the machine's load: compare them with each other, not with the flights timed one "
            "at a time.")
    if "1" in sweep and "16" in sweep:
        note += (f" In the sweep one drone alone took {sweep['1']['ms_per_step_mean_median']} ms per step and sixteen "
                 f"{sweep['16']['ms_per_step_mean_median']} ms, so the 16-drone time divided by 16 is not one drone's "
                 "cost.")
    lines += [note + "\n", "## The link drops\n"]
    v = c["link_drop"].get("valley_compile", {})
    s = c["link_drop"].get("supervisor", {})
    if "call" in v:
        lines.append(f"- Valley compile: the call {v['call']}; the {v.get('fallback', '?')} read the request "
                     f"(\"{v.get('request', '?')}\") as squads {v.get('squads')} and strikes {v.get('strike_ids')}.")
    else:
        lines.append(f"- Valley compile: {v.get('error', 'not run')}.")
    lines.append(f"- Nemotron supervisor on the forest trail: {s.get('ai_calls')} calls, {s.get('ai_fallbacks')} fell back "
                 f"to the rule supervisor; completed {s.get('completed')}, drones lost {s.get('drones_lost')}, "
                 f"collisions {s.get('collisions')}.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="results/compute")
    a = ap.parse_args(argv)
    res = Path(a.results)
    c = {"platform": f"{platform.processor() or platform.machine()}, {os.cpu_count()} cores, "
                     f"Python {platform.python_version()}",
         "controller_sweep": controller_rows(res / "wildfire_valley" / "runs.jsonl"),
         "token_factory": token_factory(res), "station": station_timing(), "link_drop": link_drop()}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "compute.json").write_text(json.dumps(c, indent=1))
    (out / "REPORT.md").write_text(write_report(c))
    print(f"wrote {out / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
