"""Wind: a steady mean, correlated gusts and scheduled gust fronts.

Gusts follow an Ornstein-Uhlenbeck process (a standard, simple turbulence
model): one shared component that every drone feels, plus a smaller
per-drone component. Gust fronts are scheduled step changes with a ramp,
used for the "sudden disturbance" scenarios. A front sweeps across the
swarm at its own speed, so drones on the upwind side are hit first and the
formation is sheared, not just pushed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class GustFront:
    start_s: float
    duration_s: float
    velocity_mps: tuple[float, float, float]
    ramp_s: float = 0.5

    def factor(self, t: float) -> float:
        """0 outside the front, 1 at full strength, linear ramps at both ends."""
        if t < self.start_s or t > self.start_s + self.duration_s:
            return 0.0
        rise = (t - self.start_s) / max(self.ramp_s, 1e-9)
        fall = (self.start_s + self.duration_s - t) / max(self.ramp_s, 1e-9)
        return float(min(1.0, rise, fall))


@dataclass
class WindConfig:
    mean_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gust_sigma_mps: float = 0.0          # shared gust standard deviation
    gust_tau_s: float = 3.0              # gust correlation time
    local_sigma_mps: float = 0.0         # per-drone turbulence standard deviation
    local_tau_s: float = 1.0
    fronts: list[GustFront] = field(default_factory=list)


class WindField:
    def __init__(self, config: WindConfig, count: int, rng: np.random.Generator):
        self.config = config
        self.rng = rng
        self.count = count
        self.mean = np.asarray(config.mean_mps, dtype=float)
        self.shared = np.zeros(3)
        self.local = np.zeros((count, 3))

    def step(self, dt: float) -> None:
        c = self.config
        if c.gust_sigma_mps > 0:
            a = dt / c.gust_tau_s
            self.shared += -a * self.shared + c.gust_sigma_mps * np.sqrt(2 * a) * self.rng.standard_normal(3)
            self.shared[2] *= 0.3  # vertical gusts are weaker near the ground
        if c.local_sigma_mps > 0:
            a = dt / c.local_tau_s
            self.local += -a * self.local + c.local_sigma_mps * np.sqrt(2 * a) * self.rng.standard_normal((self.count, 3))

    def front_vector(self, t: float) -> np.ndarray:
        total = np.zeros(3)
        for front in self.config.fronts:
            f = front.factor(t)
            if f > 0:
                total += f * np.asarray(front.velocity_mps, dtype=float)
        return total

    def front_field(self, t: float, positions: np.ndarray) -> np.ndarray:
        """Gust-front wind at each position: the front reaches the swarm centre at ``start_s``."""
        out = np.zeros((len(positions), 3))
        if not self.config.fronts or len(positions) == 0:
            return out
        centre = positions.mean(axis=0)
        for front in self.config.fronts:
            vel = np.asarray(front.velocity_mps, dtype=float)
            horiz = float(np.linalg.norm(vel[:2]))
            if horiz < 1e-6:
                out += front.factor(t) * vel
                continue
            u = vel[:2] / horiz
            delay = ((positions[:, :2] - centre[:2]) @ u) / max(horiz, 5.0)
            f = np.array([front.factor(t - d) for d in delay])
            out += f[:, None] * vel[None, :]
        return out

    def at(self, t: float, positions: np.ndarray | None = None) -> np.ndarray:
        """Wind velocity felt by each drone, shape (count, 3)."""
        if positions is None:
            return self.mean + self.shared + self.front_vector(t) + self.local
        return self.mean + self.shared + self.front_field(t, positions) + self.local

    def ambient(self, t: float) -> np.ndarray:
        """Swarm-wide wind (no per-drone part), shape (3,)."""
        return self.mean + self.shared + self.front_vector(t)
