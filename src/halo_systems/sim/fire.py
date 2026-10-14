"""Surface fire spread: Rothermel's model on a grid, for the truth and for the forecast.

Rate of spread
--------------
The head-fire rate of spread is Rothermel's surface fire model (Rothermel 1972,
USDA Forest Service research paper INT-115) with Albini's (1976) revisions, the
model inside BEHAVE, FARSITE and FlamMap. Fuels are Anderson's (1982) standard
fuel models (USDA Forest Service general technical report INT-122). As a check,
``anderson_check()`` reproduces Anderson's published rates of spread at 8% dead
fuel moisture, 100% live fuel moisture and a 5 mi/h midflame wind: every model
within 20% of his table, and the three the wildfire valley burns (1 grass, 6 dormant
brush, 10 timber litter and understory) within 8%. The unit test holds them to that.

Wind and slope add as vectors (Rothermel's phi_w along the wind, phi_s up the
slope). The effective wind speed of their sum sets the fire's ellipse, with
Anderson's (1983) length-to-breadth ratio, and the rate in any direction comes
from the ellipse with the ignition at its rear focus.

Spread over the landscape
-------------------------
Arrival times come from a minimum-travel-time calculation (as in Finney 2002,
the method in FlamMap): every cell links to its 16 neighbours (the eight
around it and the eight knight moves), the travel time of a link is its length
over the rate of spread in that direction (averaged over the two cells), and
arrival times are relaxed until nothing changes. Conditions can change through
the day: each period has its own wind and fuel moisture, and spread restarts
from wherever the fire had reached when the period began.

Everything is in SI units at the interface (metres, seconds, m/s); the
Rothermel core works in its native feet, pounds, BTU and minutes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .terrain import BARE, FOREST, GRASS, ROCK, SCRUB, Terrain

FT = 0.3048
TONS_PER_ACRE_TO_LB_PER_FT2 = 2000.0 / 43560.0
MPH_TO_FT_PER_MIN = 88.0
CHAINS_PER_HOUR_TO_M_PER_MIN = 20.1168 / 60.0


@dataclass(frozen=True)
class FuelModel:
    """One of Anderson's (1982) fuel models: loads in tons/acre, SAV in 1/ft, depth in ft."""

    number: int
    name: str
    load_1h: float
    load_10h: float
    load_100h: float
    load_live: float
    sav_1h: float
    sav_live: float
    depth_ft: float
    mx_dead: float              # dead fuel moisture of extinction (fraction)
    heat: float = 8000.0        # BTU/lb


# Anderson (1982), table 1, and the rates of spread his guide quotes at 8% dead and 100% live
# moisture with a 5 mi/h midflame wind on flat ground (chains per hour), for the check.
FUEL_MODELS = {
    1: FuelModel(1, "short grass", 0.74, 0.0, 0.0, 0.0, 3500, 1500, 1.0, 0.12),
    2: FuelModel(2, "timber with grass understory", 2.0, 1.0, 0.5, 0.5, 3000, 1500, 1.0, 0.15),
    3: FuelModel(3, "tall grass", 3.01, 0.0, 0.0, 0.0, 1500, 1500, 2.5, 0.25),
    4: FuelModel(4, "chaparral", 5.01, 4.01, 2.0, 5.01, 2000, 1500, 6.0, 0.20),
    5: FuelModel(5, "brush", 1.0, 0.5, 0.0, 2.0, 2000, 1500, 2.0, 0.20),
    6: FuelModel(6, "dormant brush", 1.5, 2.5, 2.0, 0.0, 1750, 1500, 2.5, 0.25),
    8: FuelModel(8, "closed timber litter", 1.5, 1.0, 2.5, 0.0, 2000, 1500, 0.2, 0.30),
    9: FuelModel(9, "hardwood litter", 2.92, 0.41, 0.15, 0.0, 2500, 1500, 0.2, 0.25),
    10: FuelModel(10, "timber litter and understory", 3.01, 2.0, 5.01, 2.0, 2000, 1500, 1.0, 0.25),
}
ANDERSON_ROS_CH_PER_H = {1: 78.0, 2: 35.0, 3: 104.0, 4: 75.0, 5: 18.0, 6: 32.0, 8: 1.6, 9: 7.5, 10: 7.9}

