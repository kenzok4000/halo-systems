# Valley test engineer: plan for the held-out search (T5b)

Written before any of seeds 9100-9199 was flown. The code is `src/halo_systems/ai/valley_tester.py` and
`tools/wildfire_valley_stress.py` in the commit that adds this file, run from a separate checkout of that commit.

## Question

On 20 October Kenzo approved up to USD 0.50 of new Nemotron calls for the wildfire valley, including Nemotron as a
test engineer. Given the same number of test flights, does Nemotron find conditions under which the valley's
ground station fails more often, of more kinds, or under milder conditions than random search and corner search?
And what do the failures it finds say about the station?

## System under test

Today's flight (improved controller, rule supervisor, one sweep of the survey block), the legacy station, and
Kenzo's chosen alerts: pre-signed trigger rules, sent automatically. No language model takes part in any alert,
card or flight decision; Nemotron only proposes test conditions.

## Test conditions

A test is a valley seed plus nine conditions. `build()` makes the committed valley for the seed and then changes
only what the conditions name, after every random draw, so the drawn day's conditions give exactly the committed
valley (checked in `tests/test_valley_tester.py` and on development seeds 3000 and 3001, whose outcomes match the
T3 development flights).

| Condition | Range | Drawn day | Meaning |
|---|---|---|---|
| drones | 4 to 32 | 16 | drones on today's flight |
| wind_scale | 0.5 to 2 | 1 | the day's wind speed, truth and forecast alike |
| wind_shift_deg | -90 to 90 | 0 | the true wind turns this much from 1 h after take-off; the forecast does not know |
| moisture_scale | 0.6 to 1.4 | 1 | dead fuel moisture, truth and forecast alike (below 1 is drier) |
| forecast_error | 0 to 3 | 1 | the forecast's drawn errors in wind and moisture, multiplied (0 = perfect) |
| smoulder_scale | 0.25 to 2 | 1 | how long each fire smoulders before it spreads (fires spreading at take-off unchanged) |
| patchiness | 0 to 0.8 | 0.3 | unseen patchiness of the fuel, which the station's forecast never sees |
| visibility_m | 500 to 2500 | 2500 | haze: the farthest a camera sees smoke |
| smoke_range_scale | 0.4 to 1 | 1 | how far a small plume is noticed (low-contrast smoke) |

## What counts as a failure

A test fails on any of:

- **late warning**: a phone the fire reached within 3 h, with signal, got no alert before the fire arrived;
- **missed fire**: a fire in the survey block had been spreading for 5 minutes or more by the end of the flight
  (time spreading before take-off counts) and was never located;
- **false alarm**: a public alert rested only on fires that do not exist;
- **needless evacuation**: a Level 4 or 5 alert for a zone no fire comes near (within the danger radius of any of
  its phones) in the 4 hours the truth runs;
- **drone lost**.

Severity, which guides the searchers between failures: 4 per phone warned late, 10 per missed fire, 10 per drone
lost, 5 per false alarm, 3 per needless evacuation alert, 1 per alert the fire did not justify within its level's window,
1 per false fire still standing, and up to 3 as the earliest tenth of warnings drops under 30 minutes of lead.
Window-based "not justified" alerts (the fire reached the zone, but later than the level's window) are not failures
on their own: every development flight had at least one.

## Searchers and budget

- **random**: conditions drawn uniformly from the ranges;
- **corners**: each condition at its lowest or highest value, at random;
- **nemotron**: Nemotron 3 Nano (nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B) with reasoning, temperature 0.7, sees every
  test so far (conditions, what went wrong, severity and the main numbers) and proposes the next batch with a
  reason for each; too few or unreadable proposals (conditions that are not an object naming at least one
  condition with a number) are topped up from random search and counted; a condition a readable proposal leaves
  out stays at the drawn day, and the history lists it;
- **drawn day** (the control): the drawn day's conditions on the same valleys.

Two searches per searcher, 20 tests each in batches of 4 (Nemotron: 5 calls per search, 10 in all). Test i of
search s flies valley seed 9100 + 50 s + i for every searcher, so all four fly the same 40 valleys (9100-9119 and
9150-9169). Every Nemotron reply, and every call that failed, is recorded in
`results/valley_stress/calls_cache.jsonl`, keyed by the request and the search number (the two searches' first
prompts are the same text, and each gets its own call); `--replay` re-runs a recorded search with no key and no
spend.

## Measures

Some valleys fail under the drawn day's own conditions (development seed 3001 warns 4 phones late), so a test on
such a valley "fails" whatever its conditions. A failure counts as **caused by the conditions** only when the same
valley under the drawn day did not go wrong that way.

- **Primary: failures the conditions caused**, tests per searcher (of 40).
- **Co-primary: their distinct kinds**, each labelled by what went wrong and by coarse labels for its conditions
  (`causes()`: few drones, windy, wind shift, dry, bad forecast, early or late take-off, patchy fuel, haze;
  thresholds fixed in the code).
- **Secondary:** the mildest of them (stress level: 0 = every condition at its easy end, 1 = all at the hard end;
  smoulder time has no hard end and is left out); failed tests of any cause and of each kind; phones warned late;
  random top-ups; model spend (the recorded cost of every call behind the result, failed calls included).
  Every failing test, the drawn day's included, is flown twice more on the same valley and conditions: under the
  pre-signed rules and under today's alert rule (any located fire, a smoulder included, can raise a zone to
  Level 3). In both re-flights the alert delivery lags are drawn from a stream of their own, so the station
  locates the same fires and forecasts the same way under either rule and only the alerts differ; the pair tells
  failures of the pre-signed rules from failures of the station. (The searches themselves draw the lags from the
  station's stream, as the committed valley does, so a re-flight can differ slightly from the test as searched;
  the report says how many match.)

