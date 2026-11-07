"""Phase 2 engine — smoothing, derivatives, turn candidates and classification.

One code path for Combine attempts and game snaps. Everything that could differ
between the settings (speed floor, context columns, orientation availability) is
an argument or comes from ``config.yaml``.

Nothing here reads a provided ``s``, ``a`` or ``dir`` field: all kinematics come
from smoothed x/y/time. ``o`` is read in game data only, as a *flag*, never as
part of the turn definition.

Design notes that live in ``notes/decisions.md``:

* every configured smoother runs on every attempt; the turn table carries the
  primary values and the smoother-to-smoother range (D133);
* an alternative smoother's turn is matched to a primary turn by centre time
  within ``smoothing.match_tolerance_s`` (D133);
* classification is kinematic only — apex retention vs entry speed plus
  re-acceleration (D134);
* gaps are interpolated only up to ``sampling.max_gap_s`` and any inserted frame
  inside a turn is flagged; a gap above the limit invalidates the turn (D135).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
import pyarrow as pa

from .kinematics import (DetectorParams, derivatives, detect_turns,
                         smooth_positions, wrap180)

# --------------------------------------------------------------------- schemas

TURN_SCHEMA = pa.schema([
    ("setting", pa.string()),
    ("drill_name", pa.string()),
    ("event_id", pa.string()),
    ("attempt", pa.int32()),
    ("game_id", pa.int64()),
    ("play_id", pa.int64()),
    ("nfl_id", pa.int64()),
    ("player_name", pa.string()),
    ("position", pa.string()),
    ("position_group", pa.string()),
    ("alignment", pa.string()),
    ("snap_type", pa.string()),
    ("draft_year", pa.int16()),
    ("n_frames", pa.int32()),
    ("frame_start", pa.int32()),
    ("frame_centre", pa.int32()),
    ("frame_end", pa.int32()),
    ("t_start", pa.float32()),
    ("t_end", pa.float32()),
    ("duration_s", pa.float32()),
    ("entry_speed", pa.float32()),
    ("exit_speed", pa.float32()),
    ("apex_speed", pa.float32()),
    ("turn_angle", pa.float32()),
    ("abs_turn_angle", pa.float32()),
    ("apex_retention", pa.float32()),
    ("speed_retention", pa.float32()),
    ("regain_entry_speed_s", pa.float32()),
    ("regain_exhausted", pa.bool_()),
    ("tangential_accel_entry", pa.float32()),
    ("tangential_accel_exit", pa.float32()),
    ("peak_omega_deg_s", pa.float32()),
    ("path_radius_yd", pa.float32()),
    ("path_radius_geometric_yd", pa.float32()),
    ("circle_fit_residual_frac", pa.float32()),
    ("circle_fit_usable", pa.bool_()),
    ("peak_lateral_accel_yd_s2", pa.float32()),
    ("lateral_accel_v2_over_r", pa.float32()),
    ("turn_type", pa.string()),
    ("plant", pa.bool_()),
    ("n_smoothers_matched", pa.int16()),
    ("entry_speed_smoother_min", pa.float32()),
    ("entry_speed_smoother_max", pa.float32()),
    ("exit_speed_smoother_min", pa.float32()),
    ("exit_speed_smoother_max", pa.float32()),
    ("apex_speed_smoother_min", pa.float32()),
    ("apex_speed_smoother_max", pa.float32()),
    ("abs_turn_angle_smoother_min", pa.float32()),
    ("abs_turn_angle_smoother_max", pa.float32()),
    ("speed_retention_smoother_min", pa.float32()),
    ("speed_retention_smoother_max", pa.float32()),
    ("peak_lateral_accel_smoother_min", pa.float32()),
    ("peak_lateral_accel_smoother_max", pa.float32()),
    ("max_gap_s", pa.float32()),
    ("gap_in_turn", pa.bool_()),
    ("gap_above_limit", pa.bool_()),
    ("interpolated_in_turn", pa.int16()),
    ("speed_above_cap", pa.bool_()),
    ("accel_above_cap", pa.bool_()),
    ("valid", pa.bool_()),
    ("orientation_divergence_share", pa.float32()),
    ("orientation_flag", pa.bool_()),
    ("x_centre", pa.float32()),
    ("y_centre", pa.float32()),
])
TURN_COLUMNS = [f.name for f in TURN_SCHEMA]

ATTEMPT_BASE_FIELDS = [
    ("setting", pa.string()), ("drill_name", pa.string()), ("event_id", pa.string()),
    ("attempt", pa.int32()), ("game_id", pa.int64()), ("play_id", pa.int64()),
    ("nfl_id", pa.int64()), ("n_frames", pa.int32()), ("duplicate_frames", pa.int16()),
    ("gaps_over_limit", pa.int16()), ("interpolated_frames", pa.int32()),
    ("frames_speed_above_cap", pa.int32()), ("frames_accel_above_cap", pa.int32()),
    ("position_group", pa.string()), ("snap_type", pa.string()),
    ("turns_primary", pa.int16()), ("null_detections", pa.int16()),
]


def attempt_schema(smoother_names: list[str], seg_options: tuple[str, ...] | list[str] = ()) -> pa.Schema:
    fields = list(ATTEMPT_BASE_FIELDS)
    fields += [(f"turns_{n}", pa.int16()) for n in smoother_names]
    fields += [(f"turns_seg_{s}", pa.int16()) for s in seg_options
               if s != "speed_maxima"]
    return pa.schema(fields)


SUMMARY_SCHEMA = pa.schema([
    ("level", pa.string()), ("setting", pa.string()), ("position_group", pa.string()),
    ("drill_name", pa.string()), ("snap_type", pa.string()), ("nfl_id", pa.int64()),
    ("n_units", pa.int32()), ("n_turns", pa.int32()), ("turns_per_unit", pa.float32()),
    ("median_entry_speed", pa.float32()), ("median_exit_speed", pa.float32()),
    ("median_apex_speed", pa.float32()), ("median_speed_retention", pa.float32()),
    ("median_turn_angle", pa.float32()), ("share_plant", pa.float32()),
    ("share_valid", pa.float32()),
])


# ------------------------------------------------------------------- gap filling

def fill_small_gaps(t: np.ndarray, x: np.ndarray, y: np.ndarray,
                    nominal_dt: float, max_gap: float):
    """Linearly fill frame gaps up to ``max_gap``; larger gaps are left and counted.

    Returns ``(t, x, y, inserted, gaps)`` where ``inserted`` is a boolean mask on
    the returned series (True = a manufactured frame) and ``gaps`` lists
    ``(t_before, t_after, duration)`` for every gap that is not a normal step.
    Linear interpolation is used only to put the smoothing filter on a regular
    grid; the turn metrics are computed on the smoothed series, never on raw or
    interpolated positions.
    """
    n = len(t)
    if n == 0:
        return t, x, y, np.zeros(0, bool), []
    ft, fx, fy, ins = [float(t[0])], [float(x[0])], [float(y[0])], [False]
    gaps: list[tuple[float, float, float]] = []
    for i in range(1, n):
        d = float(t[i] - t[i - 1])
        normal = d <= 1.5 * nominal_dt
        if normal:
            ft.append(float(t[i])); fx.append(float(x[i])); fy.append(float(y[i])); ins.append(False)
            continue
        gaps.append((float(t[i - 1]), float(t[i]), d))
        k = int(round(d / nominal_dt))
        if d <= max_gap and k >= 2:
            for j in range(1, k):
                w = j / k
                ft.append(float(t[i - 1] + w * d))
                fx.append(float(x[i - 1] * (1 - w) + x[i] * w))
                fy.append(float(y[i - 1] * (1 - w) + y[i] * w))
                ins.append(True)
        ft.append(float(t[i])); fx.append(float(x[i])); fy.append(float(y[i])); ins.append(False)
    return (np.asarray(ft), np.asarray(fx), np.asarray(fy),
            np.asarray(ins, bool), gaps)


# ------------------------------------------------------------------ small maths

def circle_fit(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float]:
    """Algebraic (Kasa) circle fit; returns ``(radius, normalised rms residual)``."""
    if len(xs) < 4:
        return float("nan"), float("nan")
    A = np.column_stack([xs, ys, np.ones(len(xs))])
    b = xs ** 2 + ys ** 2
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:  # pragma: no cover - degenerate input
        return float("nan"), float("nan")
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r2 = sol[2] + cx ** 2 + cy ** 2
    if not np.isfinite(r2) or r2 <= 0:
        return float("nan"), float("nan")
    r = float(np.sqrt(r2))
    if r > 1e4:  # statistically straight: a radius is not estimable
        return float("nan"), float("nan")
    resid = float(np.sqrt(np.mean((np.hypot(xs - cx, ys - cy) - r) ** 2)) / r)
    return r, resid


def _md5_seed(text: str, offset: int) -> int:
    h = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    return int((offset + h) % (2 ** 32))


# ----------------------------------------------------------------------- engine

@dataclass
class Engine:
    """All Phase-2 parameters, read once from config for one setting."""

    setting: str
    smoother_specs: dict
    primary: str
    det: DetectorParams
    nominal_dt: float
    max_gap: float
    match_tol: float
    retention_delta: float
    reaccel_min: float
    regain_horizon: float
    circle_ok: float
    speed_cap: float
    accel_cap: float
    div_deg: float
    sustain: float
    null_reps: int
    null_offset: int
    seg_options: tuple[str, ...] = ("speed_maxima", "fixed_window")

    @classmethod
    def from_config(cls, cfg, setting: str) -> "Engine":
        specs = {c["name"]: c for c in cfg.get("smoothing.candidates")}
        floor = cfg.get("detector.speed_floor_combine" if setting == "combine"
                        else "detector.speed_floor_game")
        return cls(
            setting=setting,
            smoother_specs=specs,
            primary=str(cfg.get("smoothing.primary")),
            det=DetectorParams.from_config(cfg.get("detector"), floor),
            nominal_dt=float(cfg.get("sampling.dt_s")),
            max_gap=float(cfg.get("sampling.max_gap_s")),
            match_tol=float(cfg.get("smoothing.match_tolerance_s")),
            retention_delta=float(cfg.get("plant.retention_delta")),
            reaccel_min=float(cfg.get("plant.reaccel_min_yd_s")),
            regain_horizon=float(cfg.get("detector.regain_horizon_s")),
            circle_ok=float(cfg.get("detector.circle_fit_ok_residual_frac")),
            speed_cap=float(cfg.get("sanity.speed_cap_yd_s")),
            accel_cap=float(cfg.get("smoothing.accel_cap_yd_s2")),
            div_deg=float(cfg.get("orientation.divergence_deg")),
            sustain=float(cfg.get("orientation.sustain_share")),
            null_reps=int(cfg.get("sanity.null_shuffle_reps")),
            null_offset=int(cfg.get("sanity.null_seed_offset")),
            seg_options=tuple(cfg.get("detector.segmentation_options")),
        )

    @property
    def smooth_names(self) -> list[str]:
        return list(self.smoother_specs)

    # -- one attempt ---------------------------------------------------------
    def analyse(self, ms: np.ndarray, x: np.ndarray, y: np.ndarray, ctx: dict,
                o: np.ndarray | None = None, dirv: np.ndarray | None = None,
                duplicate_frames: int = 0) -> tuple[dict, list[dict]]:
        """Smooth, detect and characterise one attempt/snap.

        ``ctx`` carries the identifier/context columns; ``o``/``dirv`` are the
        provided game fields and are used only for the orientation flag. Returns
        ``(attempt_row, [turn_row, ...])``; both are plain dicts ready for the
        Parquet schemas. Empty or too-short input returns a zero-turn attempt.
        """
        ms = np.asarray(ms, float)
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        order = np.argsort(ms, kind="stable")
        ms, x, y = ms[order], x[order], y[order]
        if o is not None:
            o = np.asarray(o, float)[order]
        if dirv is not None:
            dirv = np.asarray(dirv, float)[order]
        attempt = {**ctx, "setting": self.setting, "n_frames": int(len(ms)),
                   "duplicate_frames": int(duplicate_frames), "gaps_over_limit": 0,
                   "interpolated_frames": 0, "turns_primary": 0, "null_detections": 0,
                   "frames_speed_above_cap": 0, "frames_accel_above_cap": 0}
        for name in self.smooth_names:
            attempt[f"turns_{name}"] = 0
        if len(ms) == 0:
            return attempt, []
        keep = np.concatenate([[True], np.diff(ms) > 1e-9])
        ms, x, y = ms[keep], x[keep], y[keep]
        if o is not None:
            o = o[keep]
        if dirv is not None:
            dirv = dirv[keep]
        ot = (ms - ms[0]) / 1000.0 if len(ms) else np.zeros(0)

        attempt["n_frames"] = int(len(ms))
        if len(ms) < max(5, int(round(self.det.window_s / self.nominal_dt)) + 2):
            return attempt, []

        ft, fx, fy, inserted, gaps = fill_small_gaps(ot, x, y, self.nominal_dt, self.max_gap)
        attempt["interpolated_frames"] = int(inserted.sum())
        attempt["gaps_over_limit"] = int(sum(1 for _, _, d in gaps if d > self.max_gap))

        series: dict[str, tuple[dict, list[dict], np.ndarray, np.ndarray]] = {}
        for name, spec in self.smoother_specs.items():
            xs, ys = smooth_positions(ft, fx, fy, spec)
            d = derivatives(ft, xs, ys)
            trs = detect_turns(ft, xs, ys, self.det, derivatives=d)
            series[name] = (d, trs, xs, ys)
            attempt[f"turns_{name}"] = len(trs)
        primary_d, primary_turns = series[self.primary][0], series[self.primary][1]
        attempt["turns_primary"] = len(primary_turns)
        sp = primary_d["speed"]
        attempt["frames_speed_above_cap"] = int(np.sum(sp > self.speed_cap))
        attempt["frames_accel_above_cap"] = int(np.sum(np.abs(primary_d["tangential_accel"]) > self.accel_cap))
        # Named segmentation options: counts are reported on the complete Combine
        # file, where one extra detection pass is cheap; the game side uses the
        # configured rule only.
        if self.setting == "combine":
            _d, _t, xs_p, ys_p = series[self.primary]
            for opt in self.seg_options:
                if opt == self.det.segmentation:
                    continue
                alt = detect_turns(ft, xs_p, ys_p, replace(self.det, segmentation=opt),
                                   derivatives=_d)
                attempt[f"turns_seg_{opt}"] = len(alt)

        # shuffled-heading null: same speed and same distribution of heading
        # increments, temporal order destroyed. One detection pass per attempt.
        attempt["null_detections"] = self._null_count(ft, primary_d, ctx)

        if not primary_turns:
            return attempt, []

        rows = [self._turn_row(tr, primary_d, series, ft, inserted, ot, o, dirv, ctx)
                for tr in primary_turns]
        return attempt, rows

    # -- helpers -------------------------------------------------------------
    def _null_count(self, ft: np.ndarray, d: dict, ctx: dict) -> int:
        if self.null_reps <= 0 or len(ft) < 8:
            return 0
        speed = d["speed"]
        heading = d["heading"]
        total = 0
        key = f"{self.setting}|{ctx.get('drill_name')}|{ctx.get('event_id')}|" \
              f"{ctx.get('game_id')}|{ctx.get('play_id')}|{ctx.get('nfl_id')}"
        for rep in range(self.null_reps):
            rng = np.random.default_rng(_md5_seed(f"{key}|{rep}", self.null_offset))
            uw = np.unwrap(np.radians(heading))
            steps = np.diff(uw).copy()
            rng.shuffle(steps)
            h = uw[0] + np.concatenate([[0.0], np.cumsum(steps)])
            dt = np.diff(ft)
            v = np.maximum(speed[1:], 0.0)
            nx = np.concatenate([[0.0], np.cumsum(v * np.sin(h[1:]) * dt)])
            ny = np.concatenate([[0.0], np.cumsum(v * np.cos(h[1:]) * dt)])
            xs, ys = smooth_positions(ft, nx, ny, self.smoother_specs[self.primary])
            total += len(detect_turns(ft, xs, ys, self.det))
        return int(total)

    def _turn_row(self, tr: dict, d: dict, series: dict, ft: np.ndarray,
                  inserted: np.ndarray, ot: np.ndarray,
                  o: np.ndarray | None, dirv: np.ndarray | None,
                  ctx: dict) -> dict:
        lo, hi, apex = tr["frame_start"], tr["frame_end"], tr["frame_centre"]
        speed = d["speed"]
        tang = d["tangential_accel"]
        entry, exit_v = float(tr["entry_speed"]), float(tr["exit_speed"])
        apex_v = float(tr["apex_speed"])
        retention = exit_v / entry if entry > 1e-9 else float("nan")
        apex_ret = apex_v / entry if entry > 1e-9 else float("nan")

        # time to regain the entry speed, searched after the exit only
        if exit_v >= entry:
            regain, exhausted = 0.0, False
        else:
            regain, exhausted = float("nan"), True
            horizon = tr["t_end"] + self.regain_horizon
            j = hi + 1
            while j < len(ft) and ft[j] <= horizon:
                if speed[j] >= entry:
                    regain, exhausted = float(ft[j] - tr["t_end"]), False
                    break
                j += 1

        core_speed = speed[lo:hi + 1]
        core_tang = tang[lo:hi + 1]
        xs, ys = series[self.primary][2], series[self.primary][3]
        r_geo, resid = circle_fit(xs[lo:hi + 1], ys[lo:hi + 1])
        usable = bool(np.isfinite(r_geo) and np.isfinite(resid) and resid <= self.circle_ok)
        v2_over_r = float(core_speed.max() ** 2 / r_geo) if usable and r_geo > 1e-9 else float("nan")

        # smoother-to-smoother ranges: match each alternative's nearest turn to
        # THIS turn by centre time. A smoother that found no turn there
        # contributes nothing; the match count says how many did.
        metrics = {
            "entry_speed": [], "exit_speed": [], "apex_speed": [],
            "abs_turn_angle": [], "speed_retention": [], "peak_lateral_accel_yd_s2": [],
        }
        centre_t = float(ft[apex])
        for name, (dd, trs, _xs, _ys) in series.items():
            if not trs:
                continue
            if name == self.primary:
                alt = tr
            else:
                centres = np.asarray([float(ft[t["frame_centre"]]) for t in trs])
                idx = int(np.argmin(np.abs(centres - centre_t)))
                if abs(float(centres[idx]) - centre_t) > self.match_tol:
                    continue
                alt = trs[idx]
            e = float(alt["entry_speed"])
            metrics["entry_speed"].append(e)
            metrics["exit_speed"].append(float(alt["exit_speed"]))
            metrics["apex_speed"].append(float(alt["apex_speed"]))
            metrics["abs_turn_angle"].append(abs(float(alt["turn_angle"])))
            metrics["speed_retention"].append(float(alt["exit_speed"]) / e if e > 1e-9 else np.nan)
            metrics["peak_lateral_accel_yd_s2"].append(float(alt["lateral_accel_yd_s2"]))

        def rng_of(key: str) -> tuple[float, float]:
            vals = np.asarray([v for v in metrics[key] if np.isfinite(v)], float)
            if len(vals) == 0:
                return float("nan"), float("nan")
            return float(vals.min()), float(vals.max())

        # gap accounting on the ORIGINAL timeline inside the turn span
        m = (ot >= tr["t_start"] - 1e-9) & (ot <= tr["t_end"] + 1e-9)
        max_gap = float(np.max(np.diff(ot[m]))) if int(m.sum()) > 1 else float("nan")
        gap_in_turn = bool(np.isfinite(max_gap) and max_gap > 1.5 * self.nominal_dt)
        gap_above = bool(np.isfinite(max_gap) and max_gap > self.max_gap)
        interp_in_turn = int(inserted[lo:hi + 1].sum())

        speed_over = bool(np.isfinite(core_speed).any() and core_speed.max() > self.speed_cap)
        accel_over = bool(np.isfinite(core_tang).any() and np.nanmax(np.abs(core_tang)) > self.accel_cap)

        flag = None
        div_share = float("nan")
        if self.setting == "game" and o is not None and dirv is not None:
            mm = (ot >= tr["t_start"] - 1e-9) & (ot <= tr["t_end"] + 1e-9)
            if int(mm.sum()) > 0:
                sp_here = np.interp(ot[mm], ft, speed)
                ok = sp_here >= self.det.speed_floor
                if bool(ok.any()):
                    div = np.abs(wrap180(o[mm][ok] - dirv[mm][ok]))
                    div_share = float((div > self.div_deg).mean())
                    flag = bool(div_share >= self.sustain)

        abs_angle = abs(float(tr["turn_angle"]))
        is_plant = bool(
            np.isfinite(apex_ret)
            and apex_ret < self.retention_delta
            and (exit_v - apex_v) >= self.reaccel_min
            and entry >= self.det.speed_floor
            and exit_v >= self.det.speed_floor
        )

        row = {
            **ctx,
            "setting": self.setting,
            "n_frames": int(len(ot)),
            "frame_start": int(lo), "frame_centre": int(apex), "frame_end": int(hi),
            "t_start": float(tr["t_start"]), "t_end": float(tr["t_end"]),
            "duration_s": float(tr["duration_s"]),
            "entry_speed": entry, "exit_speed": exit_v, "apex_speed": apex_v,
            "turn_angle": float(tr["turn_angle"]), "abs_turn_angle": abs_angle,
            "apex_retention": apex_ret, "speed_retention": retention,
            "regain_entry_speed_s": regain, "regain_exhausted": exhausted,
            "tangential_accel_entry": float(tang[lo]),
            "tangential_accel_exit": float(tang[hi]),
            "peak_omega_deg_s": float(tr["peak_omega_deg_s"]),
            "path_radius_yd": float(tr["path_radius_yd"]),
            "path_radius_geometric_yd": r_geo,
            "circle_fit_residual_frac": resid,
            "circle_fit_usable": usable,
            "peak_lateral_accel_yd_s2": float(tr["lateral_accel_yd_s2"]),
            "lateral_accel_v2_over_r": v2_over_r,
            "turn_type": "plant" if is_plant else "arc",
            "plant": is_plant,
            "n_smoothers_matched": int(len(metrics["entry_speed"])),
            "max_gap_s": max_gap, "gap_in_turn": gap_in_turn,
            "gap_above_limit": gap_above,
            "interpolated_in_turn": min(interp_in_turn, 32767),
            "speed_above_cap": speed_over, "accel_above_cap": accel_over,
            # A turn crossed by a gap above the configured limit, or carrying a
            # physically impossible speed, is not a measurable turn. The
            # acceleration cap stays a flag: smoothing artefacts are common and
            # Phase 3 sees the column.
            "valid": bool(not gap_above and not speed_over),
            "orientation_divergence_share": div_share,
            "orientation_flag": flag,
            "x_centre": float(xs[apex]), "y_centre": float(ys[apex]),
        }
        for name, key in (("entry_speed", "entry_speed"),
                          ("exit_speed", "exit_speed"),
                          ("apex_speed", "apex_speed"),
                          ("abs_turn_angle", "abs_turn_angle"),
                          ("speed_retention", "speed_retention"),
                          ("peak_lateral_accel", "peak_lateral_accel_yd_s2")):
            lo_v, hi_v = rng_of(key)
            row[f"{name}_smoother_min"] = lo_v
            row[f"{name}_smoother_max"] = hi_v
        return row


def frame_of(rows: list[dict], schema: pa.Schema) -> pd.DataFrame:
    """DataFrame with the schema's columns and order (missing keys become NaN)."""
    cols = [f.name for f in schema]
    return pd.DataFrame(rows, columns=cols)
