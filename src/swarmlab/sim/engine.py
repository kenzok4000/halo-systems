"""The simulation loop: disturbances, sensing, supervision, control, physics.

One ``Simulation`` is one reproducible run. Random streams for wind,
sensing, start positions and starting charge are split from the seed with
``numpy.random.SeedSequence``, so two controllers run with the same seed
face exactly the same gusts, sensor noise and radio losses (a paired
comparison).
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
from .body import DroneBody
from .physics import autopilot_thrust, closest_approach, integrate, power_draw
from .scenario import Scenario
from .sensing import Sensing
from .wind import WindField

ACTIVE, FAILED, CRASHED, RETIRED, DOWN = 0, 1, 2, 3, 4
STATUS_NAMES = {ACTIVE: "active", FAILED: "failed", CRASHED: "crashed", RETIRED: "retired", DOWN: "down"}
NEAR_MISS_EXTRA_M = 0.5
FAILURE_DETECTION_S = 1.0
INTEGRITY_EVERY = 5          # steps between formation-integrity evaluations (10 Hz)


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


class Simulation:
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
        start = self.validator.check(scenario.directive, scenario.directive, -1e9, "mission")
        self.directive: Directive = start.directive
        self.validator.last_change_t = -math.inf
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
                                       "active", "power_w", "leader_speed", "progress_m", "ctrl_ms")}
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

    # ------------------------------------------------------------------------
    def event(self, kind: str, drone: int | None = None, **detail) -> None:
        e = {"t": round(self.t, 2), "kind": kind}
        if drone is not None:
            e["drone"] = int(drone)
        e.update(detail)
        self.events.append(e)

    def _wind_at(self, t: float, airborne: np.ndarray) -> np.ndarray:
        # Gust fronts sweep across the drones that are flying, timed on their centre.
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

        # Control (fast loop).
        a_cmd = self.ctrl.command(obs, dt)

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
        self._record(obs)
        self._check_end()

    # ------------------------------------------------------------------------
    def _supervise(self, obs: Observation) -> None:
        t = self.t
        if t >= self.next_supervise:
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
            self._apply(proposal, self.sup.name, obs, requested_t=t_req)

    def _apply(self, proposal: dict, source: str, obs: Observation, requested_t: float | None = None) -> None:
        result = self.validator.check(proposal, self.directive, self.t, source)
        entry = {"t": round(self.t, 2), "action": result.action, "violations": result.violations, **result.directive.to_dict()}
        if requested_t is not None:
            entry["requested_t"] = round(requested_t, 2)
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
        low = (self.p[:, 2] < r) & self.airborne_mask()
        for d in np.nonzero(low)[0]:
            was_failed = self.status[d] == FAILED
            self.status[d] = DOWN if was_failed else CRASHED
            self.p[d, 2] = r
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
        active = self.active_mask()
        slots, _ = self.ctrl.plan.slots()
        s = self.series
        s["t"].append(self.t)
        if active.any():
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
        ideal = rotate(formation_offsets(self.directive.formation, k, self.directive.spacing_m), self.ctrl.plan.heading)
        rel = pa - pa.mean(axis=0)
        cost = np.sum((rel[:, None, :] - ideal[None, :, :]) ** 2, axis=2)
        cols = hungarian(cost)
        return float(np.sqrt(np.mean(cost[np.arange(k), cols])))

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
    def summary(self, obs: Observation | None = None) -> dict:
        """The compact state report given to supervisors (rules and Nemotron alike)."""
        obs = obs or self.obs
        plan = self.ctrl.plan
        act = self.active_mask()
        centre = plan.centre()
        heading = np.array([math.cos(plan.heading), math.sin(plan.heading), 0.0])
        world = self.sc.world
        lookahead = 60.0
        width_free = world.free_width_ahead(centre, heading, lookahead, self.directive.altitude_m) if world.obstacle_count else None
        density = world.density_ahead(centre, heading, lookahead, 40.0) if world.obstacle_count else 0.0
        seps = [s for _, s in self._sep_window if math.isfinite(s)]
        winds = np.array([w for _, w in self._wind_window]) if self._wind_window else np.zeros((1, 3))
        mean_wind = self._ambient_est
        gust = float(np.max(np.linalg.norm(winds - mean_wind[None, :], axis=1))) if len(winds) else 0.0
        if obs is not None and act.sum() >= 2:
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
        remaining = max(0.0, plan.path.length - plan.s) if not plan.path.loop else None
        lost = sorted(int(d) for d in self.failed_at if self.t - self.failed_at[d] >= FAILURE_DETECTION_S)
        lost += [int(d) for d in np.nonzero(self.status == CRASHED)[0] if int(d) not in lost]
        width_now = plan.width()
        out = {
            "time_s": round(self.t, 1),
            "mission": {
                "progress_pct": round(100 * plan.s / plan.path.length, 1) if plan.path.length > 0 and not plan.path.loop else None,
                "remaining_m": round(remaining, 1) if remaining is not None else None,
                "leader_speed_mps": round(plan.speed_now, 2),
            },
            "swarm": {"flying": int(act.sum()), "planned": self.n, "lost_drones": lost,
                      "retired_low_battery": int(np.sum(self.status == RETIRED))},
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
                "gust_peak_mps_last_5s": round(gust, 1),
                "obstacles_per_1000m2_ahead": round(density, 2),
                "free_corridor_width_ahead_m": round(width_free, 1) if width_free is not None else None,
                "lookahead_m": lookahead,
            },
            "comms": {"stale_link_fraction": round(stale, 2), "isolated_drones": isolated},
            "energy": {
                "battery_mean_pct": round(float(np.mean(batt)), 1),
                "battery_min_pct": round(float(np.min(batt)), 1),
                "endurance_left_min": round(endurance_min, 1),
                "time_to_finish_min": round(remaining / max(self.directive.speed_mps, 0.1) / 60.0, 1) if remaining is not None else None,
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
                shape: round(float(np.ptp(formation_offsets(shape, max(int(act.sum()), 1), self.directive.spacing_m)[:, 1])), 1)
                for shape in SHAPES
            },
        }
        return out

    # ------------------------------------------------------------------------
    def run(self, max_wall_s: float | None = None) -> RunResult:
        while not self.ended:
            self.step()
            if max_wall_s is not None and time.perf_counter() - self.wall_start > max_wall_s:
                break
        return self.result()

    def result(self) -> RunResult:
        from ..eval.metrics import compute_metrics
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
