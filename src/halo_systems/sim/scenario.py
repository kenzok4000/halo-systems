"""Scenario description: everything needed to reproduce a run from a seed."""

from __future__ import annotations

from dataclasses import dataclass, field

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
        }
