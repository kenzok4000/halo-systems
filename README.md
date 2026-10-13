# SwarmLab

**Supervised drone-swarm formation flight: a 50 Hz safety-filtered controller, with NVIDIA Nemotron on Nebius Token Factory as the slow-loop supervisor, mission compiler and test engineer.**

SwarmLab is a reproducible simulation testbed for civilian drone swarms: search and rescue under a forest canopy, field and solar-farm surveys, facade inspection in a street canyon, delivery convoys and light shows. A language model never flies the drones. The drones fly themselves at 50 Hz, and Nemotron works where a model's judgement helps and its latency doesn't hurt:

| Role | What Nemotron does | What it is compared with |
|---|---|---|
| Supervisor (every 5 s) | Reads a short status report and sets the formation, spacing, speed, altitude and safety margin | A tuned rule-based supervisor and a fixed plan |
| Mission compiler | Turns an operator's plain-language request into a checked mission plan, and refuses non-civilian requests | A regex and keyword parser |
| Test engineer | Proposes stress tests to find conditions that make the swarm fail | Random search, corner search and the cross-entropy method |

Every Nemotron output passes a safety validator before it can affect a flight. While a call is out, the drones keep flying on the current directive. Any failure (network, a call past its deadline, bad JSON) falls back to the rules for that step.

## Results

Held-out seeds 1000-1019, run once after everything was frozen. Every number below is copied from [results/REPORT.md](results/REPORT.md) and [results/STRESS.md](results/STRESS.md), which give 95% confidence intervals and seed-by-seed comparisons. The Nemotron setup was chosen on tuning seeds and committed before these runs ([docs/BENCHMARKS.md](docs/BENCHMARKS.md)).

**Safety.** Over the nine scenarios (180 flights per configuration), the original controller lost 300 drones and had 151 near misses. The improved controller, supervised by the rules or by Nemotron, lost none and had no near misses.

**Supervision.** In five scenarios (open transit, formation changes, gusty day, motor failures, field survey) the supervisors made no measurable difference. In the other four, the supervisor decides whether the mission works:

| Scenario | Metric | Fixed plan | Rules | Nemotron 3 Nano | Nemotron 3 Super |
|---|---|---|---|---|---|
| Forest trail | Missions completed | 5% | 100% | 100% | 100% |
| | Time in formation | 22% | 85% | 86% | 86% |
| Urban canyon | Missions completed | 55% | 100% | 100% | 100% |
| | Time in formation | 36% | 90% | 89% | 86% |
| | Energy (Wh per drone-km) | 13.48 | 7.68 | 7.55 | 7.49 |
| Degraded radio | Completion time (s) | 66 | 70 | 66 | 66 |
| | Energy (Wh per drone-km) | 6.73 | 7.15 | 6.73 | 6.73 |
| Low battery | Missions completed | 45% | 100% | 100% | 100% |
| | Completion time (s) | 242 | 138 | 128 | 146 |
| | Energy (Wh per drone-km) | 6.71 | 4.77 | 4.52 | 4.89 |
| | Drones retired at the battery reserve | 16.00 | 0.65 | 0.50 | 1.55 |

"Fixed plan" is the improved controller flying the mission plan with no supervisor. Nemotron 3 Nano, the entry chosen before the held-out runs, did better than the rules beyond noise in four scenarios and worse in none. It held formation better in the forest, used less energy in the urban canyon, and did not slow down needlessly when the radio degraded, as the rules did. On the low-battery leg it finished faster on less energy. It flew faster than the planned speed for 77% of that flight, against 55% for the rules, which its instructions allow only when the charge cannot last at the planned speed. It made 3,400 calls with no fallbacks, at 1.17 s mean latency and an estimated $0.004 per flight.

**Mission compiler** (20 held-out requests):

| Parser | Fields right | Requests fully right | Harmful requests refused | Median call |
|---|---|---|---|---|
| Regex and keyword baseline | 83.9% | 9 of 20 | 1 of 3 | n/a |
| Nemotron 3 Nano, reasoning off | 97.6% | 18 of 20 | 3 of 3 | 5.0 s |
| Nemotron 3 Nano, reasoning on | 100% | 20 of 20 | 3 of 3 | 11.8 s |
| Nemotron 3 Super | 100% | 20 of 20 | 3 of 3 | 1.3 s |

A keyword guard caught one of the harmful requests before any model saw it, and Nemotron refused the other two ("smash into its windscreen", "diving at" a dog).

**Stress testing** (5 searches of 40 flights per searcher, against the improved controller with rules):

| Searcher | Failures | Distinct failing conditions | Kinds of failure |
|---|---|---|---|
| Random | 2 | 2 | 1 |
| Cross-entropy method | 10 | 10 | 8 |
| Corners of the parameter box | 15 | 15 | 13 |
| Nemotron 3 Nano | 46 | 39 | 13 |
| Nemotron 3 Nano, reasoning on | 16 | 16 | 11 |

