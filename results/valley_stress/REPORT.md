# Valley test engineer (T5b)

Searchers hunt for conditions under which the wildfire valley's ground station fails. Each search flies the same number of tests; test i of search s flies valley seed 9100 + 50 s + i for every searcher, and the drawn day (the committed valley's own conditions) is flown on the same valleys. The system under test is today's flight (improved controller, rule supervisor) with pre-signed alert rules, sent automatically; no language model takes part in any alert or card.

A test fails on a late warning (a phone the fire reached within 3 h, with signal, warned late or never), a missed fire (a fire in the survey block had been spreading for 5 minutes or more by the end of the flight, time before take-off included, and was never located), a false alarm (a public alert resting only on fires that do not exist), a needless evacuation (a Level 4 or 5 alert for a zone no fire comes near in 4 h) or a lost drone. Some valleys fail under the drawn day too, so a failure counts as caused by the conditions only when the same valley under the drawn day did not fail that way. Kinds pair what went wrong with coarse labels for the conditions (`causes()`); the stress level runs from 0 (every condition at its easy end) to 1 (all at the hard end).

## By searcher

| Searcher | Searches | Tests | Failures the conditions caused | Their kinds | Mildest of them (stress) | Failed tests (any) | late-warning | missed-fire | false-alarm | needless-evacuation | drone-lost | Phones warned late | Random top-ups | Model spend |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| random | 2 | 40 | 10 | 9 | 0.31 | 19 | 12 | 1 | 2 | 4 | 0 | 104 | 0 | USD 0.0000 |
| corners | 2 | 40 | 13 | 12 | 0.38 | 17 | 15 | 0 | 3 | 1 | 0 | 92 | 0 | USD 0.0000 |
| nemotron | 2 | 40 | 11 | 12 | 0.36 | 19 | 10 | 2 | 3 | 6 | 0 | 69 | 4 | USD 0.0260 |
| drawn-day | 1 | 40 | n/a | n/a | n/a | 14 | 9 | 1 | 3 | 1 | 0 | 26 | 0 | USD 0.0000 |

## Failing tests

