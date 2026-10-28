"""Formation shapes and the moving formation frame.

Offsets are in the formation frame (x forward along the path, y to the left,
z up) and centred on their centroid. ``spacing`` is the distance between
adjacent drones.
"""

from __future__ import annotations

import math

import numpy as np

SHAPES = ("grid", "line", "column", "v", "ring")


def formation_offsets(shape: str, n: int, spacing: float) -> np.ndarray:
    if n <= 0:
        return np.zeros((0, 3))
    s = float(spacing)
    if shape == "grid":
        cols = int(math.ceil(math.sqrt(n)))
        idx = np.arange(n)
        row, col = idx // cols, idx % cols
        rows = int(math.ceil(n / cols))
        xy = np.stack([-(row - (rows - 1) / 2) * s, (col - (cols - 1) / 2) * s], axis=1)
        # Centre an incomplete last row.
        last = row == rows - 1
        missing = rows * cols - n
        xy[last, 1] += missing * s / 2
    elif shape == "line":
        xy = np.stack([np.zeros(n), (np.arange(n) - (n - 1) / 2) * s], axis=1)
    elif shape == "column":
        xy = np.stack([-(np.arange(n) - (n - 1) / 2) * s, np.zeros(n)], axis=1)
    elif shape == "v":
        # One drone at the apex, the rest alternately left and right on two arms swept back 45 degrees.
        pts = [(0.0, 0.0)]
        k = 1
        while len(pts) < n:
            for side in (1, -1):
                if len(pts) < n:
                    pts.append((-k * s * math.cos(math.radians(45)), side * k * s * math.sin(math.radians(45))))
            k += 1
        xy = np.array(pts)
    elif shape == "ring":
        # Neighbours on the ring are one chord apart: chord = 2 r sin(pi / n) = spacing.
        radius = s / (2 * math.sin(math.pi / n)) if n > 2 else (s / 2 if n == 2 else 0.0)
        a = 2 * math.pi * np.arange(n) / n
        xy = np.stack([radius * np.cos(a), radius * np.sin(a)], axis=1)
    else:
        raise ValueError(f"unknown formation shape {shape!r}; choose from {SHAPES}")
    xy = xy - xy.mean(axis=0, keepdims=True)
    return np.concatenate([xy, np.zeros((n, 1))], axis=1)


def extent(offsets: np.ndarray) -> tuple[float, float]:
    """(width across the path, length along it) of a set of offsets."""
    if len(offsets) == 0:
        return 0.0, 0.0
    return float(np.ptp(offsets[:, 1])), float(np.ptp(offsets[:, 0]))


def rotate(offsets: np.ndarray, heading: float) -> np.ndarray:
    # Anticlockwise by ``heading`` radians about z: from the formation frame into the world frame.
    c, s = math.cos(heading), math.sin(heading)
    out = offsets.copy()
    out[:, 0] = c * offsets[:, 0] - s * offsets[:, 1]
    out[:, 1] = s * offsets[:, 0] + c * offsets[:, 1]
    return out


def round_corners(points: np.ndarray, radius: float, loop: bool, samples: int = 16) -> np.ndarray:
    """Replace each interior corner of a polyline with a smooth curve.

    Each corner becomes a quadratic Bezier between points ``radius *
    tan(turn / 2)`` before and after it (at most 40% of either segment), so
    the virtual leader never has to change direction instantly.
    """
    pts = [tuple(p) for p in points]
    if len(pts) < 3 or radius <= 0:
        return np.asarray(points, dtype=float)
    core = pts[:-1] if loop else pts
    m = len(core)
    out = []
    for i in range(m):
        b = np.asarray(core[i], dtype=float)
        interior = loop or 0 < i < m - 1
        if not interior:
            out.append(b)
            continue
        a = np.asarray(core[i - 1], dtype=float)
        c = np.asarray(core[(i + 1) % m], dtype=float)
        la, lc = np.linalg.norm(b - a), np.linalg.norm(c - b)
        if la < 1e-9 or lc < 1e-9:
            out.append(b)
            continue
        d1, d2 = (b - a) / la, (c - b) / lc
        turn = math.acos(float(np.clip(d1 @ d2, -1.0, 1.0)))
        if turn < 1e-3:
            out.append(b)
            continue
        cut = min(radius * math.tan(min(turn, 3.0) / 2), 0.4 * la, 0.4 * lc)
        p0, p2 = b - d1 * cut, b + d2 * cut
        for u in np.linspace(0.0, 1.0, samples):
            out.append((1 - u) ** 2 * p0 + 2 * (1 - u) * u * b + u ** 2 * p2)
    if loop:
        out.append(out[0])
    return np.asarray(out, dtype=float)


class Path:
    """A polyline (corners rounded) the formation centre follows at a commanded speed."""

    def __init__(self, waypoints, loop: bool = False, corner_radius: float = 15.0):
        w = np.asarray(waypoints, dtype=float)
        if w.ndim != 2 or w.shape[1] not in (2, 3) or len(w) < 1:
            raise ValueError("waypoints must be a list of [x, y] or [x, y, z]")
        self.waypoints = w[:, :2].copy()
        self.points = w[:, :2]
        self.loop = loop
        if loop and len(w) > 1:
            self.points = np.vstack([self.points, self.points[:1]])
        self.points = round_corners(self.points, corner_radius, loop and len(w) > 2)
        seg = np.diff(self.points, axis=0)
        self.seg_len = np.linalg.norm(seg, axis=1)
        self.cum = np.concatenate([[0.0], np.cumsum(self.seg_len)])
        self.length = float(self.cum[-1])

    def at(self, s: float):
        """Position (2,) and unit tangent (2,) at arc length ``s``."""
        if self.length <= 0:
            return self.points[0].copy(), np.array([1.0, 0.0])
        if self.loop:
            s = s % self.length
        s = min(max(s, 0.0), self.length)
        i = int(np.searchsorted(self.cum, s, side="right") - 1)
        i = min(max(i, 0), len(self.seg_len) - 1)
        while i < len(self.seg_len) - 1 and self.seg_len[i] <= 1e-9:
            i += 1
        f = (s - self.cum[i]) / max(self.seg_len[i], 1e-9)
        a, b = self.points[i], self.points[i + 1]
        t = (b - a) / max(self.seg_len[i], 1e-9)
        return a + f * (b - a), t

    def heading_at(self, s: float, smooth: float = 8.0) -> float:
        """Path heading, averaged over +-``smooth`` metres so corners turn gently."""
        _, t0 = self.at(s - smooth)
        _, t1 = self.at(s + smooth)
        t = t0 + t1
        if np.linalg.norm(t) < 1e-9:
            _, t = self.at(s)
        # Radians anticlockwise from +x (east), the convention for every heading in the plan.
        return math.atan2(t[1], t[0])


def lawnmower(x0: float, y0: float, width: float, height: float, lane: float, heading_deg: float = 0.0):
    """Back-and-forth survey waypoints covering a rectangle (lanes along x)."""
    lanes = max(1, int(math.ceil(height / max(lane, 1e-6))))
    pts = []
    for k in range(lanes):
        y = y0 + (k + 0.5) * height / lanes
        xs = (x0, x0 + width) if k % 2 == 0 else (x0 + width, x0)
        pts.append((xs[0], y))
        pts.append((xs[1], y))
    if heading_deg:
        c, s = math.cos(math.radians(heading_deg)), math.sin(math.radians(heading_deg))
        pts = [(x0 + c * (x - x0) - s * (y - y0), y0 + s * (x - x0) + c * (y - y0)) for x, y in pts]
    return pts
