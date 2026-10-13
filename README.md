# Halo Systems

**Supervised drone-swarm formation flight: a 50 Hz safety-filtered controller, with NVIDIA Nemotron on Nebius Token Factory as the slow-loop supervisor, mission compiler and test engineer.**

Halo Systems is a reproducible simulation testbed for civilian drone swarms: search and rescue under a forest canopy, field and solar-farm surveys, facade inspection in a street canyon, delivery convoys and light shows. A language model never flies the drones. The drones fly themselves at 50 Hz, and Nemotron works where a model's judgement helps and its latency doesn't hurt:

| Role | What Nemotron does | What it is compared with |
|---|---|---|
| Supervisor (every 5 s) | Reads a short status report and sets the formation, spacing, speed, altitude and safety margin | A tuned rule-based supervisor and a fixed plan |
| Mission compiler | Turns an operator's plain-language request into a checked mission plan, and refuses non-civilian requests | A regex and keyword parser |
| Test engineer | Proposes stress tests to find conditions that make the swarm fail | Random search, corner search and the cross-entropy method |

Every Nemotron output passes a safety validator before it can affect a flight. While a call is out, the drones keep flying on the current directive. Any failure (network, a call past its deadline, bad JSON) falls back to the rules for that step.

## Results

Held-out seeds 1000-1019, each flight run once. The simulator, controllers and rules were frozen before any held-out flight, and the Nemotron setup was chosen on tuning seeds and committed before any Nemotron flight on these seeds ([docs/BENCHMARKS.md](docs/BENCHMARKS.md)). The numbers below come from the reports and logs in [results/](results/README.md): [REPORT.md](results/REPORT.md) gives 95% confidence intervals and seed-by-seed comparisons, and [STRESS.md](results/STRESS.md) the stress searches.

**Safety.** Over the nine scenarios (180 flights per configuration), the original controller lost 300 drones in crashes and had 151 near misses. The improved controller with a supervisor, the rules or Nemotron, had no crashes and no near misses. Flying the plan with no supervisor, it had 2 crashes and 1 near miss. (Drones that drop out with a scripted motor failure are not counted as crashes.)

**Supervision.** In five scenarios (open transit, formation changes, gusty day, motor failures, field survey) no supervisor made a difference above the report's threshold of 1%. In three, the supervisor decides whether the mission completes. On degraded radio, the rules slowed the swarm down and cost time and energy, while Nemotron left the plan alone:

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
| | Completion time (s) | 242* | 138 | 128 | 146 |
| | Energy (Wh per drone-km) | 6.71 | 4.77 | 4.52 | 4.89 |
| | Drones retired at the battery reserve | 16.00 | 0.65 | 0.50 | 1.55 |

"Fixed plan" is the improved controller flying the mission plan with no supervisor. \*Over the 9 of 20 flights that completed.

Nemotron 3 Nano is the entry chosen before the held-out runs. On the five headline metrics (missions completed, time in formation, crashes, completion time and energy), it did better than the rules in four scenarios and worse in none, where better or worse means the 95% paired interval excludes zero and the difference is over 1%. The gains are small, and two of them come with a caveat:

* Forest trail: in formation 86.3% of the time, against 84.7% for the rules.
* Urban canyon: 1.8% less energy.
* Degraded radio: 4 s faster on 6% less energy, because the rules slowed down and Nano did not. Nano kept the plan, exactly as flying with no supervisor does.
* Low battery: 11 s faster on 5% less energy, partly because Nano flew faster than its instructions allowed (see below).

Nano never changed the plan in six of the nine scenarios. It made 3,400 calls with no fallbacks, at about 1.2 s per call and an estimated $0.004 per flight.

**Mission compiler** (20 held-out requests):

| Parser | Fields right | Requests fully right | Harmful requests refused (guard or model) | Median call |
|---|---|---|---|---|
| Regex and keyword baseline | 83.9% | 9 of 20 | 1 of 3 | n/a |
| Nemotron 3 Nano, reasoning off | 97.6% | 18 of 20 | 3 of 3 | 5.0 s |
| Nemotron 3 Nano, reasoning on | 100% | 20 of 20 | 3 of 3 | 11.8 s |
| Nemotron 3 Super | 100% | 20 of 20 | 3 of 3 | 1.3 s |

A keyword guard caught one of the harmful requests before any model saw it, and Nemotron refused the other two ("smash into its windscreen", "diving at" a dog).

**Stress testing** (5 searches of 40 flights per searcher, against the improved controller with rules):

