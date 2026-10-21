# Architecture

Halo Systems has three loops that run at very different speeds. Only the fastest one touches the motors, and no language model is in it.

| Loop | Rate | Who decides | What it decides |
|---|---|---|---|
| Control | 50 Hz, every drone | Conventional controller with a safety filter | Thrust vector |
| Supervision | every 5 s of simulated time | Rule supervisor or Nemotron | Formation, spacing, speed, altitude, safety margin |
| Mission | once, before flight | Operator text compiled by Nemotron or a regex baseline | The mission plan the supervisor returns to |

```
 operator text ──► mission compiler ──► validator ──► mission plan
                                                          │
 status report every 5 s ──► supervisor ──► validator ──► directive
                                                              │
 50 Hz, per drone: slot tracking + consensus ──► CBF safety filter ──► thrust ──► physics
```

## Where each loop runs

[results/compute/REPORT.md](../results/compute/REPORT.md), made by `tools/compute_table.py`, times the controller, the rule supervisor, the station and the forecast on development valleys 3000-3003 (16 drones), flown one at a time; the valley compile's latencies are from its scored calls (`results/valley_eval`). The timings are from the development machine (x86_64, 4 cores), not a drone or a Jetson.

* **Flight control** runs on each drone at 50 Hz. All 16 drones' commands in one vectorised call took 3.33 ms per 20 ms step (p95 4.57 ms). In the committed valley sweep (seeds 4000-4019), which flew its flights in parallel so its times depend on the machine's load, one drone's controller alone took 3.2 ms per step, against 5.5 ms for all 16.
* **The rule supervisor** runs on the ground station every 5 s. A decision took a median 0.035 ms (p95 0.063 ms).
* **The station** (smoke bearings, thermal finds, located fires, cards to fire dispatch) runs on the ground station (a Jetson Orin Nano is the candidate) once a second. It took a median 2.56 ms per step (p95 17.59 ms), steps that ran a forecast included, busy 3.56% of the flight.
* **The spread forecast** (with crew briefs and pre-signed alert checks) runs once a fire is located: every 2 min, sooner when the picture changes (at least 30 s apart). A forecast took a median 1620.1 ms (p95 1790.3 ms).
* **The valley compile** runs once per job on Nebius Token Factory (Nemotron 3 Nano, reasoning): median 4.7 s, p95 10.44 s (47 calls).
* **The test engineer** (Nemotron, proposing stress tests) runs on Token Factory before deployment, per batch of tests. It is not in flight and was not timed here (`results/valley_stress`).

When the cloud link drops, flight control, the rule supervisor, the station and the forecast are unaffected: none of them uses the link, and no model takes part in any alert or card. The valley compile's call fails and the regex baseline reads the request instead; the operator checks the plan. A Nemotron supervisor falls back to the rules: on the forest trail all 18 calls fell back, and the flight completed with no drones lost.

## Simulator (`sim/`)