# Land cover to fuel model for a dry valley the morning after a lightning storm.
COVER_FUEL = {GRASS: 1, SCRUB: 6, FOREST: 10, ROCK: 0, BARE: 0}


@dataclass(frozen=True)
class Moisture:
    """Fuel moisture as fractions of dry weight."""

    d1: float = 0.08
    d10: float = 0.09
    d100: float = 0.10
    live: float = 1.0


def rothermel(fm: FuelModel, moist: Moisture, wind_mph: float = 0.0, slope_tan: float = 0.0) -> dict:
    """Rothermel's head-fire rate of spread for wind and slope in the same direction.

    Returns ros_ft_min, the no-wind no-slope rate r0_ft_min, the factors needed to combine wind and
    slope as vectors (phi_w per unit wind, the wind-factor constants, phi_s per unit tan^2) and the
    reaction intensity.
    """
    rho_p, s_t, s_e = 32.0, 0.0555, 0.010
    loads_dead = np.array([fm.load_1h, fm.load_10h, fm.load_100h]) * TONS_PER_ACRE_TO_LB_PER_FT2
    sav_dead = np.array([fm.sav_1h, 109.0, 30.0])
    m_dead = np.array([moist.d1, moist.d10, moist.d100])
    load_live = fm.load_live * TONS_PER_ACRE_TO_LB_PER_FT2
    sav_live = fm.sav_live
    total_load = loads_dead.sum() + load_live
    if total_load <= 0 or fm.depth_ft <= 0:
        return {"ros_ft_min": 0.0, "r0_ft_min": 0.0, "ir": 0.0}
    a_dead = sav_dead * loads_dead / rho_p
    a_live = sav_live * load_live / rho_p
    a_d, a_l = a_dead.sum(), a_live
    a_t = a_d + a_l
    f_dead_j = a_dead / a_d if a_d > 0 else np.zeros(3)
    f_d, f_l = a_d / a_t, a_l / a_t
    sigma_d = float(np.sum(f_dead_j * sav_dead))
    sigma = f_d * sigma_d + f_l * sav_live
    rho_b = total_load / fm.depth_ft
    beta = rho_b / rho_p
    beta_op = 3.348 * sigma ** -0.8189
    ratio = beta / beta_op
    gamma_max = sigma ** 1.5 / (495.0 + 0.0594 * sigma ** 1.5)
    a_exp = 133.0 * sigma ** -0.7913
    gamma = gamma_max * ratio ** a_exp * math.exp(a_exp * (1.0 - ratio))
    wn_dead = float(np.sum(f_dead_j * loads_dead)) * (1 - s_t)
    wn_live = load_live * (1 - s_t)
    mf_dead = float(np.sum(f_dead_j * m_dead))
    # Live fuel moisture of extinction (Albini 1976).
    if load_live > 0:
        eps_d = loads_dead * np.exp(-138.0 / sav_dead)
        w_prime = eps_d.sum() / (load_live * math.exp(-500.0 / sav_live))
        mf_fine = float(np.sum(eps_d * m_dead) / max(eps_d.sum(), 1e-12))
        mx_live = max(fm.mx_dead, 2.9 * w_prime * (1.0 - mf_fine / fm.mx_dead) - 0.226)
    else:
        mx_live = fm.mx_dead

    def damping(m, mx):
        r = min(1.0, m / mx) if mx > 0 else 1.0
        return max(0.0, 1.0 - 2.59 * r + 5.11 * r * r - 3.52 * r ** 3)

    eta_s = min(1.0, 0.174 * s_e ** -0.19)
    ir = gamma * (wn_dead * fm.heat * damping(mf_dead, fm.mx_dead) * eta_s
                  + wn_live * fm.heat * damping(moist.live, mx_live) * eta_s)
    xi = math.exp((0.792 + 0.681 * sigma ** 0.5) * (beta + 0.1)) / (192.0 + 0.2595 * sigma)
    q_dead = 250.0 + 1116.0 * m_dead
    q_live = 250.0 + 1116.0 * moist.live
    sink = rho_b * (f_d * float(np.sum(f_dead_j * np.exp(-138.0 / sav_dead) * q_dead))
                    + f_l * math.exp(-138.0 / sav_live) * q_live)
    r0 = ir * xi / sink if sink > 0 else 0.0
    c = 7.47 * math.exp(-0.133 * sigma ** 0.55)
    b = 0.02526 * sigma ** 0.54
    e = 0.715 * math.exp(-3.59e-4 * sigma)
    u = min(wind_mph * MPH_TO_FT_PER_MIN, 0.9 * ir)      # wind limit
    phi_w = c * u ** b * ratio ** -e if u > 0 else 0.0
    phi_s = 5.275 * beta ** -0.3 * slope_tan ** 2
    return {"ros_ft_min": r0 * (1.0 + phi_w + phi_s), "r0_ft_min": r0, "ir": ir, "c": c, "b": b, "e": e,
            "ratio": ratio, "beta": beta, "phi_s_per_tan2": 5.275 * beta ** -0.3, "wind_limit_ft_min": 0.9 * ir}


