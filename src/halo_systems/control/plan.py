"""The shared formation plan: where every slot is, now.

All drones (or a ground station broadcasting to them) compute the same plan
from the mission and the current directive: a virtual leader moving along
the path, the formation heading, and each drone's slot. The two controllers
differ in how they build it:

* ``reassign``: Hungarian slot assignment after every change or loss
  (improved) versus keeping slots in drone-number order (original).
* ``blend``: slots glide to a new formation at a bounded speed (improved)
  versus jumping at once (original).
* ``pace``: the leader slows while the formation is out of shape and before
  bends, and wide formations turn more slowly (improved), versus holding the
  commanded speed and turn rate (original).

With ``reassign`` and ``blend`` together every drone flies a straight line
to its new slot and all arrive at the same moment; with the assignment
minimising the sum of squared distances this is the CAPT scheme of Turpin,
Michael and Kumar (IJRR 2014), whose paths do not cross.

An airspace hold (``hold``, the operator's "aircraft inbound" order) stops the
leader and lowers the plan to a hold altitude, clear of crewed firefighting
aircraft; ``resume`` climbs back and carries on from where it stopped. A plan
that has never held flies exactly as before.
"""

from __future__ import annotations

import math

import numpy as np

from .assignment import assign_slots
from .directive import Directive
from .formations import Path, formation_offsets, rotate

PACE_DEADBAND_M = 1.0
PACE_FLOOR = 0.3
TURN_SLOT_SPEED = 5.0     # m/s of extra speed the outermost slot may need while turning
BEND_LOOKAHEAD_M = 25.0
LEADER_ACCEL = 1.5        # m/s^2
YAW_ACCEL = 0.4           # rad/s^2
YAW_GAIN = 1.5            # 1/s
# Airspace hold: the leader brakes harder than LEADER_ACCEL so the swarm stops within a few metres, and the plan's
# altitude moves at up to ALT_RATE_MPS, its rate changing by at most ALT_ACCEL_MPS2 so slot velocities never jump.
HOLD_DECEL_MPS2 = 2.0
ALT_RATE_MPS = 2.5
ALT_ACCEL_MPS2 = 1.0


def ramp(value: float, rate: float, goal: float, dt: float) -> tuple[float, float, bool]:
    """Move ``value`` toward ``goal`` at up to ALT_RATE_MPS, the rate changing by at most ALT_ACCEL_MPS2 and slowing
    to arrive without overshoot. Returns (value, rate, arrived)."""
    gap = goal - value
    want = math.copysign(min(ALT_RATE_MPS, math.sqrt(2.0 * ALT_ACCEL_MPS2 * abs(gap))), gap)
    rate += max(-ALT_ACCEL_MPS2 * dt, min(ALT_ACCEL_MPS2 * dt, want - rate))
    step = rate * dt
    if abs(gap) <= 1e-6 or (step * gap > 0 and abs(step) >= abs(gap)):
        return goal, 0.0, True
    return value + step, rate, False


