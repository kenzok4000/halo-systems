# Building a Halo scout

This is the build for one drone of the swarm, written for a hobbyist who has built an FPV quad before. Parts and prices are in [BOM.md](BOM.md). The CAD and the printable parts are in [cad/](cad/) and [exports/](exports/). Repeat it 16 times for a full swarm.

The drone scouts a block of dry scrub or forest after lightning. It looks down with a radiometric thermal camera for smouldering spots, looks ahead with an RGB camera for smoke, samples the air with a particulate sensor, and reports to the ground station over a shared 900 MHz radio. It carries nothing else.

## Time and tools

**Time (estimates).** The first drone takes about 10 hours at the bench, plus 6 to 8 hours of unattended printing for its 72 g of PETG and TPU. Once the process is settled, each further drone takes about 4 hours at the bench. A 16-drone swarm is roughly 70 hours of bench work, plus a day for the radio network and test flights.

**Tools.**

- Temperature-controlled soldering iron, 60 W or more, with a chisel tip for the XT60 and 14 AWG leads and a fine tip for the flight controller pads. Solder, flux, desoldering braid.
- Heat gun and heat-shrink.
- Hex drivers 1.5, 2.0 and 2.5 mm; an 8 mm socket or prop wrench for the M5 prop nuts.
- Flush cutters, wire strippers for 14 to 30 AWG, tweezers, a small pick for pulling JST pins.
- Multimeter and a fused "smoke stopper" for the first power-up.
- Calipers, medium-strength threadlocker.
- An FDM printer with a 220 x 220 mm bed that prints PETG and TPU.
- A laptop with Mission Planner or QGroundControl, Raspberry Pi Imager, the ExpressLRS Configurator and RFDesign's modem tools.
- A balance charger and fire-resistant LiPo bags (both in the ground station section of the BOM).

## Wiring at a glance

| Device | Connects to | Power | Notes |
|---|---|---|---|
| Tekko32 F4 4-in-1 ESC | Battery through the XT60 lead; FC through the supplied 8-pin lead to ESC connector 1 | Battery | Pull the telemetry (TLM) pin out of the lead, see step 4 |
| Kakute H7 flight controller | ESC lead | 5 V from its own regulator | ArduPilot target `KakuteH7` |
| Micro M10 GNSS and compass | UART3 (`SERIAL3`); compass on the SDA and SCL pads | FC 5 V | |
| RFD900ux swarm radio | UART1 (`SERIAL1`) | Matek BEC 5 V | Draws up to about 800 mA at 1 W, too much for the FC regulator |
| Holybro Remote ID | UART6 (`SERIAL6`, with `BRD_ALT_CONFIG` 1) | FC 5 V | |
| RadioMaster RP1 V2 safety receiver | R7 pin on ESC connector 2 (`SERIAL7`, receive only) | FC 5 V | Set the receiver to output SBUS, see step 7 |
| Raspberry Pi Zero 2 W | UART4 (`SERIAL4`) to GPIO 14 and 15 | Matek BEC 5 V into the 5 V header pins | |
| FLIR Lepton 3.5 on PureThermal Mini Pro | Pi micro-USB data port through the OTG adapter | From the Pi's USB | Shows up as a USB video camera |
| Camera Module 3 | Pi camera connector, 22-pin to 15-pin ribbon | From the Pi | |
| Sensirion SPS30 | Pi I2C (GPIO 2 and 3); SEL pin to ground for I2C | Matek BEC 5 V | |
| VIFLY strobe | Nothing | Its own cell | |

