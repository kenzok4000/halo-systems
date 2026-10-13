# Benchmarks

This page explains how the numbers in `results/REPORT.md` are produced and how to read them. The numbers themselves live only in the generated report, so the two cannot drift apart.

## Scenarios

Nine scenarios, each with 16 drones (12 in the field survey). Each tests one thing a supervisor might need to handle.

| Scenario | Use case | What makes it hard |
|---|---|---|
| open_transit | Transit | Nothing much: the control case |
| formation_changes | Light show, reconfiguration | Five operator-ordered formation changes on a looped circuit |
| forest_trail | Search and rescue under canopy | A 9-13 m trail through 140 m of dense forest, under a 22 m ceiling |
| urban_canyon | Facade inspection | A 14-20 m street between building blocks, narrower than the planned grid needs, under a 22 m ceiling |
| gusty_day | Close-formation transit | 3 m spacing, strong turbulence and sudden 7-11 m/s crosswind gust fronts |
| comms_degraded | Any | 35% packet loss, 250 ms latency, a 10 s blackout for a third of the swarm |
| motor_failures | Any | Three drones lose power at random moments |
| low_battery | Delivery, long legs | A 1.2 km leg into a headwind with batteries at about 32% |
| field_survey | Crop scouting, search | A lawnmower sweep of a 280 × 210 m field in line abreast |

## Configurations

| Name | Controller | Supervisor |
|---|---|---|
| `original+fixed` | Port of the passive formation law from an earlier private simulator | None: flies the mission plan |
| `improved+fixed` | Improved controller with the safety filter | None |
| `improved+rule` | Improved controller | Tuned rule supervisor |
| `improved+nemotron:<model>` | Improved controller | Nemotron, reasoning off |
| `improved+nemotron-think:<model>` | Improved controller | Nemotron, reasoning on |
| `improved-no-<feature>+rule` | Improved controller with one technique removed | Rule supervisor (ablation) |

## Protocol

* **Seeds.** Seeds 0-9 were used for all development and tuning, including the rule thresholds, controller gains, the safety filter's navigation padding and the Nemotron prompt and model choice. Seeds 1000-1019 are held out and run once, after everything is frozen.
* **Pairing.** A seed fixes the wind, sensor noise, radio losses, obstacle layout and initial positions, and every configuration sees the same ones. Comparisons are made seed by seed.
* **Statistics.** Means come with 95% percentile-bootstrap confidence intervals (10,000 resamples, fixed generator seed). Paired comparisons report the mean difference, its bootstrap interval, and how many seeds were better, tied or worse.
* **Model latency counts.** In benchmarks the simulation pauses while a model call is made. The answer then takes effect only after the call's measured latency has passed in simulated time, so slow answers cost what they would in flight.
* **Failures are not hidden.** A run whose supervisor could not reach the model at all (no key, rejected key, spent budget) is recorded as an error and excluded. Individual failed calls fall back to the rules for that step and are counted in `ai_fallbacks`.

## Nemotron setup, chosen before the held-out runs

Chosen on 13 October 2026 from the last tuning round (seeds 5-9, `results/tuning/`), and committed before any Nemotron flight on seeds 1000-1019. Nothing in the controller, rule supervisor, validator, prompts or engine changes between that commit and the held-out runs.

* **Supervisor, main entry: Nemotron 3 Nano, reasoning off** (`improved+nemotron:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`). On the tuning seeds it flew the low-battery leg faster and on less energy than the rules, tied or slightly beat them elsewhere, held formation slightly less well in the urban canyon, and cost about a fifth as much per flight as Super.
* **Supervisor, also run: Nemotron 3 Super, reasoning off**, as a declared comparison with a larger model.
* **Not run on the held-out seeds: Nano with reasoning on.** On the tuning seeds it completed 3 of 5 forest flights where the rules completed 5, and its calls took several seconds each. Its tuning results are reported as they are.
* **Mission compiler:** Nano with reasoning off, Nano with reasoning on and Super are all scored on the held-out prompts against the regex baseline.

## Metrics

| Metric | Meaning |
|---|---|
| Time in formation (%) | Share of the flight with the integrity error below 1 m (see ARCHITECTURE.md) |
| Formation error, mean (m) | Mean integrity error over the flight |
| Crashes per run | Drones destroyed: 2 per drone-to-drone collision, 1 per obstacle or ground strike |
| Near misses per run | Times two flying drones came within 0.8 m centre to centre |
| Missions completed (%) | The formation reached the end of its route before the time limit; n/a for looped circuits |
| Completion time (s) | For completed runs only |
| Energy (Wh per drone-km) | Battery energy per drone per kilometre flown |
| Drones retired | Drones that left the formation at the battery reserve |
| Time faster than planned (%) | Share of the flight flown faster than the operator's planned speed. Flying faster than planned shortens completion time, so this is reported beside it |
| Model calls, fallbacks, latency, tokens, cost | From the call records, for Nemotron runs |

Time in formation counts the drones still flying. A controller that loses drones in a crash can still score well on it, which is why crashes are always reported beside it.

## Known limits of the benchmark

* Some scenarios separate the configurations clearly and some do not. The report says which.
* The rule supervisor was tuned on the same scenarios it is tested on, with different seeds. The same holds for the Nemotron prompt. Neither has seen the held-out seeds.
* The physics and sensor models are textbook approximations, not calibrated against hardware.
