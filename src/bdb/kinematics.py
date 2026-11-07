"""Smoothing, derivatives and the provisional turn detector.

Both settings go through this module; nothing here reads a provided speed,
acceleration or orientation field. ``dir`` is never an input.

The detector is deliberately simple and is *validated against drill geometry*
before anything downstream trusts it (TASK.md Phase 1d). All of its numbers come
from config.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

# --------------------------------------------------------------------- helpers


def wrap180(deg: np.ndarray | float) -> np.ndarray | float:
    """Wrap an angular difference into [-180, 180)."""
    return (np.asarray(deg) + 180.0) % 360.0 - 180.0


def heading_deg(vx: np.ndarray, vy: np.ndarray) -> np.ndarray:
    """Heading in degrees, 0 = +y axis, increasing clockwise (NFL ``dir``).

    Verified empirically against the provided ``dir`` column in the audit's
    cross-check item: for a 40-yard dash running along +x the provided ``dir`` is
    ~90 deg, and atan2(vx, vy) is also ~90.
    """
    return np.degrees(np.arctan2(vx, vy)) % 360.0


# -------------------------------------------------------------------- smoothers


def _savgol(v: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    from scipy.signal import savgol_filter

    n = len(v)
    w = min(int(window), n if n % 2 == 1 else n - 1)
    if w < 3:
        return v.astype(float).copy()
    p = min(int(polyorder), w - 1)
    return savgol_filter(v.astype(float), w, p, mode="interp")


def _kalman_rts(t: np.ndarray, z: np.ndarray, q: float, r: float) -> np.ndarray:
    """Constant-velocity Kalman filter + Rauch-Tung-Striebel smoother (1-D)."""
    n = len(z)
    if n < 3:
        return z.astype(float).copy()
    dt = np.gradient(t)
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = q * np.array([[0.25, 0.5], [0.5, 1.0]])
    R = np.array([[r]])
    x = np.array([z[0], 0.0])
    P = np.eye(2) * 1.0
    xs_f = np.zeros((n, 2))
    Ps_f = np.zeros((n, 2, 2))
    xs_p = np.zeros((n, 2))
    Ps_p = np.zeros((n, 2, 2))
    for i in range(n):
        Fd = np.array([[1.0, dt[i]], [0.0, 1.0]])
        if i > 0:
            x = Fd @ x
            P = Fd @ P @ Fd.T + Q * dt[i]
        xs_p[i], Ps_p[i] = x, P
        y = z[i] - H @ x
        S = H @ P @ H.T + R
        K = P @ H.T @ np.linalg.inv(S)
        x = x + (K @ y).ravel()
        P = (np.eye(2) - K @ H) @ P
        xs_f[i], Ps_f[i] = x, P
    xs = xs_f.copy()
    for i in range(n - 2, -1, -1):
        Fd = np.array([[1.0, dt[i + 1]], [0.0, 1.0]])
        C = Ps_f[i] @ Fd.T @ np.linalg.inv(Ps_p[i + 1])
        xs[i] = xs_f[i] + C @ (xs[i + 1] - xs_p[i + 1])
    return xs[:, 0]


def _spline(t: np.ndarray, v: np.ndarray, s_yards: float) -> np.ndarray:
    from scipy.interpolate import UnivariateSpline

    n = len(v)
    if n < 4:
        return v.astype(float).copy()
    s = n * float(s_yards) ** 2
    try:
        return UnivariateSpline(t, v, k=3, s=s)(t)
    except Exception:  # pragma: no cover - degenerate input
        return v.astype(float).copy()


def smooth_positions(t: np.ndarray, x: np.ndarray, y: np.ndarray,
                     spec: dict) -> tuple[np.ndarray, np.ndarray]:
    """Apply one named smoother to both coordinates with identical parameters."""
    kind = spec["kind"]
    if kind == "savgol":
        return (_savgol(x, spec["window"], spec["polyorder"]),
                _savgol(y, spec["window"], spec["polyorder"]))
    if kind == "kalman":
        return (_kalman_rts(t, x, spec["q"], spec["r"]),
                _kalman_rts(t, y, spec["q"], spec["r"]))
    if kind == "spline":
        return (_spline(t, x, spec["smoothing_yards"]),
                _spline(t, y, spec["smoothing_yards"]))
    raise ValueError(f"unknown smoother kind {kind!r}")


def derivatives(t: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> dict[str, np.ndarray]:
    """Velocity, speed, heading, tangential accel and angular rate.

    Never called on raw positions — only on smoothed coordinates.
    """
    vx = np.gradient(xs, t)
    vy = np.gradient(ys, t)
    speed = np.hypot(vx, vy)
    ahead = heading_deg(vx, vy)
    unwrapped = np.degrees(np.unwrap(np.radians(ahead)))
    omega = np.gradient(unwrapped, t)  # deg/s
    tang = np.gradient(speed, t)
    return {
        "vx": vx, "vy": vy, "speed": speed, "heading": ahead,
        "heading_unwrapped": unwrapped, "omega_deg_s": omega,
        "tangential_accel": tang,
    }


# --------------------------------------------------------------------- detector


@dataclass
class DetectorParams:
    window_s: float
    min_turn_angle_deg: float
    speed_floor: float
    min_separation_s: float
    min_rate_deg_s: float = 15.0
    max_duration_s: float = 2.0
    speed_walk_tolerance: float = 0.2

    @classmethod
    def from_config(cls, det: dict, speed_floor: float) -> "DetectorParams":
        return cls(
            window_s=float(det["window_s"]),
            min_turn_angle_deg=float(det["min_turn_angle_deg"]),
            speed_floor=float(speed_floor),
            min_separation_s=float(det["nms_min_separation_s"]),
            min_rate_deg_s=float(det.get("min_angular_rate_deg_s", 15.0)),
            max_duration_s=float(det.get("max_turn_duration_s", 2.0)),
            speed_walk_tolerance=float(det.get("speed_walk_tolerance", 0.2)),
        )


def detect_turns(t: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                 params: DetectorParams) -> list[dict]:
    """Provisional turn detector (plant-aware segmentation).

    Three steps, each with a config parameter and no data-tuned numbers:

    1. **Trigger.** A heading change of at least ``min_turn_angle_deg`` across a
       ``window_s`` window, with some frame in the window moving above
       ``speed_floor`` (so a standing player cannot trigger).
    2. **Segment.** From the speed minimum (the plant) inside the trigger window,
       walk back to the last local speed maximum and forward to the next one,
       capped at ``max_duration_s``. Entry speed is the approach speed, exit speed
       is the re-accelerated speed — not the near-zero speed at the plant itself.
    3. **Accept.** Keep the turn if the total heading change over that span clears
       ``min_turn_angle_deg`` and both entry and exit speeds clear the speed floor.

    Step 2 is what makes a cone or shuttle plant measurable: at 10 Hz the heading
    is undefined when the speed passes through zero, so measuring the heading
    change only inside a fixed 0.5 s window records a 180-degree plant as a
    30-degree turn.
    """
    n = len(t)
    dt = float(np.median(np.diff(t))) if n > 2 else 0.1
    if dt <= 0 or not np.isfinite(dt):
        return []
    w = max(2, int(round(params.window_s / dt)))
    if n < w + 2:
        return []
    d = derivatives(t, xs, ys)
    speed, unwrapped = d["speed"], d["heading_unwrapped"]
    span = unwrapped[w:] - unwrapped[:-w]
    start = np.arange(len(span))
    # a trigger needs movement somewhere in the window; the speed floor is then
    # applied to entry and exit after segmentation
    peak_in_window = np.array([speed[i:i + w + 1].max() for i in start])
    ok = peak_in_window >= params.speed_floor
    score = np.where(ok, np.abs(span), 0.0)
    cand = np.where(score >= params.min_turn_angle_deg)[0]
    if len(cand) == 0:
        return []

    sep = max(1, int(round(params.min_separation_s / dt)))
    order = cand[np.argsort(-score[cand], kind="stable")]
    kept: list[int] = []
    for c in order:
        if all(abs(c - k) >= sep for k in kept):
            kept.append(int(c))
    kept.sort()

    max_half = max(2, int(round(0.5 * params.max_duration_s / dt)))
    tol = params.speed_walk_tolerance

    turns = []
    covered: list[tuple[int, int]] = []
    for c in kept:
        seg = np.arange(c, min(n - 1, c + w) + 1)
        apex = int(seg[np.argmin(speed[seg])])
        lo = apex
        while lo - 1 >= 0 and (apex - (lo - 1)) <= max_half and speed[lo - 1] >= speed[lo] - tol:
            lo -= 1
        hi = apex
        while hi + 1 < n and ((hi + 1) - apex) <= max_half and speed[hi + 1] >= speed[hi] - tol:
            hi += 1
        angle = float(unwrapped[hi] - unwrapped[lo])
        if abs(angle) < params.min_turn_angle_deg:
            continue
        if speed[lo] < params.speed_floor or speed[hi] < params.speed_floor:
            continue
        if any(min(hi, b) - max(lo, a) > 0.5 * (hi - lo) for a, b in covered):
            continue
        covered.append((lo, hi))
        entry, exit_v = float(speed[lo]), float(speed[hi])
        om = float(np.max(np.abs(d["omega_deg_s"][lo:hi + 1])))
        turns.append({
            "frame_start": int(lo),
            "frame_centre": int(apex),
            "frame_end": int(hi),
            "t_start": float(t[lo]),
            "t_end": float(t[hi]),
            "entry_speed": entry,
            "exit_speed": exit_v,
            "apex_speed": float(speed[apex]),
            "turn_angle": angle,
            "duration_s": float(t[hi] - t[lo]),
            "peak_omega_deg_s": om,
            "path_radius_yd": float(entry / np.radians(om)) if om > 1e-6 else np.nan,
            "lateral_accel_yd_s2": float(entry * np.radians(om)),
            "speed_retention": float(exit_v / entry) if entry > 1e-6 else np.nan,
            "max_gap_s": float(np.max(np.diff(t[lo:hi + 1]))) if hi > lo else np.nan,
            "x_centre": float(xs[apex]),
            "y_centre": float(ys[apex]),
        })
    return turns


def frames_per_turn(entry_speed: float, turn_angle_deg: float,
                    window_s: float, dt: float) -> float:
    """A crude kinematic bound on how many frames a turn of this size needs."""
    if entry_speed <= 0:
        return np.nan
    return window_s / dt
