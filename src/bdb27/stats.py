"""Shared uncertainty helpers for Stage 06: a cluster bootstrap of a median."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

MIN_BOOTSTRAP_REPS = 2000


def seed_from_label(label: str, base: int) -> int:
    """Deterministic RNG seed from a label (same scheme as envelope._seed)."""
    digest = int(hashlib.md5(label.encode()).hexdigest()[:8], 16)
    return int((base + digest) % (2 ** 32))


def cluster_bootstrap_median(values, clusters, *, reps, ci_level, label,
                             base_seed) -> dict:
    """Percentile bootstrap of a median, resampling clusters (players).

    Turns repeat within a player, so the resampling unit is the player, not the
    turn. Every replicate draws ``n_players`` players with replacement and
    concatenates all of the drawn players' turns in draw order. The interval is
    the ``(1-ci)/2`` and ``1-(1-ci)/2`` percentiles of those replicate medians
    -- a bootstrap interval, never a posterior.

    A cell is estimable only with at least one turn and at least two players;
    otherwise the median and both bounds are NaN and the caller must not draw.
    """
    if int(reps) < MIN_BOOTSTRAP_REPS:
        raise ValueError(
            f"cluster_bootstrap_median: reps must be at least "
            f"{MIN_BOOTSTRAP_REPS}, got {reps}")
    values = np.asarray(values, dtype=float)
    clusters = np.asarray(clusters)
    if len(values) != len(clusters):
        raise ValueError(
            "cluster_bootstrap_median: values and clusters must be the same "
            f"length, got {len(values)} and {len(clusters)}")

    keep = ~(pd.isna(values) | pd.isna(clusters))
    values = values[keep]
    clusters = clusters[keep]
    n_turns = int(len(values))
    players = pd.unique(clusters)
    n_players = int(len(players))
    seed = seed_from_label(label, base_seed)
    estimable = n_turns > 0 and n_players >= 2

    result = {
        "median": float("nan"),
        "ci_low": float("nan"),
        "ci_high": float("nan"),
        "ci_level": float(ci_level),
        "reps": int(reps),
        "seed": int(seed),
        "n_turns": n_turns,
        "n_players": n_players,
        "estimable": estimable,
    }
    if not estimable:
        return result

    # Player -> the positional indices of that player's turns in kept order.
    groups = {pid: np.flatnonzero(clusters == pid) for pid in players}
    rng = np.random.default_rng(seed)
    reps = int(reps)
    draws = np.empty(reps, dtype=float)
    for r in range(reps):
        pick = rng.choice(players, size=n_players, replace=True)
        idx = np.concatenate([groups[pid] for pid in pick])
        draws[r] = np.median(values[idx])
    alpha = (1.0 - float(ci_level)) / 2.0
    ci_low, ci_high = np.quantile(draws, [alpha, 1.0 - alpha])

    result.update({
        "median": float(np.median(values)),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
    })
    return result
