"""Append-safe JSON-lines files.

Several processes append to the same files: benchmark workers share the
model-call cache, the call log and the spend ledger. On the shared project
folder, two appends were once seen to overlap and leave a broken line, and
every worker that then loaded the cache stopped with a parse error. So each
append takes an exclusive lock and goes out in a single write, and readers
skip a line they cannot parse instead of failing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no advisory locks; single-process use is still safe
    fcntl = None


def append(path: str | os.PathLike, obj) -> None:
    data = (json.dumps(obj) + "\n").encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read(path: str | os.PathLike, skipped: list | None = None) -> list[dict]:
    """Every parsable JSON object in the file, in order. Unparsable lines go to ``skipped``."""
    rows = []
    for line in Path(path).read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            rows.append(obj)
        elif skipped is not None:
            skipped.append(line[:120].decode("utf-8", "replace"))
    return rows