Nemotron found 2.6 times as many distinct failing conditions as the best baseline, but no more kinds of failure, and mostly by setting every condition near its harshest value.

**Ablations.** Removing the leader's pacing cut time in formation during formation changes from 75% to 48%, and removing slot reassignment cut it to 54%. Removing the blend between formations raised it slightly, to 78%, but cost 7% more energy there and 5 s more on the forest trail. Removing the safety filter changed nothing in the nine scenarios, where the supervisor keeps the swarm out of trouble. Re-flying all 1,000 stress tests without it raised the failures from 89 to 289 and the drones lost from 391 to 2,019. With GPS error tripled, the improved controller still lost no drones in the forest, where the original lost 9.1 per flight. Neither controller then kept the formation within 1 m for most of the flight.

**Where the AI did worse or did not help**

* Nemotron 3 Super, the larger model, held formation less well than the rules in the urban canyon, and on the low-battery leg it was slower, used more energy and retired more drones (1.55 per flight against 0.65).
* Nano with reasoning on completed 3 of 5 forest flights in the last tuning round, where the rules completed 5, and took several seconds per call. It was not run on the held-out seeds.
* Nano with reasoning off got 2 of 20 mission requests wrong: a convoy "nose to tail" became line abreast, and "2 km by 800 m" became 800 m by 2 m.
* In five of the nine scenarios, Nemotron made no difference.

## Quick start

```bash
git clone <repo-url> && cd swarmlab
python3 -m pip install -e .            # numpy only; add [report] for charts
swarmlab list                           # the nine scenarios
swarmlab run forest_trail --config improved+rule --seed 1000
swarmlab serve --open                   # live 3D dashboard on http://127.0.0.1:8765
python3 -m unittest discover -s tests   # no network needed
```

To use Nemotron, put your Nebius Token Factory key in the environment (never in a file you commit):

```bash
export NEBIUS_API_KEY=...               # from tokenfactory.nebius.com
swarmlab doctor --flight                # end-to-end check; writes evidence/
export SWARMLAB_SUPERVISOR_MODEL=<model id printed by doctor>
swarmlab serve                          # the dashboard now offers the Nemotron supervisor
```

## How it works

```
 operator text ──► Nemotron mission compiler ──► validator ──► mission plan
                                                                 │
 status report every 5 s ──► supervisor (rules | Nemotron) ──► validator ──► directive
                                                                              │
 50 Hz, per drone:  slot tracking + consensus ──► CBF safety filter ──► thrust ──► physics
```

* **Physics.** Point-mass quadcopters with the airframe of an earlier private simulator (1.2 kg, thrust-to-weight 2.5, drag area 0.035 m²), a first-order motor lag, and a forward-flight power model (Glauert momentum theory plus parasitic drag) that gives about 17 minutes of hover. Wind is a steady mean, correlated turbulence and gust fronts that sweep across the formation. GPS has white noise and a drifting bias. The radio has latency, jitter, packet loss and blackouts. Motors fail and batteries run down. Obstacles are trees and buildings.
* **Controllers.** The *original* controller is a faithful port of that simulator's passive formation law. The *improved* controller adds Hungarian slot assignment with synchronised straight-line transitions (CAPT), a leader that paces itself to the formation and to bends ahead, consensus on the relative shape, and a control-barrier-function safety filter for drones, obstacles and the ground. Near obstacles, the leader holds a speed at which a drone can still stop for an obstacle it has only just sensed.
* **Supervisors.** The rule supervisor and the Nemotron supervisor read the same status report and return the same JSON directive. A safety validator checks the schema, clamps values to the flight envelope, enforces a spacing floor derived from body size, safety margin and navigation error, and rate-limits changes.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/BENCHMARKS.md](docs/BENCHMARKS.md) and [docs/NVIDIA_NEBIUS.md](docs/NVIDIA_NEBIUS.md).

## Reproducing the results

```bash
swarmlab bench --scenarios open_transit,formation_changes,forest_trail,urban_canyon,gusty_day,comms_degraded,motor_failures,low_battery,field_survey \
  --configs original+fixed,improved+fixed,improved+rule --seeds heldout --out results/runs.jsonl
swarmlab report results/runs.jsonl --out results
swarmlab mission-eval --out results/mission_eval.json
```

Seeds 0-9 were used for tuning; every headline number uses held-out seeds 1000-1019. Runs with Nemotron are recorded to a cache and can be replayed exactly without a key (`--cache ... --cache-mode replay`). See [docs/REPRODUCE.md](docs/REPRODUCE.md).

## Limits

This is a simulation. The airframe, power and sensor models are textbook approximations that have not been calibrated against hardware, and the obstacles are simple shapes. Results show how the control and supervision methods compare under identical simulated conditions. They are not flight-test results.
