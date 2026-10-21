"""Squads: the swarm split into groups, each flying its own formation over its own area.

An operator gives each area a number of drones (or asks for ``auto``); each area's drones fly as one squad, a line
abreast no wider than the area. Each squad patrols its area on a closed circuit of lanes along x (along the
contours on the slopes), so it keeps looking again until the patrol ends.

``SquadPlan`` holds one ``FormationPlan`` per squad over the same n drones (each built for its own members) and
answers the questions the controllers and the simulator ask of a plan. The simulator branches on
``isinstance(plan, SquadPlan)`` wherever pooling all drones into one formation would be wrong (formation error,
the supervisor's report, the stream); with no squads nothing changes.

Moving drones between squads mid-flight (``move``): the movers leave their squad at once, fly straight to the
receiving squad in the world frame, 10 m above the formation layer, and join it (a normal re-assignment and blend)
once within JOIN_RADIUS_M of its centre. Both squads' circuits are re-planned for their new sizes from where they
are, so a smaller squad leaves no unseen stripes and a bigger one does not spill out of its strip.

An airspace hold (``hold``; see plan.py) stops every squad where it is and lowers it to the hold altitude; drones in
transit stop too, their transit frozen, and descend the same way from the height each is at (never climbing on toward
the transit layer). ``resume`` climbs every part, and every drone in transit, back and they carry on.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from .directive import Directive
from .formations import Path
from .plan import FormationPlan, ramp

MIN_SQUAD = 3                # smallest squad the default split flies
MIN_LANE_PITCH_M = 12.0     # the two lanes of a one-lane circuit are at least this far apart, so it has no cusp
JOIN_RADIUS_M = 30.0        # a transiting drone joins its squad within this distance of the squad's centre
TRANSIT_LIFT_M = 10.0       # transit layer above the formation's altitude
TRANSIT_SPEED_MPS = 8.0


@dataclass
class Area:
    code: str
    name: str
    rect: tuple            # (x0, y0, x1, y1), metres

    @property
    def ha(self) -> float:
        x0, y0, x1, y1 = self.rect
        return (x1 - x0) * (y1 - y0) / 1e4

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


@dataclass
class Squad:
    """One squad: its drones (global indices), its area and its patrol circuit."""

    id: int
    code: str
    name: str
    rect: tuple
    drones: list
    waypoints: list = field(default_factory=list)
    lanes: int = 0
    lane_m: float = 0.0     # lane width: (k - 1) x spacing + swath
    pass_s: float = 0.0     # predicted time for one circuit

    def to_dict(self) -> dict:
        return {"id": self.id, "code": self.code, "name": self.name,
                "area": [round(float(v), 1) for v in self.rect], "n": len(self.drones), "members": list(self.drones),
                "lanes": self.lanes, "lane_m": round(self.lane_m, 1), "pass_s": round(self.pass_s, 1),
                "path": [[round(float(x), 1), round(float(y), 1)] for x, y in self.waypoints]}


def lane_width(k: int, spacing: float, swath: float) -> float:
    return max(k - 1, 0) * spacing + swath


def cap_for(height: float, spacing: float, swath: float) -> int:
    """Most drones one line may have and still fit across an area this tall."""
    return max(0, int(math.floor((height - swath) / spacing + 1e-9)) + 1) if height >= swath else 0


def lanes_for(k: int, height: float, spacing: float, swath: float) -> int:
    """Lanes of a k-drone circuit: enough to cover the height, rounded up to an even number so the circuit closes."""
    lanes = max(1, int(math.ceil(height / max(lane_width(k, spacing, swath), 1e-6) - 1e-9)))
    return lanes + (lanes % 2)


def breakpoints(height: float, spacing: float, swath: float, lo: int = 1) -> list[int]:
    """Squad sizes at which a circuit needs fewer lanes: the only sizes worth flying (from lo up to the cap)."""
    out, last = [], None
    for k in range(lo, cap_for(height, spacing, swath) + 1):
        lanes = lanes_for(k, height, spacing, swath)
        if lanes != last:
            out.append(k)
            last = lanes
    return out


def lane_ys(rect: tuple, k: int, spacing: float, swath: float):
    """The y of each lane of a k-drone circuit over ``rect``, south to north; returns (ys, lanes, lane width)."""
    _, y0, _, y1 = rect
    lw = lane_width(k, spacing, swath)
    lanes = lanes_for(k, y1 - y0, spacing, swath)
    lo, hi = y0 + 0.5 * lw, y1 - 0.5 * lw
    if hi - lo < MIN_LANE_PITCH_M * (lanes - 1):
        mid = 0.5 * (y0 + y1)
        lo, hi = mid - 0.5 * MIN_LANE_PITCH_M * (lanes - 1), mid + 0.5 * MIN_LANE_PITCH_M * (lanes - 1)
    return np.linspace(lo, hi, lanes), lanes, lw


def cue_lane(rect: tuple, k: int, spacing: float, swath: float, points) -> int | None:
    """The lane a squad should start on to look at ``points`` (x, y) first: the lane nearest the most of them
    (ties to the southern lane); None when no point lies in the area."""
    x0, y0, x1, y1 = rect
    inside = [(x, y) for x, y in points if x0 <= x <= x1 and y0 <= y <= y1]
    if not inside:
        return None
    ys, _, _ = lane_ys(rect, k, spacing, swath)
    votes = np.bincount([int(np.argmin(np.abs(ys - y))) for _, y in inside], minlength=len(ys))
    return int(np.argmax(votes))


def circuit(rect: tuple, k: int, spacing: float, swath: float, start_lane: int = 0, eastward: bool = True):
    """A closed patrol circuit over an area: an even number of lanes along x, the outer ones just inside it.

    Returns (waypoints, lanes, lane width). Lanes run from the south edge north and the circuit closes along the
    west edge; ``start_lane`` and ``eastward`` rotate it to begin on that lane, flown that way.
    """
    x0, y0, x1, y1 = rect
    ys, lanes, lw = lane_ys(rect, k, spacing, swath)
    pts = []
    for i, y in enumerate(ys):
        a, b = (x0, x1) if i % 2 == 0 else (x1, x0)
        pts += [(float(a), float(y)), (float(b), float(y))]
    # The loop as built (lane i runs east when i is even), or flown backwards, rotated to begin at the lane.
    lane = start_lane % lanes
    seq = pts if (lane % 2 == 0) == eastward else pts[::-1]
    j = next(j for j in range(0, len(seq), 2) if abs(seq[j][1] - ys[lane]) < 1e-9)
    return seq[j:] + seq[:j], lanes, lw


_PASS_CACHE: dict = {}


def pass_time(rect: tuple, k: int, directive: Directive, swath: float, dt: float = 0.25) -> float:
    """Seconds for a k-drone squad's leader to fly its circuit once, turns paced (the plan alone, no physics)."""
    key = (tuple(round(float(v), 3) for v in rect), k, directive.formation, directive.spacing_m, directive.speed_mps,
           round(swath, 6), dt)
    if key not in _PASS_CACHE:
        _PASS_CACHE[key] = _pass_time(rect, k, directive, swath, dt)
    return _PASS_CACHE[key]


