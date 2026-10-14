"""The wildfire world and the ground station's side of it.

What is true (the simulation's ground truth):

* Each lightning ignition smoulders in place until its activation time, then spreads by Rothermel's
  model over the terrain's fuels (``fire.py``), with the day's weather in hourly periods and a
  patchy, seed-dependent multiplier on the spread rate so no two hillsides burn alike.
* A fire's burning area makes its hotspot (what a thermal camera can find), its smoke plume (what an RGB
  camera can see) and its particulate plume (what the PM2.5 sensor smells), in ``smoke.py``.
* A fire under tree crowns is partly hidden from a camera looking down; one under a rock shelf is hidden
  completely, and only its smoke gives it away.

What the ground station knows, and does with it:

1. **Locate.** A thermal find gives a fire's position to within the navigation error. Smoke sightings
   give bearings: a sighting that points along the plume of a fire already located sizes that fire from
   the height of the smoke it saw; the rest are triangulated (``smoke.triangulate``), and a cluster of
   crossings becomes a fire only when a least-squares fix from several drones holds up without any one of
   them (``smoke.fix_is_sound``), which rejects the ghost crossings of two plumes' bearings. The fix is
   then moved upwind by the plume's drift. A smoke location is upgraded when a thermal find lands near it.
2. **Forecast.** For the located fires, an ensemble of spread runs (perturbed wind, fuel moisture,
   spread rate and position) gives, per map cell, the median arrival time and the probability the
   fire arrives within 30, 60, 120 and 180 minutes. It is run twice: for the fires the station believes
   are spreading, and for every located fire as if the smouldering ones took off now. The weather inputs
   are the drones' own wind estimate for now and a weather forecast for later, which is not the true
   weather, and the station never sees the patchy spread-rate multiplier the truth burns with.
3. **Brief.** Per fire: how it was found, how fast its head runs, how big it will be in one and two
   hours, which way it is going, and which places it reaches and when.
4. **Alert.** Phones where the spreading-fire forecast gives at least ``prob_threshold`` of fire within
   60 minutes get an evacuation alert; phones where the take-off forecast gives that within 180 minutes
   get a warning. Each alert is drafted for a zone, checked like every other decision (``AlertCheck``),
   and sent only after a person approves it: an incident commander's tap in the dashboard, or a modelled
   approval delay in batch runs. Alerts go to simulated phones only.

Nothing here calls a language model; the forecast and the alert rules are deterministic given the seed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .fire import Moisture, Period, SpreadGrid
from .smoke import (SmokeConfig, bearing_fix, cluster, fix_is_sound, plume_points, plume_top, pm_concentration,
                    triangulate)
from .terrain import Terrain, smooth_noise

SMOULDER_AREA_M2 = 50.0      # the station treats a fire estimated smaller than this as smouldering, not spreading
MATCH_M = 60.0               # a located fire within this of a true fire counts as finding it (scoring only)


@dataclass
class FireSpec:
    x: float
    y: float
    t_active_s: float                 # when it starts to spread (flight starts at 0); before that it smoulders
    smoulder_area_m2: float = 4.0
    shelter: str = "open"             # "open", "canopy" or "overhang"
    cover: float = 0.0                # share of the hotspot hidden from a camera looking down
    occluder_m: float = 0.0           # height above ground that hides the base of the plume

    def to_dict(self, z: float, i: int) -> dict:
        return {"id": i, "x": round(self.x, 1), "y": round(self.y, 1), "z": round(z, 1), "shelter": self.shelter,
                "cover": round(self.cover, 2), "t_active_s": round(self.t_active_s, 1)}


@dataclass
class Phone:
    id: int
    x: float
    y: float
    group: str                        # "home", "campground", "trail", "road"

    def to_dict(self) -> dict:
        return {"id": self.id, "x": round(self.x, 1), "y": round(self.y, 1), "group": self.group}


@dataclass
class ForecastConfig:
    cell_m: float = 10.0
    members: int = 16
    horizon_s: float = 3 * 3600.0
    every_s: float = 120.0
    min_gap_s: float = 30.0               # news (a new fire, one taking off) brings the next forecast forward, to no sooner than this
    wind_dir_sigma_deg: float = 10.0
    wind_speed_sigma: float = 0.15
    ros_sigma: float = 0.3
    moisture_sigma: float = 0.01
    measured_wind_sigma_mps: float = 0.5
    measured_dir_sigma_deg: float = 5.0
    elevation_sigma_deg: float = 1.0      # error on the angle up to a plume's top, used to size the fire


@dataclass
class AlertPolicy:
    rule: str = "forecast"            # "forecast", or "distance" (the ablation: alert by distance alone)
    prob_threshold: float = 0.3
    evacuate_within_s: float = 3600.0
    warn_within_s: float = 3 * 3600.0
    danger_radius_m: float = 50.0     # fire this close to a phone counts as reaching it
    distance_evacuate_m: float = 800.0
    distance_warn_m: float = 1600.0
    zone_buffer_m: float = 100.0
    approval: str = "modelled"        # "modelled" (batch runs) or "manual" (dashboard tap)
    approval_median_s: float = 90.0
    approval_sigma: float = 0.5
    delivery_s: tuple[float, float] = (5.0, 30.0)
    unreachable_share: float = 0.05
    max_recipients: int = 5000
    max_zone_ha: float = 5000.0
    max_chars: int = 360              # Wireless Emergency Alerts and EU-Alert carry up to 360 characters
    authority: str = "Valley Fire District"


@dataclass
class WildfireConfig:
    fires: list[FireSpec]
    weather: list[Period]             # the true weather
    weather_forecast: list[Period]    # what the station is told
    phones: list[Phone] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)
    overhangs: list[dict] = field(default_factory=list)
    smoke: SmokeConfig = field(default_factory=SmokeConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    alerts: AlertPolicy = field(default_factory=AlertPolicy)
    truth_cell_m: float = 5.0
    horizon_s: float = 4 * 3600.0     # truth is computed this far; forecasts made during the flight look 3 h ahead
    heterogeneity_sigma: float = 0.3

    def summary(self, terrain: Terrain) -> dict:
        return {
            "fires": [f.to_dict(float(terrain.height(f.x, f.y)), i) for i, f in enumerate(self.fires)],
            "phones": [p.to_dict() for p in self.phones],
            "places": self.places,
            "overhangs": self.overhangs,
            "smoke_model": {"rise_mps": self.smoke.rise_mps, "top_ref_m": self.smoke.top_ref_m,
                            "ref_area_m2": self.smoke.ref_area_m2, "top_max_m": self.smoke.top_max_m},
            "alert_rule": self.alerts.rule,
        }


def compass(dx: float, dy: float) -> str:
    """Direction a vector points, as a 16-point compass word (x east, y north)."""
    names = ["east", "east-northeast", "northeast", "north-northeast", "north", "north-northwest", "northwest",
             "west-northwest", "west", "west-southwest", "southwest", "south-southwest", "south", "south-southeast",
             "southeast", "east-southeast"]
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    return names[int(round(ang / 22.5)) % 16]


def convex_hull(points: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain; returns the hull's corners counter-clockwise."""
    pts = np.unique(np.asarray(points, dtype=float).reshape(-1, 2), axis=0)
    if len(pts) <= 2:
        return pts
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                a, b = out[-1] - out[-2], p - out[-2]
                if a[0] * b[1] - a[1] * b[0] > 0:
                    break
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def buffered_zone(points: np.ndarray, buffer_m: float, sides: int = 12) -> np.ndarray:
    """A polygon around points: the hull of a small circle of ``buffer_m`` around each point."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    ang = np.linspace(0, 2 * math.pi, sides, endpoint=False)
    ring = np.stack([np.cos(ang), np.sin(ang)], axis=1) * buffer_m
    return convex_hull((pts[:, None, :] + ring[None, :, :]).reshape(-1, 2))


def inside(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Points inside a convex counter-clockwise polygon."""
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    if len(poly) < 3:
        return np.zeros(len(pts), dtype=bool)
    a = poly
    b = np.roll(poly, -1, axis=0)
    cross = (b[None, :, 0] - a[None, :, 0]) * (pts[:, None, 1] - a[None, :, 1]) - \
            (b[None, :, 1] - a[None, :, 1]) * (pts[:, None, 0] - a[None, :, 0])
    return np.all(cross >= -1e-9, axis=1)


