"""How the valley's fires would be found without the drones: today's methods, modelled from published figures.

Each method is a rule applied to the same true fires the drones face, so no flight is needed and nothing is
tuned: every number below comes from a published source (listed in docs/WILDFIRE.md, "Drones against today's
methods"), and where a source gives a range we take the end that favours today's method. No cloud, a camera
with a clear view of the whole valley, every satellite detection credited to the right fire although one
satellite pixel is larger than the valley, and the fastest published delivery.

* **Geostationary satellite** (GOES ABI over the US): a scan every 5 min. A fire is detected at the first scan
  at which its fire radiative power (FRP) reaches 34.5 MW, the size below which Li et al. (2020) found ABI
  "hard to detect", and delivered 266 s later (the product's latency requirement for the US sector).
* **Polar-orbiting satellites**: MODIS on Terra (10:30 local), VIIRS on NOAA-20 and NOAA-21 (12:40) and on
  Suomi NPP (13:30), each passing once in daylight within about half an orbit (+-50 min) of that time. A fire is
  detected at a pass if its FRP reaches 2.3 MW for MODIS (100 m^2 burning at 800 K, "under very good observing
  conditions") or 0.33 MW for VIIRS (the same area scaled by its pixel, 375 m against 1 km), delivered within
  a minute (direct readout in the US and Canada).
* **AI camera network** (ALERTCalifornia, Pano AI): smoke is picked up 4 min after the fire starts to spread
  (the vendor's "usually within four to five minutes"; a research system took a median 13.5 min). A spot
  smouldering under a few square metres puts up no smoke a camera kilometres away can pick out, so the clock
  starts when the fire starts to spread. As deployed, cameras see far fewer fires: in 2024 CAL FIRE's cameras
  picked up 1,668 of the 7,553 fires in its area, so a second camera rule sees each fire with that chance.
* **Public calls** (911 in the US, 119 in Japan): the first call comes an exponentially distributed time after
  the fire starts to spread, with a mean of 10 min ("in densely populated California, most fires are reported
  within 15 min"; this gives 78% within 15 min). The same reasoning as for cameras holds while it smoulders.

FRP follows from how fast the fire consumes fuel: FRP (MW) = combustion rate (kg/s) / 0.368 (Wooster et al.
2005, eq. 14), with the whole fuel load of the cell's Anderson fuel model consumed as the front passes (the most
FRP the fuel can give). The flight starts at 08:00 local time, so the four hours scored are 08:00 to 12:00.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .fire import COVER_FUEL, FUEL_MODELS

TONS_PER_ACRE_TO_KG_PER_M2 = 907.18474 / 4046.8564
COMBUSTION_KG_PER_MJ = 0.368          # Wooster et al. (2005), eq. 14: kg/s of fuel burnt per MW of FRP


@dataclass(frozen=True)
class TodayConfig:
    start_local_h: float = 8.0            # local time when the flight starts
    frp_window_s: float = 300.0           # FRP is the mean over this long before a look
    geo_scan_s: float = 300.0             # GOES ABI, US sector (Mode 6)
    geo_frp_mw: float = 34.5              # Li et al. (2020): ABI "hard to detect fires smaller than ~34.5 MW"
    geo_latency_s: float = 266.0          # GOES-R fire product latency requirement, US sector
    # (name, nominal local time of the daylight pass in hours, FRP needed in MW)
    polar_passes: tuple = (("MODIS Terra", 10.5, 2.3), ("VIIRS NOAA-20", 12.0 + 40.0 / 60.0, 0.33),
                           ("VIIRS NOAA-21", 12.0 + 40.0 / 60.0, 0.33), ("VIIRS Suomi NPP", 13.5, 0.33))
    polar_spread_min: float = 50.0        # a site is seen within about half an orbit of the nominal time
    polar_latency_s: float = 60.0         # FIRMS ultra-real-time, US and Canada direct readout
    camera_delay_s: float = 240.0         # Pano AI: "usually within four to five minutes"
    camera_seen_share: float = 1668.0 / 7553.0   # CAL FIRE 2024: fires its cameras picked up, of all in its area
    public_mean_s: float = 600.0          # exponential; 78% of fires reported within 15 min

    def methods(self) -> list[str]:
        return ["geostationary", "polar", "camera", "public", "camera_deployed"]


def fuel_load(wf) -> np.ndarray:
    """Fuel load (kg/m^2) of each forecast-grid cell: the whole load of its Anderson fuel model."""
    cover = np.asarray(wf.terrain.cover_at(wf.grid.X, wf.grid.Y))
    load = np.zeros(cover.shape)
    for c, number in COVER_FUEL.items():
        fm = FUEL_MODELS.get(number)
        if fm is not None:
            load[cover == c] = (fm.load_1h + fm.load_10h + fm.load_100h + fm.load_live) * TONS_PER_ACRE_TO_KG_PER_M2
    return load


class FirePower:
    """Fire radiative power of one true fire over time, from the fuel its front consumes."""

    def __init__(self, arrival: np.ndarray, load: np.ndarray, cell_m2: float, window_s: float):
        # Each cell's whole fuel load is taken to burn as the front reaches it, so the fuel consumed by
        # any time is a running sum over the cells in order of arrival.
        a = arrival.ravel()
        burn = np.isfinite(a)
        order = np.argsort(a[burn])
        self.t = a[burn][order]
        self.kg = np.concatenate([[0.0], np.cumsum(load.ravel()[burn][order] * cell_m2)])
        self.window_s = window_s

    def frp_mw(self, t) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        # Mean burn rate (kg/s) over the window before each t, turned into MW with COMBUSTION_KG_PER_MJ.
        hi = np.searchsorted(self.t, t, side="right")
        lo = np.searchsorted(self.t, t - self.window_s, side="right")
        return (self.kg[hi] - self.kg[lo]) / self.window_s / COMBUSTION_KG_PER_MJ


def today_methods(wf, seed: int, cfg: TodayConfig | None = None) -> list[dict]:
    """When each of today's methods would first report each fire, and how big the fire was then.

    Returns one dict per fire: {method: {"t": seconds after the flight starts or None, "area_m2", "by"}}.
    None means not reported within the horizon (4 h). Random parts (the satellites' phase, the passes' timing,
    the first call) are drawn from the seed alone, so they are the same for every flight on that seed.
    """
    cfg = cfg or TodayConfig()
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), 9101]))
    horizon = wf.cfg.horizon_s
    load = fuel_load(wf)
    cell_m2 = wf.grid.cell_m ** 2
    geo_phase = float(rng.uniform(0.0, cfg.geo_scan_s))
    passes = []
    for name, local_h, frp in cfg.polar_passes:
        t = (local_h - cfg.start_local_h) * 3600.0 + float(rng.uniform(-1.0, 1.0)) * cfg.polar_spread_min * 60.0
        passes.append((t, name, frp))
    passes.sort()
    out = []
    for i, f in enumerate(wf.fires):
        power = FirePower(wf.fire_arrival[i], load, cell_m2, cfg.frp_window_s)
        public_delay = float(rng.exponential(cfg.public_mean_s))
        camera_sees = bool(rng.random() < cfg.camera_seen_share)
        found: dict = {}
        # geostationary: every scan from the moment the fire spreads
        k0 = math.ceil((f.t_active_s - geo_phase) / cfg.geo_scan_s)
        scans = geo_phase + cfg.geo_scan_s * np.arange(k0, k0 + int(horizon / cfg.geo_scan_s) + 2)
        hit = np.nonzero(power.frp_mw(scans) >= cfg.geo_frp_mw)[0]
        found["geostationary"] = (float(scans[hit[0]]) + cfg.geo_latency_s, "GOES ABI") if len(hit) else None
        found["polar"] = None
        # Passes are in time order: the first after the fire starts to spread that sees enough power reports it.
        for t, name, frp in passes:
            if t >= f.t_active_s and float(power.frp_mw(t)) >= frp:
                found["polar"] = (t + cfg.polar_latency_s, name)
                break
        found["camera"] = (f.t_active_s + cfg.camera_delay_s, "camera")
        found["public"] = (f.t_active_s + public_delay, "call")
        found["camera_deployed"] = found["camera"] if camera_sees else None
        row = {}
        for m in cfg.methods():
            v = found[m]
            if v is None or v[0] > horizon:
                row[m] = {"t": None, "area_m2": None, "by": None}
            else:
                row[m] = {"t": round(v[0], 1), "area_m2": round(float(wf.area(v[0])[i]), 1), "by": v[1]}
        out.append(row)
    return out
