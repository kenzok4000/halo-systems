"""The benchmark scenarios.

Each scenario is a function of the seed, so held-out seeds give new
forests, gust timings, failure times and wind directions, not just new
sensor noise. Every scenario also carries a plain-language mission text,
which is what an operator would type (used by the mission compiler).

The order and number of rng draws in each builder are part of the
benchmark: changing them changes every seed. The positional arguments of
each ``Directive(...)`` call are formation, spacing_m, speed_mps,
altitude_m and safety_margin_m.

Civilian use cases covered: wildfire scouting, search and rescue,
agricultural and forest survey, infrastructure inspection, delivery and
inspection transit, and light shows.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from ..control.directive import Directive
from ..control.formations import lawnmower
from ..sim.scenario import MotorFailure, Scenario, ScriptedDirective, SensorConfig, Target
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
        use_case="delivery and inspection transit", tags=("baseline",),
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
    # A few lone trees in the open before and after the forest. Both x positions
    # are drawn and one is chosen; keep it that way so every seed's later draws,
    # and so its layout, stay the same.
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
        use_case="light show / long survey flights", tags=("failure",),
    )


def low_battery(seed: int = 0, n: int = 16) -> Scenario:
    rng = _rng(seed, 7)
    # Not used. The draw is kept so that every seed's later draws, and so its
    # headwind, stay the same.
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


SCOUT_FOV_DEG = 45.0          # a small radiometric thermal camera's horizontal field of view
SCOUT_ALTITUDE_M = 20.0       # low enough to resolve a smouldering patch of a few metres
SCOUT_SPACING_M = 14.0        # 15% overlap between neighbouring footprints (16.6 m wide at 20 m)
SCOUT_SPEED_MPS = 6.0


def wildfire_scout(seed: int = 0, n: int = 16, block_m: tuple = (300.0, 200.0), hotspots: int = 6) -> Scenario:
    """Find smouldering hotspots in a block of dry scrub the morning after a lightning storm.

    The swarm sweeps the block line abreast with thermal cameras looking down. The lane
    width follows from the camera footprint and the number of drones, so the same block
    takes one drone many passes and sixteen drones one: the value of a swarm is the
    time to find every hotspot, measured against swarm size (``make("wildfire_scout", seed, n=4)``).
    """
    rng = _rng(seed, 11)
    w, h = float(block_m[0]), float(block_m[1])
    sensor = SensorConfig(fov_deg=SCOUT_FOV_DEG, dwell_s=0.5, cell_m=5.0, area=(0.0, 0.0, w, h))
    swath = 2.0 * float(sensor.footprint_radius(SCOUT_ALTITUDE_M))
    lane = (max(n, 1) - 1) * SCOUT_SPACING_M + swath
    path = lawnmower(0.0, 0.0, w, h, lane)
    margin = 10.0
    targets = [Target(float(x), float(y), float(r), "hotspot")
               for x, y, r in zip(rng.uniform(margin, w - margin, hotspots), rng.uniform(margin, h - margin, hotspots),
                                  rng.uniform(1.5, 4.0, hotspots))]
    length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    return Scenario(
        name="wildfire_scout",
        title="Wildfire scout",
        description=f"Thermal sweep of a {w:.0f} x {h:.0f} m block of dry scrub for smouldering hotspots the morning "
                    f"after a lightning storm, line abreast at {SCOUT_ALTITUDE_M:.0f} m so the camera footprints overlap.",
        n=n, duration_s=float(min(1800.0, 60.0 + 1.6 * length / SCOUT_SPEED_MPS)), waypoints=path,
        directive=Directive("line", SCOUT_SPACING_M, SCOUT_SPEED_MPS, SCOUT_ALTITUDE_M, 1.0, source="mission"),
        wind=_wind_from(rng, rng.uniform(3.0, 6.0), sigma=1.0, local=0.4),
        targets=targets, sensor=sensor,
        use_case="wildfire scouting: finding ignitions before they grow", tags=("survey", "scouting"),
    )


VALLEY_EXTENT = (-400.0, -350.0, 900.0, 650.0)     # the whole valley, ridges included (multiples of 10 m)
VALLEY_BLOCK = (0.0, 0.0, 500.0, 300.0)           # the survey block on the valley floor


def _valley_axis(terrain, block):
    """y of the valley floor (lowest ground across the block's band) at every x node of the terrain."""
    bx0, by0, bx1, by1 = block
    ys = terrain.y0 + np.arange(terrain.heights.shape[0]) * terrain.cell_m
    band = (ys >= by0 - 50.0) & (ys <= by1 + 50.0)
    h = np.where(band[:, None], terrain.heights, np.inf)
    return ys[np.argmin(h, axis=0)]


def _pick(rng, terrain, box, want, tries=400):
    """A random point in ``box`` (x0, y0, x1, y1) where ``want(x, y)`` holds; the last try if none does."""
    x0, y0, x1, y1 = box
    for _ in range(tries):
        x, y = float(rng.uniform(x0, x1)), float(rng.uniform(y0, y1))
        if want(x, y):
            break
    return x, y


def wildfire_valley(seed: int = 0, n: int = 16, alert_rule: str = "forecast", approval: str = "auto") -> Scenario:
    """Scout a mountain valley for lightning fires, forecast their spread and warn the phones in their way.

    The morning after dry lightning, five fires smoulder in a valley between two ridges: one already
    spreading in open scrub, one under tree crowns, one under a rock overhang where no camera looking down
    can see it, one smouldering in the open, and one across the valley outside the survey block. The swarm
    sweeps the block with thermal cameras looking down (as ``wildfire_scout``) while its forward cameras
    watch for smoke; the ground station locates fires from both, forecasts their spread with an ensemble of
    Rothermel runs under the forecast weather, briefs the fire crews, and drafts evacuation alerts for the
    phones in their path: zone alerts at Japan-style levels, issued by fixed rules once checked (simulated
    phones only), which an operator can cancel or clear.
    Everything that is true here (spread, smoke, weather) is hidden from the station except through the
    drones' sensors and a weather forecast with errors; ``docs/WILDFIRE.md`` lists every assumption.
    """
    from ..sim.fire import Moisture, Period
    from ..sim.terrain import FOREST, GRASS, SCRUB, valley
    from ..sim.wildfire import AlertPolicy, FireSpec, Phone, WildfireConfig, grid_zones
    rng = _rng(seed, 21)
    terrain = valley(rng, VALLEY_EXTENT, cell_m=5.0, block=VALLEY_BLOCK)
    bx0, by0, bx1, by1 = VALLEY_BLOCK
    axis = _valley_axis(terrain, VALLEY_BLOCK)
    xs = terrain.x0 + np.arange(terrain.heights.shape[1]) * terrain.cell_m

    def floor_y(x):
        return float(np.interp(x, xs, axis))

    def ground(x, y):
        return float(terrain.height(x, y))

    def slope(x, y):
        gx, gy = terrain.gradient(x, y)
        return float(np.hypot(gx, gy))

    cover = lambda x, y: int(terrain.cover_at(x, y))        # noqa: E731
    # -- weather: light morning wind turning up-valley and freshening, fuels drying through the day --------
    heading = math.radians(rng.uniform(-70.0, 70.0))        # where the wind blows to; 0 = up the valley (+x)
    calm = float(rng.uniform(1.5, 3.0))
    freshen = float(rng.uniform(0.4, 0.9))                  # m/s per hour after the flight starts
    veer = math.radians(rng.uniform(-8.0, 8.0))             # per hour
    weather = []
    for h in range(-6, 5):
        speed = calm if h <= 0 else min(calm + freshen * h, 9.0)
        ang = heading + veer * h
        d1 = float(np.clip(0.10 - 0.006 * (h + 6), 0.05, 0.12))
        weather.append(Period(h * 3600.0, (speed * math.cos(ang), speed * math.sin(ang)),
                              Moisture(d1, d1 + 0.01, d1 + 0.02, 1.0)))
    # The forecast the station is given: right in shape, wrong in detail.
    bias_speed = float(np.exp(rng.normal(0.0, 0.2)))
    bias_dir = math.radians(rng.normal(0.0, 12.0))
    told = []
    for p in weather:
        wx, wy = p.wind_mps
        k = bias_speed * float(np.exp(rng.normal(0.0, 0.1)))
        a = bias_dir + math.radians(rng.normal(0.0, 5.0))
        dm = float(rng.normal(0.0, 0.008))
        m = p.moisture
        told.append(Period(p.start_s, ((wx * math.cos(a) - wy * math.sin(a)) * k, (wx * math.sin(a) + wy * math.cos(a)) * k),
                           Moisture(max(0.03, m.d1 + dm), max(0.03, m.d10 + dm), max(0.03, m.d100 + dm), m.live)))
    # -- fires ----------------------------------------------------------------------------------------------
    inner = (bx0 + 20.0, by0 + 20.0, bx1 - 20.0, by1 - 20.0)
    fires, boxes, overhangs = [], [], []
    x, y = _pick(rng, terrain, inner, lambda x, y: cover(x, y) in (SCRUB, GRASS))
    fires.append(FireSpec(x, y, float(rng.uniform(-35.0, -10.0)) * 60.0, smoulder_area_m2=4.0, shelter="open"))
    x, y = _pick(rng, terrain, inner, lambda x, y: cover(x, y) == FOREST)
    canopy = float(terrain.canopy_at(x, y))
    fires.append(FireSpec(x, y, float(rng.uniform(20.0, 150.0)) * 60.0, smoulder_area_m2=3.0, shelter="canopy",
                          cover=float(rng.uniform(0.6, 0.8)), occluder_m=0.6 * canopy))
    x, y = _pick(rng, terrain, inner, lambda x, y: slope(x, y) > 0.2 and cover(x, y) != FOREST)
    g = ground(x, y)
    shelf = (round(x - 7.0, 1), round(y - 5.0, 1), round(g + 2.5, 1)), (round(x + 7.0, 1), round(y + 5.0, 1), round(g + 4.5, 1))
    boxes.append(Box(*shelf))
    overhangs.append({"x": round(x, 1), "y": round(y, 1), "lo": list(shelf[0]), "hi": list(shelf[1])})
    fires.append(FireSpec(x, y, float(rng.uniform(10.0, 90.0)) * 60.0, smoulder_area_m2=4.0, shelter="overhang",
                          cover=1.0, occluder_m=4.5))
    x, y = _pick(rng, terrain, inner, lambda x, y: cover(x, y) in (SCRUB, GRASS))
    fires.append(FireSpec(x, y, float(rng.uniform(30.0, 240.0)) * 60.0, smoulder_area_m2=4.0, shelter="open"))
    side = 1.0 if rng.random() < 0.5 else -1.0
    far_y = (by1 + 60.0, by1 + 200.0) if side > 0 else (by0 - 200.0, by0 - 60.0)
    x, y = _pick(rng, terrain, (bx0 + 50.0, far_y[0], bx1 - 50.0, far_y[1]), lambda x, y: cover(x, y) in (SCRUB, GRASS, FOREST))
    fires.append(FireSpec(x, y, float(rng.uniform(-20.0, 30.0)) * 60.0, smoulder_area_m2=6.0, shelter="open"))
    # -- obstacles: a radio mast on the knoll and dead snags standing above the crowns ---------------------------
    cyls = []
    hx, hy = max(((ground(x, y), (x, y)) for x, y in zip(rng.uniform(bx0 + 60, bx1 - 60, 200), rng.uniform(by0 + 40, by1 - 40, 200))))[1]
    cyls.append(Cylinder(float(hx), float(hy), 1.5, ground(hx, hy) + 45.0))
    for _ in range(3):
        sx, sy = _pick(rng, terrain, inner, lambda x, y: cover(x, y) == FOREST)
        cyls.append(Cylinder(sx, sy, 0.6, ground(sx, sy) + float(terrain.canopy_at(sx, sy)) + float(rng.uniform(18.0, 26.0))))
    # -- people: homes down the valley, a campground up it, hikers, drivers and a ranch ----------------------------
    phones, places = [], []

    def add(group, pts):
        for px, py in pts:
            phones.append(Phone(len(phones), float(px), float(py), group))

    hx0 = bx1 + float(rng.uniform(140.0, 280.0))
    homes = [(hx0 + r * math.cos(a), floor_y(hx0) + 0.7 * r * math.sin(a))
             for r, a in zip(rng.uniform(0, 90, 26), rng.uniform(0, 2 * math.pi, 26))]
    add("home", homes)
    places.append({"name": "Cedar Flat homes", "x": round(hx0, 1), "y": round(floor_y(hx0), 1), "kind": "homes"})
    cx0 = bx0 - float(rng.uniform(120.0, 260.0))
    camp = [(cx0 + r * math.cos(a), floor_y(cx0) + r * math.sin(a))
            for r, a in zip(rng.uniform(0, 45, 14), rng.uniform(0, 2 * math.pi, 14))]
    add("campground", camp)
    places.append({"name": "Pine Hollow campground", "x": round(cx0, 1), "y": round(floor_y(cx0), 1), "kind": "campground"})
    tside = -side
    t_y0 = (by1 + 40.0) if tside > 0 else (by0 - 40.0)
    trail = [(float(tx), t_y0 + tside * float(rng.uniform(0.0, 80.0))) for tx in rng.uniform(bx0 - 50, bx1 + 50, 8)]
    add("trail", trail)
    places.append({"name": "Ridge trail", "x": round(0.5 * (bx0 + bx1), 1), "y": round(t_y0 + tside * 40.0, 1), "kind": "trail"})
    road = [(float(rx), floor_y(rx) + float(rng.normal(0.0, 4.0))) for rx in rng.uniform(-350.0, 850.0, 10)]
    add("road", road)
    places.append({"name": "Valley road bridge", "x": 250.0, "y": round(floor_y(250.0), 1), "kind": "road"})
    rx0 = float(rng.uniform(bx0 + 50, bx1 - 50))
    ry0 = (by0 - float(rng.uniform(90.0, 160.0))) if side > 0 else (by1 + float(rng.uniform(90.0, 160.0)))
    add("ranch", [(rx0 + float(rng.normal(0, 15)), ry0 + float(rng.normal(0, 15))) for _ in range(5)])
    places.append({"name": "Dry Creek ranch", "x": round(rx0, 1), "y": round(ry0, 1), "kind": "ranch"})
    # -- the flight -----------------------------------------------------------------------------------------------------
    sensor = SensorConfig(fov_deg=SCOUT_FOV_DEG, dwell_s=0.5, cell_m=5.0, area=VALLEY_BLOCK)
    swath = 2.0 * float(sensor.footprint_radius(SCOUT_ALTITUDE_M))
    lane = (max(n, 1) - 1) * SCOUT_SPACING_M + swath
    path = lawnmower(bx0, by0, bx1 - bx0, by1 - by0, lane)
    length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    targets = [Target(f.x, f.y, math.sqrt(f.smoulder_area_m2 / math.pi), "fire", cover=f.cover) for f in fires]
    x0, y0, x1, y1 = VALLEY_EXTENT
    world = World(cylinders=cyls, boxes=boxes, geofence_lo=(x0, y0, 0.0),
                  geofence_hi=(x1, y1, float(terrain.heights.max()) + 150.0), terrain=terrain)
    w0 = weather[6].wind_mps
    # Fixed evacuation zones: south slopes, valley floor and north slopes, four across from west to east.
    zones = grid_zones([x0, bx0, 0.5 * (bx0 + bx1), bx1, x1], [y0, by0 - 30.0, by1 + 30.0, y1],
                       [("S", "South slopes"), ("F", "Valley floor"), ("N", "North slopes")], places)
    wildfire = WildfireConfig(fires=fires, weather=weather, weather_forecast=told, phones=phones, places=places,
                              overhangs=overhangs, zones=zones, alerts=AlertPolicy(rule=alert_rule, approval=approval))
    return Scenario(
        name="wildfire_valley",
        title="Wildfire valley",
        description=f"Lightning fires in a mountain valley: a {bx1 - bx0:.0f} x {by1 - by0:.0f} m thermal sweep line "
                    f"abreast at {SCOUT_ALTITUDE_M:.0f} m above the trees, smoke watch on the forward cameras, a spread "
                    f"forecast for the fire crews and automatic, checked zone alerts for {len(phones)} simulated phones.",
        n=n, duration_s=float(min(2400.0, 90.0 + 1.6 * length / SCOUT_SPEED_MPS)), waypoints=path,
        directive=Directive("line", SCOUT_SPACING_M, SCOUT_SPEED_MPS, SCOUT_ALTITUDE_M, 1.0, source="mission"),
        world=world,
        wind=_wind_from(rng, math.hypot(*w0), sigma=1.0, local=0.4, direction_deg=math.degrees(math.atan2(w0[1], w0[0]))),
        targets=targets, sensor=sensor, wildfire=wildfire,
        use_case="wildfire scouting: finding fires, forecasting their spread and warning people in their way",
        tags=("survey", "scouting", "wildfire", "terrain"),
    )


SCENARIOS = {
    f.__name__: f for f in (open_transit, formation_changes, forest_trail, gusty_day, comms_degraded,
                            motor_failures, low_battery, field_survey, urban_canyon, wildfire_scout, wildfire_valley)
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