def polygon_area(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


@dataclass
class AlertCheckResult:
    ok: bool
    reasons: list

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": self.reasons}


class AlertCheck:
    """Every alert passes this before anyone can approve it, as every directive passes the safety validator."""

    REQUIRED = ("hazard", "area", "action", "expires", "authority")

    def __init__(self, policy: AlertPolicy):
        self.policy = policy

    def check(self, alert: dict, forecast: dict | None, recent: list[dict]) -> AlertCheckResult:
        p = self.policy
        reasons = []
        text = alert.get("message", "")
        if not text or len(text) > p.max_chars:
            reasons.append(f"message must be 1 to {p.max_chars} characters (is {len(text)})")
        for part in self.REQUIRED:
            if not alert.get(part):
                reasons.append(f"missing {part}")
        if alert.get("level") not in ("evacuate", "warning"):
            reasons.append("level must be evacuate or warning")
        zone = np.asarray(alert.get("zone", []), dtype=float).reshape(-1, 2)
        area_ha = polygon_area(zone) / 1e4
        if len(zone) < 3 or area_ha <= 0:
            reasons.append("zone is not a polygon")
        elif area_ha > p.max_zone_ha:
            reasons.append(f"zone of {area_ha:.0f} ha is larger than {p.max_zone_ha:.0f} ha; needs escalation")
        n = len(alert.get("phones", []))
        if n == 0:
            reasons.append("no recipients")
        elif n > p.max_recipients:
            reasons.append(f"{n} recipients is more than {p.max_recipients}; needs escalation")
        if not 3600 <= alert.get("expires_in_s", 0) <= 12 * 3600:
            reasons.append("expiry must be 1 to 12 hours")
        if p.rule == "forecast" and alert.get("level") == "evacuate":
            # An evacuation must rest on the forecast it cites.
            if forecast is None or forecast.get("id") != alert.get("forecast_id"):
                reasons.append("evacuation must cite the current forecast")
            elif max(alert.get("max_prob", 0.0), 0.0) < p.prob_threshold:
                reasons.append("forecast probability below the evacuation threshold")
        for r in recent:
            if r["level"] == alert.get("level") and set(alert.get("phones", [])) <= set(r["phones"]) \
                    and alert.get("t", 0) - r["t"] < 600:
                reasons.append(f"duplicate of alert {r['id']} from the last 10 minutes")
                break
        return AlertCheckResult(not reasons, reasons)


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def area_from_top(top_m: float, cfg: SmokeConfig) -> float:
    """Burning area (m^2) whose plume reaches ``top_m``: the inverse of ``smoke.plume_top``."""
    return float(cfg.ref_area_m2 * (max(top_m, 1.0) / cfg.top_ref_m) ** (1 / 0.35))


def sorensen(a: np.ndarray, b: np.ndarray) -> float | None:
    """Sorensen (Dice) index of two burnt-area masks: 1 is a perfect match; None when both are empty."""
    na, nb = int(a.sum()), int(b.sum())
    if na + nb == 0:
        return None
    return 2.0 * int((a & b).sum()) / (na + nb)


class Wildfire:
    """Ground truth for the fires plus the ground station's picture, run alongside a Simulation."""

    def __init__(self, sim):
        sc = sim.sc
        self.cfg: WildfireConfig = sc.wildfire
        self.terrain: Terrain = sc.world.terrain
        cfg = self.cfg
        self.rng = np.random.default_rng(np.random.SeedSequence([sim.seed, 7707]))
        self.fires = cfg.fires
        nf = len(self.fires)
        # -- truth -------------------------------------------------------------------------------
        # A patchy multiplier on the spread rate (fuel loads, gaps, moisture pockets) the station never sees.
        tr = cfg.truth_cell_m
        probe = SpreadGrid(self.terrain, tr)
        noise = smooth_noise(self.rng, probe.z.shape, 40.0 / tr, octaves=2)
        mult = np.exp(cfg.heterogeneity_sigma * noise - 0.5 * cfg.heterogeneity_sigma ** 2)
        self.truth = SpreadGrid(self.terrain, tr, ros_multiplier=mult)
        coarse = SpreadGrid(self.terrain, cfg.forecast.cell_m)
        iy, ix = probe.cell_of(coarse.X, coarse.Y)
        self.truth10 = SpreadGrid(self.terrain, cfg.forecast.cell_m, ros_multiplier=mult[iy, ix])
        ign = [(f.x, f.y, f.t_active_s) for f in self.fires]
        self.fire_arrival = [self.truth10.arrival([g], cfg.weather, cfg.horizon_s) for g in ign]
        self.arrival = self.truth.arrival(ign, cfg.weather, cfg.horizon_s) if nf else np.full(self.truth.z.shape, np.inf)
        self.arrival10 = np.min(self.fire_arrival, axis=0) if nf else np.full(coarse.z.shape, np.inf)
        # What the station models with: the same terrain and fuel map, no knowledge of the patchiness.
        self.grid = coarse
        self.base = np.array([[f.x, f.y, float(self.terrain.height(f.x, f.y))] for f in self.fires]).reshape(-1, 3)
        self.occluder = np.array([f.occluder_m for f in self.fires])
        # -- phones ----------------------------------------------------------------------------
        self.phones = cfg.phones
        self.phone_xy = np.array([[p.x, p.y] for p in self.phones], dtype=float).reshape(-1, 2)
        self._phone_cells = [self._cells_near(self.grid, x, y, cfg.alerts.danger_radius_m) for x, y in self.phone_xy]
        self._phone_cells_truth = [self._cells_near(self.truth, x, y, cfg.alerts.danger_radius_m) for x, y in self.phone_xy]
        self.phone_truth_s = np.array([float(self.arrival[c].min()) if len(c[0]) else math.inf
                                       for c in self._phone_cells_truth])
        self.unreachable = self.rng.random(len(self.phones)) < cfg.alerts.unreachable_share
        # -- station state ---------------------------------------------------------------------
        # (t, drone, x, y, z, bearing_rad, elevation of the plume top, kind, elevation of the lowest visible smoke,
        #  the fire it really was: for scoring only, the station never reads it)
        self.sightings: list[tuple] = []
        self.last_sighting: dict = {}
        self._next_sighting = 0
        self._pool: list[int] = []                # camera sightings no located fire explains yet
        self._next_triangulate = 0.0
        self.located: list[dict] = []
        self.forecasts: list[dict] = []
        self.alerts: list[dict] = []
        self.phone_level = np.zeros(len(self.phones), dtype=int)     # 0 none, 1 warning, 2 evacuate (drafted)
        self.phone_delivered: dict[int, list] = {}                   # phone -> [(level, t)]
        self.contacts = 0
        self.next_station_t = 0.0
        self.next_forecast_t = math.inf
        self.check = AlertCheck(cfg.alerts)
        self._heading = np.zeros((sim.n, 2))
        self._heading[:, 0] = 1.0
        self._thermal_seen = np.zeros(nf, dtype=bool)

    # -- truth queries ----------------------------------------------------------------------------
    @staticmethod
    def _cells_near(grid: SpreadGrid, x: float, y: float, radius: float):
        d2 = (grid.X - x) ** 2 + (grid.Y - y) ** 2
        return np.nonzero(d2 <= radius * radius)

    def area(self, t: float) -> np.ndarray:
        """Burning (or smouldering) area of each fire at time t, m^2."""
        cell = self.grid.cell_m ** 2
        out = np.zeros(len(self.fires))
        for i, f in enumerate(self.fires):
            burnt = float(np.count_nonzero(self.fire_arrival[i] <= t)) * cell if t >= f.t_active_s else 0.0
            out[i] = max(f.smoulder_area_m2, burnt)
        return out

    def wind_at(self, t: float, weather=None) -> tuple[float, float]:
        periods = weather or self.cfg.weather
        per = [p for p in periods if p.start_s <= t]
        return per[-1].wind_mps if per else periods[0].wind_mps

    # -- per sensing tick -------------------------------------------------------------------------
    def tick(self, sim, elapsed: float) -> None:
        t = sim.t
        area = self.area(t)
        if len(sim.targets):
            sim.targets[:, 2] = np.sqrt(area / math.pi)
        act = np.nonzero(sim.active_mask())[0]
        if len(act) and len(self.fires):
            self._sense_smoke(sim, act, area, elapsed)
        if t >= self.next_station_t:
            self.next_station_t = t + 1.0
            self._station(sim, area)

    def _sense_smoke(self, sim, act: np.ndarray, area: np.ndarray, elapsed: float) -> None:
        cfg = self.cfg.smoke
        t = sim.t
        wind = np.asarray(self.wind_at(t), dtype=float)
        p = sim.p[act]
        v = sim.v[act, :2]
        speed = np.linalg.norm(v, axis=1)
        moving = speed > 1.0
        self._heading[act[moving]] = v[moving] / speed[moving, None]
        heading = self._heading[act]
        pts, z = plume_points(self.base, area, wind, cfg)                       # (f, k, 3), (f, k)
        visible_pt = z > self.occluder[:, None]                                  # above crowns or the shelf
        nf, k = z.shape
        # Ranges and bearings from every drone to every plume point.
        rel = pts[None, :, :, :] - p[:, None, None, :]                           # (d, f, k, 3)
        horiz = np.hypot(rel[..., 0], rel[..., 1])
        rng = np.linalg.norm(rel, axis=3)
        bearing = np.arctan2(rel[..., 1], rel[..., 0])
        elev = np.arctan2(rel[..., 2], np.maximum(horiz, 1e-6))
        hd = np.arctan2(heading[:, 1], heading[:, 0])[:, None, None]
        in_fov = np.abs(_wrap(bearing - hd)) <= math.radians(cfg.camera_hfov_deg / 2)
        cand = visible_pt[None] & in_fov & (rng <= cfg.visibility_m)
        if cand.any():
            di, fi, ki = np.nonzero(cand)
            los = self.terrain.line_of_sight(p[di], pts[fi, ki])
            seen = np.zeros_like(cand)
            seen[di[los], fi[los], ki[los]] = True
        else:
            seen = cand
        r50 = np.minimum(cfg.r50_ref_m * np.sqrt(area / cfg.ref_area_m2), cfg.visibility_m)
        el_sigma = math.radians(self.cfg.forecast.elevation_sigma_deg)
        for d_local, d in enumerate(act):
            for f in range(nf):
                if not seen[d_local, f].any():
                    continue
                key = (int(d), f)
                if t - self.last_sighting.get(key, -math.inf) < cfg.sighting_gap_s:
                    continue
                low = int(np.argmax(seen[d_local, f]))                           # lowest visible point
                top = k - 1 - int(np.argmax(seen[d_local, f][::-1]))             # highest visible point
                r = float(rng[d_local, f, low])
                hazard = math.log(2.0) * (r50[f] / max(r, 1.0)) ** 3
                if sim.rng_misc.random() >= 1.0 - math.exp(-hazard * elapsed):
                    continue
                self.last_sighting[key] = t
                b = float(bearing[d_local, f, low]) + math.radians(cfg.bearing_sigma_deg) * float(sim.rng_misc.normal())
                e = float(elev[d_local, f, top]) + el_sigma * float(sim.rng_misc.normal())
                e_low = float(elev[d_local, f, low]) + el_sigma * float(sim.rng_misc.normal())
                est = sim.obs.p_est[d] if sim.obs is not None else sim.p[d]
                self.sightings.append((t, int(d), float(est[0]), float(est[1]), float(est[2]), b, e, "camera", e_low, f))
                sim.event("smoke_sighted", int(d), bearing_deg=round(math.degrees(b) % 360.0, 1),
                          range_hint_m=int(round(r, -1)))
        # Particulate sensor: inside a plume, the source is upwind.
        conc = pm_concentration(p, self.base, area, wind, cfg)
        hit = conc >= cfg.pm_threshold_ugm3
        u = float(np.hypot(*wind))
        for d_local in np.nonzero(hit)[0]:
            d = int(act[d_local])
            key = (d, "pm")
            if t - self.last_sighting.get(key, -math.inf) < 5.0 or u < 0.5:
                continue
            self.last_sighting[key] = t
            self.contacts += 1
            upwind = math.atan2(-wind[1], -wind[0]) + math.radians(10.0) * float(sim.rng_misc.normal())
            est = sim.obs.p_est[d] if sim.obs is not None else sim.p[d]
            self.sightings.append((t, d, float(est[0]), float(est[1]), float(est[2]), upwind, 0.0, "particulate", 0.0, -1))
            sim.event("smoke_contact", d, pm25_ugm3=int(conc[d_local]), upwind_deg=round(math.degrees(upwind) % 360, 1))

    # -- the ground station, once a second ----------------------------------------------------------
    def _station(self, sim, area: np.ndarray) -> None:
        t = sim.t
        new = False
        # Thermal finds from the engine: position from the finding drone's navigation, size from the image.
        for j in np.nonzero(~np.isnan(sim.target_found_t) & ~self._thermal_seen)[0]:
            self._thermal_seen[j] = True
            by = int(sim.target_found_by[j])
            est = sim.obs.p_est[by] if sim.obs is not None else sim.p[by]
            err = float(np.linalg.norm(est[:2] - sim.p[by, :2]))
            x = float(self.fires[j].x + est[0] - sim.p[by, 0])
            y = float(self.fires[j].y + est[1] - sim.p[by, 1])
            seen_area = float(area[j]) * (1.0 - self.fires[j].cover) * float(np.exp(0.2 * sim.rng_misc.normal()))
            new |= self._locate(sim, x, y, err + 1.0, "thermal", seen_area)
        # New camera sightings: explained by a located fire (they size it), or kept for triangulation.
        wind = self._wind_estimate(sim)
        for i in range(self._next_sighting, len(self.sightings)):
            if self.sightings[i][7] == "camera" and not self._associate(self.sightings[i], wind):
                self._pool.append(i)
        self._next_sighting = len(self.sightings)
        if t >= self._next_triangulate:
            self._next_triangulate = t + 2.0
            self._pool = [i for i in self._pool if t - self.sightings[i][0] <= 60.0][-160:]
            if len(self._pool) >= 4:
                lines = np.array([[self.sightings[i][2], self.sightings[i][3], self.sightings[i][5], self.sightings[i][1]]
                                  for i in self._pool])
                for centre, _spread, _size in cluster(triangulate(lines[:, :3]), radius_m=40.0, min_points=4):
                    # A crossing cluster is only a candidate: two plumes' bearings cross at ghost points too.
                    # Keep it when bearings from several drones, from well-separated angles, agree on one spot.
                    fix = bearing_fix(lines, centre, self.cfg.smoke.bearing_sigma_deg)
                    if not fix_is_sound(fix):
                        continue
                    x, y = self._drift_back(sim, fix, [self._pool[i] for i in fix["used"]])
                    new |= self._locate(sim, x, y, max(fix["err_m"], 3.0), "smoke", None)
                self._pool = [i for i in self._pool if not self._associate(self.sightings[i], wind)]
        # Sizes from the latest plume tops (up to 30, from the last two minutes).
        for loc in self.located:
            tops = [h for tt, h, _src in loc["tops"][-30:] if t - tt <= 120.0]
            if len(tops) >= 3:
                est_area = area_from_top(float(np.median(tops)), self.cfg.smoke)
                if loc["method"] == "smoke" or est_area > loc["area_m2"]:
                    was = loc["area_m2"] >= SMOULDER_AREA_M2
                    loc["area_m2"] = est_area
                    if (est_area >= SMOULDER_AREA_M2) != was:
                        new = True              # a fire taking off (or found smaller) changes the forecast
        if new:
            last = self.forecasts[-1]["t"] if self.forecasts else -math.inf
            self.next_forecast_t = min(self.next_forecast_t, max(t, last + self.cfg.forecast.min_gap_s))
        if self.located and t >= self.next_forecast_t:
            self.next_forecast_t = t + self.cfg.forecast.every_s
            self._forecast(sim)
        # Approvals that are due (modelled approval).
        for a in self.alerts:
            if a["state"] == "checked" and a.get("approve_at") is not None and t >= a["approve_at"]:
                self.approve(sim, a["id"], by="incident commander (modelled)")

    def _wind_estimate(self, sim) -> np.ndarray:
        """The swarm's own wind estimate (what the station knows), or the true wind before it has one."""
        wind = np.asarray(sim._ambient_est[:2], dtype=float)
        return wind if np.any(wind) else np.asarray(self.wind_at(sim.t), dtype=float)

    def _drift_back(self, sim, fix: dict, used: list[int]) -> tuple[float, float]:
        """Move a smoke fix from the smoke the drones saw back to the fire under it.

        The bearings point at the lowest smoke each drone could see, which the wind has carried downwind of the
        fire by (wind speed) x (height) / (rise speed). The height comes from the elevation angles, the wind from
        the swarm's own estimate, the rise speed from the plume model.
        """
        ground = float(self.terrain.height(fix["x"], fix["y"]))
        heights = []
        for i in used:
            _t, _d, x, y, z, _b, _e, _k, e_low = self.sightings[i][:9]
            heights.append(z + math.hypot(fix["x"] - x, fix["y"] - y) * math.tan(e_low) - ground)
        h = float(np.clip(np.median(heights), 0.0, self.cfg.smoke.top_max_m)) if heights else 0.0
        shift = self._wind_estimate(sim) * h / self.cfg.smoke.rise_mps
        return fix["x"] - float(shift[0]), fix["y"] - float(shift[1])

    def _associate(self, s: tuple, wind: np.ndarray) -> bool:
        """Attach a camera sighting to the located fire whose plume its bearing points at.

        A drone may see only the upper part of a plume (the base out of view or behind a rise), and the wind
        bends the plume downwind, so the bearing is tested against the whole predicted centreline, from the
        fire up to half again its estimated top. A sighting explained by exactly one fire records the height
        of the plume top it saw, which sizes that fire; one explained by two fires in line sizes neither.
        """
        if not self.located:
            return False
        t, _d, x, y, z, b, e = s[:7]
        cfg = self.cfg.smoke
        sigma = math.radians(cfg.bearing_sigma_deg)
        frac = np.linspace(0.0, 1.5, 7)
        hits = []
        for loc in self.located:
            top = float(plume_top(max(loc["area_m2"], 1.0), cfg))
            px = loc["x"] + wind[0] * top * frac / cfg.rise_mps
            py = loc["y"] + wind[1] * top * frac / cfg.rise_mps
            r = np.maximum(np.hypot(px - x, py - y), 1.0)
            tol = 3.0 * sigma + np.arctan2(max(loc["err_m"], 25.0), r)
            if np.any(np.abs(_wrap(b - np.arctan2(py - y, px - x))) <= tol):
                hits.append((loc,))
        if not hits:
            return False
        if len(hits) == 1:
            # The top the drone saw is drifted downwind by its own height: solve for that height.
            loc = hits[0][0]
            g = float(self.terrain.height(loc["x"], loc["y"]))
            h = float(plume_top(max(loc["area_m2"], 1.0), cfg))
            for _ in range(4):
                r_top = math.hypot(loc["x"] + wind[0] * h / cfg.rise_mps - x, loc["y"] + wind[1] * h / cfg.rise_mps - y)
                h = min(max(z + r_top * math.tan(e) - g, 0.0), 2.0 * cfg.top_max_m)
            loc["tops"].append((t, h, s[9]))
        return True

    def _locate(self, sim, x: float, y: float, err: float, method: str, area_est: float | None) -> bool:
        """Add or refine a located fire; True when something new was learnt."""
        for loc in self.located:
            gap = math.hypot(loc["x"] - x, loc["y"] - y)
            # Two thermal finds are the same fire only when their positions agree; a smoke fix is coarser.
            same = gap <= (3.0 * (loc["err_m"] + err) + 5.0 if method == loc["method"] == "thermal" else 60.0)
            if same:
                if method == "thermal" and loc["method"] == "smoke":
                    loc.update(x=x, y=y, err_m=err, method="thermal", upgraded_t=round(sim.t, 2),
                               area_m2=max(loc["area_m2"], area_est or 0.0))
                    sim.event("fire_located", None, fire=loc["id"], x=round(x, 1), y=round(y, 1), err_m=round(err, 1),
                              by="thermal", upgraded=True)
                    return True
                return False
        truth = None
        if len(self.base):
            d = np.hypot(self.base[:, 0] - x, self.base[:, 1] - y)
            truth = int(np.argmin(d)) if d.min() <= MATCH_M else None
        truth_err = round(float(math.hypot(self.base[truth, 0] - x, self.base[truth, 1] - y)), 1) if truth is not None else None
        loc = {"id": len(self.located), "x": x, "y": y, "err_m": err, "method": method, "t": round(sim.t, 2),
               "area_m2": area_est if area_est is not None else 0.5 * SMOULDER_AREA_M2, "tops": [],
               "truth": truth, "first_method": method, "truth_err_m": truth_err}
        self.located.append(loc)
        sim.event("fire_located", None, fire=loc["id"], x=round(x, 1), y=round(y, 1), err_m=round(err, 1), by=method)
        return True

    # -- forecast -------------------------------------------------------------------------------------
    def _members(self, t0: float) -> list[list[Period]]:
        """Weather for each ensemble member: measured wind now, the weather forecast later, perturbed."""
        fc = self.cfg.forecast
        tw = np.asarray(self.wind_at(t0), dtype=float)
        sp = float(np.hypot(*tw)) + fc.measured_wind_sigma_mps * float(self.rng.normal())
        dr = math.atan2(tw[1], tw[0]) + math.radians(fc.measured_dir_sigma_deg) * float(self.rng.normal())
        measured = (max(sp, 0.0) * math.cos(dr), max(sp, 0.0) * math.sin(dr))
        later = [p for p in self.cfg.weather_forecast if p.start_s > t0]
        now = [p for p in self.cfg.weather_forecast if p.start_s <= t0]
        base = [Period(t0, measured, now[-1].moisture if now else Moisture())] + later
        out = []
        for _ in range(fc.members):
            ddir = math.radians(fc.wind_dir_sigma_deg) * float(self.rng.normal())
            dsp = float(np.exp(fc.wind_speed_sigma * self.rng.normal()))
            dm = fc.moisture_sigma * float(self.rng.normal())
            c, s = math.cos(ddir), math.sin(ddir)
            member = []
            for p in base:
                wx, wy = p.wind_mps
                m = p.moisture
                member.append(Period(p.start_s, ((wx * c - wy * s) * dsp, (wx * s + wy * c) * dsp),
                                     Moisture(max(0.02, m.d1 + dm), max(0.02, m.d10 + dm), max(0.02, m.d100 + dm), m.live)))
            out.append(member)
        return out

    def _seeds(self, loc: dict, jx: float, jy: float, t0: float) -> list[tuple]:
        """Ignition points for a located fire: its centre and, once it is larger than a cell, a ring at its radius."""
        r = math.sqrt(max(loc["area_m2"], 1.0) / math.pi)
        cx, cy = loc["x"] + jx, loc["y"] + jy
        pts = [(cx, cy, t0)]
        if r > self.grid.cell_m:
            pts += [(cx + r * math.cos(a), cy + r * math.sin(a), t0) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
        return pts

    def run_forecast(self, t0: float, located: list[dict]) -> dict:
        """Ensemble arrival times from the located fires as the station knows them at t0.

        ``active``: the fires the station believes are spreading. ``potential``: every located fire, the
        smouldering ones as if they took off now (what a warning rests on).
        """
        fc = self.cfg.forecast
        horizon = t0 + fc.horizon_s
        members = self._members(t0)
        spreading = [l["area_m2"] >= SMOULDER_AREA_M2 for l in located]
        act, pot = [], []
        for weather in members:
            scale = float(np.exp(fc.ros_sigma * self.rng.normal() - 0.5 * fc.ros_sigma ** 2))
            seeds_act, seeds_all = [], []
            for loc, on in zip(located, spreading):
                jx, jy = self.rng.normal(0.0, loc["err_m"], 2)
                pts = self._seeds(loc, float(jx), float(jy), t0)
                seeds_all += pts
                if on:
                    seeds_act += pts
            a = self.grid.arrival(seeds_act, weather, horizon, scale=scale, cache=False) if seeds_act \
                else np.full(self.grid.z.shape, np.inf)
            act.append(a)
            pot.append(a if all(spreading) else self.grid.arrival(seeds_all, weather, horizon, scale=scale, cache=False))
        return {"t": t0, "active": self._stats(np.array(act), t0), "potential": self._stats(np.array(pot), t0),
                "located": [{k: v for k, v in l.items() if k != "tops"} for l in located]}

    def _stats(self, stack: np.ndarray, t0: float) -> dict:
        finite = np.where(np.isfinite(stack), stack, 1e12)
        p50 = np.median(finite, axis=0)
        p50 = np.where(p50 >= 1e11, np.inf, p50)
        probs = {m: (stack <= t0 + 60 * m).mean(axis=0) for m in (30, 60, 120, 180)}
        phone_arr = np.array([[float(a[c].min()) if len(c[0]) else math.inf for c in self._phone_cells] for a in stack]) \
            if len(self.phones) else np.zeros((len(stack), 0))
        return {"p50": p50, "probs": probs, "phone_arrivals": phone_arr}

    def _forecast(self, sim) -> None:
        t0 = sim.t
        fc = self.run_forecast(t0, self.located)
        fc["id"] = len(self.forecasts)
        self.forecasts.append(fc)
        brief = self._brief(fc)
        fc["brief"] = brief
        g = self.grid
        act, pot = fc["active"], fc["potential"]
        sim.event("forecast", None, forecast=fc["id"],
                  grid={"x0": g.x0, "y0": g.y0, "cell_m": g.cell_m, "nx": g.nx, "ny": g.ny},
                  p50_arrival_s=[None if not math.isfinite(v) else int(round(float(v) - t0)) for v in act["p50"].ravel()],
                  prob_reach_60min=[round(float(v), 2) for v in act["probs"][60].ravel()],
                  potential_prob_180min=[round(float(v), 2) for v in pot["probs"][180].ravel()],
                  isochrones_min=[30, 60, 120, 180], brief=brief)
        self._draft_alerts(sim, fc)

    def _brief(self, fc: dict) -> list[dict]:
        """Per located fire: speed, size in one and two hours, direction, places reached and when."""
        t0 = fc["t"]
        g = self.grid
        locs = fc["located"]
        if not locs:
            return []
        lx = np.array([l["x"] for l in locs])
        ly = np.array([l["y"] for l in locs])
        owner = np.argmin((g.X[..., None] - lx) ** 2 + (g.Y[..., None] - ly) ** 2, axis=2)
        out = []
        for i, l in enumerate(locs):
            state = "smouldering" if l["area_m2"] < SMOULDER_AREA_M2 else "spreading"
            p50 = (fc["potential"] if state == "smouldering" else fc["active"])["p50"]
            mine = owner == i
            grow = {h: float(np.count_nonzero(mine & (p50 <= t0 + 60 * h))) * g.cell_m ** 2 / 1e4 for h in (60, 120)}
            reach = mine & (p50 <= t0 + 3600)
            if reach.any():
                far = np.hypot(g.X[reach] - l["x"], g.Y[reach] - l["y"])
                k = int(np.argmax(far))
                head_m_min = float(far[k]) / 60.0
                direction = compass(float(g.X[reach][k] - l["x"]), float(g.Y[reach][k] - l["y"]))
            else:
                head_m_min, direction = 0.0, "not spreading"
            places = []
            for pl in self.cfg.places:
                iy, ix = g.cell_of(pl["x"], pl["y"])
                if owner[iy, ix] == i and p50[iy, ix] <= t0 + self.cfg.forecast.horizon_s:
                    places.append({"name": pl["name"], "eta_min": int(round((float(p50[iy, ix]) - t0) / 60.0))})
            places.sort(key=lambda p: p["eta_min"])
            text = (f"Fire {l['id'] + 1} ({l['method']} find, {state}): "
                    + ("if it takes off, " if state == "smouldering" else "")
                    + f"head runs about {head_m_min:.1f} m/min {direction}"
                    + f"; about {grow[60]:.1f} ha in 1 h and {grow[120]:.1f} ha in 2 h"
                    + ("; reaches " + ", ".join(f"{p['name']} in about {p['eta_min']} min" for p in places) if places else "")
                    + ".")
            out.append({"fire": l["id"], "method": l["method"], "state": state, "head_m_per_min": round(head_m_min, 2),
                        "direction": direction, "area_1h_ha": round(grow[60], 2), "area_2h_ha": round(grow[120], 2),
                        "places": places, "text": text})
        return out

    # -- alerts ------------------------------------------------------------------------------------------
    def _draft_alerts(self, sim, fc: dict) -> None:
        pol = self.cfg.alerts
        if not len(self.phones):
            return
        t0 = fc["t"]
        act, pot = fc["active"], fc["potential"]
        p_evac = (act["phone_arrivals"] <= t0 + pol.evacuate_within_s).mean(axis=0)
        p_warn = (pot["phone_arrivals"] <= t0 + pol.warn_within_s).mean(axis=0)
        locs = fc["located"]
        if pol.rule == "distance":
            lx = np.array([l["x"] for l in locs])
            ly = np.array([l["y"] for l in locs])
            dist = np.sqrt((self.phone_xy[:, 0, None] - lx[None]) ** 2 + (self.phone_xy[:, 1, None] - ly[None]) ** 2)
            spreading = np.array([l["area_m2"] >= SMOULDER_AREA_M2 for l in locs])
            evac = np.where(spreading[None, :], dist, np.inf).min(axis=1) <= pol.distance_evacuate_m
            warn = (dist.min(axis=1) <= pol.distance_warn_m) & ~evac
        else:
            evac = p_evac >= pol.prob_threshold
            warn = (p_warn >= pol.prob_threshold) & ~evac
        for level, want, code in (("evacuate", evac, 2), ("warning", warn, 1)):
            new = want & (self.phone_level < code)
            if not new.any():
                continue
            src = act if level == "evacuate" else pot
            cells = np.argwhere(src["probs"][60 if level == "evacuate" else 180] >= pol.prob_threshold)
            pts = [self.phone_xy[new]]
            if len(cells) and pol.rule == "forecast":
                pts.append(np.stack([self.grid.X[cells[:, 0], cells[:, 1]], self.grid.Y[cells[:, 0], cells[:, 1]]], axis=1))
            zone = buffered_zone(np.concatenate(pts), pol.zone_buffer_m)
            phones = [int(i) for i in np.nonzero(inside(zone, self.phone_xy) & (self.phone_level < code))[0]]
            place = self._nearest_place(self.phone_xy[new].mean(axis=0))
            arr = src["phone_arrivals"][:, new]
            med = np.median(np.where(np.isfinite(arr), arr, 1e12), axis=0)
            eta = float(np.min(med)) - t0
            action = ("Leave now by the valley road, away from the smoke." if level == "evacuate"
                      else "Be ready to leave. Pack essentials and watch for an evacuation order.")
            alert = {
                "id": len(self.alerts), "t": round(t0, 2), "level": level, "forecast_id": fc["id"], "rule": pol.rule,
                "hazard": "wildfire", "area": f"{place} and around", "action": action,
                "authority": pol.authority, "expires_in_s": 6 * 3600,
                "expires": "in 6 hours", "zone": [[round(float(a), 1), round(float(b), 1)] for a, b in zone],
                "phones": phones, "max_prob": float(np.max((p_evac if level == "evacuate" else p_warn)[new])),
                "eta_min": None if eta > 1e10 else int(round(eta / 60.0)),
            }
            alert["message"] = (f"{pol.authority}: {'EVACUATE NOW' if level == 'evacuate' else 'WILDFIRE WARNING'}. "
                                f"Fire near {alert['area']}"
                                + (f", expected within about {alert['eta_min']} min" if alert["eta_min"] is not None else "")
                                + f". {action} Alert expires {alert['expires']}.")
            result = self.check.check(alert, fc, [a for a in self.alerts if a["state"] != "rejected"])
            alert["check"] = result.to_dict()
            alert["state"] = "checked" if result.ok else "rejected"
            self.alerts.append(alert)
            sim.event("alert_drafted", None, alert=alert["id"], level=level, phones=len(phones), zone=alert["zone"],
                      message=alert["message"])
            sim.event("alert_check", None, alert=alert["id"], **result.to_dict())
            if result.ok:
                self.phone_level[phones] = np.maximum(self.phone_level[phones], code)
                if pol.approval == "modelled":
                    alert["approve_at"] = t0 + float(pol.approval_median_s * np.exp(pol.approval_sigma * self.rng.normal()))

    def _nearest_place(self, xy) -> str:
        if not self.cfg.places:
            return "the valley"
        d = [math.hypot(p["x"] - xy[0], p["y"] - xy[1]) for p in self.cfg.places]
        return self.cfg.places[int(np.argmin(d))]["name"]

    def approve(self, sim, alert_id: int, by: str = "incident commander") -> dict:
        """A person approves a checked alert; it then goes to the phones in its zone (simulated)."""
        if not 0 <= alert_id < len(self.alerts):
            return {"ok": False, "reason": "no such alert"}
        a = self.alerts[alert_id]
        if a["state"] != "checked":
            return {"ok": False, "reason": f"alert is {a['state']}"}
        t = a["approve_at"] if a.get("approve_at") is not None and by.endswith("(modelled)") else sim.t
        a["state"] = "approved"
        a["approved_t"] = round(t, 2)
        a["approved_by"] = by
        lo, hi = self.cfg.alerts.delivery_s
        code = 2 if a["level"] == "evacuate" else 1
        delivered = 0
        for ph in a["phones"]:
            if self.unreachable[ph]:
                continue
            lag = float(self.rng.uniform(lo, hi))
            self.phone_delivered.setdefault(ph, []).append((code, t + lag, a["t"] + lag))
            delivered += 1
        a["delivered"] = delivered
        sim.event("alert_approved", None, alert=alert_id, by=by)
        sim.event("alert_delivered", None, alert=alert_id, phones=delivered, of=len(a["phones"]))
        a["state"] = "delivered"
        return {"ok": True, "delivered": delivered}

    def finish(self, sim) -> None:
        """At the end of a flight, alerts still waiting for their modelled approval are approved when due."""
        for a in self.alerts:
            if a["state"] == "checked" and a.get("approve_at") is not None:
                self.approve(sim, a["id"], by="incident commander (modelled)")

    # -- evaluation against the truth ------------------------------------------------------------------
    def evaluate(self, sim) -> dict:
        """Locating, forecasting and warning scores for one flight (keys described in docs/WILDFIRE.md)."""
        out: dict = {}
        nf = len(self.fires)
        first = {}
        for loc in self.located:
            if loc["truth"] is not None and loc["truth"] not in first:
                first[loc["truth"]] = loc
        fires = []
        for i, f in enumerate(self.fires):
            loc = first.get(i)
            fires.append({"shelter": f.shelter, "active_at_start": f.t_active_s <= 0.0,
                          "located": loc is not None, "t": loc["t"] if loc else None,
                          "by": loc["first_method"] if loc else None, "err_m": loc["truth_err_m"] if loc else None})
        out["fires_total"] = nf
        out["fires_located"] = len(first)
        out["fires_located_pct"] = round(100.0 * len(first) / nf, 1) if nf else None
        out["fires_located_by_smoke"] = sum(1 for f in fires if f["by"] == "smoke")
        out["fires_located_by_thermal"] = sum(1 for f in fires if f["by"] == "thermal")
        out["false_locations"] = sum(1 for l in self.located if l["truth"] is None)
        out["all_located_s"] = max(f["t"] for f in fires) if fires and all(f["located"] for f in fires) else None
        smoke_err = [f["err_m"] for f in fires if f["by"] == "smoke"]
        out["smoke_location_error_m"] = round(float(np.median(smoke_err)), 1) if smoke_err else None
        out["smoke_sightings"] = sum(1 for s in self.sightings if s[7] == "camera")
        out["smoke_contacts"] = self.contacts
        out["fire_table"] = fires
        out["forecasts"] = len(self.forecasts)
        out.update(self._forecast_skill())
        out.update(self._warning_scores())
        return out

    def _forecast_skill(self) -> dict:
        """The flight's last forecast against where the fires really burn in the next 1 and 3 hours."""
        keys = ("sorensen_1h", "sorensen_3h", "sorensen_3h_all_fires", "phone_arrival_mae_min",
                "phone_arrival_bias_min", "phone_arrival_n", "brier_60min")
        if not self.forecasts:
            return {k: None for k in keys}
        fc = self.forecasts[-1]
        t0 = fc["t"]
        p50 = fc["active"]["p50"]
        spreading = {l["truth"] for l in fc["located"] if l["truth"] is not None and l["area_m2"] >= SMOULDER_AREA_M2}
        known = [self.fire_arrival[i] for i in sorted(spreading) if self.fires[i].t_active_s <= t0]
        truth_known = np.min(known, axis=0) if known else np.full(p50.shape, np.inf)
        out = {}
        for h, key in ((1, "sorensen_1h"), (3, "sorensen_3h")):
            # Only ground that burns after the forecast is made counts; ground already burnt would flatter it.
            fmask = (p50 > t0) & (p50 <= t0 + 3600 * h)
            tmask = (truth_known > t0) & (truth_known <= t0 + 3600 * h)
            v = sorensen(fmask, tmask)
            out[key] = None if v is None else round(v, 3)
        tall = (self.arrival10 > t0) & (self.arrival10 <= t0 + 3 * 3600)
        v = sorensen((p50 > t0) & (p50 <= t0 + 3 * 3600), tall)
        out["sorensen_3h_all_fires"] = None if v is None else round(v, 3)
        # Phones: forecast median arrival against the true arrival, where both fall inside the 3 h window.
        if len(self.phones):
            arr = fc["active"]["phone_arrivals"]
            med = np.median(np.where(np.isfinite(arr), arr, 1e12), axis=0)
            truth = self.phone_truth_s
            both = (truth > t0) & (truth <= t0 + 3 * 3600) & (med < 1e11)
            err = (med[both] - truth[both]) / 60.0
            out["phone_arrival_mae_min"] = round(float(np.mean(np.abs(err))), 1) if len(err) else None
            out["phone_arrival_bias_min"] = round(float(np.mean(err)), 1) if len(err) else None
            out["phone_arrival_n"] = int(len(err))
            prob = (arr <= t0 + 3600).mean(axis=0)
            hit = (truth > t0) & (truth <= t0 + 3600)
            out["brier_60min"] = round(float(np.mean((prob - hit) ** 2)), 4)
        else:
            out.update(phone_arrival_mae_min=None, phone_arrival_bias_min=None, phone_arrival_n=0, brier_60min=None)
        return out

    def _warning_scores(self) -> dict:
        """Who was warned before the fire reached them, how early, and who was told to leave for nothing."""
        horizon = 3 * 3600.0
        truth = self.phone_truth_s
        reached = (truth > 0.0) & (truth <= horizon)
        first_any = np.full(len(self.phones), np.inf)
        first_evac = np.full(len(self.phones), np.inf)
        first_any_auto = np.full(len(self.phones), np.inf)
        for ph, deliveries in self.phone_delivered.items():
            for code, td, td_auto in deliveries:
                first_any[ph] = min(first_any[ph], td)
                first_any_auto[ph] = min(first_any_auto[ph], td_auto)
                if code == 2:
                    first_evac[ph] = min(first_evac[ph], td)
        in_time = reached & (first_any < truth)
        lead = (truth[in_time] - first_any[in_time]) / 60.0
        lead_auto = (truth[in_time] - first_any_auto[in_time]) / 60.0
        evac_in_time = reached & (first_evac < truth)
        evac_lead = (truth[evac_in_time] - first_evac[evac_in_time]) / 60.0
        evacuated = np.isfinite(first_evac)
        warned = np.isfinite(first_any)
        delivered = [a for a in self.alerts if a["state"] == "delivered"]
        return {
            "phones_total": int(len(self.phones)),
            "phones_reached_3h": int(reached.sum()),
            "phones_warned_in_time": int(in_time.sum()),
            "phones_missed": int((reached & ~in_time & ~self.unreachable).sum()),
            "phones_missed_no_signal": int((reached & ~in_time & self.unreachable).sum()),
            "lead_time_min_median": round(float(np.median(lead)), 1) if len(lead) else None,
            "lead_time_min_p10": round(float(np.percentile(lead, 10)), 1) if len(lead) else None,
            "lead_time_auto_min_median": round(float(np.median(lead_auto)), 1) if len(lead_auto) else None,
            "evac_lead_time_min_median": round(float(np.median(evac_lead)), 1) if len(evac_lead) else None,
            "phones_evacuated": int(evacuated.sum()),
            "evac_not_reached_3h": int((evacuated & ~reached).sum()),
            "phones_warned": int(warned.sum()),
            "warned_not_reached_3h": int((warned & ~reached).sum()),
            "alerts_drafted": len(self.alerts),
            "alerts_rejected": sum(1 for a in self.alerts if a["state"] == "rejected"),
            "alerts_delivered": len(delivered),
            "first_alert_s": round(min(float(first_any.min()), 1e12), 1) if np.isfinite(first_any).any() else None,
        }

    # -- dashboard ------------------------------------------------------------------------------------
    def hello(self) -> dict:
        """What the page needs once: the true arrival-time grid, so it can draw the fire growing."""
        g = self.truth10
        return {"fire_truth": {"x0": g.x0, "y0": g.y0, "cell_m": g.cell_m, "nx": g.nx, "ny": g.ny,
                               "arrival_s": [None if not math.isfinite(v) else int(round(float(v)))
                                             for v in self.arrival10.ravel()]},
                "phones_unreachable": [int(i) for i in np.nonzero(self.unreachable)[0]]}

    def frame(self, sim) -> dict:
        t = sim.t
        area = self.area(t)
        level = np.zeros(len(self.phones), dtype=int)
        for ph, deliveries in self.phone_delivered.items():
            for code, td, _ in deliveries:
                if td <= t:
                    level[ph] = max(level[ph], code)
        return {
            "fires_state": [{"id": i, "area_m2": round(float(a), 1),
                             "smoke_top_m": round(float(plume_top(a, self.cfg.smoke)), 1),
                             "active": bool(t >= f.t_active_s)} for i, (f, a) in enumerate(zip(self.fires, area))],
            "located": [{"id": l["id"], "x": round(l["x"], 1), "y": round(l["y"], 1), "err_m": round(l["err_m"], 1),
                         "by": l["method"], "area_m2": round(l["area_m2"], 1)} for l in self.located],
            "sightings": [{"drone": s[1], "bearing_deg": round(math.degrees(s[5]) % 360, 1), "t": round(s[0], 2),
                           "kind": s[7]} for s in self.sightings if t - s[0] <= 5.0][-40:],
            "alerts": [{"id": a["id"], "state": a["state"], "level": a["level"], "phones": len(a["phones"])}
                       for a in self.alerts],
            "phones_alerted": level.tolist(),
            "forecast_id": self.forecasts[-1]["id"] if self.forecasts else None,
        }
