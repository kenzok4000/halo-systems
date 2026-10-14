"""Smoke: what a fire's plume looks like from a drone, and what a particulate sensor reads inside it.

Two ways a drone notices smoke, both on the scouting airframe in ``hardware/``:

* **Seeing the plume** with its forward RGB camera. The plume is a bent-over column: smoke rises at
  ``rise_mps`` while the wind carries it, so at height z it has drifted ``U z / rise`` downwind, up to a
  top that grows with the burning area. A plume point counts as visible when it is above whatever hides
  the fire (tree crowns, a rock shelf), inside the camera's horizontal field of view, within the haze
  visibility, and not behind terrain. Detection is a hazard: a plume of burning area A at range r is
  noticed at a rate ln 2 (R50 / r)^3 per second, so a reference plume is seen within a second half the
  time at R50 = ``r50_ref_m`` * sqrt(A / ``ref_area_m2``). A sighting reports the bearing to the lowest
  visible point of the plume, with Gaussian error.
* **Flying through it** with a particulate (PM2.5) sensor. Concentration follows the Gaussian plume
  equation with ground reflection and Pasquill-Gifford class D dispersion (Briggs' rural fits); the
  sensor triggers above ``pm_threshold_ugm3``, and the swarm then knows the source is upwind.

The ground station turns bearings into locations by triangulation: pairs of sightings taken from
different places at a useful angle cross at candidate points, and a tight cluster of crossings is a
located plume. It does not know which plume a sighting belongs to, so crossings of two different
plumes can make a false location; the metrics count those.

The numbers (R50, rise speed, emission factor, threshold) are stated assumptions to be calibrated
against real footage and sensor logs; docs/WILDFIRE.md lists them and their sources.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SmokeConfig:
    camera_hfov_deg: float = 90.0        # forward RGB camera, horizontal field of view
    r50_ref_m: float = 300.0             # range at which a reference plume is noticed within 1 s half the time
    ref_area_m2: float = 10.0            # burning area of the reference plume (a smouldering spot)
    visibility_m: float = 2500.0         # haze limit
    bearing_sigma_deg: float = 2.0
    rise_mps: float = 1.5                # buoyant rise of a small plume
    top_ref_m: float = 12.0              # plume top above ground for the reference area
    top_max_m: float = 150.0
    pm_g_per_s_per_m2: float = 0.01      # PM2.5 emission per square metre burning
    pm_threshold_ugm3: float = 35.0      # sensor trigger, above background
    sighting_gap_s: float = 2.0          # a drone reports the same plume at most this often


def plume_top(area_m2, cfg: SmokeConfig):
    """Height of the plume top above the ground (m) for a burning area."""
    a = np.maximum(np.asarray(area_m2, dtype=float), 0.1)
    return np.clip(cfg.top_ref_m * (a / cfg.ref_area_m2) ** 0.35, 6.0, cfg.top_max_m)


def plume_points(base: np.ndarray, area_m2: np.ndarray, wind_xy: np.ndarray, cfg: SmokeConfig,
                 fractions=(0.15, 0.35, 0.55, 0.75, 1.0)) -> tuple[np.ndarray, np.ndarray]:
    """Points along each plume's centreline: shape (fires, k, 3), and their heights above the base (fires, k)."""
    base = np.asarray(base, dtype=float).reshape(-1, 3)
    top = plume_top(area_m2, cfg).reshape(-1)
    z = top[:, None] * np.asarray(fractions)[None, :]
    drift = z / cfg.rise_mps                                   # seconds of rise to reach z
    pts = np.repeat(base[:, None, :], len(fractions), axis=1).copy()
    pts[..., 0] += wind_xy[0] * drift
    pts[..., 1] += wind_xy[1] * drift
    pts[..., 2] += z
    return pts, z


