# Halo Systems

**Autonomous drone swarms that find wildfires early and keep flying safely while the fire changes the weather.**

A language model never flies the drones. They fly themselves at 50 Hz behind a safety filter. NVIDIA Nemotron, served by Nebius Token Factory, supervises them every 5 seconds, turns an operator's plain-language request into a mission plan, and works as a test engineer that hunts for the conditions that make the swarm fail. Every decision it makes passes a safety check, and every result below is measured against hand-written rules on seeds held out from tuning.

![Nemotron orders a column as the swarm enters a forest trail; a replay of a recorded held-out run in the live dashboard](docs/demo.gif)

## Why a swarm, and why now

* **Extreme wildfires are rising.** A UN report projects a global increase in extreme fires of up to 14% by 2030 and 50% by the end of the century, and calls on governments to shift their spending to prevention and preparedness ([WMO on the UNEP report, 2022](https://wmo.int/media/news/number-of-wildfires-forecast-rise-50-2100)).
* **Finding a fire early decides what it costs.** US fire services suppress 98% of fires during initial attack. The 1 to 2% that escape become the large fires that account for 30% or more of fire spending ([US Forest Service testimony, 2017](https://www.fs.usda.gov/sites/default/files/fs_media/fs_document/usda-testimony-senr-fire-christiansen-08-03-17.pdf)).
* **A large fire makes its own weather.** Its heat can build a thunderstorm whose stronger winds fan the fire ([NOAA, 2021](https://www.nesdis.noaa.gov/wildfires-spawn-severe-weather)). A scouting swarm has to keep its formation and its safety margins in gusts, on a degraded radio link and on short batteries.

One drone sweeps one strip of ground; a line of sixteen sweeps sixteen. In the wildfire scout scenario, a 300 × 200 m block of dry scrub hides six smouldering hotspots. Sixteen drones sweep it in one lane where one drone needs thirteen, and the time each swarm size takes to find every hotspot is measured on the held-out seeds.

The first customers are forest and fire services that already fly single drones and cannot run a fleet without evidence that it stays safe. Mountain search and rescue is the second use: the same sweep, looking for a person's heat signature instead of a hotspot. Halo Systems is that evidence and the software that produces it: a supervised swarm, and a test range in which a language model looks for the crash before it happens in the air.

## Results at a glance

Every number comes from held-out seeds 1000-1019, each flown once after tuning on seeds 0-9 was finished, and can be regenerated from the rows committed in [results/](results/README.md).

| Question | Answer | Evidence |
|---|---|---|
| Does the supervised swarm crash? | No crashes and no near misses in 180 held-out flights with the rules or with Nemotron supervising. The original controller lost 300 drones in the same flights | [REPORT.md](results/REPORT.md) |
| Does the safety filter matter? | Re-flying all 1,000 stress tests without it raised the failing tests from 89 to 289 and the drones lost from 391 to 2,019 | [stress/no_safety_filter](results/stress/no_safety_filter) |
| Is Nemotron a better test engineer than classical search? | It found 39 distinct failing conditions in 200 flights, 2.6 times the best baseline (corner search, 15), but no more kinds of failure | [STRESS.md](results/STRESS.md) |
| Can it turn plain language into a mission plan? | Nemotron 3 Super, and Nano with reasoning on, got all 20 held-out requests fully right, and Nano with reasoning off 18. A regex parser got 9. All three harmful requests were refused, against one for the parser | [mission_eval_*.json](results/) |
| Is it a better supervisor than tuned rules? | Slightly. Nemotron 3 Nano did better than the rules in 4 of 9 scenarios and worse in none (95% paired interval excludes zero, difference over 1%). The gains are small and one comes partly from breaking the plan, see [where the AI did worse](#where-the-ai-did-worse-or-did-not-help) | [REPORT.md](results/REPORT.md) |
| What does it cost? | Nano made 3,400 supervisor calls on the held-out seeds with no fallbacks, at about 1.2 s and an estimated $0.004 per flight. The whole project used about 13,100 requests, an estimated $8.23 | [NVIDIA_NEBIUS.md](docs/NVIDIA_NEBIUS.md#spend-guard) |
| Can a judge check it without a key? | Yes. Every Nemotron supervisor call was recorded, and 24 held-out Nemotron flights replayed from the recording with no key matched the original rows on every metric | [replay_check](results/replay_check/runs.jsonl) |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/stress-search-dark.png">
  <img alt="Bar charts of the stress-test search. Distinct failing conditions found in 200 flights: random 2, cross-entropy 10, corners 15, Nemotron Nano with reasoning 15, Nemotron Nano 39. Mean stress level of the failures: 0.59, 0.53, 0.62, 0.80, 0.85." src="docs/figures/stress-search-light.png">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/supervisor-vs-rules-dark.png">
  <img alt="Paired differences against the rule supervisor with 95% confidence intervals, for Nemotron 3 Nano and Super, in time in formation, completion time and energy per drone-km, in the scenarios where a supervisor matters." src="docs/figures/supervisor-vs-rules-light.png">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/safety-dark.png">
  <img alt="Left: drones lost in 180 held-out flights, original controller 300, improved controller flying the plan 2, with the rules 0, with Nano 0, with Super 0. Right: 1,000 stress tests re-flown without the safety filter, failing tests 89 to 289 and drones lost 391 to 2,019." src="docs/figures/safety-light.png">
</picture>

The figures are drawn from the committed results by [tools/make_figures.py](tools/make_figures.py), which also prints every plotted number.

## Try it in two minutes, no key needed

```bash
git clone <repo-url> halo-systems && cd halo-systems
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e '.[report]'   # numpy, plus matplotlib for charts
halo list                           # the ten scenarios
halo run forest_trail --config improved+rule --seed 1000   # one flight, about 30 s
halo serve --open                   # live 3D dashboard on http://127.0.0.1:8765
python3 -m unittest discover -s tests   # no network needed
```

Run the commands from the repository folder: the dashboard reads `results/` and `evidence/` from there. The tests need no key and no network, and CI runs them on Python 3.10 to 3.13 ([tests.yml](.github/workflows/tests.yml)).

To watch Nemotron supervise without a key, replay its recorded held-out flights in the dashboard:

```bash
halo serve --open --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B --cache results/nemotron_cache.jsonl
```

Pick a scenario, a seed from 1000 to 1019 and the Nemotron supervisor. Every call is answered from the recording, so the flight is the one in the results. On a seed or scenario that was not recorded, each call misses and the rules take that step.

To use Nemotron live, put your Nebius Token Factory key in the environment (never in a file you commit):

```bash
export NEBIUS_API_KEY=...               # from tokenfactory.nebius.com
halo doctor --flight                # end-to-end check; writes evidence/latest/
export HALO_SUPERVISOR_MODEL=<model id printed by doctor>
halo serve                          # the dashboard now offers the Nemotron supervisor
```

A spend guard refuses any call that could take the estimated total past `HALO_BUDGET_USD` (18 by default).

## How it works

![Three loops: a mission compiler before flight, a supervisor every 5 s, and a 50 Hz controller with a safety filter. No language model in the fast loop.](docs/architecture.png)

Three loops run at three speeds, and the language model sits only in the two slow ones:

| Loop | Rate | Who decides | What checks it |
|---|---|---|---|
| Mission | Once, before flight | Nemotron mission compiler, or a regex parser | A keyword guard and the model refuse non-civilian requests; every field is sanitised, clamped to the flight envelope and filled with a default if missing |
| Supervision | Every 5 s | Nemotron or the rule supervisor, reading the same status report | A safety validator clamps every directive to the flight envelope, enforces a spacing floor and rate-limits changes |
| Control | 50 Hz, per drone | Slot tracking, consensus and a paced leader | A control-barrier-function safety filter keeps clearance from drones, obstacles and the ground |

* **Physics.** Point-mass quadcopters with the airframe of an earlier private simulator (1.2 kg, thrust-to-weight 2.5, drag area 0.035 m²) with a first-order motor lag and a forward-flight power model (momentum theory plus parasitic drag) that gives about 17 minutes of hover. Wind is a steady mean, correlated turbulence and gust fronts that sweep across the formation. GPS has white noise and a drifting bias. The radio has latency, jitter, packet loss and blackouts. Motors fail and batteries run down. Obstacles are trees and buildings. In the wildfire scout, each drone carries a downward thermal camera and hotspots count as found once a footprint has held them for half a second.
* **Controllers.** The *original* controller is a faithful port of that simulator's passive formation law, the baseline the project started from. The *improved* controller adds optimal slot assignment with synchronised straight-line transitions, a leader that paces itself to the formation and to bends ahead, consensus on the relative shape, and the safety filter. Near obstacles, the leader holds a speed at which a drone can still stop for an obstacle it has only just sensed.
* **Supervisors.** The rule supervisor and Nemotron read the same status report and return the same JSON directive: formation, spacing, speed, altitude and safety margin. While a call is out, the drones keep flying on the current directive. A failed call (network, deadline, bad JSON) falls back to the rules for that step, and every fallback is counted.

## How it uses NVIDIA Nemotron and Nebius Token Factory

| Role | Model | What it is compared with |
|---|---|---|
| Supervisor, every 5 s | Nemotron 3 Nano 30B-A3B, reasoning off (Super 120B-A12B as a declared comparison) | A tuned rule supervisor and the fixed plan |
| Mission compiler | Nano with reasoning off and on, and Super | A regex and keyword parser |
| Test engineer | Nano with reasoning off and on | Random search, corner search and the cross-entropy method |

* All calls go to Token Factory's OpenAI-compatible chat completions API through a standard-library client with deadlines, retries with backoff, a rate limit, JSON mode and defensive parsing ([ai/client.py](src/halo_systems/ai/client.py)).
* Reasoning is switched off for the supervisor (`chat_template_kwargs: {"enable_thinking": false}`), because a directive that arrives seconds late describes a swarm that has already flown on. The mission compiler gains from reasoning: Nano went from 18 to 20 of 20 requests fully right, at a median of 12 s instead of 5 s.
* In benchmarks, a model's answer takes effect only after its measured latency has passed in simulated time, so a slow answer costs what it would in flight.
* Every supervisor call is recorded to a cache keyed on the model, messages, temperature and options, so anyone can replay a Nemotron flight exactly with no key and no cost.
* A ledger of every call's tokens and estimated cost, with a cap checked before each request, kept the whole project at an estimated $8.23.

The details, including what the tuning rounds taught us about reasoning and deadlines, are in [docs/NVIDIA_NEBIUS.md](docs/NVIDIA_NEBIUS.md).

## Results in detail

Held-out seeds 1000-1019, each flight run once. The simulator, controllers and rules were frozen before any held-out flight, and the Nemotron setup was chosen on tuning seeds and committed before any Nemotron flight on these seeds ([docs/BENCHMARKS.md](docs/BENCHMARKS.md)). [REPORT.md](results/REPORT.md) gives 95% confidence intervals and seed-by-seed comparisons for every number, and [STRESS.md](results/STRESS.md) the stress searches.

### Supervision

In five scenarios (open transit, formation changes, gusty day, motor failures, field survey) no supervisor made a difference above the report's 1% threshold. In three, the supervisor decides whether the mission completes. On degraded radio, the rules slowed the swarm down and cost time and energy, while Nemotron left the plan alone:

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

Nemotron 3 Nano against the rules, seed by seed: the mean difference, its 95% paired bootstrap interval, and how many of the 20 seeds were better, tied or worse:

| Scenario | Time in formation | Completion time | Energy per drone-km |
|---|---|---|---|
| Forest trail | +1.62 pp [+1.01, +2.60], 14/6/0 | −0.01 s [−0.03, +0.01], 0/20/0 | −0.36% [−0.49, −0.21], 0/20/0 |
| Urban canyon | −1.20 pp [−6.14, +3.82], 8/1/11 | −0.01 s [−0.04, +0.01], 0/20/0 | −1.77% [−3.05, −0.44], 13/3/4 |
| Degraded radio | +0.44 pp [+0.05, +0.93], 3/17/0 | −3.85 s [−4.00, −3.70], 20/0/0 | −5.85% [−6.20, −5.48], 20/0/0 |
| Low battery | +0.58 pp [+0.17, +1.00], 8/10/2 | −10.73 s [−16.80, −4.17], 15/0/5 | −5.39% [−7.33, −3.27], 16/1/3 |

Higher time in formation and lower time and energy are better. A seed counts as tied when the difference is within 1% of the rules' mean, which is why the forest trail's energy saving, real but small, shows as 20 ties. The figure above draws the same intervals for every scenario where a supervisor matters, Super included. Super was worse than the rules on the low-battery leg (+7.39 s [+3.38, +11.47] and +2.48% energy [+0.81, +4.23]) and held formation less well in the urban canyon (−4.47 pp [−8.06, −0.82]).

Nano never changed the plan in six of the nine scenarios. It made 3,400 calls with no fallbacks, at about 1.2 s per call and an estimated $0.004 per flight.

### Mission compiler (20 held-out requests)

| Parser | Fields right | Requests fully right | Harmful requests refused (guard or model) | Median call |
|---|---|---|---|---|
| Regex and keyword baseline | 83.9% | 9 of 20 | 1 of 3 | n/a |
| Nemotron 3 Nano, reasoning off | 97.6% | 18 of 20 | 3 of 3 | 5.0 s |
| Nemotron 3 Nano, reasoning on | 100% | 20 of 20 | 3 of 3 | 11.8 s |
| Nemotron 3 Super | 100% | 20 of 20 | 3 of 3 | 1.3 s |

A keyword guard caught one of the harmful requests before any model saw it, and Nemotron refused the other two ("smash into its windscreen", "diving at" a dog).

### Stress testing (5 searches of 40 flights per searcher, against the improved controller with rules)

| Searcher | Failures | Distinct failing conditions | Kinds of failure | Mean stress level of failures | Mildest failure |
|---|---|---|---|---|---|
| Random | 2 | 2 | 1 | 0.59 | 0.55 |
| Cross-entropy method | 10 | 10 | 8 | 0.53 | 0.50 |
| Corners of the parameter box | 15 | 15 | 13 | 0.62 | 0.50 |
| Nemotron 3 Nano | 46 | 39 | 13 | 0.85 | 0.48 |
| Nemotron 3 Nano, reasoning on | 16 | 15 | 11 | 0.80 | 0.46 |

Nemotron found 2.6 times as many distinct failing conditions as the best baseline, but no more kinds of failure than corner search, and mostly near the hard end of every range: the stress level runs from 0 (every condition at its easy end) to 1 (every condition at its hard end). Its mildest failures were slightly milder than any baseline's. With reasoning on, 20 of the 200 flights were random tests, filling in when the model proposed too few.

### Ablations and sensitivity

Removing the leader's pacing cut time in formation during formation changes from 75% to 48%, and with motor failures from 93% to 85%. Removing slot reassignment cut it during formation changes to 54%. Removing the blend between formations raised it slightly, to 78%, but cost 7% more energy there and 5.5 s more on the forest trail. Removing consensus or the state filter made no measurable difference, and removing feedforward saved 1% of energy during formation changes. Removing the safety filter made no difference beyond noise in the nine scenarios, where the supervisor keeps the swarm out of trouble, but across the 1,000 stress tests it tripled the failures. With GPS error tripled, the improved controller with rules still lost no drones in the forest, where the original lost 9.1 per flight, but it held formation for less of the flight than the original did (4% against 13%). Neither kept the formation within 1 m for most of the flight.

### Where the AI did worse or did not help

* On the low-battery leg, Nano's directive was above the planned speed for 77% of the flight, against 55% for the rules. In 90 of the 150 updates where the status report said the planned speed was enough for every drone, Nano kept a higher speed, which its instructions forbid. Super did that once in 243. Part of Nano's time and energy gain on that leg comes from breaking the plan.
* Nano took longer than the rules to settle after a change of formation: 6.3 s against 4.8 s in the forest and 4.9 s against 2.6 s in the urban canyon.
* In gusts the rules widen the spacing and Nano does not, so its closest pairs were nearer (1.93 m against 2.08 m on average), with no near misses for either.
* Nemotron 3 Super, the larger model, held formation less well than the rules in the urban canyon, and on the low-battery leg it was slower, used more energy and retired more drones (1.55 per flight against 0.65).
* Nano with reasoning on completed 3 of 5 forest flights in the last tuning round, where the rules completed 5, and took several seconds per call. It was not run on the held-out seeds.
* Nano with reasoning off got 2 of 20 mission requests wrong: a convoy "nose to tail" became line abreast, and "2 km by 800 m" became 800 m by 2 m.
* In six of the nine scenarios, Nano never changed the plan, so its flights there were the same as flying with no supervisor.

## Reproducing the results

```bash
halo bench --scenarios open_transit,formation_changes,forest_trail,urban_canyon,gusty_day,comms_degraded,motor_failures,low_battery,field_survey \
  --configs original+fixed,improved+fixed,improved+rule --seeds heldout --out results/runs.jsonl
halo report results/runs.jsonl --out results
halo mission-eval --sets heldout --out results/mission_eval_baseline.json   # regex baseline; add --model <id> for Nemotron
```

Seeds 0-9 were used for tuning; every headline number uses held-out seeds 1000-1019. The supervisor flights with Nemotron were recorded to a cache and replay exactly without a key (`--cache results/nemotron_cache.jsonl --cache-mode replay`); the mission-compiler and stress-search calls are kept in call logs instead. [docs/REPRODUCE.md](docs/REPRODUCE.md) lists every command behind `results/`.

## Where things are

| Path | What it holds |
|---|---|
| [src/halo_systems/sim/](src/halo_systems/sim) | Physics, wind, sensing, radio, obstacles, the ground sensor and the simulation engine |
| [src/halo_systems/control/](src/halo_systems/control) | Formations, slot assignment, the original and improved controllers, the safety filter |
| [src/halo_systems/supervisor/](src/halo_systems/supervisor) | The rule supervisor and the Nemotron supervisor |
| [src/halo_systems/safety/](src/halo_systems/safety) | The validator every directive passes |
| [src/halo_systems/ai/](src/halo_systems/ai) | Token Factory client, spend guard, call cache, prompts, mission compiler, test engineer, end-to-end check |
| [src/halo_systems/eval/](src/halo_systems/eval) | Scenarios, metrics, statistics, benchmark runner and report |
| [src/halo_systems/dashboard/](src/halo_systems/dashboard) | The live 3D dashboard (`halo serve`) |
| [results/](results/README.md) | Every committed result, with the rows behind it |
| [evidence/](evidence/README.md) | The live end-to-end check against Token Factory |
| [docs/](docs/README.md) | Architecture, the benchmark protocol, Nemotron and Nebius notes, reproduction commands and a code tour, in reading order |
| [tests/](tests) | Unit and integration tests, offline (a fake transport stands in for the API) |

## Limits and responsible use

* **This is a simulation.** The airframe, power and sensor models are textbook approximations that have not been calibrated against hardware, and obstacles are simple shapes. The results compare control and supervision methods under identical simulated conditions; they are not flight-test results, and the wildfire scout's camera and hotspots are idealised.
* **Civilian use only.** The mission compiler refuses requests to harm, chase or intimidate people, animals or vehicles, to drop objects on people or to jam radios, and nothing in the code controls a payload.
* **The model is advisory.** It never commands the motors, every directive is validated, and the safety filter acts whatever the directive. The stress search shows the filter is not a guarantee: it found conditions in which the swarm fails with the filter on.
* **The name.** The project was called SwarmLab until 13 October 2026. Some recorded evidence and older commits use that name; the commands are now `halo`, and the old `SWARMLAB_` environment variables still work.
