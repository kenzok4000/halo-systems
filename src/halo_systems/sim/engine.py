"""The simulation loop: disturbances, sensing, supervision, control, physics.

One ``Simulation`` is one reproducible run. Four random streams are split
from the seed with ``numpy.random.SeedSequence``: wind, sensing, the start
(positions and starting charge) and the noise on the supervisor's wind
reading. Two controllers run with the same seed therefore face exactly the
same gusts, sensor noise and radio losses (a paired comparison).

Each step, in order: wind, motor failures, sensing and radio, formation
membership, supervisor and operator script, controller, thrust and physics,
collisions, energy, ground sensor, recording, end check.

Physics and the metrics use the true state. Controllers work from navigation
estimates and radio messages, and the status report's separation, wind and
formation error are estimates too.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..control.assignment import hungarian
from ..control.base import Controller, Observation
from ..control.directive import Directive
from ..control.formations import SHAPES, Path, formation_offsets, rotate
from ..safety.validator import Limits, SafetyValidator
from ..supervisor.base import Decision, FixedSupervisor, Supervisor
from ..supervisor.rule import CORRIDOR_BUFFER_M, interp_table
from .body import DroneBody
from .physics import autopilot_thrust, closest_approach, integrate, power_draw
from .scenario import Scenario
from .sensing import Sensing
from .wind import WindField

# Drone status, kept in Simulation.status and written to each frame's "s" (the dashboard colours drones by it):
# ACTIVE   flying in formation.
# FAILED   motor out: falls with no thrust; the formation drops it after FAILURE_DETECTION_S.
# CRASHED  hit another drone, or hit an obstacle or the ground while its motors still worked.
# RETIRED  left the formation at the battery reserve.
# DOWN     a failed drone that has reached an obstacle or the ground (not counted as a crash).
# CRASHED, RETIRED and DOWN drones are no longer moved or checked for collisions; they stay where they were.
ACTIVE, FAILED, CRASHED, RETIRED, DOWN = 0, 1, 2, 3, 4
STATUS_NAMES = {ACTIVE: "active", FAILED: "failed", CRASHED: "crashed", RETIRED: "retired", DOWN: "down"}
NEAR_MISS_EXTRA_M = 0.5
FAILURE_DETECTION_S = 1.0
LOOKAHEAD_M = 100.0          # how far ahead of the formation centre the supervisor summary looks
INTEGRITY_EVERY = 5          # steps between formation-integrity evaluations (10 Hz)
SENSE_EVERY = 5              # steps between ground-sensor updates (10 Hz, a thermal camera's frame rate)
MIN_VISIBLE = 0.15           # a hotspot with less of it visible from above than this is never confirmed by the camera


@dataclass
class RunResult:
    scenario: dict
    controller: str
    supervisor: str
    seed: int
    metrics: dict
    events: list
    directives: list
    validator_log: list
    ai_calls: list
    series: dict = field(default_factory=dict)
    frames: list = field(default_factory=list)
    world: dict = field(default_factory=dict)

    def to_dict(self, include_frames: bool = True, include_series: bool = False) -> dict:
        out = {
            "scenario": self.scenario,
            "controller": self.controller,
            "supervisor": self.supervisor,
            "seed": self.seed,
            "metrics": self.metrics,
            "events": self.events,
            "directives": self.directives,
            "validator_log": self.validator_log,
            "ai_calls": self.ai_calls,
            "world": self.world,
        }
        if include_frames:
            out["frames"] = self.frames
        if include_series:
            out["series"] = {k: [round(float(x), 4) for x in v] for k, v in self.series.items()}
        return out


def energy_table(body: DroneBody, headwind: float, speed_limits=(0.5, 12.0)) -> dict:
    """Energy per drone per ground kilometre at a few cruise speeds, in steady level flight."""
    out = {}
    lo, hi = speed_limits
    for v in (3, 4, 5, 6, 8, 10, 12):
        if v < lo or v > hi:
            continue
        air = max(v + headwind, 0.0)
        out[str(v)] = round(float(body.level_power_w(air)) / (3.6 * v), 2)
    return out


class Simulation:
    """One run: the true state, the logs and the step loop.

    True state: ``p``, ``v`` and ``thrust`` (n, 3), ``status`` (the codes
    above) and ``battery_wh`` (n,). Logs: ``events``, ``directives``,
    ``ai_calls``, ``frames`` (at ``record_hz``) and ``series`` (every step,
    for the metrics). The dashboard and the evaluation use ``step``, ``run``,
    ``result``, ``summary``, ``frame`` and ``inject``.
    """

    def __init__(self, scenario: Scenario, controller: Controller, supervisor: Supervisor | None = None,
                 seed: int = 0, body: DroneBody | None = None, record_hz: float = 10.0,
                 validator: SafetyValidator | None = None):
        self.sc = scenario
        self.body = body or DroneBody()
        self.ctrl = controller
        self.sup = supervisor or FixedSupervisor()
        self.seed = seed
        self.dt = scenario.dt
        self.n = n = scenario.n
        ss = np.random.SeedSequence(seed)
        r_wind, r_sense, r_init, r_misc = (np.random.default_rng(s) for s in ss.spawn(4))
        self.rng_misc = r_misc
        self.wind = WindField(scenario.wind, n, r_wind)
        self.sensing = Sensing(scenario.sensing, n, self.dt, r_sense)
        nav_sigma = math.hypot(scenario.sensing.gps_sigma_m, scenario.sensing.gps_bias_sigma_m)
        limits = Limits(altitude_m=(5.0, scenario.altitude_max_m))
        self.validator = validator or SafetyValidator(self.body.radius_m, nav_sigma_m=nav_sigma, limits=limits)
        self.path = Path(scenario.waypoints, loop=scenario.loop)
        # The scenario's directive passes the same validator as any other, so it can be clamped too; its
        # validator log entry has t = -1e9. The rate-limit clock is then reset, so the first supervisor
        # or operator change is not held back by the mission.
        start = self.validator.check(scenario.directive, scenario.directive, -1e9, "mission")
        self.directive: Directive = start.directive
        self.mission_directive: Directive = start.directive
        self.validator.last_change_t = -math.inf
        self.ctrl.nav_sigma_m = nav_sigma
        # Squads (control/squads.py): several formations, each patrolling its own area. Every squad-only branch
        # below is guarded by self.squads, so runs without squads are unchanged.
        self.squads = bool(scenario.squads)
        if self.squads:
            swath = 2.0 * float(scenario.sensor.footprint_radius(self.directive.altitude_m))
            self.ctrl.reset_squads(scenario.squads, self.directive, n, swath)
            self._squad_orders = sorted(scenario.squad_script, key=lambda o: o["t"])
        else:
            self.ctrl.reset(self.path, self.directive, n)
        pos, vel = self.ctrl.plan.slots()
        self.p = pos + r_init.normal(0.0, 0.3, size=(n, 3))
        self.v = vel.copy()
        hover = np.zeros((n, 3))
        hover[:, 2] = self.body.mass_kg * 9.81
        self.thrust = hover
        self.status = np.full(n, ACTIVE)
        frac = scenario.battery_fraction * (1.0 + r_init.uniform(-scenario.battery_spread, scenario.battery_spread, n))
        self.capacity_wh = self.body.battery_wh
        self.battery_wh = np.clip(frac, 0.01, 1.0) * self.capacity_wh
        self.energy_wh = np.zeros(n)
        self.distance_m = np.zeros(n)
        self.t = 0.0
        self.k = 0
        self.events: list[dict] = []
        self.directives: list[dict] = [{"t": 0.0, "action": "accepted", **self.directive.to_dict()}]
        self.ai_calls: list[dict] = []
        self.pending: list[tuple[float, float, Decision]] = []
        self.next_supervise = scenario.supervisor_period_s if self.sup.name != "fixed" else math.inf
        self.failed_at: dict[int, float] = {}
        self.members_known = np.ones(n, dtype=bool)
        self.record_every = max(1, int(round(1.0 / (record_hz * self.dt)))) if record_hz > 0 else 0
        self.frames: list[dict] = []
        self.series = {k: [] for k in ("t", "rms_error", "shape_error", "integrity_error", "max_error", "min_sep",
                                       "active", "power_w", "leader_speed", "progress_m", "ctrl_ms",
                                       "speed_over_plan", "targets_found", "coverage_pct")}
        # Ground sensing: targets to find and the survey area's coverage grid.
        sensor = scenario.sensor
        self.targets = np.array([[t.x, t.y, t.radius_m] for t in scenario.targets], dtype=float).reshape(-1, 3)
        self.target_seen_s = np.zeros(len(self.targets))
        self.target_view_s = np.zeros(len(self.targets))   # time in some camera's footprint, hidden or not (scoring)
        self.target_found_t = np.full(len(self.targets), np.nan)
        self.target_found_by = np.full(len(self.targets), -1)
        # Share of each target a camera looking down can see; only the wildfire valley hides any.
        cover = np.array([t.cover for t in scenario.targets], dtype=float)
        self.target_visible = (1.0 - cover) if cover.any() else None
        self.coverage: np.ndarray | None = None
        if sensor.enabled and sensor.area:
            x0, y0, x1, y1 = sensor.area
            nx = max(1, int(math.ceil((x1 - x0) / sensor.cell_m)))
            ny = max(1, int(math.ceil((y1 - y0) / sensor.cell_m)))
            self.coverage = np.zeros((nx, ny), dtype=bool)
            self._cover_x = x0 + (np.arange(nx) + 0.5) * sensor.cell_m
            self._cover_y = y0 + (np.arange(ny) + 0.5) * sensor.cell_m
        self._integrity = 0.0
        self.done_t = None
        self.ended = False
        self._near = np.zeros((n, n), dtype=bool)
        self.near_miss_events = 0
        self.collisions = 0
        self.obstacle_crashes = 0
        self.ground_crashes = 0
        self._sep_window = deque()
        self._wind_window = deque()
        self._near_window = deque()
        self._ambient_est = np.zeros(3)
        self.obs: Observation | None = None
        self.wall_start = time.perf_counter()
        self._script = sorted(scenario.script, key=lambda s: s.t)
        self._failures = sorted(scenario.failures, key=lambda f: f.t)
        # Fires, smoke, forecasts and alerts (the wildfire valley only).
        self.wildfire = None
        if scenario.wildfire is not None:
            from .wildfire import Wildfire
            self.wildfire = Wildfire(self)

    # ------------------------------------------------------------------------
    def event(self, kind: str, drone: int | None = None, **detail) -> None:
        e = {"t": round(self.t, 2), "kind": kind}
        if drone is not None:
            e["drone"] = int(drone)
        e.update(detail)
        self.events.append(e)

    def _wind_at(self, t: float, airborne: np.ndarray) -> np.ndarray:
        # Gust fronts sweep across the drones that are flying, timed on their centre. WindField.at(t, positions)
        # would centre the sweep on all drones, landed ones included; here drones not flying get no front wind.
        # The front is subtracted and added back; summing afresh would round differently and change recorded runs.
        wind = self.wind.at(t)
        if self.sc.wind.fronts:
            wind = wind - self.wind.front_vector(t)
            wind[airborne] += self.wind.front_field(t, self.p[airborne])
        return wind

    def active_mask(self) -> np.ndarray:
        return self.status == ACTIVE

    def airborne_mask(self) -> np.ndarray:
        return (self.status == ACTIVE) | (self.status == FAILED)

    # ------------------------------------------------------------------------
    def step(self) -> None:
        dt = self.dt
        self.t = round(self.t + dt, 10)
        self.k += 1
        t = self.t
        n = self.n

        # Disturbances.
        self.wind.step(dt)
        wind = self._wind_at(t, self.airborne_mask())
        while self._failures and self._failures[0].t <= t:
            f = self._failures.pop(0)
            if 0 <= f.drone < n and self.status[f.drone] == ACTIVE:
                self.status[f.drone] = FAILED
                self.failed_at[f.drone] = t
                self.event("motor_failure", f.drone)
        for b in self.sc.sensing.blackouts:
            if abs(b.start_s - t) < dt / 2:
                self.event("comms_blackout", None, drones=list(b.drones), until_s=b.end_s)
        for front in self.sc.wind.fronts:
            if abs(front.start_s - t) < dt / 2:
                self.event("gust_front", None, velocity_mps=list(front.velocity_mps), duration_s=front.duration_s)

        # Sensing and radio.
        active = self.active_mask()
        p_est, v_est = self.sensing.estimate(self.p, self.v)
        nbr_pos, nbr_vel, nbr_age = self.sensing.exchange(t, p_est, v_est, active, self.p)
        # The swarm learns of a failure once its heartbeat has been missing for a second.
        known = active.copy()
        for d, tf in self.failed_at.items():
            if t - tf < FAILURE_DETECTION_S:
                known[d] = True
        obs = Observation(t, p_est, v_est, nbr_pos, nbr_vel, nbr_age, known & active)
        obs.active = active  # drones fly only if they are actually flying
        self.obs = obs
        if not np.array_equal(known, self.members_known):
            self.members_known = known
            self.ctrl.update_members(Observation(t, p_est, v_est, nbr_pos, nbr_vel, nbr_age, known))

        # Supervision (slow loop) and operator script.
        self._supervise(obs)
        while self._script and self._script[0].t <= t:
            s = self._script.pop(0)
            proposal = {**self.directive.to_dict(), **s.changes}
            self._apply(proposal, "operator", obs)

        if self.squads:
            while self._squad_orders and self._squad_orders[0]["t"] <= t:
                self.squad_order(self._squad_orders.pop(0), obs)

        # Control (fast loop).
        a_cmd = self.ctrl.command(obs, dt)
        if self.squads:
            for d, to in self.ctrl.plan.join_arrivals(p_est, t):
                sq = self.ctrl.plan.squads[to]
                self.event("squad_joined", d, squad=sq.code, squad_now=sq.to_dict())

        # Physics.
        powered = active.copy()
        thrust_cmd = autopilot_thrust(a_cmd, v_est, self.body)
        p0 = self.p.copy()
        airborne = self.airborne_mask()
        p1, v1, thrust, accel = integrate(self.p, self.v, self.thrust, thrust_cmd, wind, powered, self.body, dt)
        self.p = np.where(airborne[:, None], p1, self.p)
        self.v = np.where(airborne[:, None], v1, 0.0)
        self.thrust = thrust
        self.distance_m += np.where(active, np.linalg.norm(self.p - p0, axis=1), 0.0)

        self._collisions(p0, airborne)
        self._energy(thrust, powered, np.linalg.norm(self.v - wind, axis=1))
        if self.wildfire is not None and self.k % SENSE_EVERY == 0:
            self.wildfire.tick(self, SENSE_EVERY * dt)
        if self.sc.sensor.enabled and self.k % SENSE_EVERY == 0:
            self._sense_ground(SENSE_EVERY * dt)
        self._record(obs)
        self._check_end()

    def _sense_ground(self, elapsed: float) -> None:
        """Targets in some flying drone's camera footprint accumulate dwell; covered cells are marked."""
        act = self.active_mask()
        if not act.any():
            return
        p = self.p[act]
        terrain = self.sc.world.terrain
        foot = self.sc.sensor.footprint_radius(p[:, 2] if terrain is None else p[:, 2] - terrain.height(p[:, 0], p[:, 1]))
        if len(self.targets):
            d = np.hypot(p[:, None, 0] - self.targets[None, :, 0], p[:, None, 1] - self.targets[None, :, 1])
            in_view = d - self.targets[None, :, 2] <= foot[:, None]
            seen = in_view.any(axis=0)
            self.target_view_s[seen] += elapsed
            pending = np.isnan(self.target_found_t)
            if self.target_visible is None:
                self.target_seen_s[seen & pending] += elapsed
            else:
                # A partly hidden hotspot takes longer to confirm; one mostly hidden is never seen from above.
                vis = self.target_visible
                gain = seen & pending & (vis >= MIN_VISIBLE)
                self.target_seen_s[gain] += elapsed * vis[gain]
            found = pending & (self.target_seen_s >= self.sc.sensor.dwell_s - 1e-9)
            drones = np.nonzero(act)[0]
            for j in np.nonzero(found)[0]:
                by = int(drones[np.argmin(np.where(in_view[:, j], d[:, j], np.inf))])
                self.target_found_t[j] = self.t
                self.target_found_by[j] = by
                self.event("target_found", by, target=int(j), what=self.sc.targets[j].kind,
                           x=round(float(self.targets[j, 0]), 1), y=round(float(self.targets[j, 1]), 1))
        if self.coverage is not None:
            cell = self.sc.sensor.cell_m
            x0, y0 = self._cover_x[0] - 0.5 * cell, self._cover_y[0] - 0.5 * cell
            nx, ny = self.coverage.shape
            for (x, y, _), r in zip(p, foot):
                if r <= 0.0:
                    continue
                i0, i1 = max(0, int((x - r - x0) // cell)), min(nx, int((x + r - x0) // cell) + 1)
                j0, j1 = max(0, int((y - r - y0) // cell)), min(ny, int((y + r - y0) // cell) + 1)
                if i0 >= i1 or j0 >= j1:
                    continue
                cx = self._cover_x[i0:i1, None]
                cy = self._cover_y[None, j0:j1]
                self.coverage[i0:i1, j0:j1] |= (cx - x) ** 2 + (cy - y) ** 2 <= r * r

    @property
    def coverage_pct(self) -> float | None:
        return round(100.0 * float(self.coverage.mean()), 2) if self.coverage is not None else None

    @property
    def targets_found(self) -> int:
        return int(np.sum(~np.isnan(self.target_found_t)))

    # ------------------------------------------------------------------------
    def _supervise(self, obs: Observation) -> None:
        t = self.t
        # One request in flight at a time: a slow answer delays the next request rather than
        # overlapping it, so answers can never arrive out of order or override a newer one.
        if t >= self.next_supervise and not self.pending:
            self.next_supervise += self.sc.supervisor_period_s
            summary = self.summary(obs)
            decision = self.sup.decide(summary, self.directive, t)
            if decision is not None:
                self.pending.append((t + max(decision.latency_s, 0.0), t, decision))
                if decision.meta:
                    self.ai_calls.append({"t_request": round(t, 2), "latency_s": round(decision.latency_s, 3), **decision.meta})
        ready = [x for x in self.pending if x[0] <= t]
        self.pending = [x for x in self.pending if x[0] > t]
        for _, t_req, decision in ready:
            if decision.directive is None:
                continue
            proposal = decision.directive.to_dict() if isinstance(decision.directive, Directive) else decision.directive
            source = "fallback" if decision.meta.get("fallback") else self.sup.name
            self._apply(proposal, source, obs, requested_t=t_req)

    def _apply(self, proposal: dict, source: str, obs: Observation, requested_t: float | None = None) -> None:
        result = self.validator.check(proposal, self.directive, self.t, source)
        entry = {"t": round(self.t, 2), "action": result.action, "violations": result.violations, **result.directive.to_dict()}
        if requested_t is not None:
            entry["requested_t"] = round(requested_t, 2)
        if result.ok and source == "operator":
            # An operator order replaces the mission plan that supervisors return to.
            self.mission_directive = result.directive
        if result.ok and not result.directive.same_plan(self.directive):
            self.directive = result.directive
            self.ctrl.set_directive(self.directive, obs)
            self.directives.append(entry)
            self.event("directive", None, source=source, formation=self.directive.formation,
                       spacing_m=self.directive.spacing_m, speed_mps=self.directive.speed_mps)
        elif result.ok:
            self.directive = result.directive
            if source != "rule":
                self.directives.append({**entry, "action": "unchanged"})
        else:
            self.directives.append(entry)
            self.event("directive_rejected", None, source=source, violations=result.violations)

    def _collisions(self, p0: np.ndarray, airborne: np.ndarray) -> None:
        """Count this step's collisions, near misses and obstacle and ground strikes.

        Drone-to-drone contact uses the closest approach during the step, so fast
        crossings count. A touching pair counts while at least one of the two is
        still flying, so a pile-up of three drones counts as two collisions; both
        drones become CRASHED, even one whose motor had already failed. A near
        miss counts once, when two active drones come within 2 r +
        NEAR_MISS_EXTRA_M. Obstacle and ground strikes are checked only at the
        end-of-step positions; a failed drone that strikes becomes DOWN and does
        not count as a crash.
        """
        r = self.body.radius_m
        cd = closest_approach(p0, self.p, airborne)
        hit = cd < 2 * r
        if hit.any():
            pairs = np.argwhere(np.triu(hit, 1))
            for i, j in pairs:
                if self.status[i] in (ACTIVE, FAILED) or self.status[j] in (ACTIVE, FAILED):
                    self.collisions += 1
                    self.event("collision", int(i), other=int(j), distance_m=round(float(cd[i, j]), 3))
                    for d in (i, j):
                        self.status[d] = CRASHED
                        self.v[d] = 0.0
        active = self.active_mask()
        pair_active = active[:, None] & active[None, :]
        near = (cd < 2 * r + NEAR_MISS_EXTRA_M) & pair_active
        new_near = near & ~self._near
        cnt = int(np.triu(new_near, 1).sum())
        self.near_miss_events += cnt
        if cnt:
            self._near_window.append((self.t, cnt))
        self._near = near
        if self.sc.world.obstacle_count:
            clear, _ = self.sc.world.clearance(self.p)
            bad = (clear < r) & self.airborne_mask()
            for d in np.nonzero(bad)[0]:
                was_failed = self.status[d] == FAILED
                self.status[d] = DOWN if was_failed else CRASHED
                self.v[d] = 0.0
                if not was_failed:
                    self.obstacle_crashes += 1
                    self.event("obstacle_crash", int(d))
        terrain = self.sc.world.terrain
        ground = 0.0 if terrain is None else terrain.height(self.p[:, 0], self.p[:, 1])
        low = (self.p[:, 2] < ground + r) & self.airborne_mask()
        for d in np.nonzero(low)[0]:
            was_failed = self.status[d] == FAILED
            self.status[d] = DOWN if was_failed else CRASHED
            self.p[d, 2] = r + (0.0 if terrain is None else float(ground[d]))
            self.v[d] = 0.0
            if not was_failed:
                self.ground_crashes += 1
                self.event("ground_crash", int(d))

    def _energy(self, thrust: np.ndarray, powered: np.ndarray, airspeed: np.ndarray) -> None:
        power = power_draw(thrust, powered, self.body, airspeed)
        used = power * self.dt / 3600.0
        self.energy_wh += used
        self.battery_wh -= used
        self._power = power
        active = self.active_mask()
        low = active & (self.battery_wh < self.sc.battery_reserve * self.capacity_wh)
        for d in np.nonzero(low)[0]:
            self.status[d] = RETIRED
            self.v[d] = 0.0
            self.event("retired_low_battery", int(d), battery_pct=round(100 * self.battery_wh[d] / self.capacity_wh, 1))

    def _record(self, obs: Observation) -> None:
        """Append this step to ``series`` and, at ``record_hz``, a frame to ``frames``.

        It also does work the run depends on, on every step even with
        ``record_hz=0``: it draws the noisy wind reading from ``rng_misc`` and
        keeps the 5 s windows (separation, wind, near misses) that ``summary``
        reads.
        """
        active = self.active_mask()
        slots, _ = self.ctrl.plan.slots()
        s = self.series
        s["t"].append(self.t)
        s["speed_over_plan"].append(self.directive.speed_mps - self.mission_directive.speed_mps)
        if active.any() and self.squads:
            err = np.linalg.norm(self.p[active] - slots[active], axis=1)
            s["rms_error"].append(float(np.sqrt(np.mean(err ** 2))))
            s["max_error"].append(float(err.max()))
            s["shape_error"].append(self._pooled(lambda m: self._shape_error(m, slots)))
            if self.k % INTEGRITY_EVERY == 1 or INTEGRITY_EVERY == 1:
                self._integrity = self._pooled(self._integrity_of)
            s["integrity_error"].append(self._integrity)
        elif active.any():
            err = np.linalg.norm(self.p[active] - slots[active], axis=1)
            rel = (self.p[active] - self.p[active].mean(axis=0)) - (slots[active] - slots[active].mean(axis=0))
            s["rms_error"].append(float(np.sqrt(np.mean(err ** 2))))
            s["max_error"].append(float(err.max()))
            s["shape_error"].append(float(np.sqrt(np.mean(np.sum(rel ** 2, axis=1)))))
            if self.k % INTEGRITY_EVERY == 1 or INTEGRITY_EVERY == 1:
                self._integrity = self.integrity_error()
            s["integrity_error"].append(self._integrity)
        else:
            s["rms_error"].append(float("nan"))
            s["max_error"].append(float("nan"))
            s["shape_error"].append(float("nan"))
            s["integrity_error"].append(float("nan"))
        if active.sum() >= 2:
            pa = self.p[active]
            d = np.linalg.norm(pa[:, None] - pa[None], axis=2)
            np.fill_diagonal(d, np.inf)
            ms = float(d.min())
        else:
            ms = float("nan")
        s["min_sep"].append(ms)
        s["active"].append(int(active.sum()))
        s["power_w"].append(float(self._power.sum()))
        s["leader_speed"].append(float(self.ctrl.plan.speed_now))
        s["progress_m"].append(float(self.ctrl.plan.s))
        s["ctrl_ms"].append(1000.0 * (self.ctrl.compute_s[-1] if self.ctrl.compute_s else 0.0))
        s["targets_found"].append(float(self.targets_found))
        s["coverage_pct"].append(float(self.coverage_pct) if self.coverage is not None else float("nan"))
        # Rolling windows for the supervisor summary (estimates only).
        est_sep = self._estimated_min_sep(obs)
        self._sep_window.append((self.t, est_sep))
        amb = self.wind.ambient(self.t) + self.rng_misc.normal(0.0, 0.3, 3)
        self._ambient_est = 0.98 * self._ambient_est + 0.02 * amb
        self._wind_window.append((self.t, amb))
        for w in (self._sep_window, self._wind_window, self._near_window):
            while w and w[0][0] < self.t - 5.0:
                w.popleft()
        if self.record_every and self.k % self.record_every == 0:
            self.frames.append(self.frame())

    def integrity_error(self) -> float:
        """RMS distance from the flying drones to the ideal formation for their number.

        The ideal is the directive's shape and spacing for the drones still
        flying, at the plan's heading, centred on the drones; drones are matched
        to ideal positions optimally. Holes left by lost drones and drones out
        of place both count, whichever slot each drone was given.
        """
        act = self.active_mask()
        k = int(act.sum())
        if k == 0:
            return float("nan")
        pa = self.p[act]
        if self.ctrl.plan.surface is not None:
            pa = pa.copy()
            pa[:, 2] -= self.ctrl.plan.surface.follow(pa[:, 0], pa[:, 1])[0]
        ideal = rotate(formation_offsets(self.directive.formation, k, self.directive.spacing_m), self.ctrl.plan.heading)
        rel = pa - pa.mean(axis=0)
        cost = np.sum((rel[:, None, :] - ideal[None, :, :]) ** 2, axis=2)
        cols = hungarian(cost)
        return float(np.sqrt(np.mean(cost[np.arange(k), cols])))

    # -- squads ---------------------------------------------------------------------------------------------------
    def _pooled(self, fn) -> float:
        """A per-squad error pooled over the flying drones: sqrt(sum k e^2 / sum k), squads with none left out."""
        act = self.active_mask()
        num, den = 0.0, 0
        for i, m in enumerate(self.ctrl.plan.part_masks()):
            k = int((m & act).sum())
            if k:
                num += k * fn(i) ** 2
                den += k
        return float(np.sqrt(num / den)) if den else float("nan")

    def _shape_error(self, i: int, slots: np.ndarray) -> float:
        a = self.ctrl.plan.part_masks()[i] & self.active_mask()
        rel = (self.p[a] - self.p[a].mean(axis=0)) - (slots[a] - slots[a].mean(axis=0))
        return float(np.sqrt(np.mean(np.sum(rel ** 2, axis=1))))

    def _integrity_of(self, i: int) -> float:
        """integrity_error for squad i alone: its flying members against the ideal line for their number."""
        part = self.ctrl.plan.parts[i]
        a = self.ctrl.plan.part_masks()[i] & self.active_mask()
        k = int(a.sum())
        pa = self.p[a].copy()
        if part.surface is not None:
            pa[:, 2] -= part.surface.follow(pa[:, 0], pa[:, 1])[0]
        ideal = rotate(formation_offsets(self.directive.formation, k, self.directive.spacing_m), part.heading)
        rel = pa - pa.mean(axis=0)
        cost = np.sum((rel[:, None, :] - ideal[None, :, :]) ** 2, axis=2)
        cols = hungarian(cost)
        return float(np.sqrt(np.mean(cost[np.arange(k), cols])))

    def squad_state(self) -> dict:
        """Squad ids per drone and each squad's state, for frames and the dashboard (squad runs only)."""
        plan = self.ctrl.plan
        act = self.active_mask()
        out = []
        for i, (sq, part, m) in enumerate(zip(plan.squads, plan.parts, plan.part_masks())):
            c = part.centre()
            out.append({"id": sq.id, "code": sq.code, "flying": int((m & act).sum()),
                        "centre": [round(float(c[0]), 1), round(float(c[1]), 1)],
                        "heading": round(float(part.heading), 3),
                        "passes": round(float(part.s / part.path.length), 2) if part.path.length > 0 else 0.0,
                        "joining": sorted(int(d) for d, tr in plan.transit.items() if tr["to"] == i)})
        return {"sq": plan.squad_of.tolist(), "squads": out}

    def squad_order(self, order: dict, obs: Observation, source: str = "operator") -> dict:
        """Move drones between squads: {"op": "move", "from": code, "to": code, "k": n}. The k flying drones of
        the source squad nearest the receiving squad's centre go (by their own position estimates, ties by
        number). Checked by the safety validator first; returns {ok, reasons, t, drones}."""
        plan = self.ctrl.plan
        codes = {sq.code: sq.id for sq in plan.squads}
        act = self.active_mask()
        masks = plan.part_masks()
        flying = {sq.code: int((masks[sq.id] & act).sum()) for sq in plan.squads}
        swath = plan.swath
        from ..control.squads import cap_for
        caps = {sq.code: cap_for(sq.rect[3] - sq.rect[1], self.directive.spacing_m, swath) for sq in plan.squads}
        k = order.get("k")
        incoming = {sq.code: sum(1 for d, tr in plan.transit.items() if tr["to"] == sq.id and act[d])
                    for sq in plan.squads}
        bad = self.validator.check_squads(order, flying, caps, self.t, source, incoming)
        if bad:
            self.event("squad_change_rejected", None, by=source, order=dict(order), reasons=bad)
            return {"ok": False, "reasons": bad, "t": round(self.t, 2), "drones": []}
        src, dst = codes[order["from"]], codes[order["to"]]
        cand = np.nonzero(masks[src] & act)[0]
        target = plan.parts[dst].centre()[:2]
        dist = np.hypot(obs.p_est[cand, 0] - target[0], obs.p_est[cand, 1] - target[1])
        movers = [int(d) for d in cand[np.lexsort((cand, dist))][:k]]
        plan.move(movers, dst, obs.p_est, self.t)
        self.event("squad_changed", None, by=source, op="move", **{"from": order["from"]}, to=order["to"],
                   drones=movers, squads=[sq.to_dict() for sq in plan.squads])
        return {"ok": True, "reasons": [], "t": round(self.t, 2), "drones": movers}

    def _estimated_min_sep(self, obs: Observation) -> float:
        act = obs.active
        if act.sum() < 2:
            return float("inf")
        d = np.linalg.norm(obs.p_est[:, None, :] - obs.nbr_pos, axis=2)
        ok = np.isfinite(obs.nbr_age) & (obs.nbr_age < 1.0) & act[:, None] & act[None, :]
        np.fill_diagonal(ok, False)
        return float(np.min(np.where(ok, d, np.inf)))

    def frame(self) -> dict:
        slots, _ = self.ctrl.plan.slots()
        active = self.active_mask()
        return {
            "t": round(self.t, 2),
            "p": np.round(self.p, 2).tolist(),
            "s": self.status.tolist(),
            "slots": [[round(float(x), 2) for x in slots[i]] if active[i] else None for i in range(self.n)],
            "wind": np.round(self.wind.ambient(self.t), 2).tolist(),
            "formation": self.directive.formation,
            "battery": np.round(100 * self.battery_wh / self.capacity_wh, 1).tolist(),
            **({"found": (~np.isnan(self.target_found_t)).tolist(), "coverage_pct": self.coverage_pct}
               if self.sc.sensor.enabled else {}),
            **(self.wildfire.frame(self) if self.wildfire is not None else {}),
            **(self.squad_state() if self.squads else {}),
        }

    def _check_end(self) -> None:
        if self.t >= self.sc.duration_s - 1e-9:
            self.ended = True
        if not self.active_mask().any():
            self.ended = True
        if self.ctrl.plan.done() and self.done_t is None:
            self.done_t = self.t
            self.event("mission_complete", None)
        if self.sc.end_when_done and self.done_t is not None and self.t >= self.done_t + 5.0:
            self.ended = True

    # ------------------------------------------------------------------------
    def inject(self, kind: str, **kw) -> dict:
        """Add a disturbance or operator command during a live run (used by the dashboard).

        ``motor_failure`` (drone), ``gust`` (velocity_mps, duration_s), ``blackout``
        (drones, duration_s) or ``directive`` (changes). Unspecified values are
        drawn at random. Returns what was injected; it is also logged as an event.
        """
        from .scenario import MotorFailure, ScriptedDirective
        from .sensing import Blackout
        from .wind import GustFront
        rng = np.random.default_rng()
        start = round(self.t + self.dt, 10)
        active = np.nonzero(self.active_mask())[0]
        if kind == "motor_failure":
            drone = int(kw["drone"]) if kw.get("drone") is not None else (int(rng.choice(active)) if len(active) else 0)
            self._failures.insert(0, MotorFailure(start, drone))
            detail = {"drone": drone}
        elif kind == "gust":
            if kw.get("velocity_mps") is not None:
                vel = tuple(float(x) for x in kw["velocity_mps"])
            else:
                heading = self.ctrl.plan.heading + rng.choice([-1, 1]) * math.pi / 2
                mag = float(rng.uniform(8.0, 11.0))
                vel = (mag * math.cos(heading), mag * math.sin(heading), -1.0)
            front = GustFront(start, float(kw.get("duration_s", 5.0)), vel, ramp_s=0.6)
            self.sc.wind.fronts.append(front)
            detail = {"velocity_mps": [round(x, 1) for x in vel], "duration_s": front.duration_s}
        elif kind == "blackout":
            drones = [int(d) for d in kw["drones"]] if kw.get("drones") else \
                sorted(int(d) for d in rng.choice(active, size=max(1, len(active) // 3), replace=False)) if len(active) else []
            self.sc.sensing.blackouts.append(Blackout(start, start + float(kw.get("duration_s", 10.0)), drones))
            detail = {"drones": drones, "duration_s": float(kw.get("duration_s", 10.0))}
        elif kind == "directive":
            changes = dict(kw.get("changes") or {})
            self._script.insert(0, ScriptedDirective(start, changes))
            detail = {"changes": changes}
        elif kind == "squad":
            if not self.squads:
                raise ValueError("this run has no squads")
            # Applied at the first step at or after at_s, so a recorded take reproduces a headless run.
            at = max(float(kw["at_s"]), start) if kw.get("at_s") is not None else start
            order = {"t": round(at, 10), "op": kw.get("op", "move"), "from": kw.get("from"), "to": kw.get("to"),
                     "k": kw.get("k")}
            self._squad_orders.append(order)
            self._squad_orders.sort(key=lambda o: o["t"])
            start = order["t"]
            detail = {k: v for k, v in order.items() if k != "t"}
        else:
            raise ValueError(f"unknown injection {kind!r}")
        self.event("injected", detail.get("drone"), what=kind, **{k: v for k, v in detail.items() if k != "drone"})
        return {"kind": kind, "t": start, **detail}

    def summary(self, obs: Observation | None = None) -> dict:
        """The compact state report given to supervisors (rules and Nemotron alike).

        Sections: mission, swarm, mission_directive, current_directive,
        formation, safety, environment, comms, energy, limits,
        formation_widths_at_current_spacing_m and checks. Separation, wind,
        gusts and formation error come from the drones' estimates and a noisy
        wind reading. Lost drones, battery, near misses and collisions come from
        the simulator's own bookkeeping, and the corridor and obstacle density
        from the map.

        Nemotron gets this dict as compact JSON (``ai.prompts.supervisor_user``),
        and recorded calls are looked up by the exact prompt text. Changing a key
        name, the key order or a rounding here changes that text, so recorded
        Nemotron runs would no longer replay.
        """
        obs = obs or self.obs
        plan = self.ctrl.plan
        act = self.active_mask()
        if self.squads:
            # Squads: the corridor ahead of every squad (the narrowest counts), the largest squad for the shape.
            parts = [p for p in plan.parts if len(p.members)]
            lead = plan.largest()
        else:
            parts, lead = [plan], plan
        centre = lead.centre()
        heading = np.array([math.cos(lead.heading), math.sin(lead.heading), 0.0])
        world = self.sc.world
        lookahead = LOOKAHEAD_M
        frees, tail = [], 0.0
        for part in parts:
            c = part.centre()
            h = np.array([math.cos(part.heading), math.sin(part.heading), 0.0])
            # The corridor check covers the whole formation: from its tail to the lookahead point.
            tail_i = float(max(0.0, -np.min(part.target[part.members, 0]))) if len(part.members) else 0.0
            flight_z = self.directive.altitude_m + (0.0 if world.terrain is None else float(world.terrain.follow(c[0], c[1])[0]))
            if world.obstacle_count:
                frees.append((world.free_width_ahead(c, h, lookahead, flight_z, behind=tail_i), tail_i))
            if part is lead:
                tail = tail_i
        if frees:
            width_free, tail = min(frees) if self.squads else frees[0]
        else:
            width_free = None
        density = world.density_ahead(centre, heading, lookahead, 40.0) if world.obstacle_count else 0.0
        seps = [s for _, s in self._sep_window if math.isfinite(s)]
        winds = np.array([w for _, w in self._wind_window]) if self._wind_window else np.zeros((1, 3))
        mean_wind = self._ambient_est
        gust = float(np.max(np.linalg.norm(winds - mean_wind[None, :], axis=1))) if len(winds) else 0.0
        if obs is not None and act.sum() >= 2 and self.squads:
            # Links that matter are within a squad; squads hundreds of metres apart are often out of radio range.
            sq = plan.squad_of[act]
            ages = obs.nbr_age[np.ix_(act, act)]
            off = ~np.eye(int(act.sum()), dtype=bool) & (sq[:, None] == sq[None, :]) & (sq[:, None] >= 0)
            stale = float(np.mean(ages[off] > 1.0)) if off.any() else 0.0
            isolated = int(np.sum(np.all((ages > 1.0) | ~off, axis=1) & off.any(axis=1)))
        elif obs is not None and act.sum() >= 2:
            ages = obs.nbr_age[np.ix_(act, act)]
            off = ~np.eye(int(act.sum()), dtype=bool)
            stale = float(np.mean(ages[off] > 1.0))
            isolated = int(np.sum(np.all((ages > 1.0) | ~off, axis=1)))
        else:
            stale, isolated = 0.0, 0
        batt = 100 * self.battery_wh[act] / self.capacity_wh if act.any() else np.array([0.0])
        power = self._power[act] if act.any() else np.array([1.0])
        endurance_min = float(np.min((self.battery_wh[act] - self.sc.battery_reserve * self.capacity_wh)
                                     / np.maximum(power, 1.0) * 60.0)) if act.any() else 0.0
        if self.squads:
            # A patrol ends on time: what is left is the patrol's remaining time at the mission speed.
            remaining = max(0.0, (self.sc.patrol_s or 0.0) - self.t) * self.mission_directive.speed_mps
        else:
            remaining = max(0.0, plan.path.length - plan.s) if not plan.path.loop else None
        lost = sorted(int(d) for d in self.failed_at if self.t - self.failed_at[d] >= FAILURE_DETECTION_S)
        lost += [int(d) for d in np.nonzero(self.status == CRASHED)[0] if int(d) not in lost]
        width_now = plan.width()
        # Drones in one formation: all flying drones, or the flying members of the largest squad.
        line = int(act.sum()) if not self.squads else int((plan.part_masks()[plan.parts.index(lead)] & act).sum())
        headwind = float(-mean_wind[:2] @ heading[:2])
        wh_table = energy_table(self.body, headwind, self.validator.limits.speed_mps)
        out = {
            "time_s": round(self.t, 1),
            "mission": {
                "brief": self.sc.description,
                "progress_pct": (round(100 * self.t / self.sc.patrol_s, 1) if self.squads and self.sc.patrol_s else
                                 round(100 * plan.s / plan.path.length, 1) if not self.squads and plan.path.length > 0 and not plan.path.loop else None),
                "remaining_m": round(remaining, 1) if remaining is not None else None,
                "leader_speed_mps": round(plan.speed_now, 2),
                **({"targets_found_so_far": self.targets_found, "area_covered_pct": self.coverage_pct}
                   if self.sc.sensor.enabled else {}),
            },
            "swarm": {"flying": int(act.sum()), "planned": self.n, "lost_drones": lost,
                      "retired_low_battery": int(np.sum(self.status == RETIRED))},
            "mission_directive": {k: v for k, v in self.mission_directive.to_dict().items() if k not in ("reason", "source")},
            "current_directive": {k: v for k, v in self.directive.to_dict().items() if k not in ("reason", "source")},
            "formation": {
                "rms_error_m": round(self.ctrl.formation_error_estimate(obs), 2) if obs is not None else None,
                "width_m": round(width_now, 1),
            },
            "safety": {
                "closest_pair_m_last_5s": round(min(seps), 2) if seps else None,
                "near_misses_last_5s": int(sum(c for _, c in self._near_window)),
                "collisions_so_far": int(self.collisions),
            },
            "environment": {
                "wind_mps": round(float(np.linalg.norm(mean_wind[:2])), 1),
                "wind_from_deg": round(float((math.degrees(math.atan2(-mean_wind[1], -mean_wind[0])) + 360) % 360), 0),
                "headwind_mps": round(float(-mean_wind[:2] @ heading[:2]), 1),
                "gust_peak_mps_last_5s": round(gust, 1),
                "obstacles_per_1000m2_ahead": round(density, 2),
                "free_corridor_width_m": round(width_free, 1) if width_free is not None else None,
                "corridor_checked_from_m": round(-tail, 1),
                "lookahead_m": lookahead,
            },
            "comms": {"stale_link_fraction": round(stale, 2), "isolated_drones": isolated},
            "energy": {
                "battery_mean_pct": round(float(np.mean(batt)), 1),
                "battery_min_pct": round(float(np.min(batt)), 1),
                "endurance_left_min": round(endurance_min, 1),
                "time_to_finish_min": round(remaining / max(self.directive.speed_mps, 0.1) / 60.0, 1) if remaining is not None else None,
                "usable_wh_mean": round(float(np.mean(self.battery_wh[act] - self.sc.battery_reserve * self.capacity_wh)), 2) if act.any() else 0.0,
                "usable_wh_min": round(float(np.min(self.battery_wh[act] - self.sc.battery_reserve * self.capacity_wh)), 2) if act.any() else 0.0,
                # Energy model applied to the current headwind: what each cruise speed would cost.
                "wh_per_km_by_speed": wh_table,
            },
            "limits": {
                "formations": list(SHAPES),
                "spacing_m": list(self.validator.limits.spacing_m),
                "spacing_floor_m": round(self.validator.spacing_floor(self.directive.safety_margin_m), 2),
                "speed_mps": list(self.validator.limits.speed_mps),
                "altitude_m": list(self.validator.limits.altitude_m),
                "safety_margin_m": list(self.validator.limits.safety_margin_m),
            },
            "formation_widths_at_current_spacing_m": {
                shape: round(float(np.ptp(formation_offsets(shape, max(line, 1), self.directive.spacing_m)[:, 1])), 1)
                for shape in SHAPES
            },
        }
        out["checks"] = self._checks(line, width_free, wh_table, remaining, out["energy"]["usable_wh_mean"])
        return out

    def _checks(self, flying: int, free: float | None, wh_table: dict, remaining: float | None, usable: float) -> dict:
        """Arithmetic both supervisors would otherwise each redo: what fits the corridor, what the leg costs."""
        d, m = self.directive, self.mission_directive
        checks = {}
        if free is not None:
            # Width scales with spacing, so a unit-spacing width converts any spacing to a width.
            per_m = {s: float(np.ptp(formation_offsets(s, max(flying, 1), 1.0)[:, 1])) for s in SHAPES}
            buffer = 2 * (d.safety_margin_m + CORRIDOR_BUFFER_M)
            plan_need = per_m[m.formation] * m.spacing_m + 2 * (m.safety_margin_m + CORRIDOR_BUFFER_M)
            checks["corridor"] = {
                "free_width_m": round(free, 1),
                "needed_width_m": {s: round(per_m[s] * d.spacing_m + buffer, 1) for s in SHAPES},
                "fits_now": [s for s in SHAPES if per_m[s] * d.spacing_m + buffer <= free],
                "widest_spacing_that_fits_m": {s: (round((free - buffer) / per_m[s], 1) if per_m[s] > 0 else None)
                                               for s in SHAPES},
                "mission_plan_needs_m": round(plan_need, 1),
                "mission_plan_fits": bool(plan_need <= free),
            }
        if remaining is not None and remaining > 0 and wh_table:
            km = remaining / 1000.0
            cheapest = min(wh_table, key=lambda k: (wh_table[k], float(k)))
            checks["energy"] = {
                "usable_wh_mean": usable,
                "wh_needed_at_mission_speed": round(interp_table(wh_table, m.speed_mps) * km, 2),
                "wh_needed_at_current_speed": round(interp_table(wh_table, d.speed_mps) * km, 2),
                "cheapest_speed_mps": float(cheapest),
                "wh_needed_at_cheapest_speed": round(wh_table[cheapest] * km, 2),
            }
        return checks

    # ------------------------------------------------------------------------
    def run(self, max_wall_s: float | None = None) -> RunResult:
        while not self.ended:
            self.step()
            if max_wall_s is not None and time.perf_counter() - self.wall_start > max_wall_s:
                break
        return self.result()

    def result(self) -> RunResult:
        from ..eval.metrics import compute_metrics
        if self.wildfire is not None:
            self.wildfire.finish(self)
        metrics = compute_metrics(self)
        return RunResult(
            scenario=self.sc.summary(),
            controller=self.ctrl.name,
            supervisor=self.sup.name,
            seed=self.seed,
            metrics=metrics,
            events=self.events,
            directives=self.directives,
            validator_log=self.validator.log,
            ai_calls=self.ai_calls,
            series={k: np.asarray(v) for k, v in self.series.items()},
            frames=self.frames,
            world=self.sc.world.to_dict(),
        )
