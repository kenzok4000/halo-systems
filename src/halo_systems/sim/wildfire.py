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

1. **Locate and map.** A thermal find gives a fire's position to within the navigation error, and each
   drone's thermal camera maps the burning ground it flies over (``_map_thermal``), so a spreading fire's
   shape and size are known where the swarm has been. Smoke sightings give bearings: a sighting that points
   along the plume of exactly one located fire, and could not be any other's, sizes that fire from the
   height of the smoke it saw; the rest are triangulated (``smoke.triangulate``), and a cluster of
   crossings becomes a fire only when a least-squares fix from several drones holds up without any one of
   them (``smoke.fix_is_sound``), which rejects the ghost crossings of two plumes' bearings. The fix is
   then moved upwind by the plume's drift, using the swarm's own wind estimate. A smoke location is
   upgraded when a thermal find lands near it.
2. **Forecast.** From the mapped burning ground and the located fires, an ensemble of spread runs
   (perturbed wind, fuel moisture, spread rate and position) gives, per map cell, the median arrival time and the probability the
   fire arrives within 30, 60, 120 and 180 minutes. It is run twice: for the fires the station believes
   are spreading, and for every located fire as if the smouldering ones took off now. The weather inputs
   are a modelled measurement of the wind now (the true wind with noise, ``measured_wind_sigma_mps`` and
   ``measured_dir_sigma_deg``) and a weather forecast for later, which is not the true weather, and the
   station never sees the patchy spread-rate multiplier the truth burns with.
3. **Brief and tell the crews.** Per fire: how it was found, which way its head runs (a compass bearing
   from the ensemble, with a cone for its uncertainty) and why (the measured wind, the slope, the fuel
   ahead and its dryness, with Rothermel's wind and slope factors naming the main driver), how fast, how big
   it will be in one and two hours, and which places it reaches and when. A fire's brief is relayed to the
   fire crews (simulated devices) when it is first located, and again when it changes enough to act on.
4. **Alert.** The valley is divided into fixed evacuation zones. After every forecast, fixed rules give each
   zone a level in the spirit of Japan's five-level evacuation scale: Level 3 (prepare; those who need longer
   should leave) when the forecast gives at least ``prob_threshold`` of fire reaching a phone in the zone
   within 3 hours, counting smouldering fires as if they took off; Level 4 (everyone leave now) within 60
   minutes from the spreading fires; Level 5 (fire is here; get to safety) at even odds within 15 minutes.
   A zone's level only rises automatically. Each alert passes ``AlertCheck`` and then goes out at once, by
   cell broadcast to every phone in the zone (simulated phones only). Sending at once by fixed rules follows
   the idea of Japan's earthquake early warning, which is fully automatic; Japan's Levels 3 to 5
   are decided by mayors (docs/WILDFIRE.md). An operator can cancel an alert or give a zone the
   all-clear; a manual approval mode is kept for drills. Under the pre-signed rules (``AlertPolicy.rule`` of
   "presigned") public alerts go out only under trigger rules the alerting authority signed in advance, for
   fires believed spreading, and each located fire also goes to fire dispatch as a card; with
   ``smoulder_alerts`` the Level 3 rule counts smoulders too, as above, so a smoulder near homes brings a public
   "prepare" as well as its card.

Nothing here calls a language model; the forecast and the alert rules are deterministic given the seed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .detection import today_methods
from .fire import FUEL_MODELS, Moisture, Period, SpreadGrid, wind_slope_factors
from .smoke import (SmokeConfig, bearing_fix, cluster, fix_is_sound, plume_points, plume_top, pm_concentration,
                    triangulate)
from .terrain import FOREST, Terrain, smooth_noise

SMOULDER_AREA_M2 = 50.0      # the station treats a fire estimated smaller than this as smouldering, not spreading
MATCH_M = 60.0               # a located fire within this of a true fire's burning ground counts as finding it (scoring only)
CARD_MOVE_M = 30.0           # presigned: a fix that moves further than this (or twice its error) gets a new card to dispatch
MAP_JOIN_M = 60.0            # mapped burning ground within this of a located fire belongs to it
EVENT_GAP_S = 10.0           # smoke_sighted / smoke_contact events per drone at most this often (sightings are all kept)


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
    size_top_sigma_max_m: float = 12.0    # a plume top known worse than this (seen from too far) does not size a fire
    size_top_cap: float = 0.9             # tops above this share of the highest plume give only a lower bound on area
    cone_share: float = 0.8               # a brief's direction cone holds this share of the members' head bearings ...
    cone_extra_deg: float = 10.0          # ... widened, in quadrature, for errors the members do not represent (set on dev seeds)


@dataclass
class CrewConfig:
    """When the station relays a fire's brief to the fire crews (simulated devices: nothing is sent anywhere).

    A fire's brief goes out when the fire is first located, and again when it changes enough to act on, against
    what the crews were last told.
    """
    turn_deg: float = 30.0                # the head's bearing moved at least this much, and out of the cone last sent
    speed_ratio: float = 1.5              # the head's speed rose or fell by at least this factor ...
    speed_min_m_min: float = 0.5          # ... and by at least this much
    eta_earlier_min: float = 15.0         # a place on the list is now reached at least this much sooner, by the clock
    channel: str = "crew tablets and radio (simulated)"


@dataclass
class MappingConfig:
    fov_deg: float = 45.0                 # the scout's thermal camera looking down, as for the hotspot finder
    forest_seen: float = 0.4              # chance one look through tree crowns sees the fire burning under them
    every_s: float = 1.0


LEVELS = {
    3: ("Level 3", "PREPARE TO LEAVE",
        "Older people, families with children and anyone who needs longer: leave now. Everyone else: get ready."),
    4: ("Level 4", "EVACUATE NOW", "Everyone: leave now by the valley road, away from the smoke."),
    5: ("Level 5", "EMERGENCY", "Fire is here. If you cannot get out safely, shelter in a cleared area or a solid building."),
}


@dataclass
class AlertPolicy:
    # "forecast"; "presigned" (public alerts only for fires believed spreading, each under a trigger rule the
    # alerting authority signed in advance; a newly located fire goes to fire dispatch as a card); or "distance"
    # (the ablation: alert by distance alone)
    rule: str = "forecast"
    # "presigned" only: the Level 3 rule (T3) also counts each located smoulder as if it took off now, as today's
    # rule does, so a smoulder near homes brings a public "prepare" as well as its card to fire dispatch
    smoulder_alerts: bool = False
    prob_threshold: float = 0.3
    evacuate_within_s: float = 3600.0      # Level 4
    warn_within_s: float = 3 * 3600.0      # Level 3
    emergency_within_s: float = 900.0      # Level 5, at even odds
    emergency_prob: float = 0.5
    danger_radius_m: float = 50.0     # fire this close to a phone counts as reaching it
    distance_evacuate_m: float = 800.0
    distance_warn_m: float = 1600.0
    distance_emergency_m: float = 150.0
    approval: str = "auto"            # "auto" (sent once checked), "manual" (a tap), or "modelled" (a modelled delay)
    hold_after_cancel_s: float = 1800.0    # after a cancel or all-clear the rules re-alert that zone only above
                                           # the level it had (after a cancel, at Level 5 at least)
    approval_median_s: float = 90.0
    approval_sigma: float = 0.5
    delivery_s: tuple[float, float] = (5.0, 30.0)
    unreachable_share: float = 0.05
    max_recipients: int = 5000
    max_zone_ha: float = 5000.0
    max_chars: int = 360              # Wireless Emergency Alerts and EU-Alert carry up to 360 characters
    authority: str = "Valley Fire District"
    channel: str = "cell broadcast (simulated)"
    dispatch_channel: str = "fire dispatch, initial attack (simulated)"

    def trigger(self, level: int) -> dict:
        """The pre-signed trigger rule behind an automatic alert at this level ("presigned" only)."""
        if level == 5:
            text = (f"a fire believed spreading has at least a {self.emergency_prob:.0%} chance of reaching a phone "
                    f"in the zone within {self.emergency_within_s / 60:.0f} minutes")
        else:
            within = self.evacuate_within_s if level == 4 else self.warn_within_s
            which = ("a located fire, spreading or smouldering (counted as if it took off now),"
                     if level == 3 and self.smoulder_alerts else "a fire believed spreading")
            text = (f"{which} has at least a {self.prob_threshold:.0%} chance of reaching a phone "
                    f"in the zone within {within / 3600:.0f} hour{'s' if within != 3600 else ''}")
        return {"rule": f"T{level}", "text": text, "signed_by": self.authority, "signed": "in advance (simulated)"}


