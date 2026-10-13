"""Point-mass multirotor dynamics with an autopilot thrust loop.

This is the same model as in the original engine: thrust bounded
by the thrust-to-weight ratio, quadratic air drag on the air-relative
velocity, gravity, semi-implicit Euler at 50 Hz. Two additions, both
documented in docs/ARCHITECTURE.md: a first-order thrust response (motor
lag) and a no-downward-thrust limit, since a quadcopter cannot push itself
down. There are no attitude or rotor dynamics.
"""

from __future__ import annotations

import numpy as np

from .body import AIR_DENSITY, GRAVITY, DroneBody

G_VEC = np.array([0.0, 0.0, -GRAVITY])


def drag_accel(velocity: np.ndarray, body: DroneBody) -> np.ndarray:
    """Drag acceleration magnitude-direction for velocities (n, 3) (opposes motion)."""
    speed = np.linalg.norm(velocity, axis=-1, keepdims=True)
    return -0.5 * AIR_DENSITY * body.drag_area_m2 * speed * velocity / body.mass_kg


def autopilot_thrust(a_cmd: np.ndarray, v_est: np.ndarray, body: DroneBody) -> np.ndarray:
    """Thrust force the autopilot asks for to achieve ``a_cmd`` (ground frame).

    It cancels gravity and feeds forward drag on its own ground-velocity
    estimate (it cannot see the wind), then respects the thrust limit and
    never asks for downward thrust.
    """
    m = body.mass_kg
    force = m * (a_cmd - G_VEC - drag_accel(v_est, body))
    force[:, 2] = np.maximum(force[:, 2], 0.0)
    mag = np.linalg.norm(force, axis=1, keepdims=True)
    scale = np.minimum(1.0, body.max_thrust_n / np.maximum(mag, 1e-12))
    return force * scale


def integrate(p, v, thrust, thrust_cmd, wind, powered, body: DroneBody, dt: float):
    """Advance one step. Returns new (p, v, thrust, accel)."""
    alpha = min(1.0, dt / max(body.motor_tau_s, 1e-9))
    thrust = thrust + alpha * (thrust_cmd - thrust)
    thrust = np.where(powered[:, None], thrust, 0.0)
    rel = v - wind
    speed = np.linalg.norm(rel, axis=1, keepdims=True)
    drag = -0.5 * AIR_DENSITY * body.drag_area_m2 * speed * rel
    accel = (thrust + drag) / body.mass_kg + G_VEC
    v_new = v + accel * dt
    p_new = p + v_new * dt
    return p_new, v_new, thrust, accel


def power_draw(thrust: np.ndarray, powered: np.ndarray, body: DroneBody, airspeed: np.ndarray | float = 0.0) -> np.ndarray:
    """Electrical power (W) per drone from its thrust and airspeed (see ``DroneBody.rotor_power_w``)."""
    t = np.linalg.norm(thrust, axis=1)
    return np.where(powered, body.rotor_power_w(t, airspeed) + body.avionics_power_w, 0.0)


def closest_approach(p0: np.ndarray, p1: np.ndarray, mask: np.ndarray):
    """Smallest pairwise distance during a step, assuming straight-line motion.

    ``p0``/``p1`` are positions at the start/end of the step (n, 3). Returns an
    (n, n) matrix (inf on the diagonal and for masked-out drones), so fast
    crossings between frames still register.
    """
    d0 = p0[:, None, :] - p0[None, :, :]
    dd = (p1 - p0)[:, None, :] - (p1 - p0)[None, :, :]
    denom = np.sum(dd * dd, axis=2)
    s = np.where(denom > 1e-12, -np.sum(d0 * dd, axis=2) / np.maximum(denom, 1e-12), 0.0)
    s = np.clip(s, 0.0, 1.0)
    closest = d0 + s[..., None] * dd
    dist = np.linalg.norm(closest, axis=2)
    pair = mask[:, None] & mask[None, :]
    np.fill_diagonal(pair, False)
    return np.where(pair, dist, np.inf)