def pm_concentration(points: np.ndarray, base: np.ndarray, area_m2: np.ndarray, wind_xy: np.ndarray,
                     cfg: SmokeConfig) -> np.ndarray:
    """PM2.5 above background (ug/m^3) at each point from every plume, summed: (m,) for (m, 3) points."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    base = np.asarray(base, dtype=float).reshape(-1, 3)
    u = float(np.hypot(*wind_xy))
    if u < 0.5 or not len(base):
        return np.zeros(len(points))
    ux, uy = wind_xy[0] / u, wind_xy[1] / u
    dx = points[:, None, 0] - base[None, :, 0]
    dy = points[:, None, 1] - base[None, :, 1]
    down = dx * ux + dy * uy
    cross = -dx * uy + dy * ux
    xd = np.maximum(down, 1.0)
    sy = 0.08 * xd / np.sqrt(1.0 + 0.0001 * xd)                # Briggs rural, class D
    sz = 0.06 * xd / np.sqrt(1.0 + 0.0015 * xd)
    top = plume_top(area_m2, cfg)[None, :]
    h = np.minimum(top, cfg.rise_mps * xd / u)                  # centreline height along the bent-over plume
    zrel = points[:, None, 2] - base[None, :, 2]
    q = cfg.pm_g_per_s_per_m2 * np.asarray(area_m2, dtype=float)[None, :] * 1e6   # ug/s
    c = q / (2 * math.pi * u * sy * sz) * np.exp(-cross ** 2 / (2 * sy ** 2)) * (
        np.exp(-(zrel - h) ** 2 / (2 * sz ** 2)) + np.exp(-(zrel + h) ** 2 / (2 * sz ** 2)))
    c = np.where(down > 1.0, c, 0.0)
    return c.sum(axis=1)


def triangulate(sightings: np.ndarray, min_angle_deg: float = 15.0, min_baseline_m: float = 20.0,
                max_range_m: float = 2500.0) -> np.ndarray:
    """Crossing points of pairs of bearing lines: (k, 2).

    ``sightings`` rows are (x, y, bearing_rad). A pair counts when the bearings differ by at least
    ``min_angle_deg``, the observers stand at least ``min_baseline_m`` apart, and the crossing lies in
    front of both within ``max_range_m``.
    """
    s = np.asarray(sightings, dtype=float).reshape(-1, 3)
    m = len(s)
    if m < 2:
        return np.zeros((0, 2))
    i, j = np.triu_indices(m, 1)
    p1, p2 = s[i, :2], s[j, :2]
    d1 = np.stack([np.cos(s[i, 2]), np.sin(s[i, 2])], axis=1)
    d2 = np.stack([np.cos(s[j, 2]), np.sin(s[j, 2])], axis=1)
    cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    ok = np.abs(cross) >= math.sin(math.radians(min_angle_deg))
    ok &= np.linalg.norm(p2 - p1, axis=1) >= min_baseline_m
    w = p2 - p1
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (w[:, 0] * d2[:, 1] - w[:, 1] * d2[:, 0]) / cross
        t2 = (w[:, 0] * d1[:, 1] - w[:, 1] * d1[:, 0]) / cross
    ok &= (t1 > 0) & (t2 > 0) & (t1 < max_range_m) & (t2 < max_range_m)
    return (p1 + t1[:, None] * d1)[ok]


def cluster(points: np.ndarray, radius_m: float = 40.0, min_points: int = 4,
            max_clusters: int = 12) -> list[tuple[np.ndarray, float, int]]:
    """Clusters of crossing points: (median position, median distance to it, size), densest first.

    Points are binned on a grid of ``radius_m`` cells; the densest 3 x 3 block of cells seeds a cluster,
    whose members are the points within ``radius_m`` of the block's median. Members are removed and the
    search repeats, so the cost grows with the number of points, not its square.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    out = []
    left = np.ones(len(pts), dtype=bool)
    while left.sum() >= min_points and len(out) < max_clusters:
        idx = np.nonzero(left)[0]
        cells = np.floor(pts[idx] / radius_m).astype(np.int64)
        keys, inverse, counts = np.unique(cells, axis=0, return_inverse=True, return_counts=True)
        inverse = inverse.reshape(-1)
        lookup = {tuple(k): c for k, c in zip(keys.tolist(), counts.tolist())}
        block = np.array([sum(lookup.get((kx + dx, ky + dy), 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                          for kx, ky in keys.tolist()])
        best = int(np.argmax(block))
        if block[best] < min_points:
            break
        bx, by = keys[best]
        near = (np.abs(cells[:, 0] - bx) <= 1) & (np.abs(cells[:, 1] - by) <= 1)
        centre = np.median(pts[idx[near]], axis=0)
        d = np.linalg.norm(pts[idx] - centre, axis=1)
        members = idx[d <= radius_m]
        if len(members) < min_points:
            left[idx[near]] = False          # a loose block: drop it and look elsewhere
            continue
        centre = np.median(pts[members], axis=0)
        spread = float(np.median(np.linalg.norm(pts[members] - centre, axis=1)))
        out.append((centre, spread, len(members)))
        left[members] = False
    return out


def bearing_fix(sightings: np.ndarray, guess: np.ndarray, bearing_sigma_deg: float = 2.0, gate_m: float = 10.0,
                iterations: int = 3) -> dict | None:
    """Least-squares position from every bearing line that passes near ``guess``.

    ``sightings`` rows are (x, y, bearing_rad, drone). A line counts when it passes within ``gate_m`` plus
    three bearing errors of the current estimate, in front of its observer. The estimate minimises the sum of
    squared distances to the lines, each weighted by 1 / range^2 (a bearing error grows with range), and is
    redone with the lines that count at the new estimate. Returns the position, its one-sigma error (m), the
    error with any one drone's lines left out, the lines and distinct drones used, the widest angle between
    them, and the lines' RMS miss distance in bearing
    errors (about 1 for a real plume, larger for a ghost crossing of two plumes' bearings); None when fewer than
    two lines count.
    """
    s = np.asarray(sightings, dtype=float).reshape(-1, 4)
    if len(s) < 2:
        return None
    est = np.asarray(guess, dtype=float).copy()
    sig = math.radians(bearing_sigma_deg)
    use = None
    for _ in range(iterations):
        dx, dy = est[0] - s[:, 0], est[1] - s[:, 1]
        r = np.maximum(np.hypot(dx, dy), 1.0)
        nx, ny = -np.sin(s[:, 2]), np.cos(s[:, 2])
        perp = np.abs(nx * dx + ny * dy)
        ahead = (np.cos(s[:, 2]) * dx + np.sin(s[:, 2]) * dy) > 0
        use = ahead & (perp <= gate_m + 3.0 * sig * r)
        if use.sum() < 2:
            return None
        w = 1.0 / r[use] ** 2
        a11 = float(np.sum(w * nx[use] ** 2))
        a12 = float(np.sum(w * nx[use] * ny[use]))
        a22 = float(np.sum(w * ny[use] ** 2))
        c = nx[use] * s[use, 0] + ny[use] * s[use, 1]
        b1, b2 = float(np.sum(w * nx[use] * c)), float(np.sum(w * ny[use] * c))
        det = a11 * a22 - a12 * a12
        if det <= 1e-12 * max(a11 * a22, 1e-30):
            return None
        est = np.array([(a22 * b1 - a12 * b2) / det, (a11 * b2 - a12 * b1) / det])
    dx, dy = est[0] - s[use, 0], est[1] - s[use, 1]
    r = np.maximum(np.hypot(dx, dy), 1.0)

    def sigma_m(keep):
        # One-sigma position error of the weighted fit: sigma^2 (A^T W A)^-1 with W = 1 / (sigma r)^2.
        a11 = float(np.sum(nx[use][keep] ** 2 / r[keep] ** 2))
        a12 = float(np.sum(nx[use][keep] * ny[use][keep] / r[keep] ** 2))
        a22 = float(np.sum(ny[use][keep] ** 2 / r[keep] ** 2))
        det = a11 * a22 - a12 * a12
        if det <= 1e-12 * max(a11 * a22, 1e-30):
            return math.inf
        return sig * math.sqrt(max((a11 + a22) / det, 0.0))

    err = sigma_m(np.ones(len(r), dtype=bool))
    # The same error with each drone's lines left out: a fix that rests on one drone's view is not a fix.
    drones = s[use, 3]
    err_without = max((sigma_m(drones != d) for d in np.unique(drones)), default=math.inf)
    miss = (nx[use] * dx + ny[use] * dy) / (sig * r)
    angles = np.sort(np.mod(np.arctan2(dy, dx), 2 * math.pi))
    gaps = np.diff(np.concatenate([angles, angles[:1] + 2 * math.pi]))
    spread = math.degrees(2 * math.pi - float(gaps.max())) if len(angles) > 1 else 0.0
    return {"x": float(est[0]), "y": float(est[1]), "err_m": float(err), "err_without_one_m": float(err_without),
            "lines": int(use.sum()),
            "drones": int(len(np.unique(s[use, 3]))), "angle_deg": spread, "rms_sigma": float(np.sqrt(np.mean(miss ** 2))),
            "used": np.nonzero(use)[0]}


def fix_is_sound(fix: dict | None, min_drones: int = 3, min_lines: int = 6, min_angle_deg: float = 30.0,
                 max_err_m: float = 25.0, max_err_without_one_m: float = 40.0, max_rms_sigma: float = 1.5) -> bool:
    """Whether a bearing fix is a plume and not a ghost crossing: several drones, seen from well-separated
    angles, agreeing to within their bearing error, and still placed when any one drone's view is left out."""
    return (fix is not None and fix["drones"] >= min_drones and fix["lines"] >= min_lines
            and fix["angle_deg"] >= min_angle_deg and fix["err_m"] <= max_err_m
            and fix["err_without_one_m"] <= max_err_without_one_m and fix["rms_sigma"] <= max_rms_sigma)

