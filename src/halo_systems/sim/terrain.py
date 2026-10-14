"""Terrain: a height grid, land cover and the surface the drones follow.

Heights live on a regular grid of nodes (``x0 + ix * cell_m``, ``y0 + iy * cell_m``),
stored as ``heights[iy, ix]`` and interpolated bilinearly, the way a digital
terrain model (DTM) from a national mapping agency is used. Two surfaces matter:

* the ground (the DTM), which the cameras look at and the drones must not hit;
* the surface model (DSM), ground plus tree crowns, which a terrain-following
  survey drone keeps its height above. Real survey autopilots follow a DSM
  from lidar or photogrammetry, with a look-ahead so a rising slope is climbed
  before it is reached. Here the look-ahead is a maximum filter of the DSM over
  ``follow_radius_m``, computed once.

Line of sight between two points is checked by sampling the segment against
the ground (smoke behind a ridge is not seen). Everything is vectorised over
points, so a swarm costs one call.

Land cover classes follow the order in ``COVER_NAMES``; the fire model maps each
class to a standard fuel model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

ROCK, GRASS, SCRUB, FOREST, BARE = 0, 1, 2, 3, 4
COVER_NAMES = ("rock", "grass", "scrub", "forest", "bare")


def _max_filter(a: np.ndarray, radius_cells: int) -> np.ndarray:
    """Maximum over a square window of half-width ``radius_cells`` (separable, edges padded)."""
    if radius_cells <= 0:
        return a.copy()
    out = a
    for axis in (0, 1):
        padded = np.pad(out, [(radius_cells, radius_cells) if ax == axis else (0, 0) for ax in (0, 1)], mode="edge")
        stack = [np.take(padded, np.arange(k, k + out.shape[axis]), axis=axis) for k in range(2 * radius_cells + 1)]
        out = np.max(stack, axis=0)
    return out


def smooth_noise(rng: np.random.Generator, shape: tuple[int, int], scale_cells: float, octaves: int = 3) -> np.ndarray:
    """Smooth random field in [-1, 1]: a few octaves of bilinearly upsampled white noise."""
    ny, nx = shape
    total = np.zeros(shape)
    amp, norm = 1.0, 0.0
    scale = max(scale_cells, 1.0)
    for _ in range(octaves):
        gy = max(2, int(math.ceil(ny / scale)) + 2)
        gx = max(2, int(math.ceil(nx / scale)) + 2)
        coarse = rng.uniform(-1.0, 1.0, size=(gy, gx))
        yy = np.arange(ny) / scale
        xx = np.arange(nx) / scale
        iy = np.minimum(yy.astype(int), gy - 2)
        ix = np.minimum(xx.astype(int), gx - 2)
        fy = (yy - iy)[:, None]
        fx = (xx - ix)[None, :]
        # Smoothstep weights hide the grid lines of plain bilinear noise.
        fy = fy * fy * (3 - 2 * fy)
        fx = fx * fx * (3 - 2 * fx)
        a = coarse[iy][:, ix]
        b = coarse[iy][:, ix + 1]
        c = coarse[iy + 1][:, ix]
        d = coarse[iy + 1][:, ix + 1]
        total += amp * (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy)
        norm += amp
        amp *= 0.5
        scale /= 2.0
    return total / norm


@dataclass
class Terrain:
    """A height grid with land cover and tree crowns on it."""

    x0: float
    y0: float
    cell_m: float
    heights: np.ndarray                      # (ny, nx) ground height in metres
    cover: np.ndarray | None = None          # (ny, nx) land cover class per node
    canopy_m: np.ndarray | None = None       # (ny, nx) crown-top height above ground (0 = no trees)
    follow_radius_m: float = 15.0
    _follow: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.heights = np.asarray(self.heights, dtype=float)
        if self.canopy_m is None:
            self.canopy_m = np.zeros_like(self.heights)
        if self.cover is None:
            self.cover = np.full(self.heights.shape, GRASS, dtype=int)
        dsm = self.heights + self.canopy_m
        self._follow = _max_filter(dsm, int(math.ceil(self.follow_radius_m / self.cell_m)))

    # -- geometry ------------------------------------------------------------------
    @property
    def shape(self) -> tuple[int, int]:
        return self.heights.shape

    @property
    def extent(self) -> tuple[float, float, float, float]:
        ny, nx = self.heights.shape
        return self.x0, self.y0, self.x0 + (nx - 1) * self.cell_m, self.y0 + (ny - 1) * self.cell_m

    def _interp(self, grid: np.ndarray, x, y) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        ny, nx = grid.shape
        gx = np.clip((x - self.x0) / self.cell_m, 0.0, nx - 1.000001)
        gy = np.clip((y - self.y0) / self.cell_m, 0.0, ny - 1.000001)
        ix = gx.astype(int)
        iy = gy.astype(int)
        fx = gx - ix
        fy = gy - iy
        return (grid[iy, ix] * (1 - fx) * (1 - fy) + grid[iy, ix + 1] * fx * (1 - fy)
                + grid[iy + 1, ix] * (1 - fx) * fy + grid[iy + 1, ix + 1] * fx * fy)

    def height(self, x, y) -> np.ndarray:
        """Ground height at (x, y), bilinear; outside the grid the edge value."""
        return self._interp(self.heights, x, y)

    def canopy_at(self, x, y) -> np.ndarray:
        """Crown-top height above the ground at (x, y)."""
        return self._interp(self.canopy_m, x, y)

    def follow_height(self, x, y) -> np.ndarray:
        """Height of the surface a terrain-following drone keeps its altitude above (DSM, maximum-filtered)."""
        return self._interp(self._follow, x, y)

    def follow(self, x, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Followed surface height and its slope (dh/dx, dh/dy) at (x, y)."""
        e = 0.5 * self.cell_m
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        h = self._interp(self._follow, x, y)
        hx = (self._interp(self._follow, x + e, y) - self._interp(self._follow, x - e, y)) / (2 * e)
        hy = (self._interp(self._follow, x, y + e) - self._interp(self._follow, x, y - e)) / (2 * e)
        return h, hx, hy

    def gradient(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        """Ground slope (dh/dx, dh/dy) by central differences over one cell."""
        e = 0.5 * self.cell_m
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        gx = (self.height(x + e, y) - self.height(x - e, y)) / (2 * e)
        gy = (self.height(x, y + e) - self.height(x, y - e)) / (2 * e)
        return gx, gy

    def cover_at(self, x, y) -> np.ndarray:
        """Land cover class at the nearest node."""
        ny, nx = self.heights.shape
        ix = np.clip(np.rint((np.asarray(x, dtype=float) - self.x0) / self.cell_m).astype(int), 0, nx - 1)
        iy = np.clip(np.rint((np.asarray(y, dtype=float) - self.y0) / self.cell_m).astype(int), 0, ny - 1)
        return self.cover[iy, ix]

    def line_of_sight(self, a: np.ndarray, b: np.ndarray, samples: int = 24, clearance_m: float = 0.5) -> np.ndarray:
        """Whether the straight segments a[i] -> b[i] stay above the ground (shapes (m, 3) -> (m,)).

        The end points themselves are not tested, so a point on the ground can be seen.
        """
        a = np.asarray(a, dtype=float).reshape(-1, 3)
        b = np.asarray(b, dtype=float).reshape(-1, 3)
        s = np.linspace(0.0, 1.0, samples + 2)[1:-1]
        pts = a[:, None, :] + (b - a)[:, None, :] * s[None, :, None]
        ground = self.height(pts[..., 0], pts[..., 1])
        return np.all(pts[..., 2] > ground + clearance_m, axis=1)

    # -- export ----------------------------------------------------------------------
    def to_dict(self) -> dict:
        ny, nx = self.heights.shape
        return {
            "x0": self.x0, "y0": self.y0, "cell_m": self.cell_m, "nx": nx, "ny": ny,
            "heights": [round(float(v), 1) for v in self.heights.ravel()],
            "max_m": round(float(self.heights.max()), 1), "min_m": round(float(self.heights.min()), 1),
        }

    def landcover_dict(self) -> dict:
        ny, nx = self.heights.shape
        out = {"x0": self.x0, "y0": self.y0, "cell_m": self.cell_m, "nx": nx, "ny": ny,
               "classes": list(COVER_NAMES), "grid": [int(v) for v in self.cover.ravel()]}
        if self.canopy_m is not None:
            # Tree and scrub heights above the ground, for drawing the forest (drones fly over its top).
            out["canopy_m"] = [round(float(v) * 2) / 2 for v in self.canopy_m.ravel()]
        return out


def valley(rng: np.random.Generator, extent: tuple[float, float, float, float], cell_m: float = 5.0,
           block: tuple[float, float, float, float] = (0.0, 0.0, 300.0, 200.0), relief_m: float = 35.0,
           ridge_m: float = 120.0, forest_share: float = 0.3) -> Terrain:
    """A valley of dry scrub between two ridges, with forest in the gullies and rock on the steep tops.

    ``block`` is the survey block. Inside it the ground rises by up to about ``relief_m`` (a valley
    floor, the lower slopes and a knoll); the ridges outside it climb to about ``ridge_m``, the
    mountain backdrop. The valley runs along x with a gentle down-valley slope.
    """
    x0, y0, x1, y1 = extent
    nx = int(round((x1 - x0) / cell_m)) + 1
    ny = int(round((y1 - y0) / cell_m)) + 1
    xs = x0 + np.arange(nx) * cell_m
    ys = y0 + np.arange(ny) * cell_m
    X, Y = np.meshgrid(xs, ys)
    bx0, by0, bx1, by1 = block
    cy = 0.5 * (by0 + by1) + rng.uniform(-0.15, 0.15) * (by1 - by0)
    half = 0.5 * (by1 - by0)
    # Valley axis wanders a little; distance across it in units of the block's half-height.
    wander = 0.25 * half * smooth_noise(rng, (1, nx), 40.0 / cell_m, octaves=2)[0]
    across = (Y - (cy + wander[None, :])) / half
    side = np.where(across > 0, rng.uniform(0.85, 1.15), rng.uniform(0.85, 1.15))
    # Gentle within the block, steep beyond it: a smooth floor plus ridges that rise outside |across| > 1.
    floor = relief_m * 0.35 * np.minimum(across ** 2, 1.44)
    rise = np.clip((np.abs(across) - 0.95) / 1.5, 0.0, 1.0)
    crest = 1.0 + 0.25 * smooth_noise(rng, (ny, nx), 80.0 / cell_m, octaves=2)
    ridges = ridge_m * side * crest * rise * rise * (3.0 - 2.0 * rise)
    down_valley = 0.03 * (X - bx0)
    # A knoll somewhere inside the block.
    kx = rng.uniform(bx0 + 0.25 * (bx1 - bx0), bx0 + 0.75 * (bx1 - bx0))
    ky = rng.uniform(by0 + 0.3 * (by1 - by0), by0 + 0.7 * (by1 - by0))
    knoll = relief_m * 0.55 * np.exp(-(((X - kx) / 45.0) ** 2 + ((Y - ky) / 35.0) ** 2))
    rough = 6.0 * smooth_noise(rng, (ny, nx), 60.0 / cell_m) + 2.0 * smooth_noise(rng, (ny, nx), 15.0 / cell_m)
    heights = floor + ridges + down_valley + knoll + rough
    heights -= heights.min()
    # Slope for cover: forest in gullies and on the lower shaded slopes, rock on steep high ground.
    gy, gx = np.gradient(heights, cell_m)
    slope = np.hypot(gx, gy)
    wet = smooth_noise(rng, (ny, nx), 30.0 / cell_m)
    forest_score = wet - 0.6 * np.clip(slope - 0.35, 0.0, None) - 0.004 * np.clip(heights - 60.0, 0.0, None)
    grass_score = smooth_noise(rng, (ny, nx), 25.0 / cell_m)
    # Shares are set over the survey block, so the block is mostly scrub whatever the ridges look like.
    inside = (X >= bx0) & (X <= bx1) & (Y >= by0) & (Y <= by1)
    threshold = np.quantile(forest_score[inside], 1.0 - forest_share)
    grass_threshold = np.quantile(grass_score[inside], 0.85)
    cover = np.full((ny, nx), SCRUB, dtype=int)
    cover[grass_score >= grass_threshold] = GRASS
    cover[forest_score >= threshold] = FOREST
    cover[(slope > 0.7) | (heights > 0.85 * heights.max())] = ROCK
    canopy = np.where(cover == FOREST, rng.uniform(10.0, 20.0, size=(ny, nx)), 0.0)
    canopy = np.where(cover == SCRUB, 1.5, canopy)
    return Terrain(float(x0), float(y0), float(cell_m), heights, cover, canopy)
