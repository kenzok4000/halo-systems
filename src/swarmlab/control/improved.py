"""The improved conventional controller.

Same sensing and the same acceleration budget as the original, with five
standard techniques from the multi-robot literature:

1. Hungarian slot re-assignment after every formation change or loss, so
   paths do not cross and gaps close (``plan.reassign``).
2. Slots that glide to a new formation at bounded speed (``plan.blend``).
3. A virtual leader that slows while the formation is out of shape
   (``plan.pace``).
4. PD tracking of each slot plus a consensus term on the relative shape, so
   a gust that pushes the whole swarm does not tear the formation apart.
5. A control-barrier-function safety filter (``cbf.py``) for drone-to-drone,
   obstacle and ground clearance, with margins that grow with message age.

It also smooths its own navigation estimate with a simple complementary
filter, which removes measurement jitter from the motor commands.
"""

from __future__ import annotations

import time

import numpy as np

from .base import Controller, Observation
from .cbf import MAX_NEIGHBOURS, OBSTACLE_ROWS, build_constraints, project


FEATURES = ("reassign", "blend", "pace", "consensus", "cbf", "filter", "feedforward")


class ImprovedController(Controller):
    """``features`` switches the techniques on and off individually (for the ablation study)."""

    name = "improved"
    label = "Improved controller"

    kp = 3.0
    kd = 3.2
    kc = 0.8
    a_brake = 3.0
    gamma = 3.0

    def __init__(self, *args, features=FEATURES, speed_limit: float = 20.0, **kwargs):
        super().__init__(*args, speed_limit=speed_limit, **kwargs)
        unknown = set(features) - set(FEATURES)
        if unknown:
            raise ValueError(f"unknown features {sorted(unknown)}; choose from {FEATURES}")
        self.features = frozenset(features)
        self.reassign = "reassign" in self.features
        self.blend = "blend" in self.features
        self.pace = "pace" in self.features
        if self.features != frozenset(FEATURES):
            missing = sorted(set(FEATURES) - self.features)
            self.name = "improved-no-" + "-".join(missing)
            self.label = "Improved without " + ", ".join(missing)
        self._pf = None
        self._vf = None
        self._a_prev = None
        self._ref_v_prev = None
        self.filter_active_steps = 0

    def _filter(self, obs: Observation, dt: float):
        if "filter" not in self.features:
            return obs.p_est, obs.v_est
        if self._pf is None:
            self._pf = obs.p_est.copy()
            self._vf = obs.v_est.copy()
            self._a_prev = np.zeros_like(obs.p_est)
            return self._pf, self._vf
        self._vf = self._vf + self._a_prev * dt
        self._vf += 0.3 * (obs.v_est - self._vf)
        self._pf = self._pf + self._vf * dt
        self._pf += 0.15 * (obs.p_est - self._pf)
        return self._pf, self._vf

    def command(self, obs: Observation, dt: float) -> np.ndarray:
        t0 = time.perf_counter()
        plan = self.plan
        p, v = self._filter(obs, dt)
        act = obs.active
        plan.advance(dt, obs.t, self.shape_error_estimate(obs))
        ref_p, ref_v = plan.slots()
        if self._ref_v_prev is None:
            self._ref_v_prev = ref_v.copy()
            self._ref_a = np.zeros_like(ref_v)
        # Feed-forward of the slot's acceleration, low-passed (0.2 s) so plan steps do not kick.
        raw = (ref_v - self._ref_v_prev) / dt
        self._ref_a += min(1.0, dt / 0.2) * (raw - self._ref_a)
        self._ref_v_prev = ref_v.copy()
        ref_a = self._ref_a if "feedforward" in self.features else 0.0
        spacing = plan.directive.spacing_m

        # Consensus on the relative shape with neighbours heard in the last second.
        rel_now = obs.nbr_pos - p[:, None, :]                       # (n, n, 3)
        rel_want = ref_p[None, :, :] - ref_p[:, None, :]
        dist = np.linalg.norm(rel_now, axis=2)
        near = (np.isfinite(obs.nbr_age) & (obs.nbr_age < 1.0) & act[None, :] & act[:, None]
                & (dist < 2.5 * spacing))
        np.fill_diagonal(near, False)
        cnt = np.maximum(near.sum(axis=1, keepdims=True), 1)
        consensus = np.sum(np.where(near[..., None], rel_now - rel_want, 0.0), axis=1) / cnt
        if "consensus" not in self.features:
            consensus = 0.0

        a_nom = ref_a + self.kp * (ref_p - p) + self.kd * (ref_v - v) + self.kc * consensus
        speed = np.linalg.norm(v, axis=1, keepdims=True)
        over = np.maximum(0.0, speed - self.speed_limit)
        a_nom -= 2.0 * over * v / np.maximum(speed, 1e-9)
        nn = np.linalg.norm(a_nom, axis=1, keepdims=True)
        a_nom = a_nom * np.minimum(1.0, self.accel_limit / np.maximum(nn, 1e-12))

        if "cbf" not in self.features:
            a = a_nom
            a[~act] = 0.0
            self._a_prev = a
            self.compute_s.append(time.perf_counter() - t0)
            return a
        d_safe = 2 * self.body.radius_m + plan.directive.safety_margin_m
        g, b, m = build_constraints(
            p, v, obs.nbr_pos, obs.nbr_vel, obs.nbr_age, act,
            d_safe=d_safe, a_brake=self.a_brake, gamma=self.gamma, world=self.world,
            obstacle_clearance=self.body.radius_m + plan.directive.safety_margin_m, floor_z=self.floor_z)
        # Break symmetric deadlocks in front of obstacles: slide toward the slot's side.
        if self.world.obstacle_count:
            k_nbr = min(MAX_NEIGHBOURS, max(len(p) - 1, 0))
            rows = slice(k_nbr, k_nbr + OBSTACLE_ROWS)  # layout from build_constraints
            blocked = m[:, rows] & (np.einsum("nkd,nd->nk", g[:, rows], a_nom) > b[:, rows])
            if blocked.any():
                n_away = -g[:, rows]
                first = np.argmax(blocked, axis=1)
                rows = np.arange(len(p))
                away = n_away[rows, first]
                tangent = np.stack([-away[:, 1], away[:, 0], np.zeros(len(p))], axis=1)
                side = np.sign(np.einsum("nd,nd->n", ref_p - p, tangent))
                side[side == 0] = 1.0
                push = blocked.any(axis=1)
                a_nom[push] += 0.5 * self.accel_limit * side[push, None] * tangent[push]
        a, active_rows = project(a_nom, g, b, m, self.accel_limit)
        self.filter_active_steps += int(active_rows.any(axis=1)[act].sum())
        a[~act] = 0.0
        self._a_prev = a
        self.compute_s.append(time.perf_counter() - t0)
        return a
