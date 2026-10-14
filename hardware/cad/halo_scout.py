"""Halo Systems scout quadcopter: parametric CAD model and exports.

Builds the airframe the Halo Systems simulator flies (src/halo_systems/sim/body.py): a
7-inch true-X quadcopter with a downward radiometric thermal camera, an oblique
RGB camera, a PM2.5 smoke sensor, GNSS on a mast, a multipoint telemetry radio,
an ExpressLRS safety receiver and a Raspberry Pi Zero 2 W companion computer.

Run with CadQuery 2.8 (see ../README.md):

    python halo_scout.py            # writes ../exports/*

Outputs, all in ../exports/:
    halo_scout.step           assembly with named, coloured parts (mm)
    halo_scout.stl            all parts in one mesh (mm)
    print/*.stl               each printed part on its own, ready to slice (mm)
    halo_scout.glb            low-poly glTF binary (metres, +Y up, +Z forward,
                              +X left, origin at the centre of mass); each part
                              is a named node, props spin about their node's +Y
    mass_properties.json      mass, centre of mass, inertia, projected areas,
                              printed-part masses (read by ../budget.py)

CAD frame: x forward, y left, z up, millimetres. The origin of the CAD frame is
the centre of the arms' underside; the exports move it to the centre of mass.
Purchased-part masses come from ../bom.csv; printed parts get their mass from
their CAD volume. Dimensions of purchased parts are the makers' published
figures where they exist (see the BOM), otherwise simple envelopes.
"""

from __future__ import annotations

import csv
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq
import numpy as np

# --------------------------------------------------------------------------
# Key dimensions (mm unless stated). Change these and re-run.
# --------------------------------------------------------------------------
WHEELBASE = 295.0            # motor-to-motor diagonal (GEPRC Mark4 7-inch)
PROP_DIAMETER = 177.8        # 7 inch; four of them give 0.0993 m^2 of disk
PROP_BLADES = 3              # HQProp 7x4x3
PROP_PITCH_DEG = 13.0        # blade angle used for the simple blade solid
ARM_THICKNESS = 5.0
ARM_ROOT_WIDTH = 26.0
ARM_TIP_WIDTH = 17.0
MOTOR_PAD_DIAMETER = 32.0
PLATE_THICKNESS = 2.5
PLATE_LENGTH = 130.0
PLATE_WIDTH = 46.0
STANDOFF_HEIGHT = 30.0       # arm top to top plate
MOTOR_DIAMETER = 33.9        # EMAX ECO II 2807 bell
MOTOR_HEIGHT = 22.0          # base + bell, without shaft
BATTERY_SIZE = (116.0, 34.0, 38.5)   # Tattu 2200 mAh 6S, L x W x H
BATTERY_X = -10.0            # battery centre along x (moves the centre of mass)
POD_SIZE = (104.0, 50.0, 26.0)       # printed sensor pod under the frame
POD_X = 6.0                  # pod centre along x
POD_WALL = 2.0
THERMAL_X = 42.0             # downward thermal camera position along x
THERMAL_HFOV_DEG = 57.0      # Lepton 3.5 native horizontal field of view
THERMAL_USED_FOV_DEG = 45.0  # cropped in software to match the simulator
RGB_TILT_DEG = 20.0          # RGB camera looks forward and 20 deg down
RGB_HFOV_DEG = 66.0          # Camera Module 3 standard lens
SMOKE_SENSOR_SIZE = (12.0, 41.0, 41.0)  # SPS30 standing on edge, inlet up
GNSS_MAST_HEIGHT = 70.0      # from the bracket under the top plate
GNSS_MAST_X = -78.0
LEG_RADIUS = 80.0            # leg position along each arm from the centre
LEG_LENGTH = 58.0            # below the arms
ANTENNA_RADIUS = 58.0        # 900 MHz antennas on the rear arms
ANTENNA_LENGTH = 51.0
ANTENNA_TILT_DEG = 35.0

# Printing assumptions for printed-part mass (typical filament densities).
DENSITY_G_CM3 = {"PETG": 1.27, "TPU": 1.21}

HERE = Path(__file__).resolve().parent
HARDWARE = HERE.parent
EXPORTS = HARDWARE / "exports"
BOM_CSV = HARDWARE / "bom.csv"

MOTOR_R = WHEELBASE / 2.0                      # 147.5 mm, simulator body radius 150 mm
ARM_Z0, ARM_Z1 = 0.0, ARM_THICKNESS
TOP_Z0 = ARM_Z1 + STANDOFF_HEIGHT
TOP_Z1 = TOP_Z0 + PLATE_THICKNESS
PROP_Z = ARM_Z1 + MOTOR_HEIGHT + 4.0           # prop hub centre height

