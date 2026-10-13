"""Collision-avoidance safety filter based on control barrier functions.

For a pair of double-integrator robots with bounded braking, Wang, Ames and
Egerstedt (IEEE T-RO 2017, "Safety barrier certificates for collisions-free
multirobot systems") use

    h_ij = sqrt(2 (a_i + a_j) (|dp| - Ds)) + dp . dv / |dp|

which stays non-negative exactly when the two can still brake apart. The
constraint dh/dt >= -gamma h is linear in the accelerations, and each drone
takes half of it, so the filter runs on every drone with no coordination.
Obstacles and the ground get the single-robot version. Each drone then picks
the acceleration closest to its nominal command that satisfies all its
constraints (a small quadratic programme), solved here with Dykstra's
alternating projections, vectorised across the swarm.

Stale neighbour data is handled conservatively: the safety distance grows
with the age of the last message, and neighbours not heard from for over
3 s are dropped (the drone cannot avoid what it cannot see).
"""

from __future__ import annotations

import numpy as np

MAX_NEIGHBOURS = 6   # nearest neighbours each drone constrains against
OBSTACLE_ROWS = 3    # nearest obstacle surfaces each drone constrains against
OBSTACLE_RANGE_M = 15.0  # how far ahead the filter sees obstacles (a forward depth sensor's range)


def _rows_dot(a, b):
    return np.einsum("...k,...k->...", a, b)


def build_constraints(p, v, nbr_pos, nbr_vel, nbr_age, active, *, d_safe, a_brake, gamma, max_neighbours=MAX_NEIGHBOURS,
                      stale_growth=0.5, max_age=3.0, world=None, obstacle_clearance=1.0, floor_z=1.0,
                      obstacle_range=OBSTACLE_RANGE_M):
    """Linear constraints G a <= b for every drone: shapes (n, K, 3), (n, K)."""
    n = len(p)
    rows_g, rows_b, rows_m = [], [], []

    # -- neighbours -----------------------------------------------------------
    dp = p[:, None, :] - nbr_pos                      # (n, n, 3)
    dv = v[:, None, :] - nbr_vel
    dist = np.linalg.norm(dp, axis=2)
    known = np.isfinite(nbr_age) & (nbr_age <= max_age) & active[None, :] & active[:, None]
    np.fill_diagonal(known, False)
    ranked = np.where(known, dist, np.inf)
    k = min(max_neighbours, max(n - 1, 0))
    if k > 0:
        idx = np.argpartition(ranked, k - 1, axis=1)[:, :k] if n - 1 > k else np.argsort(ranked, axis=1)[:, :k]
        r = np.arange(n)[:, None]
        sel_dp, sel_dv = dp[r, idx], dv[r, idx]
        sel_d, sel_ok = dist[r, idx], np.isfinite(ranked[r, idx])
        age = np.where(np.isfinite(nbr_age[r, idx]), nbr_age[r, idx], 0.0)
        ds = d_safe + stale_growth * np.minimum(age, max_age)
        unit = sel_dp / np.maximum(sel_d, 1e-9)[..., None]
        closing = _rows_dot(sel_dp, sel_dv) / np.maximum(sel_d, 1e-9)
        gap = sel_d - ds
        root = np.sqrt(4.0 * a_brake * np.maximum(gap, 1e-6))
        h = root + closing
        vv = _rows_dot(sel_dv, sel_dv)
        rhs = gamma * h + 2 * a_brake * closing / root + vv / np.maximum(sel_d, 1e-9) - closing ** 2 / np.maximum(sel_d, 1e-9)
        b = np.where(gap > 1e-3, 0.5 * rhs, -a_brake)
        rows_g.append(-unit)
        rows_b.append(b)
        rows_m.append(sel_ok)

    # -- obstacles --------------------------------------------------------------
    if world is not None and world.obstacle_count:
        od, away, mask = world.nearby(p, obstacle_range, k=OBSTACLE_ROWS)
        gap = od - obstacle_clearance
        root = np.sqrt(2.0 * a_brake * np.maximum(gap, 1e-6))
        closing = _rows_dot(away, v[:, None, :])
        h = root + closing
        rhs = gamma * h + a_brake * closing / root
        rows_g.append(-away)
        rows_b.append(np.where(gap > 1e-3, rhs, -a_brake))
        rows_m.append(mask & active[:, None])

    # -- ground -------------------------------------------------------------------
    up = np.tile(np.array([0.0, 0.0, 1.0]), (n, 1, 1))
    gap = p[:, 2:3] - floor_z
    root = np.sqrt(2.0 * a_brake * np.maximum(gap, 1e-6))
    closing = v[:, 2:3]
    h = root + closing
    rhs = gamma * h + a_brake * closing / root
    rows_g.append(-up)
    rows_b.append(np.where(gap > 1e-3, rhs, -a_brake))
    rows_m.append(np.tile(active[:, None], (1, 1)))

    g = np.concatenate(rows_g, axis=1)
    b = np.concatenate(rows_b, axis=1)
    m = np.concatenate(rows_m, axis=1)
    return g, b, m


def project(a_nom, g, b, mask, a_max, iterations=12):
    """Closest acceleration to ``a_nom`` with G a <= b and |a| <= a_max (Dykstra).

    Also returns which constraints the nominal command violated (the filter's interventions).
    """
    n, k, _ = g.shape
    active_rows = mask & (_rows_dot(g, a_nom[:, None, :]) - b > 1e-6)
    x = a_nom.copy()
    incr = np.zeros((n, k + 1, 3))
    gg = np.maximum(_rows_dot(g, g), 1e-12)
    for _ in range(iterations):
        for j in range(k):
            z = x + incr[:, j]
            viol = _rows_dot(g[:, j], z) - b[:, j]
            step = np.where(mask[:, j] & (viol > 0), viol / gg[:, j], 0.0)
            new = z - step[:, None] * g[:, j]
            incr[:, j] = z - new
            x = new
        z = x + incr[:, k]
        nz = np.linalg.norm(z, axis=1, keepdims=True)
        new = z * np.minimum(1.0, a_max / np.maximum(nz, 1e-12))
        incr[:, k] = z - new
        x = new
    return x, active_rows
