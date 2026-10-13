"""Safety validator, CBF filter, rule supervisor, Nebius client and Nemotron supervisor.

The client tests use a fake HTTP transport defined here; they test retry,
parsing and logging logic only. Real calls are made by ``swarmlab doctor``.
"""

import json
import math
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from swarmlab.ai.budget import Budget, Pricing
from swarmlab.ai.cache import CallCache
from swarmlab.ai.client import NebiusClient, NebiusError, extract_json
from swarmlab.control.cbf import build_constraints, project
from swarmlab.control.directive import Directive
from swarmlab.safety.validator import SafetyValidator
from swarmlab.supervisor.nemotron import NemotronSupervisor, parse_directive
from swarmlab.supervisor.rule import RuleSupervisor

FAKE_KEY = "test-key-0123456789abcdefghijklmnop"
_LEDGER_DIR = tempfile.TemporaryDirectory()
_SAVED_ENV = {}


def setUpModule():
    # Fake calls must never reach the real spend ledger.
    for k in ("SWARMLAB_LEDGER", "SWARMLAB_BUDGET_USD", "NEBIUS_PRICE_INPUT_PER_M", "NEBIUS_PRICE_OUTPUT_PER_M"):
        _SAVED_ENV[k] = os.environ.pop(k, None)
    os.environ["SWARMLAB_LEDGER"] = str(Path(_LEDGER_DIR.name) / "ledger.jsonl")


def tearDownModule():
    for k, v in _SAVED_ENV.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    _LEDGER_DIR.cleanup()


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.v = SafetyValidator(0.15, nav_sigma_m=0.3)
        self.cur = Directive("grid", 6.0, 5.0, 20.0, 1.0)

    def test_clamps_out_of_range_values(self):
        r = self.v.check({**self.cur.to_dict(), "speed_mps": 40, "altitude_m": 500}, self.cur, 10.0, "nemotron")
        self.assertEqual(r.action, "clamped")
        self.assertEqual((r.directive.speed_mps, r.directive.altitude_m), (12.0, 120.0))

    def test_rejects_unknown_shape_and_non_numbers(self):
        for bad in ({**self.cur.to_dict(), "formation": "star"}, {**self.cur.to_dict(), "spacing_m": "wide"},
                    {**self.cur.to_dict(), "speed_mps": float("nan")}, "not an object"):
            r = self.v.check(bad, self.cur, 10.0, "nemotron")
            self.assertEqual(r.action, "rejected")
            self.assertEqual(r.directive, self.cur)

    def test_raises_spacing_to_the_safe_floor(self):
        r = self.v.check({**self.cur.to_dict(), "spacing_m": 2.0, "safety_margin_m": 2.0}, self.cur, 10.0, "rule")
        self.assertGreaterEqual(r.directive.spacing_m, 2 * 0.15 + 2.0 + 3 * math.sqrt(2) * 0.3 - 1e-6)

    def test_rate_limits_plan_changes(self):
        a = self.v.check({**self.cur.to_dict(), "formation": "line"}, self.cur, 10.0, "nemotron")
        self.assertTrue(a.ok)
        b = self.v.check({**a.directive.to_dict(), "formation": "ring"}, a.directive, 11.0, "nemotron")
        self.assertEqual(b.action, "rejected")
        c = self.v.check({**a.directive.to_dict(), "formation": "ring"}, a.directive, 14.0, "nemotron")
        self.assertTrue(c.ok)


