# NVIDIA Nemotron on Nebius Token Factory

SwarmLab uses NVIDIA Nemotron models, served by Nebius Token Factory's OpenAI-compatible API, in three roles. Each role has a conventional baseline that gets the same inputs, so every claim about the model is a measured comparison.

| Role | Input | Output | Baseline |
|---|---|---|---|
| Supervisor | Status report every 5 s of simulated time | Directive (formation, spacing, speed, altitude, safety margin) | Rule supervisor; fixed plan |
| Mission compiler | Operator's plain-language request | Mission plan JSON, or a refusal for non-civilian use | Regex and keyword parser |
| Test engineer | Every stress test so far, with outcomes | The next batch of tests, each with a reason | Random search; corner search; cross-entropy method |

## Why the model is not in the control loop

The drones need a new thrust command every 20 ms. A hosted model answers in hundreds of milliseconds to seconds, and sometimes not at all. So the model works only where a decision can wait a few seconds, and where judgement over many signals helps: wind, near misses, radio health, battery, and the corridor ahead. The engine applies each answer only after its measured latency has passed in simulated time, so a slow answer has a real cost in the benchmark.

Nothing the model says reaches the motors directly. Every directive passes the safety validator (schema, envelope clamps, a spacing floor derived from body size and navigation error, and a rate limit), and the 50 Hz safety filter still holds clearance whatever the directive.

## Reliability

`ai/client.py` uses only the standard library:

* **Credentials** come from the environment only (`NEBIUS_API_KEY`). The key is never logged, printed or written to disk, and log lines are scrubbed of anything that looks like a key.
* **Timeouts and deadlines.** Every request has a timeout, and a supervisor call has a deadline for the whole call, retries and waits included: 15 s with reasoning off (8 s per attempt) and 60 s with it on (one attempt). After that the rules decide that step. The values come from the tuning-seed call logs: Super's slow attempts either answered within about 2 s on retry or not at all, and an answer much later than 15 s describes a swarm that has flown on 75 m.
* **Retries** use exponential backoff with jitter on network errors, 408, 409, 425, 429 and 5xx. `Retry-After` is honoured. Authentication errors are not retried.
* **Rate limiting** uses a client-side token bucket.
* **JSON mode** is used when the model supports it, with an automatic fallback when it does not.
* **Responses are validated**: HTTP status, JSON shape, and a non-empty message. Replies are then parsed defensively (reasoning tags and code fences are stripped) and checked field by field.
* **Graceful failure**: the supervisor falls back to the rule supervisor for that step. In the dashboard, a failed mission-compiler call falls back to the regex parser. The test engineer tops up with random tests. Every fallback is counted.
* **Structured logging**: one JSON line per call with latency, attempts, tokens, estimated cost, request id, finish reason and outcome.
* **One request in flight.** The supervisor never has two calls outstanding, so answers cannot arrive out of order.
* **Shared files are append-safe.** Benchmark workers share the call cache, the call log and the spend ledger. Each append takes a file lock and is written in one go, and a reader skips a broken line instead of stopping.

## Reasoning on or off

Nemotron 3 models reason before they answer unless told not to. The reasoning comes back in `message.reasoning` and counts against `max_tokens`; if it uses them all, `content` is empty and `finish_reason` is `length`. SwarmLab sends `chat_template_kwargs: {"enable_thinking": false}` unless a role asks for reasoning (`nemotron-think:<model>` in a config), in which case `max_tokens` is at least 4096. A reply cut off at the limit is not retried, because the same request would be cut off again.

With reasoning off, Nemotron 3 Nano answered in about 1 s but, before the status report spelled out its corridor and energy checks in words, it ignored them: it kept a grid formation that the report said did not fit. With reasoning on, it used those checks, but answers took 9 to 14 s at the median in the two tuning rounds, and the slowest took over 100 s before the 60 s deadline was added. Both variants, and Super, are compared on the tuning seeds in `results/`.

## Spend guard

`ai/budget.py` keeps a ledger of every call's tokens and estimated cost in a JSON-lines file (`SWARMLAB_LEDGER`), shared by all processes and sessions that use the same file. Before each request, the guard adds a pessimistic estimate of that request to the total: about three characters per token for the prompt, plus the full `max_tokens` for the reply. If the total would pass the cap (`SWARMLAB_BUDGET_USD`, default 18), the call is refused. A benchmark then stops instead of running up a bill.

* Some requests are billed even though they give no usable answer. A reply that turns out empty or malformed is counted at its reported usage. A connection that drops after sending is counted at the worst case.
* Unless real prices are given in `NEBIUS_PRICE_INPUT_PER_M` and `NEBIUS_PRICE_OUTPUT_PER_M`, costs use built-in price ceilings set above the prices listed for these models, so the ledger over-counts.
* The provider's billing page is the authority on actual charges.
* `swarmlab budget` prints the total. `swarmlab budget --add-usd X --note "..."` records spend made outside the tool.

## Reproducibility

Every Nemotron call in a benchmark can be recorded to a cache keyed on the model, the messages, the temperature and the request options (reasoning switch, token limit). Failed calls are stored too, with their error and latency, so a replayed run falls back at exactly the moments the live run did. Anyone can then replay the run exactly, with no key and no cost:

```bash
swarmlab bench ... --configs improved+nemotron:<model> --cache results/nemotron_cache.jsonl --cache-mode replay
```

A replay that misses the cache is reported as a fallback, never silently filled.

## Evidence

`swarmlab doctor --flight` runs the end-to-end check:

1. It lists models and picks a Nemotron model.
2. It sends a real supervisor request built from a real simulation state.
3. It validates the reply and passes it through the safety validator.
4. It flies a full scenario with Nemotron supervising.

It writes `evidence/nebius_e2e.json` (each step, pass or fail, usage and cost) and `evidence/nebius_calls.jsonl` (every call, redacted).

Status: the end-to-end check passed live on 12 October 2026. The model list returned 25 models, 4 of them Nemotron. A supervisor call to Nemotron 3 Nano took 0.97 s and returned a valid directive that the validator accepted. A full forest-trail flight then made 30 calls with no fallbacks. The integration is also tested offline against a fake transport (`tests/test_safety_ai.py`).
