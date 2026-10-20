# Results

Every flight result here comes from held-out seeds 1000-1019, each flight run once, except the wildfire valley, which has its own held-out seeds 4000-4019; the stress search flies its own search seeds, and the mission compiler is scored on a held-out set of 20 requests. The simulator, controllers and rules were frozen before any held-out flight (in the wildfire valley, before the flights reported here; see its protocol), and the Nemotron setup was chosen on tuning seeds 0-9 and committed before any Nemotron flight on the held-out seeds ([BENCHMARKS.md](BENCHMARKS.md)). The generated reports behind each section are [REPORT.md](../results/REPORT.md) (95% confidence intervals and seed-by-seed comparisons for every number), [STRESS.md](../results/STRESS.md), [wildfire/REPORT.md](../results/wildfire/REPORT.md) and [wildfire_valley/REPORT.md](../results/wildfire_valley/REPORT.md). The figures outside the wildfire valley are drawn from the committed rows by [tools/make_figures.py](../tools/make_figures.py), which also prints every plotted number.

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

The sweep was flown in a 3 to 6 m/s breeze with turbulence. The harder weather a large fire can whip up, gust fronts, is tested in the supervision scenarios below, with radio loss, motor failures and short batteries. In this scenario the fire does not spread: hotspots are fixed points, and a camera finds one once its footprint has covered it for half a second. The wildfire valley below simulates the spread. No scenario simulates the fire's effect on its own wind; gust fronts are injected to stand in for it.

## Wildfire valley: finding fires, forecasting their spread, warning phones

The wildfire valley is a mountain valley the morning after dry lightning, with five fires per flight: one already spreading in open scrub, one under tree crowns, one under a rock overhang that no camera looking down can see, one smouldering in the open, and one across the valley outside the survey block. The swarm sweeps the valley floor with downward thermal cameras and watches ahead for smoke. The ground station locates the fires, forecasts their spread with an ensemble of 16 runs of Rothermel's fire model, 3 hours ahead, briefs the fire crews and sends evacuation alerts zone by zone at Japan-style levels (3 prepare, 4 evacuate, 5 emergency). Alerts go out automatically by fixed rules, each after an alert check, to simulated phones only; an operator can cancel an alert or give a zone the all-clear. Nothing in this scenario calls a language model: none finds the fires, makes the forecast or decides an alert. The drones carry the sensors of the Halo scout in [hardware/](../hardware/README.md), a design priced from off-the-shelf parts. The scenario, the station and every assumption with its source are in [WILDFIRE.md](WILDFIRE.md).

**Protocol.** Held-out seeds 4000 to 4019 (20 valleys, 100 fires), each flown once with the final code: 80 flights, with 1, 4 and 16 drones, and the 16-drone flights again with alerts by distance alone, all on the improved controller with the rule supervisor. Seeds 2000 to 2011 and 3000 to 3019 were used while the scenario and the station were built. An earlier sweep on seeds 4000 to 4019 was stopped part-way when the station changed, and its rows were set aside unread. The models of today's detection methods were checked only on seeds 3000 to 3003. Brackets are 95% bootstrap intervals over seeds. The tables are copied from [wildfire_valley/REPORT.md](../results/wildfire_valley/REPORT.md), which `tools/wildfire_valley_report.py` generates from [runs.jsonl](../results/wildfire_valley/runs.jsonl) (means and intervals in [summary.json](../results/wildfire_valley/summary.json)); `tools/wildfire_valley_figures.py` draws the charts.

**What is simulated.** The valley simulates the fire's spread (Rothermel's surface fire model on land cover, slope and wind), plume heights, smoke and heat detection, the forecast, the alerts and the phones. Smoke and heat detection are probability models of a detector, not image recognition. Crown fire, spotting and the fire's effect on its own wind are not modelled; each makes real fires faster and less predictable than this forecast assumes. The swarm flies its sweep and lands without going back to check a fire, it flies 20 m above the canopy straight over a spreading fire, its simulated GNSS error is about a sixth of the scout's receiver, and an alert counts as delivered, not as acted on.

![Map of one held-out flight (seed 4012, 16 drones, all five fires found): the valley's land cover and ridges, the true burnt area 1 and 3 hours after the last forecast against the forecast's median outlines, the fires located by thermal camera and by smoke, the twelve evacuation zones, and the phones coloured by the alert level they received](../results/wildfire_valley/map.png)