# Motor layout and spin, ArduPilot QUAD/X numbering (viewed from above).
MOTORS = {
    "FR": dict(angle=-45.0, motor=1, spin="ccw"),
    "RL": dict(angle=135.0, motor=2, spin="ccw"),
    "FL": dict(angle=45.0, motor=3, spin="cw"),
    "RR": dict(angle=-135.0, motor=4, spin="cw"),
}

# Materials for the STEP colours and the glTF (base colour RGBA, metallic, roughness).
MATERIALS = {
    "carbon": ((0.13, 0.13, 0.14, 1.0), 0.2, 0.5),
    "motor": ((0.62, 0.64, 0.67, 1.0), 0.8, 0.35),
    "motor_dark": ((0.16, 0.17, 0.19, 1.0), 0.6, 0.4),
    "prop": ((0.78, 0.80, 0.82, 1.0), 0.0, 0.6),
    "pcb_black": ((0.10, 0.11, 0.12, 1.0), 0.1, 0.6),
    "pcb_green": ((0.10, 0.42, 0.22, 1.0), 0.1, 0.6),
    "pcb_blue": ((0.12, 0.25, 0.55, 1.0), 0.1, 0.6),
    "printed": ((0.93, 0.45, 0.13, 1.0), 0.0, 0.7),   # high-visibility orange
    "tpu": ((0.20, 0.21, 0.23, 1.0), 0.0, 0.9),
    "battery": ((0.20, 0.22, 0.26, 1.0), 0.1, 0.55),
    "sensor_white": ((0.86, 0.87, 0.88, 1.0), 0.0, 0.6),
    "glass": ((0.05, 0.06, 0.08, 1.0), 0.3, 0.15),
    "antenna": ((0.08, 0.08, 0.09, 1.0), 0.1, 0.5),
    "strobe": ((0.95, 0.95, 0.97, 1.0), 0.0, 0.3),
    "strap": ((0.70, 0.12, 0.12, 1.0), 0.0, 0.8),
    "nylon": ((0.90, 0.90, 0.88, 1.0), 0.0, 0.7),
    "gold": ((0.85, 0.65, 0.25, 1.0), 0.9, 0.3),
}


@dataclass
class Part:
    name: str
    shape: cq.Shape
    material: str
    label: str
    group: str
    bom_id: str | None = None      # mass share of a BOM line
    mass_share: float = 1.0        # fraction of that line's unit mass
    printed: str | None = None     # "PETG" or "TPU": mass from volume
    fill: float = 1.0              # printed fill factor (shells 1.0)
    explode: tuple = (0.0, 0.0, 0.0)
    pivot: tuple | None = None     # node origin (props: hub centre)
    extras: dict = field(default_factory=dict)
    mass_g: float = 0.0


# --------------------------------------------------------------------------
# Small geometry helpers (CadQuery solids in the CAD frame)
# --------------------------------------------------------------------------
V = cq.Vector


def box(cx, cy, cz, lx, ly, lz):
    """Box centred at (cx, cy, cz)."""
    return cq.Solid.makeBox(lx, ly, lz, V(cx - lx / 2, cy - ly / 2, cz - lz / 2))


def rbox(cx, cy, cz, lx, ly, lz, r):
    """Box with rounded vertical edges."""
    wp = cq.Workplane("XY").rect(lx, ly).extrude(lz).edges("|Z").fillet(r)
    return wp.val().translate(V(cx, cy, cz - lz / 2))


def cyl(x, y, z0, d, h, direction=(0, 0, 1)):
    return cq.Solid.makeCylinder(d / 2, h, V(x, y, z0), V(*direction))


def rot_z(shape, deg):
    return shape.rotate(V(0, 0, 0), V(0, 0, 1), deg)


def polar(r, deg):
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


# --------------------------------------------------------------------------
# Part builders
# --------------------------------------------------------------------------
def arm(angle):
    r0 = 18.0
    pts = [(r0, -ARM_ROOT_WIDTH / 2), (MOTOR_R, -ARM_TIP_WIDTH / 2),
           (MOTOR_R, ARM_TIP_WIDTH / 2), (r0, ARM_ROOT_WIDTH / 2)]
    body = cq.Workplane("XY").polyline(pts).close().extrude(ARM_THICKNESS).val()
    pad = cyl(MOTOR_R, 0, ARM_Z0, MOTOR_PAD_DIAMETER, ARM_THICKNESS)
    slot = box((r0 + MOTOR_R) / 2 + 10, 0, ARM_Z1 / 2, 46, 6, ARM_THICKNESS * 2)
    return rot_z(body.fuse(pad).cut(slot).clean(), angle)


def motor(x, y):
    base = cyl(x, y, ARM_Z1, MOTOR_DIAMETER - 3, 4)
    bell = cyl(x, y, ARM_Z1 + 4, MOTOR_DIAMETER, MOTOR_HEIGHT - 4)
    shaft = cyl(x, y, ARM_Z1 + MOTOR_HEIGHT, 5, 9)
    return base.fuse(bell).fuse(shaft).clean()


