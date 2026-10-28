"""Stress testing: search a space of hard conditions for ones that make the swarm fail.

Four searchers share one budget of simulated flights and one scoring rule:

* random search (the usual baseline),
* corner search: random corners of the parameter box, each parameter at its
  lowest or highest value (boundary-value testing),
* the cross-entropy method (a standard optimiser for this kind of search),
* Nemotron as a test engineer: it sees every test so far (parameters and
  outcome) and proposes the next batch, with a reason for each.

A flight "fails" if any drone crashes or two drones come within the
near-miss distance. Severity combines crashes, near misses and the
closest approach, so searchers are rewarded for getting closer to failure
even before they find one. The system under test is the improved
controller with the rule supervisor.
"""

from __future__ import annotations

import dataclasses
import json
import math

import numpy as np

from ..control.directive import Directive
from ..control.formations import SHAPES
from ..sim.scenario import MotorFailure, Scenario, ScriptedDirective
from ..sim.sensing import SensingConfig
from ..sim.wind import GustFront, WindConfig
from ..sim.world import Cylinder, World

SPACE = {
    # name: (low, high, kind, meaning)
    "drones": (6, 32, "int", "number of drones"),
    "spacing_m": (2.5, 8.0, "float", "planned spacing between neighbours"),
    "speed_mps": (2.0, 12.0, "float", "cruise speed"),
    "wind_mps": (0.0, 9.0, "float", "steady crosswind"),
    "turbulence_mps": (0.0, 3.0, "float", "gust standard deviation"),
    "gust_front_mps": (0.0, 14.0, "float", "strength of two sudden gust fronts (0 = none)"),
    "packet_loss": (0.0, 0.6, "float", "radio packet loss probability"),
    "radio_latency_s": (0.02, 0.5, "float", "radio latency"),
    "gps_noise_m": (0.05, 1.0, "float", "GPS white noise (1 sigma)"),
    "gps_bias_m": (0.1, 1.2, "float", "slowly drifting GPS bias (1 sigma)"),
    "motor_failures": (0, 4, "int", "drones that lose power mid-flight"),
    "formation_change": (0, 4, "int", "index into grid, line, column, v, ring ordered at t=20 s (0 = keep grid)"),
    "tree_density": (0.0, 25.0, "float", "trees per 1000 m^2 in a 120 m forest band (0 = open)"),
    "trail_width_m": (6.0, 40.0, "float", "width of the clear trail through the forest"),
}
NAMES = list(SPACE)
# Parameters where the low end is the harder condition; for all others the high end is.
LOW_IS_HARDER = {"spacing_m", "trail_width_m"}


def clip_params(p: dict) -> dict:
    """Make proposed parameters valid. Every searcher's proposals pass through here, the model's included.

    Keys outside ``SPACE`` are dropped. A missing, non-numeric or non-finite value becomes the middle of
    its range, and every value is clamped to [low, high]. Integers are rounded with Python's ``round``
    (halves go to the even number) and floats are kept to 3 decimals.
    """
    out = {}
    for k, (lo, hi, kind, _) in SPACE.items():
        v = p.get(k, (lo + hi) / 2)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = (lo + hi) / 2
        if not math.isfinite(v):
            v = (lo + hi) / 2
        v = min(max(v, lo), hi)
        out[k] = int(round(v)) if kind == "int" else round(v, 3)
    return out


def stress_level(params: dict) -> float:
    """How hard a test's conditions are, from 0 (every parameter at its easy end) to 1 (all at the hard end).

    A failure found at a low stress level tells an engineer more than one found with every
    parameter at its limit, so searches are also compared on their mildest failure.
    """
    p = clip_params(params)
    h = []
    for k, (lo, hi, _, _) in SPACE.items():
        u = (p[k] - lo) / (hi - lo)
        h.append(1.0 - u if k in LOW_IS_HARDER else u)
    return round(float(np.mean(h)), 3)


