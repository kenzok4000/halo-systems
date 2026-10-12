"""Static world: ground, vertical cylinders (trees, poles) and boxes (buildings).

Every query is vectorised over drones. ``clearance`` returns, for each point,
the distance to the nearest obstacle surface and the unit direction pointing
away from it, which the collision-avoidance filter and the metrics use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Cylinder:
    x: float
    y: float
    radius: float
    height: float

    def to_dict(self) -> dict:
        return {"kind": "cylinder", "x": self.x, "y": self.y, "radius": self.radius, "height": self.height}


@dataclass
class Box:
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    def to_dict(self) -> dict:
        return {"kind": "box", "lo": list(self.lo), "hi": list(self.hi)}


@dataclass
class World:
    cylinders: list[Cylinder] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)
    geofence_lo: tuple[float, float, float] = (-500.0, -500.0, 0.0)
    geofence_hi: tuple[float, float, float] = (500.0, 500.0, 120.0)

    def __post_init__(self) -> None:
        self._pack()

    def _pack(self) -> None:
        if self.cylinders:
            self._cyl = np.array([[c.x, c.y, c.radius, c.height] for c in self.cylinders], dtype=float)
        else:
            self._cyl = np.zeros((0, 4))
        if self.boxes:
            self._box_lo = np.array([b.lo for b in self.boxes], dtype=float)
            self._box_hi = np.array([b.hi for b in self.boxes], dtype=float)
        else:
            self._box_lo = np.zeros((0, 3))
            self._box_hi = np.zeros((0, 3))

    def add_cylinder(self, cyl: Cylinder) -> None:
        self.cylinders.append(cyl)
        self._pack()

    @property
    def obstacle_count(self) -> int:
        return len(self.cylinders) + len(self.boxes)

    def to_dict(self) -> dict:
        return {
            "obstacles": [c.to_dict() for c in self.cylinders] + [b.to_dict() for b in self.boxes],
            "geofence": {"lo": list(self.geofence_lo), "hi": list(self.geofence_hi)},
        }

    def clearance(self, points: np.ndarray, max_objects: int = 0):
        """Distance from each point to the nearest obstacle surface.

        Returns ``(distance, away)`` with shapes (n,) and (n, 3). Points with no
        obstacles nearby get ``inf`` distance and a zero direction. Distances are
        negative inside an obstacle.
        """
        n = len(points)
        best = np.full(n, np.inf)
        away = np.zeros((n, 3))
        if len(self._cyl):
            d, u = self._cylinder_distance(points)
            idx = np.argmin(d, axis=1)
            dmin = d[np.arange(n), idx]
            better = dmin < best
            best[better] = dmin[better]
            away[better] = u[np.arange(n), idx][better]
        if len(self._box_lo):
            d, u = self._box_distance(points)
            idx = np.argmin(d, axis=1)
            dmin = d[np.arange(n), idx]
            better = dmin < best
            best[better] = dmin[better]
            away[better] = u[np.arange(n), idx][better]
        return best, away

    def nearby(self, points: np.ndarray, radius: float, k: int = 4):
        """Up to ``k`` nearest obstacle surfaces within ``radius`` of each point.

        Returns ``(distance, away, mask)`` with shapes (n, k), (n, k, 3), (n, k).
        """
        n = len(points)
        parts_d, parts_u = [], []
        if len(self._cyl):
            d, u = self._cylinder_distance(points)
            parts_d.append(d)
            parts_u.append(u)
        if len(self._box_lo):
            d, u = self._box_distance(points)
            parts_d.append(d)
            parts_u.append(u)
        if not parts_d:
            return np.full((n, k), np.inf), np.zeros((n, k, 3)), np.zeros((n, k), dtype=bool)
        d = np.concatenate(parts_d, axis=1)
        u = np.concatenate(parts_u, axis=1)
        kk = min(k, d.shape[1])
        idx = np.argpartition(d, kk - 1, axis=1)[:, :kk] if d.shape[1] > kk else np.tile(np.arange(d.shape[1]), (n, 1))
        rows = np.arange(n)[:, None]
        dsel = d[rows, idx]
        usel = u[rows, idx]
        out_d = np.full((n, k), np.inf)
        out_u = np.zeros((n, k, 3))
        out_d[:, :kk] = dsel
        out_u[:, :kk] = usel
        mask = out_d < radius
        return out_d, out_u, mask

    def _cylinder_distance(self, points: np.ndarray):
        c = self._cyl
        dx = points[:, None, 0] - c[None, :, 0]
        dy = points[:, None, 1] - c[None, :, 1]
        horiz = np.hypot(dx, dy)
        radial = horiz - c[None, :, 2]
        above = points[:, None, 2] - c[None, :, 3]
        # Below the top: distance to the side wall. Above: distance to the top rim/disk.
        side = above <= 0
        dist = np.where(side, radial, np.hypot(np.maximum(radial, 0.0), above))
        safe_h = np.maximum(horiz, 1e-9)
        ux = dx / safe_h
        uy = dy / safe_h
        # Direction away from the obstacle.
        top_r = np.maximum(radial, 0.0)
        top_norm = np.maximum(np.hypot(top_r, above), 1e-9)
        u = np.zeros(dist.shape + (3,))
        u[..., 0] = np.where(side, ux, ux * top_r / top_norm)
        u[..., 1] = np.where(side, uy, uy * top_r / top_norm)
        u[..., 2] = np.where(side, 0.0, above / top_norm)
        return dist, u

    def _box_distance(self, points: np.ndarray):
        lo = self._box_lo[None]
        hi = self._box_hi[None]
        p = points[:, None, :]
        centre = 0.5 * (lo + hi)
        half = 0.5 * (hi - lo)
        q = np.abs(p - centre) - half
        outside = np.linalg.norm(np.maximum(q, 0.0), axis=2)
        inside = np.minimum(np.max(q, axis=2), 0.0)
        dist = outside + inside
        closest = np.clip(p, lo, hi)
        diff = p - closest
        norm = np.linalg.norm(diff, axis=2, keepdims=True)
        u = np.where(norm > 1e-9, diff / np.maximum(norm, 1e-9), 0.0)
        # Inside the box: push out through the nearest face.
        inside_mask = outside <= 1e-12
        if np.any(inside_mask):
            axis = np.argmax(q, axis=2)
            sign = np.sign(p - centre)
            face = np.zeros_like(u)
            np.put_along_axis(face, axis[..., None], np.take_along_axis(sign, axis[..., None], axis=2), axis=2)
            u = np.where(inside_mask[..., None], face, u)
        return dist, u

    def free_width_ahead(self, centre: np.ndarray, heading: np.ndarray, lookahead: float, altitude: float) -> float:
        """Narrowest free lateral gap along a corridor ahead of ``centre``.

        Samples stations every 5 m up to ``lookahead`` and, at each, finds the
        widest obstacle-free interval around the path line at flight altitude.
        Returns the narrowest of those widths (capped at 200 m).
        """
        cap = 200.0
        if not len(self._cyl) and not len(self._box_lo):
            return cap
        h = np.asarray(heading, dtype=float)
        h = h / max(np.linalg.norm(h[:2]), 1e-9)
        lat = np.array([-h[1], h[0], 0.0])
        narrowest = cap
        offsets = np.linspace(-cap / 2, cap / 2, 401)
        for s in np.arange(0.0, lookahead + 1e-9, 5.0):
            station = np.array([centre[0] + h[0] * s, centre[1] + h[1] * s, altitude])
            pts = station[None, :] + offsets[:, None] * lat[None, :]
            d, _ = self.clearance(pts)
            free = d > 0.0
            # Widest run of free samples containing (or nearest to) the path line.
            mid = len(offsets) // 2
            if not free[mid]:
                width = 0.0
                # Path line blocked: take the widest free run anywhere.
                runs = _runs(free)
                width = max((b - a) for a, b in runs) * (offsets[1] - offsets[0]) if runs else 0.0
            else:
                a = mid
                while a > 0 and free[a - 1]:
                    a -= 1
                b = mid
                while b < len(free) - 1 and free[b + 1]:
                    b += 1
                width = (b - a) * (offsets[1] - offsets[0])
            narrowest = min(narrowest, width)
        return float(narrowest)

    def density_ahead(self, centre: np.ndarray, heading: np.ndarray, lookahead: float, half_width: float) -> float:
        """Obstacles per 1,000 m^2 in a rectangle ahead of ``centre``."""
        if not len(self._cyl) and not len(self._box_lo):
            return 0.0
        h = np.asarray(heading, dtype=float)[:2]
        h = h / max(np.linalg.norm(h), 1e-9)
        lat = np.array([-h[1], h[0]])
        pts = []
        if len(self._cyl):
            pts.append(self._cyl[:, :2])
        if len(self._box_lo):
            pts.append(0.5 * (self._box_lo[:, :2] + self._box_hi[:, :2]))
        xy = np.concatenate(pts) - np.asarray(centre)[None, :2]
        along = xy @ h
        across = xy @ lat
        inside = (along >= 0) & (along <= lookahead) & (np.abs(across) <= half_width)
        area = lookahead * 2 * half_width
        return float(1000.0 * inside.sum() / max(area, 1e-9))


def _runs(mask: np.ndarray):
    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs
