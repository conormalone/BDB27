"""lc_route_depth — geometric route depth as *depth at the break* (D61).

The outcome model needs a route-depth control that is not mechanically a
function of the outcome.  Realised start-to-end displacement fails that test:
over a fixed route duration it is essentially speed.  ``pass_length`` fails it
too (it is a literal ``NA`` string in 129,458 rows and, when present, tracks the
thrown ball rather than the route design).  This module therefore realises
depth from the 10 Hz tracking: the downfield displacement at the moment the
receiver breaks.

Frozen definition (``bdb27_lc.route_depth`` holds every number):

1. rotate each play into the play frame (+x downfield from ``play_direction``);
   the origin is the receiver's position at the snap frame;
2. smooth the trajectory with the existing centred rolling-median smoother
   (``bdb27_lc.thresholds.smoothing.window_frames``);
3. heading theta(t) from smoothed velocity over a 0.2 s window;
4. the stem is at least 0.8 s of post-snap motion;
5. the break is the first frame inside the stem where the cumulative |dtheta|
   over a 0.4 s window reaches 35 deg and the new heading persists within
   20 deg for at least 0.3 s; the break timestamp is the first frame of that
   sustained change (D62 relaxed 45 deg/0.3 s to 35 deg/0.4 s);
6. depth at break is the downfield displacement at that frame, in yards,
   relative to the snap position;
7. a route with no detectable break keeps ``break_found = False`` and a
   **missing** depth.  Nothing is imputed: route family carries those routes;
8. a break after the throw frame is flagged ``break_after_throw`` (the throw
   frame is the tracking ``pass_forward`` event; it is not re-derived);
9. the model enters route family (always), centred break depth, and the
   ``break_found`` indicator.  Depth is a control and is reported, never
   interpreted.

No post-snap outcome (target, result, completion) enters the definition.  The
``pass_forward`` event is used only to label the sensitivity subset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import lc_common as C

#: Columns this module needs from the tracking table.
TRACKING_COLUMNS = ("game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
                    "event")

#: Output schema of :func:`route_depth_table` (order fixed; asserted on write).
ROUTE_DEPTH_SCHEMA = [
    "game_id", "play_id", "nfl_id",
    "play_direction", "snap_time_s", "throw_time_s",
    "break_found", "break_depth_yd", "break_time_from_snap_s",
    "break_after_throw", "throw_known", "heading_before_deg",
    "heading_after_deg", "turn_deg",
    "n_frames", "median_gap_s", "start_offset_s", "flags",
]


class RouteDepthConfigError(ValueError):
    """Raised when the config block cannot support the frozen definition."""


def params(cfg: dict) -> dict:
    """Return the validated ``bdb27_lc.route_depth`` parameter block."""
    block = C.lc(cfg).get("route_depth")
    if not isinstance(block, dict):
        raise RouteDepthConfigError("config has no bdb27_lc.route_depth block")
    required = ("heading_window_s", "stem_min_s", "break_window_s",
                "break_angle_deg", "sustain_window_s", "sustain_angle_deg",
                "min_frames", "max_gap_s", "snap_event", "throw_event",
                "direction_column", "direction_right", "direction_left")
    missing = [key for key in required if key not in block]
    if missing:
        raise RouteDepthConfigError(
            f"bdb27_lc.route_depth is missing keys: {missing}")
    # The smoother is the one already used on the Combine ceiling: never a
    # second smoothing convention.
    expected_window = int(C.lc(cfg)["thresholds"]["smoothing"]["window_frames"])
    configured = int(block.get("smoothing_window_frames", expected_window))
    if configured != expected_window:
        raise RouteDepthConfigError(
            "route_depth.smoothing_window_frames must equal "
            "thresholds.smoothing.window_frames "
            f"({configured} != {expected_window})")
    return dict(block)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def _wrap(angle: np.ndarray) -> np.ndarray:
    """Wrap angles in radians to (-pi, pi]."""
    return (np.asarray(angle) + np.pi) % (2.0 * np.pi) - np.pi


def smooth_trajectory(values: np.ndarray, window_frames: int) -> np.ndarray:
    """Centred rolling median — the existing ceiling smoother, unchanged.

    A piecewise-linear path's coordinates are preserved exactly by a centred
    median at a kink of the kind a route break produces (the median of a linear
    run is the centre value), so this does not shift the break backwards.
    """
    series = pd.Series(np.asarray(values, dtype="float64"))
    return series.rolling(window=int(window_frames), center=True,
                          min_periods=1).median().to_numpy(dtype=float)


def heading(t: np.ndarray, downfield: np.ndarray, lateral: np.ndarray,
            window_s: float, min_speed_yds: float = 0.0) -> np.ndarray:
    """Heading from smoothed velocity over ``window_s`` seconds, in radians.

    Velocity is the centred displacement between ``t - window/2`` and
    ``t + window/2``, interpolated onto the actual (possibly irregular) frame
    times.  Frames whose window falls outside the track, or whose implied speed
    is below ``min_speed_yds``, get NaN — a heading is undefined there, not
    guessed.
    """
    t = np.asarray(t, dtype=float)
    half = float(window_s) / 2.0
    theta = np.full(t.shape, np.nan, dtype=float)
    if len(t) < 2:
        return theta
    valid = (t - half >= t[0]) & (t + half <= t[-1])
    if not valid.any():
        return theta
    d_plus = np.interp(t + half, t, downfield)
    d_minus = np.interp(t - half, t, downfield)
    l_plus = np.interp(t + half, t, lateral)
    l_minus = np.interp(t - half, t, lateral)
    dd = d_plus - d_minus
    dl = l_plus - l_minus
    speed = np.hypot(dd, dl) / max(window_s, 1e-9)
    theta[valid] = np.arctan2(dl[valid], dd[valid])
    theta[valid & (speed < min_speed_yds)] = np.nan
    return theta


def _circular_mean(angles: np.ndarray) -> float:
    values = angles[np.isfinite(angles)]
    if values.size == 0:
        return float("nan")
    return float(math.atan2(np.sin(values).mean(), np.cos(values).mean()))


@dataclass
class BreakResult:
    break_found: bool = False
    break_depth_yd: float = float("nan")
    break_time_from_snap_s: float = float("nan")
    heading_before_deg: float = float("nan")
    heading_after_deg: float = float("nan")
    turn_deg: float = float("nan")
    flags: list[str] = field(default_factory=list)


def detect_break(t: np.ndarray, downfield: np.ndarray, lateral: np.ndarray,
                 snap_time: float, p: dict) -> BreakResult:
    """Detect the break on an already-smoothed play-frame trajectory.

    ``t`` is absolute frame time in seconds, ``snap_time`` the snap timestamp,
    and ``downfield``/``lateral`` are the rotated, smoothed coordinates.  The
    stem requirement is evaluated in seconds after the snap.
    """
    result = BreakResult()
    t = np.asarray(t, dtype=float)
    downfield = np.asarray(downfield, dtype=float)
    lateral = np.asarray(lateral, dtype=float)
    if len(t) < int(p["min_frames"]):
        result.flags.append("too_few_frames")
        return result
    if not np.isfinite(snap_time):
        result.flags.append("no_snap_frame")
        return result

    dt = np.diff(t)
    median_dt = float(np.median(dt)) if len(dt) else float("nan")
    if not np.isfinite(median_dt) or median_dt <= 0:
        result.flags.append("bad_frame_times")
        return result

    theta = heading(t, downfield, lateral, float(p["heading_window_s"]),
                    float(p.get("min_speed_yds", 0.0)))
    if np.isfinite(theta).sum() < 4:
        result.flags.append("heading_unavailable")
        return result

    t_rel = t - snap_time
    t_end = float(t[-1])
    stem = float(p["stem_min_s"])
    break_window = float(p["break_window_s"])
    sustain = float(p["sustain_window_s"])
    break_angle = math.radians(float(p["break_angle_deg"]))
    sustain_angle = math.radians(float(p["sustain_angle_deg"]))
    max_gap = float(p["max_gap_s"])

    # |dtheta| attributed to the interval between consecutive frames; the
    # interval's timestamp is the midpoint.  (One fewer interval than frames.)
    d_theta = _wrap(theta[1:] - theta[:-1])
    mid_t = 0.5 * (t[1:] + t[:-1])

    # A gap longer than max_gap_s breaks the cumulative-turn window: do not let
    # a break claim turn that happened across missing frames.
    gap_ok = np.ones(t.shape, dtype=bool)
    if len(dt):
        gap_ok[1:] = dt <= max_gap

    for i in range(len(t)):
        if not np.isfinite(theta[i]):
            continue
        if t_rel[i] < stem:
            continue
        if t[i] + sustain > t_end:
            break
        if not gap_ok[i]:
            continue
        in_window = (mid_t > t[i] - break_window) & (mid_t <= t[i])
        if in_window.sum() == 0 or not gap_ok[i - int(in_window.sum()) + 1:i + 1].all():
            continue
        turn = float(np.nansum(np.abs(d_theta[in_window])))
        if not np.isfinite(turn) or turn < break_angle:
            continue

        sustain_mask = (t > t[i]) & (t <= t[i] + sustain)
        if sustain_mask.sum() < 2:
            continue
        future = theta[sustain_mask]
        if not np.isfinite(future).all():
            continue
        post = _circular_mean(future)
        if np.any(np.abs(_wrap(future - post)) > sustain_angle):
            continue

        # First frame of the sustained change: walk back while the heading is
        # already within the sustain tolerance of the post-break heading.
        j = i
        while j - 1 >= 0 and (t[i] - t[j - 1]) <= break_window:
            prior = theta[j - 1]
            if not np.isfinite(prior):
                break
            if abs(float(_wrap(prior - post))) > sustain_angle:
                break
            if not gap_ok[j]:
                break
            j -= 1

        result.break_found = True
        result.break_depth_yd = float(downfield[j])
        result.break_time_from_snap_s = float(t[j] - snap_time)
        result.heading_before_deg = math.degrees(float(theta[j - 1])) \
            if j - 1 >= 0 and np.isfinite(theta[j - 1]) else float("nan")
        result.heading_after_deg = math.degrees(post)
        result.turn_deg = math.degrees(float(_wrap(post - theta[j - 1]))) \
            if j - 1 >= 0 and np.isfinite(theta[j - 1]) else float("nan")
        break
    return result


# --------------------------------------------------------------------------- #
# Per-track analysis
# --------------------------------------------------------------------------- #
def analyse_track(track: pd.DataFrame, play_direction: str, cfg: dict,
                  overrides: dict | None = None,
                  play_snap_time=None, play_throw_time=None) -> dict:
    """Analyse one receiver-play track (already sorted by time).

    ``track`` needs ``time`` (parsed), ``x``, ``y``, ``s``, ``a``, ``event``.
    ``play_direction`` comes from ``player_play``; an unusable value yields a
    flagged, non-detected result rather than a guessed downfield sign.
    ``play_snap_time``/``play_throw_time`` are fallbacks for plays whose events
    are recorded on another player's rows (the tracking event is play-level).
    """
    p = params(cfg)
    if overrides:
        p = {**p, **overrides}
    out = {
        "play_direction": play_direction,
        "snap_time_s": float("nan"),
        "throw_time_s": float("nan"),
        "break_found": False,
        "break_depth_yd": float("nan"),
        "break_time_from_snap_s": float("nan"),
        "break_after_throw": False,
        "throw_known": False,
        "heading_before_deg": float("nan"),
        "heading_after_deg": float("nan"),
        "turn_deg": float("nan"),
        "n_frames": int(len(track)),
        "median_gap_s": float("nan"),
        "start_offset_s": float("nan"),
        "flags": "",
    }
    flags: list[str] = []

    if len(track) < 2:
        out["flags"] = "too_few_frames"
        return out

    times = pd.to_datetime(track["time"], errors="coerce")
    if times.isna().all():
        out["flags"] = "bad_frame_times"
        return out
    t = (times - times.iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    order = np.argsort(t, kind="stable")
    t = t[order]
    x = pd.to_numeric(track["x"], errors="coerce").to_numpy(dtype=float)[order]
    y = pd.to_numeric(track["y"], errors="coerce").to_numpy(dtype=float)[order]
    events = track["event"].astype(str).to_numpy()[order] if "event" in track \
        else np.array([""] * len(track))
    good = np.isfinite(t) & np.isfinite(x) & np.isfinite(y)
    if not good.all():
        flags.append("missing_positions")
        t, x, y, events = t[good], x[good], y[good], events[good]
    out["n_frames"] = int(len(t))
    if len(t) < int(p["min_frames"]):
        out["flags"] = ";".join(flags + ["too_few_frames"])
        return out

    dt = np.diff(t)
    out["median_gap_s"] = float(np.median(dt)) if len(dt) else float("nan")
    if np.any(dt > float(p["max_gap_s"])):
        flags.append("gap_over_max")

    direction = str(play_direction).strip().lower()
    if direction == str(p["direction_right"]).lower():
        sign = 1.0
    elif direction == str(p["direction_left"]).lower():
        sign = -1.0
    else:
        out["flags"] = ";".join(flags + ["play_direction_missing"])
        return out

    snap_mask = events == str(p["snap_event"])
    if snap_mask.any():
        snap_idx = int(np.flatnonzero(snap_mask)[0])
        snap_time = float(t[snap_idx])
    elif play_snap_time is not None and pd.notna(play_snap_time):
        snap_time = float((pd.Timestamp(play_snap_time)
                           - times.iloc[0]).total_seconds())
        snap_idx = int(np.argmin(np.abs(t - snap_time)))
        if abs(t[snap_idx] - snap_time) > float(p.get("snap_tolerance_s", 0.15)):
            flags.append("no_snap_frame")
    else:
        snap_idx = 0
        snap_time = float(t[0])
        flags.append("no_snap_frame")

    throw_mask = events == str(p["throw_event"])
    if throw_mask.any():
        throw_time = float(t[np.flatnonzero(throw_mask)[0]])
        out["throw_known"] = True
        out["throw_time_s"] = throw_time
    elif play_throw_time is not None and pd.notna(play_throw_time):
        throw_time = float((pd.Timestamp(play_throw_time)
                            - times.iloc[0]).total_seconds())
        out["throw_known"] = bool(t[0] <= throw_time <= t[-1])
        out["throw_time_s"] = throw_time if out["throw_known"] else float("nan")
        if not out["throw_known"]:
            throw_time = float("nan")
    else:
        throw_time = float("nan")

    out["snap_time_s"] = snap_time
    out["start_offset_s"] = float(t[0] - snap_time)

    # Play frame: +downfield, lateral right-handed under the same rotation.
    downfield = sign * (x - x[snap_idx])
    lateral = sign * (y - y[snap_idx])

    window = int(p.get("smoothing_window_frames",
                       C.lc(cfg)["thresholds"]["smoothing"]["window_frames"]))
    downfield = smooth_trajectory(downfield, window)
    lateral = smooth_trajectory(lateral, window)

    result = detect_break(t, downfield, lateral, snap_time, p)
    flags.extend(result.flags)
    out.update({
        "break_found": bool(result.break_found),
        "break_depth_yd": float(result.break_depth_yd),
        "break_time_from_snap_s": float(result.break_time_from_snap_s),
        "heading_before_deg": float(result.heading_before_deg),
        "heading_after_deg": float(result.heading_after_deg),
        "turn_deg": float(result.turn_deg),
    })
    if result.break_found and out["throw_known"]:
        out["break_after_throw"] = bool(
            (snap_time + result.break_time_from_snap_s) > throw_time)
    out["flags"] = ";".join(flags)
    return out


def route_depth_table(tracking: pd.DataFrame, play_direction: dict | pd.Series,
                      cfg: dict, overrides: dict | None = None,
                      play_events: dict | None = None) -> pd.DataFrame:
    """One row per (game_id, play_id, nfl_id) track in ``tracking``.

    ``play_direction`` maps ``(game_id, play_id)`` to the raw direction string;
    ``play_events`` optionally maps the same key to ``{"snap": ts,
    "throw": ts}`` for plays whose events appear on other players' rows.
    The function never drops a track silently: every input key appears in the
    output, with ``flags`` explaining anything missing.
    """
    if not set(("game_id", "play_id", "nfl_id")).issubset(tracking.columns):
        raise KeyError("tracking needs game_id, play_id, nfl_id")
    work = tracking.copy()
    work["time"] = pd.to_datetime(work["time"], errors="coerce")
    work = work.sort_values(["game_id", "play_id", "nfl_id", "time"],
                            kind="stable")
    if isinstance(play_direction, pd.Series):
        direction_map = play_direction.to_dict()
    else:
        direction_map = dict(play_direction)
    rows = []
    for (game_id, play_id, nfl_id), group in work.groupby(
            ["game_id", "play_id", "nfl_id"], sort=True):
        direction = direction_map.get((game_id, play_id), "")
        events = (play_events or {}).get((game_id, play_id), {})
        record = analyse_track(group, direction, cfg, overrides=overrides,
                               play_snap_time=events.get("snap"),
                               play_throw_time=events.get("throw"))
        rows.append({"game_id": int(game_id), "play_id": int(play_id),
                     "nfl_id": int(nfl_id), **record})
    if not rows:
        return pd.DataFrame(columns=ROUTE_DEPTH_SCHEMA)
    return pd.DataFrame(rows)[ROUTE_DEPTH_SCHEMA]


# --------------------------------------------------------------------------- #
# Reporting helpers
# --------------------------------------------------------------------------- #
def _iqr(series: pd.Series) -> tuple[float, float]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return float("nan"), float("nan")
    return float(values.quantile(0.25)), float(values.quantile(0.75))


def coverage_summary(table: pd.DataFrame, positions: pd.Series | None = None,
                     route_family: pd.Series | None = None) -> dict:
    """Coverage, after-throw rate and break-depth distribution by family.

    ``positions`` and ``route_family`` are aligned Series on the same index.
    Depth summaries use detected breaks only; no-break routes contribute
    coverage counts, never a fabricated depth.
    """
    work = table.copy()
    if positions is not None:
        work["position"] = pd.Series(positions).reindex(work.index).to_numpy()
    if route_family is not None:
        work["route_family"] = pd.Series(route_family).reindex(work.index).to_numpy()
    summary: dict = {
        "n_route_snaps": int(len(work)),
        "n_break_found": int(work["break_found"].astype(bool).sum()),
        "n_no_break": int((~work["break_found"].astype(bool)).sum()),
        "break_rate": float(work["break_found"].astype(bool).mean())
        if len(work) else float("nan"),
    }
    by_position = {}
    groups = work.groupby("position", dropna=True) if "position" in work else []
    for name, group in groups:
        found = group["break_found"].astype(bool)
        by_position[str(name)] = {
            "n_routes": int(len(group)),
            "n_break_found": int(found.sum()),
            "break_rate": float(found.mean()) if len(group) else float("nan"),
            "n_break_after_throw": int(group["break_after_throw"].astype(bool).sum()),
            "n_throw_known": int(group["throw_known"].astype(bool).sum()),
        }
    summary["by_position"] = by_position

    rows = []
    if "route_family" in work:
        for name, group in work.groupby("route_family", dropna=False, sort=True):
            found = group["break_found"].astype(bool)
            depths = group.loc[found, "break_depth_yd"]
            q25, q75 = _iqr(depths)
            rows.append({
                "route_family": str(name),
                "n_routes": int(len(group)),
                "n_break_found": int(found.sum()),
                "break_rate": float(found.mean()) if len(group) else float("nan"),
                "median_break_depth_yd": float(depths.median())
                if not depths.dropna().empty else float("nan"),
                "iqr_low_yd": q25, "iqr_high_yd": q75,
            })
    summary["by_route_family"] = rows

    # D63 defines a pre-throw break as: break found, throw frame known, and
    # before the throw.  The throw_known term is not optional -- without it an
    # unknown-throw break is silently counted as "before the throw" (D64).
    found = work["break_found"].astype(bool)
    known = work["throw_known"].astype(bool) & found
    after_throw = known & work["break_after_throw"].astype(bool)
    pre_throw = known & ~work["break_after_throw"].astype(bool)
    unknown_throw = found & ~work["throw_known"].astype(bool)
    n_found = int(found.sum())
    n_after = int(after_throw.sum())
    n_pre = int(pre_throw.sum())
    n_unknown = int(unknown_throw.sum())
    assert n_found == n_pre + n_after + n_unknown, (
        "break partition must be exhaustive and disjoint: "
        f"found={n_found} != pre={n_pre} + after={n_after} + unknown={n_unknown}")
    summary["break_after_throw_rate"] = (
        float(work.loc[known, "break_after_throw"].astype(bool).mean())
        if known.any() else float("nan"))
    summary["n_break_after_throw"] = n_after
    summary["n_break_before_throw"] = n_pre
    summary["break_rate_before_throw"] = (
        float(pre_throw.mean()) if len(work) else float("nan"))
    pre_depths = work.loc[pre_throw, "break_depth_yd"]
    summary["median_break_depth_before_throw_yd"] = (
        float(pre_depths.median()) if not pre_depths.dropna().empty
        else float("nan"))
    flags = work["flags"].fillna("").astype(str)
    summary["flag_counts"] = {
        str(flag): int(count)
        for flag, count in flags[flags != ""].str.split(";").explode().value_counts().items()
    }
    return summary


def sensitivity_grid(cfg: dict) -> list[dict]:
    """The declared specification curve: threshold and window perturbations.

    The heading window stays at the frozen value; the curve is the required
    ±10 deg on the break angle crossed with ±0.1 s on the break window.  The
    chosen point (``break_angle_deg`` x ``break_window_s``) is returned first
    and flagged ``primary`` so every table can name the row the answer uses.
    """
    p = params(cfg)
    block = p.get("robustness", {})
    angles = block.get("break_angle_deg", [float(p["break_angle_deg"])])
    windows = block.get("break_window_s", [float(p["break_window_s"])])
    heading = float(p["heading_window_s"])
    chosen = (float(p["break_angle_deg"]), float(p["break_window_s"]))
    specs = []
    for angle in angles:
        for window in windows:
            specs.append({
                "break_angle_deg": float(angle),
                "break_window_s": float(window),
                "heading_window_s": heading,
                "primary": (float(angle), float(window)) == chosen,
            })
    specs.sort(key=lambda spec: (not spec["primary"],
                                 -spec["break_angle_deg"],
                                 spec["break_window_s"]))
    return specs