def build(params: dict, seed: int) -> Scenario:
    """The stress-test flight: a 360 m straight leg in grid formation at 15 m altitude, 75 s long, with
    a 22 m altitude ceiling. ``TESTER_SYSTEM`` describes the same leg in words, so change both together."""
    p = clip_params(params)
    rng = np.random.default_rng(np.random.SeedSequence([seed, 77]))
    # Keep the order of the random draws: gust front angles, wind angle, tree count, three draws per
    # tree (skipped trees included), failure times, then which drones fail. A saved search keeps only
    # params and seed, so replay() rebuilds the same conditions only while this order stays the same.
    n = p["drones"]
    fronts = []
    if p["gust_front_mps"] > 0:
        # Each front is a 5 s gust in a random horizontal direction with a 1 m/s downdraft (z = -1).
        for t in (25.0, 45.0):
            ang = rng.uniform(0, 2 * math.pi)
            fronts.append(GustFront(t, 5.0, (p["gust_front_mps"] * math.cos(ang), p["gust_front_mps"] * math.sin(ang), -1.0)))
    wang = rng.uniform(0, 2 * math.pi)
    wind = WindConfig(mean_mps=(p["wind_mps"] * math.cos(wang), p["wind_mps"] * math.sin(wang), 0.0),
                      gust_sigma_mps=p["turbulence_mps"], local_sigma_mps=p["turbulence_mps"] * 0.4, fronts=fronts)
    sensing = SensingConfig(gps_sigma_m=p["gps_noise_m"], gps_bias_sigma_m=p["gps_bias_m"], latency_s=p["radio_latency_s"],
                            jitter_s=p["radio_latency_s"] * 0.3, loss_prob=p["packet_loss"])
    trees = []
    if p["tree_density"] > 0:
        x0, x1 = 120.0, 240.0
        area = (x1 - x0) * 120.0
        for _ in range(rng.poisson(area * p["tree_density"] / 1000.0)):
            x, y, r = rng.uniform(x0, x1), rng.uniform(-60, 60), rng.uniform(0.25, 0.6)
            if abs(y) < p["trail_width_m"] / 2 + r:
                continue
            trees.append(Cylinder(float(x), float(y), float(r), 30.0))
    fails = [MotorFailure(float(t), int(d)) for t, d in zip(np.sort(rng.uniform(15, 50, p["motor_failures"])),
                                                             rng.choice(n, size=min(p["motor_failures"], n - 2), replace=False))]
    script = [ScriptedDirective(20.0, {"formation": SHAPES[p["formation_change"]]})] if p["formation_change"] else []
    return Scenario(name="stress", title="Stress test", description=json.dumps(p), n=n, duration_s=75.0,
                    waypoints=[(0.0, 0.0), (360.0, 0.0)],
                    directive=Directive("grid", p["spacing_m"], p["speed_mps"], 15.0, 1.0, source="mission"),
                    world=World(cylinders=trees), wind=wind, sensing=sensing, failures=fails, script=script,
                    altitude_max_m=22.0, use_case="stress test")


def severity(m: dict) -> float:
    # Drones lost (a collision takes two) weigh most, then near misses, then up to 3 for a closest approach
    # under 3 m, so a near pass scores before anything fails. No recorded closest approach counts as 10 m.
    crashes = 2 * m["collisions"] + m["obstacle_crashes"] + m["ground_crashes"]
    sep = m["min_separation_m"] if m["min_separation_m"] is not None else 10.0
    return 10.0 * crashes + 2.0 * m["near_misses"] + max(0.0, 3.0 - sep)


def failed(m: dict) -> bool:
    return (m["collisions"] + m["obstacle_crashes"] + m["ground_crashes"]) > 0 or m["near_misses"] > 0


def evaluate(params: dict, seed: int, config: str = "improved+rule") -> dict:
    from ..eval.runner import make_controller, make_supervisor, split_config
    from ..sim.body import DroneBody
    from ..sim.engine import Simulation
    sc = build(params, seed)
    ctrl, sup = split_config(config)
    body = DroneBody()
    m = Simulation(sc, make_controller(ctrl, body, sc.world), make_supervisor(sup), seed=seed, body=body, record_hz=0).run().metrics
    keep = ("collisions", "obstacle_crashes", "ground_crashes", "near_misses", "min_separation_m", "formation_error_mean_m",
            "time_formed_pct", "completed")
    out = {k: m[k] for k in keep}
    out["severity"] = round(severity(m), 3)
    out["failed"] = failed(m)
    return out


# ------------------------------------------------------------------ searchers
# Random, corner and cross-entropy search work in the unit cube; this maps a point back to parameters and,
# like every proposal, through clip_params.
def _unit_to_params(u: np.ndarray) -> dict:
    out = {}
    for k, x in zip(NAMES, u):
        lo, hi, _, _ = SPACE[k]
        out[k] = lo + float(x) * (hi - lo)
    return clip_params(out)


