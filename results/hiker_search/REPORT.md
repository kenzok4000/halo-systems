# Missing hiker: found against swarm size

Seeds 9200-9219 (20 searches, held out: never flown before this run), each flown with 1, 4, 16 drones: today's controller and rule supervisor, line abreast at 20 m with thermal cameras looking down, one sweep of a 400 x 250 m meadow and forest edge. The person (a heat signature 0.8 m across) lies in the open half the time and under 30 to 80% tree crown otherwise; the same seed puts the person in the same place whatever the swarm size. A find needs half a second of the person in some camera's view, longer when partly hidden. Simulated; no model in the loop.

The rule supervisor may speed a lone drone up to save charge: when the rest of the sweep at the planned 6 m/s would need more than 90% of the usable charge, it orders the cruise speed that costs least per kilometre, which can be faster. A faster pass gives a person under tree cover less time in view. The secondary row flies one drone on the same seeds with the planned speed held (today's controller, no supervisor), to tell the number of lanes apart from the speed-up.

Time to find counts every flight, a miss as never found; a median or 90% that lands on a miss is "not reached". Mean speed is the distance along the sweep path over the time to the sweep's end (or to the flight's end, if the sweep was not finished); fastest ordered is the highest speed any plan ordered. A flight ends 5 s after the sweep, or when the last drone retires at its battery reserve.

| Drones | Found | In the open | Under trees | Time to find (median) | 90% found within | Sweeps completed | Mean speed (median) | Fastest ordered | Flight took (median) | Drones retired | Drones lost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16 of 20 | 9 of 9 | 7 of 11 | 7.3 min | not reached | 20 of 20 | 8.7 m/s | 12.0 m/s | 12.6 min | 0 | 0 |
| 4 | 20 of 20 | 9 of 9 | 11 of 11 | 3.9 min | 4.6 min | 20 of 20 | 5.9 m/s | 6.0 m/s | 6.2 min | 0 | 0 |
| 16 | 20 of 20 | 9 of 9 | 11 of 11 | 0.8 min | 2.4 min | 20 of 20 | 5.0 m/s | 6.0 m/s | 3.1 min | 0 | 0 |
| 1, speed held (no supervisor) | 19 of 20 | 9 of 9 | 10 of 11 | 11.2 min | 14.9 min | 0 of 20 | 5.9 m/s | 6.0 m/s | 16.2 min | 20 | 0 |

Row "1, speed held (no supervisor)": the sweep was not finished in 20 of 20 flights (drones retired at the battery reserve in those flights: 20); the median flight covered 87.8% of the area.

## What it shows (written after the run)

The plan (`PREREG.md`) set no threshold; these notes read the table above.

- **Four and sixteen drones found everyone.** With 4 and with 16 drones the person was found in all 20 searches,
  including the 11 under 30 to 80% tree crown: a median 3.9 minutes in with 4 drones (90% within 4.6) and 0.8
  minutes with 16 (90% within 2.4). The meadow took one sweep of 6.2 and 3.1 minutes.
- **One drone has to choose between speed and coverage.** With the rule supervisor, the lone drone sped up to
  12 m/s to finish the sweep on one charge, and missed 4 of the 11 people under trees, all under heavy crown (69%
  to 77% hidden). With the planned 6 m/s held, it found 19 of 20 but ran out of usable charge before finishing in
  every search (87.8% of the area covered, median): a person in the part it never reached would not be found.
- **The development seeds showed the same pattern** (2000-2005: one drone 4 of 6 with the supervisor, 6 of 6 with
  the speed held, 4 and 16 drones 6 of 6).
- No drones lost and no collisions.

`film.json` holds the Mission tab's hiker request ("Search the 400 by 250 m meadow for a missing hiker with 12
drones line abreast, 8 m apart, at 30 m.") flown on seeds 0-9 to choose a seed for filming; those seeds are not
part of this measurement. Seed 3 puts the person under 63% tree crown, found 34 s into the flight by drone 8.