* **Body and power** (`body.py`, `physics.py`). Point-mass quadcopter: 1.2 kg, thrust-to-weight 2.5, drag area 0.035 m², radius 0.15 m, first-order motor lag of 0.05 s, and no downward thrust, since a quadcopter cannot push itself down. Rotor power comes from momentum theory: induced velocity from Glauert's relation v_i⁴ + V²v_i² = v_h⁴, plus parasitic power D·V. That gives about 17 minutes of hover on the battery. Energy per kilometre has a minimum at an economical cruise speed, which the supervisor can choose.
* **Wind** (`wind.py`). Steady mean, Ornstein-Uhlenbeck turbulence that is common to the swarm plus a smaller local part per drone, and gust fronts. A gust front sweeps across the formation, reaching each drone after a delay set by its position along the gust direction, so the formation is pulled apart and not just pushed sideways.
* **Sensing and radio** (`sensing.py`). Each drone's navigation fix has white noise plus a slowly drifting bias, like GNSS. Each drone broadcasts its estimate every step. Messages arrive after a latency with jitter, can be lost, are cut off by range limits and blackouts, and are dead-reckoned forward by their age.
* **World** (`world.py`). Trees (cylinders) and buildings (boxes) with a clearance query. `free_width_ahead` measures the obstacle-free lane along the path, from the formation's tail to 100 m ahead of its centre.
* **Ground sensor and targets** (`scenario.py`, `engine.py`). A scenario can carry targets on the ground (smouldering hotspots) and a camera looking straight down from each drone. The footprint is a disc of radius altitude × tan(FOV/2), the circle inscribed in the image. Ten times a second the engine adds dwell to every target inside some flying drone's footprint; a target is found once its dwell reaches the sensor's dwell time, and the find is an event with the drone and the time. Coverage is a grid of 5 m cells over the survey block, marked when a cell's centre falls inside a footprint. The supervisor's report carries the count found so far and the area covered, not the hotspots' positions.
* **Engine** (`engine.py`). Fixed 0.02 s step. It applies scripted events (motor failures, operator orders, blackouts), detects drone-to-drone collisions by closest approach within each step and obstacle and ground strikes at the end of each step, retires drones whose battery reaches the reserve, and builds the status report for the supervisor. A supervisor's answer is applied only after its measured latency has passed in simulated time, so a slow model has a real cost.
* **Terrain** (`terrain.py`). A height grid with land cover, used the way a national mapping agency's terrain model is. The drones keep their height above the ground plus tree crowns, with a look-ahead so a rising slope is climbed before it is reached. Line of sight is checked against the ground, so smoke behind a ridge is not seen.
* **Fire spread** (`fire.py`). Rothermel's surface fire model with Albini's revisions, on Anderson's standard fuel models, with wind and slope added as vectors. Arrival times over the landscape come from a minimum-travel-time calculation, as in FlamMap. It is used for the true fires and for the forecast.
* **Smoke** (`smoke.py`). What a fire's plume looks like from a drone's forward camera, what a particulate (PM2.5) sensor reads inside it, and the triangulation that turns smoke bearings into located plumes.
* **Wildfire valley** (`wildfire.py`). The wildfire world and the ground station. Lightning fires smoulder, then spread over the terrain's fuels. The station locates and maps fires from thermal finds and smoke bearings, forecasts their spread with an ensemble of spread runs, briefs the fire crews and alerts the phones (simulated only) in fixed evacuation zones. Under today's rule (`alert_rule` "forecast", the scenario default) fixed rules set each zone's level, and Level 3 also counts smouldering fires as if they took off now. Under the pre-signed rules (`alert_rule` "presigned"), the entry's alert design, public alerts go out automatically only under trigger rules the alerting authority signed in advance, and each located fire also goes to fire dispatch as a card. Nothing here calls a language model. See [WILDFIRE.md](WILDFIRE.md).
* **Crew outputs** (`crew_export.py`). Each fire's brief as a map file (GeoJSON or KML) and as a short message in the order of the NWCG size-up report. Every feature says it is simulated.
* **Without drones** (`detection.py`). How the valley's fires would be found by today's methods (satellites, an AI camera network, public calls), as rules from published figures applied to the same true fires. No flight is needed and nothing is tuned.

## Controllers (`control/`)

Both controllers get the same noisy observations and the same acceleration limit (8 m/s²).

* **Original** (`original.py`). A port of the passive formation law from an earlier private simulator. The velocity target is the slot velocity plus a gain on slot error and on neighbour repulsion, and the acceleration follows the velocity error. Slots stay in drone-number order and jump at once on a formation change. The original had no obstacles, so the port adds textbook potential-field obstacle repulsion with the same gain.
* **Improved** (`improved.py`). Standard techniques from the multi-robot literature, each of which can be switched off for the ablation study:
  1. *reassign*: Hungarian assignment of drones to slots after every formation change or loss, so paths do not cross and gaps close.
  2. *blend*: synchronised straight-line transitions to the new slots at a bounded speed (as in CAPT, Turpin et al.).
  3. *pace*: the virtual leader slows when the formation is out of shape and before bends.
  4. *consensus*: a term on the relative shape with neighbours heard in the last second, so a common push does not tear the formation.
  5. *cbf*: a control-barrier-function safety filter (Wang, Ames and Egerstedt, 2017) for drone-to-drone, obstacle and ground clearance. Each drone keeps the acceleration closest to its nominal command that satisfies all its constraints, found approximately with a fixed 12 rounds of Dykstra's alternating projections. Safety distances include body size, the directive's safety margin, two standard deviations of navigation error, and a margin that grows with the age of each neighbour's last message. The filter sees obstacles out to 15 m, like a forward depth sensor. Its obstacle barrier only holds if a drone closes on an obstacle no faster than √(2 a_brake gap), so near mapped obstacles the leader is held to the speed at which an obstacle first seen at 15 m can still be avoided (about 9 m/s). The stress-test search found tree strikes at 8 to 10 m/s before this cap was added.
  6. *filter*: a complementary filter on the navigation estimate.
  7. *feedforward*: the slot's own acceleration, low-passed.

The other modules in `control/`:

