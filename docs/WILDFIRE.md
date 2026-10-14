# The wildfire valley

The morning after dry lightning, a few fires smoulder in a mountain valley. Some are in the open, one is under
tree crowns, one is under a rock overhang where no camera looking down can see it, and one is across the valley.
A swarm of scout drones sweeps the valley floor. The ground station finds the fires from the drones' thermal
images and smoke sightings, forecasts how far each will spread and how soon, briefs the fire crews, and drafts
evacuation alerts for the zones in the fire's path. Alerts go out automatically by fixed rules, at levels
modelled on Japan's evacuation scale, once each passes a check; an operator can cancel them or give the
all-clear. They go to simulated phones only.

This page describes the scenario (`wildfire_valley` in `src/halo_systems/eval/scenarios.py`), what the
simulation treats as true, what the station is allowed to know, how each result is scored, and every assumption
with its source. Results are in [results/wildfire_valley/REPORT.md](../results/wildfire_valley/REPORT.md).
Nothing in this scenario calls a language model, so it costs nothing to run.

![One flight: the true spread against the forecast, the fires found and the phones alerted](../results/wildfire_valley/map.png)

## What is simulated as true

| Part | Model | Where |
|---|---|---|
| Ground | A valley 1,300 x 1,000 m on a 5 m grid: a floor with a knoll, ridges whose crests stand 130 to 195 m above it on each side, rock on steep high ground. The survey block is 500 x 300 m on the floor | `sim/terrain.py` |
| Land cover and fuel | Scrub (Anderson fuel model 6, dormant brush), grass (model 1), forest (model 10, timber litter and understory) and rock (no fuel). Forest crowns 10 to 20 m tall, scrub 1.5 m | `sim/terrain.py`, `sim/fire.py` |
| Fire spread | Rothermel's surface fire model with Albini's revisions, wind and slope added as vectors, an elliptical fire shape, and arrival times by minimum travel time on a 16-neighbour grid. A patchy multiplier on the spread rate (log-normal, sigma 0.3, about 40 m patches) makes each hillside burn differently; the station never sees it | `sim/fire.py`, `sim/wildfire.py` |
| Weather | Hourly periods from six hours before take-off. Light morning wind (1.5 to 3 m/s) that turns up the valley (within 70 degrees) and freshens by 0.4 to 0.9 m/s an hour after take-off, veering up to 8 degrees an hour. Fine dead fuel moisture falls from 10% to a floor of 5% | `eval/scenarios.py` |
| Fires | Five per flight: one already spreading in open scrub (lit 10 to 35 minutes before take-off), one under tree crowns (60 to 80% hidden from above), one under a rock overhang (fully hidden), one smouldering in the open, and one across the valley outside the survey block. Smouldering fires cover 3 to 6 m2 until they take off, at seeded times from 20 minutes before take-off to 4 hours after | `eval/scenarios.py` |
| Smoke | A bent-over plume: smoke rises at 1.5 m/s while the wind carries it, to a top that grows with the burning area (12 m for 10 m2, up to 150 m). Crowns hide the plume below 60% of their height; the overhang hides it below 4.5 m. A particulate (PM2.5) plume spreads by the Gaussian plume equation | `sim/smoke.py` |
| Obstacles | A 45 m radio mast on the knoll, three dead snags standing 18 to 26 m above the crowns, and the rock overhang | `eval/scenarios.py` |
| People | 63 phones: 26 homes down the valley, 14 at a campground up it, 8 hikers on a ridge trail, 10 drivers on the valley road and 5 at a ranch. 5% have no signal | `eval/scenarios.py` |

## What the drones sense

The airframe is the one in [hardware/](../hardware/README.md): a 7-inch quadcopter with a radiometric thermal
camera looking down, an RGB camera looking ahead and a PM2.5 sensor. The swarm flies line abreast 14 m apart at
6 m/s, 20 m above the tree tops (it follows the highest crown within 15 m), so the same sweep covers the block in
fewer lanes the more drones there are.

- **Thermal camera, looking down (45 degree field of view).** A fire is found after 0.5 s in view, scaled by the
  share of it visible from above: a fire under crowns that are 70% closed needs 1.7 s, and one with less than 15%
  visible (the overhang) is never found this way. Position comes from the finding drone's own navigation, and one
  image shows the hot ground inside its footprint (20 to 35 m across), not a large fire's whole extent.
- **The same camera maps burning ground.** Once a second, every 10 m cell inside a drone's footprint that is
  burning or burnt is marked on the station's map, placed by the drone's position estimate. Under tree crowns a
  look sees the fire 40% of the time. Mapping is how the station learns a spreading fire's real shape and size.
