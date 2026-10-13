"""Scenario description: everything needed to reproduce a run from a seed."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..control.directive import Directive
from .sensing import SensingConfig
from .wind import WindConfig
from .world import World


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

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "radius_m": self.radius_m, "kind": self.kind}


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
        }