def _params_to_unit(p: dict) -> np.ndarray:
    return np.array([(p[k] - SPACE[k][0]) / (SPACE[k][1] - SPACE[k][0]) for k in NAMES])


class RandomSearch:
    name = "random"

    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)

    def propose(self, history: list[dict], k: int) -> list[dict]:
        return [{"params": _unit_to_params(self.rng.uniform(0, 1, len(NAMES))), "why": "uniform random"} for _ in range(k)]


class CornerSearch:
    name = "corners"

    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)

    def propose(self, history: list[dict], k: int) -> list[dict]:
        return [{"params": _unit_to_params(self.rng.integers(0, 2, len(NAMES)).astype(float)), "why": "random corner"}
                for _ in range(k)]


class CrossEntropySearch:
    name = "cem"

    def __init__(self, seed: int, elite_frac: float = 0.25):
        self.rng = np.random.default_rng(seed)
        self.mu = np.full(len(NAMES), 0.5)
        self.sigma = np.full(len(NAMES), 0.3)
        self.elite_frac = elite_frac

    def propose(self, history: list[dict], k: int) -> list[dict]:
        # Once there are 4 tests, refit before each batch: the elite is the top ``elite_frac`` (at least
        # 2) of all tests so far by severity, and the mean and spread move 70% of the way to the elite's.
        # The spread never drops below 0.05, so sampling keeps exploring. Samples are drawn in the unit
        # cube, one axis per parameter in SPACE order.
        if len(history) >= 4:
            ranked = sorted(history, key=lambda h: -h["outcome"]["severity"])
            elite = ranked[:max(2, int(len(ranked) * self.elite_frac))]
            u = np.array([_params_to_unit(h["params"]) for h in elite])
            self.mu = 0.7 * u.mean(axis=0) + 0.3 * self.mu
            self.sigma = np.maximum(0.05, 0.7 * u.std(axis=0) + 0.3 * self.sigma)
        return [{"params": _unit_to_params(np.clip(self.rng.normal(self.mu, self.sigma), 0, 1)), "why": "CEM sample"}
                for _ in range(k)]


TESTER_SYSTEM = """You are a test engineer hunting for conditions that make a drone swarm fail. The swarm \
flies a 360 m leg in a grid formation at 15 m altitude. A 50 Hz controller with a collision-avoidance \
safety filter flies each drone, and a rule-based supervisor may change formation, spacing or speed. A test \
fails if any drone crashes or two drones pass within 0.8 m of each other (centre to centre).

You choose the test conditions. Parameters and their ranges:
{space}

You will see every test run so far with its outcome. Severity = 10 x crashes + 2 x near misses + \
max(0, 3 - closest approach in m); higher is closer to failure. Propose the next tests to find failures, \
and especially failures of different kinds than those already found. Reply with one JSON object and nothing else:
{{"tests": [{{"params": {{...all parameters...}}, "why": "one short sentence"}}, ...]}}"""