def _pass_time(rect: tuple, k: int, directive: Directive, swath: float, dt: float) -> float:
    pts, _, _ = circuit(rect, k, directive.spacing_m, swath)
    path = Path(pts, loop=True)
    plan = FormationPlan(path, directive, k, reassign=True, blend=True, pace=True)
    t = 0.0
    while plan.s < path.length and t < 20000.0:
        plan.advance(dt, t, 0.0)
        t += dt
    return t


def allocate(n: int, areas: list[Area], directive: Directive, swath: float, patrol_s: float) -> dict:
    """The default split ("auto"): see every area once per patrol if the drones allow, then equal revisits.

    1. Areas are opened in the order given (the valley floor first), each with the smallest squad (at least
       MIN_SQUAD) that flies its circuit within the patrol, while enough drones are left.
    2. Further drones go, one size step at a time, to the area whose circuit takes longest. Sizes only take the
       steps at which a circuit needs fewer lanes (breakpoints); an area stops at its cap, the widest line that
       fits across it.
    3. Drones left over stay in reserve at the launch point (spares for failures and battery swaps).
    Returns {code: drones}, with "R" for the reserve.
    """
    spacing = directive.spacing_m
    cache: dict = {}

    def t_of(a: Area, k: int) -> float:
        if k <= 0:
            return math.inf
        if (a.code, k) not in cache:
            cache[(a.code, k)] = pass_time(a.rect, k, directive, swath)
        return cache[(a.code, k)]

    steps = {a.code: breakpoints(a.height, spacing, swath, lo=MIN_SQUAD) for a in areas}
    out = {a.code: 0 for a in areas}
    left = n
    for a in areas:
        k = next((k for k in steps[a.code] if t_of(a, k) <= patrol_s), None)
        if k is not None and k <= left:
            out[a.code], left = k, left - k
    if not any(out.values()) and n:
        first = areas[0]                     # too few drones for any full circuit: they all fly the floor
        out[first.code], left = min(n, cap_for(first.height, spacing, swath)), n - min(n, cap_for(first.height, spacing, swath))
    while True:
        grow = []
        for a in areas:
            k = out[a.code]
            if k == 0:
                continue
            nxt = next((s for s in steps[a.code] if s > k), None)
            if nxt is not None and nxt - k <= left:
                grow.append((t_of(a, k), -areas.index(a), a, nxt))
        if not grow:
            break
        _, _, a, nxt = max(grow, key=lambda g: (g[0], g[1]))
        left -= nxt - out[a.code]
        out[a.code] = nxt
    out["R"] = left
    return out