## What the development seeds showed

Development seeds 3000-3007, one search of 8 tests per searcher (batches of 4), flown on 20 October:

| Searcher | Failures the conditions caused | Their kinds | Mildest of them (stress) | Failed tests (any cause) |
|---|---|---|---|---|
| random | 4 of 8 | 4 | 0.46 | 6 of 8 |
| corners | 3 of 8 | 3 | 0.38 | 4 of 8 |
| nemotron, first prompt | 0 of 8 | 0 | none | 2 of 8 |
| nemotron, revised prompt | 0 of 8 | 0 | none | 1 of 8 |
| drawn day (control) | n/a | n/a | n/a | 3 of 8 |

The drawn day itself fails on 3 of the 8 valleys (3001 and 3006 warn 4 phones late; 3004 raises a false alarm and a
needless evacuation), which is why the measure counts only failures the drawn day does not have; that measure was
chosen after seeing these flights and before any held-out flight.

Changes made on the development seeds, disclosed here:

- The first prompt asked Nemotron to look for failures "under milder conditions". Its tests were mild and found
  little. The prompt now states the scoring (failures the conditions cause first, new kinds second, then the
  mildest conditions) and says some valleys fail under the drawn day's own conditions. The revised prompt still
  found no failure the conditions caused on the development seeds.
- The reply token ceiling was raised from 8,000 to 12,000 after one reasoning reply used 6,248 tokens. Every
  development reply finished on its own.

An independent code review before any held-out flight (four reviewers, each finding checked by another) led to
these changes, none of which touches the station, the flight or the drawn valley:

- The plan and the prompt said a missed fire "spread for 5 minutes or more during the flight". The scorer counts
  time spreading before take-off too (a 16-drone sweep lasts about 4 minutes, so with 16 drones the written rule
  could never fire). The scorer is unchanged; the plan, the prompt and the report now say what it counts. The development
  searches ran with the old wording.
- A lost drone was counted twice or three times in severity; now once. No drone was lost on the development
  seeds.
- A proposal whose conditions could not be read was flown as the drawn day and counted as the model's; it is now
  topped up from random search and counted.
- Both Nemotron searches' first requests had the same cache key, so the second search would have reused the
  first's reply; the key now includes the search number. Failed calls are now recorded, with their cost, so a
  replay follows the live run and the spend includes them.
- The recheck now flies both rules with delivery lags on their own stream (above). Before, it re-flew only today's
  rule on the station's shared stream, so after the first alert that differed every later forecast was redrawn.

Nemotron's reasons often did not match what went wrong (a test it expected to crash a drone passed; one meant to
cause a false alarm warned phones late instead). The prompt was not tuned further: with one test per valley and
valleys that differ, 8 tests are too few to say whether a better prompt would help, and further tuning would be
tuning on noise. The held-out run uses the revised prompt, with the missed-fire wording corrected, as committed, whatever it
shows.

Development spend: 4 calls, USD 0.0065 (ledger call ids 913d7121b7d7, 6de8bb64cb3f, 9cd6dacbdda8, f3bbe07269b3).

## Spend

Ten calls, each about USD 0.001 to 0.003 at the ceiling price for Nano with reasoning (USD 0.0007 to 0.0027 on
the development seeds), through the shared ledger with the cap set below Kenzo's approval (HALO_BUDGET_USD=8.73). The valley compile used USD 0.047 of the USD 0.50.

## Seeds

9100-9119 and 9150-9169, reserved on 20 October and never flown before; 9120-9149 and 9170-9199 stay unused.
Development: 3000-3007 (each searcher and the drawn day 8 tests; Nemotron twice, once per prompt).

## Commands

    PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher random --search 0 --out results/valley_stress/random_0.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher random --search 1 --out results/valley_stress/random_1.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher corners --search 0 --out results/valley_stress/corners_0.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher corners --search 1 --out results/valley_stress/corners_1.json
    HALO_BUDGET_USD=8.73 PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher nemotron --search 0 \
        --cache results/valley_stress/calls_cache.jsonl --out results/valley_stress/nemotron_0.json
    HALO_BUDGET_USD=8.73 PYTHONPATH=src python3 tools/wildfire_valley_stress.py search --searcher nemotron --search 1 \
        --cache results/valley_stress/calls_cache.jsonl --out results/valley_stress/nemotron_1.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py drawn-day --out results/valley_stress/drawn_day.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py recheck results/valley_stress/{random,corners,nemotron}_?.json \
        results/valley_stress/drawn_day.json --out results/valley_stress/recheck_forecast.json
    PYTHONPATH=src python3 tools/wildfire_valley_stress.py report results/valley_stress/{random,corners,nemotron}_?.json \
        results/valley_stress/drawn_day.json --recheck results/valley_stress/recheck_forecast.json \
        --out results/valley_stress/REPORT.md