class NemotronSearch:
    """Nemotron as the test engineer.

    Every call sends the whole history so far: each test's params, severity, whether it failed, drones
    lost (as "crashes"), near misses and closest approach. Temperature defaults to 0.7. When the model
    proposes too few tests or the call fails, the batch is filled from ``RandomSearch(seed)``, so the
    n-th top-up has the same conditions as the n-th test of the random search with that seed
    (``results/stress/random_<seed>.json``). With ``strict=True`` (the CLI) an error every later call
    would hit too (no key, rejected key, budget) is raised, so a dead key cannot turn the search into
    random search; with ``strict=False`` (the tests) the batch is topped up instead.
    """

    name = "nemotron"

    def __init__(self, client, model: str, seed: int = 0, temperature: float = 0.7, think: bool = False,
                 strict: bool = False):
        self.client = client
        self.strict = strict
        self.model = model
        self.think = think
        self.temperature = temperature
        self.fallback = RandomSearch(seed)
        self.calls: list[dict] = []
        space = "\n".join(f"- {k}: {lo} to {hi} ({kind}), {meaning}" for k, (lo, hi, kind, meaning) in SPACE.items())
        self.system = TESTER_SYSTEM.format(space=space)

    def propose(self, history: list[dict], k: int) -> list[dict]:
        from .client import NebiusError, extract_json, reasoning
        rows = [{"params": h["params"], "severity": h["outcome"]["severity"], "failed": h["outcome"]["failed"],
                 "crashes": 2 * h["outcome"]["collisions"] + h["outcome"]["obstacle_crashes"] + h["outcome"]["ground_crashes"],
                 "near_misses": h["outcome"]["near_misses"], "closest_m": h["outcome"]["min_separation_m"]} for h in history]
        user = (f"Tests so far ({len(rows)}):\n{json.dumps(rows, separators=(',', ':'))}\n\n"
                f"Propose exactly {k} new tests.")
        # These calls are not recorded for replay. A saved search keeps each test's parameters and seed
        # instead, and replay() flies them again.
        try:
            res = self.client.chat(self.model, [{"role": "system", "content": self.system}, {"role": "user", "content": user}],
                                   role="test-engineer", temperature=self.temperature,
                                   max_tokens=8000 if self.think else 2500, extra_body=reasoning(self.think))
            self.calls.append(res.record.to_dict())
            obj = extract_json(res.content)
            tests = obj.get("tests") or []
            out = [{"params": clip_params(t.get("params") or {}), "why": str(t.get("why", ""))[:200]} for t in tests[:k]
                   if isinstance(t, dict)]
        except (NebiusError, ValueError) as exc:
            if self.strict and getattr(exc, "fatal", False):
                raise
            self.calls.append({"status": "error", "error": str(exc)[:200]})
            out = []
        while len(out) < k:  # top up with random tests if the model gave too few or failed
            out.append({**self.fallback.propose(history, 1)[0], "why": "random top-up (model gave too few tests)"})
        return out


def run_search(searcher, budget: int = 40, batch: int = 4, seed: int = 0, workers: int = 4, progress=None) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor
    history: list[dict] = []
    with ProcessPoolExecutor(workers) as ex:
        while len(history) < budget:
            k = min(batch, budget - len(history))
            # Each batch is proposed only after the previous one has flown, so an adaptive searcher sees every
            # outcome so far. Test i gets the same flight seed whichever searcher proposed it, so two searches
            # with the same seed differ only in the conditions they chose.
            props = searcher.propose(history, k)
            seeds = [seed * 10007 + len(history) + i for i in range(k)]
            outcomes = list(ex.map(evaluate, [p["params"] for p in props], seeds))
            for prop, s, o in zip(props, seeds, outcomes):
                history.append({"i": len(history), "params": prop["params"], "why": prop["why"], "seed": s, "outcome": o})
            if progress:
                progress(f"{searcher.name}: {len(history)}/{budget}, failures so far {sum(h['outcome']['failed'] for h in history)}")
    return history


def replay(history: list[dict], config: str, workers: int = 4) -> list[dict]:
    """Fly the same tests (conditions and seeds) again with another configuration, for paired ablations."""
    from concurrent.futures import ProcessPoolExecutor
    from functools import partial
    with ProcessPoolExecutor(workers) as ex:
        outcomes = list(ex.map(partial(evaluate, config=config), [h["params"] for h in history], [h["seed"] for h in history]))
    return [{**h, "outcome": o} for h, o in zip(history, outcomes)]


def failure_kinds(history: list[dict]) -> set:
    """Coarse labels of distinct failure modes, for counting variety, not just volume.

    A label looks like ``what:cause+cause``. ``what`` is the first of collision, obstacle, ground and
    near-miss that the flight had. The causes are the parameters past the hand-set thresholds below
    (forest, gusts, radio, gps, tight, failures, reform), sorted, or ``other`` if none is. The kind
    counts reported for the stress searches (13 each for Nemotron and corner search in
    ``docs/DEVPOST.md``, 3 of them in common) depend on these thresholds.
    """
    kinds = set()
    for h in history:
        o, p = h["outcome"], h["params"]
        if not o["failed"]:
            continue
        what = "collision" if o["collisions"] else "obstacle" if o["obstacle_crashes"] else "ground" if o["ground_crashes"] else "near-miss"
        cause = []
        if p["tree_density"] > 5 and p["trail_width_m"] < 20:
            cause.append("forest")
        if p["gust_front_mps"] > 8 or p["turbulence_mps"] > 1.5:
            cause.append("gusts")
        if p["packet_loss"] > 0.3 or p["radio_latency_s"] > 0.3:
            cause.append("radio")
        if p["gps_noise_m"] > 0.5 or p["gps_bias_m"] > 0.7:
            cause.append("gps")
        if p["spacing_m"] < 3.5:
            cause.append("tight")
        if p["motor_failures"] >= 2:
            cause.append("failures")
        if p["formation_change"]:
            cause.append("reform")
        kinds.add(what + ":" + "+".join(sorted(cause) or ["other"]))
    return kinds


