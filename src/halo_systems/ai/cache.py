"""Record and replay of model calls, so benchmark runs can be reproduced exactly.

``record`` mode answers from the store when the request is already there,
and otherwise makes a live call and stores the reply under a hash of the
request (model, messages, temperature, options). A call that failed for a
non-fatal reason is stored too, with its error and latency, so a replay fails
at the same point the live run did instead of drifting onto a different path.
``replay`` mode answers from the store only and never makes a live call, so a
replayed benchmark can never silently mix in new live answers. Stored entries
keep the original call record (latency, tokens, request id), so reports show
what the live call cost, never a made-up number.

``CallCache`` itself only stores and looks up entries. Serving a hit, failing
on a replay miss and marking a replayed answer all happen in
``NemotronSupervisor.decide`` (``supervisor/nemotron.py``): a hit sets
``meta["cached"]`` on the decision, and a replay miss raises a fatal
``NebiusError``. That stops a benchmark run (``strict=True``); otherwise the
step falls back and the error is recorded in the decision.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .. import jsonl


def request_key(model: str, messages: list[dict], temperature: float, options: dict | None = None) -> str:
    """``options`` holds anything else that changes the reply (reasoning switch, token limit).

    The key covers the exact message text, so a one-character change to a prompt turns its stored
    calls into misses. The way the key is built (these field names, the ``json.dumps`` settings and
    the cut to 32 hex characters) is fixed too: changing it turns every entry in an existing cache
    file, such as ``results/nemotron_cache.jsonl``, into a miss.
    """
    req = {"model": model, "messages": messages, "temperature": temperature}
    if options:
        req["options"] = options
    blob = json.dumps(req, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class CallCache:
    def __init__(self, path: str | Path, mode: str = "record"):
        if mode not in ("record", "replay", "off"):
            raise ValueError("cache mode must be record, replay or off")
        self.path = Path(path)
        self.mode = mode
        self.entries: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.skipped: list[str] = []      # broken lines; their calls count as misses
        # The whole file is loaded once, here. If a key appears twice, the later entry is the one served.
        if self.path.exists():
            for e in jsonl.read(self.path, self.skipped):
                if "key" in e:
                    self.entries[e["key"]] = e

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, content: str | None, record: dict, error: str | None = None) -> None:
        # Only record mode writes, so a replay leaves the file as it was. Each entry is appended as one line
        # the moment it is made, so a run that stops halfway keeps every call it made before.
        if self.mode != "record":
            return
        entry = {"key": key, "content": content, "record": record}
        if error:
            entry["error"] = error
        with self._lock:
            self.entries[key] = entry
            jsonl.append(self.path, entry)