| Searcher | Search | Test | Seed | What went wrong | Not on the drawn day | Conditions (changed from the drawn day) | Stress | Why (model) |
|---|---|---|---|---|---|---|---|---|
| random | 0 | 1 | 9100 | late-warning | late-warning | drones 28, wind_scale 1.453, wind_shift_deg -16.689, moisture_scale 0.832, forecast_error 0.617, smoulder_scale 1.64, patchiness 0.313, visibility_m 895.685, smoke_range_scale 0.423 | 0.50 |  |
| random | 0 | 5 | 9104 | late-warning | late-warning | drones 24, wind_scale 1.433, wind_shift_deg -70.077, moisture_scale 0.92, forecast_error 2.934, smoulder_scale 0.338, patchiness 0.325, visibility_m 727.239, smoke_range_scale 0.86 | 0.60 |  |
| random | 0 | 6 | 9105 | late-warning | late-warning | drones 27, wind_scale 1.086, wind_shift_deg 61.308, moisture_scale 1.311, forecast_error 0.927, smoulder_scale 1.414, patchiness 0.445, visibility_m 1632.8, smoke_range_scale 0.438 | 0.45 |  |
| random | 0 | 7 | 9106 | late-warning | none | drones 24, wind_scale 1.626, wind_shift_deg -33.811, moisture_scale 0.986, forecast_error 0.969, smoulder_scale 1.013, patchiness 0.529, visibility_m 1908.02, smoke_range_scale 0.91 | 0.42 |  |
| random | 0 | 8 | 9107 | false-alarm | none | drones 21, wind_scale 1.916, wind_shift_deg -37.643, moisture_scale 0.989, forecast_error 1.258, smoulder_scale 1.129, patchiness 0.578, visibility_m 1282.39, smoke_range_scale 0.643 | 0.58 |  |
| random | 0 | 9 | 9108 | needless-evacuation | needless-evacuation | drones 24, wind_scale 0.514, wind_shift_deg -8.024, moisture_scale 0.835, forecast_error 0.568, smoulder_scale 1.647, patchiness 0.148, visibility_m 612.038, smoke_range_scale 0.969 | 0.31 |  |
| random | 0 | 10 | 9109 | needless-evacuation | needless-evacuation | drones 8, wind_scale 1.647, wind_shift_deg -51.924, moisture_scale 1.397, forecast_error 0.891, smoulder_scale 1.717, patchiness 0.371, visibility_m 743.772, smoke_range_scale 0.405 | 0.60 |  |
| random | 0 | 12 | 9111 | late-warning | none | drones 25, wind_scale 1.164, wind_shift_deg -37.91, moisture_scale 1.339, forecast_error 0.957, smoulder_scale 0.533, patchiness 0.762, visibility_m 1814.62, smoke_range_scale 0.588 | 0.44 |  |
| random | 0 | 13 | 9112 | late-warning | none | drones 28, wind_scale 1.127, wind_shift_deg -34.497, moisture_scale 1.318, forecast_error 1.515, smoulder_scale 1.772, patchiness 0.113, visibility_m 2022.03, smoke_range_scale 0.762 | 0.29 |  |
| random | 0 | 17 | 9116 | missed-fire | none | drones 15, wind_scale 0.622, wind_shift_deg 39.432, moisture_scale 1.348, forecast_error 1.989, smoulder_scale 1.397, patchiness 0.357, visibility_m 2447.18, smoke_range_scale 0.978 | 0.29 |  |
| random | 0 | 19 | 9118 | false-alarm | false-alarm | drones 14, wind_scale 0.803, wind_shift_deg -56.961, moisture_scale 0.863, forecast_error 0.998, smoulder_scale 0.59, patchiness 0.236, visibility_m 2245.63, smoke_range_scale 0.984 | 0.37 |  |
| random | 1 | 3 | 9152 | late-warning | none | drones 6, wind_scale 1.667, wind_shift_deg 81.526, moisture_scale 0.973, forecast_error 2.317, smoulder_scale 1.627, patchiness 0.634, visibility_m 1856.29, smoke_range_scale 0.734 | 0.69 |  |
| random | 1 | 5 | 9154 | late-warning | none | drones 24, wind_scale 1.346, wind_shift_deg 32.854, moisture_scale 1.264, forecast_error 2.112, smoulder_scale 0.778, patchiness 0.028, visibility_m 994.483, smoke_range_scale 0.513 | 0.46 |  |
| random | 1 | 10 | 9159 | needless-evacuation | needless-evacuation | drones 11, wind_scale 1.793, wind_shift_deg 49.449, moisture_scale 0.986, forecast_error 2.89, smoulder_scale 0.67, patchiness 0.704, visibility_m 1824.51, smoke_range_scale 0.918 | 0.62 |  |
| random | 1 | 11 | 9160 | late-warning | none | drones 25, wind_scale 0.508, wind_shift_deg 63.153, moisture_scale 0.688, forecast_error 1.27, smoulder_scale 0.682, patchiness 0.498, visibility_m 1652.34, smoke_range_scale 0.689 | 0.48 |  |
| random | 1 | 12 | 9161 | needless-evacuation | none | drones 12, wind_scale 1.56, wind_shift_deg 2.735, moisture_scale 0.708, forecast_error 2.909, smoulder_scale 1.046, patchiness 0.6, visibility_m 1079.73, smoke_range_scale 0.409 | 0.72 |  |
| random | 1 | 14 | 9163 | late-warning | late-warning | drones 10, wind_scale 1.605, wind_shift_deg -18.578, moisture_scale 1.367, forecast_error 0.11, smoulder_scale 1.85, patchiness 0.376, visibility_m 648.87, smoke_range_scale 0.436 | 0.52 |  |
| random | 1 | 19 | 9168 | late-warning | late-warning | drones 21, wind_scale 0.58, wind_shift_deg 65.272, moisture_scale 0.917, forecast_error 1.965, smoulder_scale 0.658, patchiness 0.376, visibility_m 2245.68, smoke_range_scale 0.761 | 0.43 |  |
| random | 1 | 20 | 9169 | late-warning | late-warning | drones 15, wind_scale 0.651, wind_shift_deg 72.128, moisture_scale 1.043, forecast_error 0.489, smoulder_scale 1.866, patchiness 0.078, visibility_m 503.559, smoke_range_scale 0.973 | 0.41 |  |
| corners | 0 | 1 | 9100 | false-alarm, late-warning | late-warning | drones 4, wind_scale 2, wind_shift_deg -90, moisture_scale 0.6, forecast_error 0, smoulder_scale 0.25, patchiness 0 | 0.50 |  |
| corners | 0 | 9 | 9108 | false-alarm | false-alarm | drones 4, wind_scale 2, wind_shift_deg -90, moisture_scale 1.4, forecast_error 0, smoulder_scale 0.25, patchiness 0.8 | 0.50 |  |
| corners | 0 | 12 | 9111 | late-warning | none | drones 4, wind_scale 2, wind_shift_deg 90, moisture_scale 1.4, forecast_error 3, smoulder_scale 2, patchiness 0.8, visibility_m 500 | 0.75 |  |
| corners | 0 | 14 | 9113 | late-warning | late-warning | drones 4, wind_scale 0.5, wind_shift_deg 90, moisture_scale 1.4, forecast_error 0, smoulder_scale 0.25, patchiness 0.8 | 0.38 |  |
| corners | 0 | 16 | 9115 | late-warning | late-warning | drones 4, wind_scale 2, wind_shift_deg 90, moisture_scale 1.4, forecast_error 3, smoulder_scale 2, patchiness 0.8, visibility_m 500 | 0.75 |  |
| corners | 0 | 18 | 9117 | late-warning | late-warning | drones 4, wind_scale 0.5, wind_shift_deg -90, moisture_scale 0.6, forecast_error 3, smoulder_scale 2, patchiness 0 | 0.50 |  |
| corners | 0 | 19 | 9118 | late-warning | late-warning | drones 4, wind_scale 0.5, wind_shift_deg -90, moisture_scale 1.4, forecast_error 0, smoulder_scale 0.25, patchiness 0.8, visibility_m 500, smoke_range_scale 0.4 | 0.62 |  |
| corners | 0 | 20 | 9119 | late-warning | late-warning | drones 32, wind_scale 0.5, wind_shift_deg -90, moisture_scale 0.6, forecast_error 0, smoulder_scale 2, patchiness 0.8, smoke_range_scale 0.4 | 0.50 |  |
| corners | 1 | 2 | 9151 | late-warning | late-warning | drones 32, wind_scale 2, wind_shift_deg -90, moisture_scale 1.4, forecast_error 0, smoulder_scale 0.25, patchiness 0.8, visibility_m 500 | 0.50 |  |
| corners | 1 | 3 | 9152 | late-warning | none | drones 4, wind_scale 2, wind_shift_deg 90, moisture_scale 1.4, forecast_error 0, smoulder_scale 2, patchiness 0.8, visibility_m 500 | 0.62 |  |
| corners | 1 | 5 | 9154 | false-alarm, late-warning | false-alarm | drones 4, wind_scale 2, wind_shift_deg 90, moisture_scale 1.4, forecast_error 3, smoulder_scale 0.25, patchiness 0, visibility_m 500 | 0.62 |  |
| corners | 1 | 9 | 9158 | late-warning | none | drones 32, wind_scale 0.5, wind_shift_deg 90, moisture_scale 1.4, forecast_error 0, smoulder_scale 0.25, patchiness 0.8, smoke_range_scale 0.4 | 0.38 |  |
| corners | 1 | 16 | 9165 | late-warning | late-warning | drones 32, wind_scale 2, wind_shift_deg 90, moisture_scale 0.6, forecast_error 3, smoulder_scale 0.25, patchiness 0, visibility_m 500 | 0.62 |  |
| corners | 1 | 17 | 9166 | late-warning | none | drones 4, wind_scale 0.5, wind_shift_deg -90, moisture_scale 1.4, forecast_error 3, smoulder_scale 0.25, patchiness 0, visibility_m 500, smoke_range_scale 0.4 | 0.62 |  |
| corners | 1 | 18 | 9167 | needless-evacuation | needless-evacuation | drones 4, wind_scale 2, wind_shift_deg -90, moisture_scale 1.4, forecast_error 3, smoulder_scale 0.25, patchiness 0, visibility_m 500, smoke_range_scale 0.4 | 0.75 |  |
| corners | 1 | 19 | 9168 | late-warning | late-warning | drones 4, wind_scale 2, wind_shift_deg 90, moisture_scale 0.6, forecast_error 0, smoulder_scale 0.25, patchiness 0 | 0.50 |  |
| corners | 1 | 20 | 9169 | late-warning | late-warning | drones 4, wind_scale 2, wind_shift_deg 90, moisture_scale 0.6, forecast_error 0, smoulder_scale 2, patchiness 0.8, smoke_range_scale 0.4 | 0.75 |  |
| nemotron | 0 | 1 | 9100 | late-warning | late-warning | drones 8, wind_scale 1.5, wind_shift_deg 90, moisture_scale 0.6, forecast_error 2.5, smoulder_scale 0.5, patchiness 0.8, visibility_m 500, smoke_range_scale 0.4 | 0.92 | shift hides smoke under overhang, cameras miss fire causing missed-fire |
| nemotron | 0 | 3 | 9102 | false-alarm | none | wind_scale 1.8, wind_shift_deg 45, moisture_scale 0.7, forecast_error 2.5, smoulder_scale 1.5, patchiness 0.5, visibility_m 1500, smoke_range_scale 0.6 | 0.68 | unforecasted wind turn triggers needless evacuation despite no fire arrival |
| nemotron | 0 | 7 | 9106 | late-warning | none | wind_scale 1.2, wind_shift_deg 30, moisture_scale 1.2, forecast_error 2.8, patchiness 0.2 | 0.35 | high forecast error predicts spread that never occurs, causing needless evacuation |
| nemotron | 0 | 8 | 9107 | needless-evacuation | needless-evacuation | drones 32, wind_scale 2, wind_shift_deg 90, moisture_scale 0.6, forecast_error 0.5 | 0.44 | extreme wind shift and speed risk drone loss |
| nemotron | 0 | 9 | 9108 | needless-evacuation | needless-evacuation | drones 4, moisture_scale 0.6, smoulder_scale 0.5, patchiness 0.8, visibility_m 500, smoke_range_scale 0.4 | 0.71 | hidden fire spreads early and is hidden from thermal and smoke detection |
| nemotron | 0 | 10 | 9109 | needless-evacuation | needless-evacuation | drones 32, wind_scale 2, wind_shift_deg 90, moisture_scale 0.6, forecast_error 3, patchiness 0.8, visibility_m 500, smoke_range_scale 0.4 | 0.88 | severe gust and sudden wind shift cause a drone to crash |
| nemotron | 0 | 12 | 9111 | late-warning | none | wind_shift_deg 5, moisture_scale 0.9, forecast_error 1.2, patchiness 0.4, visibility_m 2000, smoke_range_scale 0.9 | 0.36 | reduced smoke visibility and slight forecast error delay warning |
| nemotron | 0 | 13 | 9112 | late-warning | none | drones 15, patchiness 0.35, visibility_m 2400, smoke_range_scale 0.9 | 0.30 | hidden fire under a rock overhang evades detection, causing a missed-fire |
| nemotron | 0 | 16 | 9115 | missed-fire | missed-fire | wind_shift_deg 5, moisture_scale 0.9, forecast_error 1.2, patchiness 0.4, visibility_m 2000, smoke_range_scale 0.8 | 0.38 | reduced smoke visibility and slight wind shift delay warnings, causing a late-warning |
| nemotron | 0 | 17 | 9116 | false-alarm, missed-fire | false-alarm | wind_shift_deg -51.95, moisture_scale 1.053, forecast_error 1.474, smoulder_scale 1.177, patchiness 0.366, visibility_m 2250.54, smoke_range_scale 0.465 | 0.48 | random top-up (model gave too few or unreadable tests) |
| nemotron | 0 | 19 | 9118 | false-alarm | false-alarm | drones 17, wind_scale 1.942, wind_shift_deg 6.684, moisture_scale 1.163, forecast_error 0.185, smoulder_scale 0.668, patchiness 0.295, visibility_m 1738.1, smoke_range_scale 0.783 | 0.38 | random top-up (model gave too few or unreadable tests) |
| nemotron | 0 | 20 | 9119 | late-warning, needless-evacuation | late-warning, needless-evacuation | drones 17, wind_scale 1.959, wind_shift_deg -85.641, moisture_scale 0.703, forecast_error 0.625, smoulder_scale 1.418, patchiness 0.785, visibility_m 1215.28, smoke_range_scale 0.405 | 0.77 | random top-up (model gave too few or unreadable tests) |
| nemotron | 1 | 3 | 9152 | late-warning | none | drones 4, wind_scale 0.5 | 0.28 | Very low drone count reduces coverage, likely producing missed-fire under baseline weather. |
| nemotron | 1 | 7 | 9156 | needless-evacuation | needless-evacuation | wind_scale 1.8, wind_shift_deg 60, forecast_error 2, patchiness 0.6 | 0.50 | large wind shift combined with high wind speed causes evacuation of a zone the fire never reaches |
| nemotron | 1 | 9 | 9158 | late-warning | none | drones 4, wind_shift_deg 60, smoulder_scale 0.25, patchiness 0.8, visibility_m 500, smoke_range_scale 0.4 | 0.73 | missed-fire due to low coverage and hidden fire |
| nemotron | 1 | 12 | 9161 | needless-evacuation | none | wind_shift_deg 15, forecast_error 2 | 0.33 | needless-evacuation with mild wind shift and forecast error |
| nemotron | 1 | 14 | 9163 | late-warning | late-warning | moisture_scale 0.6, forecast_error 3, smoulder_scale 0.25, smoke_range_scale 0.9 | 0.43 | high forecast error and dry fuel trigger unjustified public alert |
| nemotron | 1 | 17 | 9166 | late-warning | none | drones 4, wind_shift_deg 45, patchiness 0.8, visibility_m 500, smoke_range_scale 0.4 | 0.71 | Low drone count with high patchiness and limited visibility to trigger a missed-fire. |
| nemotron | 1 | 19 | 9168 | late-warning | late-warning | drones 32, wind_scale 2, wind_shift_deg 60 | 0.36 | Maximum wind speed and shift to induce a drone loss. |

