# Who reported each fire first, by when it starts to spread

A post-hoc breakdown of the committed held-out rows (seeds 4000-4019 in `results/wildfire_valley/`, seeds 7000-7019 in `results/wildfire_valley_7000/`). Nothing was flown for it, and the bins were picked after those results were known. Made by `tools/wildfire_spread_timing.py`.

Why: the drones' lead over today's methods comes mostly from the smouldering phase, and the scenario sets how long each fire smoulders (from 10 to 240 minutes after take-off, by type; `docs/WILDFIRE.md`). Observed lightning holdover times are longer: across 42 datasets the median time from strike to detection ranges from 1.4 to 53.6 hours (Moris et al. 2023, Earth System Science Data 15: 1151). "Today's four" are the geostationary and polar satellites, an AI camera network with a clear view, and public calls, modelled as in the main report; "first" means the drones' report came before the earliest of the four.

## Seeds 4000-4019

| When the fire starts to spread | Fires | Found by 16 drones | Reported first by 16 drones | Lead when first, median (min) | Today's four, median minutes after it spread | Reported first by 4 drones | by 1 drone |
|---|---|---|---|---|---|---|---|
| already spreading at take-off | 30 | 27 | 1 | 3 | 4 | 0 | 0 |
| 0 to 20 min after take-off | 9 | 8 | 8 | 17 | 4 | 4 | 0 |
| 20 to 60 min | 19 | 17 | 17 | 43 | 3 | 15 | 7 |
| 60 to 120 min | 22 | 17 | 17 | 80 | 4 | 16 | 13 |
| after 120 min | 20 | 17 | 17 | 143 | 4 | 20 | 20 |

All fires: 16 drones reported 60 of 100 first.

## Seeds 7000-7019

| When the fire starts to spread | Fires | Found by 16 drones | Reported first by 16 drones | Lead when first, median (min) | Today's four, median minutes after it spread | Reported first by 4 drones | by 1 drone |
|---|---|---|---|---|---|---|---|
| already spreading at take-off | 27 | 24 | 0 | n/a | 4 | 0 | 0 |
| 0 to 20 min after take-off | 12 | 9 | 9 | 13 | 4 | 4 | 0 |
| 20 to 60 min | 27 | 23 | 23 | 36 | 4 | 21 | 12 |
| 60 to 120 min | 18 | 14 | 14 | 77 | 2 | 14 | 10 |
| after 120 min | 16 | 16 | 16 | 150 | 4 | 15 | 16 |

All fires: 16 drones reported 62 of 100 first.

## What it shows

- Fires already spreading at take-off: the drones were first on 1 of 30 and 0 of 27. A satellite or a camera usually has them before the swarm takes off; the drones add a precise position and the spread forecast, not an earlier report.
- Fires that start to spread within 20 minutes of take-off, the shortest smoulders in the scenario: the drones were first on 8 of 9 and 9 of 12, by a median of 17 and 13 minutes. That lead needs no long smoulder.
- Longer smoulders give longer leads, close to the time left before the fire spreads plus today's detection delay (right panel of `timing.png`). So the 69-minute median lead in the main report (62 on seeds 7000-7019) is a property of the smoulder times the scenario draws, not something the drones add on their own. Observed holdover times run from strike to detection, so they are not a smoulder time, but they are long (medians of 1.4 to 53.6 hours), which suggests the scenario's 10 to 240 minutes are not generous.
