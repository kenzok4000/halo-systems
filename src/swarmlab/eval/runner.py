"""Benchmark runner: scenarios x seeds x configurations, in parallel.

A configuration is a controller and a supervisor, written ``controller+supervisor``
(``improved+rule``, ``original+fixed``, ``improved-no-cbf+fixed``...). Every run
writes one row of metrics; rows go to a JSON-lines file that the report
and the dashboard read. Seeds 0-9 were used for tuning; held-out seeds
start at 1000 so tuned rules and prompts are never scored on what they
were tuned on.
"""

from __future__ import annotations

import dataclasses
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .. import jsonl
from ..control.improved import FEATURES, ImprovedController
from ..control.original import OriginalController
from ..sim.body import DroneBody
from ..sim.engine import Simulation
from ..supervisor.base import FixedSupervisor
from ..supervisor.rule import RuleSupervisor
from .scenarios import make

TUNING_SEEDS = tuple(range(0, 10))
HELD_OUT_SEEDS = tuple(range(1000, 1020))


def make_controller(spec: str, body: DroneBody, world):
    if spec == "original":
        return OriginalController(body, world)
    if spec == "improved":
        return ImprovedController(body, world)
    if spec.startswith("improved-no-"):
        drop = set(spec[len("improved-no-"):].split("-"))
        unknown = drop - set(FEATURES)
        if unknown:
            raise ValueError(f"unknown feature(s) {sorted(unknown)} in {spec!r}")
        return ImprovedController(body, world, features=[f for f in FEATURES if f not in drop])
    raise ValueError(f"unknown controller {spec!r}")


def make_supervisor(spec: str, **kw):
    if spec in ("fixed", "none"):
        return FixedSupervisor()
    if spec == "rule":
        return RuleSupervisor()
    if spec.startswith("nemotron"):
        from ..supervisor.nemotron import NemotronSupervisor
        return NemotronSupervisor.from_spec(spec, **kw)
    raise ValueError(f"unknown supervisor {spec!r}")


def split_config(config: str) -> tuple[str, str]:
    ctrl, _, sup = config.partition("+")
    return ctrl, (sup or "fixed")


def run_one(scenario: str, seed: int, config: str, n: int | None = None, record_hz: float = 0.0,
            keep_result: bool = False, supervisor_kw: dict | None = None, gps_scale: float | None = None,
            **overrides):
    """``gps_scale`` multiplies the scenario's GPS noise and bias (the navigation-error sweep)."""
    ctrl_spec, sup_spec = split_config(config)
    sc = make(scenario, seed, n=n, **overrides)
    if gps_scale is not None:
        sc = dataclasses.replace(sc, sensing=dataclasses.replace(
            sc.sensing, gps_sigma_m=sc.sensing.gps_sigma_m * gps_scale,
            gps_bias_sigma_m=sc.sensing.gps_bias_sigma_m * gps_scale))
    body = DroneBody()
    sup_kw = dict(supervisor_kw or {})
    if sup_spec.startswith("nemotron"):
        sup_kw.setdefault("strict", True)   # a dead key or spent budget fails the run instead of mislabelling it
    sim = Simulation(sc, make_controller(ctrl_spec, body, sc.world), make_supervisor(sup_spec, **sup_kw),
                     seed=seed, body=body, record_hz=record_hz)
    t0 = time.perf_counter()
    result = sim.run()
    row = {
        "scenario": scenario, "seed": seed, "config": config, "controller": ctrl_spec, "supervisor": sup_spec,
        "n": sc.n, "wall_s": round(time.perf_counter() - t0, 2), **result.metrics,
    }
    if gps_scale is not None:
        row["gps_scale"] = gps_scale
    if overrides:
        row["overrides"] = {k: (v if isinstance(v, (int, float, str, bool)) else repr(v)) for k, v in overrides.items()}
    return (row, result) if keep_result else row


def _job(args):
    scenario, seed, config, n, extra = args
    try:
        return run_one(scenario, seed, config, n=n, **extra)
    except Exception as exc:  # a crash in one run must not lose the batch; it is recorded instead
        return {"scenario": scenario, "seed": seed, "config": config, "n": n, "error": f"{type(exc).__name__}: {exc}"}


def run_grid(scenarios, seeds, configs, out_path: str | os.PathLike, n=None, workers: int | None = None,
             extra: dict | None = None, resume: bool = True, progress=print) -> list[dict]:
    """Run every combination and append rows to ``out_path`` (JSON lines). Skips rows already there."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    rows = []
    if resume and out.exists():
        for r in jsonl.read(out):
            if "error" not in r:
                done.add((r["scenario"], r["seed"], r["config"], r.get("n")))
                rows.append(r)
    ns = n if isinstance(n, (list, tuple)) else [n]
    jobs = []
    for sc in scenarios:
        for nn in ns:
            for cfg in configs:
                for s in seeds:
                    key_n = nn if nn is not None else make(sc, s).n
                    if (sc, s, cfg, key_n) in done:
                        continue
                    jobs.append((sc, s, cfg, nn, dict(extra or {})))
    workers = workers or max(1, (os.cpu_count() or 2))
    t0 = time.perf_counter()
    with ProcessPoolExecutor(workers) as ex:
        futures = [ex.submit(_job, j) for j in jobs]
        for k, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            jsonl.append(out, row)
            rows.append(row)
            if progress and (k % 10 == 0 or k == len(jobs)):
                progress(f"{k}/{len(jobs)} runs, {time.perf_counter() - t0:.0f} s")
    return rows