* `directive.py`: the directive a supervisor sets and the 50 Hz controller flies. It never touches the motors.
* `formations.py`: formation shapes and the `Path` the formation centre follows.
* `assignment.py`: Hungarian assignment of drones to slots.
* `plan.py`: `FormationPlan`, the virtual leader and every drone's slot, computed alike by every drone from the mission and the directive. The two controllers differ in how they build it (*reassign*, *blend*, *pace*). An airspace hold stops its leader and lowers the plan to the hold altitude.
* `base.py`: `Observation` and the `Controller` interface.
* `cbf.py`: the control-barrier-function safety filter used by the improved controller (*cbf* above).
* `squads.py`: the swarm split into squads, each flying its own formation over its own area. An operator gives each area a number of drones (or asks for `auto`); each squad flies line abreast, no wider than its area, on a closed circuit of lanes until the patrol ends. Drones can be moved between squads mid-flight, and both squads' circuits are then re-planned for their new sizes.

## Supervisors (`supervisor/`)

All supervisors read the same status report (`Simulation.summary`) and return the same five-field directive.

* **Fixed** (`base.py`): flies the mission plan unchanged. The baseline for "no supervision".
* **Rule** (`rule.py`). Hand-written rules, tuned on seeds 0-9:
  * After gusts or near misses, widen spacing and margin for 15 s.
  * When more than half the radio links are stale, slow down.
  * When the battery cannot finish the leg, drop to the cheapest speed (with hysteresis).
  * When the formation does not fit the free corridor ahead, tighten spacing, and if that is not enough, switch to a column.
  * Otherwise return to the mission plan.
* **Nemotron** (`nemotron.py`). An NVIDIA Nemotron model on Nebius Token Factory reads the report and its own last four directives, and answers in JSON. Any failure (network, timeout, HTTP error, bad JSON, missing field) falls back to the rule supervisor's answer for that step, and the fallback is counted. In benchmarks, an error that would repeat on every call (no key, key rejected, budget cap) stops the run instead, so a flight flown by the rules is never reported as a Nemotron result.

## Safety validator (`safety/validator.py`)

Every directive, whoever proposed it, passes the validator before the controller sees it:

* Schema check: a known formation and finite numbers. A missing field keeps its current value, and the omission is logged as a violation.
* Clamping to the flight envelope: spacing 2-15 m, speed 0.5-12 m/s, altitude 5 m up to the scenario's ceiling, safety margin 0.5-4 m.
* A spacing floor of bodies + margin + 3√2 σ_nav, the smallest spacing the safety filter can hold with the navigation error present.
* A rate limit of one change per 3 s.

## Aircraft inbound (airspace hold)

When crewed firefighting aircraft (air tankers, helicopters, a lead plane) enter the area, the operator orders the swarm to hold: every flying drone stops where it is and descends to a low hold altitude, clear of the aircraft's working height, until the operator resumes. The leader brakes at 2 m/s², the formation keeps its shape and heading, and the plan's altitude ramps down at up to 2.5 m/s from where the drones are (drones in transit between squads stop too, and squad moves are refused while holding). On resume the swarm climbs back to the directive's altitude at the same rate, then speeds up and carries on the sweep from where it stopped. The ground station, smoke watch and alerts keep running; battery drains while hovering and drones still retire at the reserve.

The order passes `SafetyValidator.check_airspace` and is logged like a directive (`kind` "airspace"):

* `op` must be `hold` or `resume`.
* hold: the altitude (above the ground the plan follows, tree crowns included) defaults to 10 m and must be a finite number; it is clamped to between the 5 m floor and the lower of the directive's altitude and the swarm's altitude now (a hold never climbs, even when ordered while the drones are still climbing). A further hold while holding is accepted only if it is lower. A hold is never refused for timing and does not reset the directive rate limit.
* resume: only while holding, and no sooner than 3 s after the hold began.

Orders are scripted (`Scenario.airspace_script`, e.g. `[{"t": 600, "op": "hold"}, {"t": 900, "op": "resume"}]`) or injected during a flight: `sim.inject("aircraft", op="hold", altitude_m=None, at_s=None)`, or from the dashboard `POST /api/runs/<id>/inject {"kind": "aircraft", "op": "hold"}`. Each hold is reported in the metrics (`airspace_holds`, `airspace_held_s`, `airspace_clear_s_max`: from the order, or from a later, lower hold, until every drone is stopped at the hold altitude), in frames and in the supervisors' status report; runs without an order are unchanged. `tools/airspace_hold_demo.py` flies the wildfire valley with and without a hold.

## Model calls (`ai/`)