- **RGB camera, looking ahead (90 degree field of view).** A plume point counts as visible when it is above
  whatever hides the fire, in the field of view, within the 2.5 km haze limit and not behind terrain. A plume of
  burning area A at range r is noticed at a rate ln 2 (R50 / r)^3 per second, with R50 = 300 m x sqrt(A / 10 m2):
  a smouldering patch is seen within a second half the time at about 200 m, a spreading fire across the whole
  valley. A sighting reports the bearing to the lowest smoke the drone can see (2 degree error) and the
  elevation angles of that point and of the plume top (1 degree error).
- **PM2.5 sensor.** Inside the plume, above 35 ug/m3, the drone reports that the source is upwind.

## What the station knows and does

The station sees only the drones' reports, their own position estimates, their wind estimate (the mean of the
swarm's last 5 s of wind measurements; before it has a second of them, the weather forecast) and a weather
forecast that is right in shape and wrong in detail (a speed bias of about 20%, a direction bias of about 12
degrees, hour-to-hour errors and a fuel moisture error).

1. **Locate.** A thermal find is a fire at that position, and mapped burning ground more than 60 m from every
   located fire is a new one. Smoke bearings are crossed in pairs; a tight cluster of crossings is only a
   candidate, because the bearings of two different plumes also cross. A candidate becomes a fire when a
   least-squares fix from at least six bearings by three or more drones, spread over at least 30 degrees, agrees
   to within 1.5 bearing errors and is still placed to within 40 m when any one drone's bearings are left out.
   The fix is then moved upwind by the plume's drift (wind x height / rise speed), with the height from the
   elevation angles. A fix within 60 m of a known fire (its position, its mapped ground, its estimated radius, or
   the downwind line of its plume) is that fire. A thermal find upgrades a smoke location it lands on, and
   located fires that turn out to stand on one patch of mapped ground are merged into the first one found.
2. **Size.** Mapped ground and the hotspot image measure a fire directly, so they set its size when there are
   any. Only otherwise does the plume size it: each later sighting that points along one located fire's plume,
   and could not be any other located fire's plume at any size, records the height of the plume top it saw,
   solved for the drift, with an uncertainty from its range. The median of the latest well-measured tops (known
   to 12 m or better, from at least three drones) gives the burning area by the plume-top law. Area grows as the
   top to the power 2.9 and the tallest plumes look alike, so a top above 90% of the highest plume gives only a
   lower bound. A fire estimated under 50 m2 is treated as smouldering.
3. **Forecast.** An ensemble of 16 spread runs on a 10 m grid, 3 hours ahead, from the mapped burning ground and
   a disc of each located fire's estimated area. Each member perturbs the wind direction (10 degrees), wind speed
   (15%), fuel moisture (1 point), spread rate (30%, log-normal) and each located fire's position (its location
   error). The first period uses the drones' wind estimate, later periods the
   weather forecast. It runs twice: for the fires believed to be spreading, and for every located fire as if the
   smouldering ones took off now. It reruns every 2 minutes, and 30 seconds after news (a new fire, one taking off).
4. **Brief.** For each fire: how it was found, whether it is spreading, how fast its head runs and which way, its
   size in one and two hours, and which named places it reaches and when.
5. **Alert.** The valley is divided into twelve fixed evacuation zones (south slopes, valley floor and north
   slopes, four across), as US counties divide theirs and Japanese towns issue evacuation information by district.
   After every forecast, fixed rules give each zone a level in the spirit of Japan's five-level evacuation scale:

   | Level | Message | Rule: the forecast gives fire within 50 m of a phone in the zone |
   |---|---|---|
   | 3, prepare to leave | Those who need longer leave now; everyone else gets ready | at 30% or more within 3 hours, counting smouldering fires as if they took off now |
   | 4, evacuate now | Everyone leaves now | at 30% or more within 60 minutes, from the fires believed to be spreading |
   | 5, emergency | Fire is here; shelter if you cannot get out safely | at 50% or more within 15 minutes |

   Levels 1 and 2 (fire weather, fire in the valley) are on the dashboard only. A zone's level only rises
   automatically. Each alert passes `AlertCheck`: a message of 1 to 360 characters (the limit for Wireless
   Emergency Alerts on 4G and later networks, and for EU-Alert); a hazard, area, action, expiry and authority;
   a channel marked as simulated; a zone polygon under 5,000 ha; 1 to 5,000 recipients; an expiry of 1 to 12
   hours; a Level 4 or 5 that cites the current forecast at its threshold; a level above the zone's current
   one; and no duplicate in the last 10 minutes. A checked alert goes out at once, by cell broadcast to every
   phone in the zone (simulated phones only), arriving 5 to 30 s later. No language model takes part. An
   operator can cancel an alert (a false alarm: the zone's phones are told, and the rules hold off that zone
   for 30 minutes unless it reaches Level 5) or give a zone the all-clear; both messages pass a check too. A
   manual mode, where each alert waits for a tap, is kept for drills.

## How results are scored

All scores compare the station's picture with the truth, which the station never sees.

