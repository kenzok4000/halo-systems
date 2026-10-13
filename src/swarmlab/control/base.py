"""Controller interface shared by the original and improved controllers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..sim.body import DroneBody
from ..sim.world import World
from .directive import Directive
from .formations import Path
from .plan import FormationPlan


@dataclass
class Observation:
    """Everything a controller may use at one step (no ground truth)."""

    t: float
    p_est: np.ndarray        # (n, 3) own navigation estimates
    v_est: np.ndarray        # (n, 3)
    nbr_pos: np.ndarray      # (n, n, 3) drone i's view of drone j
    nbr_vel: np.ndarray      # (n, n, 3)
    nbr_age: np.ndarray      # (n, n) seconds since last message (inf = never)
    active: np.ndarray       # (n,) drones flying in the formation


class Controller:
    """Turns the formation plan into an acceleration demand per drone (50 Hz)."""

    name = "controller"
    label = "Controller"
    reassign = False
    blend = False
    pace = False

    def __init__(self, body: DroneBody, world: World, accel_limit: float = 8.0, speed_limit: float = 12.0,
                 floor_z: float = 1.5):
        self.body = body
        self.world = world
        self.accel_limit = accel_limit
        self.speed_limit = speed_limit
        self.floor_z = floor_z
        # Accuracy of the navigation sensor (1 sigma per axis, metres), set by the simulator from the
        # sensor spec, as a real controller is configured from its GNSS datasheet.
        self.nav_sigma_m = 0.0
        self.plan: FormationPlan | None = None
        self.compute_s: list[float] = []

    def reset(self, path: Path, directive: Directive, n: int) -> None:
        self.plan = FormationPlan(path, directive, n, reassign=self.reassign, blend=self.blend, pace=self.pace)

    def set_directive(self, directive: Directive, obs: Observation) -> None:
        self.plan.set_directive(directive, obs.p_est, obs.t)

    def update_members(self, obs: Observation) -> None:
        self.plan.set_members(np.nonzero(obs.active)[0], obs.p_est, obs.t)

    def formation_error_estimate(self, obs: Observation) -> float:
        pos, _ = self.plan.slots()
        a = obs.active
        if not a.any():
            return 0.0
        return float(np.sqrt(np.mean(np.sum((obs.p_est[a] - pos[a]) ** 2, axis=1))))

    def shape_error_estimate(self, obs: Observation) -> float:
        """Like ``formation_error_estimate`` with the swarm's common offset removed."""
        pos, _ = self.plan.slots()
        a = obs.active
        if not a.any():
            return 0.0
        rel = (obs.p_est[a] - obs.p_est[a].mean(axis=0)) - (pos[a] - pos[a].mean(axis=0))
        return float(np.sqrt(np.mean(np.sum(rel ** 2, axis=1))))

    def command(self, obs: Observation, dt: float) -> np.ndarray:
        raise NotImplementedError


def prioritized_clip(demand: np.ndarray, limit: float, axis: np.ndarray | None = None, first: np.ndarray | None = None):
    """Clip each row of ``demand`` to ``limit``; rows in ``first`` give ``axis`` first call.

    Port of the original engine's priority rule for one axis: the
    component along ``axis`` (e.g. pulling up near the ground) is served
    first, the rest gets whatever budget is left.
    """
    out = demand.copy()
    norm = np.linalg.norm(out, axis=1, keepdims=True)
    out = out * np.minimum(1.0, limit / np.maximum(norm, 1e-12))
    if axis is not None and first is not None and first.any():
        d = demand[first]
        along = d @ axis
        take = np.clip(along, -limit, limit)
        rest = d - along[:, None] * axis[None, :]
        budget = np.sqrt(np.maximum(0.0, limit ** 2 - take ** 2))
        rn = np.linalg.norm(rest, axis=1)
        rest = rest * np.minimum(1.0, budget / np.maximum(rn, 1e-12))[:, None]
        out[first] = take[:, None] * axis[None, :] + rest
    return out
