# Public alerts under pre-signed trigger rules

Seeds 9000-9019 (20 valleys), never flown before this measurement; 16 drones on today's flight (improved+rule), each valley flown under three alert policies. Made by `tools/wildfire_alerts_sweep.py`. Simulated phones only; no language model takes part in any alert.

| Per flight (sum over all valleys) | today's rule, automatic | pre-signed rules, automatic | pre-signed rules, one-tap approval |
|---|---|---|---|
| Public alerts sent | 148 | 141 | 118 |
| of them Level 3 | 75 | 68 | 52 |
| Sent while every fire behind it was still smouldering | 11 | 9 | 7 |
| Sent for false fires only | 0 | 0 | 0 |
| Not justified: the fire did not reach the zone in the level's window | 44 | 41 | 39 |
| Phones warned before the fire reached them | 635 | 632 | 631 |
| Phones reached without a warning (with signal) | 0 | 3 | 4 |
| Phones warned that the fire did not reach within 3 h | 360 | 336 | 335 |
| Cards to fire dispatch | 0 | 92 | 91 |
| of them for false fires | 0 | 11 | 10 |
| Median warning lead, minutes (median over valleys) | 127.6 | 127.6 | 126.3 |
| Median wait for approval, seconds (median over valleys) | 0.0 | 0.0 | 85.8 |

Phones the fire reached within 3 hours, all valleys: 660.

Paired by valley, against today's rule (total over all valleys, 95% bootstrap interval over valleys):

| Difference | pre-signed rules, automatic | pre-signed rules, one-tap approval |
|---|---|---|
| Phones warned before the fire reached them | -3 (-8 to +0) | -4 (-9 to +0) |
| Phones reached without a warning | +3 (+0 to +8) | +4 (+0 to +9) |
| Alerts while every fire behind it still smouldered | -2 (-6 to +0) | -4 (-12 to +0) |
| Alerts not justified | -3 (-9 to +2) | -5 (-13 to +2) |
| Public alerts sent | -7 (-14 to -1) | -30 (-41 to -20) |

## What it shows (written after the run)

Pre-signed rules changed little. Over 20 valleys they sent 141 public alerts against 148 under today's rule, 9
against 11 while every fire behind them still smouldered, and warned 336 phones the fire did not reach within 3 h,
against 360. Level 4 and 5 alerts were nearly the same (48 and 25 against 49 and 24): those come from fires
already spreading, under either rule. The cost: 3 of the 660 phones the fire reached got no warning in time,
against none under today's rule, in two valleys. All three were hikers on the ridge trail, reached 2.4 to 2.7 hours
after take-off. In 9001 the fire that reached the hiker had been found smouldering and took off 41 minutes after
take-off; in 9009 it was the overhang fire, found by its smoke, taking off at 46 minutes. The swarm had landed after
4 minutes, so no drone saw either fire spread and no public alert followed; today's rule had warned those phones
on the smoulder. Both fires had gone to fire dispatch as cards 30 s into the flight (checked by re-flying the two
valleys on the same code). The median warning lead was the same (128 minutes).

Against the predictions written before the run: fewer alerts and fewer for smoulders, as predicted, though by
less than the development valleys suggested (23 against 28 there); some phones missed in valleys where a
smouldering fire takes off after the swarm lands, as predicted; the lead within 5 minutes, as predicted.
One-tap approval waited a median of 86 s per valley and sent 118 alerts: while an alert waits, a later forecast
can raise the zone's level, so fewer, higher alerts go out (426 phones told to evacuate against 386), and one
more phone was warned late.

What this means for the pre-signed design: the public sees slightly fewer alerts, and a fire found smouldering
depends on fire dispatch acting on its card, or on another flight seeing it take off. A patrol that keeps flying
(squads) or a second flight might close the gap; that was not measured here. The initial-attack model
(`results/wildfire_initial_attack/`) shows how often the first crew reaches such a fire while it still smoulders.