* `client.py`: OpenAI-compatible client for Nebius Token Factory with timeouts, retries with backoff and jitter, `Retry-After`, a client-side rate limit, JSON-mode fallback, response validation, and a redacted call log.
* `budget.py`: a spend ledger shared by every process, checked before each request. See [NVIDIA_NEBIUS.md](NVIDIA_NEBIUS.md).
* `cache.py`: a record/replay cache keyed on the model, messages, temperature and request options (reasoning switch, token limit). The supervisor's calls go through it, so any Nemotron supervisor flight can be replayed exactly without a key. The valley compiler's and the valley test engineer's calls go through it too. The mission compiler's and the benchmark test engineer's (`tester.py`) calls are kept in call logs instead.
* `prompts.py`: the Nemotron supervisor's prompt and the status message built for it. They must stay the same byte for byte, or the stored calls in `results/nemotron_cache.jsonl` become misses; `PROMPT_VERSION` is recorded with every supervisor decision.
* `mission.py`: the mission compiler, its civilian-use guard, the regex baseline and the scoring.
* `valley.py`: the valley mission compiler. Nemotron reads an operator's plain words, such as "check last night's strikes in the north block with 16 drones", and answers which drones, areas, patrol length and strikes they mean. Code applies that to the lightning strike list, picks the squads and checks the plan by fixed rules. The model never flies the drones, never picks a strike by itself and takes no part in alerts. Its calls are recorded in `results/valley_compile_cache.jsonl`; `regex_baseline` is what it is scored against (`halo valley-eval`), with the same civilian-use guard as the mission compiler.
* `tester.py`: the stress-test search (random, corners, cross-entropy method, Nemotron), and re-flying a search on another configuration.
* `valley_tester.py`: the test engineer for the wildfire valley. It searches the day's weather, fuels and haze for conditions where the ground station misses a fire, warns people too late, or sends a public alert it should not have. The flight under test uses the pre-signed alert rules. Random search, corner search and Nemotron share the budget. The model only proposes test conditions; no language model takes part in any alert, card or flight decision.
* `doctor.py`: the end-to-end check, which writes `evidence/latest/` (the committed check is in `evidence/`).

## Evaluation (`eval/`)

* `scenarios.py`: the ten benchmark scenarios (the wildfire scout among them), the wildfire valley and the missing hiker search (`hiker_search`), twelve in all (`halo list`), plus `from_mission` for compiled missions.
* `runner.py`: runs a grid of scenarios × configurations × seeds in parallel processes, resumable, one JSON line per run.
* `metrics.py`, `stats.py` and `report.py`: per-run metrics, bootstrap confidence intervals, paired comparisons on the same seeds, and the Markdown report with charts.

The headline formation metric is the *integrity error*. The ideal formation is the directive's shape and spacing for the drones still flying, at the path heading, centred on the drones. The error is the RMS distance from each drone to its ideal position, with drones and positions matched by the Hungarian method. This scores the shape the swarm holds, wherever it is, and it is fair to a controller that reassigns slots after a loss. A run counts as "in formation" while this error is below 1 m.

## Dashboard (`dashboard/`)

A standard-library HTTP server streams frames over server-sent events to a three.js page. With a key, supervisor calls run on a worker thread, so the flight never waits for the API. When replaying recorded calls without a key, they are answered in step with the simulation, exactly as in the benchmark. The page lets the operator start flights with any swarm size, inject gusts, motor failures and radio blackouts, order formations, compile missions and browse the benchmark results. For the wildfire valley the server also compiles a patrol from plain words (`POST /api/valley/compile`, answered from `results/valley_compile_cache.jsonl` without a key) and takes squad moves and aircraft holds as injects. In scouting scenarios it draws the hotspots and lights each one up when a drone's camera has found it.

## Known simplifications

Beyond what the model leaves out by design (rotor and attitude dynamics, and terrain and smoke outside the wildfire valley), a review of the code found these. They apply equally to every configuration, so comparisons between controllers and supervisors are unaffected, but they make some conditions milder than their settings suggest.

* **Vertical turbulence is much weaker than set.** `wind.py` scales the vertical part of the shared turbulence by 0.3 on every step rather than once, so vertical gusts are close to zero. Horizontal turbulence, local turbulence and gust fronts behave as described.
* **Vertical GPS bias** starts at 0.6 of the horizontal bias but drifts to the full size within about 20 s (`sensing.py`).
* **Obstacle and ground strikes** are checked at the end of each 0.02 s step, so a fast grazing pass of a thin trunk could be missed. Drone-to-drone contact is checked within the step.
* **Drones that retire** at the battery reserve leave the flight where they are (a real drone would land), and other drones are not checked against them.
* **Radio latency** is rounded to whole steps, so the default 50-70 ms becomes 40-60 ms.
