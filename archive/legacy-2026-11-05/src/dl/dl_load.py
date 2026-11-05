"""dl_load — Part 2 cumulative-load definitions (prereg §7, design §5.4).

Both load definitions are computed on the same snap sequence, with the same
recency weighting and the same number of tuned parameters:

* **absolute load** — cumulative yards accumulated above a fixed speed
  threshold ``tau_abs`` on *earlier* snaps in the same game;
* **relative load** — cumulative yards accumulated above
  ``fraction * v_player(t_match)``, where ``v_player`` is the shrunken Combine
  curve.

To guarantee the two definitions differ *only* in where the threshold comes
from, every arithmetic path in this module goes through one internal routine,
:func:`_weighted_sum`.  The caller supplies the distance-above-threshold
column; this module never re-derives it, so no rounding difference can leak in
between definitions.

Contract notes
--------------
* ``snaps`` is a list of mappings in **chronological order**: earliest snap
  first, so ``seconds_ago`` is strictly *decreasing*.  Unsorted (or tied)
  input raises ``ValueError`` instead of silently summing the wrong snap.
* The first snap's load is ``0.0``, never ``NA``: a load of zero is a
  meaningful measurement (no earlier work in the game).
* Distances above threshold must be ``>= 0`` — a negative value means the
  caller passed the wrong column or the wrong threshold, not a real deficit.
* The flat ``absolute_load`` / ``relative_load`` output columns stay ``None``
  until a threshold is frozen on a training fold (prereg §7 tuning rule); see
  :func:`flat_load_column`.

The pure arithmetic reads no config.  Only :func:`equal_tuning_budget` (grid
lengths) and :func:`add_load_columns` (half-life, window, frozen threshold)
inspect the parsed ``config.yaml``.

Table-level hook
----------------
:func:`add_load_columns` is the one entry point ``dl_snaps`` calls lazily.  It
turns the unordered snap table into per-rusher chronological sequences and
materialises every column in ``LOAD_COLUMNS``:

* ``seconds_since_last_snap`` — the rest covariate: the clock gap to the
  rusher's previous snap in the same game (``NA`` on the first snap, and
  when a clock reading is missing).
* ``total_distance_before`` — the unweighted distance benchmark: yards
  travelled on *earlier* snaps of the same game.  Per-snap distance is
  measured over the fixed post-snap window (``game.post_snap_window_s``), from
  the two matched in-game speeds; a snap without both speeds contributes
  nothing and is never counted as zero.  The first snap of a sequence is
  ``0.0``; a later snap whose earlier snaps were *all* unmeasured is ``NA``
  ("no measured work" is not "no work").  The weighted columns follow the same
  availability rule.
* ``absolute_load`` / ``relative_load`` — the flat columns.  They stay ``NA``
  until a threshold is frozen on a training fold (prereg §7), exactly as
  :func:`flat_load_column` specifies; pass ``load.chosen.absolute_speed_yds``
  (and, for the relative column, ``load.chosen.relative_fraction`` plus a
  per-row ``v_ceiling_yds`` column) to populate them.
* ``snaps_in_drive`` — the count of the rusher's earlier snaps in the same
  drive.  The snap table carries no drive key, so this is ``NA`` unless the
  frame supplies ``drive_id``; it is never back-filled from the game sequence.

Causal weighting: the prereg definition is the load *before* snap *s*, so
each snap is evaluated against its own clock, not against the last snap of the
game.  That is why the sequence routine re-bases ``seconds_ago`` on the focal
snap and reuses :func:`_weighted_sum` (the single arithmetic path) rather than
calling :func:`absolute_load`, whose ``seconds_ago`` is a single fixed
reference.  Zero gaps are permitted: ``game_clock`` has one-second resolution,
so consecutive plays can share a reading, and a zero gap is a real zero gap.
"""

from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