def anderson_check() -> dict:
    """Rate of spread for each fuel model at Anderson's reference conditions, against his table."""
    out = {}
    for k, fm in FUEL_MODELS.items():
        r = rothermel(fm, Moisture(0.08, 0.08, 0.08, 1.0), wind_mph=5.0)
        ch_h = r["ros_ft_min"] * 60.0 / 66.0
        out[k] = {"model_ch_per_h": round(ch_h, 1), "anderson_ch_per_h": ANDERSON_ROS_CH_PER_H[k],
                  "ratio": round(ch_h / ANDERSON_ROS_CH_PER_H[k], 3)}
    return out


def length_to_breadth(effective_wind_mph):
    """Anderson (1983): fire ellipse length-to-breadth ratio from the effective midflame wind (capped at 8)."""
    u = np.asarray(effective_wind_mph, dtype=float)
    return np.clip(0.936 * np.exp(0.2566 * u) + 0.461 * np.exp(-0.1548 * u) - 0.397, 1.0, 8.0)


# ---------------------------------------------------------------------------------------------------
@dataclass
class Period:
    """Weather for one stretch of the day: from ``start_s`` until the next period starts."""

    start_s: float
    wind_mps: tuple[float, float]       # 10 m wind (x, y) in m/s
    moisture: Moisture = field(default_factory=Moisture)


MIDFLAME_FACTOR = {1: 0.36, 2: 0.36, 3: 0.44, 4: 0.55, 5: 0.36, 6: 0.44, 8: 0.28, 9: 0.28, 10: 0.36}
"""Albini and Baughman (1979) wind adjustment factors from 20 ft wind to midflame, by fuel model.
The 20 ft (6.1 m) wind is taken as 0.87 of the 10 m wind (open terrain)."""

STEPS = [(dx, dy) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
         if (dx, dy) != (0, 0) and max(abs(dx), abs(dy)) == 1 or {abs(dx), abs(dy)} == {1, 2}]


