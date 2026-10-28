"""What each drone knows: its own noisy navigation fix and its neighbours'
broadcast states, delivered over a lossy, delayed radio link.

Navigation error = a slowly drifting bias (Ornstein-Uhlenbeck, like GNSS
multipath and atmospheric error) plus white noise. Every drone broadcasts its
own estimate each step; a message from j reaches i after the link latency
(plus jitter) unless it is lost, out of range, or either drone is in a
blackout. Receivers dead-reckon the last message forward by its age.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Blackout:
    start_s: float
    end_s: float
    drones: list[int]


@dataclass
class SensingConfig:
    gps_sigma_m: float = 0.15            # white noise per axis
    gps_bias_sigma_m: float = 0.25       # drifting bias per axis (steady-state std)
    gps_bias_tau_s: float = 20.0
    vel_sigma_mps: float = 0.05
    latency_s: float = 0.05
    jitter_s: float = 0.02
    loss_prob: float = 0.02
    range_m: float = 400.0
    blackouts: list[Blackout] = field(default_factory=list)


class Sensing:
    def __init__(self, config: SensingConfig, count: int, dt: float, rng: np.random.Generator):
        self.c = config
        self.n = count
        self.dt = dt
        self.rng = rng
        self.bias = rng.standard_normal((count, 3)) * config.gps_bias_sigma_m
        self.bias[:, 2] *= 0.6
        # Every drone's broadcasts of the last few steps, in a ring buffer long enough for the longest delay
        # (latency plus jitter, in whole steps).
        lat_steps = int(round(config.latency_s / dt))
        jit_steps = int(round(config.jitter_s / dt))
        self.min_delay = max(lat_steps, 0)
        self.max_delay = self.min_delay + max(jit_steps, 0)
        self.hist_len = self.max_delay + 2
        self.hist = np.zeros((self.hist_len, count, 6))
        self.hist_t = np.full(self.hist_len, -np.inf)
        self.k = 0
        self.recv = np.zeros((count, count, 6))
        self.recv_t = np.full((count, count), -np.inf)
        self.delivered = 0
        self.attempted = 0

    def estimate(self, p: np.ndarray, v: np.ndarray):
        """Each drone's own navigation estimate (position, velocity)."""
        c = self.c
        if c.gps_bias_sigma_m > 0:
            a = self.dt / c.gps_bias_tau_s
            self.bias += -a * self.bias + c.gps_bias_sigma_m * np.sqrt(2 * a) * self.rng.standard_normal(self.bias.shape)
        p_est = p + self.bias + c.gps_sigma_m * self.rng.standard_normal(p.shape)
        v_est = v + c.vel_sigma_mps * self.rng.standard_normal(v.shape)
        return p_est, v_est

    def in_blackout(self, t: float) -> np.ndarray:
        dark = np.zeros(self.n, dtype=bool)
        for b in self.c.blackouts:
            if b.start_s <= t < b.end_s:
                idx = [d for d in b.drones if 0 <= d < self.n]
                dark[idx] = True
        return dark

    def exchange(self, t: float, p_est: np.ndarray, v_est: np.ndarray, alive: np.ndarray, p_true: np.ndarray):
        """Broadcast this step's estimates and deliver whatever arrives.

        Returns ``(pos, vel, age)``: each drone's dead-reckoned view of every
        other drone, shape (n, n, 3), (n, n, 3), (n, n). ``age`` is ``inf``
        for a neighbour never heard from.
        """
        slot = self.k % self.hist_len
        self.hist[slot, :, :3] = p_est
        self.hist[slot, :, 3:] = v_est
        self.hist_t[slot] = t
        n = self.n
        dark = self.in_blackout(t)
        talk = alive & ~dark
        link = talk[:, None] & talk[None, :]
        np.fill_diagonal(link, False)
        # Radio range depends on the true distance between drones: physics, not anything a drone knows.
        if self.c.range_m < np.inf:
            dist = np.linalg.norm(p_true[:, None, :] - p_true[None, :, :], axis=2)
            link &= dist <= self.c.range_m
        self.attempted += int(link.sum())
        ok = link & (self.rng.random((n, n)) >= self.c.loss_prob)
        if self.max_delay > self.min_delay:
            delay = self.rng.integers(self.min_delay, self.max_delay + 1, size=(n, n))
        else:
            delay = np.full((n, n), self.min_delay)
        delay = np.minimum(delay, self.k)  # nothing older than the start
        src_slot = (self.k - delay) % self.hist_len
        senders = np.broadcast_to(np.arange(n)[None, :], (n, n))
        msg = self.hist[src_slot, senders]                 # (n, n, 6)
        msg_t = self.hist_t[src_slot]                      # (n, n)
        # A message no newer than the last one received on that link is dropped, so a late packet never
        # replaces fresher news.
        newer = ok & (msg_t > self.recv_t)
        self.recv[newer] = msg[newer]
        self.recv_t[newer] = msg_t[newer]
        self.delivered += int(newer.sum())
        self.k += 1
        age = t - self.recv_t
        pos = self.recv[:, :, :3] + self.recv[:, :, 3:] * np.where(np.isfinite(age), age, 0.0)[..., None]
        # A drone always knows itself.
        idx = np.arange(n)
        pos[idx, idx] = p_est
        vel = self.recv[:, :, 3:].copy()
        vel[idx, idx] = v_est
        age = age.copy()
        age[idx, idx] = 0.0
        return pos, vel, age

    @property
    def delivery_rate(self) -> float:
        return self.delivered / self.attempted if self.attempted else 1.0