The Kakute H7 brings out four two-way serial ports and one receive-only pin. GNSS, swarm radio, companion and Remote ID each need a two-way port, so the safety receiver goes on the receive-only R7 pin as SBUS. The port map comes from [ArduPilot's Kakute H7 page](https://ardupilot.org/copter/docs/common-holybro-kakuteh7.html).

## Steps

### 1. Print the parts

Slice the files in [exports/print/](exports/print/): sensor pod, thermal mount and sun shroud, RGB camera mount, smoke sensor bracket and GNSS mast in PETG; four landing legs and two antenna mounts in TPU 95A. The CAD masses assume solid shells and 55% fill for the legs. These files are envelope geometry from the CAD. Before printing, add holes and slots to suit your screws, the Pi's M2.5 holes and the cable routes. Print the thermal shroud in a light colour so it stays cool in the sun.

### 2. Frame

Assemble the GEPRC Mark4 7-inch frame with its standoffs, following the kit. Use threadlocker on every metal-to-metal screw. Leave the top plate off until step 8. Fit the TPU landing legs under the arms, about 80 mm from the centre, and the TPU antenna mounts on the two rear arms.

### 3. Motors

Mount the four EMAX ECO II 2807 motors with M3 screws short enough not to touch the windings: check by turning each bell by hand. Route the motor wires along the arms and secure them with heat-shrink or ties so props cannot reach them. Spin directions are set in software, so the wire order does not matter yet.

### 4. ESC and power

Solder the motor wires to the Tekko32 F4 pads. Solder the XT60 lead and the low-ESR capacitor to the ESC battery pads, observing polarity. Solder the Matek BEC12S-Pro input to the same battery pads and set its output to 5 V. Before plugging anything else in, check the BEC output with the multimeter.

Take the supplied 8-pin ESC lead and pull the telemetry (TLM) pin out of its housing with a pick. ESC telemetry is not used, and the free R7 line will carry the safety receiver. Plug the lead into ESC connector 1 on the flight controller.

### 5. Flight controller

Mount the Kakute H7 on the soft grommets on top of the ESC, arrow forward. First power-up through the smoke stopper, with no props.

Flash ArduPilot Copter for the `KakuteH7` target over DFU, as described on [ArduPilot's Kakute H7 page](https://ardupilot.org/copter/docs/common-holybro-kakuteh7.html). Remote ID needs the OpenDroneID feature, which some standard builds leave out; if so, build firmware with it enabled using the [custom firmware server](https://custom.ardupilot.org/). The board's defaults already match the Tekko32 for battery voltage and current monitoring.

### 6. GNSS and compass

Print the GNSS mast and mount the Micro M10 at the top, arrow forward, as far from the power wiring as it will go. Wire it to UART3 and the I2C pads. Set `SERIAL3_PROTOCOL` 5 (GPS).

### 7. Radios

**Swarm radio.** Mount the RFD900ux at the rear on the arms with its two pigtails running to the antennas on the rear arms. Wire its data lines to UART1 and its power to the Matek BEC. Set `SERIAL1_PROTOCOL` 2 (MAVLink 2) and `SERIAL1_BAUD` 57. In the EU and the UK use the 868 MHz RFD868ux-EU in place of the US 900 MHz version.

**Safety receiver.** Flash the RP1 V2 with ExpressLRS 3, set its serial protocol to SBUS in the receiver's web page ([ExpressLRS serial protocols](https://www.expresslrs.org/software/serial-protocols/)) and wire its output to R7 on ESC connector 2. Set `SERIAL7_PROTOCOL` 23 (RC input) and `SERIAL7_OPTIONS` 3, the value ArduPilot's Kakute H7 page gives for SBUS. Every drone's receiver uses the same [binding phrase](https://www.expresslrs.org/quick-start/binding/), so one handset reaches the whole swarm. Set the handset's telemetry ratio to Off so 16 receivers never answer at once.

**Remote ID.** Wire the Holybro Remote ID module to UART6 and set `BRD_ALT_CONFIG` 1, `SERIAL6_PROTOCOL` 2, `DID_ENABLE` 1 and `DID_MAVPORT` 6. Follow [ArduPilot's Remote ID page](https://ardupilot.org/copter/docs/common-remoteid.html) for the module's own setup. See the legal note on what this module does and does not satisfy.

### 8. Companion computer and sensors

**Raspberry Pi Zero 2 W.** Flash Raspberry Pi OS Lite (64-bit) with SSH enabled. In `raspi-config`, turn the serial login shell off and the serial hardware on. Add `dtoverlay=disable-bt` to `config.txt` so the full UART is on GPIO 14 and 15. Wire GPIO 14 to R4 and GPIO 15 to T4 on the flight controller, plus ground. Set `SERIAL4_PROTOCOL` 2 and `SERIAL4_BAUD` 921. Power the Pi from the Matek BEC into the 5 V and ground header pins, never from the flight controller. Turn Wi-Fi off in flight (`rfkill block wifi`) so it does not crowd the 2.4 GHz safety link; keep it for bench set-up. [ArduPilot's guide](https://ardupilot.org/dev/docs/raspberry-pi-via-mavlink.html) covers the MAVLink link.

**Thermal camera.** Seat the Lepton 3.5 on the PureThermal Mini Pro and fix the board in the printed thermal mount, lens looking straight down through the pod window. Connect it to the Pi's data port with the JST-SR cable and the OTG adapter, shortened to fit. It appears as a USB video camera. Request the 16-bit (Y16) format: with the Lepton's TLinear output on, each pixel is a temperature in hundredths of a kelvin. The Lepton sees 57 degrees across; keep the middle 122 of its 160 columns to match the simulator's 45 degrees.

**RGB camera.** Fit the Camera Module 3 in its printed mount at the front, tilted 20 degrees down, and connect it to the Pi with the Zero ribbon.

**Smoke sensor.** Fit the SPS30 on its bracket at the front of the top plate, inlet up and clear of the battery. Tie its SEL pin to ground for I2C, wire SDA and SCL to GPIO 2 and 3, and power it from the BEC. The rotors push air through it, so read it as a rise over the block's background, not as an absolute PM2.5 figure.

**Software.** The Pi reads the thermal frames, the RGB frames and the SPS30, flags hotspots and smoke, and sends them to the flight controller as MAVLink messages. The swarm radio carries them to the ground. This repository does not yet contain that onboard program or a MAVLink bridge for the simulator's controller; see the gaps in the [README](README.md).

Close the pod, fit the top plate, strap the battery on top and mount the strobe behind it.

### 9. ArduPilot set-up

Do the standard [first-time set-up](https://ardupilot.org/copter/docs/initial-setup.html): frame, [accelerometer](https://ardupilot.org/copter/docs/common-accelerometer-calibration.html), [compass](https://ardupilot.org/copter/docs/common-compass-calibration-in-mission-planner.html), radio, then [motor order and direction](https://ardupilot.org/copter/docs/connect-escs-and-motors.html) with props off. Then set:

| Parameter | Value | Why |
|---|---|---|
| `FRAME_CLASS`, `FRAME_TYPE` | 1, 12 | Quad, Betaflight X: the board's default, matching the ESC lead. Spin directions are the same as ArduPilot's standard X. |
| `MOT_PWM_TYPE` | 6 | DShot600 to the AM32 ESC |
| `SYSID_THISMAV` | 1 to 16 | One number per drone; the ground station is 255 |
| `BATT_CAPACITY` | 2200 | |
| `BATT_LOW_VOLT`, `BATT_FS_LOW_ACT` | 21.0, 2 | Return home at 3.5 V per cell |
| `BATT_CRT_VOLT`, `BATT_FS_CRT_ACT` | 19.8, 1 | Land at 3.3 V per cell |
| `FS_THR_ENABLE`, `FS_GCS_ENABLE` | 1, 1 | Return home if the safety link or the swarm link is lost ([failsafes](https://ardupilot.org/copter/docs/failsafe-landing-page.html)) |
| `FENCE_ENABLE`, `FENCE_TYPE`, `FENCE_ACTION` | 1, 7, 1 | Height, circle and polygon fence around the block ([fences](https://ardupilot.org/copter/docs/common-geofencing-landing-page.html)) |
| `FENCE_ALT_MAX` | 55 | Metres; above the highest return height below |
| `RTL_ALT` | 2500 + 150 x (drone number - 1) | Centimetres. Staggers the return heights from 25 m to 47.5 m so drones never meet on the way home |
| `FLTMODE1`, `FLTMODE4`, `FLTMODE6` | 4, 6, 9 | One three-position switch: GUIDED, RTL, LAND ([modes](https://ardupilot.org/copter/docs/flight-modes.html)) |
| `LAND_REPOSITION` | 0 | Stops the shared handset's sticks moving 16 landing drones at once |
| `ANGLE_MAX`, `WPNAV_ACCEL` | 3000, 250 | The real drone has about four times the simulator's thrust margin; these keep it within the simulated envelope |
| `WPNAV_SPEED` | 600 | 6 m/s, the scouting speed in the simulator |

Tune with [ArduPilot's tuning process](https://ardupilot.org/copter/docs/tuning-process-instructions.html) on one drone, then copy its parameter file to the others, changing only `SYSID_THISMAV` and `RTL_ALT`. Check [vibration](https://ardupilot.org/copter/docs/common-measuring-vibration.html) in the first logs.

### 10. Swarm radio network

The RFD900ux ships with point-to-point firmware. Flash all 17 radios (16 drones and the ground unit) with RFDesign's multipoint firmware, which the [listing](https://irlock.com/products/rfd-900ux-us-fcc-approved) names. Give each radio its own node ID, and the same network ID, air data rate and channel settings. Start at 20 dBm (100 mW) and raise it only if the link margin needs it: every watt comes out of the 8 W avionics budget. Lower the MAVLink stream rates (`SR1_` parameters) to 1 or 2 Hz per stream, since 16 drones share one channel.

### 11. Bench tests and first hover

1. Props off: check every failsafe. Switch the handset off and confirm RTL; unplug the ground radio and confirm RTL.
2. Props on, in an open field, one drone: hover in LOITER at 3 m, then at 20 m. Check the thermal and RGB streams on the ground station.
3. Fly one scripted lawnmower pass over a known warm patch, such as a sunlit dark tarp, at 20 m and 6 m/s.
4. Add drones in steps: 2, then 4, then 16. Each step only after the previous one flew clean.

### 12. Calibrate the simulator against the real drone

The simulator's numbers are estimates, not measurements (see the docstring of `src/halo_systems/sim/body.py`). Measure four things on the first finished drone:

1. **Mass.** Weigh it ready to fly, battery on.
2. **Avionics power.** On the bench, props off, everything streaming: battery voltage times current.
3. **Hover power.** Hover for 60 s at 20 m in still air and read the mean `BAT.Volt` and `BAT.Curr` from the log.
4. **Usable energy.** Fly a hover to the low-voltage return and add up the watt-hours in the log.

Then the figure of merit that `body.py` would need is `FoM = T * v_h / (P_hover - P_avionics)`, with `T = m * 9.81` and `v_h = sqrt(T / (2 * 1.225 * 0.0993))`. For example, at 1.006 kg, T = 9.87 N and v_h = 6.37 m/s; a measured 140 W hover with 6.6 W of avionics gives FoM = 0.47. [budget.py](budget.py) runs the same formula; put the measured values into a copy of `DroneBody` with `dataclasses.replace` to see the change in endurance before deciding whether the simulator's parameters should move.

### 13. Pre-flight checks, every sortie

- Authorisation, registration and Remote ID in order for this operation (see below). The incident's air operations know you are flying.
- Wind under the limit you tested in. No rain on the open smoke sensor inlet.
- Each drone: props tight and undamaged, battery charged and strapped, strobe on, cameras clean, GNSS 3D fix with 10 or more satellites, Remote ID broadcasting, no pre-arm warnings.
- Swarm: all 16 drones and the ground radio show on the ground station, each with its own system ID. Fence and return heights loaded. The handset switch is in GUIDED and moves every drone to RTL when flipped.
- Crew: one remote pilot on the ground station and handset, one observer watching the sky for crewed aircraft. Both know the abort call: the switch to RTL, then LAND if a crewed aircraft comes close.

## Legal note

Rules differ by country and change often. Check the regulator's current pages before any flight. All pages below were read on 2026-10-14.

**Near a fire.** Fire aviation is the first concern. In the US the FAA [prohibits flying a drone over emergency operations, including wildfires](https://www.faa.gov/uas/getting_started/where_can_i_fly/airspace_restrictions), unless the flight is part of the response. Fire agencies run drones through the National Interagency UAS Program, coordinated with fire aviation and under temporary flight restrictions ([FAA, UAS in wildfire response](https://www.faa.gov/uas/public_safety_gov/uas-wildfire-response)). Treat the swarm as a tool for a fire agency or a land manager working with one, not as something to fly at a fire on your own.

**United Kingdom.** At 1 kg, the operator needs an Operator ID and every remote pilot a Flyer ID ([CAA, getting what you need to fly legally](https://www.caa.co.uk/drones/open-category/drone-code/getting-what-you-need-to-fly-legally/)). The CAA lists both flying beyond visual line of sight and swarm operations as examples that need an operational authorisation in the Specific Category, applied for through UK SORA; PDRA01 covers visual line of sight only ([CAA, Specific Category overview](https://www.caa.co.uk/drones/specific-category/specific-category-overview/)). The CAA also publishes guidance for [swarm operations](https://www.caa.co.uk/drones/specific-category/swarm-operations/).

**European Union.** The operator registers with the national aviation authority of their country of residence; a drone of 250 g or more, or one with a camera, needs it ([EASA, registration](https://www.easa.europa.eu/the-agency/faqs/registration-requirements)). Beyond visual line of sight is a Specific Category operation and needs an operational authorisation from that authority, through a standard scenario, a PDRA or a SORA ([EASA, Specific Category](https://www.easa.europa.eu/en/domains/drones-air-mobility/operating-drone/specific-category-civil-drones)).

**United States.** Every drone flown under Part 107 must be registered, whatever its weight, and must meet Remote ID ([FAA, registration](https://www.faa.gov/uas/getting_started/register_drone)). A Remote ID broadcast module only allows flight within sight of the pilot; beyond sight needs a standard Remote ID drone ([FAA, Remote ID](https://www.faa.gov/uas/getting_started/remote_id)). The Holybro module is sold for integrators who file their own declaration of compliance with the FAA ([Holybro](https://docs.holybro.com/radio/remote-id/overview-and-spec)), so a home-built drone counts as standard Remote ID only once its builder has filed that declaration. Flying beyond visual line of sight (section 107.31) and one pilot flying several drones (section 107.35) each need a [Part 107 waiver](https://www.faa.gov/uas/commercial_operators/part_107_waivers). The FAA's proposed Part 108 rule for beyond visual line of sight flight was [published on 7 August 2025](https://www.federalregister.gov/documents/2025/08/07/2025-14992/normalizing-unmanned-aircraft-systems-beyond-visual-line-of-sight-operations) and its comment period was reopened in January 2026. A search of the Federal Register on 2026-10-14 found no final rule yet.

**Radio.** The RFD900ux-US is for 902 to 928 MHz, licence-free in the US, Canada, Australia and New Zealand according to its [listing](https://irlock.com/products/rfd-900ux-us-fcc-approved). Use an 868 MHz version in the UK and EU, and keep within the local power and duty-cycle limits.