## Failing tests re-flown under both rules

Each failing test (drawn-day tests included) flown twice more on the same valley and conditions, once under the pre-signed rules and once under today's rule (any located fire, a smoulder included, can raise a zone to Level 3). In both flights the alert delivery lags come from a stream of their own, so the station locates the same fires and forecasts the same way and only the alerts differ; the searches themselves drew the lags from the station's stream, so a re-flight can differ slightly from the test as searched (63 of 69 went wrong in exactly the same ways). Phones warned late: 263 under pre-signed rules, 102 under today's rule.

| What went wrong | Under pre-signed rules | Also under today's rule | Only under today's rule |
|---|---|---|---|
| late-warning | 43 | 12 | 0 |
| missed-fire | 4 | 4 | 0 |
| false-alarm | 11 | 11 | 1 |
| needless-evacuation | 11 | 11 | 0 |
| drone-lost | 0 | 0 | 0 |

## What it shows (written after the run)

**Nemotron was not a better test engineer than random or corner search.** On the same 40 valleys, the conditions it proposed caused 11 failures, against 10 for random search and 13 for corner search. Its failures were of 12 kinds (random 9, corners 12), and the mildest was no milder (stress 0.36, against 0.31 and 0.38). Three of its 11 came from random top-ups: one call reached the 12,000-token reply ceiling before it gave any tests, so its four tests were drawn at random, as the plan says. Its own 36 tests caused 8 failures. The searchers differ by a few tests in 40, well within chance, so the finding is "no better", not "worse".

