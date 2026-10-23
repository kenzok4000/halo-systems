# The entry's alert design on held-out valleys: plan for the measurement (seeds 9400-9419)

Written before any of seeds 9400-9419 was flown. The code that flies them is the commit that adds this file, run
from a separate checkout of that commit. Made by `tools/wildfire_alerts_sweep.py` with
`--arms today,presigned,presigned-smoulders`.

## Why

On 21 October the entry's pre-signed alert rules were set to count a smouldering fire in the Level 3 (prepare) rule,
as if it took off now, as today's rule does ([docs/WILDFIRE.md](../../docs/WILDFIRE.md)). The demo video's valley
takes fly under that design. Until now it had been compared with today's rule only on 8 development valleys (seeds
3000-3007), where its public alerts were the same as today's rule's, alert for alert, and its cards to fire dispatch
the same as the spreading-only pre-signed rules'. The valley's alert results (seeds 4000-4019 and 7000-7019) were
measured under today's rule. A reviewer pointed out that the design shown had not itself been flown on held-out
seeds. This measurement closes that gap. It changes no code that flies and costs nothing: no language model takes
part in any flight, alert or card.

## Arms (each valley flown once per arm; the flight is the same in all three, only the alerts and cards differ)

- **Today's rule, automatic** (`forecast`/`auto`): the committed rule the valley results were measured under.
- **Pre-signed rules, automatic** (`presigned`/`auto`): spreading fires only, as T3 flew them on seeds 9000-9019.
- **Pre-signed rules, smoulders too, automatic** (`presigned`/`auto` with `smoulder_alerts`): the entry's design.

16 drones on today's single sweep, improved+rule, the legacy station, report_v2 scoring. Simulated phones only.

## Measures

- **Primary: alert for alert.** Each public alert is identified by the time it was sent, its level and its zone.
  The number of valleys in which the entry's design sent exactly the same public alerts as today's rule, and the
  number of alerts sent under one policy only.
- **Co-primary: phones warned before the fire reached them**, and phones the fire reached without a warning (with
  signal), the entry's design minus today's rule, total over valleys with a 95% bootstrap interval over valleys.
- **Secondary:** public alerts sent and at Level 3; alerts sent while every fire behind them still smouldered;
  alerts not justified; phones warned that the fire did not reach within 3 h; median warning lead; cards to fire
  dispatch and how many were for false fires, and in how many valleys the entry's design's card counts equal the
  spreading-only rules'.

## Predictions, written now

- By construction the entry's design should send the same public alerts as today's rule in every valley: in the
  code, `smoulder_alerts` makes the pre-signed Level 3 rule take the same maximum over potential arrivals as the
  forecast rule, and the Level 4 and 5 rules are unchanged (`src/halo_systems/sim/wildfire.py`, `_zone_targets`).
  So the alert-for-alert count should be 20 of 20, every paired difference 0, and the median lead the same. If any
  valley differs, the report says so and the difference is traced before any number is quoted.
- Cards to fire dispatch should equal the spreading-only pre-signed rules' in every valley: the cards do not depend
  on the Level 3 rule.
- The check on development seed 3000 (one valley, before this plan was written) agreed with both predictions.

## Seeds

9400-9419, reserved for this measurement on 23 October and never flown before; the next fresh set starts at 9420.

## Commands

    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --seeds 9400-9419 --arms today,presigned,presigned-smoulders \
      --out results/wildfire_alerts_smoulders/runs.jsonl
    PYTHONPATH=src python3 tools/wildfire_alerts_sweep.py --report --out results/wildfire_alerts_smoulders/runs.jsonl