def build_squads(counts: dict, areas: list[Area], directive: Directive, swath: float,
                 starts: dict | None = None) -> list[Squad]:
    """One squad per area with drones, numbered area by area, each with its circuit. "R" (reserve) is skipped.
    ``starts`` ({code: lane}) starts a squad's circuit on that lane (see cue_lane) instead of the southern one."""
    squads, nxt = [], 0
    by_code = {a.code: a for a in areas}
    for code, m in counts.items():
        if code == "R" or m <= 0:
            continue
        if code not in by_code:
            raise ValueError(f"no area {code!r}; areas are {', '.join(by_code)}")
        a = by_code[code]
        cap = cap_for(a.height, directive.spacing_m, swath)
        if m > cap:
            raise ValueError(f"{m} drones in one line do not fit across {a.name} (at most {cap})")
        pts, lanes, lw = circuit(a.rect, m, directive.spacing_m, swath, start_lane=(starts or {}).get(code) or 0)
        squads.append(Squad(len(squads), code, a.name, a.rect, list(range(nxt, nxt + m)), pts, lanes, lw,
                            pass_time(a.rect, m, directive, swath)))
        nxt += m
    return squads


def parse_counts(spec) -> dict | str | None:
    """'auto', 'F10-N7-S7' / 'F10,N7,S7', or a dict; None or 'none' for no squads."""
    if spec is None or spec == "none":
        return None
    if isinstance(spec, dict):
        return {str(k): int(v) for k, v in spec.items()}
    if spec == "auto":
        return "auto"
    out = {}
    for part in str(spec).replace(",", "-").split("-"):
        part = part.strip()
        if not part:
            continue
        code, num = part[0].upper(), part[1:]
        if not num.isdigit():
            raise ValueError(f"bad squad spec {spec!r}: use 'auto' or e.g. 'F10-N7-S7'")
        out[code] = out.get(code, 0) + int(num)
    return out


