# Results

Every number here comes from held-out seeds 1000-1019, each flight run once. The simulator, controllers and rules were frozen before any held-out flight, and the Nemotron setup was chosen on tuning seeds 0-9 and committed before any Nemotron flight on the held-out seeds ([BENCHMARKS.md](BENCHMARKS.md)). The generated reports behind each section are [REPORT.md](../results/REPORT.md) (95% confidence intervals and seed-by-seed comparisons for every number), [STRESS.md](../results/STRESS.md) and [wildfire/REPORT.md](../results/wildfire/REPORT.md). The figures are drawn from the committed rows by [tools/make_figures.py](../tools/make_figures.py), which also prints every plotted number.

## Wildfire scouting: what the swarm is for

The wildfire scout sweeps a 300 × 200 m block of dry scrub for six smouldering hotspots with downward thermal cameras (45° field of view, 20 m up, a 16.6 m strip per drone). The same 20 held-out blocks were flown with 1, 4 and 16 drones, with the improved controller and the rule supervisor; Nemotron was not flown on this scenario. Every flight found every hotspot, with no crashes and no near misses. What changes is time ([results/wildfire/REPORT.md](../results/wildfire/REPORT.md)):

| Drones | Time to find all six hotspots | First find | Time to cover 90% of the block | Coverage rate | Energy for the block |
|---|---|---|---|---|---|
| 1 | 536 s [487, 580] | 96 s | 620 s | 0.5 ha/min | 25.1 Wh |
| 4 | 180 s [163, 196] | 22.5 s | 198 s | 1.6 ha/min | 32.9 Wh |
| 16 | 41 s [38, 43] | 6.5 s | 44 s | 6.2 ha/min | 32.7 Wh |

Means over the 20 held-out blocks, with 95% bootstrap intervals. Seed by seed, sixteen drones found the last hotspot 13.3 times faster than one drone [12.0, 14.5], 8.3 minutes earlier [7.4, 9.0] on a block that one drone needs 11.6 minutes to finish, for 30% more battery energy in total. The drones fly line abreast 14 m apart, so sixteen of them sweep the block in one lane where one drone needs thirteen.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/wildfire-swarm-size-dark.png">
  <img alt="Time to find all six hotspots: one drone 536 s, four drones 180 s, sixteen drones 41 s, means over 20 held-out blocks with 95% intervals. Battery energy for the block: 25.1, 32.9 and 32.7 Wh." src="figures/wildfire-swarm-size-light.png">
</picture>

The sweep was flown in a 3 to 6 m/s breeze with turbulence. The harder weather a large fire can whip up, gust fronts, is tested in the other scenarios below, with radio loss, motor failures and short batteries. The simulator does not model the fire itself: hotspots are fixed points that do not spread, and a camera finds one once its footprint has covered it for half a second.

## Supervision

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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/supervisor-vs-rules-dark.png">
  <img alt="Paired differences against the rule supervisor with 95% confidence intervals, for Nemotron 3 Nano and Super, in time in formation, completion time and energy per drone-km, in the scenarios where a supervisor matters." src="figures/supervisor-vs-rules-light.png">
</picture>

Nano never changed the plan in six of the nine scenarios. It made 3,400 calls with no fallbacks, at about 1.2 s per call and an estimated $0.004 per flight.

## Mission compiler (20 held-out requests)

| Parser | Fields right | Requests fully right | Harmful requests refused (guard or model) | Median call |
|---|---|---|---|---|
| Regex and keyword baseline | 83.9% | 9 of 20 | 1 of 3 | n/a |
| Nemotron 3 Nano, reasoning off | 97.6% | 18 of 20 | 3 of 3 | 5.0 s |
| Nemotron 3 Nano, reasoning on | 100% | 20 of 20 | 3 of 3 | 11.8 s |
| Nemotron 3 Super | 100% | 20 of 20 | 3 of 3 | 1.3 s |

A keyword guard caught one of the harmful requests before any model saw it, and Nemotron refused the other two ("smash into its windscreen", "diving at" a dog).

