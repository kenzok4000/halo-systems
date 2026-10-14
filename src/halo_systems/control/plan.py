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
                 start_progress: float = 0.0, yaw_rate: float = 0.35, blend_speed: float = 5.0):
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
        self._rebuild(np.arange(n), None, t=0.0, initial=True)

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
            self.blend_T = float(max(1.0, disp.max() / self.blend_speed)) if k else 0.0
            self.blend_t0 = t
        self.target = new_target
        self.members = np.asarray(members, dtype=int)
        self.changes += 0 if initial else 1

    def _to_frame(self, positions: np.ndarray) -> np.ndarray:
        centre = self.centre()
        rel = positions - centre[None, :]
        rel = rotate(rel, -self.heading)
        rel[:, 2] = 0.0
        return rel

    # -- time evolution ------------------------------------------------------
    def centre(self) -> np.ndarray:
        xy, _ = self.path.at(self.s)
        return np.array([xy[0], xy[1], self.directive.altitude_m])

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
            bend = abs(self._heading_change(self.s, self.s + BEND_LOOKAHEAD_M))
            if bend > 1e-3:
                speed = min(speed, max(PACE_FLOOR * d.speed_mps, yaw_rate * BEND_LOOKAHEAD_M / bend))
        if self.pace:
            # Smooth leader speed changes: at most LEADER_ACCEL m/s^2 either way.
            speed = min(max(speed, self.speed_now - LEADER_ACCEL * dt), self.speed_now + LEADER_ACCEL * dt)
        if self.path.length > 0 and not self.path.loop:
            # Brake to stop at the end of the path (about 1 m/s^2).
            speed = min(speed, math.sqrt(2 * 1.0 * max(self.path.length - self.s, 0.0)))
        self.speed_now = speed
        self.s += speed * dt
        target_heading = self.path.heading_at(self.s)
        diff = (target_heading - self.heading + math.pi) % (2 * math.pi) - math.pi
        if self.pace:
            # Second-order heading: the turn rate itself changes at most YAW_ACCEL rad/s^2,
            # so slot velocities never jump.
            want = max(-yaw_rate, min(yaw_rate, YAW_GAIN * diff))
            self.heading_rate += max(-YAW_ACCEL * dt, min(YAW_ACCEL * dt, want - self.heading_rate))
            self.heading += self.heading_rate * dt
        else:
            step = max(-yaw_rate * dt, min(yaw_rate * dt, diff))
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
        vel_c = np.array([tangent[0] * self.speed_now, tangent[1] * self.speed_now, 0.0])
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
