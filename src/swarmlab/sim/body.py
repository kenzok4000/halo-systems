"""Drone body model.

The defaults describe the 1.2 kg, 7-inch quadcopter of an earlier private
simulator, which the original controller comes from: the same mass, drag
area, thrust-to-weight ratio and body radius, so results stay comparable.
The power and battery numbers are estimates from momentum theory, chosen so
that hover endurance is about 17 minutes, the figure the earlier simulator uses
for this airframe. They are not calibrated against hardware.

Power in forward flight is induced power from Glauert's momentum theory
(the induced velocity falls as airspeed rises) plus parasitic power (drag
times airspeed), the textbook decomposition (Leishman, *Principles of
Helicopter Aerodynamics*, ch. 2 and 5). The sum is U-shaped in airspeed, so
there is a real most-economical cruise speed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

GRAVITY = 9.81
AIR_DENSITY = 1.225


@dataclass(frozen=True)
class DroneBody:
    name: str = "7-inch quadcopter"
    mass_kg: float = 1.2
    radius_m: float = 0.15
    drag_area_m2: float = 0.035          # Cd * A, from the original engine
    thrust_ratio: float = 2.5            # maximum thrust / weight
    rotor_disk_area_m2: float = 0.0993   # four 7-inch rotors
    figure_of_merit: float = 0.6         # rotor efficiency vs ideal momentum theory
    avionics_power_w: float = 8.0
    battery_wh: float = 41.0             # usable energy, about 17 hover-minutes
    motor_tau_s: float = 0.05            # first-order thrust response
    max_speed_mps: float = 25.0          # the original engine's top speed

    @property
    def max_thrust_n(self) -> float:
        return self.thrust_ratio * self.mass_kg * GRAVITY

    def rotor_power_w(self, thrust_n, airspeed_mps=0.0):
        """Shaft power (scalar or array) for a thrust at an airspeed.

        Induced power T * v_i / FoM, with v_i from Glauert's relation for an
        edgewise rotor, v_i^4 + V^2 v_i^2 = v_h^4, plus parasitic power D * V.
        """
        thrust_n = np.asarray(thrust_n, dtype=float)
        v = np.asarray(airspeed_mps, dtype=float)
        vh2 = thrust_n / (2.0 * AIR_DENSITY * self.rotor_disk_area_m2)
        vi = np.sqrt(np.maximum(0.0, 0.5 * (-v ** 2 + np.sqrt(v ** 4 + 4.0 * vh2 ** 2))))
        induced = thrust_n * vi / self.figure_of_merit
        parasitic = 0.5 * AIR_DENSITY * self.drag_area_m2 * v ** 3
        return induced + parasitic

    def level_power_w(self, airspeed_mps):
        """Electrical power in steady level flight at an airspeed (scalar or array)."""
        v = np.asarray(airspeed_mps, dtype=float)
        drag = 0.5 * AIR_DENSITY * self.drag_area_m2 * v ** 2
        thrust = np.hypot(self.mass_kg * GRAVITY, drag)
        return self.rotor_power_w(thrust, v) + self.avionics_power_w

    def economical_speed_mps(self, max_speed=None) -> float:
        """Airspeed with the lowest energy per metre in still air."""
        top = max_speed or self.max_speed_mps
        speeds = np.linspace(0.5, top, 500)
        return float(speeds[np.argmin(self.level_power_w(speeds) / speeds)])

    @property
    def hover_power_w(self) -> float:
        return float(self.rotor_power_w(self.mass_kg * GRAVITY)) + self.avionics_power_w

    @property
    def hover_endurance_min(self) -> float:
        return 60.0 * self.battery_wh / self.hover_power_w

    def to_dict(self) -> dict:
        return asdict(self)