class CbfTest(unittest.TestCase):
    def test_head_on_pair_is_made_to_brake(self):
        p = np.array([[0.0, 0, 10], [8.0, 0, 10]])
        v = np.array([[5.0, 0, 0], [-5.0, 0, 0]])
        age = np.zeros((2, 2))
        g, b, m = build_constraints(p, v, np.tile(p, (2, 1, 1)), np.tile(v, (2, 1, 1)), age, np.ones(2, bool),
                                    d_safe=1.3, a_brake=3.0, gamma=1.5)
        a_nom = np.array([[2.0, 0, 0], [-2.0, 0, 0]])  # both still pushing toward each other
        a, active = project(a_nom, g, b, m, 8.0, iterations=40)
        self.assertTrue(active.any())
        self.assertLess(a[0, 0], 0)  # drone 0 now brakes
        self.assertGreater(a[1, 0], 0)
        viol = np.einsum("nkd,nd->nk", g, a) - b
        self.assertLess(np.max(np.where(m, viol, -np.inf)), 1e-2)

    def test_far_apart_leaves_command_unchanged(self):
        p = np.array([[0.0, 0, 10], [80.0, 0, 10]])
        v = np.zeros((2, 3))
        g, b, m = build_constraints(p, v, np.tile(p, (2, 1, 1)), np.tile(v, (2, 1, 1)), np.zeros((2, 2)),
                                    np.ones(2, bool), d_safe=1.3, a_brake=3.0, gamma=1.5)
        a_nom = np.array([[1.0, 2.0, 0.5], [0.0, -1.0, 0.0]])
        a, _ = project(a_nom, g, b, m, 8.0)
        np.testing.assert_allclose(a, a_nom, atol=1e-9)


def summary(**over):
    s = {
        "time_s": 30.0,
        "mission": {"brief": "test", "progress_pct": 20.0, "remaining_m": 800.0, "leader_speed_mps": 5.0},
        "swarm": {"flying": 16, "planned": 16, "lost_drones": [], "retired_low_battery": 0},
        "mission_directive": {"formation": "grid", "spacing_m": 6.0, "speed_mps": 5.0, "altitude_m": 15.0, "safety_margin_m": 1.0},
        "current_directive": {"formation": "grid", "spacing_m": 6.0, "speed_mps": 5.0, "altitude_m": 15.0, "safety_margin_m": 1.0},
        "formation": {"rms_error_m": 0.4, "width_m": 18.0},
        "safety": {"closest_pair_m_last_5s": 5.0, "near_misses_last_5s": 0, "collisions_so_far": 0},
        "environment": {"wind_mps": 2.0, "wind_from_deg": 0, "headwind_mps": 0.0, "gust_peak_mps_last_5s": 1.0,
                        "obstacles_per_1000m2_ahead": 0.0, "free_corridor_width_m": None, "lookahead_m": 100.0},
        "comms": {"stale_link_fraction": 0.0, "isolated_drones": 0},
        "energy": {"battery_mean_pct": 90.0, "battery_min_pct": 88.0, "endurance_left_min": 12.0, "time_to_finish_min": 2.7,
                   "usable_wh_mean": 30.0, "usable_wh_min": 29.0,
                   "wh_per_km_by_speed": {"3": 9.0, "4": 7.0, "5": 6.0, "6": 5.5, "8": 4.6, "10": 4.1, "12": 4.0}},
        "limits": {"formations": ["grid", "line", "column", "v", "ring"], "spacing_m": [2, 15], "spacing_floor_m": 2.54,
                   "speed_mps": [0.5, 12], "altitude_m": [5, 120], "safety_margin_m": [0.5, 4]},
        "formation_widths_at_current_spacing_m": {"grid": 18.0, "line": 90.0, "column": 0.0, "v": 63.6, "ring": 30.6},
    }
    for path, value in over.items():
        node = s
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return s


