"""Settings read from the environment.

Each setting is read as ``HALO_<NAME>``. The name the project used before it
was renamed Halo Systems, ``SWARMLAB_<NAME>``, is accepted too, so an existing
environment (and its spend ledger) keeps working. An empty value counts as
unset.
"""

from __future__ import annotations

import os

PREFIXES = ("HALO_", "SWARMLAB_")


def env(name: str, default: str | None = None) -> str | None:
    """The value of ``HALO_<name>``, else ``SWARMLAB_<name>``, else ``default``."""
    for prefix in PREFIXES:
        value = os.environ.get(prefix + name)
        if value not in (None, ""):
            return value
    return default
