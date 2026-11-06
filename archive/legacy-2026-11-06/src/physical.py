"""physical — onset detection and distance-crossing primitives.

Shared by Phase 1 (onset detectability, Combine test-retest) and, later, by
Phases 2-3. Deliberately measurement-only: no modelling, no pooling, no
thresholds beyond what config.yaml declares.

Both settings are 10 Hz (verified: 100 ms spacing in the Combine tracking and in
the game tracking), so one definition can in principle apply to both. Whether it
*should* is Phase 1 item (e), and that is an empirical question this module helps
answer rather than assume.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

try:
    import common as C
except ImportError:  # pragma: no cover
    from . import common as C  # type: ignore


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def travel_direction(xs: Sequence[float], ys: Sequence[float],
                     start: int = 0, stop: int | None = None) -> tuple[float, float]:
    """Unit vector of travel over xs[start:stop], by principal axis of the path.

    ``dir`` cannot be used: it wraps at 0/360 and is a per-frame heading, not a
    line. The principal axis is the direction that minimises perpendicular
    scatter, i.e. the line the athlete actually ran.
    """
    stop = len(xs) if stop is None else stop
    pts = [(float(xs[i]), float(ys[i])) for i in range(start, stop)
           if xs[i] is not None and ys[i] is not None]
    if len(pts) < 2:
        raise ValueError("travel_direction needs at least two positioned frames")
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx + syy <= 1e-12:
        raise ValueError("degenerate travel direction (no spatial extent)")
    # Angle of the major axis of the 2x2 scatter matrix.
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    ux, uy = math.cos(theta), math.sin(theta)
    # Orient along the actual displacement so distance is non-decreasing.
    dx, dy = pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]
    if ux * dx + uy * dy < 0:
        ux, uy = -ux, -uy
    norm = math.hypot(ux, uy)
    if norm == 0:
        raise ValueError("degenerate travel direction (zero-length path)")
    return ux / norm, uy / norm


def project_along(xs: Sequence[float], ys: Sequence[float],
                  ux: float, uy: float,
                  origin: tuple[float, float] | None = None) -> list[float]:
    """Signed distance along (ux, uy) from ``origin`` (default: first frame)."""
    if origin is None:
        origin = (float(xs[0]), float(ys[0]))
    return [(float(x) - origin[0]) * ux + (float(y) - origin[1]) * uy
            for x, y in zip(xs, ys)]


def speed_series(xs: Sequence[float], ys: Sequence[float],
                 times: Sequence[float]) -> list[float]:
    """Central-difference speed from positions, in yd/s.

    Used as an independent cross-check on the provided ``s`` column; a mismatch
    means the position series and the speed series disagree.
    """
    n = len(xs)
    out = [0.0] * n
    for i in range(n):
        if i == 0:
            a, b = 0, 1
        elif i == n - 1:
            a, b = n - 2, n - 1
        else:
            a, b = i - 1, i + 1
        if b <= a:
            out[i] = 0.0
            continue
        dt = float(times[b]) - float(times[a])
        dist = math.hypot(float(xs[b]) - float(xs[a]), float(ys[b]) - float(ys[a]))
        out[i] = dist / dt if dt > 0 else 0.0
    return out


def cumulative_path(xs: Sequence[float], ys: Sequence[float]) -> list[float]:
    """Cumulative Euclidean path length from the first frame, in yards."""
    out = [0.0]
    for i in range(1, len(xs)):
        out.append(out[-1] + math.hypot(float(xs[i]) - float(xs[i - 1]),
                                        float(ys[i]) - float(ys[i - 1])))
    return out


# --------------------------------------------------------------------------- #
# Onset
# --------------------------------------------------------------------------- #
class OnsetResult(dict):
    """Plain dict subclass so callers can use it in JSON and attribute-free code."""


def detect_onset(times: Sequence[float], speed: Sequence[float],
                 cfg: Mapping, *, search_from: float | None = None,
                 search_to: float | None = None,
                 baseline_from: float | None = None) -> OnsetResult:
    """First sustained crossing of the configured speed threshold.

    ``times`` are seconds relative to an arbitrary origin (snap time for game
    tracking, first frame for a Combine attempt). ``search_from`` defaults to the
    first frame; for game snaps it must be the snap time.

    ``baseline_from`` changes only the *context* baseline window, never the
    onset rule: the threshold is absolute, so the baseline cannot move the
    detected onset. For a game snap pass ``-baseline_window_s`` so the reported
    at-rest speed is measured before the snap instead of during the first 0.5 s
    of movement.

    Returns a dict with ``found`` (bool), ``onset_s`` (seconds, same origin),
    ``index``, ``baseline_speed``, ``threshold``, ``reason``.
    """
    o = C.section(cfg, "onset")
    thr = float(o["speed_yds"])
    need = int(o["sustained_frames"])
    b_win = float(o["baseline_window_s"])
    min_b = int(o["min_baseline_frames"])
    max_lat = float(o["max_latency_s"])

    t0 = float(times[0]) if search_from is None else float(search_from)
    t_end = (float(times[-1]) if search_to is None
             else min(float(search_to), float(times[-1])))

    idx = [i for i, t in enumerate(times) if t0 <= float(t) <= t_end]
    if len(idx) < need + 1:
        return OnsetResult(found=False, onset_s=None, index=None,
                           baseline_speed=None, threshold=thr,
                           reason=f"only {len(idx)} frames in search window")

    if baseline_from is None:
        base_idx = [i for i in idx if float(times[i]) < t0 + b_win]
        if len(base_idx) < min_b:
            base_idx = idx[:min_b]
        if len(base_idx) < min_b:
            return OnsetResult(found=False, onset_s=None, index=None,
                               baseline_speed=None, threshold=thr,
                               reason="baseline not estimable")
        baseline = sorted(float(speed[i]) for i in base_idx)[len(base_idx) // 2]
        baseline_ctx = round(baseline, 4)
    else:
        # Pre-onset context only. No fallback to post-onset frames: a
        # contaminated baseline is reported as absent, not as a number.
        base_idx = [i for i in range(len(times))
                    if float(baseline_from) <= float(times[i]) < t0]
        baseline_ctx = (round(sorted(float(speed[i]) for i in base_idx)
                              [len(base_idx) // 2], 4)
                        if len(base_idx) >= min_b else None)

    limit = t0 + min(max_lat, float(o["search_window_s"]))
    for pos in range(len(idx) - need + 1):
        i = idx[pos]
        if float(times[i]) > limit:
            break
        if all(float(speed[idx[pos + k]]) >= thr for k in range(need)):
            return OnsetResult(found=True, onset_s=float(times[i]), index=i,
                               baseline_speed=baseline_ctx,
                               threshold=thr, reason="ok")
    return OnsetResult(found=False, onset_s=None, index=None,
                       baseline_speed=baseline_ctx, threshold=thr,
                       reason=f"no {need}-frame run at or above {thr} yd/s "
                              f"within {max_lat}s")


def detect_onset_displacement(times: Sequence[float], xs: Sequence[float],
                              ys: Sequence[float],
                              cfg: Mapping) -> OnsetResult:
    """Alternative onset: first sustained cumulative displacement.

    ``TASK.md`` Phase 2 allows a speed **or** displacement threshold. The speed
    threshold is the primary definition (it is also what Phase 1 used); this
    function exists so the two can be compared rather than assumed equivalent.
    Displacement is measured along the *path*, so it needs no travel direction
    and cannot be gamed by lateral jitter: noise of +/-0.03 yd per frame
    accumulates as a random walk, not as a straight-line 0.10 yd.
    """
    o = C.section(cfg, "onset")
    thr = float(o["displacement_yds"])
    need = int(o["sustained_frames"])
    max_lat = float(o["max_latency_s"])
    path = cumulative_path(xs, ys)
    t0 = float(times[0])
    limit = t0 + min(max_lat, float(o["search_window_s"]))
    if len(times) < need + 1:
        return OnsetResult(found=False, onset_s=None, index=None,
                           baseline_speed=None, threshold=thr,
                           reason=f"only {len(times)} frames")
    for pos in range(len(times) - need + 1):
        if float(times[pos]) > limit:
            break
        if all(path[pos + k] >= thr for k in range(need)):
            return OnsetResult(found=True, onset_s=float(times[pos]), index=pos,
                               baseline_speed=None, threshold=thr, reason="ok")
    return OnsetResult(found=False, onset_s=None, index=None,
                       baseline_speed=None, threshold=thr,
                       reason=f"no {need}-frame run at or above {thr} yd "
                              f"displacement within {max_lat}s")


# --------------------------------------------------------------------------- #
# Crossing a distance
# --------------------------------------------------------------------------- #
def time_at_distance(times: Sequence[float], along: Sequence[float],
                     distance: float, *, first_index: int = 0) -> dict:
    """Interpolated time at which ``along`` first reaches ``distance``.

    Linear interpolation between the bracketing frames (model-free). Search
    starts at ``first_index`` (the onset frame) so pre-onset jitter cannot
    satisfy the crossing.
    """
    d = float(distance)
    if d <= 0:
        return {"reached": True, "time_s": float(times[first_index]),
                "interpolated": False, "reason": "distance <= 0"}
    prev_i = None
    for i in range(first_index, len(along)):
        if along[i] is None:
            continue
        if along[i] >= d:
            if prev_i is None:
                return {"reached": True, "time_s": float(times[i]),
                        "interpolated": False, "reason": "reached at first frame"}
            dt = float(times[i]) - float(times[prev_i])
            da = float(along[i]) - float(along[prev_i])
            if da <= 0:
                frac = 0.0
            else:
                frac = (d - float(along[prev_i])) / da
            frac = min(max(frac, 0.0), 1.0)
            return {"reached": True,
                    "time_s": float(times[prev_i]) + frac * dt,
                    "interpolated": True, "reason": "ok"}
        prev_i = i
    return {"reached": False, "time_s": None, "interpolated": False,
            "reason": f"never reached {d} yd after onset"}


def _is_gap(dt: float, nominal_dt: float) -> bool:
    """A frame spacing is a gap when it is >1.5 nominal frames.

    Structural tolerance, not an analysis threshold: 100 ms nominal, so a
    spacing above 150 ms means at least one frame is missing.
    """
    return dt > nominal_dt * 1.5


def time_at_distance_qc(times: Sequence[float], along: Sequence[float],
                        distance: float, *, first_index: int, onset_s: float,
                        nominal_dt: float, max_gap_s: float,
                        first_second_s: float = 1.0) -> dict:
    """Interpolated crossing with the Phase 2 gap policy applied.

    Policy (``TASK.md`` Phase 2): interpolate gaps **under** ``max_gap_s`` only
    **outside** the first second after onset. A crossing that lands inside a
    gap in that first second is reported as crossed-but-not-measurable; it is
    never filled.

    Returns ``ok`` (a usable time exists), ``crossed`` (the distance was
    physically reached), ``time_s`` (absolute, same origin as ``times``),
    ``elapsed_s`` (from onset), ``interpolated``, ``gap_s``, ``gap_policy``
    (``none`` / ``bridged`` / ``forbidden_first_second`` / ``too_long``) and a
    human-readable ``reason``.
    """
    d = float(distance)
    if d <= 0:
        return {"ok": True, "crossed": True, "time_s": float(times[first_index]),
                "elapsed_s": float(times[first_index]) - float(onset_s),
                "interpolated": False, "gap_s": 0.0, "gap_policy": "none",
                "reason": "distance <= 0"}
    prev_i = None
    for i in range(first_index, len(along)):
        if along[i] is None:
            continue
        if along[i] >= d:
            if prev_i is None:
                t_cross = float(times[i])
                return {"ok": True, "crossed": True, "time_s": t_cross,
                        "elapsed_s": t_cross - float(onset_s),
                        "interpolated": False, "gap_s": 0.0,
                        "gap_policy": "none", "reason": "reached at first frame"}
            dt = float(times[i]) - float(times[prev_i])
            da = float(along[i]) - float(along[prev_i])
            frac = 0.0 if da <= 0 else (d - float(along[prev_i])) / da
            frac = min(max(frac, 0.0), 1.0)
            t_cross = float(times[prev_i]) + frac * dt
            gap = _is_gap(dt, nominal_dt)
            policy = "none"
            if gap:
                inside_first_s = (t_cross - float(onset_s)) <= float(first_second_s)
                if inside_first_s:
                    policy = "forbidden_first_second"
                elif dt < float(max_gap_s):
                    policy = "bridged"
                else:
                    policy = "too_long"
            ok = policy in ("none", "bridged")
            return {"ok": ok, "crossed": True,
                    "time_s": t_cross if ok else None,
                    "elapsed_s": (t_cross - float(onset_s)) if ok else None,
                    "interpolated": True, "gap_s": dt, "gap_policy": policy,
                    "reason": ("ok" if ok else
                               "gap inside the first second after onset: "
                               "not interpolated" if policy == "forbidden_first_second"
                               else "gap >= threshold: not interpolated")}
        prev_i = i
    return {"ok": False, "crossed": False, "time_s": None, "elapsed_s": None,
            "interpolated": False, "gap_s": None, "gap_policy": "none",
            "reason": f"never reached {d} yd after onset"}


# --------------------------------------------------------------------------- #
# Gaps / QC
# --------------------------------------------------------------------------- #
def frame_gaps(times: Sequence[float]) -> list[float]:
    return [float(times[i]) - float(times[i - 1]) for i in range(1, len(times))]


def gap_flags(times: Sequence[float], cfg: Mapping, *,
              onset_s: float | None = None) -> dict:
    """Count frame gaps, and how many fall inside the first second after onset.

    TASK.md: interpolate gaps under 0.5 s only outside the first second. So a
    gap inside the first second is *unfixable* and must be recorded, never
    filled.
    """
    o = C.section(cfg, "onset")
    nominal = 1.0 / float(o["hz"])
    gaps = frame_gaps(times)
    big = [g for g in gaps if _is_gap(g, nominal)]
    inside_first_s = 0
    if onset_s is not None:
        for g, t in zip(gaps, times[1:]):
            if _is_gap(g, nominal) and float(t) <= onset_s + 1.0:
                inside_first_s += 1
    return {"n_frames": len(times), "n_gaps": len(big),
            "max_gap_s": (max(gaps) if gaps else 0.0),
            "gaps_in_first_s_after_onset": inside_first_s}


def speed_cap_flags(speed: Sequence[float], cfg: Mapping) -> int:
    cap = float(C.section(cfg, "onset")["speed_cap_yds"])
    return sum(1 for s in speed if s is not None and float(s) > cap)


def duplicate_attempts(attempts: Sequence[int]) -> int:
    return len(attempts) - len(set(attempts))


# --------------------------------------------------------------------------- #
# Parametric cross-check: v(t) = vmax * (1 - exp(-t/tau))
# --------------------------------------------------------------------------- #
def exponential_speed(vmax: float, tau: float, t: float) -> float:
    if t <= 0 or tau <= 0:
        return 0.0
    return vmax * (1.0 - math.exp(-t / tau))


def exponential_distance(vmax: float, tau: float, t: float) -> float:
    if t <= 0 or tau <= 0:
        return 0.0
    return vmax * (t - tau * (1.0 - math.exp(-t / tau)))


def exponential_time_to_distance(vmax: float, tau: float,
                                 distance: float) -> float | None:
    """Invert the fitted curve by bisection. ``None`` if unreachable by 30 s."""
    if distance <= 0:
        return 0.0
    lo, hi = 0.0, 30.0
    if exponential_distance(vmax, tau, hi) < distance:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if exponential_distance(vmax, tau, mid) < distance:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def fit_exponential_speed(times: Sequence[float], speeds: Sequence[float],
                          cfg: Mapping, *, onset_s: float,
                          onset_index: int) -> dict:
    """Least-squares fit of ``v(t) = vmax(1 - exp(-(t-onset)/tau))``.

    CROSS-CHECK ONLY. The fit is made on the post-onset speed samples inside a
    short window (``physical.crosscheck.window_s``); it is poorly constrained in
    the first yard, which is exactly the region the estimand lives in, so its
    result is reported beside the model-free time and never substituted for it.

    Grid search then deterministic local refinement: no optimiser dependency,
    no random starts, same answer on every machine.
    """
    p = C.section(cfg, "physical")["crosscheck"]
    lo_v, hi_v = float(p["vmax_range"][0]), float(p["vmax_range"][1])
    lo_t, hi_t = float(p["tau_range"][0]), float(p["tau_range"][1])
    n_coarse = int(p["grid_points"])
    n_refine = int(p["refine_points"])
    rounds = int(p["refine_rounds"])
    window = float(p["window_s"])
    min_n = int(p["min_samples"])

    samples = [(float(t) - float(onset_s), float(s))
               for t, s in zip(times, speeds)
               if s is not None and float(t) >= float(onset_s)
               and float(t) <= float(onset_s) + window]
    if len(samples) < min_n:
        return {"found": False, "vmax": None, "tau": None, "rmse": None,
                "n_samples": len(samples),
                "reason": f"only {len(samples)} post-onset samples"}

    def sse(vmax: float, tau: float) -> float:
        total = 0.0
        for t, s in samples:
            e = s - exponential_speed(vmax, tau, t)
            total += e * e
        return total / len(samples)

    def grid(a: float, b: float, n: int):
        out = []
        step = (b - a) / (n - 1)
        for i in range(n):
            out.append(a + i * step)
        return out

    best = (float("inf"), lo_v, lo_t)
    for vmax in grid(lo_v, hi_v, n_coarse):
        for tau in grid(lo_t, hi_t, n_coarse):
            e = sse(vmax, tau)
            if e < best[0]:
                best = (e, vmax, tau)
    vmax, tau = best[1], best[2]
    span_v = (hi_v - lo_v) / (n_coarse - 1)
    span_t = (hi_t - lo_t) / (n_coarse - 1)
    for _ in range(rounds):
        for cand_v in grid(max(lo_v, vmax - span_v), min(hi_v, vmax + span_v),
                           n_refine):
            for cand_t in grid(max(lo_t, tau - span_t), min(hi_t, tau + span_t),
                               n_refine):
                e = sse(cand_v, cand_t)
                if e < best[0]:
                    best = (e, cand_v, cand_t)
        vmax, tau = best[1], best[2]
        span_v *= 0.35
        span_t *= 0.35
    return {"found": True, "vmax": vmax, "tau": tau,
            "rmse": math.sqrt(best[0]), "n_samples": len(samples),
            "reason": "ok"}


# --------------------------------------------------------------------------- #
# Whole-attempt measurement (used by Phase 1 (e)/(h))
# --------------------------------------------------------------------------- #
def measure_attempt(times: Sequence[float], xs: Sequence[float],
                    ys: Sequence[float], speeds: Sequence[float],
                    cfg: Mapping, distances: Sequence[float]) -> dict:
    """Onset + time-to-distance for one tracking segment.

    Returns a flat dict; never raises for a degenerate attempt, it reports
    ``ok=False`` with a reason so the audit can count failures instead of dying.
    """
    out: dict = {"ok": False, "reason": "", "onset_s": None,
                 "onset_frame": None, "baseline_speed": None}
    if len(times) < 4:
        out["reason"] = "too few frames"
        return out
    onset = detect_onset(times, speeds, cfg)
    out.update({k: onset[k] for k in
                ("onset_s", "index", "baseline_speed", "reason")})
    out["onset_frame"] = onset["index"]
    if not onset["found"]:
        out["ok"] = False
        return out
    i0 = onset["index"]
    try:
        ux, uy = travel_direction(xs, ys, start=i0)
    except ValueError as exc:
        out["reason"] = f"direction: {exc}"
        return out
    along = project_along(xs, ys, ux, uy, origin=(float(xs[i0]), float(ys[i0])))
    rel_t = [float(t) - float(times[0]) for t in times]
    for d in distances:
        r = time_at_distance(rel_t, along, float(d), first_index=i0)
        key = f"t_to_{str(d).replace('.', 'p')}yd"
        out[key] = (round(r["time_s"] - float(times[i0] - times[0]), 4)
                    if r["reached"] else None)
    out["ok"] = True
    out["reason"] = "ok"
    return out
