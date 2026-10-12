"""Record and replay of model calls, so benchmark runs can be reproduced exactly.

``record`` mode makes live calls and stores each reply under a hash of the
request (model, messages, temperature). ``replay`` mode answers from the
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


def request_key(model: str, messages: list[dict], temperature: float) -> str:
    blob = json.dumps({"model": model, "messages": messages, "temperature": temperature}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class CallCache:
    def __init__(self, path: str | Path, mode: str = "record"):
        if mode not in ("record", "replay", "off"):
            raise ValueError("cache mode must be record, replay or off")
        self.path = Path(path)
        self.mode = mode
        self.entries: dict[str, dict] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    e = json.loads(line)
                    self.entries[e["key"]] = e

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, content: str, record: dict) -> None:
        if self.mode != "record":
            return
        entry = {"key": key, "content": content, "record": record}
        with self._lock:
            self.entries[key] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