**Its reasons rarely matched what happened.** Each proposal came with a reason naming the failure it expected. By a rough keyword check, that failure happened in 3 of its 36 tests. It expected missed fires in 13 tests and lost drones in 8; one fire was missed and no drone was lost. On the development seeds it found no failure the conditions caused (above). Nemotron 3 Nano reads the history it is given, but here it did not learn the station's weak points from it.

**What the failures say about the station**, from all searchers together:

- Late warnings are the commonest failure for every searcher. Under the pre-signed rules, a smoulder that takes off near homes brings no public alert until the station believes it is spreading. Re-flown under today's rule, 31 of the 43 failing tests with late warnings warned every phone in time, and phones warned late fell from 263 to 102.
- The other 12 late-warning tests fail under either rule, because the station located the fire too late. Corner search found these most often: 7 of the 10 late warnings its conditions caused flew 4 drones, often in haze or with short smoke range.
- Needless evacuations came with strong wind: 7 of the 9 the conditions caused had the wind at 1.5 times the day's or more, often with a wind shift the forecast did not know about.
- False alarms rest on false fires the station located and never cleared. Three drawn-day valleys had one under both alert rules.
- No drone was lost in 160 tests, including 32 drones in twice the day's wind.

**Pre-signed rules against today's rule on every valley (added after the run).** The re-flights above cover only the failing tests, so they cannot show what today's rule costs on the valleys where the pre-signed rules did well. After the run we flew all 40 drawn-day valleys under both rules, paired the same way (`drawn_day_both_rules.json`, made by `tools/wildfire_valley_both_rules.py` from the checkout of 902d69d):

