# Code tour

A guide to reading the source, in the order a flight runs. The package is `halo_systems` under `src/`, the command is `halo`, and every module named here is under `src/halo_systems/`. Only numpy is needed at run time; matplotlib draws the report charts.

## Follow one flight

```bash
halo run forest_trail --config improved+rule --seed 1000
```

1. **Command line.** `cli.py` parses the command and calls `eval/runner.py: run_one`.
2. **Configuration.** `run_one` splits `improved+rule` into a controller and a supervisor (`split_config`, `make_controller`, `make_supervisor`). The same small language names every configuration in the results: `original` or `improved`, optionally `improved-no-<feature>` for an ablation, then `+fixed`, `+rule`, `+nemotron:<model id>` or `+nemotron-think:<model id>`.
3. **Scenario.** `eval/scenarios.py: make("forest_trail", 1000)` builds a `Scenario` (`sim/scenario.py`) from a random stream seeded by the seed and the scenario: the route, the mission plan (a `Directive`), the trees, the wind and gust fronts, sensor and radio settings, motor failures and battery levels. The same seed gives every configuration the same conditions.
4. **Simulation.** `sim/engine.py: Simulation.run` calls `step()` every 0.02 s. Each step runs in this order:
   1. *Disturbances*: wind turbulence and gust fronts (`sim/wind.py`), scheduled motor failures, radio blackouts.
   2. *Sensing and radio* (`sim/sensing.py`): each drone's noisy navigation estimate, and the messages it has heard from the others, delayed, lost or cut off. These make up the `Observation` (`control/base.py`), which is all a controller may see.
   3. *Supervision*, every 5 s of simulated time: `Simulation.summary` writes the status report, the supervisor returns a `Decision` (`supervisor/base.py`), and the engine applies it only after the call's latency has passed in simulated time. Whatever the source (mission, rules, Nemotron, a fallback or an operator order), the proposal passes `safety/validator.py` first.
   4. *Control*, every step: `control/improved.py: ImprovedController.command` filters the navigation estimate, moves the shared virtual leader along the route (`control/plan.py: FormationPlan`), tracks each drone's slot with feedforward and consensus, and finally runs the safety filter (`control/cbf.py`). `control/original.py` is the baseline law.
   5. *Physics* (`sim/physics.py`): the autopilot turns the acceleration command into thrust, and the point-mass model integrates it with drag, motor lag and wind.
   6. *Bookkeeping*: collisions and near misses, battery energy from the power model (`sim/body.py`), the ground sensor in scouting scenarios, the recorded series, and the end-of-mission check.
5. **Metrics.** `eval/metrics.py: compute_metrics` turns the recorded ground truth into one flat row (time in formation, crashes, completion time, energy, model calls and so on), which `halo run` prints as JSON.

## From flights to the report

* `eval/runner.py: run_grid` (`halo bench`) flies scenarios × configurations × seeds in parallel processes and appends one JSON line per flight. It resumes: rows already in the output file are skipped.
* `eval/stats.py` defines every metric's label and better direction, the bootstrap confidence intervals and the seed-by-seed paired comparison.
* `eval/report.py` (`halo report`) writes `REPORT.md`, `summary.json` and the charts from a rows file. `cli.py: cmd_stress_report` writes `STRESS.md`.
* `tools/make_figures.py` draws the README figures from the committed results and prints every plotted number.

## Where Nemotron comes in

* **Supervisor.** `supervisor/nemotron.py` builds the messages (`ai/prompts.py`), looks the request up in the record/replay cache (`ai/cache.py`), and otherwise calls Token Factory through `ai/client.py`. Any failure returns the rule supervisor's answer (`supervisor/rule.py`) for that step and is counted.
* **Client.** `ai/client.py` is the only code that talks to the network: https only, key from the environment, deadlines, retries, rate limit, JSON mode and a redacted call log. `ai/budget.py` checks the spend ledger before every request.
* **Mission compiler.** `ai/mission.py` holds the keyword guard, the prompt, the parsing and clamping of the plan, the regex baseline it is scored against, and the scoring. `eval/scenarios.py: from_mission` turns a compiled plan into a flyable scenario.
* **Test engineer.** `ai/tester.py` defines the 14-parameter stress space, builds a short flight from a set of parameters, and runs the searchers: random, corners, the cross-entropy method and Nemotron.
* **End-to-end check.** `ai/doctor.py` (`halo doctor`) lists the models, makes one real supervisor call, validates it and optionally flies a full scenario.

## The dashboard

`dashboard/server.py` (`halo serve`) is a standard-library HTTP server. It runs each live flight on its own thread, paced to the wall clock, and streams frames and log entries to the page over server-sent events. `dashboard/static/app.js` draws the scene with three.js (vendored in `static/vendor/`) and has no build step.

## Things that look odd but are deliberate

* **Prompt and report wording is frozen.** The replay cache keys each call on the exact messages, so changing a prompt, the status report's wording or a JSON field name would orphan every recorded Nemotron call in `results/nemotron_cache.jsonl`.
* **Random draws that look unused stay.** Some scenario builders draw a number they do not use (the low-battery leg's `heading`, for example). Removing a draw would shift every later one and change every seed's layout.
* **Crashes count drones, not events.** A drone-to-drone collision destroys two drones, so the report's crashes are 2 × collisions + obstacle strikes + ground strikes.
* **Three formation errors.** `formation_rms_mean_m` is the raw distance from each drone to its assigned slot. `shape_error_*` removes the formation's mean offset. `formation_error_*` and time in formation use the integrity error, which matches drones to the ideal shape by the Hungarian method; this is the headline metric (see [ARCHITECTURE.md](ARCHITECTURE.md)).
* **The regex baseline is frozen.** It was written with both prompt sets in the first commit and has not changed since, so its scores are a fair baseline. Its rough edges stay.
* **Report text such as "1 drones"** is pinned by the tests and by the committed reports.

## Tests

| File | What it covers |
|---|---|
| `tests/test_sim.py` | Physics, power model, swept collision checks, Hungarian assignment, formation geometry, scenarios (the wildfire scout included), and the engine: same seed same result, paired conditions, one model request in flight, the ground sensor |
| `tests/test_safety_ai.py` | The validator, the safety filter, the rule supervisor, the Nemotron supervisor and client against a fake transport (key handling, retries, deadlines, reasoning switch, cache), the spend guard, the stress search |
| `tests/test_mission_dashboard.py` | The mission compiler and its guard, statistics, and the dashboard server (key-less replay, refusals, request checks) |
| `tests/test_report.py` | The report generator: labels, sweeps, Nemotron usage, failed runs, the scouting table |
| `tests/test_settings.py` | The `HALO_` settings and the older `SWARMLAB_` names |
| `tests/test_wildfire.py` | The wildfire valley: terrain and cover, fire spread, smoke fixes, forecasts, the alert check, today's methods and short valley flights |
| `tests/test_cli.py` | The command line's one-line errors (unknown scenario or supervisor, no model id) and `HALO_DEBUG=1` for the traceback |
