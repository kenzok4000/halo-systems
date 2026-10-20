# Initial attack: plan for the held-out measurement (T4)

Written before any of seeds 9000-9039 was flown. The model, every number in it and the report it writes are in
`tools/wildfire_initial_attack.py` (its docstring gives each source); the code that flies and models the seeds is
the commit that adds this file, run from a separate checkout of that commit.

## Question

When a fire is reported only by today's methods (satellites, cameras as deployed, public calls), how often does
the first crew get there while it is still smouldering, and how many fires escape initial attack? How does that
change when the drones' cards to fire dispatch are added?

## Flights

40 valleys, each flown once with 16 drones on today's flight (improved+rule), pre-signed alert rules, automatic:
seeds 9000-9019 are the pre-signed, automatic arm of the T3 alert sweep (`results/wildfire_alerts/`), and seeds
9020-9039 are flown by `wildfire_initial_attack.py fly`. The flight is the same in both; only the seeds differ.

## Measures

Per true fire (five per valley, 200 fires), with today's reports and with the drones' cards added (the earlier of
the two). The crew's response time is drawn once per fire and used in both arms.

- **Primary: the first crew on scene while the fire still smoulders**, today against with the drones, paired by
  valley, with a 95% bootstrap interval over valleys (10,000 resamples). This rests only on report times and the
  response time, not on line-building rates.
- **Co-primary: fires that escape initial attack** under the perimeter race with one Type 3 engine, the same way.
- **Secondary:** the fire's size when the crew arrives (median); fires contained and their size then; escapes when
  the first crews build line at 2, 4 and 8 times one engine's rate (more resources arriving together); the
  sensitivity rows (response fixed at 15, 30, 60 and 120 minutes, 60 being the value Reimer et al. 2019 used for
  lightning fires; a Type 6 engine, a helitack crew and a 20-person crew; the rate halved; escape at 2 ha and at
  20 ha; today with an ideal camera that sees every fire). Fires overrun by another fire's front before they spread
  join that fire, and fires that never spread in the truth are left out; the report gives both counts.

## What the development seeds showed (3000-3003, 20 fires, not held out)

The first crew reached a smouldering fire in 10 of 20 fires with the drones' cards and in none with today's
reports (today's methods only see a fire once it spreads). The fire was 0.2 ha (median) when the crew arrived
with today's reports, and 0.005 ha with the drones'. With one Type 3 engine every fire found after it started
spreading outran the crew: 18 of 20 escaped with today's reports (2 were overrun by a neighbour's front first) and
9 with the drones' (the ones the crew reached while smouldering were held). With line built at 2, 4 and 8 times
one engine's rate, 15, 8 and 6 escaped with today's reports and 6, 2 and 1 with the drones'. With the response
fixed at 60 minutes, 18 and 12. The valley is a dry, windy day by design (its last weather period,
from 4 h, is its driest and windiest), so its fires spread faster than the average fire behind CAL FIRE's 95%
initial-attack figure; the write-up says so.

Predictions written now: with the drones, the crew reaches a smouldering fire in 35 to 60% of fires, and with
today's reports in under 5%. With one engine, escapes with today's reports stay above 80% and the drones prevent
between a third and a half of them. With stronger crews both arms escape less and the gap narrows.

## Changes made on the development seeds, before this run

An independent review of the model on the dev seeds (four reviewers, each finding checked by two more) led to the
first four changes below. No reviewer ran any of the held-out seeds.

- The first dev run printed the median 12 h area of escaped fires under "median area when contained"; the report
  now gives the median size of the fires actually contained, with their count.
- A fire overrun by another fire's front before it spreads was counted as an escape of its own (and, when the
  drones had carded it, as one prevented); it now joins the fire that overran it.
- A fire lit on rock (the overhang fire can be) had no line rate and stopped the model; it now takes the fuel it
  burns in its first hour of spread, and a fire that never spreads in the truth is left out and counted.
- The docstring misquoted Reimer et al. 2019 (their 30 minutes is for human-caused fires near roads; lightning
  fires got 60) and called the truth's weather after 4 h a forecast (it is the true weather's last period);
  both are corrected. A rerun of `fly` now resumes instead of flying every seed again.
- The perimeter now fills unburnt islands inside the fire before counting its edge (a crew lines the outer edge).
- The crew-on-scene measure and the 2, 4 and 8 times rows were added after the dev run showed that with one engine
  only fires reached while smouldering were held.
- Dispatch cards: a card now also goes out when a fire's fix moves more than 30 m (or twice its error) from where
  its last card put it, and a card is scored against the true fire at its position when sent. On the four dev
  valleys this changed no card time.

## Seeds

9000-9039, reserved on 20 October and never flown before. Development: 3000-3003.

## Commands

    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9000-9019 --out results/wildfire_alerts/runs.jsonl
    PYTHONPATH=src python3 tools/wildfire_initial_attack.py fly --seeds 9020-9039 \
        --out results/wildfire_initial_attack/flights.jsonl
    PYTHONPATH=src python3 tools/wildfire_initial_attack.py model \
        --flights results/wildfire_alerts/runs.jsonl,results/wildfire_initial_attack/flights.jsonl \
        --out results/wildfire_initial_attack
