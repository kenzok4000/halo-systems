"""Record and replay of model calls, so benchmark runs can be reproduced exactly.

``record`` mode makes live calls and stores each reply under a hash of the
request (model, messages, temperature, options). A call that failed is stored
too, with its error and latency, so a replay fails at the same point the live
run did instead of drifting onto a different path. ``replay`` mode answers from the
store only and fails loudly on a miss, so a replayed benchmark can never
silently mix in new live answers. Stored entries keep the original call
record (latency, tokens, request id), marked ``cached`` when replayed, so
reports show what the live call cost, never a made-up number.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .. import jsonl


def request_key(model: str, messages: list[dict], temperature: float, options: dict | None = None) -> str:
    """``options`` holds anything else that changes the reply (reasoning switch, token limit)."""
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
        if self.path.exists():
            for e in jsonl.read(self.path, self.skipped):
                if "key" in e:
                    self.entries[e["key"]] = e

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, content: str | None, record: dict, error: str | None = None) -> None:
        if self.mode != "record":
            return
        entry = {"key": key, "content": content, "record": record}
        if error:
            entry["error"] = error
        with self._lock:
            self.entries[key] = entry
            jsonl.append(self.path, entry)