class FormationPlan:
    """The virtual leader and every drone's slot, moved on once per control step.

    ``s`` is the leader's arc length along the path (m) and ``speed_now`` its
    speed (m/s); ``heading`` (rad) and ``heading_rate`` (rad/s) are the
    formation frame's heading and turn rate. ``offset`` is each drone's
    current slot in that frame, ``target`` the slot it is blending toward from
    ``blend_from``, and ``offset_rate`` the blend's rate of change.
    ``members`` are the drones the formation is built for; the slots of other
    drones go to the centre, and the controllers give those drones a zero
    command. ``yaw_rate`` is the maximum turn rate (rad/s) and ``blend_speed``
    (m/s) sets how long a blend takes.
    """

    def __init__(self, path: Path, directive: Directive, n: int, *, reassign: bool, blend: bool, pace: bool,
                 start_progress: float = 0.0, yaw_rate: float = 0.35, blend_speed: float = 5.0,
                 members: np.ndarray | None = None):
        self.path = path
        self.directive = directive
        self.n = n
        self.reassign = reassign
        self.blend = blend
        self.pace = pace
        self.s = start_progress
        self.yaw_rate = yaw_rate
        self.blend_speed = blend_speed
        self.heading = path.heading_at(self.s)
        self.heading_rate = 0.0
        self.members = np.arange(n)
        self.offset = np.zeros((n, 3))          # current per-drone offset (formation frame)
        self.target = np.zeros((n, 3))          # offset being blended toward
        self.blend_from = np.zeros((n, 3))
        self.blend_t0 = 0.0
        self.blend_T = 0.0
        self.offset_rate = np.zeros((n, 3))
        self.speed_now = directive.speed_mps
        self.speed_cap = math.inf               # set by the controller (sensing-limited speed near obstacles)
        # Terrain following: an object with follow(x, y) -> (height, dh/dx, dh/dy), or None for flat
        # ground. The directive's altitude is then height above that surface.
        self.surface = None
        self.changes = 0
        # Airspace hold: ``hold_alt_m`` is the hold altitude (above the followed surface) while holding, else None.
        # ``alt_now`` is the plan's ramped altitude while holding or climbing back afterwards (None: the directive's
        # altitude, as always), ``alt_rate`` its rate of change, and ``_recover`` marks the leader speeding up again.
        self.hold_alt_m: float | None = None
        self.alt_now: float | None = None
        self.alt_rate = 0.0
        self._recover = False
        # ``members`` (sorted drone indices) builds the plan for a subset of the n drones, as one squad of several
        # does (control/squads.py); None is every drone.
        self._rebuild(np.arange(n) if members is None else np.asarray(members, dtype=int), None, t=0.0, initial=True)

    # -- membership and directives ---------------------------------------
    def set_members(self, members: np.ndarray, positions: np.ndarray | None, t: float) -> None:
        members = np.asarray(members, dtype=int)
        if np.array_equal(members, self.members):
            return
        self._rebuild(members, positions, t)

    def set_directive(self, directive: Directive, positions: np.ndarray | None, t: float) -> None:
        if directive.same_plan(self.directive):
            self.directive = directive
            return
        self.directive = directive
        self._rebuild(self.members, positions, t)

    def _rebuild(self, members, positions, t, initial=False):
        d = self.directive
        k = len(members)
        slots = formation_offsets(d.formation, k, d.spacing_m)
        if k and self.reassign and positions is not None and not initial:
            # Current positions expressed in the formation frame.
            rel = self._to_frame(positions[members])
            order = assign_slots(rel, slots)
        else:
            order = np.arange(k)
        new_target = np.zeros((self.n, 3))
        new_target[members] = slots[order]
        if initial or not self.blend:
            self.offset = new_target.copy()
            self.blend_T = 0.0
        else:
            self.blend_from = self.offset.copy()
            # Drones joining or leaving start from where they are.
            if positions is not None:
                rel_all = self._to_frame(positions)
                fresh = ~np.isin(members, self.members)
                self.blend_from[members[fresh]] = rel_all[members[fresh]]
            disp = np.linalg.norm(new_target[members] - self.blend_from[members], axis=1) if k else np.zeros(1)
            # One duration for every drone, set by the farthest moving at blend_speed on average (at least
            # 1 s), so all arrive together, as the CAPT scheme needs.
            self.blend_T = float(max(1.0, disp.max() / self.blend_speed)) if k else 0.0
            self.blend_t0 = t
        self.target = new_target
        self.members = np.asarray(members, dtype=int)
        self.changes += 0 if initial else 1

    def adopt(self, positions: np.ndarray, t: float) -> None:
        """Start a blend from where the members are now to their slots (a squad given a new route mid-flight)."""
        m = self.members
        if not len(m):
            return
        rel = self._to_frame(positions)
        self.offset = np.zeros((self.n, 3))
        self.offset[m] = rel[m]
        if self.reassign:
            order = assign_slots(rel[m], formation_offsets(self.directive.formation, len(m), self.directive.spacing_m))
            self.target = np.zeros((self.n, 3))
            self.target[m] = formation_offsets(self.directive.formation, len(m), self.directive.spacing_m)[order]
        if self.blend:
            self.blend_from = self.offset.copy()
            disp = np.linalg.norm(self.target[m] - self.blend_from[m], axis=1)
            self.blend_T = float(max(1.0, disp.max() / self.blend_speed))
            self.blend_t0 = t
        else:
            self.offset = self.target.copy()

    # -- airspace hold ------------------------------------------------------
    @property
    def holding(self) -> bool:
        return self.hold_alt_m is not None

    def altitude_now(self, positions: np.ndarray | None = None, active: np.ndarray | None = None) -> float:
        """The plan's altitude above the followed surface now: the ramp's while holding or climbing back after a
        hold; otherwise the members' mean height (``positions``, flying ones by ``active``), so a hold ordered while
        the drones are still climbing to a new directive altitude starts where they are, capped at the directive's
        altitude (the directive's altitude itself without positions)."""
        if self.alt_now is not None:
            return self.alt_now
        alt = float(self.directive.altitude_m)
        m = self.members
        if positions is None or not len(m):
            return alt
        if active is not None:
            m = m[np.asarray(active, dtype=bool)[m]]
            if not len(m):
                return alt
        p = positions[m]
        ground = self.surface.follow(p[:, 0], p[:, 1])[0] if self.surface is not None else 0.0
        return min(alt, float(np.mean(p[:, 2] - ground)))

    def hold(self, altitude_m: float, positions: np.ndarray | None = None, active: np.ndarray | None = None) -> None:
        """Stop where the formation is and descend to ``altitude_m`` above the followed surface (or the directive's
        altitude, if lower). The descent starts from ``altitude_now(positions, active)`` and never from a climb:
        any climb in progress (climbing back after an earlier hold) stops at once. A second hold while holding
        changes the hold altitude."""
        if self.alt_now is None:
            self.alt_now = self.altitude_now(positions, active)
        self.alt_rate = min(self.alt_rate, 0.0)
        self.hold_alt_m = float(altitude_m)
        self._recover = False

    def resume(self) -> None:
        """End the hold: climb back to the directive's altitude, then speed up and carry on along the path."""
        self.hold_alt_m = None

    def copy_hold(self, other: "FormationPlan") -> None:
        """Take over another plan's hold state (a squad given a new circuit mid-flight)."""
        self.hold_alt_m, self.alt_now, self.alt_rate, self._recover = \
            other.hold_alt_m, other.alt_now, other.alt_rate, other._recover

    def _hold_speed(self, speed: float, dt: float) -> float:
        if self.hold_alt_m is not None or self.alt_now is not None:
            # Holding, or climbing back to the directive's altitude after a hold: brake to a stop and stay there.
            return min(speed, max(0.0, self.speed_now - HOLD_DECEL_MPS2 * dt))
        # Back at the directive's altitude: speed up at no more than LEADER_ACCEL (pace or not), then fly as usual.
        limit = self.speed_now + LEADER_ACCEL * dt
        if speed <= limit:
            self._recover = False
            return speed
        return limit

    def _ramp_altitude(self, dt: float) -> None:
        goal = self.directive.altitude_m if self.hold_alt_m is None else min(self.hold_alt_m, self.directive.altitude_m)
        self.alt_now, self.alt_rate, arrived = ramp(self.alt_now, self.alt_rate, goal, dt)
        if arrived and self.hold_alt_m is None:
            # Back at the directive's altitude: the plan follows the directive again; the leader speeds up next.
            self.alt_now, self.alt_rate, self._recover = None, 0.0, True

    def _to_frame(self, positions: np.ndarray) -> np.ndarray:
        centre = self.centre()
        rel = positions - centre[None, :]
        rel = rotate(rel, -self.heading)
        rel[:, 2] = 0.0
        return rel

    # -- time evolution ------------------------------------------------------
    def centre(self) -> np.ndarray:
        xy, _ = self.path.at(self.s)
        # z is height above the followed surface, not world height; slots() adds the terrain under each slot.
        return np.array([xy[0], xy[1], self.directive.altitude_m if self.alt_now is None else self.alt_now])

    def advance(self, dt: float, t: float, formation_error: float = 0.0) -> None:
        """Move the leader, heading and slot offsets on by ``dt`` seconds.

        ``formation_error`` is the shape error (m) that ``pace`` slows for. In order:

        1. Leader speed: the directive's speed, capped by ``speed_cap``.
        2. With ``pace``: slow for shape error, lower the turn rate of wide
           formations, slow before bends, and change speed by at most
           LEADER_ACCEL per second.
        3. Brake to stop at the end of an open path, then move the leader on.
        4. Turn the heading toward the path at up to ``yaw_rate``; with
           ``pace`` the turn rate itself changes by at most YAW_ACCEL per
           second.
        5. Move the slot offsets along any blend in progress.

        During an airspace hold the leader instead brakes to a stop at HOLD_DECEL_MPS2, the heading stops
        turning and the altitude ramps down to the hold altitude; after ``resume`` the altitude ramps back up
        and only then does the leader speed up again (at most LEADER_ACCEL).
        """
        d = self.directive
        speed = min(d.speed_mps, self.speed_cap)
        yaw_rate = self.yaw_rate
        if self.pace:
            # Slow the leader while the formation is more than a metre out of shape,
            # but never below 30% so stragglers stuck behind obstacles cannot halt it.
            # Speed halves when the error is 1.5 m past the deadband.
            if formation_error > 0:
                speed = speed * max(PACE_FLOOR, 1.0 / (1.0 + max(0.0, formation_error - PACE_DEADBAND_M) / 1.5))
            # Turn a wide formation more slowly, so its outer drones need at most
            # TURN_SLOT_SPEED extra, and slow the leader before bends to match.
            half_width = self.half_span()
            yaw_rate = min(yaw_rate, TURN_SLOT_SPEED / max(half_width, 1.0))
            # Slow before a bend so the next BEND_LOOKAHEAD_M take at least bend / yaw_rate seconds, the time
            # the formation needs to turn, but never below PACE_FLOOR x the directive's speed.
            bend = abs(self._heading_change(self.s, self.s + BEND_LOOKAHEAD_M))
            if bend > 1e-3:
                speed = min(speed, max(PACE_FLOOR * d.speed_mps, yaw_rate * BEND_LOOKAHEAD_M / bend))
        if self.pace:
            # Smooth leader speed changes: at most LEADER_ACCEL m/s^2 either way.
            speed = min(max(speed, self.speed_now - LEADER_ACCEL * dt), self.speed_now + LEADER_ACCEL * dt)
        still = self.hold_alt_m is not None or self.alt_now is not None
        if still or self._recover:
            speed = self._hold_speed(speed, dt)
        if self.path.length > 0 and not self.path.loop:
            # Brake to stop at the end of the path (about 1 m/s^2).
            speed = min(speed, math.sqrt(2 * 1.0 * max(self.path.length - self.s, 0.0)))
        self.speed_now = speed
        self.s += speed * dt
        target_heading = self.path.heading_at(self.s)
        # The signed turn to the path's heading, wrapped to [-pi, pi) so the formation turns the short way.
        diff = (target_heading - self.heading + math.pi) % (2 * math.pi) - math.pi
        if self.pace:
            # Second-order heading: the turn rate itself changes at most YAW_ACCEL rad/s^2,
            # so slot velocities never jump.
            want = max(-yaw_rate, min(yaw_rate, YAW_GAIN * diff))
            if still:
                want = 0.0          # holding: the formation keeps its heading
            self.heading_rate += max(-YAW_ACCEL * dt, min(YAW_ACCEL * dt, want - self.heading_rate))
            self.heading += self.heading_rate * dt
        else:
            step = max(-yaw_rate * dt, min(yaw_rate * dt, diff))
            if still:
                step = 0.0
            self.heading_rate = step / dt
            self.heading += step
        if self.blend and self.blend_T > 0:
            u = min(1.0, (t - self.blend_t0) / self.blend_T)
            f = u * u * (3 - 2 * u)  # smoothstep: zero speed at both ends
            self.offset = self.blend_from + f * (self.target - self.blend_from)
            self.offset_rate = (self.target - self.blend_from) * (6 * u * (1 - u) / self.blend_T)
            if u >= 1.0:
                self.blend_T = 0.0
                self.offset_rate = np.zeros((self.n, 3))
        else:
            self.offset = self.target.copy()
            self.offset_rate = np.zeros((self.n, 3))
        if self.alt_now is not None:
            self._ramp_altitude(dt)

    def _heading_change(self, s0: float, s1: float) -> float:
        a, b = self.path.heading_at(s0), self.path.heading_at(s1)
        return (b - a + math.pi) % (2 * math.pi) - math.pi

    def half_span(self) -> float:
        """Largest distance of any member's target slot from the formation centre."""
        if len(self.members) == 0:
            return 0.0
        return float(np.max(np.linalg.norm(self.target[self.members, :2], axis=1)))

    def done(self) -> bool:
        return (not self.path.loop) and self.s >= self.path.length - 0.05

    def slots(self):
        """World position and velocity of every drone's slot, (n, 3) each."""
        centre = self.centre()
        _, tangent = self.path.at(self.s)
        world_off = rotate(self.offset, self.heading)
        pos = centre[None, :] + world_off
        # Slot velocity: the leader's, plus the frame's rotation (omega cross the offset), plus the blend's
        # own rate; over terrain the slot also climbs and sinks with the slope beneath it.
        vel_c = np.array([tangent[0] * self.speed_now, tangent[1] * self.speed_now, 0.0])
        if self.alt_now is not None:
            vel_c[2] = self.alt_rate
        omega = self.heading_rate
        rot_v = np.stack([-omega * world_off[:, 1], omega * world_off[:, 0], np.zeros(self.n)], axis=1)
        vel = vel_c[None, :] + rot_v + rotate(self.offset_rate, self.heading)
        if self.surface is not None:
            h, hx, hy = self.surface.follow(pos[:, 0], pos[:, 1])
            pos[:, 2] += h
            vel[:, 2] += hx * vel[:, 0] + hy * vel[:, 1]
        return pos, vel

    def width(self) -> float:
        if len(self.members) == 0:
            return 0.0
        return float(np.ptp(self.target[self.members, 1]))
