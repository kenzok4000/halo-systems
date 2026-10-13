"""Nebius Token Factory client (OpenAI-compatible chat completions), stdlib only.

* Credentials come from the environment only: ``NEBIUS_API_KEY`` (required)
  and ``NEBIUS_BASE_URL`` (default ``https://api.tokenfactory.nebius.com/v1``).
  The key is never logged, printed or written to disk, is sent only over https,
  and is never forwarded on a redirect.
* Timeouts on every request; retries with exponential backoff and jitter on
  network errors, 429 and 5xx; ``Retry-After`` is honoured; a client-side
  token bucket keeps us under a request-rate limit.
* Every call returns a ``CallRecord`` (latency, tokens, cost estimate,
  status, attempts, request id) and is appended to a JSON-lines log with
  secrets redacted, which is the evidence for the usage and cost report.
* A spend guard (``budget.py``) checks a shared ledger before every request
  and refuses calls that could cross the cap.
* Responses are validated: HTTP status, JSON shape, a non-empty message.
  Callers parse and validate the content themselves (see ``extract_json``).

Failures raise ``NebiusError`` with a short reason; supervisors catch it and
fall back to the last safe directive, so the swarm never waits on the API.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import jsonl
from .budget import Budget, BudgetExceeded, Pricing, estimate_prompt_tokens

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_KEY_PATTERN = re.compile(r"(Bearer\s+)?[A-Za-z0-9_\-\.]{24,}")


class NebiusError(RuntimeError):
    """``fatal`` errors (no key, key rejected, budget cap) will fail every later call too."""

    def __init__(self, reason: str, status: int | None = None, record: "CallRecord | None" = None, fatal: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.record = record
        self.fatal = fatal


@dataclass
class CallRecord:
    call_id: str
    role: str
    model: str
    started_at: float
    latency_s: float = 0.0
    attempts: int = 0
    status: str = "pending"            # ok | error
    http_status: int | None = None
    error: str | None = None
    request_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None
    response_format: str | None = None
    cached: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChatResult:
    content: str
    record: CallRecord
    raw: dict


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: int):
        self.rate = rate_per_s
        self.capacity = burst
        self.tokens = float(burst)
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
            time.sleep(wait)


def reasoning(on: bool) -> dict:
    """Request body switch for Nemotron 3's reasoning mode. With it on (the model's default), the
    reasoning counts against max_tokens and can use all of it, leaving an empty answer."""
    return {"chat_template_kwargs": {"enable_thinking": bool(on)}}


def redact(text: str, secret: str | None) -> str:
    if not text:
        return text
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return _KEY_PATTERN.sub(lambda m: "[REDACTED]" if m.group(1) else m.group(0), text)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib follows redirects and copies every header, the key included, to the new URL, even on
    another host. The API never redirects, so a redirect comes back as its own HTTP status instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def base_url_problem(url: str) -> str | None:
    """Why the key must not be sent to ``url``, or None. Only https is allowed."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return f"NEBIUS_BASE_URL must be an https URL, got {parsed.scheme or 'no'} scheme"
    return None