__all__ = [
    "ABS_DISTANCE_KEY",
    "GRID_KINDS",
    "LOAD_COLUMN_ORDER",
    "REL_DISTANCE_KEY",
    "TIME_KEY",
    "add_load_columns",
    "absolute_load",
    "ceiling_speed",
    "equal_tuning_budget",
    "flat_load_column",
    "grid_label",
    "load_thresholds_from_cfg",
    "posterior_load",
    "recency_weight",
    "relative_load",
    "speed_at",
    "threshold_grid_loads",
]

#: Column holding the time since the snap, in seconds (larger = earlier).
TIME_KEY = "seconds_ago"
#: Distance column already computed against the absolute speed threshold.
ABS_DISTANCE_KEY = "distance_above_threshold_yds"
#: Distance column already computed against ``fraction * v_ceiling``.
REL_DISTANCE_KEY = "distance_above_fraction_yds"

#: Supported grid kinds; the name doubles as the output column prefix.
GRID_KINDS = ("absolute", "relative")

#: Every column :func:`add_load_columns` owns, in emitted order.  Must stay in
#: sync with ``dl_snaps.LOAD_COLUMNS``; the hook asserts it emits all of them.
LOAD_COLUMN_ORDER = (
    "absolute_load",
    "relative_load",
    "total_distance_before",
    "snaps_in_drive",
    "seconds_since_last_snap",
)

#: League clock constants used to convert ``quarter`` + ``game_clock`` into a
#: monotone game time.  These are rules of the game, not analysis knobs.
REGULATION_QUARTER_S = 900.0
OVERTIME_QUARTER_S = 600.0

