"""Live dashboard: a small standard-library web server streaming a running simulation.

``swarmlab serve`` starts it (default http://127.0.0.1:8765). The browser
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

from ..eval.runner import make_controller, make_supervisor, split_config
from ..eval.scenarios import SCENARIOS, from_mission, make
from ..sim.body import DroneBody
from ..sim.engine import STATUS_NAMES, Simulation
from ..supervisor.base import Decision, Supervisor

STATIC = Path(__file__).with_name("static")
FRAME_HZ = 10.0
MAX_RUNS = 4


class AsyncSupervisor(Supervisor):
    """Runs a slow supervisor on a worker thread; the simulation never waits for it."""

    def __init__(self, inner: Supervisor):
        self.inner = inner
        self.name = inner.name
        self.label = inner.label
        self.uses_ai = inner.uses_ai
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.requested_t = None

    def decide(self, summary, current, t):
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
    def __init__(self, scenario, config: str, seed: int, supervisor_kw: dict | None = None):
        ctrl_spec, sup_spec = split_config(config)
        body = DroneBody()
        sup = make_supervisor(sup_spec, **(supervisor_kw or {}))
        # Replayed calls return at once, so they run in step with the simulation, exactly as in the benchmark.
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
        self.subscribers: list[queue.Queue] = []
        self.sent_events = 0
        self.sent_directives = 1
        self.sent_ai = 0
        self.stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.started = time.time()

    # -- streaming ------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock:
            self.subscribers.append(q)
            q.put(("hello", self.hello()))
        return q

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
                "altitude_max_m": sc.altitude_max_m}

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
                self.publish("frame", self.frame())
                self._flush_logs()
        self._flush_logs()
        self.publish("end", {"metrics": sim.result().metrics if sim.series["t"] else {}})

    def _flush_logs(self) -> None:
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
            "collisions": int(sim.collisions + sim.obstacle_crashes + sim.ground_crashes),
            "progress_m": round(float(sim.ctrl.plan.s), 1),
            "path_m": round(float(sim.path.length), 1),
            "leader_speed": round(float(sim.ctrl.plan.speed_now), 2),
            "battery_mean": round(float(np.mean(100 * sim.battery_wh[sim.active_mask()] / sim.capacity_wh)), 1) if sim.active_mask().any() else 0.0,
            "near_misses": int(sim.near_miss_events),
        }
        f["directive"] = {k: v for k, v in sim.directive.to_dict().items()}
        f["heading"] = round(float(sim.ctrl.plan.heading), 3)
        f["ai_pending"] = bool(self.async_sup and self.async_sup.future is not None)
        return f

    def export(self) -> dict:
        with self.lock:
            res = self.sim.result()
        return {"config": self.config, "seed": self.seed, **res.to_dict(include_frames=False, include_series=True)}


class State:
    def __init__(self, results_dir: Path, evidence_dir: Path, model: str | None, cache_path: Path | None = None):
        self.runs: dict[str, LiveRun] = {}
        self.results_dir = results_dir
        self.evidence_dir = evidence_dir
        self.model = model or os.environ.get("SWARMLAB_SUPERVISOR_MODEL") or None
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
                raise ValueError("Nemotron is not available: set NEBIUS_API_KEY and SWARMLAB_SUPERVISOR_MODEL")
            config = f"{split_config(config)[0]}+nemotron:{self.model}"
        if body.get("mission"):
            scenario = from_mission(body["mission"], seed, body.get("mission_text", ""))
        else:
            name = body.get("scenario", "forest_trail")
            if name not in SCENARIOS:
                raise ValueError(f"unknown scenario {name!r}")
            scenario = make(name, seed)
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


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = "swarmlab"

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
            q = run.subscribe()
            try:
                # Replay what already happened so a late subscriber has the full log.
                for e in list(run.sim.events):
                    self._send("event", e)
                for d in list(run.sim.directives[1:]):
                    self._send("directive", d)
                for a in list(run.sim.ai_calls):
                    self._send("ai", a)
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
    if status["ai_available"]:
        from ..ai.client import NebiusClient, NebiusError
        client = NebiusClient(timeout_s=60.0, max_retries=2, log_path=state.evidence_dir / "dashboard_calls.jsonl")
        try:
            spec, problems, rec = compile_with_model(text, client, state.model)
            return {"source": "nemotron" if rec else "guard", "model": state.model, "spec": spec, "problems": problems,
                    "call": rec}
        except NebiusError as exc:
            spec = regex_baseline(text)
            return {"source": "regex-baseline", "spec": spec, "problems": [f"Nemotron call failed ({exc.reason}); used the regex baseline"]}
    spec = regex_baseline(text)
    note = "Nemotron is not configured here (no key or model), so the regex baseline parsed this."
    return {"source": "regex-baseline", "spec": spec, "problems": [note] if not harmful(text) else []}


def _results(results_dir: Path) -> dict:
    out = {"available": False}
    summary = results_dir / "summary.json"
    if summary.exists():
        out["available"] = True
        out["summary"] = json.loads(summary.read_text())
        out["charts"] = sorted(p.name for p in results_dir.glob("*.png"))
        report = results_dir / "REPORT.md"
        out["report_md"] = report.read_text() if report.exists() else ""
    mission = results_dir / "mission_eval.json"
    if mission.exists():
        data = json.loads(mission.read_text())
        out["mission_eval"] = {k: v["summary"] for k, v in data.get("sets", {}).items()}
    return out


def _evidence(evidence_dir: Path) -> dict:
    p = evidence_dir / "nebius_e2e.json"
    if not p.exists():
        return {"available": False}
    return {"available": True, **json.loads(p.read_text())}


def serve(host: str = "127.0.0.1", port: int = 8765, results_dir: str = "results", evidence_dir: str = "evidence",
          model: str | None = None, cache: str | None = None, open_browser: bool = False) -> None:
    state = State(Path(results_dir), Path(evidence_dir), model, Path(cache) if cache else None)
    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"SwarmLab dashboard on {url} (Ctrl+C to stop)")
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