### Fires found by swarm size

A fire counts as found when the station places one within 60 m of its burning ground (its ignition point while it smoulders). A false fire is a location more than 60 m from every fire's burning ground, including ones the station later merged into a real fire. Mapped: burning ground the thermal cameras mapped by the end of the flight ([REPORT.md](../results/wildfire_valley/REPORT.md#finding-the-fires)).

| Drones | Fires found (%) | False fires per flight | Duplicates merged per flight | Smoke fix error, median (m) | Mapped (ha), median | All found by (s) |
|---|---|---|---|---|---|---|
| 1 | 60.0 [60.0, 60.0] | 0.00 [0.00, 0.00] | 0.35 | n/a | 0.21 | none of 20 flights |
| 4 | 77.0 [72.0, 82.0] | 0.30 [0.00, 0.75] | 0.25 | 5.7 | 0.20 | 2 of 20 flights; median 459 s |
| 16 | 86.0 [80.0, 91.0] | 0.45 [0.25, 0.65] | 0.15 | 9.3 | 0.19 | 8 of 20 flights; median 189 s |

More drones found more fires, but also placed more false ones: 0.45 per flight with sixteen. Sixteen drones found all five fires in 8 of 20 flights. One drone cannot locate a fire by smoke, because a smoke fix needs bearings from three or more drones ([WILDFIRE.md](WILDFIRE.md)). In all 20 of its flights the single drone was also retired at its battery reserve before completing the mission (`completed` false and one drone retired in every one-drone row of runs.jsonl).

### Hidden fires

By where the fire was: share found, share of those found by smoke, and the median time to find it.

| Fire | 1 drone | 4 drones | 16 drones |
|---|---|---|---|
| Spreading at the start, open scrub | 100% found, 0% by smoke, 288 s | 100% found, 80% by smoke, 88 s | 95% found, 89% by smoke, 1 s |
| Under tree crowns | 100% found, 0% by smoke, 352 s | 95% found, 16% by smoke, 188 s | 95% found, 26% by smoke, 34 s |
| Under a rock overhang | 0% found, 0% by smoke | 75% found, 100% by smoke, 130 s | 75% found, 100% by smoke, 24 s |
| Smouldering in the open | 100% found, 0% by smoke, 345 s | 95% found, 63% by smoke, 184 s | 80% found, 88% by smoke, 19 s |
| Across the valley | 0% found, 0% by smoke | 20% found, 100% by smoke, 393 s | 85% found, 100% by smoke, 140 s |

The two fires no downward camera can reach were found only by smoke: the one under the overhang in 75% of flights with four or sixteen drones, and the one across the valley in 20% of flights with four and 85% with sixteen.

![Four bar charts by swarm size (1, 4 and 16 drones), means with 95% intervals over the 20 held-out seeds: fires found 60, 77 and 86%; forecast overlap at 1 hour (Sorensen) 0.77, 0.74 and 0.71; arrival error at phones 17.6, 20.4 and 24.6 minutes; phones warned before the fire 94.7, 94.9 and 94.7%](../results/wildfire_valley/swarm_size.png)

### Against today's ways of finding fires