class SpreadGrid:
    """Fuel, slope and per-period direction-dependent spread rates on a grid; arrival times by relaxation."""

    def __init__(self, terrain: Terrain, cell_m: float, extent: tuple[float, float, float, float] | None = None,
                 ros_multiplier: np.ndarray | None = None):
        x0, y0, x1, y1 = extent or terrain.extent
        self.cell_m = float(cell_m)
        self.x0, self.y0 = float(x0), float(y0)
        self.nx = int(round((x1 - x0) / cell_m)) + 1
        self.ny = int(round((y1 - y0) / cell_m)) + 1
        xs = self.x0 + np.arange(self.nx) * self.cell_m
        ys = self.y0 + np.arange(self.ny) * self.cell_m
        self.X, self.Y = np.meshgrid(xs, ys)
        self.z = terrain.height(self.X, self.Y)
        self.fuel = np.vectorize(lambda c: COVER_FUEL.get(int(c), 0))(terrain.cover_at(self.X, self.Y))
        gx, gy = terrain.gradient(self.X, self.Y)
        self.slope_x, self.slope_y = gx, gy
        self.mult = np.ones_like(self.z) if ros_multiplier is None else ros_multiplier
        self._cache: dict = {}

    def cell_of(self, x, y):
        ix = np.clip(np.rint((np.asarray(x) - self.x0) / self.cell_m).astype(int), 0, self.nx - 1)
        iy = np.clip(np.rint((np.asarray(y) - self.y0) / self.cell_m).astype(int), 0, self.ny - 1)
        return iy, ix

    def _rates(self, period: Period, cache: bool = True) -> list[np.ndarray]:
        """Rate of spread (m/s) along each of the 16 steps, per cell, for one period's conditions.

        Rates are kept for reuse unless ``cache`` is False (forecast members, whose weather is never repeated).
        """
        key = (period.wind_mps, period.moisture)
        if key in self._cache:
            return self._cache[key]
        wx, wy = period.wind_mps
        w10 = math.hypot(wx, wy)
        head = np.zeros_like(self.z)            # head-fire rate, m/s
        dirx = np.zeros_like(self.z)
        diry = np.zeros_like(self.z)
        lb = np.ones_like(self.z)
        for k in np.unique(self.fuel):
            if k == 0:
                continue
            mask = self.fuel == k
            fm = FUEL_MODELS[int(k)]
            base = rothermel(fm, period.moisture)
            r0 = base["r0_ft_min"]
            if r0 <= 0:
                continue
            u_mid = w10 * 0.87 * MIDFLAME_FACTOR[int(k)] / 0.44704 * MPH_TO_FT_PER_MIN    # ft/min
            u_mid = min(u_mid, base["wind_limit_ft_min"])
            phi_w = base["c"] * u_mid ** base["b"] * base["ratio"] ** -base["e"] if u_mid > 0 else 0.0
            ux, uy = (wx / w10, wy / w10) if w10 > 0 else (0.0, 0.0)
            sx, sy = self.slope_x[mask], self.slope_y[mask]
            tan2 = sx * sx + sy * sy
            phi_s = base["phi_s_per_tan2"] * tan2
            snorm = np.sqrt(np.maximum(tan2, 1e-12))
            vx = phi_w * ux + phi_s * sx / snorm
            vy = phi_w * uy + phi_s * sy / snorm
            phi_e = np.hypot(vx, vy)
            ros = r0 * (1.0 + phi_e) * FT / 60.0                                      # m/s
            # Effective wind (mi/h) that alone would give phi_e, for the ellipse shape.
            u_eff = np.where(phi_e > 0, (phi_e * base["ratio"] ** base["e"] / base["c"]) ** (1.0 / base["b"]), 0.0)
            head[mask] = ros
            dirx[mask] = np.where(phi_e > 0, vx / np.maximum(phi_e, 1e-12), 1.0)
            diry[mask] = np.where(phi_e > 0, vy / np.maximum(phi_e, 1e-12), 0.0)
            lb[mask] = length_to_breadth(u_eff / MPH_TO_FT_PER_MIN)
        head *= self.mult
        ecc = np.sqrt(np.maximum(lb * lb - 1.0, 0.0)) / lb
        rates = []
        for dx, dy in STEPS:
            norm = math.hypot(dx, dy)
            cos_t = (dx * dirx + dy * diry) / norm
            rates.append(head * (1.0 - ecc) / np.maximum(1.0 - ecc * cos_t, 1e-9))
        if cache:
            self._cache[key] = rates
        return rates

    def head_rate(self, period: Period, x: float, y: float) -> float:
        """Head-fire rate of spread (m/s) at a point."""
        rates = self._rates(period)
        iy, ix = self.cell_of(x, y)
        return float(max(r[iy, ix] for r in rates))

    def arrival(self, ignitions: list[tuple[float, float, float]], periods: list[Period], horizon_s: float,
                max_iter: int = 2000, scale: float = 1.0, cache: bool = True) -> np.ndarray:
        """Arrival time (s) at every cell; ``inf`` where the fire does not arrive before ``horizon_s``.

        ``ignitions`` are (x, y, t_ignite) points; a cell holding an ignition burns from its time.
        ``scale`` multiplies every spread rate (a forecast member's error on the fuel model).
        """
        t = np.full(self.z.shape, np.inf)
        for x, y, t0 in ignitions:
            iy, ix = self.cell_of(x, y)
            t[iy, ix] = min(t[iy, ix], t0)
        periods = sorted(periods, key=lambda p: p.start_s)
        for i, per in enumerate(periods):
            end = periods[i + 1].start_s if i + 1 < len(periods) else horizon_s
            if end <= per.start_s:
                continue
            rates = self._rates(per, cache=cache)
            if scale != 1.0:
                rates = [r * scale for r in rates]
            # Cells already burning by the start of this period are fixed; the rest are recomputed.
            fixed = t <= per.start_s
            frontier = np.where(fixed, np.maximum(t, per.start_s), np.inf)
            # Ignitions scheduled inside this period also seed it.
            pending = (t > per.start_s) & (t < end) & np.isfinite(t)
            cur = np.where(fixed, frontier, np.inf)
            seeds = np.where(pending, t, np.inf)
            cur = np.minimum(cur, seeds)
            costs = []
            for (dx, dy), rate in zip(STEPS, rates):
                dist = self.cell_m * math.hypot(dx, dy)
                # Rate leaving the source cell and arriving at the target, averaged as travel times.
                src = np.roll(np.roll(rate, dy, axis=0), dx, axis=1)
                with np.errstate(divide="ignore"):
                    cost = 0.5 * dist / src + 0.5 * dist / rate
                cost = np.where((src > 0) & (rate > 0), cost, np.inf)
                costs.append(self._mask_wrap(cost, dx, dy))
            # Bellman-Ford sweeps on a grid padded with two cells of inf, so a shift is a slice, not a copy.
            # Times at or past the period's end are dropped as they appear: they cannot lead to an earlier
            # time and are discarded afterwards anyway, and dropping them stops the sweeps at the fire front.
            ny, nx = cur.shape
            pad = np.full((ny + 4, nx + 4), np.inf)
            inner = pad[2:-2, 2:-2]
            cur = np.where(cur < end, cur, np.inf)
            inner[...] = cur
            views = [(pad[2 - dy:2 - dy + ny, 2 - dx:2 - dx + nx], cost) for (dx, dy), cost in zip(STEPS, costs)]
            keep = fixed & np.isfinite(cur)
            buf = np.empty_like(cur)
            for _ in range(max_iter):
                new = inner.copy()
                for shifted, cost in views:
                    np.add(shifted, cost, out=buf)
                    np.minimum(new, buf, out=new)
                new[keep] = inner[keep]
                new[new >= end] = np.inf
                if np.array_equal(new, inner):
                    break
                inner[...] = new
            cur = inner.copy()
            # Cells this period reaches take their new time; ignitions still to come keep theirs.
            t = np.where(fixed, t, np.where(cur < end, cur, np.where(t >= end, t, np.inf)))
        t[t > horizon_s] = np.inf
        return t

    def _mask_wrap(self, cost: np.ndarray, dx: int, dy: int) -> np.ndarray:
        """Links that np.roll wraps around the grid edge are cut."""
        c = cost.copy()
        if dx > 0:
            c[:, :dx] = np.inf
        elif dx < 0:
            c[:, dx:] = np.inf
        if dy > 0:
            c[:dy, :] = np.inf
        elif dy < 0:
            c[dy:, :] = np.inf
        return c
