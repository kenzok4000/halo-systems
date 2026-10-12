"""The benchmark scenarios.

Each scenario is a function of the seed, so held-out seeds give new
forests, gust timings, failure times and wind directions, not just new
sensor noise. Every scenario also carries a plain-language mission text,
which is what an operator would type (used by the mission compiler).

Civilian use cases covered: search and rescue, agricultural and forest
survey, infrastructure inspection, delivery convoys, and light shows.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from ..control.directive import Directive
from ..control.formations import lawnmower
from ..sim.scenario import MotorFailure, Scenario, ScriptedDirective
from ..sim.sensing import Blackout, SensingConfig
from ..sim.wind import GustFront, WindConfig
from ..sim.world import Box, Cylinder, World


def _rng(seed: int, salt: int) -> np.random.Generator:
    """Scenario-layout randomness, separate from the run's physics streams."""
    return np.random.default_rng(np.random.SeedSequence([seed, salt]))


def _wind_from(rng, speed, sigma=0.8, local=0.3, direction_deg=None):
    ang = math.radians(direction_deg if direction_deg is not None else rng.uniform(0, 360))
    return WindConfig(mean_mps=(speed * math.cos(ang), speed * math.sin(ang), 0.0),
                      gust_sigma_mps=sigma, local_sigma_mps=local)


def open_transit(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 1)
    return Scenario(
        name="open_transit",
        title="Open-field transit",
        description="Fly a grid formation 300 m across open ground in light, gusty wind.",
        n=n, duration_s=80.0, waypoints=[(0.0, 0.0), (300.0, 0.0)],
        directive=Directive("grid", 6.0, 6.0, 20.0, 1.0, source="mission"),
        wind=_wind_from(rng, rng.uniform(1.0, 4.0)),
        use_case="delivery convoy / inspection transit", tags=("baseline",),
    )


def formation_changes(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 2)
    order = ["line", "v", "ring", "column", "grid"]
    times = [12.0, 30.0, 48.0, 66.0, 84.0]
    jitter = rng.uniform(-2.0, 2.0, len(times))
    return Scenario(
        name="formation_changes",
        title="Formation changes on a circuit",
        description="Operator orders five formation changes while the swarm flies a rectangular circuit.",
        n=n, duration_s=100.0, waypoints=[(0, 0), (160, 0), (160, 110), (0, 110)], loop=True,
        directive=Directive("grid", 6.0, 5.0, 25.0, 1.0, source="mission"),
        wind=_wind_from(rng, rng.uniform(1.0, 3.0)),
        script=[ScriptedDirective(t + j, {"formation": f}) for t, j, f in zip(times, jitter, order)],
        end_when_done=False, use_case="light show / research", tags=("transition",),
    )


def forest_trail(seed: int = 0, n: int = 16) -> Scenario:
    """Under-canopy flight along a narrow trail through dense forest."""
    rng = _rng(seed, 3)
    trail = rng.uniform(9.0, 13.0)
    centre_y = rng.uniform(-2.0, 2.0)
    x0, x1 = 140.0, 280.0
    trees = []
    # Poisson forest (about one tree per 45 m^2) on both sides of the trail.
    area = (x1 - x0) * 120.0
    for _ in range(rng.poisson(area / 45.0)):
        x, y = rng.uniform(x0, x1), rng.uniform(-60.0, 60.0)
        r = rng.uniform(0.25, 0.6)
        if abs(y - centre_y) < trail / 2 + r:
            continue
        trees.append(Cylinder(float(x), float(y), float(r), float(rng.uniform(26.0, 36.0))))
    # A few lone trees in the open before and after the forest.
    for _ in range(12):
        x = rng.choice([rng.uniform(40.0, 120.0), rng.uniform(300.0, 380.0)])
        y = rng.uniform(-50.0, 50.0)
        if abs(y) < 14.0:
            continue
        trees.append(Cylinder(float(x), float(y), float(rng.uniform(0.3, 0.8)), float(rng.uniform(15.0, 30.0))))
    return Scenario(
        name="forest_trail",
        title="Forest trail under the canopy",
        description=f"Search along a {trail:.0f} m wide trail through 140 m of dense forest, below the canopy (ceiling 22 m).",
        n=n, duration_s=150.0, waypoints=[(0.0, centre_y), (420.0, centre_y)],
        directive=Directive("grid", 6.0, 5.0, 15.0, 1.0, source="mission"),
        world=World(cylinders=trees),
        wind=_wind_from(rng, rng.uniform(0.5, 2.0), sigma=0.4, local=0.2),
        altitude_max_m=22.0,
        use_case="search and rescue under canopy", tags=("obstacles", "adaptation"),
    )