| All 40 drawn-day valleys | Pre-signed rules | Today's rule |
|---|---|---|
| Phones the fire reached within 3 h | 1,176 | 1,176 |
| Warned late or never (with signal) | 50, in 9 valleys | 0 |
| Public alerts sent | 242 | 284 |
| of them while every fire behind it still smouldered | 25 | 61 |
| Not justified within the level's window | 69 | 83 |
| Resting only on false fires | 15 | 15 |
| Valleys with a needless evacuation | 1 | 1 |
| Cards to fire dispatch | 195 | none (today's rule sends no cards) |
| Earliest tenth of warnings, median lead over valleys | 69.8 min | 73.8 min |

25 of the 50 late phones are in one valley, 9103, which as searched had none. The re-flight draws the station's random numbers without the delivery lags in between, so it locates and forecasts slightly differently. As searched, the drawn day warned 26 phones late in 9 valleys. So on these 40 valleys the pre-signed rules warn 26 to 50 phones late, depending on the draw, and today's rule, flown on the same draw as the 50, warned none late. With T3 (seeds 9000-9019: 3 of 660 under the pre-signed rules, none under today's rule), the pre-signed rules warned 29 to 53 of 1,836 phones late or never (about 1 in 63 to 1 in 35), in 11 of 60 valleys. Today's rule warned all of them in time, for about 13% more public alerts (432 against 383) and more alerts the fire did not justify within the level's window (127 against 110). On 21 October Kenzo was asked whether public alerts under the pre-signed rules should count smoulders too; docs/WILDFIRE.md gives the answer.

**Spend.** Ten Nemotron calls, USD 0.026; the development searches used USD 0.0065 more. The ledger total after the run was USD 8.3131, of the USD 8.74 Kenzo approved.