def summarise_search(history: list[dict]) -> dict:
    sev = [h["outcome"]["severity"] for h in history]
    first = next((h["i"] + 1 for h in history if h["outcome"]["failed"]), None)
    fails = [h for h in history if h["outcome"]["failed"]]
    levels = [stress_level(h["params"]) for h in fails]
    return {"tests": len(history), "random_top_ups": sum(h["why"].startswith("random top-up") for h in history),
            "failures": len(fails),
            # the same conditions repeated with another seed count once
            "distinct_failing_conditions": len({json.dumps(clip_params(h["params"]), sort_keys=True) for h in fails}),
            "distinct_failure_kinds": len(failure_kinds(history)), "first_failure_at": first,
            "mildest_failure_stress": min(levels) if levels else None,
            "mean_failure_stress": round(float(np.mean(levels)), 3) if levels else None,
            "max_severity": max(sev) if sev else None, "mean_severity": float(np.mean(sev)) if sev else None}


def _lost(o: dict) -> int:
    return 2 * o["collisions"] + o["obstacle_crashes"] + o["ground_crashes"]


def compare_replays(named: dict[str, dict]) -> str:
    """Markdown table pairing each re-flown search with the search it re-flew, test by test.

    `named` maps file names to saved searches; a re-flown search names its source in `replay_of`.
    """
    groups: dict[tuple, list[int]] = {}
    for r in named.values():
        src = named.get(r.get("replay_of", ""))
        if src is None:
            continue
        # g holds the table's columns in order: flights, failed as searched, failed when re-flown, failed
        # only when re-flown, failed only as searched, drones lost as searched, drones lost when re-flown.
        g = groups.setdefault((src["searcher"].split(":")[0], r["config"]), [0] * 7)
        for a, b in zip(src["history"], r["history"]):
            fa, fb = a["outcome"]["failed"], b["outcome"]["failed"]
            g[0] += 1
            g[1] += fa
            g[2] += fb
            g[3] += fb and not fa
            g[4] += fa and not fb
            g[5] += _lost(a["outcome"])
            g[6] += _lost(b["outcome"])
    if not groups:
        return ""
    lines = ["| Tests from | Re-flown on | Flights | Failed as searched | Failed when re-flown | Failed only when re-flown "
             "| Failed only as searched | Drones lost as searched | Drones lost when re-flown |",
             "|---|---|---|---|---|---|---|---|---|"]
    for (name, config), g in groups.items():
        lines.append(f"| {name} | {config} | " + " | ".join(str(x) for x in g) + " |")
    return "\n".join(lines) + "\n"


def compare_searches(results: list[dict]) -> str:
    """Markdown table comparing searchers over several saved searches (the JSON `halo stress` writes)."""
    by: dict[str, list[dict]] = {}
    for r in results:
        name = r["searcher"].split(":")[0] + (f" (tests re-flown on {r['config']})" if r.get("config") else "")
        by.setdefault(name, []).append(r)
    lines = ["| Searcher | Searches | Flights | Failures | Distinct failing conditions | Failure kinds (all searches) "
             "| Mildest failure (stress level) | Mean stress level of failures | Random top-ups |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, rs in by.items():
        hist = [h for r in rs for h in r["history"]]
        sums = [summarise_search(r["history"]) for r in rs]
        fails = [h for h in hist if h["outcome"]["failed"]]
        levels = [stress_level(h["params"]) for h in fails]
        mild = f"{min(levels):.2f}" if levels else "n/a"
        mean = f"{np.mean(levels):.2f}" if levels else "n/a"
        distinct = len({json.dumps(clip_params(h["params"]), sort_keys=True) for h in fails})
        lines.append(f"| {name} | {len(rs)} | {len(hist)} | {len(fails)} | {distinct} "
                     f"| {len(failure_kinds(hist))} | {mild} | {mean} | {sum(s['random_top_ups'] for s in sums)} |")
    return "\n".join(lines) + "\n"