def gusty_day(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 4)
    base = rng.uniform(0, 360)
    fronts = []
    t = rng.uniform(10.0, 16.0)
    while t < 70.0:
        ang = math.radians(base + rng.uniform(60, 120) * rng.choice([-1, 1]))
        mag = rng.uniform(7.0, 11.0)
        dur = rng.uniform(4.0, 7.0)
        fronts.append(GustFront(float(t), float(dur), (mag * math.cos(ang), mag * math.sin(ang), float(rng.uniform(-1.5, 0.5))), ramp_s=0.6))
        t += dur + rng.uniform(8.0, 14.0)
    wind = _wind_from(rng, rng.uniform(3.0, 5.0), sigma=2.0, local=0.8, direction_deg=base)
    wind.fronts = fronts
    return Scenario(
        name="gusty_day",
        title="Gust fronts",
        description="Close-formation transit (3 m spacing) in strong turbulence with repeated sudden crosswind gust fronts of 7-11 m/s.",
        n=n, duration_s=90.0, waypoints=[(0.0, 0.0), (420.0, 0.0)],
        directive=Directive("grid", 3.0, 6.0, 25.0, 0.8, source="mission"),
        wind=wind, use_case="infrastructure inspection in bad weather", tags=("disturbance",),
    )


def comms_degraded(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 5)
    start = float(rng.uniform(18.0, 25.0))
    cut = sorted(int(i) for i in rng.choice(n, size=max(1, n // 3), replace=False))
    return Scenario(
        name="comms_degraded",
        title="Degraded radio",
        description="Transit with 35% packet loss, 250 ms latency, and a 10 s blackout for a third of the swarm.",
        n=n, duration_s=90.0, waypoints=[(0.0, 0.0), (380.0, 0.0)],
        directive=Directive("grid", 4.5, 6.0, 20.0, 0.8, source="mission"),
        wind=_wind_from(rng, rng.uniform(2.0, 4.0), sigma=1.0, local=0.4),
        sensing=SensingConfig(latency_s=0.25, jitter_s=0.1, loss_prob=0.35,
                              blackouts=[Blackout(start, start + 10.0, cut)]),
        use_case="search and rescue in a radio-shadowed valley", tags=("comms",),
    )


def motor_failures(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 6)
    k = 3 if n >= 8 else 1
    drones = rng.choice(n, size=k, replace=False)
    times = np.sort(rng.uniform(10.0, 45.0, size=k))
    return Scenario(
        name="motor_failures",
        title="Motor failures",
        description="Three drones lose power at random moments; the rest must close the gaps.",
        n=n, duration_s=90.0, waypoints=[(0.0, 0.0), (180.0, 0.0), (180.0, 150.0)],
        directive=Directive("ring", 5.0, 5.0, 25.0, 1.0, source="mission"),
        wind=_wind_from(rng, rng.uniform(1.0, 3.0)),
        failures=[MotorFailure(float(t), int(d)) for t, d in zip(times, drones)],
        use_case="light show / persistent monitoring", tags=("failure",),
    )


def low_battery(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 7)
    heading = rng.uniform(0, 360)
    head = rng.uniform(3.0, 5.0)
    return Scenario(
        name="low_battery",
        title="Low battery, into the wind",
        description="Finish a 1.2 km leg into a headwind with batteries at about 32%; drones below 15% must leave.",
        n=n, duration_s=400.0, waypoints=[(0.0, 0.0), (1200.0, 0.0)],
        directive=Directive("grid", 6.0, 5.0, 30.0, 1.0, source="mission"),
        wind=_wind_from(rng, head, sigma=1.0, local=0.3, direction_deg=180.0 + rng.uniform(-20, 20)),
        battery_fraction=0.32, battery_spread=0.1,
        use_case="agricultural survey return leg", tags=("energy",),
    )


def field_survey(seed: int = 0, n: int = 12) -> Scenario:
    rng = _rng(seed, 8)
    lane = 70.0
    path = lawnmower(0.0, 0.0, 280.0, 3 * lane, lane, heading_deg=float(rng.uniform(-10, 10)))
    return Scenario(
        name="field_survey",
        title="Field survey (lawnmower)",
        description="Sweep a 280 x 210 m field in line-abreast lanes for crop scouting or a missing-person search.",
        n=n, duration_s=240.0, waypoints=path,
        directive=Directive("line", 6.0, 6.0, 30.0, 1.0, source="mission"),
        wind=_wind_from(rng, rng.uniform(2.0, 5.0), sigma=1.0, local=0.4),
        use_case="agricultural survey / search and rescue", tags=("survey",),
    )


def urban_canyon(seed: int = 0, n: int = 16) -> Scenario:
    """Inspection flight down a street between building blocks."""
    rng = _rng(seed, 9)
    street = rng.uniform(14.0, 20.0)
    boxes = []
    x = 80.0
    while x < 320.0:
        length = rng.uniform(20.0, 45.0)
        for side in (-1, 1):
            depth = rng.uniform(15.0, 30.0)
            height = rng.uniform(25.0, 60.0)
            y_in = side * street / 2
            y_out = side * (street / 2 + depth)
            boxes.append(Box((x, min(y_in, y_out), 0.0), (x + length, max(y_in, y_out), height)))
        x += length + rng.uniform(8.0, 14.0)
    return Scenario(
        name="urban_canyon",
        title="Urban street canyon",
        description=f"Inspect facades along a {street:.0f} m wide street, staying below 22 m.",
        n=n, duration_s=150.0, waypoints=[(0.0, 0.0), (400.0, 0.0)],
        directive=Directive("grid", 6.0, 5.0, 15.0, 1.0, source="mission"),
        world=World(boxes=boxes),
        wind=_wind_from(rng, rng.uniform(2.0, 4.0), sigma=1.2, local=0.5),
        altitude_max_m=22.0,
        use_case="infrastructure inspection", tags=("obstacles", "adaptation"),
    )


SCENARIOS = {
    f.__name__: f for f in (open_transit, formation_changes, forest_trail, gusty_day, comms_degraded,
                            motor_failures, low_battery, field_survey, urban_canyon)
}

# Scenarios where the swarm has to change its plan to do well (supervisor benchmark).
ADAPTIVE = ("forest_trail", "urban_canyon", "gusty_day", "comms_degraded", "low_battery", "motor_failures")


def make(name: str, seed: int = 0, n: int | None = None, **overrides) -> Scenario:
    """Build a scenario by name. ``overrides`` replace any ``Scenario`` field afterwards."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    sc = SCENARIOS[name](seed) if n is None else SCENARIOS[name](seed, n=n)
    return dataclasses.replace(sc, **overrides) if overrides else sc


def from_mission(spec: dict, seed: int = 0, text: str = "") -> Scenario:
    """Build a flyable scenario from a compiled ``MissionSpec`` (open ground, light wind).

    Unstated values take the defaults in ``ai.mission.DEFAULTS``. Values outside
    the flight envelope are left for the safety validator to clamp at take-off,
    so the clamp shows up in the directive log.
    """
    from ..ai.mission import DEFAULTS
    if spec.get("refused"):
        raise ValueError("this mission was refused: " + (spec.get("refusal_reason") or "outside civilian use"))
    d = {k: (v if v is not None else DEFAULTS[k]) for k, v in (spec.get("directive") or {}).items()}
    n = int(min(64, max(2, spec.get("drones") or DEFAULTS["drones"])))
    route = spec.get("route") or {}
    kind = route.get("type") or "transit"
    rng = _rng(seed, 10)
    from ..control.formations import formation_offsets
    width = float(np.ptp(formation_offsets(d["formation"], n, d["spacing_m"])[:, 1]))
    if kind == "survey":
        w = float(route.get("width_m") or 300.0)
        h = float(route.get("height_m") or 200.0)
        lane = float(route.get("lane_m") or max(20.0, width + d["spacing_m"]))
        waypoints = lawnmower(0.0, 0.0, min(w, 1500.0), min(h, 1500.0), lane)
        loop = False
    elif kind == "loop":
        side = max(80.0, 3 * width)
        waypoints = [(0.0, 0.0), (side, 0.0), (side, side * 0.7), (0.0, side * 0.7)]
        loop = True
    else:
        length = float(route.get("length_m") or 300.0)
        waypoints = [(0.0, 0.0), (min(length, 3000.0), 0.0)]
        loop = False
    ceiling = (spec.get("constraints") or {}).get("altitude_max_m") or 120.0
    altitude = d["altitude_m"] if (spec.get("directive") or {}).get("altitude_m") is not None else min(DEFAULTS["altitude_m"], ceiling - 2.0)
    path_len = sum(math.dist(a, b) for a, b in zip(waypoints, waypoints[1:]))
    return Scenario(
        name="custom_mission", title="Operator mission", description=text or "Mission compiled from the operator's request.",
        n=n, duration_s=float(min(900.0, 60.0 + 1.6 * path_len / max(d["speed_mps"], 0.5))) if not loop else 180.0,
        waypoints=waypoints, loop=loop,
        directive=Directive(d["formation"], d["spacing_m"], d["speed_mps"], altitude, d["safety_margin_m"], source="mission"),
        wind=_wind_from(rng, rng.uniform(1.0, 3.0)), altitude_max_m=float(ceiling), end_when_done=not loop,
        use_case="operator mission", tags=("custom",),
    )