_STAMP_KEY = "threshold_yds"
_REL_TOL = 1e-9


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _positive(name: str, value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(out) or out <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return out


def _non_negative(name: str, value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(out) or out < 0.0:
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
    return out


def _check_ordering(snaps: Sequence[Mapping], time_key: str = TIME_KEY) -> list[float]:
    """Return ``seconds_ago`` per snap; raise unless strictly decreasing."""
    times: list[float] = []
    for i, snap in enumerate(snaps):
        if time_key not in snap:
            raise KeyError(f"snap {i} has no {time_key!r} key")
        secs = float(snap[time_key])
        if not math.isfinite(secs) or secs < 0.0:
            raise ValueError(
                f"snap {i}: {time_key} must be finite and >= 0, got {snap[time_key]!r}"
            )
        if times and not secs < times[-1]:
            raise ValueError(
                "snaps must be in chronological order (earliest first, strictly "
                f"decreasing {time_key}); snap {i} has {secs!r} after {times[-1]!r}"
            )
        times.append(secs)
    return times


def _column(snaps: Sequence[Mapping], key: str) -> list[float]:
    out: list[float] = []
    for i, snap in enumerate(snaps):
        if key not in snap:
            raise KeyError(f"snap {i} has no {key!r} key")
        value = float(snap[key])
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"snap {i}: {key} must be finite and >= 0, got {snap[key]!r}")
        out.append(value)
    return out


def _assert_threshold_stamp(
    snaps: Sequence[Mapping], threshold: float, key: str = _STAMP_KEY
) -> None:
    """If snaps carry their own threshold, it must match the one requested."""
    for i, snap in enumerate(snaps):
        if key in snap:
            stamped = float(snap[key])
            if not math.isclose(stamped, threshold, rel_tol=_REL_TOL, abs_tol=1e-12):
                raise ValueError(
                    f"snap {i}: {key}={stamped!r} disagrees with requested "
                    f"threshold {threshold!r}"
                )


# --------------------------------------------------------------------------- #
# 1. Recency weighting
# --------------------------------------------------------------------------- #
def recency_weight(seconds_ago: float, half_life_s: float) -> float:
    """``0.5 ** (seconds_ago / half_life_s)`` — exponential recency decay.

    The weight depends on the age of the *contributing* snap, so an earlier
    snap contributes less than a recent one.
    """
    half_life = _positive("half_life_s", half_life_s)
    age = _non_negative("seconds_ago", seconds_ago)
    return 0.5 ** (age / half_life)


# --------------------------------------------------------------------------- #
# Shared arithmetic — the single path used by both definitions
# --------------------------------------------------------------------------- #
def _weighted_sum(values: Sequence[float], weights: Sequence[float]) -> list[float]:
    """Load of snap ``i`` = sum over ``j < i`` of ``weight[j] * value[j]``.

    ``out[0]`` is exactly ``0.0`` (no earlier work), not ``NA``.  The current
    snap never contributes to its own load.
    """
    if len(values) != len(weights):
        raise ValueError(
            f"values/weights length mismatch: {len(values)} != {len(weights)}"
        )
    out: list[float] = []
    accumulated = 0.0
    for value, weight in zip(values, weights):
        out.append(accumulated)
        accumulated += weight * value
    return out


def _load_from_values(
    snaps: Sequence[Mapping],
    values: Sequence[float],
    half_life_s: float,
    time_key: str = TIME_KEY,
) -> list[float]:
    times = _check_ordering(snaps, time_key)
    weights = [recency_weight(secs, half_life_s) for secs in times]
    return _weighted_sum(values, weights)


# --------------------------------------------------------------------------- #
# 2./3. Absolute and relative load
# --------------------------------------------------------------------------- #
def absolute_load(
    snaps: Sequence[Mapping],
    threshold_yds: float,
    half_life_s: float,
    distance_key: str = ABS_DISTANCE_KEY,
    *,
    time_key: str = TIME_KEY,
) -> list[float]:
    """Cumulative yards above a fixed speed threshold, recency-weighted.

    ``snaps`` must already carry ``distance_key`` computed against
    ``threshold_yds``; the threshold is validated here so a mismatched column
    fails loudly.  Returns one load per snap, first value ``0.0``.
    """
    threshold = _positive("threshold_yds", threshold_yds)
    _assert_threshold_stamp(snaps, threshold)
    values = _column(snaps, distance_key)
    return _load_from_values(snaps, values, half_life_s, time_key)


def relative_load(
    snaps: Sequence[Mapping],
    fraction: float,
    half_life_s: float,
    v_ceiling_yds: float,
    distance_key: str = REL_DISTANCE_KEY,
    *,
    time_key: str = TIME_KEY,
) -> list[float]:
    """Cumulative yards above ``fraction * v_ceiling``, recency-weighted.

    Identical arithmetic to :func:`absolute_load` — same weights, same
    :func:`_weighted_sum` — differing only in that the threshold is a fraction
    of the player's own ceiling speed rather than a fixed yardage.
    """
    frac = _positive("fraction", fraction)
    if frac > 1.0:
        raise ValueError(f"fraction must be in (0, 1], got {fraction!r}")
    ceiling = _positive("v_ceiling_yds", v_ceiling_yds)
    _assert_threshold_stamp(snaps, frac * ceiling)
    values = _column(snaps, distance_key)
    return _load_from_values(snaps, values, half_life_s, time_key)


# --------------------------------------------------------------------------- #
# 4. Combine curve helpers
# --------------------------------------------------------------------------- #
def speed_at(vmax: float, tau: float, t: float) -> float:
    """``vmax * (1 - exp(-t / tau))`` — the fitted Combine acceleration curve."""
    peak = _positive("vmax", vmax)
    time_constant = _positive("tau", tau)
    elapsed = float(t)
    if not math.isfinite(elapsed):
        raise ValueError(f"t must be finite, got {t!r}")
    return peak * (1.0 - math.exp(-elapsed / time_constant))


def ceiling_speed(vmax: float, tau: float, t: float) -> float:
    """Alias of :func:`speed_at` with the physical ``t >= 0`` validation."""
    elapsed = float(t)
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise ValueError(f"t must be finite and >= 0, got {t!r}")
    return speed_at(vmax, tau, elapsed)


# --------------------------------------------------------------------------- #
# 5. Threshold grids
# --------------------------------------------------------------------------- #
def grid_label(value: float) -> str:
    """Format a grid point for a column name: ``1.5`` -> ``'1p5'``."""
    return f"{float(value):.1f}".replace(".", "p")


def threshold_grid_loads(
    snaps: Sequence[Mapping],
    grid: Sequence[float],
    kind: str,
    half_life_s: float,
    *,
    speed_key: str = "speed_yds",
    duration_key: str = "duration_s",
    v_ceiling_yds: float | None = None,
    time_key: str = TIME_KEY,
) -> dict[str, list[float]]:
    """One load column per grid point, e.g. ``{'absolute_load_1p5': [...]}``.

    Each snap is assumed to hold a representative ``speed_key`` sustained for
    ``duration_key`` seconds, so the distance above a candidate threshold is
    ``max(0, speed - threshold) * duration``.  ``kind='relative'`` requires
    ``v_ceiling_yds`` (the shrunken ceiling speed at ``t_match``).

    The returned columns are candidate columns only.  The flat
    ``absolute_load`` / ``relative_load`` column is *not* produced here — it
    stays ``None`` until a threshold is frozen (see :func:`flat_load_column`).
    """
    if kind not in GRID_KINDS:
        raise ValueError(f"kind must be one of {GRID_KINDS}, got {kind!r}")
    half_life = _positive("half_life_s", half_life_s)
    points = [float(g) for g in grid]
    if not points:
        raise ValueError("grid must not be empty")
    for prev, cur in zip(points, points[1:]):
        if not cur > prev:
            raise ValueError(f"grid must be strictly increasing, got {points!r}")

    ceiling: float | None = None
    if kind == "relative":
        if v_ceiling_yds is None:
            raise ValueError("kind='relative' requires v_ceiling_yds")
        ceiling = _positive("v_ceiling_yds", v_ceiling_yds)

    times = _check_ordering(snaps, time_key)
    speeds = _column(snaps, speed_key)
    durations = _column(snaps, duration_key)
    weights = [recency_weight(secs, half_life) for secs in times]

    out: dict[str, list[float]] = {}
    for point in points:
        threshold = point if kind == "absolute" else point * ceiling  # type: ignore[operator]
        values = [
            max(0.0, speed - threshold) * duration
            for speed, duration in zip(speeds, durations)
        ]
        out[f"{kind}_load_{grid_label(point)}"] = _weighted_sum(values, weights)
    return out


def flat_load_column(
    kind: str,
    value: float | None,
    snaps: Sequence[Mapping],
    half_life_s: float,
    *,
    v_ceiling_yds: float | None = None,
    distance_key: str | None = None,
    time_key: str = TIME_KEY,
) -> list[float] | None:
    """The flat ``absolute_load`` / ``relative_load`` column, or ``None``.

    ``value`` is the threshold selected inside a training fold.  While no
    threshold is frozen (``value is None``) the column stays ``None`` so the
    caller writes ``NA``; grid candidate columns are untouched.
    """
    if value is None:
        return None
    if kind == "absolute":
        key = distance_key or ABS_DISTANCE_KEY
        return absolute_load(snaps, value, half_life_s, key, time_key=time_key)
    if kind == "relative":
        key = distance_key or REL_DISTANCE_KEY
        if v_ceiling_yds is None:
            raise ValueError("kind='relative' requires v_ceiling_yds")
        return relative_load(
            snaps, value, half_life_s, v_ceiling_yds, key, time_key=time_key
        )
    raise ValueError(f"kind must be one of {GRID_KINDS}, got {kind!r}")


# --------------------------------------------------------------------------- #
# 6. Ceiling-uncertainty posterior
# --------------------------------------------------------------------------- #
def posterior_load(
    snaps: Sequence[Mapping],
    fraction: float,
    half_life_s: float,
    vmax: float,
    tau: float,
    se_log_vmax: float,
    se_log_tau: float,
    draws: int,
    seed: int,
    *,
    t_match_s: float = 1.0,
    speed_key: str = "speed_yds",
    duration_key: str = "duration_s",
    time_key: str = TIME_KEY,
) -> tuple[list[float], list[float]]:
    """Draw-mean relative load and its across-draw SD, per snap.

    ``vmax`` and ``tau`` are the shrunken point estimates; draws are
    log-normal on the shrunken log scale, ``log V ~ N(log vmax, se_log_vmax)``
    and ``log T ~ N(log tau, se_log_tau)``, independent (the shrunken fit does
    not report a log-scale covariance).  ``t_match_s`` is the Combine-time
    window matched to the game (prereg §7: 1.0 s or 2.0 s).

    Returns ``(mean_load, draw_sd)``, both length ``len(snaps)``, deterministic
    for a given ``seed``.  With both SEs zero this reduces exactly — to
    floating-point tolerance — to :func:`relative_load` at the point estimate.
    """
    frac = _positive("fraction", fraction)
    if frac > 1.0:
        raise ValueError(f"fraction must be in (0, 1], got {fraction!r}")
    peak = _positive("vmax", vmax)
    time_constant = _positive("tau", tau)
    se_v = _non_negative("se_log_vmax", se_log_vmax)
    se_t = _non_negative("se_log_tau", se_log_tau)
    t_match = _non_negative("t_match_s", t_match_s)

    n_draws = int(draws)
    if n_draws != draws or n_draws < 1:
        raise ValueError(f"draws must be a positive integer, got {draws!r}")

    times = _check_ordering(snaps, time_key)
    speeds = _column(snaps, speed_key)
    durations = _column(snaps, duration_key)
    weights = [recency_weight(secs, half_life_s) for secs in times]

    mu_v = math.log(peak)
    mu_t = math.log(time_constant)
    rng = random.Random(seed)

    # Welford per snap: the textbook sum-of-squares form loses all precision
    # when the across-draw SD is near zero (SE -> 0), which is exactly the
    # case the convergence check exercises.
    running_mean = [0.0] * len(snaps)
    running_m2 = [0.0] * len(snaps)
    for draw_no in range(1, n_draws + 1):
        draw_vmax = rng.lognormvariate(mu_v, se_v)
        draw_tau = rng.lognormvariate(mu_t, se_t)
        threshold = frac * speed_at(draw_vmax, draw_tau, t_match)
        values = [
            max(0.0, speed - threshold) * duration
            for speed, duration in zip(speeds, durations)
        ]
        loads = _weighted_sum(values, weights)
        for i, load in enumerate(loads):
            delta = load - running_mean[i]
            running_mean[i] += delta / draw_no
            running_m2[i] += delta * (load - running_mean[i])

    mean = list(running_mean)
    sd = [math.sqrt(max(0.0, m2 / n_draws)) for m2 in running_m2]
    return mean, sd


# --------------------------------------------------------------------------- #
# 7. Equal tuning budget
# --------------------------------------------------------------------------- #
def equal_tuning_budget(cfg: Mapping) -> int:
    """Assert both grids have the same length; return that shared length.

    Accepts either the full config (``bdb27_dl`` block present) or the DL block
    itself.  Stage 04 uses the returned count to prove the two definitions get
    equal tuning freedom (prereg §7): one candidate per grid point each.
    """
    if not isinstance(cfg, Mapping):
        raise TypeError(f"cfg must be a mapping, got {type(cfg).__name__}")
    block: Mapping = cfg["bdb27_dl"] if "bdb27_dl" in cfg else cfg
    load_cfg = block.get("load", block)
    if not isinstance(load_cfg, Mapping):
        raise TypeError("cfg['bdb27_dl']['load'] must be a mapping")

    abs_grid = load_cfg.get("absolute_speed_grid_yds")
    rel_grid = load_cfg.get("relative_fraction_grid")
    if not abs_grid or not rel_grid:
        raise ValueError(
            "cfg['bdb27_dl']['load'] must declare absolute_speed_grid_yds and "
            "relative_fraction_grid"
        )
    n_abs = len(abs_grid)
    n_rel = len(rel_grid)
    if n_abs != n_rel:
        raise ValueError(
            "load grids must have equal length for equal tuning freedom: "
            f"absolute={n_abs} ({list(abs_grid)}), relative={n_rel} ({list(rel_grid)})"
        )
    return n_abs


# --------------------------------------------------------------------------- #
# 8. Table-level hook (called lazily by dl_snaps)
# --------------------------------------------------------------------------- #
def _load_block(cfg: Mapping) -> Mapping:
    """The ``bdb27_dl`` block, or the block itself when already unwrapped."""
    if not isinstance(cfg, Mapping):
        raise TypeError(f"cfg must be a mapping, got {type(cfg).__name__}")
    block = cfg["bdb27_dl"] if "bdb27_dl" in cfg else cfg
    if not isinstance(block, Mapping):
        raise TypeError("cfg['bdb27_dl'] must be a mapping")
    return block


def load_thresholds_from_cfg(cfg: Mapping) -> dict[str, float | None]:
    """Frozen thresholds, if any: ``{'absolute_speed_yds', 'relative_fraction'}``.

    Reads ``load.chosen`` (the block ``00_thresholds`` writes into
    ``thresholds.json``), so the table hook and the model stage agree on where
    a tuned value comes from.  ``None`` means "not frozen yet" and the flat
    load column stays ``NA``.
    """
    block = _load_block(cfg)
    load_cfg = block.get("load", block)
    if not isinstance(load_cfg, Mapping):
        raise TypeError("cfg['bdb27_dl']['load'] must be a mapping")
    chosen = load_cfg.get("chosen") or block.get("chosen") or {}
    if not isinstance(chosen, Mapping):
        raise TypeError("cfg['bdb27_dl']['load']['chosen'] must be a mapping")
    out: dict[str, float | None] = {}
    for kind, key in (("absolute", "absolute_speed_yds"),
                      ("relative", "relative_fraction")):
        value = chosen.get(key)
        out[key] = None if value is None else _positive(key, value)
    if out["relative_fraction"] is not None and out["relative_fraction"] > 1.0:
        raise ValueError(
            f"relative_fraction must be in (0, 1], got {out['relative_fraction']!r}"
        )
    return out


def elapsed_game_seconds(quarter: Any, game_clock_s: Any) -> float:
    """Monotone seconds since kickoff from ``quarter`` and clock remaining.

    ``game_clock`` counts *down* inside a quarter, so the elapsed form is
    ``(quarter - 1) * length + (length - clock)``.  Overtime periods are 10
    minutes.  Returns ``nan`` when either input is missing or the clock is not
    a valid time for that period.
    """
    if quarter is None or game_clock_s is None:
        return math.nan
    try:
        q = float(quarter)
        clock = float(game_clock_s)
    except (TypeError, ValueError):
        return math.nan
    if not math.isfinite(q) or not math.isfinite(clock) or q < 1.0:
        return math.nan
    if q <= 4.0:
        length = REGULATION_QUARTER_S
        base = (q - 1.0) * length
    else:
        length = OVERTIME_QUARTER_S
        base = 4.0 * REGULATION_QUARTER_S + (q - 5.0) * length
    if clock < 0.0 or clock > length + _REL_TOL:
        return math.nan
    return base + (length - clock)


def _snap_distance(speed_1_0: Any, speed_2_0: Any, window_s: float) -> float:
    """Yards travelled over the fixed post-snap window, or ``nan``.

    Uses the two measured in-game speeds that bracket the window: the mean
    speed sustained for ``window_s``.  A missing speed makes the distance
    genuinely unavailable (``nan``), never zero.
    """
    try:
        s1 = float(speed_1_0)
        s2 = float(speed_2_0)
    except (TypeError, ValueError):
        return math.nan
    if not (math.isfinite(s1) and math.isfinite(s2)) or s1 < 0.0 or s2 < 0.0:
        return math.nan
    return 0.5 * (s1 + s2) * window_s


def _causal_prefix_sum(values: Sequence[float], times: Sequence[float],
                       half_life_s: float) -> list[float]:
    """``out[i] = sum_{j < i} w(t_i - t_j) * values[j]``; ``NA`` values skip.

    Re-bases the recency weight on the focal snap, which is what "load before
    snap *s*" means.  ``_weighted_sum`` is the shared arithmetic path; it emits
    the running total *before* each contribution, so a trailing zero-value pair
    is appended to read the complete sum.

    The sequence's first snap is exactly ``0.0`` (no earlier work — a real
    zero).  A later snap whose earlier snaps were *all* unmeasured is ``nan``:
    "no earlier measured work" must not be read as "no earlier work".
    """
    out: list[float] = []
    for i in range(len(times)):
        if i == 0:
            out.append(0.0)
            continue
        vals: list[float] = []
        wts: list[float] = []
        for j in range(i):
            value = values[j]
            if value is None or not math.isfinite(float(value)):
                continue          # unmeasured work is not zero work
            gap = times[i] - times[j]
            if gap < 0.0:         # pragma: no cover - ordering is enforced above
                raise ValueError(f"snap {i} precedes snap {j} in the sequence")
            wts.append(recency_weight(gap, half_life_s))
            vals.append(float(value))
        if not vals:
            out.append(math.nan)  # earlier snaps exist but none were measured
            continue
        out.append(_weighted_sum(vals + [0.0], wts + [1.0])[-1])
    return out


def _cumulative_total(values: Sequence[float]) -> list[float]:
    """Unweighted running total of earlier values (``NA`` values skip).

    Same availability rule as the weighted path: ``0.0`` before the first snap,
    ``nan`` once earlier snaps exist but none of them were measured.
    """
    out: list[float] = []
    total = 0.0
    measured = 0
    for value in values:
        if measured == 0 and out:
            out.append(math.nan)
        else:
            out.append(total)
        if value is not None and math.isfinite(float(value)):
            total += float(value)
            measured += 1
    return out


def _finite(value: Any) -> bool:
    """True when ``value`` converts to a finite float (``pd.NA`` is False)."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def add_load_columns(frame, cfg: Mapping, mode: str = "sample"):
    """Materialise the load / rest / sequence columns on the snap table.

    ``frame`` is one row per snap, in any order.  Every column in
    :data:`LOAD_COLUMN_ORDER` is returned, with ``NA`` where the value is
    genuinely unavailable (see the module docstring).  The input frame is never
    mutated; row order is preserved.

    ``mode`` is accepted so ``dl_snaps`` can call one hook for both provenance
    levels; the arithmetic is identical in both (TASK.md: one code path).
    """
    import pandas as pd  # local import: pure-python users need not have it

    if mode not in ("sample", "full"):
        raise ValueError(f"mode must be 'sample' or 'full', got {mode!r}")
    block = _load_block(cfg)
    load_cfg = block.get("load", block)
    half_life = _positive("recency_half_life_s",
                          load_cfg.get("recency_half_life_s", 300.0))
    game_cfg = block.get("game", {})
    window = game_cfg.get("post_snap_window_s", [1.0, 2.0])
    if len(window) != 2:
        raise ValueError(
            f"game.post_snap_window_s must hold two times, got {window!r}")
    window_s = float(window[1]) - float(window[0])
    if not math.isfinite(window_s) or window_s <= 0.0:
        raise ValueError(
            f"game.post_snap_window_s must be increasing, got {window!r}")

    required = ("game_id", "play_id", "nfl_id", "quarter", "game_clock_s")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"snap table lacks required columns {missing}")

    thresholds = load_thresholds_from_cfg(cfg)
    n = len(frame)
    out = frame.copy()
    for col in LOAD_COLUMN_ORDER:
        out[col] = pd.Series([math.nan] * n, dtype="float64")
    if n == 0:
        return out

    elapsed = [elapsed_game_seconds(q, c)
               for q, c in zip(frame["quarter"].to_numpy(),
                               frame["game_clock_s"].to_numpy())]
    s1s = (frame["in_game_speed_1_0"].to_numpy() if "in_game_speed_1_0"
           in frame.columns else [math.nan] * n)
    s2s = (frame["in_game_speed_2_0"].to_numpy() if "in_game_speed_2_0"
           in frame.columns else [math.nan] * n)
    distance = [_snap_distance(a, b, window_s) for a, b in zip(s1s, s2s)]
    rep_speed = [math.nan if not (_finite(a) and _finite(b))
                 else 0.5 * (float(a) + float(b)) for a, b in zip(s1s, s2s)]

    drive = (frame["drive_id"].to_numpy() if "drive_id" in frame.columns
             else None)
    ceiling = (frame["v_ceiling_yds"].to_numpy()
               if "v_ceiling_yds" in frame.columns else None)
    play_ids = [float(p) if _finite(p) else math.inf
                for p in frame["play_id"].to_numpy()]
    columns = {c: out.columns.get_loc(c) for c in LOAD_COLUMN_ORDER}

    # Deterministic chronological order: timed rows first, then by clock and
    # play id; rows with no clock fall back to play id order at the end.
    order = sorted(
        range(n),
        key=lambda i: (
            0 if math.isfinite(elapsed[i]) else 1,
            elapsed[i] if math.isfinite(elapsed[i]) else 0.0,
            play_ids[i],
            i,
        ),
    )

    groups: dict[tuple, list[int]] = {}
    for i in order:
        key = (frame["game_id"].iloc[i], frame["nfl_id"].iloc[i])
        groups.setdefault(key, []).append(i)

    for key in sorted(groups, key=lambda k: (str(k[0]), str(k[1]))):
        idx = groups[key]
        times = [elapsed[i] for i in idx]
        dists = [distance[i] for i in idx]
        # Rows without a clock cannot be sequenced: every sequence column for
        # this rusher-game stays NA rather than being guessed from play order.
        if not all(math.isfinite(t) for t in times):
            continue
        for pos in range(1, len(idx)):
            out.iat[idx[pos], columns["seconds_since_last_snap"]] = (
                times[pos] - times[pos - 1])
        totals = _cumulative_total(dists)
        for pos, i in enumerate(idx):
            out.iat[i, columns["total_distance_before"]] = totals[pos]

        if thresholds["absolute_speed_yds"] is not None:
            tau = thresholds["absolute_speed_yds"]
            above = [
                math.nan if not (math.isfinite(rep_speed[i])
                                 and math.isfinite(distance[i]))
                else max(0.0, rep_speed[i] - tau) * window_s
                for i in idx
            ]
            loads = _causal_prefix_sum(above, times, half_life)
            for pos, i in enumerate(idx):
                out.iat[i, columns["absolute_load"]] = loads[pos]

        if thresholds["relative_fraction"] is not None and ceiling is not None:
            frac = thresholds["relative_fraction"]
            above = []
            for i in idx:
                c, s = ceiling[i], rep_speed[i]
                if not _finite(c) or float(c) <= 0.0 or not math.isfinite(s):
                    above.append(math.nan)
                else:
                    above.append(max(0.0, s - frac * float(c)) * window_s)
            loads = _causal_prefix_sum(above, times, half_life)
            for pos, i in enumerate(idx):
                out.iat[i, columns["relative_load"]] = loads[pos]

        if drive is not None:
            counts: dict[Any, int] = {}
            for i in idx:
                d = drive[i]
                if d is None or not (_finite(d) or isinstance(d, str)):
                    continue
                seen = counts.get(d, 0)
                out.iat[i, columns["snaps_in_drive"]] = float(seen)
                counts[d] = seen + 1

    # Cast to float64 explicitly: iat assignment keeps the placeholder dtype.
    for col in LOAD_COLUMN_ORDER:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    return out
