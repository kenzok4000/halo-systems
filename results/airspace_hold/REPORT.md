# Aircraft inbound: an airspace hold over the wildfire valley

One development seed (3000), 16 drones, today's controller and the rule supervisor (improved+rule), flown twice: without a hold, and with the operator's "aircraft inbound" hold. This is a demonstration of the control on one seed, not a measurement: other seeds, swarm sizes and hold times will give other numbers.

What the hold does: when crewed firefighting aircraft (air tankers, helicopters, a lead plane) come into the valley, the operator orders a hold. The order passes the same safety validator as every directive and is logged like one. Every flying drone stops where it is and descends to the hold altitude (10 m above the surface the drones follow, tree crowns included, unless the operator gives a lower one), clear of the aircraft's working height. The ground station, smoke watch and alerts keep running. On resume the swarm climbs back to its working altitude and carries on the sweep from where it stopped.

## Timeline

* Planned sweep: 1139 m at the nominal 6 m/s, about 3.2 min of flying.
* Hold ordered at 90 s, resume ordered at 390 s (5 min).
* Why then: the 16-drone sweep is planned as about 3.2 min of flying (1139 m at the nominal 6 m/s), so an order 10 minutes in would come after the sweep; the hold was ordered at 90 s instead, halfway through the planned sweep, and resumed 5 minutes later as planned.
* Where the swarm was: at 90 s its leader was 474 m along the 1139 m route (42%), flying at 1.8 m/s; the flight without a hold took 247 s to sweep the whole route.
* Time limit for both flights: 697 s (the scenario's 397 s plus the hold), so the held flight can finish its sweep; each flight still ends 5 s after its sweep is complete.

## The orders in the validator's log

```
{"t": 90.0, "source": "operator", "kind": "airspace", "order": {"t": 90.0, "op": "hold"}, "action": "accepted", "violations": [], "altitude_m": 10.0}
{"t": 390.0, "source": "operator", "kind": "airspace", "order": {"t": 390.0, "op": "resume"}, "action": "accepted", "violations": [], "altitude_m": null}
```

## The hold

* Held from 90 s to 390 s at 10 m above the followed surface, ordered by the operator; 16 drones flying when it began.
* Every flying drone was within 1.5 m of the hold altitude and slower than 0.5 m/s 6.1 s after the order.
* Charge used while holding: 188.0 Wh by the flying drones, about 11.75 Wh each (28.7% of one battery).
* Drones retired at the battery reserve during the hold: 0.

## With and without the hold

| | without hold | with hold |
|---|---|---|
| sweep complete | yes, at 247 s | yes, at 552 s |
| fires located (of 5) | 5 | 5 |
| by smoke / by thermal camera | 4 / 1 | 4 / 1 |
| hotspots seen by the cameras | 3 | 3 |
| area covered | 99.6% | 99.7% |
| drones retired at the reserve | 0 | 0 |
| mean battery left (flying drones) | 76.3% | 47.2% |
| energy used | 156 Wh | 346 Wh |
| collisions, obstacle and ground strikes | 0, 0, 0 | 0, 0, 0 |

The sweep finished 305.0 s later with the hold.

The held swarm stopped 474 m along the route and moved again at 396.4 s, 6.4 s after the resume order, once it had climbed back. 50 m past that point (524 m) it was 306.5 s behind the flight without a hold: the 300 s held plus 6.5 s for braking, climbing back and speeding up again (the descent to the hold altitude happened within the held time).

Further along the route it was 306.5 s behind at 600 m, 306.3 s behind at 700 m, 306.7 s behind at 800 m, 305.2 s behind at 900 m, 305.0 s behind at 1000 m, 305.1 s behind at 1100 m.
By the end of the sweep the gap was 305.0 s: the rest of the sweep was flown about 5 minutes later than in the flight without a hold, and its pace was not exactly the same.

Fires per flight (shelter: located at, by):

| fire | without hold | with hold |
|---|---|---|
| 1 (open) | 0 s, smoke | 0 s, smoke |
| 2 (canopy) | 218 s, thermal | 523 s, thermal |
| 3 (overhang) | 52 s, smoke | 52 s, smoke |
| 4 (open) | 48 s, smoke | 48 s, smoke |
| 5 (open) | 0 s, smoke | 0 s, smoke |

The held flight runs longer, so its station has more time to see smoke from fires that start to spread later; a difference in fires located between the two flights reflects that as well as the hold itself.

Made by `tools/airspace_hold_demo.py` (wall time 74 s and 159 s).
