"""The original controller: a passive formation law from an earlier private simulator.

A faithful port of how a formation drone flies in that simulator: velocity target = slot velocity + 1.4 x slot
error + 2.7 x neighbour repulsion, capped at 8 m/s relative to the slot;
acceleration = 2.5 x velocity error within the formation budget, with the
floor guard getting first call. Slots stay in drone-number order and jump
at once on a formation change, as in the original.

The original had no obstacles, so this port adds the textbook
potential-field obstacle repulsion with the same 2.7 gain.
"""

from __future__ import annotations

import time

import numpy as np

from .base import Controller, Observation, prioritized_clip

FORMATION_SPEED = 8.0     # m/s, the original's formation speed
ORIGINAL_SEPARATION = 3.6  # m, the original's default neighbour separation


def clip_rows(v: np.ndarray, limit: float) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v * np.minimum(1.0, limit / np.maximum(n, 1e-12))


def floor_guard(vel: np.ndarray, p: np.ndarray, v: np.ndarray, accel: float, floor_z: float, lag: float = 0.4):
    """The original's floor guard: cap descent so the drone can brake above the floor."""
    height = p[:, 2] - floor_z - lag * np.maximum(0.0, -v[:, 2])
    max_descent = np.sqrt(2 * 0.35 * accel * np.maximum(0.0, height))
    vz = np.where(height < 0, np.maximum(vel[:, 2], np.minimum(2.0, -3 * height)), np.maximum(vel[:, 2], -max_descent))
    out = vel.copy()
    out[:, 2] = vz
    return out


class OriginalController(Controller):
    name = "original"
    label = "Original controller"
    reassign = False
    blend = False
    pace = False

    def command(self, obs: Observation, dt: float) -> np.ndarray:
        t0 = time.perf_counter()
        plan = self.plan
        plan.advance(dt, obs.t)
        ref_p, ref_v = plan.slots()
        p, v, act = obs.p_est, obs.v_est, obs.active
        sep = min(ORIGINAL_SEPARATION, 0.9 * plan.directive.spacing_m)
        diff = p[:, None, :] - obs.nbr_pos
        dist = np.linalg.norm(diff, axis=2)
        known = np.isfinite(obs.nbr_age) & (obs.nbr_age < 5.0) & act[None, :]
        np.fill_diagonal(known, False)
        f = np.where(known & (dist < sep), (sep - dist) / sep / np.maximum(dist, 1e-9), 0.0)
        rep = np.sum(diff * f[..., None], axis=1)
        if self.world.obstacle_count:
            od, away, mask = self.world.nearby(p, 4.0, k=4)
            w = np.where(mask, (4.0 - np.maximum(od, 0.0)) / 4.0, 0.0)
            rep = rep + np.sum(away * w[..., None], axis=1)
        target = ref_v + clip_rows(1.4 * (ref_p - p) + 2.7 * rep, FORMATION_SPEED)
        target = clip_rows(target, self.body.max_speed_mps)
        floor = self.floor_under(p)
        guarded = floor_guard(target, p, v, self.accel_limit, floor)
        near_floor = (guarded[:, 2] != target[:, 2]) | (p[:, 2] < floor + 1 + np.maximum(0.0, -v[:, 2]) ** 2 / 14)
        demand = 2.5 * (guarded - v)
        a = prioritized_clip(demand, self.accel_limit, np.array([0.0, 0.0, 1.0]), near_floor & act)
        a[~act] = 0.0
        self.compute_s.append(time.perf_counter() - t0)
        return a