def propeller(spin):
    """Three-blade 7-inch prop centred on the origin, disk in the XY plane."""
    r_tip = PROP_DIAMETER / 2
    hub = cyl(0, 0, -3.5, 14, 7)
    sign = 1.0 if spin == "ccw" else -1.0
    blades = None
    for k in range(PROP_BLADES):
        pts = [(5, -8), (r_tip * 0.55, -10.5), (r_tip - 4, -6), (r_tip, -1.5),
               (r_tip, 1.5), (r_tip - 4, 5), (r_tip * 0.55, 8), (5, 7)]
        blade = cq.Workplane("XY").polyline(pts).close().extrude(1.6).val().translate(V(0, 0, -0.8))
        blade = blade.rotate(V(0, 0, 0), V(1, 0, 0), sign * PROP_PITCH_DEG)
        blade = rot_z(blade, k * 360.0 / PROP_BLADES)
        blades = blade if blades is None else blades.fuse(blade)
    return hub.fuse(blades).clean()


def shell_box(cx, cy, cz, lx, ly, lz, wall):
    """Open-top box with walls and floor of thickness `wall`, centred at (cx, cy, cz)."""
    outer = rbox(cx, cy, cz, lx, ly, lz, 6)
    z0, z1 = cz - lz / 2 + wall, cz + lz / 2 + 1.0
    inner = rbox(cx, cy, (z0 + z1) / 2, lx - 2 * wall, ly - 2 * wall, z1 - z0, 4)
    return outer.cut(inner)


def landing_leg(angle):
    x, y = polar(LEG_RADIUS, angle)
    top = cq.Workplane("XY").workplane(offset=-LEG_LENGTH + 4).rect(9, 7).workplane(
        offset=LEG_LENGTH - 4).rect(15, 11).loft().val()
    top = rot_z(top, angle).translate(V(x, y, 0))
    foot = cyl(x, y, -LEG_LENGTH, 16, 4)
    return top.fuse(foot).clean()


def antenna(angle):
    """Right-angle monopole hanging below a rear arm, tilted outwards."""
    x, y = polar(ANTENNA_RADIUS, angle)
    whip = cyl(0, 0, -ANTENNA_LENGTH, 8, ANTENNA_LENGTH)
    elbow = box(0, 0, -1.5, 10, 10, 6)
    s = whip.fuse(elbow)
    s = s.rotate(V(0, 0, 0), V(0, 1, 0), -ANTENNA_TILT_DEG)   # tip leans along +x...
    s = rot_z(s, angle)                                        # ...then outwards along the arm
    return s.translate(V(x, y, ARM_Z0 - 6))


