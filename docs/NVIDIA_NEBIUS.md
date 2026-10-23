# NVIDIA Nemotron on Nebius Token Factory

Halo Systems uses NVIDIA Nemotron models, served by Nebius Token Factory's OpenAI-compatible API, in five roles. Each role has a conventional baseline that gets the same inputs, so every claim about the model is a measured comparison.

| Role | Input | Output | Baseline |
|---|---|---|---|
| Supervisor | Status report every 5 s of simulated time | Directive (formation, spacing, speed, altitude, safety margin) | Rule supervisor; fixed plan |
| Mission compiler | Operator's plain-language request | Mission plan JSON, or a refusal for non-civilian use | Regex and keyword parser |
| Valley compiler | Operator's valley job in plain words | A patrol spec (drones, areas, patrol length, which strikes to check), or a refusal | Hand-written rules |
| Test engineer | Every stress test so far, with outcomes, in the formation scenarios or the wildfire valley | The next batch of tests, each with a reason | Random search; corner search; cross-entropy method (formation scenarios only) |
| Strike picker (wildfire valley, optional check team) | A table, built by code, of the night's lightning strikes the team can still reach and finish before the patrol ends | The next strike to check, with a one-sentence reason, or none to stop | Hand-written triage rule; nearest first |

The strike picker works only with an optional check team of 4 drones, flown by `tools/wildfire_strike_check_sweep.py` (or the `checks=` option of the Python API); `halo run`, the dashboard, typed valley jobs and the headline results never fly a team. On 20 held-out valleys at 8 drones the swarm found 96% of fires with Nemotron picking and 96% with the triage rule: no measurable difference (+0.00 per flight, -0.20 to +0.20) ([strike checks](../results/strike_checks/REPORT.md)).

Nebius services used: Token Factory only, through its OpenAI-compatible chat completions API at `https://api.tokenfactory.nebius.com/v1`. The simulations are numpy on an ordinary CPU and ran outside Nebius.