def _http_post(url: str, headers: dict, body: bytes, timeout: float):
    """Default transport: returns (status, headers, body bytes). Raises URLError on network failure."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() if e.fp else b""


def _http_get(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() if e.fp else b""


class NebiusClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, *, timeout_s: float = 30.0,
                 max_retries: int = 4, backoff_base_s: float = 1.0, backoff_max_s: float = 20.0,
                 rate_per_s: float = 2.0, burst: int = 4, log_path: str | os.PathLike | None = None,
                 pricing: Pricing | None = None, budget: Budget | None = None, transport=None, get_transport=None,
                 sleep=time.sleep, clock=time.perf_counter):
        self.api_key = api_key if api_key is not None else os.environ.get("NEBIUS_API_KEY", "")
        self.base_url = (base_url or os.environ.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.bucket = TokenBucket(rate_per_s, burst)
        self.log_path = Path(log_path) if log_path else None
        self.pricing = pricing          # None: per-model prices from Pricing.for_model
        self.budget = budget or Budget()
        self.transport = transport or _http_post
        self.get_transport = get_transport or _http_get
        self.sleep = sleep
        self.clock = clock
        self.records: list[CallRecord] = []
        self._json_mode_unsupported: set[str] = set()
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "Accept": "application/json", "User-Agent": "swarmlab/0.1"}

    def _log(self, record: CallRecord, request: dict | None = None, response_text: str | None = None) -> None:
        with self._lock:
            self.records.append(record)
            if not self.log_path:
                return
            entry = {"type": "call", **record.to_dict()}
            if request is not None:
                entry["request"] = json.loads(redact(json.dumps(request), self.api_key))
            if response_text is not None:
                entry["response_text"] = redact(response_text, self.api_key)[:20000]
            jsonl.append(self.log_path, entry)

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(self.backoff_max_s, max(0.0, float(retry_after)))
            except ValueError:
                pass
        base = min(self.backoff_max_s, self.backoff_base_s * (2 ** (attempt - 1)))
        return base * (0.5 + random.random() / 2)

    def list_models(self) -> list[str]:
        if not self.configured:
            raise NebiusError("NEBIUS_API_KEY is not set")
        if base_url_problem(self.base_url):
            raise NebiusError(base_url_problem(self.base_url))
        status, _, body = self.get_transport(f"{self.base_url}/models", self._headers(), self.timeout_s)
        if status != 200:
            raise NebiusError(f"listing models failed with HTTP {status}: {redact(body.decode('utf-8', 'replace'), self.api_key)[:300]}", status)
        data = json.loads(body)
        return sorted(m.get("id", "") for m in data.get("data", []))

    def chat(self, model: str, messages: list[dict], *, role: str = "chat", temperature: float = 0.2,
             max_tokens: int = 800, json_mode: bool = True, extra_body: dict | None = None,
             timeout_s: float | None = None, deadline_s: float | None = None) -> ChatResult:
        """One chat completion with retries. Raises ``NebiusError`` when it cannot get a valid answer.

        ``deadline_s`` bounds the whole call, retries and waits included: no attempt starts or waits
        past it, and the last attempt's timeout is cut to what is left. An answer that arrives later
        than the caller can use it is worth nothing, so the caller falls back sooner instead.
        """
        record = CallRecord(call_id=uuid.uuid4().hex[:12], role=role, model=model, started_at=time.time())
        if not self.configured:
            record.status, record.error = "error", "NEBIUS_API_KEY is not set"
            self._log(record)
            raise NebiusError(record.error, record=record, fatal=True)
        if base_url_problem(self.base_url):
            record.status, record.error = "error", base_url_problem(self.base_url)
            self._log(record)
            raise NebiusError(record.error, record=record, fatal=True)
        pricing = self.pricing or Pricing.for_model(model)
        worst_case = pricing.cost(estimate_prompt_tokens(messages), max_tokens)
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        use_json = json_mode and model not in self._json_mode_unsupported
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        if extra_body:
            payload.update(extra_body)
        t0 = self.clock()
        last_error, last_status, text = "unknown error", None, ""
        attempt = 0

        def left() -> float:
            return float("inf") if deadline_s is None else deadline_s - (self.clock() - t0)

        def wait(seconds: float) -> bool:
            """Back off before the next attempt; False if the deadline would pass first."""
            if seconds >= left():
                return False
            self.sleep(seconds)
            return True

        while attempt <= self.max_retries:
            if left() <= 0:
                last_error = f"deadline of {deadline_s:g} s passed; last error: {last_error}"
                break
            attempt += 1
            record.attempts = attempt
            try:
                self.budget.check(worst_case)
            except BudgetExceeded as exc:
                record.status, record.error = "error", str(exc)
                record.latency_s = round(self.clock() - t0, 3)
                self._log(record)
                raise NebiusError(str(exc), record=record, fatal=True) from None
            self.bucket.take()
            try:
                status, headers, body = self.transport(f"{self.base_url}/chat/completions", self._headers(),
                                                       json.dumps(payload).encode(),
                                                       min(timeout_s or self.timeout_s, left()))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error, last_status = f"network error: {type(exc).__name__}: {exc}", None
                # The server may have done the work before the connection broke: count the worst case.
                self.budget.add(model=model, role=role, cost_usd=worst_case, status="unknown", call_id=record.call_id,
                                pricing=pricing.source, note=f"no usage returned ({type(exc).__name__}); counted at worst case")
                if attempt <= self.max_retries and wait(self._backoff(attempt, None)):
                    continue
                break
            headers = {k.lower(): v for k, v in (headers or {}).items()}
            record.request_id = headers.get("x-request-id") or headers.get("request-id") or record.request_id
            text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
            last_status = status
            if status == 200:
                data, content = None, ""
                try:
                    data = json.loads(text)
                    choice = data["choices"][0]
                    content = choice["message"].get("content") or ""
                    last_error = "" if content.strip() else "empty message content"
                    if not content.strip() and choice.get("finish_reason") == "length":
                        # The token limit ran out (usually spent on reasoning): the same request would
                        # be cut off again, so do not pay for retries.
                        last_error = "reply cut off at max_tokens before any answer (reasoning used the budget)"
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    last_error = f"malformed response: {type(exc).__name__}"
                # A 200 is billed even when the reply turns out empty or malformed (a reasoning model can
                # spend all of max_tokens thinking), so charge it before deciding whether to retry.
                usage = data.get("usage") if isinstance(data, dict) else None
                used_p = int((usage or {}).get("prompt_tokens") or 0)
                used_c = int((usage or {}).get("completion_tokens") or 0)
                cost = pricing.cost(used_p, used_c) if usage else worst_case
                record.prompt_tokens += used_p
                record.completion_tokens += used_c
                record.total_tokens = record.prompt_tokens + record.completion_tokens
                record.cost_usd = round(record.cost_usd + cost, 6)
                self.budget.add(model=model, role=role, prompt_tokens=used_p, completion_tokens=used_c, cost_usd=cost,
                                status="ok" if not last_error else "unusable", call_id=record.call_id,
                                pricing=pricing.source, note="" if usage else "no usage in response; counted at worst case")
                if last_error:
                    if attempt <= self.max_retries and "cut off" not in last_error \
                            and wait(self._backoff(attempt, None)):
                        continue
                    break
                record.finish_reason = choice.get("finish_reason")
                record.request_id = record.request_id or data.get("id")
                record.response_format = "json_object" if use_json else "text"
                record.status, record.http_status = "ok", 200
                record.latency_s = round(self.clock() - t0, 3)
                self._log(record, payload, text)
                return ChatResult(content, record, data)
            if status == 400 and use_json and "response_format" in text:
                # Model does not support JSON mode: remember and retry without it (prompt still asks for JSON).
                self._json_mode_unsupported.add(model)
                payload.pop("response_format", None)
                use_json = False
                attempt -= 1
                continue
            last_error = f"HTTP {status}: {redact(text, self.api_key)[:300]}"
            if status in RETRY_STATUS and attempt <= self.max_retries \
                    and wait(self._backoff(attempt, headers.get("retry-after"))):
                continue
            break
        record.status, record.http_status, record.error = "error", last_status, last_error
        record.latency_s = round(self.clock() - t0, 3)
        self._log(record, payload, text or None)
        raise NebiusError(last_error, last_status, record, fatal=last_status in (401, 403))

    def usage_summary(self) -> dict:
        ok = [r for r in self.records if r.status == "ok"]
        lat = sorted(r.latency_s for r in ok)
        return {
            "calls": len(self.records),
            "ok": len(ok),
            "errors": len(self.records) - len(ok),
            "retries": sum(max(0, r.attempts - 1) for r in self.records),
            "prompt_tokens": sum(r.prompt_tokens for r in ok),
            "completion_tokens": sum(r.completion_tokens for r in ok),
            "cost_usd": round(sum(r.cost_usd for r in self.records), 6),
            "latency_p50_s": lat[len(lat) // 2] if lat else None,
            "latency_p95_s": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
            "budget": self.budget.summary(),
        }


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply (drops reasoning tags and code fences)."""
    if text is None:
        raise ValueError("no content")
    t = _THINK.sub("", text).strip()
    if "<think>" in t.lower():          # unterminated reasoning block: keep what follows it, if anything
        t = t[t.lower().rfind("</think>") + 8:] if "</think>" in t.lower() else ""
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except ValueError:
        start = t.find("{")
        if start < 0:
            raise ValueError("no JSON object in reply")
        depth, end = 0, -1
        in_str, esc = False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            raise ValueError("unterminated JSON object in reply")
        obj = json.loads(t[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("reply JSON is not an object")
    return obj