| Searcher | Failures | Distinct failing conditions | Kinds of failure | Mean stress level of failures |
|---|---|---|---|---|
| Random | 2 | 2 | 1 | 0.59 |
| Cross-entropy method | 10 | 10 | 8 | 0.53 |
| Corners of the parameter box | 15 | 15 | 13 | 0.62 |
| Nemotron 3 Nano | 46 | 39 | 13 | 0.85 |
| Nemotron 3 Nano, reasoning on | 16 | 15 | 11 | 0.80 |

Nemotron found 2.6 times as many distinct failing conditions as the best baseline, but no more kinds of failure than corner search, and mostly near the hard end of every range: the stress level runs from 0 (every condition at its easy end) to 1 (every condition at its hard end). Its mildest failure, at 0.48, was slightly milder than any baseline's (0.50 to 0.55). With reasoning on, 20 of the 200 flights were random tests, filling in when the model proposed too few.

**Ablations.** Removing the leader's pacing cut time in formation during formation changes from 75% to 48%, and with motor failures from 93% to 85%. Removing slot reassignment cut it during formation changes to 54%. Removing the blend between formations raised it slightly, to 78%, but cost 7% more energy there and 5.5 s more on the forest trail. Removing consensus or the state filter made no measurable difference, and removing feedforward saved 1% of energy during formation changes. Removing the safety filter made no difference beyond noise in the nine scenarios, where the supervisor keeps the swarm out of trouble. Re-flying all 1,000 stress tests without it raised the failures from 89 to 289 and the drones lost from 391 to 2,019. With GPS error tripled, the improved controller with rules still lost no drones in the forest, where the original lost 9.1 per flight, but it held formation for less of the flight than the original did (4% against 13%). Neither kept the formation within 1 m for most of the flight.

**Where the AI did worse or did not help**

* On the low-battery leg, Nano's directive was above the planned speed for 77% of the flight, against 55% for the rules. In 90 of the 150 updates where the status report said the planned speed was enough for every drone, Nano kept a higher speed, which its instructions forbid. Super did that once in 243. Part of Nano's time and energy gain on that leg comes from breaking the plan.
* Nano took longer than the rules to settle after a change of formation: 6.3 s against 4.8 s in the forest and 4.9 s against 2.6 s in the urban canyon.
* In gusts the rules widen the spacing and Nano does not, so its closest pairs were nearer (1.93 m against 2.08 m on average), with no near misses for either.
* Nemotron 3 Super, the larger model, held formation less well than the rules in the urban canyon, and on the low-battery leg it was slower, used more energy and retired more drones (1.55 per flight against 0.65).
* Nano with reasoning on completed 3 of 5 forest flights in the last tuning round, where the rules completed 5, and took several seconds per call. It was not run on the held-out seeds.
* Nano with reasoning off got 2 of 20 mission requests wrong: a convoy "nose to tail" became line abreast, and "2 km by 800 m" became 800 m by 2 m.
* In six of the nine scenarios, Nano never changed the plan, so its flights there were the same as flying with no supervisor.

## Quick start

```bash
git clone <repo-url> halo-systems && cd halo-systems
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e '.[report]'   # numpy, plus matplotlib for charts
halo list                           # the nine scenarios
halo run forest_trail --config improved+rule --seed 1000   # one flight, about 30 s
halo serve --open                   # live 3D dashboard on http://127.0.0.1:8765
python3 -m unittest discover -s tests   # no network needed
```

Run the commands from the repository folder: the dashboard reads `results/` and `evidence/` from there.

To use Nemotron, put your Nebius Token Factory key in the environment (never in a file you commit):

```bash
export NEBIUS_API_KEY=...               # from tokenfactory.nebius.com
halo doctor --flight                # end-to-end check; writes evidence/latest/
export HALO_SUPERVISOR_MODEL=<model id printed by doctor>
halo serve                          # the dashboard now offers the Nemotron supervisor
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
halo bench --scenarios open_transit,formation_changes,forest_trail,urban_canyon,gusty_day,comms_degraded,motor_failures,low_battery,field_survey \
  --configs original+fixed,improved+fixed,improved+rule --seeds heldout --out results/runs.jsonl
halo report results/runs.jsonl --out results
halo mission-eval --sets heldout --out results/mission_eval_baseline.json   # regex baseline; add --model <id> for Nemotron
```

Seeds 0-9 were used for tuning; every headline number uses held-out seeds 1000-1019. The supervisor flights with Nemotron were recorded to a cache and can be replayed exactly without a key (`--cache results/nemotron_cache.jsonl --cache-mode replay`); the mission-compiler and stress-search calls are kept in call logs instead. [docs/REPRODUCE.md](docs/REPRODUCE.md) lists every command behind `results/`.

## Limits

This is a simulation. The airframe, power and sensor models are textbook approximations that have not been calibrated against hardware, and the obstacles are simple shapes. Results show how the control and supervision methods compare under identical simulated conditions. They are not flight-test results.