def build_parts() -> list[Part]:
    parts: list[Part] = []
    up = lambda d: (0.0, 0.0, d)

    def add(*a, **k):
        parts.append(Part(*a, **k))

    # Frame: arms, plates, side plates, standoffs (mass split by volume).
    for key, m in MOTORS.items():
        dx, dy = polar(1.0, m["angle"])
        add(f"arm_{key}", arm(m["angle"]), "carbon", "Carbon arm, 5 mm", "frame", "frame",
            explode=(25 * dx, 25 * dy, 0))
    add("bottom_plate", rbox(0, 0, ARM_Z0 - PLATE_THICKNESS / 2, PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS, 8),
        "carbon", "Bottom plate", "frame", "frame", explode=up(-22))
    add("top_plate", rbox(0, 0, TOP_Z0 + PLATE_THICKNESS / 2, PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS, 8),
        "carbon", "Top plate", "frame", "frame", explode=up(120))
    for side in (1, -1):
        sp = box(46, side * (PLATE_WIDTH / 2 - 1.25), ARM_Z1 + STANDOFF_HEIGHT / 2, 40, 2.5, STANDOFF_HEIGHT)
        add(f"side_plate_{'L' if side > 0 else 'R'}", sp, "carbon", "Camera side plate", "frame", "frame",
            explode=(0, side * 45, 0))
    for side in (1, -1):
        add(f"standoff_rear_{'L' if side > 0 else 'R'}", cyl(-56, side * 17, ARM_Z1, 5, STANDOFF_HEIGHT),
            "motor_dark", "Aluminium standoff", "frame", "frame", explode=up(60))

    # Motors and props.
    for key, m in MOTORS.items():
        x, y = polar(MOTOR_R, m["angle"])
        dx, dy = polar(1.0, m["angle"])
        add(f"motor_{key}", motor(x, y), "motor", "Motor 2807 1300 KV", "propulsion", "motor",
            explode=(60 * dx, 60 * dy, 30))
        add(f"prop_{key}", propeller(m["spin"]), "prop", f"Prop 7x4x3 ({m['spin'].upper()})",
            "propulsion", "props", mass_share=0.25, explode=(60 * dx, 60 * dy, 80),
            pivot=(x, y, PROP_Z), extras={"spin": m["spin"], "ardupilot_motor": m["motor"]})

    # Flight-control stack on 30.5 mm nylon standoffs.
    for i, (sx, sy) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]):
        add(f"stack_standoff_{i}", cyl(sx * 15.25, sy * 15.25, ARM_Z1, 5, 18), "nylon", "Nylon standoff",
            "stack", "hardware", mass_share=0.0, explode=up(70))
    add("esc_4in1", box(0, 0, ARM_Z1 + 5, 45, 37, 4), "pcb_black", "4-in-1 ESC 50 A (AM32)", "stack",
        "stack", mass_share=13.8 / 21.8, explode=up(55))
    add("flight_controller", box(0, 0, ARM_Z1 + 15, 36, 36, 5), "pcb_blue", "Flight controller H743 (ArduPilot)",
        "stack", "stack", mass_share=8.0 / 21.8, explode=up(88))

    # Battery, strap, power lead, strobe on top.
    bl, bw, bh = BATTERY_SIZE
    bz = TOP_Z1 + bh / 2
    batt = cq.Workplane("XY").box(bl, bw, bh).edges().fillet(3).val().translate(V(BATTERY_X, 0, bz))
    add("battery", batt, "battery", "LiPo 6S 2200 mAh, 48.8 Wh", "battery", "battery", explode=up(205))
    strap = box(BATTERY_X, 0, (TOP_Z0 + TOP_Z1 + bh) / 2 + 1, 20, bw + 3, bh + PLATE_THICKNESS + 4).cut(
        box(BATTERY_X, 0, (TOP_Z0 + TOP_Z1 + bh) / 2 + 1, 22, bw, bh + PLATE_THICKNESS + 0.5))
    add("battery_strap", strap, "strap", "Battery strap", "battery", "strap", explode=up(205))
    add("xt60", box(BATTERY_X + 46, bw / 2 + 5, TOP_Z1 + 7, 16, 8, 12), "gold", "XT60 power lead", "battery",
        "xt60", explode=(0, 20, 205))
    add("strobe", rbox(BATTERY_X - 30, 0, TOP_Z1 + bh + 5, 24, 16, 10, 4), "strobe",
        "Anti-collision strobe", "battery", "strobe", explode=up(255))

    # Smoke sensor on a printed bracket at the front of the top plate.
    sx, sy, sz = SMOKE_SENSOR_SIZE
    sps_x = PLATE_LENGTH / 2 - sx / 2 - 1
    add("smoke_sensor", box(sps_x, 0, TOP_Z1 + 2 + sz / 2, sx, sy, sz), "sensor_white",
        "PM2.5 smoke sensor (SPS30)", "sensors", "pm_sensor", explode=(70, 0, 150))
    bracket = box(sps_x - 2, 0, TOP_Z1 + 1, sx + 10, sy + 4, 2).fuse(
        box(sps_x - sx / 2 - 1.5, 0, TOP_Z1 + 15, 3, sy + 4, 28))
    add("smoke_sensor_bracket", bracket, "printed", "Printed sensor bracket", "sensors", printed="PETG",
        explode=(55, 0, 125))

    # RGB camera between the front side plates, looking forward and down.
    cam_x, cam_z = 56.0, ARM_Z1 + STANDOFF_HEIGHT / 2
    cam = box(0, 0, 0, 1.6, 25, 24).fuse(box(4.2, 0, 2, 7, 10, 10)).fuse(cyl(7.5, 0, 2, 7, 3, (1, 0, 0)))
    cam = cam.rotate(V(0, 0, 0), V(0, 1, 0), RGB_TILT_DEG).translate(V(cam_x, 0, cam_z))
    add("rgb_camera", cam, "pcb_green", "RGB camera, 12 MP, 20 deg down", "sensors", "rgb_cam",
        explode=(95, 0, 0))
    mount = box(0, 0, 0, 3, PLATE_WIDTH - 6, 28).cut(box(0, 0, 0, 6, 21, 20))
    mount = mount.rotate(V(0, 0, 0), V(0, 1, 0), RGB_TILT_DEG).translate(V(cam_x - 3.5, 0, cam_z))
    add("rgb_camera_mount", mount, "printed", "Printed camera mount", "sensors", printed="PETG",
        explode=(70, 0, 0))

    # Sensor pod under the frame: companion computer, BEC, Remote ID, thermal camera.
    px, py, pz = POD_SIZE
    pod_top = ARM_Z0 - PLATE_THICKNESS
    pod_c = pod_top - pz / 2
    pod = shell_box(POD_X, 0, pod_c, px, py, pz, POD_WALL)
    window = box(THERMAL_X, 0, pod_top - pz, 16, 16, 8)
    pod = pod.cut(window)
    add("sensor_pod", pod, "printed", "Printed sensor pod", "pod", printed="PETG", explode=up(-95))
    add("companion_computer", box(-8, 0, pod_top - 6, 65, 30, 5), "pcb_green",
        "Companion computer (Pi Zero 2 W)", "pod", "companion", explode=up(-42))
    add("bec_5v", box(-22, 0, pod_top - 14, 35, 24, 5.5), "pcb_blue", "5 V 5 A BEC", "pod", "bec",
        explode=up(-58))
    add("remote_id", box(-22, 0, pod_top - 21, 25, 20, 4), "pcb_black", "Remote ID module", "pod",
        "remote_id", explode=up(-72))
    floor = pod_top - pz
    add("thermal_board", box(THERMAL_X, 0, floor + 12, 19.2, 15.3, 3), "pcb_black",
        "USB thermal board (PureThermal)", "thermal", "thermal_board", explode=up(-125))
    lep = box(THERMAL_X, 0, floor + 3.1, 11.8, 12.7, 7.2).fuse(cyl(THERMAL_X, 0, floor - 0.8, 7, 1.5))
    add("thermal_camera", lep, "glass", "Radiometric thermal camera, looking down", "thermal", "thermal_core",
        explode=up(-150))
    shroud = rbox(THERMAL_X, 0, floor - 1.6, 22, 22, 3.2, 3).cut(box(THERMAL_X, 0, floor - 1.6, 13, 13, 5))
    add("thermal_mount", shroud, "printed", "Printed thermal mount and sun shroud", "thermal", printed="PETG",
        explode=up(-170))

    # Radio, receiver and antennas at the rear.
    add("telemetry_radio", box(-40, 0, ARM_Z1 + 5.2, 33, 25, 10.4), "pcb_black",
        "900 MHz multipoint telemetry radio", "radio", "radio", explode=(-65, 0, 28))
    add("rc_receiver", box(-40, 0, ARM_Z1 + 12, 13, 11, 3), "pcb_black", "ELRS 2.4 GHz receiver", "radio",
        "rc_rx", explode=(-65, 0, 50))
    for side, ang in (("L", 135.0), ("R", -135.0)):
        add(f"antenna_900_{side}", antenna(ang), "antenna", "900 MHz antenna", "radio", "radio_antenna",
            explode=(*[40 * c for c in polar(1.0, ang)], -55))
        x, y = polar(ANTENNA_RADIUS, ang)
        add(f"antenna_mount_{side}", rot_z(box(0, 0, ARM_Z0 - 3, 16, 12, 6), ang).translate(V(x, y, 0)), "tpu",
            "Printed antenna mount", "radio", printed="TPU", explode=(*[30 * c for c in polar(1.0, ang)], -28))
    for side in (1, -1):
        whip = cyl(0, 0, 0, 2.2, 30, (0, 0, 1)).rotate(V(0, 0, 0), V(1, 0, 0), side * 50.0)
        whip = whip.rotate(V(0, 0, 0), V(0, 1, 0), -25.0).translate(V(-PLATE_LENGTH / 2 + 4, 0, ARM_Z1 + 2))
        add(f"elrs_antenna_{'L' if side > 0 else 'R'}", whip, "antenna", "ELRS T antenna", "radio",
            "rc_rx", mass_share=0.0, explode=(-80, 0, 15))

    # GNSS on a printed mast behind the battery.
    mast = cyl(GNSS_MAST_X, 0, TOP_Z0 - 4.5, 9, GNSS_MAST_HEIGHT).fuse(
        box((GNSS_MAST_X - 52) / 2, 0, TOP_Z0 - 3, abs(GNSS_MAST_X + 52) + 10, 20, 3)).fuse(
        box(GNSS_MAST_X, 0, TOP_Z0 - 4.5 + GNSS_MAST_HEIGHT + 1, 30, 30, 2))
    add("gnss_mast", mast, "printed", "Printed GNSS mast", "gnss", printed="PETG", explode=(-75, 0, 60))
    gz = TOP_Z0 - 4.5 + GNSS_MAST_HEIGHT + 2
    add("gnss", rbox(GNSS_MAST_X, 0, gz + 5.5, 34, 28, 11, 5), "pcb_black", "GNSS M10 + compass", "gnss",
        "gnss", explode=(-75, 0, 115))

    # Landing legs (TPU).
    for key, m in MOTORS.items():
        dx, dy = polar(1.0, m["angle"])
        add(f"landing_leg_{key}", landing_leg(m["angle"]), "tpu", "TPU landing leg", "legs", printed="TPU",
            fill=0.55, explode=(35 * dx, 35 * dy, -115))
    return parts


