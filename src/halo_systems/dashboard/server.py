"""Live dashboard: a small standard-library web server streaming a running simulation.

``halo serve`` starts it (default http://127.0.0.1:8765). The browser
gets frames over Server-Sent Events and can start runs, inject gusts,
motor failures and radio blackouts, order formation changes, type a
mission for the mission compiler, and browse benchmark results.

Live runs advance in real time (or faster). A Nemotron supervisor is
called on a worker thread, so the swarm keeps flying while the model
thinks; its answer is applied when it arrives, through the validator,
exactly like a rule decision. Binds to localhost unless told otherwise.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from ..eval.report import _config_order, _scenario_order, short_label
from ..eval.runner import make_controller, make_supervisor, split_config
from ..eval.scenarios import SCENARIOS, from_mission, make
from ..settings import env
from ..sim.body import DroneBody
from ..sim.engine import STATUS_NAMES, Simulation
from ..supervisor.base import Decision, Supervisor

STATIC = Path(__file__).with_name("static")
mimetypes.add_type("model/gltf-binary", ".glb")   # Python's own table lacks it; /etc/mime.types is not everywhere
FRAME_HZ = 10.0
MAX_RUNS = 4


class AsyncSupervisor(Supervisor):
    """Runs a slow supervisor on a worker thread; the simulation never waits for it."""

    def __init__(self, inner: Supervisor):
        self.inner = inner
        # The engine uses ``name`` to schedule supervision and to tag directive sources, so the
        # wrapper takes on the inner supervisor's identity.
        self.name = inner.name
        self.label = inner.label
        self.uses_ai = inner.uses_ai
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.requested_t = None

    def decide(self, summary, current, t):
        """Start a call if none is running. Always returns None: nothing to apply yet."""
        if self.future is None:
            self.requested_t = t
            self.future = self.pool.submit(self.inner.decide, summary, current, t)
        return None

    def collect(self):
        """(requested_t, decision) when a call has finished, else None."""
        if self.future is not None and self.future.done():
            fut, t_req = self.future, self.requested_t
            self.future = None
            try:
                return t_req, fut.result()
            except Exception as exc:  # keep flying on the last directive; the error is reported
                return t_req, Decision(None, meta={"fallback": True, "error": f"{type(exc).__name__}: {exc}"})
        return None


class LiveRun:
    """One flight on its own thread, paced to wall time x speed, streamed to its SSE subscribers.

    Frames are published at ``FRAME_HZ`` of simulated time.
    """

    def __init__(self, scenario, config: str, seed: int, supervisor_kw: dict | None = None):
        ctrl_spec, sup_spec = split_config(config)
        body = DroneBody()
        sup = make_supervisor(sup_spec, **(supervisor_kw or {}))
        # Replayed calls return at once, so they run in step with the simulation, exactly as in the benchmark.
        # They have to: the engine then applies each answer after its recorded latency in simulated time,
        # so every later summary, and so every message and cache key, matches the recorded run. Live calls
        # go through AsyncSupervisor so the 50 Hz loop never waits on the network.
        replay = getattr(getattr(sup, "cache", None), "mode", None) == "replay"
        self.async_sup = AsyncSupervisor(sup) if sup.uses_ai and not replay else None
        self.sim = Simulation(scenario, make_controller(ctrl_spec, body, scenario.world), self.async_sup or sup,
                              seed=seed, body=body, record_hz=0)
        self.id = uuid.uuid4().hex[:10]
        self.config = config
        self.seed = seed
        self.speed = 1.0
        self.paused = False
        self.lock = threading.Lock()
        self.log_lock = threading.Lock()   # a new subscriber gets each log entry once: in its backlog or its queue
        self.subscribers: list[queue.Queue] = []
        self.sent_events = 0
        # directives[0] is the starting directive; the page has it from hello(), so streaming starts at 1.
        self.sent_directives = 1
        self.sent_ai = 0
        self.stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.started = time.time()

    # -- streaming ------------------------------------------------------------
    def subscribe(self) -> tuple[queue.Queue, list]:
        """A queue for what comes next, and the backlog of log entries already published (for a late subscriber)."""
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock, self.log_lock:
            self.subscribers.append(q)
            q.put(("hello", self.hello()))
            sim = self.sim
            backlog = ([("event", e) for e in sim.events[:self.sent_events]]
                       + [("directive", d) for d in sim.directives[1:self.sent_directives]]
                       + [("ai", a) for a in sim.ai_calls[:self.sent_ai]])
        return q, backlog

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def publish(self, kind: str, data: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait((kind, data))
            except queue.Full:
                pass

    def hello(self) -> dict:
        sc = self.sim.sc
        return {"id": self.id, "config": self.config, "seed": self.seed, "scenario": sc.summary(),
                "world": sc.world.to_dict(), "path": np.round(self.sim.path.points, 2).tolist(), "loop": sc.loop,
                "n": sc.n, "body_radius_m": self.sim.body.radius_m, "directive": self.sim.directive.to_dict(),
                "altitude_max_m": sc.altitude_max_m,
                "targets": [t.to_dict() for t in sc.targets],
                "sensor": {"fov_deg": sc.sensor.fov_deg, "area": list(sc.sensor.area) if sc.sensor.area else None}
                if sc.sensor.enabled else None,
                # The wildfire valley: the true arrival-time grid (the page draws the fire growing from it).
                **({"wildfire": self.sim.wildfire.hello()} if self.sim.wildfire is not None else {})}

    # -- the loop -----------------------------------------------------------------
    def _loop(self) -> None:
        sim = self.sim
        next_frame = 0.0
        wall0, sim0 = time.perf_counter(), sim.t
        while not self.stop and not sim.ended:
            if self.paused:
                time.sleep(0.05)
                wall0, sim0 = time.perf_counter(), sim.t
                continue
            target = sim0 + (time.perf_counter() - wall0) * self.speed
            if sim.t >= target:
                time.sleep(min(0.01, sim.dt / max(self.speed, 0.1)))
                continue
            with self.lock:
                sim.step()
                # A finished Nemotron call re-enters the engine the way Simulation._supervise handles
                # one: it is logged in sim.ai_calls (here with both t_request and t_applied), then put on
                # sim.pending due now, since the real wait has already passed. The next sim.step()
                # passes it through the safety validator with source "nemotron", or "fallback" when
                # meta["fallback"] is set. A None directive (for example a worker exception) keeps the
                # current plan.
                if self.async_sup is not None:
                    got = self.async_sup.collect()
                    if got is not None:
                        t_req, decision = got
                        if decision is not None:
                            if decision.meta:
                                sim.ai_calls.append({"t_request": round(t_req, 2), "t_applied": round(sim.t, 2),
                                                     "latency_s": round(decision.latency_s, 3), **decision.meta})
                            if decision.directive is not None:
                                sim.pending.append((sim.t, t_req, decision))
            if sim.t >= next_frame:
                next_frame = sim.t + 1.0 / FRAME_HZ
                with self.lock:   # the page's alert approvals change the simulation from another thread
                    frame = self.frame()
                self.publish("frame", frame)
                self._flush_logs()
        self._flush_logs()
        self.publish("end", {"metrics": sim.result().metrics if sim.series["t"] else {}})

    def _flush_logs(self) -> None:
        with self.log_lock:
            self._flush_logs_locked()

    def _flush_logs_locked(self) -> None:
        sim = self.sim
        while self.sent_events < len(sim.events):
            self.publish("event", sim.events[self.sent_events])
            self.sent_events += 1
        while self.sent_directives < len(sim.directives):
            self.publish("directive", sim.directives[self.sent_directives])
            self.sent_directives += 1
        while self.sent_ai < len(sim.ai_calls):
            self.publish("ai", sim.ai_calls[self.sent_ai])
            self.sent_ai += 1

    def frame(self) -> dict:
        sim = self.sim
        f = sim.frame()
        s = sim.series
        k = len(s["t"]) - 1
        f["metrics"] = {
            "formation_error_m": round(float(s["integrity_error"][k]), 2) if k >= 0 and np.isfinite(s["integrity_error"][k]) else None,
            "min_sep_m": round(float(s["min_sep"][k]), 2) if k >= 0 and np.isfinite(s["min_sep"][k]) else None,
            "flying": int(sim.active_mask().sum()),
            "lost": int(sum(1 for st in sim.status if STATUS_NAMES[int(st)] in ("crashed", "down"))),
            "retired": int(sum(1 for st in sim.status if STATUS_NAMES[int(st)] == "retired")),
            # Drones destroyed, as the report counts crashes: two per collision, one per obstacle or ground strike.
            "collisions": int(2 * sim.collisions + sim.obstacle_crashes + sim.ground_crashes),
            "progress_m": round(float(sim.ctrl.plan.s), 1),
            "path_m": round(float(sim.path.length), 1),
            "leader_speed": round(float(sim.ctrl.plan.speed_now), 2),
            "battery_mean": round(float(np.mean(100 * sim.battery_wh[sim.active_mask()] / sim.capacity_wh)), 1) if sim.active_mask().any() else 0.0,
            "near_misses": int(sim.near_miss_events),
        }
        if sim.sc.sensor.enabled:
            f["metrics"]["targets_found"] = sim.targets_found
            f["metrics"]["targets_total"] = len(sim.targets)
            f["metrics"]["coverage_pct"] = sim.coverage_pct
        f["directive"] = {k: v for k, v in sim.directive.to_dict().items()}
        f["heading"] = round(float(sim.ctrl.plan.heading), 3)
        f["ai_pending"] = bool(self.async_sup and self.async_sup.future is not None)
        return f

    def export(self) -> dict:
        with self.lock:
            res = self.sim.result()
        return {"config": self.config, "seed": self.seed, **res.to_dict(include_frames=False, include_series=True)}


DEFAULT_REPLAY_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"   # the model of the committed held-out cache


def replay_defaults(results_dir, model: str | None, cache: str | None) -> tuple[str | None, str | None]:
    """With no flags and no key, ``halo serve`` replays the committed Nemotron 3 Nano flights.

    The cache defaults to ``results/nemotron_cache.jsonl`` when that file exists, and the model to the
    one that cache was recorded with when no key and no HALO_SUPERVISOR_MODEL are set. With a key,
    the model stays whatever the flags or the environment say, so a live session never silently
    picks a model.
    """
    default_cache = Path(results_dir) / "nemotron_cache.jsonl"
    if cache is None and default_cache.exists():
        cache = str(default_cache)
    if model is None and cache and not os.environ.get("NEBIUS_API_KEY") and not env("SUPERVISOR_MODEL"):
        model = DEFAULT_REPLAY_MODEL
    return model, cache


class State:
    """What the request threads share: the live runs (at most ``MAX_RUNS``) and the AI settings."""

    def __init__(self, results_dir: Path, evidence_dir: Path, model: str | None, cache_path: Path | None = None):
        self.runs: dict[str, LiveRun] = {}
        self.results_dir = results_dir
        self.evidence_dir = evidence_dir
        self.model = model or env("SUPERVISOR_MODEL") or None
        self.cache_path = cache_path
        self.lock = threading.Lock()

    def ai_status(self) -> dict:
        key = bool(os.environ.get("NEBIUS_API_KEY"))
        cached = bool(self.cache_path and self.cache_path.exists())
        # Without a key, a recorded call cache still lets Nemotron runs be replayed exactly.
        mode = "live" if key else "replay" if cached else None
        return {"key_present": key, "model": self.model, "ai_available": bool(mode and self.model), "mode": mode,
                "base_url": os.environ.get("NEBIUS_BASE_URL") or "https://api.tokenfactory.nebius.com/v1"}

    def start(self, body: dict) -> LiveRun:
        seed = int(body.get("seed", 0))
        config = str(body.get("config", "improved+rule"))
        if "nemotron" in config:
            if not self.ai_status()["ai_available"]:
                raise ValueError("Nemotron is not available: set NEBIUS_API_KEY and HALO_SUPERVISOR_MODEL")
            # --model takes a model id (reasoning off) or a full spec such as nemotron-think:<model id>.
            spec = self.model if self.model.startswith("nemotron") else f"nemotron:{self.model}"
            config = f"{split_config(config)[0]}+{spec}"
        if body.get("mission"):
            scenario = from_mission(body["mission"], seed, body.get("mission_text", ""))
        else:
            name = body.get("scenario", "forest_trail")
            if name not in SCENARIOS:
                raise ValueError(f"unknown scenario {name!r}")
            n = body.get("n")
            scenario = make(name, seed, n=int(n)) if n else make(name, seed)
            if scenario.wildfire is not None and body.get("approval") in ("auto", "manual", "modelled"):
                # "auto": the station's fixed rules send each checked alert at once; "manual": alerts wait for
                # the incident commander's tap on the page.
                scenario.wildfire.alerts.approval = body["approval"]
        kw = {}
        if "nemotron" in config:
            log = self.evidence_dir / "dashboard_calls.jsonl"
            kw = {"log_path": str(log)}
            if self.cache_path:
                kw.update(cache_path=str(self.cache_path), cache_mode="record" if self.ai_status()["mode"] == "live" else "replay")
        with self.lock:
            for rid in [r for r, run in self.runs.items() if run.sim.ended or run.stop]:
                del self.runs[rid]
            while len(self.runs) >= MAX_RUNS:
                oldest = min(self.runs.values(), key=lambda r: r.started)
                oldest.stop = True
                del self.runs[oldest.id]
            run = LiveRun(scenario, config, seed, kw)
            run.speed = float(body.get("speed", 1.0))
            self.runs[run.id] = run
        run.thread.start()
        return run


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def make_handler(state: State, host: str = "127.0.0.1"):
    """The request handler class. Routes:

    GET  /, /static/<path>             the page and its files
    GET  /results/<file>               a file from the results folder (charts)
    GET  /api/status                   key, model and AI mode, and the live run ids
    GET  /api/scenarios                name, title, description, n and use case of each scenario
    GET  /api/results                  benchmark summary, chart names, REPORT.md, mission-compiler scores
    GET  /api/evidence                 the last ``halo doctor`` check
    GET  /api/runs/<id>/stream         Server-Sent Events: hello, frame, event, directive, ai, end
    GET  /api/runs/<id>/export         the run's result with its time series
    POST /api/runs                     {scenario, n} or {mission, mission_text}, with config, seed, speed;
                                       the wildfire valley also takes approval: "auto", "manual" or "modelled"
    POST /api/runs/<id>/inject         {kind, ...}: motor_failure, gust, blackout or directive
    POST /api/runs/<id>/control        {speed, paused, stop}
    POST /api/runs/<id>/approve        {alert}: approve a checked evacuation alert (simulated phones only)
    POST /api/runs/<id>/cancel         {alert}: cancel a sent evacuation alert (simulated phones only)
    POST /api/runs/<id>/allclear       {zone}: give a zone (its code, or index) the all-clear (simulated phones only)
    POST /api/compile                  {text}: the mission compiler

    Every POST must be JSON from the dashboard's own page (see ``_not_from_this_page``).
    """
    hosts = LOCAL_HOSTS | {host}

    class Handler(BaseHTTPRequestHandler):
        server_version = "halo-systems"

        def _not_from_this_page(self) -> str | None:
            """A POST can spend model credits, so only the dashboard's own page may send one. Another web
            page can make the browser POST here, but it cannot send JSON without the server's consent,
            set a foreign Origin to match, or (after DNS rebinding) make the Host one of ours."""
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return "requests must be JSON"
            netloc = self.headers.get("Host") or ""
            if host not in ("0.0.0.0", "::") and urlparse("//" + netloc).hostname not in hosts:
                return "unexpected Host header"
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != netloc:
                return "cross-origin requests are refused"
            return None

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _json(self, obj, status=HTTPStatus.OK):
            data = json.dumps(obj, default=_default).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, msg, status=HTTPStatus.BAD_REQUEST):
            self._json({"error": msg}, status)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ValueError("request too large")
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw or b"{}")

        def _file(self, path: Path):
            if not path.is_file():
                return self._error("not found", HTTPStatus.NOT_FOUND)
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                ctype = "text/javascript"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            try:
                if not parts:
                    return self._file(STATIC / "index.html")
                if parts[0] == "static":
                    target = (STATIC / "/".join(parts[1:])).resolve()
                    if STATIC.resolve() not in target.parents:
                        return self._error("not found", HTTPStatus.NOT_FOUND)
                    return self._file(target)
                if parts[0] == "results" and len(parts) == 2:
                    target = (state.results_dir / parts[1]).resolve()
                    if state.results_dir.resolve() not in target.parents:
                        return self._error("not found", HTTPStatus.NOT_FOUND)
                    return self._file(target)
                if parts[:2] == ["api", "status"]:
                    return self._json({**state.ai_status(), "runs": list(state.runs)})
                if parts[:2] == ["api", "scenarios"]:
                    out = []
                    for name in SCENARIOS:
                        sc = make(name, 0)
                        out.append({"name": name, "title": sc.title, "description": sc.description, "n": sc.n,
                                    "use_case": sc.use_case})
                    return self._json(out)
                if parts[:2] == ["api", "results"]:
                    return self._json(_results(state.results_dir))
                if parts[:2] == ["api", "evidence"]:
                    return self._json(_evidence(state.evidence_dir))
                if parts[:2] == ["api", "runs"] and len(parts) == 4:
                    run = state.runs.get(parts[2])
                    if run is None:
                        return self._error("no such run", HTTPStatus.NOT_FOUND)
                    if parts[3] == "stream":
                        return self._stream(run)
                    if parts[3] == "export":
                        return self._json(run.export())
                return self._error("not found", HTTPStatus.NOT_FOUND)
            except (BrokenPipeError, ConnectionResetError):
                return None

        def do_POST(self):
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            refused = self._not_from_this_page()
            if refused:
                return self._error(refused, HTTPStatus.FORBIDDEN)
            try:
                body = self._body()
                if parts == ["api", "runs"]:
                    run = state.start(body)
                    return self._json(run.hello())
                if parts[:2] == ["api", "runs"] and len(parts) == 4:
                    run = state.runs.get(parts[2])
                    if run is None:
                        return self._error("no such run", HTTPStatus.NOT_FOUND)
                    if parts[3] == "inject":
                        kind = body.pop("kind", "")
                        with run.lock:
                            info = run.sim.inject(kind, **body)
                        return self._json(info)
                    if parts[3] == "approve":
                        # The incident commander approves a checked alert; it then goes to the simulated phones.
                        if run.sim.wildfire is None:
                            return self._error("this run has no alerts")
                        alert = body.get("alert")
                        if not isinstance(alert, int) or isinstance(alert, bool):
                            return self._error("alert must be an alert number")
                        with run.lock:
                            if run.sim.ended or run.stop:
                                return self._json({"ok": False, "reason": "the flight has ended"})
                            out = run.sim.wildfire.approve(run.sim, alert, by="incident commander (dashboard)")
                        return self._json(out)
                    if parts[3] in ("cancel", "allclear"):
                        # The incident commander cancels a sent alert, or gives a zone the all-clear (simulated phones).
                        if run.sim.wildfire is None:
                            return self._error("this run has no alerts")
                        key = "alert" if parts[3] == "cancel" else "zone"
                        what = body.get(key)
                        if isinstance(what, bool) or not isinstance(what, (int, str)) or (key == "alert" and not isinstance(what, int)):
                            return self._error(f"{key} must be an alert number" if key == "alert" else "zone must be a zone code")
                        with run.lock:
                            if run.sim.ended or run.stop:
                                return self._json({"ok": False, "reason": "the flight has ended"})
                            wf = run.sim.wildfire
                            if key == "zone" and isinstance(what, int):     # a zone's index stands for its code
                                what = wf.zones[what]["code"] if 0 <= what < len(wf.zones) else ""
                            act = wf.cancel if parts[3] == "cancel" else wf.all_clear
                            out = act(run.sim, what, by="incident commander (dashboard)")
                        return self._json(out)
                    if parts[3] == "control":
                        if "speed" in body:
                            run.speed = float(min(20.0, max(0.1, body["speed"])))
                        if "paused" in body:
                            run.paused = bool(body["paused"])
                        if body.get("stop"):
                            run.stop = True
                        return self._json({"speed": run.speed, "paused": run.paused})
                if parts == ["api", "compile"]:
                    return self._json(_compile(state, str(body.get("text", ""))[:2000]))
                return self._error("not found", HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                return self._error(str(exc))
            except (BrokenPipeError, ConnectionResetError):
                return None

        def _stream(self, run: LiveRun):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q, backlog = run.subscribe()
            try:
                # Replay what already happened so a late subscriber has the full log; anything published
                # after it subscribed comes through its queue, so nothing arrives twice.
                for kind, data in backlog:
                    self._send(kind, data)
                while True:
                    try:
                        kind, data = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    self._send(kind, data)
                    if kind == "end":
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                run.unsubscribe(q)

        def _send(self, kind, data):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, default=_default)}\n\n".encode())
            self.wfile.flush()

    return Handler


def _default(x):
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    return str(x)


def _compile(state: State, text: str) -> dict:
    from ..ai.mission import compile_with_model, harmful, regex_baseline
    if not text.strip():
        raise ValueError("type a mission first")
    status = state.ai_status()
    # Replay mode (a cache, no key) can re-fly recorded flights, but a mission is a new request: it needs a key.
    if status["ai_available"] and status["mode"] == "live":
        from ..ai.client import NebiusClient, NebiusError
        client = NebiusClient(timeout_s=60.0, max_retries=2, log_path=state.evidence_dir / "dashboard_calls.jsonl")
        try:
            kind, _, model_id = state.model.partition(":") if state.model.startswith("nemotron") else ("", "", state.model)
            spec, problems, rec = compile_with_model(text, client, model_id, think=kind == "nemotron-think")
            return {"source": "nemotron" if rec else "guard", "model": model_id, "spec": spec, "problems": problems,
                    "call": rec}
        except NebiusError as exc:
            spec = regex_baseline(text)
            return {"source": "regex-baseline", "spec": spec, "problems": [f"Nemotron call failed ({exc.reason}); used the regex baseline"]}
    spec = regex_baseline(text)
    if status["mode"] == "replay":
        note = ("Without a key the dashboard can only replay recorded Nemotron flights; a new mission is a new call, "
                "so the regex baseline parsed this. Set NEBIUS_API_KEY to compile with Nemotron.")
    else:
        note = "Nemotron is not configured here (no key or model), so the regex baseline parsed this."
    return {"source": "regex-baseline", "spec": spec, "problems": [note] if not harmful(text) else []}


def _results(results_dir: Path) -> dict:
    out = {"available": False}
    summary = results_dir / "summary.json"
    if summary.exists():
        out["available"] = True
        out["summary"] = json.loads(summary.read_text())
        keys = [k.split("|", 1) for k in out["summary"] if "|" in k]
        out["scenarios"] = sorted({sc for sc, _ in keys}, key=_scenario_order)
        out["configs"] = [{"id": c, "label": short_label(c)} for c in _config_order({c for _, c in keys})]
        out["charts"] = sorted(p.name for p in results_dir.glob("*.png"))
        report = results_dir / "REPORT.md"
        out["report_md"] = report.read_text() if report.exists() else ""
    # One file per model run, e.g. mission_eval_nano.json; the regex baseline appears once per prompt set.
    for path in sorted(results_dir.glob("mission_eval*.json")):
        data = json.loads(path.read_text())
        name = "Nemotron " + (data.get("model") or "?").split("/")[-1] + (", reasoning on" if data.get("reasoning") else "")
        for set_name, v in data.get("sets", {}).items():
            rows = out.setdefault("mission_eval", {}).setdefault(set_name, {})
            for who, summ in v["summary"].items():
                rows["regex baseline" if who == "baseline" else name] = summ
    return out


def _evidence(evidence_dir: Path) -> dict:
    p = evidence_dir / "nebius_e2e.json"
    if not p.exists():
        return {"available": False}
    return {"available": True, **json.loads(p.read_text())}


def serve(host: str = "127.0.0.1", port: int = 8765, results_dir: str = "results", evidence_dir: str = "evidence",
          model: str | None = None, cache: str | None = None, open_browser: bool = False) -> None:
    model, cache = replay_defaults(results_dir, model, cache)
    state = State(Path(results_dir), Path(evidence_dir), model, Path(cache) if cache else None)
    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(state, host))
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"Halo Systems dashboard on {url} (Ctrl+C to stop)")
    st = state.ai_status()
    print(f"Nemotron: {st['mode'] + ', model ' + st['model'] if st['ai_available'] else 'not configured (rule supervisor and regex compiler only)'}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