class RuleSupervisorTest(unittest.TestCase):
    def test_quiet_conditions_keep_the_plan(self):
        self.assertIsNone(RuleSupervisor().decide(summary(), Directive("grid", 6.0, 5.0, 15.0, 1.0), 30.0))

    def test_narrow_corridor_gives_a_column(self):
        d = RuleSupervisor().decide(summary(**{"environment.free_corridor_width_m": 10.0}), Directive(), 30.0)
        self.assertEqual(d.directive.formation, "column")

    def test_moderate_corridor_tightens_spacing(self):
        d = RuleSupervisor().decide(summary(**{"environment.free_corridor_width_m": 20.0}), Directive(), 30.0)
        self.assertEqual(d.directive.formation, "grid")
        self.assertLess(d.directive.spacing_m, 6.0)

    def test_tight_energy_picks_cheapest_speed(self):
        d = RuleSupervisor().decide(summary(**{"energy.usable_wh_mean": 4.0}), Directive(), 30.0)
        self.assertEqual(d.directive.speed_mps, 12.0)


class FakeTransport:
    """Scripted HTTP responses for the retry and parsing logic."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(content, prompt=100, completion=20):
    return 200, {"x-request-id": "req-1"}, json.dumps({
        "id": "cmpl-1", "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}}).encode()


DIRECTIVE_JSON = json.dumps({"formation": "column", "spacing_m": 6, "speed_mps": 4, "altitude_m": 15,
                             "safety_margin_m": 1.0, "reason": "trail ahead is narrow"})


class ClientTest(unittest.TestCase):
    def client(self, responses, **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "calls.jsonl"
        t = FakeTransport(responses)
        c = NebiusClient(api_key=FAKE_KEY, base_url="https://example.invalid/v1", transport=t, log_path=self.log,
                         sleep=lambda s: None, rate_per_s=1000, burst=1000, **kw)
        return c, t

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_success_records_usage_and_never_logs_the_key(self):
        c, t = self.client([ok(DIRECTIVE_JSON)])
        r = c.chat("nvidia/nemotron-test", [{"role": "user", "content": "hi"}])
        self.assertEqual(r.record.total_tokens, 120)
        self.assertEqual(r.record.request_id, "req-1")
        self.assertEqual(t.calls[0]["headers"]["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, self.log.read_text())

    def test_retries_rate_limit_and_server_errors(self):
        c, t = self.client([(429, {"Retry-After": "1"}, b"slow down"), (503, {}, b"busy"), ok(DIRECTIVE_JSON)])
        r = c.chat("m", [{"role": "user", "content": "x"}])
        self.assertEqual(r.record.attempts, 3)

    def test_network_error_then_success(self):
        c, _ = self.client([ConnectionError("reset"), ok(DIRECTIVE_JSON)])
        self.assertEqual(c.chat("m", []).record.attempts, 2)

    def test_gives_up_and_does_not_retry_auth_errors(self):
        c, t = self.client([(401, {}, b'{"detail":"Unable to authenticate"}')])
        with self.assertRaises(NebiusError) as ctx:
            c.chat("m", [])
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(len(t.calls), 1)

    def test_exhausted_retries_raise(self):
        c, t = self.client([(500, {}, b"")] * 3, max_retries=2)
        with self.assertRaises(NebiusError):
            c.chat("m", [])
        self.assertEqual(len(t.calls), 3)

    def test_malformed_and_empty_replies_are_retried(self):
        c, _ = self.client([(200, {}, b"not json"), ok("   "), ok(DIRECTIVE_JSON)])
        self.assertEqual(c.chat("m", []).record.attempts, 3)

    def test_reply_cut_off_by_the_token_limit_is_not_retried(self):
        cut = (200, {}, json.dumps({"choices": [{"message": {"content": None, "reasoning": "thinking..."},
                                                 "finish_reason": "length"}],
                                    "usage": {"prompt_tokens": 100, "completion_tokens": 1200}}).encode())
        c, t = self.client([cut, ok(DIRECTIVE_JSON)])
        with self.assertRaises(NebiusError) as ctx:
            c.chat("m", [])
        self.assertIn("cut off", ctx.exception.reason)
        self.assertEqual(len(t.calls), 1)

    def test_json_mode_fallback(self):
        c, t = self.client([(400, {}, b'{"error":"response_format not supported"}'), ok(DIRECTIVE_JSON)])
        r = c.chat("m", [])
        self.assertIn("response_format", t.calls[0]["body"])
        self.assertNotIn("response_format", t.calls[1]["body"])
        self.assertEqual(r.record.response_format, "text")

    def test_missing_key(self):
        c = NebiusClient(api_key="", transport=FakeTransport([]))
        with self.assertRaises(NebiusError):
            c.chat("m", [])


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, responses, cap=1.0, **kw):
        t = FakeTransport(responses)
        c = NebiusClient(api_key=FAKE_KEY, transport=t, sleep=lambda s: None, rate_per_s=1000, burst=1000,
                         budget=Budget(self.ledger, cap), **kw)
        return c, t

    def test_cap_stops_the_call_before_it_is_sent(self):
        c, t = self.client([ok(DIRECTIVE_JSON)], cap=0.0001)
        with self.assertRaises(NebiusError) as ctx:
            c.chat("nvidia/nemotron-nano-test", [{"role": "user", "content": "x"}], max_tokens=1200)
        self.assertTrue(ctx.exception.fatal)
        self.assertIn("budget cap", ctx.exception.reason)
        self.assertEqual(t.calls, [])

    def test_ledger_is_shared_and_counts_billed_failures(self):
        # An empty reply is still billed; a dropped connection is counted at the worst case.
        c, _ = self.client([ok("  ", 100, 1200), ConnectionError("reset"), ok(DIRECTIVE_JSON, 100, 20)])
        r = c.chat("nvidia/nemotron-nano-test", [{"role": "user", "content": "x" * 30}], max_tokens=1200)
        price = Pricing.for_model("nvidia/nemotron-nano-test")
        entries = [json.loads(l) for l in self.ledger.read_text().splitlines()]
        self.assertEqual([e["status"] for e in entries], ["unusable", "unknown", "ok"])
        self.assertAlmostEqual(entries[0]["cost_usd"], price.cost(100, 1200), places=6)
        self.assertGreater(entries[1]["cost_usd"], price.cost(0, 1200) - 1e-9)
        self.assertEqual(r.record.prompt_tokens, 200)
        other = Budget(self.ledger, 1.0)  # a second process reading the same file
        self.assertAlmostEqual(other.spent_usd(), sum(e["cost_usd"] for e in entries), places=6)

    def test_ceilings_and_env_prices(self):
        self.assertGreater(Pricing.for_model("nvidia/some-new-model").output_per_m,
                           Pricing.for_model("nvidia/nemotron-super").output_per_m)
        os.environ["NEBIUS_PRICE_INPUT_PER_M"], os.environ["NEBIUS_PRICE_OUTPUT_PER_M"] = "0.5", "1.5"
        try:
            self.assertEqual(Pricing.for_model("anything").source, "env")
        finally:
            del os.environ["NEBIUS_PRICE_INPUT_PER_M"], os.environ["NEBIUS_PRICE_OUTPUT_PER_M"]

    def test_strict_supervisor_stops_on_a_dead_key_instead_of_falling_back(self):
        responses = [(401, {}, b'{"detail":"Unable to authenticate"}')]
        c, _ = self.client(list(responses))
        loose = NemotronSupervisor(client=c, model="nvidia/nemotron-test")
        self.assertTrue(loose.decide(summary(), Directive(), 30.0).meta["fallback"])
        c, _ = self.client(list(responses))
        strict = NemotronSupervisor(client=c, model="nvidia/nemotron-test", strict=True)
        with self.assertRaises(NebiusError):
            strict.decide(summary(), Directive(), 30.0)


class ReasoningSwitchTest(unittest.TestCase):
    def test_reasoning_is_off_by_default_and_part_of_the_cache_key(self):
        t = FakeTransport([ok(DIRECTIVE_JSON), ok(DIRECTIVE_JSON)])
        c = NebiusClient(api_key=FAKE_KEY, transport=t, sleep=lambda s: None, rate_per_s=1000, burst=1000)
        with tempfile.TemporaryDirectory() as tmp:
            cache = CallCache(Path(tmp) / "c.jsonl", "record")
            off = NemotronSupervisor(client=c, model="m", cache=cache)
            on = NemotronSupervisor(client=c, model="m", cache=cache, think=True)
            off.decide(summary(), Directive(), 30.0)
            d = on.decide(summary(), Directive(), 30.0)
            self.assertFalse(d.meta["cached"])          # same prompt, different switch: not a cache hit
        bodies = [call["body"] for call in t.calls]
        self.assertEqual([b["chat_template_kwargs"]["enable_thinking"] for b in bodies], [False, True])
        self.assertGreaterEqual(bodies[1]["max_tokens"], 4096)


class ExtractJsonTest(unittest.TestCase):
    def test_variants(self):
        want = {"a": 1, "b": "x}"}
        for text in ['{"a": 1, "b": "x}"}', '<think>let me see {no}</think>\n{"a": 1, "b": "x}"}',
                     'Sure:\n```json\n{"a": 1, "b": "x}"}\n```', 'answer {"a": 1, "b": "x}"} done']:
            self.assertEqual(extract_json(text), want, text)
        for bad in ("no json here", "[1, 2]", '{"a": '):
            with self.assertRaises(ValueError):
                extract_json(bad)

    def test_parse_directive_requires_all_fields(self):
        self.assertEqual(parse_directive(DIRECTIVE_JSON)["formation"], "column")
        with self.assertRaises(ValueError):
            parse_directive('{"formation": "grid"}')


class NemotronSupervisorTest(unittest.TestCase):
    def make(self, responses, cache=None):
        t = FakeTransport(responses)
        c = NebiusClient(api_key=FAKE_KEY, transport=t, sleep=lambda s: None, rate_per_s=1000, burst=1000, max_retries=1)
        return NemotronSupervisor(client=c, model="nvidia/nemotron-test", cache=cache), t

    def test_valid_reply_becomes_a_decision_with_latency_and_tokens(self):
        sup, _ = self.make([ok(DIRECTIVE_JSON)])
        d = sup.decide(summary(), Directive(), 30.0)
        self.assertEqual(d.directive["formation"], "column")
        self.assertFalse(d.meta["fallback"])
        self.assertEqual(d.meta["total_tokens"], 120)
        self.assertGreaterEqual(d.latency_s, 0.0)

    def test_bad_reply_falls_back_to_rules(self):
        sup, _ = self.make([ok('{"formation": "grid"}')])
        d = sup.decide(summary(**{"environment.free_corridor_width_m": 10.0}), Directive(), 30.0)
        self.assertTrue(d.meta["fallback"])
        self.assertEqual(d.directive.formation, "column")  # the rule supervisor's answer

    def test_api_failure_falls_back(self):
        sup, _ = self.make([(500, {}, b""), (500, {}, b"")])
        d = sup.decide(summary(), Directive(), 30.0)
        self.assertTrue(d.meta["fallback"])
        self.assertIn("HTTP 500", d.meta["error"])

    def test_record_then_replay_gives_the_same_answer_without_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            sup, _ = self.make([ok(DIRECTIVE_JSON)], CallCache(path, "record"))
            first = sup.decide(summary(), Directive(), 30.0)
            sup2, t2 = self.make([], CallCache(path, "replay"))
            second = sup2.decide(summary(), Directive(), 30.0)
            self.assertEqual(first.directive, second.directive)
            self.assertTrue(second.meta["cached"])
            self.assertEqual(t2.calls, [])
            miss = sup2.decide(summary(**{"time_s": 99.0}), Directive(), 99.0)
            self.assertTrue(miss.meta["fallback"])
            self.assertIn("replay cache miss", miss.meta["error"])


if __name__ == "__main__":
    unittest.main()