# Masses with no solid of their own, placed where they sit (CAD frame, mm).
POINT_MASSES = [
    ("sd", (-8.0, 0.0, -8.0)), ("rgb_cable", (20.0, 0.0, 0.0)), ("thermal_cable", (15.0, 0.0, -12.0)),
    ("otg", (-30.0, 0.0, -10.0)), ("radio_cable", (-30.0, 0.0, 10.0)),
    ("radio_pigtail", (-42.0, 30.0, 0.0)), ("radio_pigtail", (-42.0, -30.0, 0.0)),
    ("hardware", (0.0, 0.0, 12.0)),
]


# --------------------------------------------------------------------------
# Mass properties
# --------------------------------------------------------------------------
def read_bom():
    with BOM_CSV.open(newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def assign_masses(parts, bom):
    frame_parts = [p for p in parts if p.bom_id == "frame"]
    frame_vol = sum(p.shape.Volume() for p in frame_parts)
    printed = {"PETG": 0.0, "TPU": 0.0}
    for p in parts:
        if p.printed:
            p.mass_g = p.shape.Volume() / 1000.0 * DENSITY_G_CM3[p.printed] * p.fill
            printed[p.printed] += p.mass_g
        elif p.bom_id == "frame":
            p.mass_g = float(bom["frame"]["unit_mass_g"]) * p.shape.Volume() / frame_vol
        elif p.bom_id:
            p.mass_g = float(bom[p.bom_id]["unit_mass_g"]) * p.mass_share
    # Lines with quantity two map to two parts (antennas): unit mass already per part.
    points = [(float(bom[i]["unit_mass_g"]), np.array(pos)) for i, pos in POINT_MASSES]
    return printed, points


def inertia_about(shape, mass_g, ref):
    """Inertia tensor (kg m^2) of a uniform solid of given mass about point ref (mm)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    vol = props.Mass()
    m = props.MatrixOfInertia()           # about the solid's own centre, density 1 (mm^5)
    i_own = np.array([[m.Value(r, c) for c in (1, 2, 3)] for r in (1, 2, 3)])
    c = props.CentreOfMass()
    d = np.array([c.X(), c.Y(), c.Z()]) - ref
    rho = mass_g / vol                    # g/mm^3
    i = i_own * rho + mass_g * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return i * 1e-9                       # g mm^2 -> kg m^2


def projected_area(meshes, axis_drop, px=0.5):
    """Silhouette area (m^2) of triangle meshes projected along one CAD axis."""
    from PIL import Image, ImageDraw
    keep = [k for k in range(3) if k != axis_drop]
    allv = np.vstack([v for v, _ in meshes])[:, keep]
    lo, hi = allv.min(0) - 2, allv.max(0) + 2
    w, h = ((hi - lo) / px).astype(int) + 1
    img = Image.new("1", (int(w), int(h)), 0)
    draw = ImageDraw.Draw(img)
    for v, tris in meshes:
        uv = (v[:, keep] - lo) / px
        for t in tris:
            draw.polygon([tuple(uv[i]) for i in t], fill=1)
    return float(np.count_nonzero(np.array(img))) * (px * px) * 1e-6


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------
def to_gltf(p):
    """CAD (x fwd, y left, z up, mm) -> glTF (X left, Y up, Z fwd, m)."""
    p = np.asarray(p, dtype=float)
    return np.stack([p[..., 1], p[..., 2], p[..., 0]], axis=-1) / 1000.0


def mesh(shape, tol, ang):
    """Per-face tessellation with smooth normals inside each face."""
    verts, tris = shape.tessellate(tol, ang)
    v = np.array([[q.x, q.y, q.z] for q in verts], dtype=float)
    t = np.array(tris, dtype=np.int64).reshape(-1, 3)
    n = np.zeros_like(v)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    for k in range(3):
        np.add.at(n, t[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(ln > 1e-12, n / np.maximum(ln, 1e-12), np.array([0.0, 0.0, 1.0]))
    return v, t, n


def write_glb(path, nodes, root_extras):
    bin_chunks, views, accessors, meshes, materials, mat_ids = bytearray(), [], [], [], [], {}

    def view(data, target):
        while len(bin_chunks) % 4:
            bin_chunks.append(0)
        views.append({"buffer": 0, "byteOffset": len(bin_chunks), "byteLength": len(data), "target": target})
        bin_chunks.extend(data)
        return len(views) - 1

    def accessor(arr, ctype, typ, target, minmax=False):
        a = {"bufferView": view(arr.tobytes(), target), "componentType": ctype, "count": int(arr.shape[0]),
             "type": typ}
        if minmax:
            a["min"] = [float(x) for x in arr.min(0)]
            a["max"] = [float(x) for x in arr.max(0)]
        accessors.append(a)
        return len(accessors) - 1

    gl_nodes = [{"name": "halo_scout", "children": [], "extras": root_extras}]
    for nd in nodes:
        if nd["material"] not in mat_ids:
            rgba, metal, rough = MATERIALS[nd["material"]]
            materials.append({"name": nd["material"], "pbrMetallicRoughness": {
                "baseColorFactor": list(rgba), "metallicFactor": metal, "roughnessFactor": rough}})
            mat_ids[nd["material"]] = len(materials) - 1
        pos = nd["positions"].astype(np.float32)
        nor = nd["normals"].astype(np.float32)
        idx = nd["indices"].astype(np.uint16 if len(pos) < 65536 else np.uint32).reshape(-1)
        prim = {"attributes": {"POSITION": accessor(pos, 5126, "VEC3", 34962, True),
                               "NORMAL": accessor(nor, 5126, "VEC3", 34962)},
                "indices": accessor(idx, 5123 if idx.dtype == np.uint16 else 5125, "SCALAR", 34963),
                "material": mat_ids[nd["material"]]}
        meshes.append({"name": nd["name"], "primitives": [prim]})
        g = {"name": nd["name"], "mesh": len(meshes) - 1, "translation": [float(x) for x in nd["translation"]],
             "extras": nd["extras"]}
        gl_nodes.append(g)
        gl_nodes[0]["children"].append(len(gl_nodes) - 1)
    while len(bin_chunks) % 4:
        bin_chunks.append(0)
    doc = {"asset": {"version": "2.0", "generator": "Halo Systems halo_scout.py (CadQuery 2.8, custom glTF writer)"},
           "scene": 0, "scenes": [{"name": "halo_scout", "nodes": [0]}], "nodes": gl_nodes, "meshes": meshes,
           "materials": materials, "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(bin_chunks)}]}
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_chunks)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(bin_chunks), 0x004E4942) + bytes(bin_chunks))


def placed_shape(p):
    """Shape in the CAD frame (props are built at the origin and moved to their hub)."""
    return p.shape.translate(V(*p.pivot)) if p.name.startswith("prop_") else p.shape


def main():
    EXPORTS.mkdir(exist_ok=True)
    bom = read_bom()
    parts = build_parts()
    for p in parts:
        p.shape = placed_shape(p)
    printed, points = assign_masses(parts, bom)

    # Centre of mass (mm, CAD frame).
    masses = [(p.mass_g, np.array(p.shape.Center().toTuple())) for p in parts if p.mass_g > 0] + points
    m_tot = sum(m for m, _ in masses)
    com = sum(m * c for m, c in masses) / m_tot

    # Inertia about the centre of mass (kg m^2, CAD axes).
    inertia = np.zeros((3, 3))
    for p in parts:
        if p.mass_g > 0:
            inertia += inertia_about(p.shape, p.mass_g, com)
    for m, c in points:
        d = c - com
        inertia += m * (np.dot(d, d) * np.eye(3) - np.outer(d, d)) * 1e-9

    # Low-poly meshes for the glTF and the projected areas.
    nodes, tri_count, cad_meshes, cad_meshes_noprop = [], 0, [], []
    for p in parts:
        is_prop = p.name.startswith("prop_")
        tol, ang = (0.6, 0.6) if not is_prop else (0.8, 0.8)
        v, t, n = mesh(p.shape, tol, ang)
        cad_meshes.append((v, t))
        if not is_prop:
            cad_meshes_noprop.append((v, t))
        tri_count += len(t)
        origin = np.array(p.pivot) if p.pivot else np.array(p.shape.Center().toTuple())
        extras = {"label": p.label, "group": p.group, "mass_g": round(p.mass_g, 2),
                  "explode_m": [round(x, 4) for x in to_gltf(np.array(p.explode)).tolist()]}
        if p.bom_id:
            extras["bom_id"] = p.bom_id
        if p.printed:
            extras["printed"] = p.printed
        if is_prop:
            extras.update(p.extras, spin_axis=[0, 1, 0],
                          note="ccw = positive rotation about +Y seen from above")
        nodes.append({"name": p.name, "material": p.material, "translation": to_gltf(origin - com),
                      "positions": to_gltf(v - origin), "normals": to_gltf(n * 1000.0),
                      "indices": t, "extras": extras})

    frontal = projected_area(cad_meshes_noprop, 0)
    frontal_props = projected_area(cad_meshes, 0)
    top = projected_area(cad_meshes_noprop, 2)
    bb = cq.Compound.makeCompound([p.shape for p in parts]).BoundingBox()
    i_g = inertia.copy()
    # Express the inertia in glTF axes too (X left = CAD y, Y up = CAD z, Z fwd = CAD x).
    perm = [1, 2, 0]
    i_gltf = i_g[np.ix_(perm, perm)]

    props = {
        "units": {"length": "m", "mass": "kg", "inertia": "kg m^2"},
        "mass_kg": round(m_tot / 1000.0, 4),
        "mass_breakdown_g": {"parts_with_solids": round(sum(p.mass_g for p in parts), 1),
                             "point_masses": round(sum(m for m, _ in points), 1)},
        "printed_mass_g": {k: round(v, 1) for k, v in printed.items()},
        "centre_of_mass_cad_m": [round(x / 1000.0, 4) for x in com],
        "centre_of_mass_note": "CAD frame: x forward, y left, z up; origin at the centre of the arms' underside",
        "inertia_about_com_cad_axes": [[round(x, 6) for x in row] for row in i_g],
        "inertia_about_com_gltf_axes": [[round(x, 6) for x in row] for row in i_gltf],
        "principal_moments_kg_m2": sorted(round(x, 6) for x in np.linalg.eigvalsh(i_g)),
        "motor_axis_radius_m": round(MOTOR_R / 1000.0, 4),
        "prop_tip_radius_m": round((MOTOR_R + PROP_DIAMETER / 2) / 1000.0, 4),
        "rotor_disk_area_m2": round(4 * math.pi * (PROP_DIAMETER / 2000.0) ** 2, 4),
        "frontal_area_m2_without_props": round(frontal, 4),
        "frontal_area_m2_with_props": round(frontal_props, 4),
        "top_area_m2_without_props": round(top, 4),
        "bounding_box_m": {"x": round(bb.xlen / 1000, 3), "y": round(bb.ylen / 1000, 3), "z": round(bb.zlen / 1000, 3)},
        "glb_triangles": tri_count,
        "thermal_camera": {"native_hfov_deg": THERMAL_HFOV_DEG, "used_hfov_deg": THERMAL_USED_FOV_DEG,
                           "swath_at_20m_native_m": round(2 * 20 * math.tan(math.radians(THERMAL_HFOV_DEG / 2)), 2),
                           "swath_at_20m_used_m": round(2 * 20 * math.tan(math.radians(THERMAL_USED_FOV_DEG / 2)), 2),
                           "columns_kept_of_160": round(160 * math.tan(math.radians(THERMAL_USED_FOV_DEG / 2))
                                                        / math.tan(math.radians(THERMAL_HFOV_DEG / 2))),
                           "ground_sample_m_at_20m": round(2 * 20 * math.tan(math.radians(THERMAL_HFOV_DEG / 2)) / 160, 3)},
        "rgb_camera": {"hfov_deg": RGB_HFOV_DEG, "tilt_down_deg": RGB_TILT_DEG},
    }
    root_extras = {"units": "metres", "up": "+Y", "forward": "+Z", "left": "+X",
                   "origin": "centre of mass", "mass_kg": props["mass_kg"],
                   "motor_axis_radius_m": props["motor_axis_radius_m"],
                   "prop_diameter_m": PROP_DIAMETER / 1000.0,
                   "camera_down_hfov_deg": THERMAL_USED_FOV_DEG,
                   "source": "hardware/cad/halo_scout.py"}
    write_glb(EXPORTS / "halo_scout.glb", nodes, root_extras)

    # STEP assembly (named, coloured parts) and one STL, both in mm, CAD frame shifted to the CoM.
    shift = V(*(-com))
    assy = cq.Assembly(name="halo_scout")
    for p in parts:
        rgba = MATERIALS[p.material][0]
        assy.add(p.shape.translate(shift), name=p.name, color=cq.Color(*rgba))
    assy.export(str(EXPORTS / "halo_scout.step"), exportType="STEP")
    comp = cq.Compound.makeCompound([p.shape.translate(shift) for p in parts])
    cq.exporters.export(comp, str(EXPORTS / "halo_scout.stl"), tolerance=0.25, angularTolerance=0.3)

    # Printable parts, one STL each (mm, centred, resting on z = 0). Rotated copies are written once.
    # They are envelope geometry: add holes and fits for your screws and boards before printing.
    printdir = EXPORTS / "print"
    printdir.mkdir(exist_ok=True)
    for old in printdir.glob("*.stl"):
        old.unlink()
    groups = {}
    for p in parts:
        if p.printed:
            key = p.name.rsplit("_", 1)[0] if p.name.startswith(("landing_leg_", "antenna_mount_")) else p.name
            groups.setdefault(key, []).append(p)
    for key, ps in groups.items():
        s = ps[0].shape
        bb = s.BoundingBox()
        s = s.translate(V(-bb.center.x, -bb.center.y, -bb.zmin))
        fname = f"{key}_{ps[0].printed.lower()}" + (f"_x{len(ps)}" if len(ps) > 1 else "") + ".stl"
        cq.exporters.export(s, str(printdir / fname), tolerance=0.1, angularTolerance=0.2)

    (EXPORTS / "mass_properties.json").write_text(json.dumps(props, indent=2) + "\n")
    print(json.dumps({k: props[k] for k in ("mass_kg", "printed_mass_g", "centre_of_mass_cad_m",
                                            "principal_moments_kg_m2", "frontal_area_m2_without_props",
                                            "frontal_area_m2_with_props", "top_area_m2_without_props",
                                            "glb_triangles")}, indent=1))


if __name__ == "__main__":
    main()
