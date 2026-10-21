# Benchmarks

This page explains how the numbers in `results/REPORT.md` are produced and how to read them. The numbers themselves live only in the generated report, so the two cannot drift apart.

## Scenarios

Ten scenarios, each with 16 drones (12 in the field survey; the wildfire scout is also flown with 1 and 4, see below). Each tests one thing a supervisor might need to handle, except the wildfire scout, which measures what a swarm is for. The wildfire valley and the missing-hiker search, an eleventh and a twelfth scenario, have their own seeds, sweeps and scoring ([WILDFIRE.md](WILDFIRE.md), [hiker_search](../results/hiker_search/REPORT.md)).

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
| wildfire_scout | Wildfire scouting | A thermal sweep of a 300 × 200 m block of dry scrub for six smouldering hotspots, line abreast at 20 m; the lane width follows from the camera footprint and the number of drones |

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
* **Statistics.** Means come with 95% percentile-bootstrap confidence intervals (10,000 resamples, fixed generator seed). The resampling follows the order of the rows in the file, so `halo report` on the committed rows reproduces the committed `REPORT.md` files exactly. A fresh `bench` writes rows in the order the flights finish, so its per-configuration intervals can move slightly and a very narrow one can gain or lose its brackets; the means, the counts and the paired comparisons do not change. Paired comparisons report the mean difference, its bootstrap interval, and how many seeds were better, tied or worse.
* **Model latency counts.** In benchmarks the simulation pauses while a model call is made. The answer then takes effect only after the call's measured latency has passed in simulated time, so slow answers cost what they would in flight.
* **Failures are not hidden.** A run whose supervisor could not reach the model at all (no key, rejected key, spent budget) is recorded as an error and excluded. Individual failed calls fall back to the rules for that step and are counted in `ai_fallbacks`.

## Nemotron setup, chosen before the held-out runs

Chosen on 13 October 2026 from the last tuning round (seeds 5-9, `results/tuning/`), and committed before any Nemotron flight on seeds 1000-1019. The conventional held-out flights (no Nemotron) had already run by then; the choice below uses only the tuning seeds. Nothing in the controller, rule supervisor, validator, prompts or engine changes between that commit and the held-out runs.

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
| Time faster than planned (%) | Share of the flight during which the supervisor's directive set a speed above the operator's planned speed. Flying faster than planned shortens completion time, so this is reported beside it |
| Model calls, fallbacks, latency, tokens, cost | From the call records, for Nemotron runs |
| Targets found (%) | Scouting scenarios: share of the hotspots that some drone's camera footprint held for the dwell time (0.5 s) |
| Time to first find, time to find all (s) | Scouting scenarios: when the first and the last hotspot were found; time to find all is averaged over the runs that found every one |
| Area covered (%), time to 90% coverage (s), coverage rate (ha/min) | Scouting scenarios: cells of the survey block (5 m) whose centre has been inside a footprint |

Time in formation counts the drones still flying. A controller that loses drones in a crash can still score well on it, which is why crashes are always reported beside it.

### Swarm-size sweep (wildfire scout)

The wildfire scout is flown with 1, 4 and 16 drones on the same 20 held-out seeds (same hotspots, same wind), with the improved controller and the rule supervisor. The camera is a 45° thermal camera looking down from 20 m, so each drone sees a 16.6 m wide strip; drones fly line abreast 14 m apart (15% overlap), and the block is swept in as many lanes as the formation's width needs: one lane for 16 drones, 4 for 4, 13 for 1. The sweep answers the question the use case turns on: how much sooner does a swarm find every ignition than one drone? Rows are in `results/wildfire/runs.jsonl` and the report in `results/wildfire/REPORT.md`.

## Stress-test search

Each searcher gets 5 searches of 40 short flights against the improved controller with the rule supervisor, in a 14-parameter space of swarm size, spacing, speed, wind, turbulence, gust fronts, radio loss and latency, GPS noise and bias, motor failures, formation changes and forest density and width (`ai/tester.py`). A flight fails if any drone crashes or two drones pass within 0.8 m. `results/STRESS.md` compares the searchers on:

* failures, and distinct failing conditions, which count a repeated test once;
* kinds of failure: what failed (collision, obstacle, ground or near miss) and which stresses were present;
* stress level, from 0 (every parameter at its easy end) to 1 (every parameter at its hard end). A failure at a low stress level is the more useful find.

Corner search, which tests random corners of the parameter box, is the boundary-value baseline. It was added after the first Nemotron searches showed that the model mostly set every parameter near its limit, to check whether that alone explained its results.

A second search, on fresh held-out valleys, asks the same question of the wildfire valley's ground station (`ai/valley_tester.py`, [results/valley_stress](../results/valley_stress/REPORT.md)). Random search, corner search and Nemotron 3 Nano with reasoning each get 2 searches of 20 tests over wind, wind shift, fuel moisture, forecast error, smoulder times, patchiness, visibility, smoke range and swarm size. A test fails on a late warning, a missed fire, a false alarm, a needless evacuation or a lost drone. Test i of search s flies valley seed 9100 + 50 s + i for every searcher, and the same valleys are flown on the drawn day, so a failure counts as caused by the conditions only when that valley did not fail the same way on the drawn day.

## Checks on the numbers

* **Determinism.** Re-flying 100 held-out flights of the rule supervisor gave identical results apart from the controller's measured compute time.
* **Replay.** Replaying 24 held-out Nemotron flights from `results/nemotron_cache.jsonl`, with no key, gave identical rows to the live runs.

## Known limits of the benchmark

* Some scenarios separate the configurations clearly and some do not. The report says which.
* The rule supervisor was tuned on the same scenarios it is tested on, with different seeds. The same holds for the Nemotron prompt. Neither has seen the held-out seeds.
* The physics and sensor models are textbook approximations, not calibrated against hardware.
* In the nine scenarios the supervisor keeps the swarm out of situations where the safety filter has to act, so removing the filter changes nothing there. Its effect shows only in the stress tests.
* Time in formation uses a fixed 1 m threshold. With GPS error tripled, neither controller stays within it for most of the flight, although the improved one still loses no drones.
* Nemotron's held-out results come from one run per seed at temperature 0.2. A repeat would not give identical answers, which is why the call cache is published.
