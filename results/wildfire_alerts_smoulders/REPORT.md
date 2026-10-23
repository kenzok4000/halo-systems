# Public alerts under pre-signed trigger rules

Seeds 9400-9419 (20 valleys), never flown before this measurement; 16 drones on today's flight (improved+rule), each valley flown under three alert policies. Made by `tools/wildfire_alerts_sweep.py`. Simulated phones only; no language model takes part in any alert.

| Per flight (sum over all valleys) | today's rule, automatic | pre-signed rules, automatic | pre-signed rules, smoulders too, automatic |
|---|---|---|---|
| Public alerts sent | 140 | 129 | 140 |
| of them Level 3 | 74 | 65 | 74 |
| Sent while every fire behind it was still smouldering | 11 | 9 | 11 |
| Sent for false fires only | 3 | 3 | 3 |
| Not justified: the fire did not reach the zone in the level's window | 44 | 39 | 44 |
| Phones warned before the fire reached them | 561 | 555 | 561 |
| Phones reached without a warning (with signal) | 0 | 6 | 0 |
| Phones warned that the fire did not reach within 3 h | 512 | 455 | 512 |
| Cards to fire dispatch | 0 | 101 | 101 |
| of them for false fires | 0 | 10 | 10 |
| Median warning lead, minutes (median over valleys) | 116.0 | 116.0 | 116.0 |
| Median wait for approval, seconds (median over valleys) | 0.0 | 0.0 | 0.0 |

Phones the fire reached within 3 hours, all valleys: 588.

Paired by valley, against today's rule (total over all valleys, 95% bootstrap interval over valleys):

| Difference | pre-signed rules, automatic | pre-signed rules, smoulders too, automatic |
|---|---|---|
| Phones warned before the fire reached them | -6 (-14 to +0) | +0 (+0 to +0) |
| Phones reached without a warning | +6 (+0 to +14) | +0 (+0 to +0) |
| Alerts while every fire behind it still smouldered | -2 (-6 to +0) | +0 (+0 to +0) |
| Alerts not justified | -5 (-10 to -1) | +0 (+0 to +0) |
| Public alerts sent | -11 (-21 to -3) | +0 (+0 to +0) |

## Alert for alert: the pre-signed rules with smoulders against today's rule

Each public alert is identified by the time it was sent, its level and its zone. In 20 of 20 valleys the pre-signed rules with smoulders sent the same public alerts as today's rule, alert for alert; 0 alerts went out under today's rule only and 0 under the pre-signed rules with smoulders only.
Their cards to fire dispatch (count, and count for false fires) matched the spreading-only pre-signed rules' in 20 of 20 valleys.

## What it shows (written after the run)

As predicted before the run (`PREREG.md`): in all 20 valleys the entry's design, the pre-signed rules with
smoulders too, sent exactly the public alerts today's rule sent, alert for alert (140 alerts; 561 of the 588 phones
the fire reached warned in time and none with signal missed, under both; a median lead of 116 minutes under both),
and its cards to fire dispatch matched the spreading-only rules' in every valley (101 cards, 10 of them for false
fires). That is what the code says should happen: with smoulders on, the pre-signed Level 3 rule takes the same
maximum over potential arrivals as today's rule, and the Level 4 and 5 rules are unchanged. The measurement exists
so that the alert results quoted for the entry's design rest on held-out valleys flown once after the design was
chosen, not on a reading of the code alone. The spreading-only rules showed the same pattern as on seeds 9000-9019
(`../wildfire_alerts/REPORT.md`): fewer alerts (11 fewer here, 7 there) and a few phones with signal reached before
any warning (6 here, 3 there).