## Stress testing (5 searches of 40 flights per searcher, against the improved controller with rules)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/stress-search-dark.png">
  <img alt="Bar charts of the stress-test search. Distinct failing conditions found in 200 flights: random 2, cross-entropy 10, corners 15, Nemotron Nano with reasoning 15, Nemotron Nano 39. Mean stress level of the failures: 0.59, 0.53, 0.62, 0.80, 0.85." src="figures/stress-search-light.png">
</picture>

| Searcher | Failures | Distinct failing conditions | Kinds of failure | Mean stress level of failures | Mildest failure |
|---|---|---|---|---|---|
| Random | 2 | 2 | 1 | 0.59 | 0.55 |
| Cross-entropy method | 10 | 10 | 8 | 0.53 | 0.50 |
| Corners of the parameter box | 15 | 15 | 13 | 0.62 | 0.50 |
| Nemotron 3 Nano | 46 | 39 | 13 | 0.85 | 0.48 |
| Nemotron 3 Nano, reasoning on | 16 | 15 | 11 | 0.80 | 0.46 |

Nemotron found 2.6 times as many distinct failing conditions as the best baseline, but no more kinds of failure than corner search, and mostly near the hard end of every range: the stress level runs from 0 (every condition at its easy end) to 1 (every condition at its hard end). Its mildest failures were slightly milder than any baseline's. With reasoning on, 20 of the 200 flights were random tests, which the search runs in place of a model's batch when the call fails: here 5 calls failed, 4 because reasoning used up the token limit and 1 with unterminated JSON.

## Ablations and sensitivity

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/safety-dark.png">
  <img alt="Left: drones lost in 180 held-out flights, original controller 300, improved controller flying the plan 2, with the rules 0, with Nano 0, with Super 0. Right: 1,000 stress tests re-flown without the safety filter, failing tests 89 to 289 and drones lost 391 to 2,019." src="figures/safety-light.png">
</picture>


Removing the leader's pacing cut time in formation during formation changes from 75% to 48%, and with motor failures from 93% to 85%. Removing slot reassignment cut it during formation changes to 54%. Removing the blend between formations raised it slightly, to 78%, but cost 7% more energy there and 5.5 s more on the forest trail. Removing consensus or the state filter made no measurable difference, and removing feedforward saved 1% of energy during formation changes. Removing the safety filter made no difference beyond noise in the nine scenarios, where the supervisor keeps the swarm out of trouble, but across the 1,000 stress tests it tripled the failures. With GPS error tripled, the improved controller with rules still lost no drones in the forest, where the original lost 9.1 per flight, but it held formation for less of the flight than the original did (4% against 13%). Neither kept the formation within 1 m for most of the flight.

## Where the AI did worse or did not help

* On the low-battery leg, Nano's directive was above the planned speed for 77% of the flight, against 55% for the rules. In 90 of the 150 updates where the status report said the planned speed was enough for every drone, Nano kept a higher speed, which its instructions forbid. Super did that once in 243. Part of Nano's time and energy gain on that leg comes from breaking the plan.
* Nano took longer than the rules to settle after a change of formation: 6.3 s against 4.8 s in the forest and 4.9 s against 2.6 s in the urban canyon.
* In gusts the rules widen the spacing and Nano does not, so its closest pairs were nearer (1.93 m against 2.08 m on average), with no near misses for either.
* Nemotron 3 Super, the larger model, held formation less well than the rules in the urban canyon, and on the low-battery leg it was slower, used more energy and retired more drones (1.55 per flight against 0.65).
* Nano with reasoning on completed 3 of 5 forest flights in the last tuning round, where the rules completed 5, and took several seconds per call. It was not run on the held-out seeds.
* Nano with reasoning off got 2 of 20 mission requests wrong: a convoy "nose to tail" became line abreast, and "2 km by 800 m" became 800 m by 2 m.
* In six of the nine scenarios, Nano never changed the plan, so its flights there were the same as flying with no supervisor.