@dataclass
class WildfireConfig:
    fires: list[FireSpec]
    weather: list[Period]             # the true weather
    weather_forecast: list[Period]    # what the station is told
    phones: list[Phone] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)
    overhangs: list[dict] = field(default_factory=list)
    zones: list[dict] = field(default_factory=list)     # evacuation zones: {code, name, polygon [[x, y], ...]}
    smoke: SmokeConfig = field(default_factory=SmokeConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    alerts: AlertPolicy = field(default_factory=AlertPolicy)
    mapping: MappingConfig = field(default_factory=MappingConfig)
    crew: CrewConfig = field(default_factory=CrewConfig)
    truth_cell_m: float = 5.0
    horizon_s: float = 4 * 3600.0     # truth is computed this far; forecasts made during the flight look 3 h ahead
    heterogeneity_sigma: float = 0.3
    # "legacy": a thermal find within 60 m (plus the fire's radius) of a fire located by smoke moves that fix to it.
    # "tight": only within 3 x (the two fixes' errors) + 5 m, never wider than legacy; see Wildfire._locate.
    station: str = "legacy"
    report_v2: bool = False           # extra scoring keys (own evidence, camera time, miss class); scoring only

    def summary(self, terrain: Terrain) -> dict:
        return {
            "fires": [f.to_dict(float(terrain.height(f.x, f.y)), i) for i, f in enumerate(self.fires)],
            "phones": [p.to_dict() for p in self.phones],
            "places": self.places,
            "overhangs": self.overhangs,
            "zones": self.zones,
            "smoke_model": {"rise_mps": self.smoke.rise_mps, "top_ref_m": self.smoke.top_ref_m,
                            "ref_area_m2": self.smoke.ref_area_m2, "top_max_m": self.smoke.top_max_m},
            "alert_rule": self.alerts.rule,
            **({"smoulder_alerts": True} if self.alerts.smoulder_alerts else {}),
            **({"station": self.station} if self.station != "legacy" else {}),
            **({"report_v2": True} if self.report_v2 else {}),
        }


def grid_zones(x_edges, y_edges, row_names, places=()) -> list[dict]:
    """Fixed evacuation zones on a grid of rectangles, coded by row letter and column number (west to east)."""
    zones = []
    for r, (y0, y1) in enumerate(zip(y_edges, y_edges[1:])):
        letter, label = row_names[r]
        for c, (x0, x1) in enumerate(zip(x_edges, x_edges[1:])):
            inside_places = [p["name"] for p in places if x0 <= p["x"] < x1 and y0 <= p["y"] < y1]
            name = f"{label} {c + 1}" + (f" ({', '.join(inside_places)})" if inside_places else "")
            zones.append({"code": f"{letter}{c + 1}", "name": name,
                          "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]})
    return zones


def compass(dx: float, dy: float) -> str:
    """Direction a vector points, as a 16-point compass word (x east, y north)."""
    names = ["east", "east-northeast", "northeast", "north-northeast", "north", "north-northwest", "northwest",
             "west-northwest", "west", "west-southwest", "southwest", "south-southwest", "south", "south-southeast",
             "southeast", "east-southeast"]
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    return names[int(round(ang / 22.5)) % 16]


def bearing_deg(dx: float, dy: float) -> float:
    """Compass bearing of a vector, in degrees clockwise from north (x east, y north), in [0, 360)."""
    b = math.degrees(math.atan2(dx, dy)) % 360.0
    return b if b < 360.0 else 0.0


def compass_of(bearing: float) -> str:
    """A compass bearing in degrees as a 16-point compass word."""
    return compass(math.sin(math.radians(bearing)), math.cos(math.radians(bearing)))


def angle_diff(a, b):
    """Smallest absolute difference between bearings, in degrees (0 to 180); works on arrays too."""
    return np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)


def _components(mask: np.ndarray) -> np.ndarray:
    """Labels of the 8-connected patches of ``mask`` (-1 outside it), by repeated neighbour minima."""
    lab = np.full(mask.shape, -1, dtype=int)
    if not mask.any():
        return lab
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    m = mask[r0:r1, c0:c1]
    big = m.size
    cur = np.where(m, np.arange(big).reshape(m.shape), big)
    while True:
        pad = np.pad(cur, 1, constant_values=big)
        nxt = cur
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                nxt = np.minimum(nxt, pad[dy:dy + m.shape[0], dx:dx + m.shape[1]])
        nxt = np.where(m, nxt, big)
        if np.array_equal(nxt, cur):
            break
        cur = nxt
    lab[r0:r1, c0:c1] = np.where(m, cur, -1)
    return lab


def front_owner(X: np.ndarray, Y: np.ndarray, arrival: np.ndarray, t0: float,
                fires: list[tuple[float, float, float] | None],
                cells: np.ndarray, join_m: float = 60.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Which fire each cell's ground is reached from, on one arrival-time grid, and how far beyond its front.

    Ground burning within a minute of ``t0`` is the fires' ground now; each cell of it belongs to the fire
    (x, y, radius) nearest it (distance less the radius), among those burning on this grid (None: a fire not
    burning on it). A patch of burning ground no fire comes within ``join_m`` of (a hot spot no located fire
    explains) belongs to none. Each other cell in ``cells`` belongs to whoever owns the nearest ground burning now,
    at that distance: the front reaches a cell from the ground nearest it, so a fire's spread is not handed to a
    neighbour merely because the neighbour's centre is nearer. Returns the owner (an index into ``fires``; -1
    where none), the distance in metres, and the (x, y) of that nearest burning cell.
    """
    owner = np.full(X.shape, -1, dtype=int)
    dist = np.zeros(X.shape)
    ox = np.full(X.shape, np.nan)
    oy = np.full(X.shape, np.nan)
    now = arrival <= t0 + 60.0
    idx = np.array([i for i, f in enumerate(fires) if f is not None], dtype=int)
    if not now.any() or not len(idx):
        return owner, dist, ox, oy
    fx, fy, fr = (np.array([fires[i][k] for i in idx], dtype=float) for k in range(3))
    gap = np.hypot(X[now][:, None] - fx, Y[now][:, None] - fy) - fr
    near = np.argmin(gap, axis=1)
    lab = np.where(gap[np.arange(len(near)), near] <= join_m, idx[near], -1)
    # A patch is kept when any of its cells is within join_m of a fire; its cells keep their nearest fire.
    comp = _components(now)[now]
    held = np.zeros(comp.max() + 1, dtype=bool)
    held[comp[lab >= 0]] = True
    owner[now] = np.where(held[comp], idx[near], -1)
    ox[now], oy[now] = X[now], Y[now]
    # The nearest burning cell to any cell outside lies on the burning ground's edge.
    pad = np.pad(now, 1)
    edge = now & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
    ex, ey, el = X[edge], Y[edge], owner[edge]
    out = cells & ~now
    cx, cy = X[out], Y[out]
    k = np.empty(len(cx), dtype=int)
    for a in range(0, len(cx), 2048):
        d2 = (cx[a:a + 2048, None] - ex) ** 2 + (cy[a:a + 2048, None] - ey) ** 2
        k[a:a + 2048] = np.argmin(d2, axis=1)
    owner[out], ox[out], oy[out] = el[k], ex[k], ey[k]
    dist[out] = np.hypot(cx - ex[k], cy - ey[k])
    return owner, dist, ox, oy


HEAD_GROUP_DEG = 60.0                     # near-furthest cells this close in direction are one head (see _furthest)


def _furthest(X: np.ndarray, Y: np.ndarray, owned: tuple, mask: np.ndarray):
    """The cells of ``mask`` within half a cell of the greatest distance beyond the front, as one point.

    On a grid, the cell furthest from the front is often one of a mirror pair either side of the true head;
    averaging the near-furthest cells, and their offsets from the front, takes out that lean. Only the cells
    heading the same way as most of them are averaged (within ``HEAD_GROUP_DEG`` of the direction with the most
    cells within half that), so a fire with two heads of about the same length gets one of them, not a direction
    between them that no ground runs. Returns (x, y) of the front they start from, (x, y) where they lie, the
    greatest distance, and the bearing of the summed offsets.
    """
    _, dist, ox, oy = owned
    d = dist[mask]
    cell = float(abs(X[0, 1] - X[0, 0])) if X.shape[1] > 1 else 1.0
    top = d >= d.max() - 0.5 * cell
    x, y, x0, y0, d = X[mask][top], Y[mask][top], ox[mask][top], oy[mask][top], d[top]
    b = np.degrees(np.arctan2(x - x0, y - y0)) % 360.0
    agree = (angle_diff(b[:, None], b[None, :]) <= 0.5 * HEAD_GROUP_DEG).sum(axis=1)
    one = angle_diff(b, b[int(np.argmax(agree))]) <= HEAD_GROUP_DEG
    x, y, x0, y0, d = x[one], y[one], x0[one], y0[one], d[one]
    vx, vy = float(np.sum(x - x0)), float(np.sum(y - y0))
    return (float(x0.mean()), float(y0.mean()), float(x.mean()), float(y.mean()), float(d.max()),
            bearing_deg(vx, vy))


def head_of(X: np.ndarray, Y: np.ndarray, arrival: np.ndarray, t0: float, owned: tuple, i: int,
            ) -> tuple[float, float, float, float] | None:
    """Fire ``i``'s head in the next hour, from ``front_owner``'s result: of the ground it reaches after the next
    minute and within the hour, the cells furthest beyond its front now (``_furthest``). Returns (x, y) of the
    ground burning now they run from, the distance the front advances and its compass bearing; None if it
    reaches no new ground.
    """
    new = (owned[0] == i) & (arrival > t0 + 60.0) & (arrival <= t0 + 3600.0)
    if not new.any():
        return None
    x0, y0, _, _, run, bearing = _furthest(X, Y, owned, new)
    return x0, y0, run, bearing


def mean_bearing(bearings) -> float:
    """Circular mean of compass bearings in degrees."""
    r = np.radians(np.asarray(bearings, dtype=float))
    return bearing_deg(float(np.sin(r).sum()), float(np.cos(r).sum()))


def modal_bearing(bearings, width: float = 22.5) -> float:
    """The direction most bearings agree on: the circular mean of those within 2 x ``width`` of the bearing
    with the most others within ``width`` of it. Unlike a plain mean, it does not name a direction between two
    groups that no bearing gives."""
    b = np.asarray(bearings, dtype=float)
    agree = (angle_diff(b[:, None], b[None, :]) <= width).sum(axis=1)
    mode = b[int(np.argmax(agree))]
    return mean_bearing(b[angle_diff(b, mode) <= 2.0 * width])


def _if_it_takes_off(reason: str, direction: str) -> str:
    """A crew-update reason about a smouldering fire's head, worded as what it would do if it took off now."""
    fixed = {"head now advancing": "would now spread if it takes off",
             "head no longer advancing": "would no longer spread if it takes off",
             "direction now uncertain": "direction uncertain if it takes off",
             "head faster": "would spread faster if it takes off", "head slower": "would spread slower if it takes off"}
    if reason in fixed:
        return fixed[reason]
    if reason.startswith(("direction now clear", "head turned")):
        return f"would run {direction} if it takes off"
    if reason.startswith("now reaches "):
        return "would reach " + reason[len("now reaches "):] + " if it takes off"
    if reason.startswith("reaches ") and reason.endswith(" sooner"):
        return "would reach " + reason[len("reaches "):] + " if it takes off"
    return reason


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
    """Every alert passes this before it goes out, as every directive passes the safety validator."""

    REQUIRED = ("hazard", "area", "action", "expires", "authority")

    def __init__(self, policy: AlertPolicy):
        self.policy = policy

    def check(self, alert: dict, forecast: dict | None, recent: list[dict], zone_level: int = 0) -> AlertCheckResult:
        p = self.policy
        reasons = []
        text = alert.get("message", "")
        if not text or len(text) > p.max_chars:
            reasons.append(f"message must be 1 to {p.max_chars} characters (is {len(text)})")
        if "simulated" not in alert.get("channel", ""):
            reasons.append("channel must say it is simulated")
        for part in self.REQUIRED:
            if not alert.get(part):
                reasons.append(f"missing {part}")
        level = alert.get("level")
        if level not in LEVELS:
            reasons.append("level must be 3, 4 or 5")
        elif level <= zone_level:
            reasons.append(f"zone is already at Level {zone_level}; an automatic alert can only raise it")
        zone = np.asarray(alert.get("polygon", []), dtype=float).reshape(-1, 2)
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
        if p.rule == "presigned" and level in LEVELS:
            if not alert.get("trigger") or alert["trigger"].get("rule") != f"T{level}":
                reasons.append(f"an automatic Level {level} alert must name the pre-signed rule T{level}")
            if forecast is None or forecast.get("id") != alert.get("forecast_id"):
                reasons.append("alert must cite the current forecast")
            elif max(alert.get("max_prob", 0.0), 0.0) < (p.emergency_prob if level == 5 else p.prob_threshold):
                reasons.append(f"forecast probability below the Level {level} trigger")
        if p.rule == "forecast" and level in (4, 5):
            # An evacuation must rest on the forecast it cites.
            need = p.emergency_prob if level == 5 else p.prob_threshold
            if forecast is None or forecast.get("id") != alert.get("forecast_id"):
                reasons.append("evacuation must cite the current forecast")
            elif max(alert.get("max_prob", 0.0), 0.0) < need:
                reasons.append(f"forecast probability below the Level {level} threshold")
        for r in recent:
            if r.get("zone") == alert.get("zone") and r.get("level") == level and alert.get("t", 0) - r["t"] < 600:
                reasons.append(f"duplicate of alert {r['id'] + 1} from the last 10 minutes")
                break
        return AlertCheckResult(not reasons, reasons)

    def check_notice(self, notice: dict) -> AlertCheckResult:
        """A cancellation or all-clear: short, from the authority, for a zone, simulated."""
        reasons = []
        text = notice.get("message", "")
        if not text or len(text) > self.policy.max_chars:
            reasons.append(f"message must be 1 to {self.policy.max_chars} characters (is {len(text)})")
        if "simulated" not in notice.get("channel", ""):
            reasons.append("channel must say it is simulated")
        if not notice.get("authority") or not notice.get("zone"):
            reasons.append("missing authority or zone")
        if not notice.get("phones"):
            reasons.append("no recipients")
        return AlertCheckResult(not reasons, reasons)


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def area_from_top(top_m: float, cfg: SmokeConfig) -> float:
    """Burning area (m^2) whose plume reaches ``top_m``: the inverse of ``smoke.plume_top``."""
    return float(cfg.ref_area_m2 * (max(top_m, 1.0) / cfg.top_ref_m) ** (1 / 0.35))


def _to_segment(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from (x, y) to the segment from (ax, ay) to (bx, by)."""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    u = 0.0 if L2 == 0.0 else min(max(((x - ax) * dx + (y - ay) * dy) / L2, 0.0), 1.0)
    return math.hypot(x - ax - u * dx, y - ay - u * dy)


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
        self.merged: list[dict] = []              # located fires found to be part of another (mapped ground joins them)
        self._loc_id = 0
        self._fix_src: list | None = None         # scoring only: the true fires behind a smoke fix's bearings
        self._own_src: list | None = None         # scoring only (report_v2): the true fires behind a thermal fix
        self._fix_votes: list[int] = []           # scoring only (report_v2): the true fire behind each bearing of a fix
        self.relabelled: list[dict] = []          # scoring only (tight station): what an upgraded fix pointed at before
        if cfg.station not in ("legacy", "tight"):
            raise ValueError(f"station must be 'legacy' or 'tight', not {cfg.station!r}")
        if cfg.alerts.rule not in ("forecast", "presigned", "distance"):
            raise ValueError(f"alert rule must be 'forecast', 'presigned' or 'distance', not {cfg.alerts.rule!r}")
        if cfg.alerts.smoulder_alerts and cfg.alerts.rule != "presigned":
            raise ValueError("smoulder_alerts applies to the 'presigned' rule only (the forecast rule already warns "
                             "for smoulders)")
        self._wind_now = np.zeros(2)
        self.forecasts: list[dict] = []
        self._measured_wind = (0.0, 0.0)          # the wind the swarm measured for the latest forecast
        self.crew_log: list[dict] = []            # briefs relayed to the fire crews
        self.dispatch_log: list[dict] = []        # "presigned": cards to fire dispatch
        self._carded: dict[int, bool] = {}        # located fire id -> whether its last card said spreading
        self._card_xy: dict[int, tuple[float, float]] = {}   # located fire id -> where its last card put it
        self._crew_last: dict = {}                # fire id -> (time, the brief the crews were last sent)
        self.alerts: list[dict] = []
        self.notices: list[dict] = []                                # cancellations and all-clears
        self.phone_delivered: dict[int, list] = {}                   # phone -> [(level, t, t_without_approval)]
        # Evacuation zones: fixed areas; an alert goes to every phone inside one.
        self.zones = cfg.zones or grid_zones([self.grid.X.min(), self.grid.X.max() + 1.0],
                                             [self.grid.Y.min(), self.grid.Y.max() + 1.0], [("Z", "Zone")])
        self.zone_polys = [np.asarray(z["polygon"], dtype=float) for z in self.zones]
        self.zone_of_phone = np.full(len(self.phones), -1, dtype=int)
        for k, poly in enumerate(self.zone_polys):
            hit = inside(poly, self.phone_xy) & (self.zone_of_phone < 0)
            self.zone_of_phone[hit] = k
        self.zone_level = np.zeros(len(self.zones), dtype=int)      # the level last issued (and checked) per zone
        self.zone_hold_until = np.full(len(self.zones), -math.inf)
        self.zone_hold_level = np.zeros(len(self.zones), dtype=int)  # during a hold, only a level above this goes out
        self.contacts = 0
        self.next_station_t = 0.0
        self.next_forecast_t = math.inf
        self.check = AlertCheck(cfg.alerts)
        self._heading = np.zeros((sim.n, 2))
        self._heading[:, 0] = 1.0
        self._thermal_seen = np.zeros(nf, dtype=bool)
        # Thermal map: when a drone's camera first saw each 10 m cell burning (inf: not seen burning).
        self.mapped_t = np.full(self.grid.z.shape, np.inf)
        self._forest10 = np.asarray(self.terrain.cover_at(self.grid.X, self.grid.Y)) == FOREST
        self._gx, self._gy = self.grid.X.ravel(), self.grid.Y.ravel()
        self._next_map_t = 0.0
        self._mapped_dirty = False
        self._loc_cells: dict[int, np.ndarray] = {}           # located fire id -> flat indices of its mapped cells
        self._last_event: dict = {}

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
            if t >= self._next_map_t:
                self._next_map_t = t + self.cfg.mapping.every_s
                self._map_thermal(sim, act)
        if t >= self.next_station_t:
            self.next_station_t = t + 1.0
            self._station(sim, area)

    def _map_thermal(self, sim, act: np.ndarray) -> None:
        """Each drone's thermal camera marks the 10 m cells under it that are burning or burnt (still hot).

        The footprint is the camera's field of view at the drone's height above the ground; a cell under tree
        crowns is seen burning only some of the time. Cells are placed by the drone's own position estimate.
        """
        t = sim.t
        burning = self.arrival10 <= t
        if not burning.any():
            return
        g, mc = self.grid, self.cfg.mapping
        tan = math.tan(math.radians(mc.fov_deg / 2.0))
        for d in act:
            x, y, z = (float(v) for v in sim.p[d])
            agl = z - float(self.terrain.height(x, y))
            if agl <= 0.0:
                continue
            r = agl * tan + 0.5 * g.cell_m
            near = burning & ((g.X - x) ** 2 + (g.Y - y) ** 2 <= r * r)
            if not near.any():
                continue
            iy, ix = np.nonzero(near)
            under = self._forest10[iy, ix]
            if under.any():
                keep = ~under | (self.rng.random(len(iy)) < mc.forest_seen)
                iy, ix = iy[keep], ix[keep]
            est = sim.obs.p_est[d] if sim.obs is not None else sim.p[d]
            jy, jx = g.cell_of(g.X[iy, ix] + float(est[0]) - x, g.Y[iy, ix] + float(est[1]) - y)
            fresh = ~np.isfinite(self.mapped_t[jy, jx])
            if fresh.any():
                self.mapped_t[jy[fresh], jx[fresh]] = t
                self._mapped_dirty = True

    def _components(self) -> list[np.ndarray]:
        """Groups of mapped cells, joined when within two cells of each other, as flat index arrays."""
        g = self.grid
        mask = np.isfinite(self.mapped_t).ravel()
        seen = np.zeros_like(mask)
        out = []
        for start in np.flatnonzero(mask):
            if seen[start]:
                continue
            seen[start] = True
            stack, comp = [int(start)], []
            while stack:
                c = stack.pop()
                comp.append(c)
                cy, cx = divmod(c, g.nx)
                for ny_ in range(max(cy - 2, 0), min(cy + 3, g.ny)):
                    row = ny_ * g.nx
                    for nx_ in range(max(cx - 2, 0), min(cx + 3, g.nx)):
                        n = row + nx_
                        if mask[n] and not seen[n]:
                            seen[n] = True
                            stack.append(n)
            out.append(np.array(comp, dtype=int))
        return out

    def _assign_mapped(self, sim) -> bool:
        """Give each group of mapped cells to the located fire it belongs to; a group near none is a new fire."""
        if not self._mapped_dirty:
            return False
        self._mapped_dirty = False
        new = False
        cell2 = self.grid.cell_m ** 2
        groups: dict[int, list] = {}
        for comp in self._components():
            xs, ys = self._gx[comp], self._gy[comp]
            best, bd, near = None, MAP_JOIN_M, []
            for loc in self.located:
                dist = float(np.min(np.hypot(xs - loc["x"], ys - loc["y"])))
                if dist <= MAP_JOIN_M:
                    near.append(loc)
                if dist <= bd:
                    best, bd = loc, dist
            if len(near) > 1:
                # One patch of burning ground with several located fires on it is one fire: keep the first found,
                # at the best (thermal) position, and fold the others into it.
                keep = min(near, key=lambda l: (l["t"], l["id"]))
                thermal = [l for l in near if l["method"] == "thermal"]
                if keep["method"] == "smoke" and thermal:
                    th = min(thermal, key=lambda l: (l["t"], l["id"]))
                    keep.update(x=th["x"], y=th["y"], err_m=th["err_m"], method="thermal", upgraded_t=round(sim.t, 2))
                for l in near:
                    if l is not keep:
                        keep["seen_m2"] = max(keep["seen_m2"], l["seen_m2"])
                        l["merged_into"] = keep["id"]
                        self.located.remove(l)
                        self.merged.append(l)
                        sim.event("fire_merged", None, fire=l["id"], into=keep["id"])
                best, new = keep, True
            if best is None:
                if len(comp) < 2:
                    continue                    # one hot cell alone is not called a fire
                cx, cy = float(xs.mean()), float(ys.mean())
                self._own_src = [i for i in range(len(self.fires)) if np.any(self.fire_arrival[i].ravel()[comp] <= sim.t)]
                new |= self._locate(sim, cx, cy, self.grid.cell_m, "thermal", len(comp) * cell2)
                self._own_src = None
                best = min(self.located, key=lambda l: math.hypot(l["x"] - cx, l["y"] - cy))
            groups.setdefault(best["id"], []).append(comp)
        self._loc_cells = {k: np.concatenate(v) for k, v in groups.items()}
        for loc in self.located:
            cells = self._loc_cells.get(loc["id"])
            loc["mapped_m2"] = float(len(cells)) * cell2 if cells is not None else 0.0
        return new

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
                if t - self._last_event.get((int(d), "camera"), -math.inf) >= EVENT_GAP_S:
                    self._last_event[(int(d), "camera")] = t
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
            if t - self._last_event.get((d, "pm"), -math.inf) >= EVENT_GAP_S:
                self._last_event[(d, "pm")] = t
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
            # One image shows the hot ground inside the camera's footprint, not a large fire's whole extent.
            agl = float(sim.p[by, 2]) - float(self.terrain.height(sim.p[by, 0], sim.p[by, 1]))
            foot = float(sim.sc.sensor.footprint_radius(max(agl, 1.0)))
            seen_area = min(float(area[j]), math.pi * foot * foot) * (1.0 - self.fires[j].cover) \
                * float(np.exp(0.2 * sim.rng_misc.normal()))
            self._own_src = [int(j)]
            new |= self._locate(sim, x, y, err + 1.0, "thermal", seen_area)
            self._own_src = None
        # Burning ground the thermal cameras have mapped: joined to the fires it belongs to.
        new |= self._assign_mapped(sim)
        # New camera sightings: explained by a located fire (they size it), or kept for triangulation.
        wind = self._wind_estimate(sim)
        self._wind_now = wind
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
                    self._fix_src = sorted({int(self.sightings[self._pool[i]][9]) for i in fix["used"]})
                    self._fix_votes = [int(self.sightings[self._pool[i]][9]) for i in fix["used"]]
                    new |= self._locate(sim, x, y, max(fix["err_m"], 3.0), "smoke", None)
                    self._fix_src = None
                    self._fix_votes = []
                self._pool = [i for i in self._pool if not self._associate(self.sightings[i], wind)]
        # Sizes. Mapped burning ground and the hotspot image measure a fire directly; the plume top only infers
        # it (area grows as the top to the power 2.9, and the tallest plumes all look alike), so it sizes a
        # fire only when nothing direct is known, from well-measured tops (up to 30, from the last two minutes).
        fcfg = self.cfg.forecast
        for loc in self.located:
            mapped = loc.get("mapped_m2", 0.0)
            good = [(h, d) for tt, h, _src, sig, d in loc["tops"][-30:] if t - tt <= 120.0 and sig <= fcfg.size_top_sigma_max_m]
            plume = None
            # Three drones at least: one drone's line of sight can line up with another fire's plume by chance.
            if len(good) >= 3 and len({d for _, d in good}) >= 3:
                top = min(float(np.median([h for h, _ in good])), fcfg.size_top_cap * self.cfg.smoke.top_max_m)
                plume = area_from_top(top, self.cfg.smoke)
            if mapped > 0.0:
                est_area = max(mapped, loc["seen_m2"])
            elif plume is None:
                continue
            elif loc["method"] == "smoke":
                est_area = plume
            else:
                est_area = max(loc["seen_m2"], plume)
            if est_area != loc["area_m2"]:
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
        """The swarm's own wind estimate: the mean of its last 5 s of ambient-wind measurements.

        Until the swarm has a second of them, the station uses the weather forecast it was given.
        """
        samples = [w[:2] for _, w in sim._wind_window]
        if len(samples) >= 10:
            return np.mean(np.asarray(samples, dtype=float), axis=0)
        return np.asarray(self.wind_at(sim.t, self.cfg.weather_forecast), dtype=float)

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
        fire up to half again its estimated top. A sighting explained by any located fire is not triangulated
        again. It sizes a fire only when that fire is the only one whose plume, at any size, it could be: the
        height of the top it saw is solved for the plume's drift, with its uncertainty from the range.
        """
        if not self.located:
            return False
        t, _d, x, y, z, b, e = s[:7]
        cfg = self.cfg.smoke
        sigma = math.radians(cfg.bearing_sigma_deg)
        hits, could = [], 0
        for loc in self.located:
            if self._on_plume(loc, float(plume_top(max(loc["area_m2"], 1.0), cfg)), x, y, b, wind, sigma, 7):
                hits.append(loc)
            if self._on_plume(loc, cfg.top_max_m, x, y, b, wind, sigma, 13):
                could += 1
        if not hits:
            return False
        if len(hits) == 1 and could == 1:
            # The top the drone saw is drifted downwind by its own height: solve for that height.
            loc = hits[0]
            g = float(self.terrain.height(loc["x"], loc["y"]))
            h = float(plume_top(max(loc["area_m2"], 1.0), cfg))
            r_top = 1.0
            for _ in range(4):
                r_top = math.hypot(loc["x"] + wind[0] * h / cfg.rise_mps - x, loc["y"] + wind[1] * h / cfg.rise_mps - y)
                h = min(max(z + r_top * math.tan(e) - g, 0.0), cfg.top_max_m)
            sig = r_top * math.radians(self.cfg.forecast.elevation_sigma_deg) + loc["err_m"] * abs(math.tan(e))
            loc["tops"].append((t, h, s[9], sig, s[1]))
        return True

    def _on_plume(self, loc: dict, top: float, x: float, y: float, b: float, wind: np.ndarray, sigma: float,
                  k: int) -> bool:
        """Whether bearing b from (x, y) points at the centreline of a plume of this top rising from loc."""
        cfg = self.cfg.smoke
        frac = np.linspace(0.0, 1.5, k)
        px = loc["x"] + wind[0] * top * frac / cfg.rise_mps
        py = loc["y"] + wind[1] * top * frac / cfg.rise_mps
        r = np.maximum(np.hypot(px - x, py - y), 1.0)
        tol = 3.0 * sigma + np.arctan2(max(loc["err_m"], 25.0), r)
        return bool(np.any(np.abs(_wrap(b - np.arctan2(py - y, px - x))) <= tol))

    def _locate(self, sim, x: float, y: float, err: float, method: str, area_est: float | None) -> bool:
        """Add or refine a located fire; True when something new was learnt."""
        for loc in self.located:
            gap = math.hypot(loc["x"] - x, loc["y"] - y)
            cells = self._loc_cells.get(loc["id"])
            if cells is not None and len(cells):
                gap = min(gap, float(np.min(np.hypot(self._gx[cells] - x, self._gy[cells] - y))))
            # Two thermal finds are the same fire only when they agree; a smoke fix is coarser, and a large fire
            # (its mapped ground, or its estimated radius) is wider than one point. A smoke fix on the downwind
            # line of a known fire's plume is most likely that plume, seen without its base.
            reach = 60.0 + math.sqrt(max(loc["area_m2"], 0.0) / math.pi)
            if method == "smoke":
                top = 1.5 * float(plume_top(max(loc["area_m2"], 1.0), self.cfg.smoke))
                end = np.array([loc["x"], loc["y"]]) + self._wind_now * top / self.cfg.smoke.rise_mps
                gap = min(gap, _to_segment(x, y, loc["x"], loc["y"], float(end[0]), float(end[1])))
            same = gap <= (3.0 * (loc["err_m"] + err) + 5.0 if method == loc["method"] == "thermal" else reach)
            if same and self.cfg.station == "tight" and method == "thermal" and loc["method"] == "smoke":
                # Two fires 30 to 60 m apart are common: a hotspot outside both fixes' errors is another fire.
                agree = 3.0 * (loc["err_m"] + err) + 5.0
                if cells is not None and len(cells):
                    agree += math.sqrt(max(loc["area_m2"], 0.0) / math.pi)
                same = gap <= min(agree, reach)
            if same:
                if method == "thermal" and loc["method"] == "smoke":
                    if self.cfg.station == "tight":
                        # Scoring only: the fix now sits on the hotspot, so it is scored as that fire from now on;
                        # what it pointed at before is kept, so finding that fire earlier still counts.
                        truth, truth_err = self._match_truth(x, y, sim.t)
                        if truth != loc["truth"]:
                            self.relabelled.append({"id": loc["id"], "truth": loc["truth"], "t": loc.get("truth_t", loc["t"]),
                                                    "first_method": loc.get("truth_by", loc["first_method"]),
                                                    "truth_err_m": loc["truth_err_m"]})
                            loc.update(truth=truth, truth_err_m=truth_err, truth_t=round(sim.t, 2), truth_by="thermal")
                    if self.cfg.report_v2:
                        loc.setdefault("own", []).extend((i, round(sim.t, 2), "thermal") for i in (self._own_src or []))
                    loc.update(x=x, y=y, err_m=err, method="thermal", upgraded_t=round(sim.t, 2),
                               area_m2=max(loc["area_m2"], area_est or 0.0), seen_m2=area_est or 0.0)
                    sim.event("fire_located", None, fire=loc["id"], x=round(x, 1), y=round(y, 1), err_m=round(err, 1),
                              by="thermal", upgraded=True)
                    return True
                if method == "thermal":
                    loc["seen_m2"] = max(loc["seen_m2"], area_est or 0.0)
                return False
        truth, truth_err = self._match_truth(x, y, sim.t)
        loc = {"id": self._loc_id, "x": x, "y": y, "err_m": err, "method": method, "t": round(sim.t, 2),
               "area_m2": area_est if area_est is not None else 0.5 * SMOULDER_AREA_M2, "tops": [],
               "seen_m2": area_est if method == "thermal" and area_est is not None else 0.0, "mapped_m2": 0.0,
               "truth": truth, "first_method": method, "truth_err_m": truth_err, "fix_src": self._fix_src}
        if self.cfg.report_v2:
            if method == "thermal":
                src = self._own_src or []
            else:
                # Most of its bearings came from one fire's plume, and the fix landed on that fire. A fix made
                # mostly from two fires' bearings sits where their lines cross, which is neither fire.
                votes = [v for v in self._fix_votes if v >= 0]
                top = max(set(votes), key=votes.count) if votes else None
                src = [top] if top is not None and 2 * votes.count(top) > len(self._fix_votes) else []
                src = [i for i in src if self._gap_to(i, x, y, sim.t) <= MATCH_M]
            loc["own"] = [(i, loc["t"], method) for i in src]
        self._loc_id += 1
        self.located.append(loc)
        sim.event("fire_located", None, fire=loc["id"], x=round(x, 1), y=round(y, 1), err_m=round(err, 1), by=method)
        return True

    def _gap_to(self, i: int, x: float, y: float, t: float) -> float:
        """Distance from (x, y) to true fire i's burning ground (or ignition point) at time t; scoring only."""
        f = self.fires[i]
        d = math.hypot(f.x - x, f.y - y)
        burnt = (self.fire_arrival[i] <= t).ravel()
        if burnt.any():
            d = min(d, float(np.min(np.hypot(self._gx[burnt] - x, self._gy[burnt] - y))))
        return d

    def _match_truth(self, x: float, y: float, t: float) -> tuple[int | None, float | None]:
        """The true fire whose burning ground (or ignition point) at time t is nearest; scoring only."""
        best, bd = None, math.inf
        for i, f in enumerate(self.fires):
            d = math.hypot(f.x - x, f.y - y)
            burnt = (self.fire_arrival[i] <= t).ravel()
            if burnt.any():
                d = min(d, float(np.min(np.hypot(self._gx[burnt] - x, self._gy[burnt] - y))))
            if d < bd:
                best, bd = i, d
        return (best, round(bd, 1)) if best is not None and bd <= MATCH_M else (None, None)

    # -- forecast -------------------------------------------------------------------------------------
    def _members(self, t0: float) -> list[list[Period]]:
        """Weather for each ensemble member: measured wind now, the weather forecast later, perturbed."""
        fc = self.cfg.forecast
        tw = np.asarray(self.wind_at(t0), dtype=float)
        sp = float(np.hypot(*tw)) + fc.measured_wind_sigma_mps * float(self.rng.normal())
        dr = math.atan2(tw[1], tw[0]) + math.radians(fc.measured_dir_sigma_deg) * float(self.rng.normal())
        measured = (max(sp, 0.0) * math.cos(dr), max(sp, 0.0) * math.sin(dr))
        self._measured_wind = measured
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
        # Ground the thermal cameras saw burning is burning now, in every member.
        mapped = np.flatnonzero(np.isfinite(self.mapped_t))
        burning = [(float(self._gx[c]), float(self._gy[c]), t0) for c in mapped]
        act, pot = [], []
        for weather in members:
            scale = float(np.exp(fc.ros_sigma * self.rng.normal() - 0.5 * fc.ros_sigma ** 2))
            seeds_act, seeds_all = list(burning), list(burning)
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
                "located": [{k: v for k, v in l.items() if k != "tops"} for l in located],
                "mapped_cells": [int(c) for c in mapped], "measured_wind": [float(v) for v in self._measured_wind]}

    def _stats(self, stack: np.ndarray, t0: float) -> dict:
        finite = np.where(np.isfinite(stack), stack, 1e12)
        p50 = np.median(finite, axis=0)
        p50 = np.where(p50 >= 1e11, np.inf, p50)
        probs = {m: (stack <= t0 + 60 * m).mean(axis=0) for m in (30, 60, 120, 180)}
        phone_arr = np.array([[float(a[c].min()) if len(c[0]) else math.inf for c in self._phone_cells] for a in stack]) \
            if len(self.phones) else np.zeros((len(stack), 0))
        return {"p50": p50, "probs": probs, "phone_arrivals": phone_arr, "members": stack}

    def _forecast(self, sim) -> None:
        t0 = sim.t
        fc = self.run_forecast(t0, self.located)
        fc["id"] = len(self.forecasts)
        self.forecasts.append(fc)
        brief = self._brief(fc)
        fc["brief"] = brief
        for key in ("active", "potential"):
            fc[key].pop("members", None)          # each member's grid is needed only for the brief's direction
        g = self.grid
        act, pot = fc["active"], fc["potential"]
        sim.event("forecast", None, forecast=fc["id"],
                  grid={"x0": g.x0, "y0": g.y0, "cell_m": g.cell_m, "nx": g.nx, "ny": g.ny},
                  p50_arrival_s=[None if not math.isfinite(v) else int(round(float(v) - t0)) for v in act["p50"].ravel()],
                  prob_reach_60min=[round(float(v), 2) for v in act["probs"][60].ravel()],
                  potential_prob_180min=[round(float(v), 2) for v in pot["probs"][180].ravel()],
                  isochrones_min=[30, 60, 120, 180], mapped_cells=fc["mapped_cells"], brief=brief)
        self._relay_to_crews(sim, fc)
        if self.cfg.alerts.rule == "presigned":
            self._dispatch(sim, fc)
        self._draft_alerts(sim, fc)

    def _dispatch(self, sim, fc: dict) -> None:
        """"presigned": a card to fire dispatch for each newly located fire, again when one believed smouldering
        is first believed spreading, and again when a fire's fix has moved from where its last card put it by more
        than twice its error or CARD_MOVE_M (the crew goes to the card's position). Once a card has said spreading,
        later cards for that fire do too. A smoulder goes here, not to the public."""
        pol = self.cfg.alerts
        brief = {b["fire"]: b for b in fc.get("brief", [])}
        for loc in fc["located"]:
            sent = self._carded.get(loc["id"])
            spreading = loc["area_m2"] >= SMOULDER_AREA_M2 or bool(sent)
            moved = 0.0
            if sent is not None:
                px, py = self._card_xy[loc["id"]]
                moved = math.hypot(loc["x"] - px, loc["y"] - py)
                if moved <= max(2.0 * loc["err_m"], CARD_MOVE_M) and (sent or not spreading):
                    continue
            self._carded[loc["id"]] = spreading
            self._card_xy[loc["id"]] = (loc["x"], loc["y"])
            status = "spreading" if spreading else "smouldering"
            b = brief.get(loc["id"], {})
            where = self._nearest_place((loc["x"], loc["y"]))
            size = f"{loc['area_m2']:.0f} m2" if loc["area_m2"] >= SMOULDER_AREA_M2 else "under 50 m2"
            shift = moved > max(2.0 * loc["err_m"], CARD_MOVE_M)
            text = (f"{'UPDATE: fire' if sent is not None else 'New fire'} {loc['id'] + 1}, {status}, about {size}, "
                    f"{'now ' if shift else ''}near {where} at x {loc['x']:.0f} y {loc['y']:.0f} "
                    f"(within {max(loc['err_m'], 1.0):.0f} m{f', moved {moved:.0f} m' if shift else ''}), "
                    f"located by {loc['method']}. "
                    + (f"Head running {b['direction']}. " if loc["area_m2"] >= SMOULDER_AREA_M2 and b.get("direction")
                       else "")
                    + "Initial attack requested. (Simulated)")
            card = {"id": len(self.dispatch_log), "t": round(sim.t, 2), "fire": loc["id"], "status": status,
                    "x": round(loc["x"], 1), "y": round(loc["y"], 1), "err_m": round(loc["err_m"], 1),
                    "area_m2": round(loc["area_m2"], 1), "by": loc["method"], "update": sent is not None,
                    "moved_m": round(moved, 1) if shift else 0.0, "to": pol.dispatch_channel, "text": text}
            if self.cfg.report_v2:
                # Scoring only: the true fire at the card's position when it was sent, the one a crew sent there finds.
                card["truth"] = self._match_truth(loc["x"], loc["y"], sim.t)[0]
            self.dispatch_log.append(card)
            sim.event("dispatch_card", None, **{k: v for k, v in card.items() if k != "truth"})

    def _brief(self, fc: dict) -> list[dict]:
        """Per located fire: which way its head runs and why, how fast, its size in 1 and 2 h, places reached.

        The direction is the ensemble's. In each member, ground belongs to the fire whose burning ground is
        nearest it (``front_owner``), and a fire's head is its ground furthest beyond its front in the next hour
        (``head_of``). The brief gives the direction most members agree on (``modal_bearing``); a cone either side
        of it that holds ``cone_share`` of the members, widened in quadrature by ``cone_extra_deg`` for errors the
        members do not represent, chiefly the patchy spread rate the station never sees (set on development seeds
        so the true head falls in the cone about 80% of the time); their median advance as the head's speed; and
        it calls the fire not advancing unless at least half the members advance a cell or more. The reasons are
        what the forecast ran on: the wind the swarm measured, the slope under the fire (``_drivers``), the fuel
        the members burn in the hour, and how dry the fine fuels are.
        """
        t0 = fc["t"]
        g = self.grid
        locs = fc["located"]
        if not locs:
            return []
        fires = [(l["x"], l["y"], math.sqrt(max(l["area_m2"], 0.0) / math.pi)) for l in locs]
        wx, wy = fc["measured_wind"]
        now_wx = [p for p in self.cfg.weather_forecast if p.start_s <= t0]
        moist = now_wx[-1].moisture if now_wx else Moisture()
        place_cells = np.zeros(g.X.shape, dtype=bool)
        for pl in self.cfg.places:
            place_cells[g.cell_of(pl["x"], pl["y"])] = True
        smouldering = [l["area_m2"] < SMOULDER_AREA_M2 for l in locs]
        owned, heads, ahead = {}, {}, {}
        for key, used in (("active", not all(smouldering)), ("potential", any(smouldering))):
            if not used:
                continue
            src = fc[key]
            p50 = src["p50"]
            # The fires burning on this run: the ones believed spreading, or (potential) every located fire.
            lit = [f if key == "potential" or not smouldering[i] else None for i, f in enumerate(fires)]
            # Members start each fire at a jittered spot, so the median may show none of a fire's ground burning
            # now; its located position anchors its spread in the median.
            anchored = p50.copy()
            for f in lit:
                if f is not None:
                    iy, ix = g.cell_of(f[0], f[1])
                    anchored[iy, ix] = min(anchored[iy, ix], t0)
            owned[key] = front_owner(g.X, g.Y, anchored, t0, lit, (p50 <= t0 + 7200.0) | place_cells, MAP_JOIN_M)
            heads[key] = []
            ahead[key] = np.zeros((len(locs),) + g.X.shape)
            for a in src["members"]:
                own = front_owner(g.X, g.Y, a, t0, lit, a <= t0 + 3600.0, MAP_JOIN_M)
                heads[key].append([head_of(g.X, g.Y, a, t0, own, i) for i in range(len(locs))])
                hour = (a > t0 + 60.0) & (a <= t0 + 3600.0)
                for i in range(len(locs)):
                    ahead[key][i] += hour & (own[0] == i)
        out = []
        for i, l in enumerate(locs):
            state = "smouldering" if smouldering[i] else "spreading"
            key = "potential" if smouldering[i] else "active"
            p50 = fc[key]["p50"]
            mine = owned[key][0] == i
            grow = {h: float(np.count_nonzero(mine & (p50 <= t0 + 60 * h))) * g.cell_m ** 2 / 1e4 for h in (60, 120)}
            # Each member's head in the next hour, if it advances a cell or more.
            hs = [m[i] for m in heads[key] if m[i] is not None and m[i][2] >= g.cell_m]
            spread_share = len(hs) / max(len(heads[key]), 1)
            if spread_share >= 0.5:
                bearings = np.array([h[3] for h in hs])
                bearing = modal_bearing(bearings)
                spread = float(np.quantile(angle_diff(bearings, bearing), self.cfg.forecast.cone_share))
                # In whole degrees, so the text, the 90-degree test and the crew relay all read the same number.
                cone = float(round(min(math.hypot(spread, self.cfg.forecast.cone_extra_deg), 180.0)))
                advance = float(np.median([h[2] for h in hs]))
                head_m_min = advance / 60.0
                direction = compass_of(bearing)
                # For a map: an arrow from the median forecast's front now, as far as the front goes in the hour.
                mid = head_of(g.X, g.Y, p50, t0, owned[key], i)
                ox, oy = (mid[0], mid[1]) if mid is not None else (l["x"], l["y"])
                head_from = [round(ox, 1), round(oy, 1)]
                head_xy = [round(ox + advance * math.sin(math.radians(bearing)), 1),
                           round(oy + advance * math.cos(math.radians(bearing)), 1)]
                # For a map that follows the ground: the median forecast's head (its ground furthest beyond the
                # front now) at 15, 30, 60 and 120 minutes.
                track = []
                for m in (15, 30, 60, 120):
                    reach = mine & (p50 > t0 + 60.0) & (p50 <= t0 + 60.0 * m)
                    if reach.any():
                        _, _, hx, hy, _, _ = _furthest(g.X, g.Y, owned[key], reach)
                        track.append([m, round(hx, 1), round(hy, 1)])
            else:
                head_m_min, bearing, cone, head_from, head_xy = 0.0, None, None, None, None
                direction = "not spreading" if state == "smouldering" else "not advancing"
                track = []
            drivers = self._drivers(l, ahead[key][i], (wx, wy), moist, bearing)
            places = []
            for pl in self.cfg.places:
                iy, ix = g.cell_of(pl["x"], pl["y"])
                if mine[iy, ix] and p50[iy, ix] <= t0 + self.cfg.forecast.horizon_s:
                    places.append({"name": pl["name"], "eta_min": max(0, int(round((float(p50[iy, ix]) - t0) / 60.0)))})
            places.sort(key=lambda p: p["eta_min"])
            name = f"Fire {l['id'] + 1}"
            if bearing is None:
                headline = f"{name}: " + ("not spreading" if state == "smouldering" else "burning, not advancing")
                run = "not expected to " + ("spread" if state == "smouldering" else "advance") + " in the next hour"
            else:
                sure = cone < 90.0
                headline = (f"{name}: " + ("smouldering, would run " if state == "smouldering" else "head runs ")
                            + (direction if sure else "(direction uncertain)") + f" at {head_m_min:.1f} m/min")
                way = f"head runs {direction} (within {cone:.0f}°)" if sure else \
                    f"head most likely runs {direction} (direction uncertain)"
                run = (("if it takes off, " if state == "smouldering" else "")
                       + f"{way} at about {head_m_min:.1f} m/min, driven by {drivers['why']}")
            fuel = ", ".join(f"{f['fuel']} {f['share']:.0%}" for f in drivers["fuel_ahead"] if f["share"] >= 0.02)
            text = (f"{name} ({l['method']} find, {state}): {run}"
                    + (f"; burning into {fuel}" if fuel and bearing is not None else "")
                    + f"; fine fuels at {drivers['fine_fuel_moisture_pct']:.0f}% moisture"
                    + f"; about {grow[60]:.1f} ha in 1 h and {grow[120]:.1f} ha in 2 h"
                    + ("; reaches " + ", ".join(f"{p['name']} " + ("now" if p["eta_min"] < 1 else f"in about {p['eta_min']} min")
                                                 for p in places) if places else "")
                    + ".")
            out.append({"fire": l["id"], "method": l["method"], "state": state, "head_m_per_min": round(head_m_min, 2),
                        "direction": direction, "head_bearing_deg": None if bearing is None else round(bearing, 1) % 360.0,
                        "cone_deg": cone, "spread_share": round(spread_share, 2),
                        "head_from_xy": head_from, "head_xy": head_xy, "head_track": track, "drivers": {k: v for k, v in drivers.items() if k != "why"},
                        "area_1h_ha": round(grow[60], 2), "area_2h_ha": round(grow[120], 2),
                        "places": places, "headline": headline, "text": text})
        return out

    def _drivers(self, loc: dict, ahead: np.ndarray, wind: tuple[float, float], moist: Moisture,
                 bearing: float | None = None) -> dict:
        """What pushes a fire's head: the measured wind, the slope ahead, the fuel ahead and its dryness.

        ``ahead``: per cell, how many ensemble members burn it as this fire's ground in the next hour. The slope
        and the fuel are those of that ground (the members' average slope, the commonest fuel), the terrain and
        fuel the head runs into, so the reasons explain the head the brief gives; a single point under the fire
        can tilt another way, at the foot of a rock face, say. With no ground ahead, the slope and fuel under the
        fire.

        The driver named is the one pushing the head the way the brief says it runs (``bearing``): each factor's
        push along that bearing. Where neither pushes that way (the head runs where the fuel lets it, across or
        against both), the brief says so. Without a bearing, the pushes are taken along the model's own head
        direction at that slope and fuel.
        """
        g = self.grid
        wx, wy = wind
        w10 = math.hypot(wx, wy)
        iy, ix = g.cell_of(loc["x"], loc["y"])
        weight = float(ahead.sum())
        if weight > 0:
            sx, sy = float((g.slope_x * ahead).sum()) / weight, float((g.slope_y * ahead).sum()) / weight
        else:
            sx, sy = float(g.slope_x[iy, ix]), float(g.slope_y[iy, ix])
        where = "ahead" if weight > 0 else "under the fire"
        tan = math.hypot(sx, sy)
        counts = np.bincount(g.fuel.ravel().astype(int), weights=ahead.ravel())
        fuels = np.flatnonzero(counts)
        counts = counts[fuels]
        order = np.argsort(-counts, kind="stable")
        total = max(float(counts.sum()), 1.0)
        fuel_ahead = [{"fuel": FUEL_MODELS[int(fuels[k])].name if fuels[k] else "rock or bare ground",
                  "share": round(float(counts[k]) / total, 2)} for k in order]
        # Rothermel's factors for the commonest fuel ahead (or the fuel under the fire, with none ahead).
        here = next((int(fuels[k]) for k in order if fuels[k]), 0) or int(g.fuel[iy, ix])
        phi_w, phi_s = wind_slope_factors(FUEL_MODELS[here], moist, w10, tan) if here else (0.0, 0.0)
        wind_from = (bearing_deg(wx, wy) + 180.0) % 360.0 if w10 > 0 else None
        upslope = bearing_deg(sx, sy) if tan > 0 else None
        wind_txt = f"the wind ({w10:.1f} m/s from the {compass_of(wind_from)})" if wind_from is not None else "no wind"
        slope_txt = f"the slope {where} ({100 * tan:.0f}% rising to the {compass_of(upslope)})" if upslope is not None \
            else "flat ground"
        # The model combines them as vectors, as SpreadGrid does: phi_e = |phi_w u + phi_s s| along the wind (u)
        # and up the slope (s), and the head runs at R0 (1 + phi_e). Each factor's part is its push along phi_e;
        # a downslope wind's or slope's part is negative: it works against the head.
        vx = (phi_w * wx / w10 if w10 > 0 else 0.0) + (phi_s * sx / tan if tan > 0 else 0.0)
        vy = (phi_w * wy / w10 if w10 > 0 else 0.0) + (phi_s * sy / tan if tan > 0 else 0.0)
        phi_e = math.hypot(vx, vy)
        wind_part = (phi_w * (wx * vx + wy * vy) / (w10 * phi_e)) if w10 > 0 and phi_e > 0 else 0.0
        slope_part = (phi_s * (sx * vx + sy * vy) / (tan * phi_e)) if tan > 0 and phi_e > 0 else 0.0
        # Each factor's push along the head the brief gives (or, with none, along phi_e).
        if bearing is not None:
            hx, hy = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
            wind_push = phi_w * (wx * hx + wy * hy) / w10 if w10 > 0 else 0.0
            slope_push = phi_s * (sx * hx + sy * hy) / tan if tan > 0 else 0.0
        else:
            wind_push, slope_push = wind_part, slope_part
        small = 0.1 * max(phi_w, phi_s)
        against = None
        if phi_e <= 0:
            main, why = "neither", "neither wind nor slope"
        elif wind_push <= small and slope_push <= small:
            # Neither pushes the head this way: it runs where there is fuel to burn.
            main = "fuel"
            rel = [f"{txt} {'against' if push < -small else 'across'} it" for txt, push, phi in
                   ((wind_txt, wind_push, phi_w), (slope_txt, slope_push, phi_s)) if phi > 0]
            why = "where the fuel leads" + (", with " + " and ".join(rel) if rel else "")
        elif wind_push >= 2.0 * slope_push:
            main, why = "wind", wind_txt
        elif slope_push >= 2.0 * wind_push:
            main, why = "slope", slope_txt
        else:
            main, why = "wind and slope", f"{wind_txt} and {slope_txt}"
        if main in ("wind", "slope"):
            push, txt = (slope_push, slope_txt) if main == "wind" else (wind_push, wind_txt)
            if push < -small:
                against = "slope" if main == "wind" else "wind"
                why += f", with {txt} working against it"
        return {"main": main, "against": against, "why": why, "wind_mps": round(w10, 1),
                "wind_from_deg": None if wind_from is None else round(wind_from, 1) % 360.0,
                "slope_pct": round(100 * tan, 1), "upslope_deg": None if upslope is None else round(upslope, 1) % 360.0,
                "slope_where": where,
                "phi_wind": round(phi_w, 2), "phi_slope": round(phi_s, 2),
                # Shares of the head's rate R0 (1 + phi_e): the fuel alone on flat calm ground, and the wind's and
                # the slope's parts (negative when working against the head). They sum to 1.
                "shares": {"fuel": round(1.0 / (1.0 + phi_e), 2), "wind": round(wind_part / (1.0 + phi_e), 2),
                           "slope": round(slope_part / (1.0 + phi_e), 2)},
                "fuel_ahead": fuel_ahead,
                "fine_fuel_moisture_pct": round(100 * moist.d1, 1)}

    def _relay_to_crews(self, sim, fc: dict) -> None:
        """Relay a fire's brief to the crews when it is new or has changed enough to act on (``CrewConfig``).

        Against what the crews were last told about a fire, a brief goes out again when: the fire grows into a
        spreading one (or is found smaller); its head starts or stops advancing; its direction becomes clear or
        uncertain (a cone under 90 degrees either side, or not); while its direction is clear, its head turns by
        ``turn_deg`` or more and beyond the cone the crews were given; its speed rises or falls by a factor of
        ``speed_ratio`` (and by at least ``speed_min_m_min``); or it will reach a named place it was not going
        to, or reach one ``eta_earlier_min`` sooner by the clock. A fire folded into another is withdrawn. For a
        smouldering fire the head is what it would do if it took off now, and the reasons say so.
        """
        cc = self.cfg.crew
        briefed = {b["fire"] for b in fc["brief"]}
        for fire in [f for f in self._crew_last if f not in briefed]:
            del self._crew_last[fire]
            into = next((l["merged_into"] for l in self.merged if l["id"] == fire and "merged_into" in l), None)
            reason = f"merged into Fire {into + 1}" if into is not None else "no longer tracked"
            self._send_crews(sim, fc, {"fire": fire, "headline": f"Fire {fire + 1}: {reason}",
                                       "text": f"Fire {fire + 1} is {reason}; disregard its earlier brief.",
                                       "head_bearing_deg": None, "cone_deg": None, "head_m_per_min": 0.0,
                                       "head_from_xy": None, "head_xy": None, "head_track": [], "places": []}, [reason])
        for b in fc["brief"]:
            entry = self._crew_last.get(b["fire"])
            why = []
            if entry is None:
                why.append("new fire")
            else:
                t_last, last = entry
                if b["state"] != last["state"]:
                    why.append(f"now {b['state']}")
                moving, was_moving = b["head_bearing_deg"] is not None, last["head_bearing_deg"] is not None
                if moving != was_moving:
                    why.append("head now advancing" if moving else "head no longer advancing")
                elif moving:
                    sure, was_sure = b["cone_deg"] < 90.0, last["cone_deg"] < 90.0
                    if sure != was_sure:
                        why.append(f"direction now clear: {b['direction']}" if sure else "direction now uncertain")
                    elif sure and float(angle_diff(b["head_bearing_deg"], last["head_bearing_deg"])) \
                            >= max(cc.turn_deg, last["cone_deg"]):
                        # A turn inside the cone the crews were given is within the uncertainty they already have.
                        why.append(f"head turned {b['direction']}")
                v0, v1 = last["head_m_per_min"], b["head_m_per_min"]
                if abs(v1 - v0) >= cc.speed_min_m_min and max(v0, v1) >= cc.speed_ratio * max(min(v0, v1), 1e-9):
                    why.append("head faster" if v1 > v0 else "head slower")
                # Arrival times count from each brief's own time; compare them by the clock.
                gone = (sim.t - t_last) / 60.0
                before = {p["name"]: p["eta_min"] for p in last["places"]}
                new = [p["name"] for p in b["places"] if p["name"] not in before]
                sooner = [p["name"] for p in b["places"]
                          if p["name"] in before and before[p["name"]] - gone - p["eta_min"] >= cc.eta_earlier_min]
                if new:
                    why.append("now reaches " + ", ".join(new))
                if sooner:
                    why.append("reaches " + ", ".join(sooner) + " sooner")
            if why and b["state"] == "smouldering":
                why = [_if_it_takes_off(r, b["direction"]) for r in why]
            if why:
                self._crew_last[b["fire"]] = (sim.t, b)
                self._send_crews(sim, fc, b, why)

    def _send_crews(self, sim, fc: dict, b: dict, why: list[str]) -> None:
        msg = {"t": round(sim.t, 2), "fire": b["fire"], "forecast": fc["id"], "reason": "; ".join(why),
               "headline": b["headline"], "text": b["text"]}
        self.crew_log.append(msg)
        sim.event("crew_update", None, fire=b["fire"], forecast=fc["id"], reason=msg["reason"], headline=b["headline"],
                  text=b["text"], head_bearing_deg=b["head_bearing_deg"], cone_deg=b["cone_deg"],
                  head_m_per_min=b["head_m_per_min"], head_from_xy=b["head_from_xy"], head_xy=b["head_xy"],
                  head_track=b.get("head_track", []), places=b["places"], to=self.cfg.crew.channel)

    # -- alerts ------------------------------------------------------------------------------------------
    def _zone_targets(self, fc: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The level each phone's situation calls for under the rule, with its probability and median arrival."""
        pol = self.cfg.alerts
        t0 = fc["t"]
        act, pot = fc["active"], fc["potential"]
        n = len(self.phones)
        level = np.zeros(n, dtype=int)
        prob = np.zeros(n)
        eta = np.full(n, np.inf)
        if pol.rule == "distance":
            locs = fc["located"]
            if not locs:
                return level, prob, eta
            lx = np.array([l["x"] for l in locs])
            ly = np.array([l["y"] for l in locs])
            dist = np.sqrt((self.phone_xy[:, 0, None] - lx[None]) ** 2 + (self.phone_xy[:, 1, None] - ly[None]) ** 2)
            spreading = np.array([l["area_m2"] >= SMOULDER_AREA_M2 for l in locs])
            near_spreading = np.where(spreading[None, :], dist, np.inf).min(axis=1)
            level[dist.min(axis=1) <= pol.distance_warn_m] = 3
            level[near_spreading <= pol.distance_evacuate_m] = 4
            level[near_spreading <= pol.distance_emergency_m] = 5
            prob[level > 0] = 1.0
        else:
            p3 = (act["phone_arrivals"] <= t0 + pol.warn_within_s).mean(axis=0)
            if pol.rule != "presigned" or pol.smoulder_alerts:
                # The forecast rule (and pre-signed T3 with smoulder_alerts) also warns for smoulders, as if they
                # took off now.
                p3 = np.maximum((pot["phone_arrivals"] <= t0 + pol.warn_within_s).mean(axis=0), p3)
            p4 = (act["phone_arrivals"] <= t0 + pol.evacuate_within_s).mean(axis=0)
            p5 = (act["phone_arrivals"] <= t0 + pol.emergency_within_s).mean(axis=0)
            level[p3 >= pol.prob_threshold] = 3
            level[p4 >= pol.prob_threshold] = 4
            level[p5 >= pol.emergency_prob] = 5
            prob = np.select([level == 5, level == 4, level == 3], [p5, p4, p3], 0.0)
        for src in (act, pot):
            arr = src["phone_arrivals"]
            if arr.size:
                eta = np.minimum(eta, np.median(np.where(np.isfinite(arr), arr, 1e12), axis=0) - t0)
        return level, prob, eta

    def _draft_alerts(self, sim, fc: dict) -> None:
        """Raise each zone to the level the rules call for; alerts go out once checked (and approved, if manual)."""
        pol = self.cfg.alerts
        if not len(self.phones):
            return
        t0 = fc["t"]
        level, prob, eta = self._zone_targets(fc)
        for k, zone in enumerate(self.zones):
            members = np.nonzero(self.zone_of_phone == k)[0]
            if not len(members):
                continue
            target = int(level[members].max())
            if t0 < self.zone_hold_until[k] and target <= self.zone_hold_level[k]:
                continue                                        # an operator cancelled or cleared here recently
            if target < 3 or target <= self.zone_level[k]:
                continue
            if any(a["state"] == "checked" and a["zone"] == zone["code"] and a["level"] >= target for a in self.alerts):
                continue                                        # one is already waiting for approval
            at = members[level[members] >= target]
            soon = float(np.min(eta[at])) if len(at) else math.inf
            eta_min = None if not math.isfinite(soon) or soon > 1e10 else max(0, int(round(soon / 60.0)))
            tag, head, action = LEVELS[target]
            when = ("" if eta_min is None or target == 5 else ", may already be close" if eta_min < 1
                    else f", may reach it within about {eta_min} min")
            area = zone["name"]
            alert = {
                "id": len(self.alerts), "t": round(t0, 2), "level": target, "level_name": f"{tag} {head.lower()}",
                "zone": zone["code"], "polygon": zone["polygon"], "forecast_id": fc["id"], "rule": pol.rule,
                "hazard": "wildfire", "area": area, "action": action, "authority": pol.authority,
                "channel": pol.channel, "expires_in_s": 6 * 3600, "expires": "in 6 hours",
                "phones": [int(i) for i in members], "max_prob": float(np.max(prob[at])) if len(at) else 0.0,
                "eta_min": eta_min, "raised_from": int(self.zone_level[k]),
            }
            alert["message"] = (f"{pol.authority}: {tag} {head}. Wildfire near zone {zone['code']}, {area}{when}. "
                                f"{action} Alert expires {alert['expires']}. (Simulated alert)")
            if pol.rule == "presigned":
                alert["trigger"] = pol.trigger(target)
            if self.cfg.report_v2:
                # Scoring only: the located fires this level rests on (all of them for a Level 3 that counts smoulders).
                smoulders_count = (pol.rule == "forecast" or pol.smoulder_alerts) and target == 3
                alert["drivers"] = [{"id": l["id"], "truth": l["truth"]} for l in fc["located"]
                                    if smoulders_count or l["area_m2"] >= SMOULDER_AREA_M2]
            recent = [a for a in self.alerts if a["state"] not in ("rejected",)]
            result = self.check.check(alert, fc, recent, int(self.zone_level[k]))
            alert["check"] = result.to_dict()
            alert["state"] = "checked" if result.ok else "rejected"
            self.alerts.append(alert)
            sim.event("alert_drafted", None, alert=alert["id"], level=target, zone=zone["code"], phones=len(members),
                      polygon=zone["polygon"], message=alert["message"], channel=pol.channel)
            sim.event("alert_check", None, alert=alert["id"], **result.to_dict())
            if not result.ok:
                continue
            for b in self.alerts[:-1]:                          # a higher alert replaces one still waiting
                if b["state"] == "checked" and b["zone"] == zone["code"]:
                    b["state"] = "superseded"
                    b["superseded_by"] = alert["id"]
            if pol.approval == "auto":
                self.approve(sim, alert["id"], by="automatic (fixed rules)")
            elif pol.approval == "modelled":
                alert["approve_at"] = t0 + float(pol.approval_median_s * np.exp(pol.approval_sigma * self.rng.normal()))

    def _nearest_place(self, xy) -> str:
        if not self.cfg.places:
            return "the valley"
        d = [math.hypot(p["x"] - xy[0], p["y"] - xy[1]) for p in self.cfg.places]
        return self.cfg.places[int(np.argmin(d))]["name"]

    def approve(self, sim, alert_id: int, by: str = "incident commander") -> dict:
        """Send a checked alert to the phones in its zone (simulated): at once when automatic, or on approval."""
        if not 0 <= alert_id < len(self.alerts):
            return {"ok": False, "reason": "no such alert"}
        a = self.alerts[alert_id]
        if a["state"] != "checked":
            return {"ok": False, "reason": f"alert is {a['state']}"}
        t = a["approve_at"] if a.get("approve_at") is not None and by.endswith("(modelled)") else sim.t
        a["state"] = "approved"
        a["approved_t"] = round(t, 2)
        a["approved_by"] = by
        delivered = self._broadcast(a["phones"], a["level"], t, a["t"])
        a["delivered"] = delivered
        sim.event("alert_approved", None, alert=alert_id, by=by)
        sim.event("alert_delivered", None, alert=alert_id, phones=delivered, of=len(a["phones"]), level=a["level"],
                  zone=a["zone"])
        a["state"] = "delivered"
        for b in self.alerts:                                   # the zone's earlier alerts are replaced by this one
            if b["id"] != alert_id and b["zone"] == a["zone"] and b["state"] == "delivered":
                b["state"] = "superseded"
                b["superseded_by"] = alert_id
        k = self._zone_index(a["zone"])
        self.zone_level[k] = max(int(self.zone_level[k]), int(a["level"]))
        return {"ok": True, "delivered": delivered}

    def _zone_index(self, code: str) -> int | None:
        return next((i for i, z in enumerate(self.zones) if z["code"] == code), None)

    def _close_zone(self, k: int, state: str, t: float) -> None:
        """Retire every alert in a zone: those sent take ``state``; any still waiting are cancelled unsent."""
        code = self.zones[k]["code"]
        for b in self.alerts:
            if b["zone"] != code:
                continue
            if b["state"] in ("delivered", "superseded") and b.get("delivered") is not None:
                b["state"] = state
                b[f"{state}_t"] = round(t, 2)
            elif b["state"] == "checked":
                b["state"] = "cancelled"
                b["cancelled_t"] = round(t, 2)

    def _broadcast(self, phones, level: int, t: float, t_auto: float) -> int:
        lo, hi = self.cfg.alerts.delivery_s
        delivered = 0
        for ph in phones:
            if self.unreachable[ph]:
                continue
            lag = float(self.rng.uniform(lo, hi))
            self.phone_delivered.setdefault(int(ph), []).append((int(level), t + lag, t_auto + lag))
            delivered += 1
        return delivered

    def _notice(self, sim, kind: str, k: int, text: str, by: str, alert_id: int | None = None) -> dict:
        pol = self.cfg.alerts
        zone = self.zones[k]
        phones = [int(i) for i in np.nonzero(self.zone_of_phone == k)[0]]
        notice = {"id": len(self.notices), "kind": kind, "t": round(sim.t, 2), "zone": zone["code"], "alert": alert_id,
                  "authority": pol.authority, "channel": pol.channel, "phones": phones, "by": by,
                  "message": f"{pol.authority}: {text} (Simulated alert)"}
        result = self.check.check_notice(notice)
        notice["check"] = result.to_dict()
        if not result.ok:
            notice["state"] = "rejected"
            self.notices.append(notice)
            return {"ok": False, "reason": "; ".join(result.reasons)}
        notice["delivered"] = self._broadcast(phones, 0, sim.t, sim.t)
        notice["state"] = "delivered"
        self.notices.append(notice)
        sim.event(kind, None, zone=zone["code"], alert=alert_id, by=by, phones=notice["delivered"], message=notice["message"])
        return {"ok": True, "delivered": notice["delivered"]}

    def cancel(self, sim, alert_id: int, by: str = "incident commander") -> dict:
        """An operator withdraws an alert (a false alarm). If it was sent, every alert in its zone is withdrawn and
        the phones told. The zone is then held for a while: the rules re-alert it only at a level above the one
        withdrawn, and never below Level 5."""
        if not 0 <= alert_id < len(self.alerts):
            return {"ok": False, "reason": "no such alert"}
        a = self.alerts[alert_id]
        if a["state"] == "superseded":
            newer = a.get("superseded_by")
            return {"ok": False, "reason": "a newer alert replaced it" + (f" (alert {newer + 1})" if newer is not None else "")}
        if a["state"] not in ("checked", "delivered"):
            return {"ok": False, "reason": f"alert is {a['state']}"}
        k = self._zone_index(a["zone"])
        if a["state"] == "checked":                            # never sent: nothing to tell the phones
            a["state"] = "cancelled"
            a["cancelled_t"] = round(sim.t, 2)
            self._hold(k, sim.t, max(4, int(a["level"])))
            sim.event("alert_cancelled", None, zone=a["zone"], alert=alert_id, by=by, phones=0, message="")
            return {"ok": True, "delivered": 0}
        stamp = f"{int(a['t'] // 60):d} min into the flight"
        out = self._notice(sim, "alert_cancelled", k, f"CANCELLED. The {LEVELS[a['level']][0]} alert for zone "
                           f"{a['zone']} sent {stamp} is withdrawn; no alert is in force for this zone. "
                           "Follow official instructions.", by, alert_id)
        if out["ok"]:
            self._close_zone(k, "cancelled", sim.t)
            self._hold(k, sim.t, max(4, int(self.zone_level[k])))
            self.zone_level[k] = 0
        return out

    def _hold(self, k: int, t: float, level: int) -> None:
        """Hold a zone from automatic alerts at or below ``level`` for the policy's hold time."""
        self.zone_hold_until[k] = t + self.cfg.alerts.hold_after_cancel_s
        self.zone_hold_level[k] = level

    def all_clear(self, sim, zone_code: str, by: str = "incident commander") -> dict:
        """An operator ends the danger in a zone; its phones are told they may return when roads reopen. The zone
        is then held from automatic alerts at or below the level it had, unless the fire gets worse than that."""
        k = self._zone_index(zone_code)
        if k is None:
            return {"ok": False, "reason": "no such zone"}
        if self.zone_level[k] == 0:
            return {"ok": False, "reason": "zone has no alert in force"}
        out = self._notice(sim, "all_clear", k, f"ALL CLEAR for zone {zone_code}, {self.zones[k]['name']}. "
                           "The wildfire danger has passed. Return only when roads reopen.", by)
        if out["ok"]:
            self._hold(k, sim.t, int(self.zone_level[k]))
            self._close_zone(k, "cleared", sim.t)
            self.zone_level[k] = 0
        return out

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
        made = sorted(self.located + self.merged, key=lambda l: l["id"])
        if self.cfg.station == "tight":
            # A fix re-scored on an upgrade counts for its new fire from the upgrade, and for its old one before.
            recs = [{"id": l["id"], "truth": l["truth"], "t": l.get("truth_t", l["t"]),
                     "first_method": l.get("truth_by", l["first_method"]), "truth_err_m": l["truth_err_m"]} for l in made]
            for loc in sorted(recs + self.relabelled, key=lambda r: (r["t"], r["id"])):
                if loc["truth"] is not None and loc["truth"] not in first:
                    first[loc["truth"]] = loc
        else:
            for loc in made:
                if loc["truth"] is not None and loc["truth"] not in first:
                    first[loc["truth"]] = loc
        fires = []
        for i, f in enumerate(self.fires):
            loc = first.get(i)
            fires.append({"shelter": f.shelter, "active_at_start": f.t_active_s <= 0.0, "t_active_s": round(f.t_active_s, 1),
                          "located": loc is not None, "t": loc["t"] if loc else None,
                          "area_m2": round(float(self.area(loc["t"])[i]), 1) if loc else None,
                          "by": loc["first_method"] if loc else None, "err_m": loc["truth_err_m"] if loc else None})
        out["fires_total"] = nf
        out["fires_located"] = len(first)
        out["fires_located_pct"] = round(100.0 * len(first) / nf, 1) if nf else None
        out["fires_located_by_smoke"] = sum(1 for f in fires if f["by"] == "smoke")
        out["fires_located_by_thermal"] = sum(1 for f in fires if f["by"] == "thermal")
        out["false_locations"] = sum(1 for l in made if l["truth"] is None)          # every false fix, merged or not
        if self.cfg.station == "tight":
            out["false_locations"] += sum(1 for r in self.relabelled if r["truth"] is None)   # false until upgraded
        out["false_locations_standing"] = sum(1 for l in self.located if l["truth"] is None)
        out["duplicates_merged"] = len(self.merged)
        out["all_located_s"] = max(f["t"] for f in fires) if fires and all(f["located"] for f in fires) else None
        smoke_err = [f["err_m"] for f in fires if f["by"] == "smoke"]
        out["smoke_location_error_m"] = round(float(np.median(smoke_err)), 1) if smoke_err else None
        out["smoke_sightings"] = sum(1 for s in self.sightings if s[7] == "camera")
        out["smoke_contacts"] = self.contacts
        out["fire_table"] = fires
        if self.cfg.report_v2:
            out.update(self._own_evidence(sim, made, fires))
        out["forecasts"] = len(self.forecasts)
        out["forecast_t_s"] = round(self.forecasts[-1]["t"], 1) if self.forecasts else None
        out["mapped_ha"] = round(float(np.count_nonzero(np.isfinite(self.mapped_t))) * self.grid.cell_m ** 2 / 1e4, 2)
        out.update(self._forecast_skill())
        out.update(self._direction_skill())
        out["crew_updates"] = len(self.crew_log)
        out.update(self._warning_scores())
        out["today"] = today_methods(self, sim.seed)          # the same fires, found by today's methods
        return out

    def _own_evidence(self, sim, made: list, fires: list) -> dict:
        """report_v2: fires found by their own evidence, and why each other fire was missed (scoring only).

        A fire is found by its own evidence when a fix was placed by its hotspot (a thermal find of it, or its
        mapped burning ground) or by bearings to its own plume (more than half of a smoke fix's bearings came from it,
        and the fix landed within 60 m of it). The nearest-fire rule in ``fire_table`` credits any fix within 60 m, which can
        credit a fire for a fix made from its neighbour's evidence.
        Each fire then gets one class: found smouldering; found after it spread; seen by the camera but not
        credited (the hotspot was confirmed but no fix came from it); in view but never confirmed (cover or an
        overhang hid it); never in view.
        """
        own: dict[int, tuple] = {}
        for loc in made:
            for i, t, by in loc.get("own", []):
                if i not in own or t < own[i][0]:
                    own[i] = (t, by)
        cam = sim.target_found_t
        view = getattr(sim, "target_view_s", None)
        classes = {"smouldering": 0, "after_spread": 0, "not_credited": 0, "hidden": 0, "not_in_view": 0}
        for i, (f, row) in enumerate(zip(self.fires, fires)):
            t_own = own.get(i, (None, None))[0]
            row["own_t"] = t_own
            row["own_by"] = own.get(i, (None, None))[1]
            row["camera_t"] = None if i >= len(cam) or np.isnan(cam[i]) else round(float(cam[i]), 2)
            if t_own is not None:
                c = "smouldering" if t_own < f.t_active_s else "after_spread"
            elif row["camera_t"] is not None:
                c = "not_credited"
            elif view is not None and i < len(view) and view[i] > 0.0:
                c = "hidden"
            else:
                c = "not_in_view"
            row["miss"] = c
            classes[c] += 1
            if self.cfg.alerts.rule == "presigned":
                cards = [c2["t"] for c2 in self.dispatch_log if c2.get("truth") == i]
                row["dispatch_t"] = min(cards) if cards else None
        n = len(self.fires)
        return {"fires_found_own": len(own), "fires_found_own_pct": round(100.0 * len(own) / n, 1) if n else None,
                "fires_found_own_smouldering": classes["smouldering"], "fire_classes": classes}

    def _forecast_skill(self) -> dict:
        """The flight's last forecast against where the fires really burn in the next 1 and 3 hours."""
        keys = ("sorensen_1h", "sorensen_3h", "sorensen_3h_all_fires", "phone_arrival_mae_min",
                "phone_arrival_bias_min", "phone_arrival_n", "brier_60min")
        if not self.forecasts:
            return {k: None for k in keys}
        fc = self.forecasts[-1]
        t0 = fc["t"]
        p50 = fc["active"]["p50"]
        # The fires the station knew were spreading: located as spreading, or with burning ground it mapped.
        spreading = {l["truth"] for l in fc["located"] if l["truth"] is not None and l["area_m2"] >= SMOULDER_AREA_M2}
        cells = np.asarray(fc["mapped_cells"], dtype=int)
        if len(cells):
            spreading |= {i for i in range(len(self.fires)) if np.any(self.fire_arrival[i].ravel()[cells] <= t0)}
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

    def _direction_skill(self) -> dict:
        """The last forecast's head directions against where each fire's head really runs in the next hour.

        The true head is found as the brief finds it (``front_owner`` and ``head_of``), on each true fire's own
        arrival times where it reaches the ground first (ground another fire burns first is that fire's), from
        its burning ground now or, if it has not taken off yet, its ignition point. Every brief in the last
        forecast (sent to the crews or not) is scored against the true fire whose burning ground (or ignition
        point) is nearest the located fire, within ``MATCH_M``, if that fire's head advances at least two cells
        in the hour: the brief's bearing, the downwind bearing of the wind the swarm measured (a wind-only rule)
        and the upslope bearing under the fire (a slope-only rule, where the slope is 2% or more), each against
        the true bearing; whether the truth is inside the brief's cone; and the head speeds. A brief that says
        the head will not advance has no bearing and counts as missed (180 degrees off). Fires whose heads
        advance with no located fire within ``MATCH_M`` of their ground are counted, not scored. Scoring only:
        the station never reads the truth.
        """
        empty = {"direction_n": 0, "direction_err_deg_median": None, "direction_in_cone_pct": None,
                 "direction_missed": 0, "direction_unlocated": 0, "direction_table": []}
        if not self.forecasts:
            return empty
        fc = self.forecasts[-1]
        t0 = fc["t"]
        g = self.grid
        wx, wy = fc["measured_wind"]
        downwind = bearing_deg(wx, wy) if math.hypot(wx, wy) > 0 else None
        locs = {l["id"]: l for l in fc["located"]}
        # Each true fire's ground now (its ignition point if it has not taken off) and its true head.
        truth = []
        for j, f in enumerate(self.fires):
            first = self.fire_arrival[j] <= self.arrival10
            arr = np.where(first, self.fire_arrival[j], np.inf)
            iy, ix = g.cell_of(f.x, f.y)
            if first[iy, ix]:                          # not yet overrun by another fire
                arr[iy, ix] = min(arr[iy, ix], t0)
            now = arr <= t0 + 60.0
            own = front_owner(g.X, g.Y, arr, t0, [(f.x, f.y, 0.0)], arr <= t0 + 3600.0, math.inf)
            truth.append((g.X[now], g.Y[now], head_of(g.X, g.Y, arr, t0, own, 0)))
        moving = {j for j, (_, _, h) in enumerate(truth) if h is not None and h[2] >= 2 * g.cell_m}
        rows, briefed = [], set()
        for b in fc["brief"]:
            l = locs[b["fire"]]
            gaps = [float(np.min(np.hypot(xs - l["x"], ys - l["y"]))) if len(xs) else math.inf for xs, ys, _ in truth]
            briefed |= {k for k, gap in enumerate(gaps) if gap <= MATCH_M}
            j = int(np.argmin(gaps)) if gaps else None
            if j is None or gaps[j] > MATCH_M:
                continue
            if j not in moving:
                continue
            _, _, run, true_b = truth[j][2]
            # The slope-only rule: straight up the slope under the located fire, where it is 2% or more.
            cy, cx = g.cell_of(l["x"], l["y"])
            sx, sy = float(g.slope_x[cy, cx]), float(g.slope_y[cy, cx])
            up = round(bearing_deg(sx, sy), 1) % 360.0 if round(100 * math.hypot(sx, sy), 1) >= 2.0 else None
            got = b["head_bearing_deg"]
            rows.append({"fire": b["fire"], "truth_fire": j, "state": b["state"],
                         "truth_bearing_deg": round(true_b, 1) % 360.0, "truth_m_per_min": round(run / 60.0, 2),
                         "brief_bearing_deg": got, "cone_deg": b["cone_deg"], "brief_m_per_min": b["head_m_per_min"],
                         "err_deg": None if got is None else round(float(angle_diff(got, true_b)), 1),
                         "in_cone": None if got is None else bool(angle_diff(got, true_b) <= b["cone_deg"]),
                         "wind_only_err_deg": None if downwind is None else round(float(angle_diff(downwind, true_b)), 1),
                         "slope_only_err_deg": None if up is None else round(float(angle_diff(up, true_b)), 1)})
        errs = [180.0 if r["err_deg"] is None else r["err_deg"] for r in rows]
        cone = [r["in_cone"] for r in rows if r["in_cone"] is not None]
        return {"direction_n": len(rows),
                "direction_err_deg_median": round(float(np.median(errs)), 1) if errs else None,
                "direction_in_cone_pct": round(100.0 * sum(cone) / len(cone), 1) if cone else None,
                "direction_missed": sum(1 for r in rows if r["err_deg"] is None),
                "direction_unlocated": len(moving - briefed), "direction_table": rows}

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
                if code >= 3:
                    first_any[ph] = min(first_any[ph], td)
                    first_any_auto[ph] = min(first_any_auto[ph], td_auto)
                if code >= 4:
                    first_evac[ph] = min(first_evac[ph], td)
        in_time = reached & (first_any < truth)
        lead = (truth[in_time] - first_any[in_time]) / 60.0
        lead_auto = (truth[in_time] - first_any_auto[in_time]) / 60.0
        evac_in_time = reached & (first_evac < truth)
        evac_lead = (truth[evac_in_time] - first_evac[evac_in_time]) / 60.0
        evacuated = np.isfinite(first_evac)
        warned = np.isfinite(first_any)
        delivered = [a for a in self.alerts if a["state"] in ("delivered", "superseded", "cancelled", "cleared")
                     and a.get("delivered") is not None]
        latency = [a["approved_t"] - a["t"] for a in delivered if a.get("approved_t") is not None]
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
            "alerts_level3": sum(1 for a in delivered if a["level"] == 3),
            "alerts_level4": sum(1 for a in delivered if a["level"] == 4),
            "alerts_level5": sum(1 for a in delivered if a["level"] == 5),
            "zones_alerted": len({a["zone"] for a in delivered}),
            "alert_send_delay_s_median": round(float(np.median(latency)), 1) if latency else None,
            "phones_level5": int(sum(1 for ph, d in self.phone_delivered.items() if any(c == 5 for c, _, _ in d))),
            "first_alert_s": round(min(float(first_any.min()), 1e12), 1) if np.isfinite(first_any).any() else None,
            **(self._alert_scores(delivered) if self.cfg.report_v2 else {}),
        }

    def _alert_scores(self, delivered: list[dict]) -> dict:
        """report_v2: which public alerts the fire did not justify, and what each rested on (scoring only).

        An alert is not justified when no true fire reached a phone in its zone within its level's window (3 hours
        for Level 3, 1 hour for Level 4, 15 minutes for Level 5), as far as the 4-hour truth runs. Its drivers are
        the located fires the level rested on: under the forecast rule a Level 3 rests on every located fire,
        smouldering ones included; otherwise on those believed spreading.
        """
        pol = self.cfg.alerts
        window = {3: pol.warn_within_s, 4: pol.evacuate_within_s, 5: pol.emergency_within_s}
        rows = []
        for a in delivered:
            k = self._zone_index(a["zone"])
            members = np.nonzero(self.zone_of_phone == k)[0]
            reach = float(self.phone_truth_s[members].min()) if len(members) else math.inf
            t_sent = float(a.get("approved_t", a["t"]))
            truths = [d["truth"] for d in a.get("drivers", [])]
            real = sorted({i for i in truths if i is not None})
            rows.append({"id": a["id"], "t": a["t"], "sent_t": round(t_sent, 2), "level": a["level"], "zone": a["zone"],
                         "rule": a.get("trigger", {}).get("rule", a["rule"]),
                         "justified": reach <= t_sent + window[a["level"]],
                         "window_past_truth": t_sent + window[a["level"]] > self.cfg.horizon_s,
                         "drivers": truths,
                         "false_only": bool(truths) and not real,
                         "smouldering_only": bool(real) and all(self.fires[i].t_active_s > a["t"] for i in real),
                         "never_spread": bool(real) and all(self.fires[i].t_active_s >= self.cfg.horizon_s for i in real)})
        cards = [c for c in self.dispatch_log if not c["update"]]
        return {"alerts_not_justified": sum(not r["justified"] for r in rows),
                "alerts_level3_not_justified": sum(not r["justified"] for r in rows if r["level"] == 3),
                "alerts_from_false_fires": sum(r["false_only"] for r in rows),
                "alerts_for_smoulders": sum(r["smouldering_only"] for r in rows),
                "alerts_for_fires_never_spreading": sum(r["never_spread"] for r in rows),
                "alert_table": rows,
                "dispatch_cards": len(cards), "dispatch_cards_false": sum(c.get("truth") is None for c in cards),
                "dispatch_cards_smouldering": sum(c["status"] == "smouldering" for c in cards)}

    # -- dashboard ------------------------------------------------------------------------------------
    def hello(self) -> dict:
        """What the page needs once: the true arrival-time grid, so it can draw the fire growing."""
        g = self.truth10
        return {"fire_truth": {"x0": g.x0, "y0": g.y0, "cell_m": g.cell_m, "nx": g.nx, "ny": g.ny,
                               "arrival_s": [None if not math.isfinite(v) else int(round(float(v)))
                                             for v in self.arrival10.ravel()]},
                "phones_unreachable": [int(i) for i in np.nonzero(self.unreachable)[0]],
                "zones": [{**z, "phones": [int(i) for i in np.nonzero(self.zone_of_phone == k)[0]]}
                          for k, z in enumerate(self.zones)],
                "levels": {str(k): {"tag": v[0], "head": v[1], "action": v[2]} for k, v in LEVELS.items()}}

    def frame(self, sim) -> dict:
        t = sim.t
        area = self.area(t)
        # Each phone shows the latest message it has received: 0 none or cleared, else the alert level.
        level = np.zeros(len(self.phones), dtype=int)
        when = np.full(len(self.phones), -np.inf)
        for ph, deliveries in list(self.phone_delivered.items()):   # an operator's action may arrive on another thread
            for code, td, _ in list(deliveries):
                if when[ph] < td <= t:
                    level[ph], when[ph] = code, td
        recent = []
        for s in reversed(self.sightings):
            if t - s[0] > 5.0 or len(recent) >= 40:
                break
            recent.append({"drone": s[1], "bearing_deg": round(math.degrees(s[5]) % 360, 1), "t": round(s[0], 2),
                           "kind": s[7]})
        return {
            "fires_state": [{"id": i, "area_m2": round(float(a), 1),
                             "smoke_top_m": round(float(plume_top(a, self.cfg.smoke)), 1),
                             "active": bool(t >= f.t_active_s)} for i, (f, a) in enumerate(zip(self.fires, area))],
            "located": [{"id": l["id"], "x": round(l["x"], 1), "y": round(l["y"], 1), "err_m": round(l["err_m"], 1),
                         "by": l["method"], "area_m2": round(l["area_m2"], 1),
                         "mapped_m2": round(l.get("mapped_m2", 0.0), 1)} for l in self.located],
            "sightings": recent[::-1],
            "alerts": [{"id": a["id"], "state": a["state"], "level": a["level"], "zone": a["zone"],
                        "phones": len(a["phones"])} for a in self.alerts],
            "zones_level": [int(v) for v in self.zone_level],
            "phones_alerted": level.tolist(),
            "forecast_id": self.forecasts[-1]["id"] if self.forecasts else None,
        }
