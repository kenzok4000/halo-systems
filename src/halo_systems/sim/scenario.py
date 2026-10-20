"""Scenario description: everything needed to reproduce a run from a seed."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ..control.directive import Directive
from .sensing import SensingConfig
from .wind import WindConfig
from .world import World

if TYPE_CHECKING:  # the wildfire module is only needed by scenarios that have fires
    from .wildfire import WildfireConfig


@dataclass
class MotorFailure:
    t: float
    drone: int


@dataclass
class ScriptedDirective:
    """An operator command at a fixed time (used for formation transitions)."""

    t: float
    changes: dict


@dataclass
class Target:
    """Something on the ground the swarm has to find: a smouldering hotspot, a missing person's heat signature."""

    x: float
    y: float
    radius_m: float = 2.0
    kind: str = "hotspot"
    cover: float = 0.0          # share hidden from a camera looking down (tree crowns, a rock shelf); 1 = never seen

    def to_dict(self) -> dict:
        out = {"x": self.x, "y": self.y, "radius_m": self.radius_m, "kind": self.kind}
        if self.cover:
            out["cover"] = self.cover
        return out


@dataclass
class SensorConfig:
    """A camera looking straight down from each drone.

    The ground footprint is taken as a disc of radius ``altitude * tan(fov_deg / 2)``, the circle
    inscribed in the image, which is conservative for a rectangular sensor. A target counts as
    found once any part of it has been inside some drone's footprint for ``dwell_s`` in total
    (a few frames of a thermal camera). Coverage is kept on a grid of ``cell_m`` cells over
    ``area`` (x0, y0, x1, y1); a cell counts once its centre has been in a footprint.
    """

    fov_deg: float = 0.0        # 0 = no sensor: the scenario has nothing to find
    dwell_s: float = 0.5
    cell_m: float = 5.0
    area: tuple | None = None

    @property
    def enabled(self) -> bool:
        return self.fov_deg > 0.0

    def footprint_radius(self, altitude_m):
        return np.maximum(np.asarray(altitude_m, dtype=float), 0.0) * math.tan(math.radians(self.fov_deg / 2.0))


@dataclass
class Scenario:
    name: str
    title: str
    description: str
    n: int = 16
    duration_s: float = 60.0
    dt: float = 0.02
    waypoints: list = field(default_factory=lambda: [(0.0, 0.0), (200.0, 0.0)])
    loop: bool = False
    directive: Directive = field(default_factory=Directive)
    world: World = field(default_factory=World)
    wind: WindConfig = field(default_factory=WindConfig)
    sensing: SensingConfig = field(default_factory=SensingConfig)
    failures: list[MotorFailure] = field(default_factory=list)
    script: list[ScriptedDirective] = field(default_factory=list)
    battery_fraction: float = 1.0
    battery_spread: float = 0.0            # +- uniform spread of starting charge
    battery_reserve: float = 0.15          # leave the formation below this charge
    supervisor_period_s: float = 5.0
    altitude_max_m: float = 120.0          # airspace ceiling for this mission (validator bound)
    end_when_done: bool = True
    targets: list[Target] = field(default_factory=list)
    sensor: SensorConfig = field(default_factory=SensorConfig)
    use_case: str = "research"
    tags: tuple = ()
    wildfire: "WildfireConfig | None" = None   # fires, weather, phones and alert policy (the wildfire valley)
    # Squads (control/squads.py): None flies one formation along ``waypoints``. Otherwise each squad patrols its
    # own area until ``patrol_s``; ``squad_counts`` is the split asked for ("R": drones kept in reserve, not
    # flown), ``squad_areas`` the areas, and ``squad_script`` operator orders at fixed times.
    squads: list | None = None
    patrol_s: float | None = None
    squad_counts: dict | None = None
    squad_areas: list | None = None
    squad_script: list = field(default_factory=list)
    # Lightning strikes reported overnight (the wildfire valley, when asked for): what a mission may cue on.
    strikes: list | None = None

    def summary(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "drones": self.n,
            "duration_s": self.duration_s,
            "obstacles": self.world.obstacle_count,
            "use_case": self.use_case,
            "tags": list(self.tags),
            "targets": [t.to_dict() for t in self.targets],
            "sensor": {"fov_deg": self.sensor.fov_deg, "dwell_s": self.sensor.dwell_s, "cell_m": self.sensor.cell_m,
                       "area": list(self.sensor.area) if self.sensor.area else None} if self.sensor.enabled else None,
            **({"wildfire": self.wildfire.summary(self.world.terrain)} if self.wildfire is not None else {}),
            **({"squads": [sq.to_dict() for sq in self.squads], "squad_counts": dict(self.squad_counts or {}),
                "patrol_s": self.patrol_s,
                "squad_areas": [{"code": a.code, "name": a.name, "rect": list(a.rect), "ha": round(a.ha, 2)}
                                for a in (self.squad_areas or [])],
                "squad_script": [dict(o) for o in self.squad_script]} if self.squads else {}),
            **({"strikes": [dict(k) for k in self.strikes]} if self.strikes is not None else {}),
        }
