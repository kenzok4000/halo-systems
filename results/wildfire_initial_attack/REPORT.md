# Initial attack: fires that escape the first crew

Seeds 9000-9039 (40 valleys, 197 fires), never used to set anything here. Made by `tools/wildfire_initial_attack.py`; the model and every number in it are in its docstring.

| | Today's reports | With the drones' reports |
|---|---|---|
| First crew on scene while the fire still smoulders | 0 of 197 | 79 |
| Median size when the first crew arrives, ha | 0.62 | 0.07 |
| Fires escaped, one Type 3 engine | 150 of 197 (76.1%) | 95 (48.2%) |
| Fires contained at any size, and their median size then (ha) | 19 (3.04) | 92 (0.0) |

Crew on a smoulder: 34.0 to 46.2 percentage points more with the drones (95% bootstrap over valleys). Escapes prevented: 55 (21.8 to 34.2 percentage points). The drones reported 121 fires before any of today's methods.

| Sensitivity | Escaped today | With drones | Prevented | Crew on a smoulder today / with drones |
|---|---|---|---|---|
| response fixed 15 min | 146 (74.1%) | 77 (39.1%) | 69 | 0 / 103 |
| response fixed 30 min | 154 (78.2%) | 92 (46.7%) | 62 | 0 / 80 |
| response fixed 60 min | 156 (79.2%) | 120 (60.9%) | 36 | 0 / 46 |
| response fixed 120 min | 156 (79.2%) | 143 (72.6%) | 13 | 0 / 15 |
| crew: Type 6 engine, 2 people | 155 (78.7%) | 100 (50.8%) | 55 | 0 / 79 |
| crew: Helitack crew, 5 people | 155 (78.7%) | 102 (51.8%) | 53 | 0 / 79 |
| crew: 20-person Type 2 crew, sustained | 155 (78.7%) | 100 (50.8%) | 55 | 0 / 79 |
| line rate halved | 156 (79.2%) | 101 (51.3%) | 55 | 0 / 79 |
| line 2 times one engine's (more crews arriving together) | 132 (67.0%) | 85 (43.1%) | 47 | 0 / 79 |
| line 4 times one engine's (more crews arriving together) | 100 (50.8%) | 50 (25.4%) | 50 | 0 / 79 |
| line 8 times one engine's (more crews arriving together) | 59 (29.9%) | 26 (13.2%) | 33 | 0 / 81 |
| escape above 2 ha | 152 (77.2%) | 97 (49.2%) | 55 | 0 / 79 |
| escape above 20 ha | 143 (72.6%) | 89 (45.2%) | 54 | 0 / 79 |
| today with a camera that sees every fire | 147 (74.6%) | 94 (47.7%) | 53 | 0 / 79 |

## What it shows (written after the run)

Predictions written before the run, against the outcome:

| Prediction (PREREG.md) | Outcome |
|---|---|
| With the drones, the first crew reaches a smouldering fire in 35 to 60% of fires | 79 of 197 (40%) |
| With today's reports, under 5% | 0 of 197 |
| With one engine, escapes with today's reports stay above 80% | 150 of 197 (76%): lower than predicted |
| The drones prevent between a third and a half of them | 55 of 150 (37%) |
| With stronger crews both arms escape less and the gap narrows | Yes: 47, 50 and 33 prevented at 2, 4 and 8 times one engine's rate, against 55; a larger share of today's escapes (36%, 50%, 56%) |

What the drones change is when the first crew arrives. Today's methods see a fire only once it spreads, so no
crew reached one while it smouldered. With the drones' cards to fire dispatch, the crew reached 79 of 197 fires
(40%) still smouldering, and 77 of those were held. The 92 fires contained with the drones' reports had a median
size of 4 m^2 when contained (the table rounds it to 0.0 ha); the 19 contained with today's reports, 3.04 ha.

Most fires found after they started spreading outran one engine with either report: on this dry, windy day a fire
that has taken off grows faster than one crew builds line. So 53 of the 55 escapes the drones prevent are
fires the crew reached before they took off. The response time matters most: with it fixed at 15 minutes the
drones prevent 69 escapes, at 60 minutes (the value Reimer et al. 2019 used for lightning fires) 36, at 120
minutes 13. Giving today's methods a camera that sees every fire changes their escapes from 150 to 147: the gap
is about seeing fires before they spread, not camera coverage.

Counts: 200 true fires; 3 never spread in the truth and are left out. 37 fires with today's reports and 17 with
the drones' were overrun by a neighbour's front before they spread and count as part of that fire. The drones
reported 121 of the 197 first.

**Caveat.** The valley is a dry, windy day by design, so its escape rates are far above real ones (CAL FIRE's goal
is to contain 95% of fires at 10 acres or less). The absolute rates do not transfer; the share of escapes prevented (37%
with one engine at a 30-minute median response, 23% at a fixed 60 minutes, 36 to 56% with stronger first crews)
is the result to carry over, and only to fires the drones would find while they smoulder. The perimeter race
lets a fire burn freely until contained, which overstates sizes in both arms.