The same fires, found by the methods fire services use today, modelled from published figures with the benefit of the doubt: no cloud, a camera with a clear view of the whole valley, every satellite detection credited to the right fire, the fastest published delivery (`src/halo_systems/sim/detection.py`; sources in [WILDFIRE.md](WILDFIRE.md#drones-against-todays-methods)). They watch for 4 hours from 08:00; the drones fly once, for up to 40 minutes, and anything they miss counts as not found. Time is measured from when each fire started to spread: negative means found while it still smouldered. Shares are of all 100 fires.

| Method | Found in 4 h (%) | Found before it spread (%) | Found under 0.1 ha (%) | Minutes after it spread, median | Size when found, median (m²) | Says where to within |
|---|---|---|---|---|---|---|
| Geostationary satellite (GOES, 5-min scans) | 93 [89, 97] | 0 [0, 0] | 0 [0, 0] | +55 | 15,700 | a 2 km pixel or larger |
| Polar-orbiting satellites (MODIS, VIIRS) | 89 [83, 95] | 0 [0, 0] | 3 [0, 6] | +128 | 72,800 | a 375 m to 1 km pixel |
| AI camera network, clear view | 100 [100, 100] | 0 [0, 0] | 100 [100, 100] | +4 | 200 | a bearing from each camera |
| AI camera network, as deployed (sees 22% of fires) | 24 [17, 32] | 0 [0, 0] | 24 [17, 32] | not found | 150 | a bearing from each camera |
| Public calls (911 / 119) | 99 [97, 100] | 0 [0, 0] | 78 [70, 86] | +4 | 200 | the caller's description |
| Earliest of today's four (camera with a clear view) | 100 [100, 100] | 0 [0, 0] | 100 [100, 100] | +4 | 100 |  |
| Halo drones, 1, one flight | 60 [60, 60] | 40 [40, 40] | 43 [40, 46] | +26 | 4 | its burning ground (median, measured) |
| Halo drones, 4, one flight | 77 [72, 82] | 55 [50, 60] | 59 [53, 65] | -18 | 4 | 3 m (median, measured) |
| Halo drones, 16, one flight | 86 [80, 91] | 59 [52, 66] | 68 [62, 74] | -20 | 4 | 5 m (median, measured) |
| Today's four plus 16 drones | 100 [100, 100] | 59 [52, 66] | 100 [100, 100] | -20 | 5 |  |

With 1 drone, the drone reported 40 of 100 fires before the earliest of today's four, by a median of 112 minutes. With 4 drones, the swarm reported 55 of 100 fires before the earliest of today's four, by a median of 74 minutes. With 16 drones, the swarm reported 60 of 100 fires before the earliest of today's four, by a median of 69 minutes. Each median is over the fires that swarm reported first, a different set for each size.

These are models of today's methods built from cited figures, not measurements of them. Cameras and callers start their clock when the fire starts to spread, so none of them finds a smouldering fire here; the drones' lead comes mostly from that phase, and from smoulder times the scenario sets (from 20 minutes before take-off to 4 hours after). Once a fire spreads, a camera with a clear view or a call from the public reports it about 4 minutes later. Aerial patrols after lightning are left out because no source gives how often or when they fly. The drones add to today's methods rather than replace them: together, today's four and sixteen drones found every fire.

![Scatter plot of when each of the 100 fires was reported, in minutes after it started to spread, against its size when reported on a log scale. Sixteen drones (86 of 100 found) report most of their fires before they spread, at a few square metres; an AI camera with a clear view (100 of 100) and public calls (99 of 100) report fires a few minutes after they spread, at 100 to 200 m²; the geostationary satellite (93 of 100) and polar satellites (89 of 100) report them later and larger](../results/wildfire_valley/detection.png)

### Forecast skill

The last forecast of each flight against the true spread; fewer drones sweep for longer, so their last forecast is made later, with more of the fire mapped. Sorensen index: overlap between the ground the forecast's median says will burn and the ground that does burn, counting only ground that burns after the forecast (1 is a perfect match). "Located fires" compares with the true spread of the fires the station knew were spreading; "all fires" with every fire, found or not. Phone arrival error: forecast median arrival minus the true arrival, at phones the fire reaches within 3 hours (negative is early). Brier score: the forecast probability of fire within 60 minutes at each phone against what happened (0 is perfect).

| Drones | Made at (min), median | Sorensen, 1 h | Sorensen, 3 h | Sorensen, 3 h, all fires | Phone arrival error, mean abs (min) | Bias (min) | Brier, 60 min |
|---|---|---|---|---|---|---|---|
| 1 | 13.0 | 0.77 [0.72, 0.81] | 0.89 [0.87, 0.91] | 0.76 [0.71, 0.81] | 17.6 [11.6, 25.5] | 12.9 [5.9, 21.8] | 0.038 [0.022, 0.059] |
| 4 | 8.5 | 0.74 [0.68, 0.79] | 0.85 [0.82, 0.89] | 0.76 [0.71, 0.80] | 20.4 [15.2, 26.5] | 12.6 [4.5, 20.9] | 0.032 [0.020, 0.048] |
| 16 | 3.5 | 0.71 [0.63, 0.78] | 0.83 [0.79, 0.86] | 0.77 [0.72, 0.82] | 24.6 [17.7, 33.2] | 2.2 [-9.4, 15.2] | 0.029 [0.018, 0.042] |

Fewer drones score better on overlap with the located fires and on arrival error, because they finish later and forecast with more of the fire mapped. Against all fires, found or not, the three swarm sizes overlap about equally, and sixteen drones have the lowest mean bias and Brier score.

### Warnings to phones

Each flight has 63 simulated phones (homes, a campground, hikers, drivers and a ranch); 5% have no signal. Alerts arrive 5 to 30 s after they are sent. A phone is reached when fire comes within 50 m of it within 3 hours of take-off. Warned in time: an alert at Level 3 or above delivered before the fire arrives, as a share of the phones reached. Lead time: how long before the fire arrives the first alert lands (median over phones, then over flights). Evacuated: told Level 4 or 5.

| Drones | Phones reached per flight | Warned in time (%) | Missed, with signal | Missed, no signal | Lead time, median (min) | Lead time, 10th pct (min) | Evacuation lead, median (min) | Evacuated but not reached in 3 h (%) | Alerts per flight, Levels 3 / 4 / 5 | Alerts rejected by the check |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 32.1 | 94.7 [93.0, 96.4] | 2 | 32 | 124 | 60 | 64 | 10 [5, 18] | 4.2 / 1.9 / 1.0 | 0 of 144 |
| 4 | 32.1 | 94.9 [93.2, 96.5] | 1 | 32 | 126 | 62 | 85 | 18 [9, 29] | 4.5 / 2.1 / 1.1 | 0 of 154 |
| 16 | 32.1 | 94.7 [93.0, 96.4] | 2 | 32 | 126 | 64 | 100 | 20 [9, 32] | 3.6 / 2.2 / 1.4 | 0 of 144 |

The share warned in time is about the same for every swarm size, and for alerts by distance alone (below), so it is not a gain from the swarm or the forecast. With sixteen drones, 32 of the 34 phones missed had no signal. The check rejected none of the alerts, so these flights do not show it stopping a bad one.

### Forecast against distance

The 16-drone flights again, with the alert rule replaced by distance alone: Level 5 within 150 m and Level 4 within 800 m of a spreading fire, Level 3 within 1,600 m of any. Paired by seed; differences are forecast minus distance.

| Drones | Rule | Warned in time (%) | Lead time, median (min) | Phones evacuated per flight | Evacuated but not reached in 3 h (%) | Warned but not reached in 3 h per flight |
|---|---|---|---|---|---|---|
| 16 | forecast | 94.7 [93.0, 96.4] | 126 | 23.4 | 20 [9, 32] | 17.9 |
| 16 | distance | 95.0 [93.3, 96.7] | 126 | 59.2 | 49 [41, 57] | 28.8 |

Over 20 paired seeds the forecast rule changes phones warned in time by -0.1 [-0.2, 0.0] per flight and phones evacuated that the fire did not reach by -24.1 [-30.2, -18.2] per flight. The forecast told about 24 fewer phones per flight to leave that the fire did not reach, and warned about the same share in time.

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

Higher time in formation and lower time and energy are better. A seed counts as tied when the difference is within 1% of the rules' mean, which is why the forest trail's energy saving, real but small, shows as 20 ties. The figure below draws the same intervals for every scenario where a supervisor matters, Super included. Super was worse than the rules on the low-battery leg (+7.39 s [+3.38, +11.47] and +2.48% energy [+0.81, +4.23]) and held formation less well in the urban canyon (−4.47 pp [−8.06, −0.82]).

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
| Nemotron 3 Nano, reasoning on | 100% | 20 of 20 | 3 of 3 | 11.7 s |
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

Nemotron found 2.6 times as many distinct failing conditions as the best baseline, but no more kinds of failure than corner search, and mostly near the hard end of every range: the stress level runs from 0 (every condition at its easy end) to 1 (every condition at its hard end). Its mildest failures were slightly milder than any baseline's. With reasoning on, 20 of the 200 flights were random tests, which the search runs in place of a model's batch when the call fails: here 5 calls failed, all at the 8,000-token limit: 4 before any answer and 1 partway through its JSON.

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