| Score | Definition |
|---|---|
| Fires found | The station placed a fire within 60 m of a true fire's burning ground (or its ignition point, while it smoulders) at that moment |
| False fires | Locations more than 60 m from every fire's burning ground, counting those later merged into another fire |
| Smoke fix error | Distance from a smoke-only location to the fire's burning ground, when first located |
| Sorensen index, 1 h and 3 h | 2 x overlap / (forecast area + true area), counting only ground that burns after the forecast is made (ground already burnt would flatter it); 1 is perfect. "Located fires" uses the true spread of the fires the station knew were spreading (located as spreading, or with mapped ground); "all fires" uses every fire |
| Phone arrival error | Forecast median arrival minus true arrival, at phones the fire reaches within 3 hours of the forecast |
| Brier score | Mean squared difference between the forecast probability of fire within 60 minutes at each phone and what happened |
| Reached | Fire within 50 m of the phone within 3 hours of take-off |
| Warned in time | An alert at Level 3 or above was delivered before the fire arrived |
| Lead time | True arrival minus first delivery |
| Evacuated, not reached | Phones told to leave (Level 4 or 5) that the fire did not reach within 3 hours |

The last forecast of each flight is scored. A smaller swarm finishes its sweep later, so its last forecast is made
later, with more of the fire mapped; the report gives the time. Intervals are 95% bootstrap intervals over seeds.

**Which seeds were used for what.** Seeds 2000 to 2011 were flown while the scenario and the station were built.
A first sweep on seeds 3000 to 3019 then found that more drones gave a worse 1-hour forecast (Sorensen 0.57 with
one drone, 0.41 with sixteen) and an early bias of 22 minutes. The cause was sizing: with more drones, more fires
were first located by smoke and sized from the plume top, which over-estimated the largest fires by up to 23
times and made some smouldering fires look like spreading ones, and the single-image hotspot area was not
limited to the camera's footprint. The station was changed
(thermal mapping, sizing from direct measurements first, a three-drone rule for plume sizing, merging of
duplicate locations, the swarm's own wind estimate in place of the truth before it had one) and the
found-fire score was corrected to measure from the burning ground rather than the ignition point. Seeds 3000 to
3019 then became development seeds too. The results in the report are from seeds 4000 to 4019, flown once with
the final code.

## Assumptions and sources

| Assumption | Value | Source or status |
|---|---|---|
| Surface spread rate | Rothermel (1972) with Albini (1976) | Rothermel, USDA Forest Service Research Paper INT-115; Albini, General Technical Report INT-30 |
| Fuel models | Anderson's 13 standard models (1, 2, 3, 4, 5, 6, 8, 9, 10 implemented) | Anderson (1982), General Technical Report INT-122. Reproduced within 20% of his table, the valley's three within 8% (`fire.anderson_check`) |
| Fire shape | Length-to-breadth ratio from the effective wind | Anderson (1983), Research Paper INT-305 |
| Midflame wind | 0.28 to 0.55 of the 20 ft wind by fuel model | Albini and Baughman (1979), Research Paper INT-221 |
| Spread over the grid | Minimum travel time | Finney (2002), Canadian Journal of Forest Research 32: 1420-1424 |
| Particulate dispersion | Gaussian plume, Pasquill-Gifford class D | Briggs' rural dispersion fits (1973) |
| PM2.5 trigger | 35 ug/m3 | US EPA 24-hour PM2.5 standard |
| Alert length | 360 characters | FCC Report and Order 16-127 (2016) for Wireless Emergency Alerts; ETSI TS 102 900 for EU-Alert |
| Smoke detection range, plume rise and top, bearing and elevation errors, emission rate | As above | Stated assumptions, to be calibrated against real footage and sensor logs |
| Alert levels | Five levels, 3 to 5 sent to phones | Modelled on Japan's evacuation alert levels (Cabinet Office, revised May 2021); thresholds are stated assumptions |
| Weather forecast errors, patchiness of spread, delivery delay, phones without signal | As above | Stated assumptions |

## What is not modelled

- Crown fire, spotting (embers starting new fires ahead of the front) and the fire's effect on its own wind.
  Each makes real fires faster and less predictable than this forecast assumes.
- Image recognition. Smoke and heat detection is a probability model of a detector, not a neural network run
  on rendered images.
- The build's navigation error. The simulator's GNSS error is about a sixth of the scout's receiver
  ([hardware/README.md](../hardware/README.md) lists this and the other gaps between the build and the simulator).
- Re-tasking. The swarm flies its sweep and lands; it does not go back to watch a fire or check a doubtful fix.
- Flying over flames. The scouts sweep 20 m above the canopy straight over a spreading fire, through its heat
  and smoke; a real crew would climb or route around it.
- How people respond. An alert counts as delivered, not as acted on; there is no traffic on the valley road.