| Role | Models | Reasoning | Temperature | Token limit | Time limit | Prompt |
|---|---|---|---|---|---|---|
| Supervisor | Nemotron 3 Nano (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`); Super (`nvidia/nemotron-3-super-120b-a12b`) as a comparison | Off | 0.2 | 1,200 | 15 s per call, 8 s per attempt | `ai/prompts.py` |
| Mission compiler | Nano, reasoning off and on; Super | Off, or on in one Nano run | 0 | 1,500; 6,000 with reasoning | 60 s per attempt | `ai/mission.py` |
| Valley compiler | Nano, reasoning on and off | On; off as a comparison | 0 | 800; 6,000 with reasoning | 90 s per attempt | `ai/valley.py` |
| Test engineer | Nano, reasoning off and on | Off, or on in one run | 0.7 | 2,500; 8,000 with reasoning | 180 s per attempt | `ai/tester.py` |
| Test engineer, wildfire valley | Nano | On | 0.7 | 12,000 | 180 s per attempt | `ai/valley_tester.py` |
| Strike picker | Nano | On | 0 | 6,000 | 30 s per attempt | `supervisor/strikes.py` |

Our feedback on Token Factory and the models, with the numbers behind it, is in [DEVPOST.md](DEVPOST.md#feedback-on-nebius-token-factory-and-nvidia-models).

## Why the model is not in the control loop

The drones need a new thrust command every 20 ms. A hosted model answers in hundreds of milliseconds to seconds, and sometimes not at all. So the model works only where a decision can wait a few seconds, and where judgement over many signals helps: wind, near misses, radio health, battery, and the corridor ahead. The engine applies each answer only after its measured latency has passed in simulated time, so a slow answer has a real cost in the benchmark.

The strike picker is asked for the next strike 40 s before the check team's current look ends, so its thinking overlaps the look. On the held-out flights the median call took 10.8 s, 9 in 10 took under 21 s, and the slowest took 156 s. While a call is out the team keeps patrolling its square. A pick that comes back too late to finish before the patrol ends is dropped. That happened once, to the 156 s call in valley 9311, and that flight made no further check.

Nothing the model says reaches the motors directly. Every directive passes the safety validator (schema, envelope clamps, a spacing floor derived from body size, safety margin and navigation error, and a rate limit), and the 50 Hz safety filter still acts to keep clearance whatever the directive. It is not a guarantee: the stress search found conditions where the swarm fails with the filter on.

Every strike pick passes the validator too (`check_strike`: a listed strike not yet checked, a team drone flying, no airspace hold), and code, not the model, flies the team. No picker sees which strike started a fire. No team flight crashed or logged a near miss, but the closest approach between two drones in the worst team flight was 5.1 to 5.4 m with the rule or Nemotron, against 10.9 to 11.0 m with no team; where the close passes come from was not traced.

## Reliability

`ai/client.py` uses only the standard library:

* **Credentials** come from the environment only (`NEBIUS_API_KEY`). The key is never logged, printed or written to disk, and log lines are scrubbed of the key and of bearer tokens. The key is sent only over https and never follows a redirect.
* **Timeouts and deadlines.** Every request has a timeout, and a supervisor call has a deadline for the whole call, retries and waits included: 15 s with reasoning off (8 s per attempt) and 60 s with it on (one attempt). After that the rules decide that step. The values come from the first tuning round's call log (`results/tuning/round1/calls.jsonl.gz`): most of Super's slow first attempts were rescued by a quick retry (41 of 56 second attempts answered in about 2.5 s), and an answer much later than 15 s describes a swarm that has flown on 75 m.
* **Retries** use exponential backoff with jitter on network errors and HTTP 408, 409, 425, 429, 500, 502, 503 and 504. `Retry-After` is honoured, up to 20 s. Authentication errors are not retried.
* **Rate limiting** uses a client-side token bucket of 2 requests per second (bursts of 4) for each client. Benchmark workers each have their own client, so 4 workers can send up to 8 requests per second between them.
* **JSON mode** (`response_format: {"type": "json_object"}`) is used when the model supports it, with an automatic fallback when it does not. It asks for valid JSON, not for a schema, so the fields are checked after parsing.
* **Responses are validated**: HTTP status, JSON shape, and a non-empty message. Replies are then parsed defensively (reasoning tags and code fences are stripped) and checked field by field.
* **Graceful failure**: the supervisor falls back to the rule supervisor for that step. In the dashboard, a failed mission-compiler call falls back to the regex parser. The test engineer tops up with random tests. Every fallback is counted. The strike picker has no fallback: a call that fails, or a reply that names no offered strike, is not replaced by the rule. The team keeps patrolling its square, the model is asked again a circuit later, and the reply is counted as unusable (none of the 77 held-out calls was).
* **Structured logging**: one JSON line per call with latency, attempts, tokens, estimated cost, request id, finish reason and outcome.
* **One request in flight.** The supervisor never has two calls outstanding, so answers cannot arrive out of order.
* **Shared files are append-safe.** Benchmark workers share the call cache, the call log and the spend ledger. Each append takes a file lock and is written in one go, and a reader skips a broken line instead of stopping.

## Reasoning on or off

Nemotron 3 models reason before they answer unless told not to. The reasoning comes back in `message.reasoning` and counts against `max_tokens`; if it uses them all, `content` is empty and `finish_reason` is `length`. Halo Systems sends `chat_template_kwargs: {"enable_thinking": false}` unless a role asks for reasoning (`nemotron-think:<model>` in a config), in which case `max_tokens` is at least 4096. A reply cut off at the limit is not retried, because the same request would be cut off again.

With reasoning off, Nemotron 3 Nano answered in about 1 s but, before the status report spelled out its corridor and energy checks in words, it ignored them: it kept a grid formation that the report said did not fit. With reasoning on, it used those checks, but answers took several seconds each, the slowest over 100 s before the 60 s deadline was added, and in the last tuning round it completed only 3 of 5 forest flights. Once the report spelled the checks out, reasoning off did as well or better in eight of nine scenarios in that round. The exception was degraded radio, where 12 reasoning-off calls timed out within two and a half minutes, a pattern that suggests a short slowdown at the provider, and the rules took those steps. So the supervisor runs with reasoning off. The mission compiler is different: a plan is a one-off answer, and reasoning on took Nano from 18 to 20 of 20 held-out requests fully right, at a median of 12 s instead of 5 s. The valley compiler likewise: reasoning on took Nano from 39 to 47 of 48 held-out valley jobs right, at a median of 4.7 s instead of 1.1 s ([valley_eval](../results/valley_eval/REPORT.md)). The strike picker also runs with reasoning on, a median of 10.8 s a call inside its 40 s lookahead; reasoning off was not measured for it ([strike checks](../results/strike_checks/REPORT.md)). The tuning rounds are in `results/tuning/`.

## Spend guard

`ai/budget.py` keeps a ledger of every call's tokens and estimated cost in a JSON-lines file (`HALO_LEDGER`), shared by all processes and sessions that use the same file. Before each request, the guard adds a pessimistic estimate of that request to the total: about three characters per token for the prompt, plus the full `max_tokens` for the reply. If the total would pass the cap (`HALO_BUDGET_USD`, default 18), the call is refused. A benchmark then stops instead of running up a bill. The check and the request are not one atomic step across processes, so parallel workers can pass the cap by up to about one worst-case request each.

Over the whole project, up to 23 October, the ledger recorded 13,563 entries, an estimated USD 8.57 (USD 8.5652) at the price ceilings, within the USD 8.74 the entrant approved. When first read, on 14 October, it held about 13,100 requests (13,066 logged by the client and 35 from the first live check, entered by hand), 22.3 million prompt tokens and 3.6 million completion tokens, an estimated $8.23 ([evidence/budget.txt](../evidence/budget.txt)). Since then the project has used about USD 0.33 more: USD 0.047 for the valley compiler, USD 0.032 for the valley test engineer and about USD 0.25 for the strike picker (USD 0.17 on the development valleys, USD 0.086 on the held-out ones). The ledger counts every attempt, retries included, and counts requests that timed out at their worst case.

* Some requests are billed even though they give no usable answer. A reply that turns out empty or malformed is counted at its reported usage. A connection that drops after sending is counted at the worst case.
* Unless real prices are given in `NEBIUS_PRICE_INPUT_PER_M` and `NEBIUS_PRICE_OUTPUT_PER_M`, costs use built-in price ceilings set above the prices listed for these models, so the ledger over-counts.
* The provider's billing page is the authority on actual charges.
* `halo budget` prints the total. `halo budget --add-usd X --note "..."` records spend made outside the tool.

## Reproducibility

Every Nemotron supervisor call in a benchmark can be recorded to a cache keyed on the model, the messages, the temperature and the request options (reasoning switch, token limit). Failed calls are stored too, with their error and latency, so a replayed run falls back at exactly the moments the live run did. Anyone can then replay the run exactly, with no key and no cost:

```bash
halo bench ... --configs improved+nemotron:<model> --cache results/nemotron_cache.jsonl --cache-mode replay
```

A benchmark replay that misses the cache stops that run with an error rather than letting the rules fill in; in the dashboard the rules take that step and the log shows the fallback. The mission compiler's and test engineer's calls are not cached: they are kept in call logs (`results/mission_calls.jsonl.gz`, `results/stress/nemotron_calls.jsonl.gz`), and their results are scored from the committed outputs. The valley compiler's calls are cached like the supervisor's (`results/valley_compile_cache.jsonl`), so `halo valley-eval --replay` gives the same scores with no key, and so are the valley test engineer's (`results/valley_stress/calls_cache.jsonl`): `tools/wildfire_valley_stress.py search --searcher nemotron --search 0 --cache results/valley_stress/calls_cache.jsonl --replay --out check/nemotron_0.json` reproduces a recorded search with no key.

The strike picker's replies are cached too, failed calls included (`results/strike_checks/nemotron_cache.jsonl`). From `swarmlab/`, the command below flies the 20 held-out Nemotron flights again with no key and no cost; the `--out` file must be new, because the sweep skips flights already in it. A replay of valley 9300 matched the recorded flight. The commands of the plan are in [PREREG.md](../results/strike_checks/PREREG.md#commands).

```bash
PYTHONPATH=src python3 tools/wildfire_strike_check_sweep.py --seeds 9300-9319 --sizes 8 --arms nemotron \
    --cache results/strike_checks/nemotron_cache.jsonl --cache-mode replay --out check/strikes_nemotron.jsonl
```

## Evidence

`halo doctor --flight` runs the end-to-end check:

1. It lists models and picks a Nemotron model.
2. It sends a real supervisor request built from a real simulation state.
3. It validates the reply and passes it through the safety validator.
4. It flies a full scenario with Nemotron supervising.

It writes `evidence/latest/nebius_e2e.json` (each step, pass or fail, with the usage and cost of the doctor's own call and of the flight's calls) and `evidence/latest/nebius_calls.jsonl` (every call, redacted). The committed check from 12 October is in `evidence/` itself, written by an earlier build that reported the doctor's own call only; the flight's calls are in its `nebius_calls.jsonl`.

Status: the end-to-end check passed live on 12 October 2026. The model list returned 25 models, 4 of them Nemotron. A supervisor call to Nemotron 3 Nano took 0.97 s and returned a valid directive that the validator accepted. A full forest-trail flight then made 30 calls with no fallbacks, although the flight did not reach the end of the trail because the model kept a grid that did not fit. The first attempt, two minutes earlier, failed with an empty reply; `evidence/README.md` explains both runs. The integration is also tested offline against a fake transport (`tests/test_safety_ai.py`).
