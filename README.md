# SwarmLab

**Supervised drone-swarm formation flight: a 50 Hz safety-filtered controller, with NVIDIA Nemotron on Nebius Token Factory as the slow-loop supervisor, mission compiler and test engineer.**

SwarmLab is a reproducible simulation testbed for civilian drone swarms: search and rescue under a forest canopy, field and solar-farm surveys, facade inspection in a street canyon, delivery convoys and light shows. A language model never flies the drones. The drones fly themselves at 50 Hz, and Nemotron works where a model's judgement helps and its latency doesn't hurt:

| Role | What Nemotron does | What it is compared with |
|---|---|---|
| Supervisor (every 5 s) | Reads a short status report and sets the formation, spacing, speed, altitude and safety margin | A tuned rule-based supervisor and a fixed plan |
| Mission compiler | Turns an operator's plain-language request into a checked mission plan, and refuses non-civilian requests | A regex and keyword parser |
| Test engineer | Proposes stress tests to find conditions that make the swarm fail | Random search and the cross-entropy method |

Every Nemotron output passes a safety validator before it can affect a flight, and any failure (network, timeout, bad JSON) falls back to the rules for that step. The swarm never waits on the API.

> Status: results in this README are filled in from `results/REPORT.md` at the end of the build. Nothing here is a projection.

## Quick start

```bash
git clone <repo-url> && cd swarmlab
python3 -m pip install -e .            # numpy only; add [report] for charts
swarmlab list                           # the nine scenarios
swarmlab run forest_trail --config improved+rule --seed 1000
swarmlab serve --open                   # live 3D dashboard on http://127.0.0.1:8765
python3 -m unittest discover -s tests   # 50 tests, no network needed
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
* **Controllers.** The *original* controller is a faithful port of that simulator's passive formation law. The *improved* controller adds Hungarian slot assignment with synchronised straight-line transitions (CAPT), a leader that paces itself to the formation and to bends ahead, consensus on the relative shape, and a control-barrier-function safety filter for drones, obstacles and the ground.
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