def parse_script(text: str | None) -> list[dict]:
    """Operator orders at fixed times: "300:move F N 4; 420:move N S 2" -> [{t, op, from, to, k}, ...]."""
    out = []
    for part in (text or "").split(";"):
        part = part.strip()
        if not part:
            continue
        when, _, rest = part.partition(":")
        words = rest.split()
        if len(words) != 4 or words[0] != "move" or not words[3].isdigit():
            raise ValueError(f"bad squad order {part!r}: use e.g. '300:move F N 4'")
        out.append({"t": float(when), "op": "move", "from": words[1].upper(), "to": words[2].upper(),
                    "k": int(words[3])})
    return out


def canonical(counts: dict | None) -> str:
    """'none', or drones per area in order, e.g. 'F6-N5-S5-R0'."""
    if not counts:
        return "none"
    return "-".join(f"{k}{v}" for k, v in counts.items())


class SquadPlan:
    """One FormationPlan per squad over the same n drones; see the module docstring."""

    def __init__(self, squads: list[Squad], directive: Directive, n: int, *, reassign: bool, blend: bool,
                 pace: bool, swath: float):
        self.squads = copy.deepcopy(squads)       # the scenario keeps the layout it started with
        self.directive = directive
        self.n = n
        self.reassign, self.blend, self.pace = reassign, blend, pace
        self.swath = swath
        self.squad_of = np.full(n, -1, dtype=int)
        self.parts: list[FormationPlan] = []
        for sq in squads:
            self.squad_of[sq.drones] = sq.id
            self.parts.append(self._plan(sq, np.array(sorted(sq.drones), dtype=int)))
        self._surface = None
        self.speed_cap = math.inf          # not used: the controller sets each part's own cap
        self.transit: dict[int, dict] = {}  # drone -> {"to": squad id, "pos": (3,), "vel": (3,)}
        self._arrived: list[int] = []
        self.changes = 0
        # Airspace hold: the hold altitude while holding (None otherwise). A drone in transit during a hold carries
        # its own ramped altitude above the followed surface ("alt", "rate" in its transit record) until it is back
        # at its transit layer (directive altitude + TRANSIT_LIFT_M) after the resume.
        self.hold_alt_m: float | None = None

    def _plan(self, sq: Squad, members: np.ndarray) -> FormationPlan:
        fp = FormationPlan(Path(sq.waypoints, loop=True), self.directive, self.n, reassign=self.reassign,
                           blend=self.blend, pace=self.pace, members=members)
        fp.surface = getattr(self, "_surface", None)
        return fp

    # -- the plan interface the simulator and controllers use ------------------------------------------
    @property
    def surface(self):
        return self._surface

    @surface.setter
    def surface(self, value):
        self._surface = value
        for p in self.parts:
            p.surface = value

    @property
    def members(self) -> np.ndarray:
        return np.sort(np.concatenate([p.members for p in self.parts])) if self.parts else np.zeros(0, dtype=int)

    @property
    def s(self) -> float:
        """Distance flown by all squad leaders together (the 'progress' series)."""
        return float(sum(p.s for p in self.parts))

    @property
    def speed_now(self) -> float:
        return float(np.mean([p.speed_now for p in self.parts])) if self.parts else 0.0

    @property
    def heading(self) -> float:
        return self.largest().heading

    def largest(self) -> FormationPlan:
        return max(self.parts, key=lambda p: (len(p.members), -self.parts.index(p)))

    def width(self) -> float:
        return max((p.width() for p in self.parts), default=0.0)

    def done(self) -> bool:
        return False                      # a patrol ends on time, not at the end of a route

    @property
    def holding(self) -> bool:
        return self.hold_alt_m is not None

    def altitude_now(self, positions: np.ndarray | None = None, active: np.ndarray | None = None) -> float:
        """The lowest squad's altitude now (FormationPlan.altitude_now): a hold below it never makes a squad climb."""
        alts = [p.altitude_now(positions, active) for p in self.parts if len(p.members)]
        return min(alts) if alts else float(self.directive.altitude_m)

    def hold(self, altitude_m: float, positions: np.ndarray | None = None, active: np.ndarray | None = None) -> None:
        """Airspace hold for every squad and every drone in transit (see FormationPlan.hold). A drone in transit
        stops where it is and descends from its own height now (``positions``), never climbing on."""
        for p in self.parts:
            p.hold(altitude_m, positions, active)
        self.hold_alt_m = float(altitude_m)
        self._hold_from(positions)

    def _hold_from(self, positions: np.ndarray | None) -> None:
        """Start the hold's altitude ramp of every drone in transit that has none: from its height above the followed
        surface (``positions``, else its transit point), at most its transit layer; any climb in progress stops."""
        layer = self.directive.altitude_m + TRANSIT_LIFT_M
        for d, tr in self.transit.items():
            if "alt" not in tr:
                pos = tr["pos"] if positions is None else positions[d]
                ground = 0.0
                if self._surface is not None:
                    ground = float(self._surface.follow(np.array([pos[0]]), np.array([pos[1]]))[0][0])
                tr["alt"], tr["rate"] = min(float(layer), float(pos[2]) - ground), 0.0
            tr["rate"] = min(tr["rate"], 0.0)

    def resume(self) -> None:
        for p in self.parts:
            p.resume()
        self.hold_alt_m = None

    def part_masks(self) -> list[np.ndarray]:
        """Per squad, a boolean mask of its members (drones in transit belong to none)."""
        out = []
        for p in self.parts:
            m = np.zeros(self.n, dtype=bool)
            m[p.members] = True
            out.append(m)
        return out

    def slots(self):
        pos = np.zeros((self.n, 3))
        vel = np.zeros((self.n, 3))
        for i, p in enumerate(self.parts):
            pp, vv = p.slots()
            m = self.squad_of == i
            pos[m] = pp[m]
            vel[m] = vv[m]
        for d, tr in self.transit.items():
            pos[d], vel[d] = tr["pos"], tr["vel"]
        return pos, vel

    def advance(self, dt: float, t: float, errors=None) -> None:
        """``errors``: one shape error per squad (the improved controller's pace), or None."""
        for i, p in enumerate(self.parts):
            p.advance(dt, t, 0.0 if errors is None else float(errors[i]))
        self._advance_transit(dt)

    def set_members(self, members: np.ndarray, positions: np.ndarray | None, t: float) -> None:
        members = np.asarray(members, dtype=int)
        for i, p in enumerate(self.parts):
            sub = np.sort(members[self.squad_of[members] == i]) if len(members) else members
            p.set_members(sub, positions, t)
        gone = [d for d in self.transit if d not in set(members.tolist())]
        for d in gone:
            del self.transit[d]

    def set_directive(self, directive: Directive, positions: np.ndarray | None, t: float) -> None:
        self.directive = directive
        for p in self.parts:
            p.set_directive(directive, positions, t)

    # -- moving drones between squads ------------------------------------------------------------------------
    def replan(self, i: int, positions: np.ndarray, t: float) -> None:
        """Give squad i a circuit for its current size, starting on the lane nearest it, heading the same way."""
        sq, old = self.squads[i], self.parts[i]
        members = old.members
        if not len(members):
            return
        centre = old.centre()
        _, tangent = old.path.at(old.s)
        pts, lanes, lw = circuit(sq.rect, len(members), self.directive.spacing_m, self.swath)
        ys = sorted({y for _, y in pts})
        lane = int(np.argmin([abs(y - centre[1]) for y in ys]))
        pts, lanes, lw = circuit(sq.rect, len(members), self.directive.spacing_m, self.swath,
                                 start_lane=lane, eastward=bool(tangent[0] >= 0.0))
        sq.waypoints, sq.lanes, sq.lane_m = pts, lanes, lw
        sq.pass_s = pass_time(sq.rect, len(members), self.directive, self.swath)
        fp = self._plan(sq, members)
        # Start level with the squad on its first lane, not back at the strip's edge.
        fp.s = float(np.clip(abs(float(centre[0]) - pts[0][0]), 0.0, abs(pts[1][0] - pts[0][0])))
        fp.heading = old.heading
        fp.heading_rate = old.heading_rate
        fp.speed_now = old.speed_now
        fp.copy_hold(old)
        fp.adopt(positions, t)
        self.parts[i] = fp

    def move(self, drones: list[int], to: int, positions: np.ndarray, t: float) -> None:
        """Take these drones out of their squads and send them to squad ``to`` (they join on arrival)."""
        froms = sorted({int(self.squad_of[d]) for d in drones if self.squad_of[d] >= 0})
        for d in drones:
            src = int(self.squad_of[d])
            self.squad_of[d] = -1
            if src >= 0 and d in self.squads[src].drones:
                self.squads[src].drones.remove(d)
            self.transit[d] = {"to": to, "pos": positions[d].copy(), "vel": np.zeros(3)}
        if self.holding:
            self._hold_from(positions)          # the validator refuses moves during a hold; this is a safeguard
        for i in froms:
            p = self.parts[i]
            p.set_members(np.sort(np.array([m for m in p.members if self.squad_of[m] == i], dtype=int)), positions, t)
            self.replan(i, positions, t)
        self.changes += 1

    def _advance_transit(self, dt: float) -> None:
        if not self.transit:
            return
        arrived = []
        for d, tr in self.transit.items():
            if "alt" in tr:
                self._hold_transit(tr, dt)
                continue
            target = self.parts[tr["to"]].centre()
            gap = target[:2] - tr["pos"][:2]
            dist = float(np.linalg.norm(gap))
            if dist <= JOIN_RADIUS_M:
                arrived.append(d)
                continue
            step = min(TRANSIT_SPEED_MPS * dt, dist)
            u = gap / max(dist, 1e-9)
            xy = tr["pos"][:2] + u * step
            v = np.array([u[0] * TRANSIT_SPEED_MPS, u[1] * TRANSIT_SPEED_MPS, 0.0])
            z = self.directive.altitude_m + TRANSIT_LIFT_M
            if self._surface is not None:
                h, hx, hy = self._surface.follow(np.array([xy[0]]), np.array([xy[1]]))
                z += float(h[0])
                v[2] = float(hx[0]) * v[0] + float(hy[0]) * v[1]
            tr["pos"] = np.array([xy[0], xy[1], z])
            tr["vel"] = v
        self._arrived = arrived

    def _hold_transit(self, tr: dict, dt: float) -> None:
        """A drone in transit during a hold, or climbing back after one: it stays where it is (progress frozen, no
        horizontal speed) while its altitude ramps down to the hold altitude (never up) and, after the resume, back
        to its transit layer; there it carries on to its squad."""
        layer = self.directive.altitude_m + TRANSIT_LIFT_M
        goal = layer if self.hold_alt_m is None else min(self.hold_alt_m, layer, tr["alt"])
        tr["alt"], tr["rate"], arrived = ramp(tr["alt"], tr["rate"], goal, dt)
        x, y = tr["pos"][0], tr["pos"][1]
        z = tr["alt"]
        if self._surface is not None:
            z += float(self._surface.follow(np.array([x]), np.array([y]))[0][0])
        tr["pos"] = np.array([x, y, z])
        tr["vel"] = np.array([0.0, 0.0, tr["rate"]])
        if arrived and self.hold_alt_m is None:
            del tr["alt"], tr["rate"]

    def join_arrivals(self, positions: np.ndarray, t: float) -> list[tuple[int, int]]:
        """Make drones that reached their squad its members; returns [(drone, squad)]. Called by the simulator."""
        done = []
        for d in self._arrived:
            to = self.transit.pop(d)["to"]
            self.squad_of[d] = to
            self.squads[to].drones.append(d)
            done.append((d, to))
        self._arrived = []
        for to in sorted({to for _, to in done}):
            p = self.parts[to]
            p.set_members(np.sort(np.nonzero(self.squad_of == to)[0]), positions, t)
            self.replan(to, positions, t)
        return done
