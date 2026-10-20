# Public alerts under pre-signed trigger rules: plan for the held-out measurement (T3)

Written before any of seeds 9000-9019 was flown. The code that flies them is the commit that adds this file, run
from a separate checkout of that commit. Made by `tools/wildfire_alerts_sweep.py`.

## Question

On 20 October Kenzo chose pre-signed trigger rules for the public alerts: the alerting authority signs each rule in
advance (T3 at Level 3, T4 at Level 4, T5 at Level 5), a public alert goes out only for fires the station believes
are spreading, and a newly found fire, smouldering or not, goes to fire dispatch as a card instead. Against today's
committed rule (any located fire, a smoulder included, can raise a zone to Level 3), what does that gain in alerts
nobody needed, and what does it cost in phones warned in time? What does an official's one-tap approval add?

## Arms (each valley flown once per arm; the flight is the same in all three, only the alerts differ)

- **Today's rule, automatic** (`forecast`/`auto`): the committed rule.
- **Pre-signed rules, automatic** (`presigned`/`auto`).
- **Pre-signed rules, one-tap approval** (`presigned`/`modelled`): an official approves each alert after a modelled
  delay (median 90 s).

16 drones on today's flight, improved+rule, the legacy station, report_v2 scoring. Simulated phones only; no
language model takes part in any alert or card.

## Measures

- **Primary (safety): phones warned before the fire reached them**, pre-signed automatic minus today's rule, total
  over valleys with a 95% bootstrap interval over valleys (10,000 resamples); with it, phones the fire reached
  without any warning.
- **Co-primary (needless alerts): alerts sent while every fire behind them was still smouldering**, the same way.
- **Secondary:** public alerts sent and at Level 3; alerts not justified (the fire did not reach the zone within the
  level's window); alerts from false fires only; phones warned that the fire did not reach within 3 h; median
  warning lead; cards to fire dispatch and how many were for false fires; the wait for approval in the one-tap arm.

## What the development seeds showed (3000-3003, four valleys, not held out)

Pre-signed rules sent 23 alerts against 28, and 3 against 6 while every fire behind them still smouldered (the 3
came from fires the station wrongly believed spreading). Alerts not justified: 9 and 9. Phones warned in time: 86
against 90; in one valley (3001) 4 phones were reached without a warning under pre-signed rules, because a fire
the drones found smouldering took off after the swarm had landed, so no drone saw it spread; today's rule had warned
those phones on the smoulder. Its card had gone to dispatch at the start of the flight. Median lead 135 against 136
minutes. 20 dispatch cards, 1 for a false fire. One-tap approval waited a median of 110 s per valley and changed
nothing else.

Predictions written now: pre-signed rules send fewer alerts and fewer while every fire still smoulders; they warn
the same phones in most valleys but miss some in valleys where a smouldering fire takes off after the swarm lands;
the median lead changes by under 5 minutes; one-tap approval adds about 2 minutes to each alert.

## Changes made on the development seeds, before this run

- Dispatch cards: a card now also goes out when a fire's fix moves more than 30 m (or twice its error) from where its
  last card put it, once a card has said spreading later ones for that fire do too, and a card is scored against the
  true fire at its position when sent.
- The report's grouping of rows by arm had a bug that stopped it; fixed, and a paired comparison against today's
  rule added.

## Seeds

9000-9019, reserved on 20 October and never flown before. The pre-signed automatic flights of these seeds also feed
the initial-attack model (`results/wildfire_initial_attack/PREREG.md`).

## Commands

    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts/runs.jsonl
