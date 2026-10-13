"""Optimal slot assignment (Hungarian algorithm, O(n^3)).

Assigning drones to formation slots by minimum total squared distance gives
paths that do not cross (for straight-line motion), which is why the
improved controller re-assigns after every formation change or loss.
"""

from __future__ import annotations

import numpy as np


def hungarian(cost: np.ndarray) -> np.ndarray:
    """Minimum-cost assignment of rows to columns, for any rectangular cost.

    Returns ``col_of_row`` (length = rows). With more rows than columns it
    solves the transpose, and rows left without a column get -1. Shortest
    augmenting path with potentials, the classic O(n^2 m) formulation (as
    written up on e-maxx / cp-algorithms).
    """
    cost = np.asarray(cost, dtype=float)
    n, m = cost.shape
    transposed = False
    if n > m:
        cost = cost.T
        n, m = m, n
        transposed = True
    INF = float("inf")
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)      # p[j] = row matched to column j (1-based), 0 = free
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, INF)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            free = ~used[1:]
            cur = cost[i0 - 1] - u[i0] - v[1:]
            better = free & (cur < minv[1:])
            minv[1:][better] = cur[better]
            way[1:][better] = j0
            cand = np.where(free, minv[1:], INF)
            j1 = int(np.argmin(cand)) + 1
            delta = cand[j1 - 1]
            used_idx = np.nonzero(used)[0]
            u[p[used_idx]] += delta
            v[used_idx] -= delta
            minv[1:][free] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    col_of_row = np.full(n, -1, dtype=int)
    for j in range(1, m + 1):
        if p[j] > 0:
            col_of_row[p[j] - 1] = j - 1
    if transposed:
        row_of_col = np.full(m, -1, dtype=int)
        for r, c in enumerate(col_of_row):
            row_of_col[c] = r
        return row_of_col
    return col_of_row


def assign_slots(positions: np.ndarray, slots: np.ndarray) -> np.ndarray:
    """Slot index for each drone, minimising total squared distance."""
    if len(positions) == 0:
        return np.zeros(0, dtype=int)
    d = positions[:, None, :] - slots[None, :, :]
    cost = np.sum(d * d, axis=2)
    return hungarian(cost)
