"""Turn detection for defensive backs, from tracking coordinates.

Every input is smoothed x/y/time. No provided speed, acceleration or direction
field is read: ``s``, ``a`` and ``dir`` exist in the source files and are
ignored, because a turn has to be measured from the path the player took, not
from the vendor's own derivative of it. The one exception is game ``o``
(orientation), which is read only as a flag on how far the body lags the path;
it never enters the turn definition.

A turn is
    a heading change of at least 30 degrees across a 0.5 s window,
with some frame in that window above 2.5 yd/s (Combine) or 2.0 yd/s (game), and
with the turn's entry and exit speeds (the approach speed maximum and the exit
speed maximum either side of the plant) both above that floor.

Features per turn: entry / apex / exit speed, signed and absolute turn angle,
duration, path radius, peak lateral acceleration, speed retention (exit/entry),
apex retention (apex/entry), time to regain the entry speed, and a plant/arc
label. A plant is apex retention below 0.8 with at least 0.2 yd/s of
re-acceleration out of the apex; anything else is an arc.

Turns of 180 degrees or more are reversals. They are measured as plants —
apex speed, exit speed, regain time — and never through the v*omega quantities
(path radius, lateral acceleration), which are not defined when the velocity
passes through zero.

Run ``python turns.py`` to build the turn table the other scripts read. It
writes ``figures/turns.parquet`` and prints the counts.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(os.environ.get("BDB27_DATA", "data"))
OUT_DIR = Path(os.environ.get("BDB27_OUT", "figures"))
TURNS_PATH = OUT_DIR / "turns.parquet"

#: Absolute turn angle bands used everywhere: half-open, last band open-ended.
BAND_EDGES = (30.0, 45.0, 60.0, 90.0, 120.0, 150.0, 180.0)
BAND_LABELS = ("30-45", "45-60", "60-90", "90-120", "120-150", "150-180", ">180")

#: Game position families, from the NFL position on the players table.
DB_POSITIONS = ("CB", "SS", "FS", "S", "DB")

#: Detector and segmenter settings. Combine and game differ only in the floor.
WINDOW_S = 0.5
MIN_TURN_ANGLE_DEG = 30.0
SPEED_FLOOR = {"combine": 2.5, "game": 2.0}
MIN_SEPARATION_S = 0.3
MAX_TURN_DURATION_S = 2.0
SPEED_WALK_TOLERANCE = 0.2

NOMINAL_DT = 0.1
MAX_GAP_S = 0.5
SAVGOL_WINDOW = 9
SAVGOL_ORDER = 3

PLANT_RETENTION_DELTA = 0.8
PLANT_REACCEL_MIN = 0.2
REGAIN_HORIZON_S = 3.0
CIRCLE_FIT_OK_RESIDUAL = 0.25

SPEED_CAP_YD_S = 12.0
ACCEL_CAP_YD_S2 = 15.0
ORIENTATION_DIVERGENCE_DEG = 60.0
ORIENTATION_SUSTAIN_SHARE = 0.5

REVERSAL_DEG = 180.0


class MissingInput(RuntimeError):
    """A required file or column is absent. The only condition that stops a side."""


# --------------------------------------------------------------------- input
def _candidates(name: str, data_dir: Path) -> list[Path]:
    return [data_dir / f"{name}.parquet", data_dir / f"{name}.csv"]


def load_table(name: str, data_dir: Path | str = DATA_DIR,
               columns: list[str] | None = None,
               keep: tuple[str, set] | None = None) -> pd.DataFrame:
    """Read ``data/<name>.parquet`` or ``.csv``; name every path tried on failure.

    ``keep`` restricts rows to one column's values, pushed into the Parquet read
    where possible so a big player table is never fully resident.
    """
    data_dir = Path(data_dir)
    tried = _candidates(name, data_dir)
    for path in tried:
        if not path.exists():
            continue
        if path.suffix == ".parquet":
            if keep is None:
                return pd.read_parquet(path, columns=columns)
            import pyarrow.dataset as pads
            table = pads.dataset(path, format="parquet").to_table(
                columns=columns, filter=pads.field(keep[0]).isin(list(keep[1])))
            return table.to_pandas()
        df = pd.read_csv(path, usecols=columns)
        if keep is not None:
            df = df[df[keep[0]].isin(keep[1])]
        return df
    raise MissingInput(
        f"cannot find {name}: tried\n    "
        + "\n    ".join(str(p) for p in tried)
        + f"\n  put {name}.parquet (or {name}.csv) in {data_dir}/ "
        f"or set BDB27_DATA to the folder that holds it")


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise MissingInput(
            f"{name} is missing required column(s) {missing}; found {list(df.columns)}")


# ----------------------------------------------------------------- small maths
def wrap180(deg):
    """Wrap an angular difference into [-180, 180)."""
    return (np.asarray(deg) + 180.0) % 360.0 - 180.0


def heading_deg(vx: np.ndarray, vy: np.ndarray) -> np.ndarray:
    """Heading in degrees: 0 = +y, increasing clockwise (the NFL ``dir`` axis)."""
    return np.degrees(np.arctan2(vx, vy)) % 360.0


def savgol(v: np.ndarray, window: int = SAVGOL_WINDOW,
           order: int = SAVGOL_ORDER) -> np.ndarray:
    """Savitzky-Golay smoothing, with the window clipped to the series length."""
    from scipy.signal import savgol_filter

    n = len(v)
    w = min(int(window), n if n % 2 == 1 else n - 1)
    if w < 3:
        return np.asarray(v, float).copy()
    return savgol_filter(np.asarray(v, float), w, min(int(order), w - 1),
                         mode="interp")


def smooth_positions(t, x, y):
    """Smooth both coordinates with identical parameters."""
    return savgol(x), savgol(y)


def derivatives(t: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> dict:
    """Velocity, speed, heading and its derivatives, from smoothed coordinates."""
    vx = np.gradient(xs, t)
    vy = np.gradient(ys, t)
    speed = np.hypot(vx, vy)
    ahead = heading_deg(vx, vy)
    unwrapped = np.degrees(np.unwrap(np.radians(ahead)))
    return {
        "vx": vx, "vy": vy, "speed": speed, "heading": ahead,
        "heading_unwrapped": unwrapped,
        "omega_deg_s": np.gradient(unwrapped, t),
        "tangential_accel": np.gradient(speed, t),
    }


def fill_small_gaps(t, x, y, max_gap: float = MAX_GAP_S):
    """Linearly fill frame gaps up to ``max_gap`` so the filter sees a regular grid.

    Returns ``(t, x, y, inserted, gaps)``. Positions are only ever used through
    the smoothed series; a gap longer than ``max_gap`` is left as found, and a
    turn containing one is marked invalid downstream.
    """
    n = len(t)
    if n == 0:
        return t, x, y, np.zeros(0, bool), []
    ft, fx, fy, ins = [float(t[0])], [float(x[0])], [float(y[0])], [False]
    gaps = []
    for i in range(1, n):
        d = float(t[i] - t[i - 1])
        if d <= 1.5 * NOMINAL_DT:
            ft.append(float(t[i])); fx.append(float(x[i])); fy.append(float(y[i]))
            ins.append(False)
            continue
        gaps.append((float(t[i - 1]), float(t[i]), d))
        k = int(round(d / NOMINAL_DT))
        if d <= max_gap and k >= 2:
            for j in range(1, k):
                w = j / k
                ft.append(float(t[i - 1] + w * d))
                fx.append(float(x[i - 1] * (1 - w) + x[i] * w))
                fy.append(float(y[i - 1] * (1 - w) + y[i] * w))
                ins.append(True)
        ft.append(float(t[i])); fx.append(float(x[i])); fy.append(float(y[i]))
        ins.append(False)
    return (np.asarray(ft), np.asarray(fx), np.asarray(fy),
            np.asarray(ins, bool), gaps)


def circle_fit(xs: np.ndarray, ys: np.ndarray):
    """Algebraic (Kasa) circle fit; returns ``(radius, rms residual / radius)``."""
    if len(xs) < 4:
        return float("nan"), float("nan")
    A = np.column_stack([xs, ys, np.ones(len(xs))])
    b = xs ** 2 + ys ** 2
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return float("nan"), float("nan")
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r2 = sol[2] + cx ** 2 + cy ** 2
    if not np.isfinite(r2) or r2 <= 0:
        return float("nan"), float("nan")
    r = float(np.sqrt(r2))
    if r > 1e4:  # statistically straight: no radius is estimable
        return float("nan"), float("nan")
    return r, float(np.sqrt(np.mean((np.hypot(xs - cx, ys - cy) - r) ** 2)) / r)


# -------------------------------------------------------------------- detector
@dataclass(frozen=True)
class Detector:
    speed_floor: float
    window_s: float = WINDOW_S
    min_turn_angle_deg: float = MIN_TURN_ANGLE_DEG
    min_separation_s: float = MIN_SEPARATION_S
    max_duration_s: float = MAX_TURN_DURATION_S
    speed_walk_tolerance: float = SPEED_WALK_TOLERANCE


def detect_turns(t: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                 params: Detector, derivs: dict | None = None) -> list[dict]:
    """Trigger on heading change, segment at the speed maxima, accept on floor.

    The segmentation is what makes a plant measurable. At 10 Hz the heading is
    undefined when the speed passes through zero, so a 180 degree plant
    measured inside a fixed 0.5 s window records a 30 degree turn. Walking out
    from the speed minimum to the approach maximum and the exit maximum puts
    the measurement on the two valid sides of the pause.
    """
    n = len(t)
    if n < 3:
        return []
    dt = float(np.median(np.diff(t)))
    if dt <= 0 or not np.isfinite(dt):
        return []
    w = max(2, int(round(params.window_s / dt)))
    if n < w + 2:
        return []
    d = derivs if derivs is not None else derivatives(t, xs, ys)
    speed = d["speed"]
    unwrapped = d["heading_unwrapped"]
    omega_all = d["omega_deg_s"]

    span = unwrapped[w:] - unwrapped[:-w]
    start = np.arange(len(span))
    peak_in_window = np.array([speed[i:i + w + 1].max() for i in start])
    score = np.where(peak_in_window >= params.speed_floor, np.abs(span), 0.0)
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
    turns: list[dict] = []
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
        om = float(np.max(np.abs(omega_all[lo:hi + 1])))
        turns.append({
            "frame_start": int(lo), "frame_centre": int(apex), "frame_end": int(hi),
            "t_start": float(t[lo]), "t_end": float(t[hi]),
            "entry_speed": entry, "exit_speed": exit_v,
            "apex_speed": float(speed[apex]),
            "turn_angle": angle,
            "duration_s": float(t[hi] - t[lo]),
            "peak_omega_deg_s": om,
            "max_gap_s": float(np.max(np.diff(t[lo:hi + 1]))) if hi > lo else np.nan,
            "x_centre": float(xs[apex]), "y_centre": float(ys[apex]),
        })
    return turns


# ---------------------------------------------------------------------- engine
def analyse_attempt(ms, x, y, ctx: dict, setting: str,
                    o: np.ndarray | None = None,
                    dirv: np.ndarray | None = None) -> tuple[dict, list[dict]]:
    """Smooth, detect and characterise one attempt or one player's game snap.

    Returns ``(attempt_summary, [turn_row, ...])``. An attempt too short to
    measure returns zero turns rather than raising; the denominator is kept so
    a caller can print it.
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

    attempt = {**ctx, "setting": setting, "n_frames": int(len(ms)), "turns": 0}
    if len(ms) == 0:
        return attempt, []
    keep = np.concatenate([[True], np.diff(ms) > 1e-9])
    ms, x, y = ms[keep], x[keep], y[keep]
    if o is not None:
        o = o[keep]
    if dirv is not None:
        dirv = dirv[keep]
    ot = (ms - ms[0]) / 1000.0
    attempt["n_frames"] = int(len(ms))
    w = int(round(WINDOW_S / NOMINAL_DT))
    if len(ms) < max(5, w + 2):
        return attempt, []

    ft, fx, fy, inserted, gaps = fill_small_gaps(ot, x, y)
    params = Detector(speed_floor=SPEED_FLOOR[setting])
    xs, ys = smooth_positions(ft, fx, fy)
    d = derivatives(ft, xs, ys)
    turns = detect_turns(ft, xs, ys, params, derivs=d)
    attempt["turns"] = len(turns)

    rows = [_turn_row(tr, d, xs, ys, ft, inserted, ot, o, dirv, setting, ctx, params)
            for tr in turns]
    return attempt, rows


def _turn_row(tr: dict, d: dict, xs: np.ndarray, ys: np.ndarray, ft: np.ndarray,
              inserted: np.ndarray, ot: np.ndarray, o, dirv, setting: str,
              ctx: dict, params: Detector) -> dict:
    lo, hi, apex = tr["frame_start"], tr["frame_end"], tr["frame_centre"]
    speed, tang = d["speed"], d["tangential_accel"]
    entry, exit_v, apex_v = (float(tr["entry_speed"]), float(tr["exit_speed"]),
                             float(tr["apex_speed"]))
    retention = exit_v / entry if entry > 1e-9 else float("nan")
    apex_ret = apex_v / entry if entry > 1e-9 else float("nan")

    # time to regain the entry speed, searched forward from the exit only
    if exit_v >= entry:
        regain, exhausted = 0.0, False
    else:
        regain, exhausted = float("nan"), True
        horizon = tr["t_end"] + REGAIN_HORIZON_S
        j = hi + 1
        while j < len(ft) and ft[j] <= horizon:
            if speed[j] >= entry:
                regain, exhausted = float(ft[j] - tr["t_end"]), False
                break
            j += 1

    abs_angle = abs(float(tr["turn_angle"]))
    reversal = abs_angle >= REVERSAL_DEG

    core_speed = speed[lo:hi + 1]
    r_geo, resid = circle_fit(xs[lo:hi + 1], ys[lo:hi + 1])
    usable = bool(np.isfinite(r_geo) and np.isfinite(resid)
                  and resid <= CIRCLE_FIT_OK_RESIDUAL and not reversal)
    # v*omega radius and lateral acceleration are not defined for a reversal,
    # where the velocity passes through zero: those turns are measured as plants.
    om = float(tr["peak_omega_deg_s"])
    radius_vw = float(entry / np.radians(om)) if om > 1e-6 and not reversal else float("nan")
    lateral = float(entry * np.radians(om)) if om > 1e-6 and not reversal else float("nan")

    # gap accounting on the original timeline inside the turn span
    m = (ot >= tr["t_start"] - 1e-9) & (ot <= tr["t_end"] + 1e-9)
    max_gap = float(np.max(np.diff(ot[m]))) if int(m.sum()) > 1 else float("nan")
    gap_above = bool(np.isfinite(max_gap) and max_gap > MAX_GAP_S)
    speed_over = bool(np.isfinite(core_speed).any() and core_speed.max() > SPEED_CAP_YD_S)
    accel_over = bool(np.isfinite(tang[lo:hi + 1]).any()
                      and np.nanmax(np.abs(tang[lo:hi + 1])) > ACCEL_CAP_YD_S2)

    div_share, flag = float("nan"), None
    if setting == "game" and o is not None and dirv is not None and int(m.sum()) > 0:
        sp_here = np.interp(ot[m], ft, speed)
        ok = sp_here >= params.speed_floor
        if bool(ok.any()):
            div = np.abs(wrap180(o[m][ok] - dirv[m][ok]))
            div_share = float((div > ORIENTATION_DIVERGENCE_DEG).mean())
            flag = bool(div_share >= ORIENTATION_SUSTAIN_SHARE)

    is_plant = bool(
        np.isfinite(apex_ret)
        and apex_ret < PLANT_RETENTION_DELTA
        and (exit_v - apex_v) >= PLANT_REACCEL_MIN
        and entry >= params.speed_floor
        and exit_v >= params.speed_floor
    )

    return {
        **ctx, "setting": setting, "n_frames": int(len(ot)),
        "frame_start": int(lo), "frame_centre": int(apex), "frame_end": int(hi),
        "t_start": float(tr["t_start"]), "t_end": float(tr["t_end"]),
        "duration_s": float(tr["duration_s"]),
        "entry_speed": entry, "exit_speed": exit_v, "apex_speed": apex_v,
        "turn_angle": float(tr["turn_angle"]), "abs_turn_angle": abs_angle,
        "apex_retention": apex_ret, "speed_retention": retention,
        "regain_entry_speed_s": regain, "regain_exhausted": exhausted,
        "peak_omega_deg_s": om,
        "path_radius_yd": radius_vw,
        "path_radius_geometric_yd": r_geo if not reversal else float("nan"),
        "circle_fit_residual_frac": resid if not reversal else float("nan"),
        "circle_fit_usable": usable,
        "peak_lateral_accel_yd_s2": lateral,
        "reversal": bool(reversal),
        "turn_type": "plant" if is_plant else "arc",
        "plant": is_plant,
        "max_gap_s": max_gap, "gap_above_limit": gap_above,
        "speed_above_cap": speed_over, "accel_above_cap": accel_over,
        "valid": bool(not gap_above and not speed_over),
        "orientation_divergence_share": div_share,
        "orientation_flag": flag,
        "x_centre": float(xs[apex]), "y_centre": float(ys[apex]),
    }


# ----------------------------------------------------------------- generators
def _epoch_ms(series: pd.Series) -> np.ndarray:
    """Milliseconds since the epoch from a datetime or timestamp column."""
    if pd.api.types.is_numeric_dtype(series):
        return series.to_numpy(float)
    # astype on a datetime goes through its own resolution (us here), so pin the
    # unit first rather than assuming nanoseconds.
    return pd.to_datetime(series).astype("datetime64[ms]").astype("int64").to_numpy(float)


def combine_units(data_dir: Path, positions: tuple[str, ...] = ("DB",),
                  stats: dict | None = None):
    """Yield ``(ctx, ms, x, y)`` for each Combine attempt of a DB.

    A player is a DB when ``combine_results.combine_position`` says so; the
    tracking file's ``drill_type`` is not used to decide who counts.
    """
    if stats is None:
        stats = {}
    stats.setdefault("short_attempts", 0)
    tracking = load_table("combine_tracking", data_dir)
    _require_columns(tracking, ("event_id", "nfl_id", "attempt", "time",
                                "x", "y", "drill_name", "entity_type"), "combine_tracking")
    results = load_table("combine_results", data_dir)
    _require_columns(results, ("nfl_id", "combine_position"), "combine_results")
    players = load_table("players", data_dir)
    _require_columns(players, ("nfl_id", "draft_year", "nfl_position"), "players")

    pos_map = results.set_index("nfl_id")["combine_position"].to_dict()
    draft = players.set_index("nfl_id")["draft_year"].to_dict()
    cohort = {int(k) for k, v in pos_map.items() if str(v) in positions}

    df = tracking[tracking["entity_type"].astype(str).str.upper() == "PLAYER"].copy()
    df["nfl_id"] = df["nfl_id"].astype("int64")
    df = df[df["nfl_id"].isin(cohort)]
    df["ms"] = _epoch_ms(df["time"])
    df = df.drop_duplicates(["nfl_id", "event_id", "attempt", "ms"])
    df = df.sort_values(["drill_name", "event_id", "nfl_id", "attempt", "ms"],
                        kind="stable")
    for (drill, event_id, nfl_id, attempt), g in df.groupby(
            ["drill_name", "event_id", "nfl_id", "attempt"], sort=False):
        ctx = {
            "drill_name": str(drill), "event_id": str(event_id),
            "attempt": int(attempt), "game_id": None, "play_id": None,
            "nfl_id": int(nfl_id), "position_group": str(pos_map.get(int(nfl_id))),
            "snap_type": None,
            "draft_year": (int(draft[int(nfl_id)]) if int(nfl_id) in draft
                           and pd.notna(draft[int(nfl_id)]) else None),
        }
        if len(g) < 5:
            stats["short_attempts"] += 1
        yield ctx, g["ms"].to_numpy(), g["x"].to_numpy(float), g["y"].to_numpy(float)


def game_units(data_dir: Path, games_per_chunk: int = 8):
    """Yield ``(ctx, ms, x, y, o, dir)`` per DB player-snap, one game chunk at a time.

    The tracking file is streamed in chunks of games so the full season files
    never sit in memory at once.
    """
    import pyarrow.dataset as pads
    import pyarrow.parquet as pq

    columns = ["game_id", "play_id", "nfl_id", "time", "x", "y", "o", "dir"]
    path = None
    for cand in _candidates("game_tracking", Path(data_dir)):
        if cand.exists():
            path = cand
            break
    if path is None:
        raise MissingInput(
            "cannot find game_tracking: tried\n    "
            + "\n    ".join(str(p) for p in _candidates("game_tracking", Path(data_dir)))
            + f"\n  put game_tracking.parquet (or .csv) in {data_dir}/ "
            "or set BDB27_DATA to the folder that holds it")

    players = load_table("players", data_dir)
    _require_columns(players, ("nfl_id", "nfl_position"), "players")
    pos = players.set_index("nfl_id")["nfl_position"].to_dict()
    db_ids = {int(k) for k, v in pos.items() if str(v) in DB_POSITIONS}

    play = load_table("player_play", data_dir,
                      columns=["game_id", "play_id", "nfl_id", "team_abbr",
                               "coverage_assignment", "lined_up_position",
                               "route_ran", "player_get_off"],
                      keep=("nfl_id", db_ids))
    _require_columns(play, ("game_id", "play_id", "nfl_id", "team_abbr",
                            "coverage_assignment", "lined_up_position",
                            "route_ran", "player_get_off"), "player_play")

    def snap_type(row) -> str:
        # Same precedence as the published snap classification: a route is a
        # pass, a get-off is a rush, a coverage assignment is coverage, else other.
        if pd.notna(row.get("route_ran")) and row.get("route_ran") not in ("", "NA"):
            return "ROUTE"
        if pd.notna(row.get("player_get_off")) and row.get("player_get_off") not in ("", "NA"):
            return "PASS_RUSH"
        cov = row.get("coverage_assignment")
        if pd.notna(cov) and cov not in ("", "NA", "NO_ASSIGNMENT"):
            return "COVERAGE"
        return "OTHER"

    n_rows = pq.ParquetFile(path).metadata.num_rows if path.suffix == ".parquet" else None
    if n_rows is not None:
        game_ids = []
        for batch in pq.ParquetFile(path).iter_batches(columns=["game_id"],
                                                       batch_size=1_000_000):
            game_ids.append(pd.unique(batch.column(0).to_pandas()))
        game_ids = np.unique(np.concatenate(game_ids)) if game_ids else np.array([])
        dataset = pads.dataset(path, format="parquet")
        for i in range(0, len(game_ids), games_per_chunk):
            chunk = [int(g) for g in game_ids[i:i + games_per_chunk]]
            table = dataset.to_table(columns=columns,
                                     filter=pads.field("game_id").isin(chunk))
            yield from _game_chunk(table.to_pandas(), play, pos, db_ids, snap_type)
    else:
        df = pd.read_csv(path, usecols=columns)
        for gid, g in df.groupby("game_id"):
            yield from _game_chunk(g, play, pos, db_ids, snap_type)


def _game_chunk(df: pd.DataFrame, play: pd.DataFrame, pos: dict,
                db_ids: set[int], snap_type) -> list:
    out = []
    if df.empty:
        return out
    pp = play.set_index(["game_id", "play_id", "nfl_id"]).to_dict("index")
    df = df.sort_values(["game_id", "play_id", "nfl_id", "time"], kind="stable")
    df["ms"] = _epoch_ms(df["time"])
    # duplicate timestamps inside a player-snap are dropped, first kept
    df = df.drop_duplicates(["game_id", "play_id", "nfl_id", "ms"])
    for (gid, pid, nid), g in df.groupby(["game_id", "play_id", "nfl_id"],
                                         sort=True):
        nid = int(nid)
        if nid not in db_ids:
            continue
        info = pp.get((gid, pid, nid), {})
        ctx = {
            "drill_name": None, "event_id": None, "attempt": None,
            "game_id": int(gid), "play_id": int(pid), "nfl_id": nid,
            "position_group": "DB",
            "snap_type": snap_type(info),
            "draft_year": None,
        }
        out.append((ctx, g["ms"].to_numpy(), g["x"].to_numpy(float),
                    g["y"].to_numpy(float), g["o"].to_numpy(float),
                    g["dir"].to_numpy(float)))
    return out


# ---------------------------------------------------------------------- build
def build_turns(data_dir: Path | str = DATA_DIR, *, combine: bool = True,
                game: bool = True, verbose: bool = True) -> pd.DataFrame:
    """Build the turn table for whichever sides are available."""
    data_dir = Path(data_dir)
    rows: list[dict] = []
    attempts = {"combine": 0, "game": 0}

    if combine:
        try:
            stats: dict = {}
            for ctx, ms, x, y in combine_units(data_dir, stats=stats):
                att, trs = analyse_attempt(ms, x, y, ctx, "combine")
                attempts["combine"] += 1
                rows.extend(trs)
            if verbose:
                print(f"combine: {attempts['combine']:,} attempts analysed "
                      f"({stats.get('short_attempts', 0):,} shorter than the "
                      "7 frames the detector needs)")
        except MissingInput as exc:
            if verbose:
                print(f"combine: skipped — {exc}")

    if game:
        try:
            for ctx, ms, x, y, o, dirv in game_units(data_dir):
                att, trs = analyse_attempt(ms, x, y, ctx, "game", o=o, dirv=dirv)
                attempts["game"] += 1
                rows.extend(trs)
            if verbose:
                print(f"game: {attempts['game']:,} player-snaps analysed")
        except MissingInput as exc:
            if verbose:
                print(f"game: skipped — {exc}")

    df = pd.DataFrame(rows)
    if df.empty:
        if verbose:
            print("no turns were built: no side had the files it needs")
        return df
    df["band"] = band_of(df["abs_turn_angle"])
    if verbose:
        summarise(df)
    return df


def band_of(angles: pd.Series) -> pd.Series:
    """Band label for each absolute turn angle; NaN below 30 degrees."""
    a = pd.to_numeric(angles, errors="coerce")
    out = pd.Series(np.nan, index=a.index, dtype=object)
    for i in range(len(BAND_EDGES) - 1):
        out[(a >= BAND_EDGES[i]) & (a < BAND_EDGES[i + 1])] = BAND_LABELS[i]
    out[a >= BAND_EDGES[-1]] = BAND_LABELS[-1]
    return out


def valid_turns(df: pd.DataFrame, setting: str | None = None) -> pd.DataFrame:
    if df.empty or "valid" not in df.columns:
        return df.iloc[0:0]
    out = df[df["valid"].fillna(False).astype(bool)]
    if setting is not None:
        out = out[out["setting"] == setting]
    return out[out["position_group"] == "DB"]


def summarise(df: pd.DataFrame) -> None:
    for setting in ("combine", "game"):
        part = valid_turns(df, setting)
        if part.empty:
            continue
        print(f"{setting}: {len(part):,} valid DB turns from "
              f"{part['nfl_id'].nunique():,} players")
        counts = part.groupby("band", observed=False).size()
        total = len(part)
        line = "  ".join(
            f"{label} {int(counts.get(label, 0)):,} "
            f"({counts.get(label, 0) / total * 100:.2f}%)"
            for label in BAND_LABELS)
        print("  " + line)


def load_turns(*, rebuild: bool = False, data_dir: Path | str = DATA_DIR) -> pd.DataFrame:
    """Read the cached turn table, building it if it is absent."""
    if TURNS_PATH.exists() and not rebuild:
        return pd.read_parquet(TURNS_PATH)
    df = build_turns(data_dir)
    if not df.empty:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(TURNS_PATH, index=False)
    return df


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default=str(DATA_DIR))
    p.add_argument("--out", default=str(TURNS_PATH))
    p.add_argument("--force", action="store_true", help="rebuild the cache")
    p.add_argument("--combine-only", action="store_true")
    p.add_argument("--game-only", action="store_true")
    args = p.parse_args(argv)
    if Path(args.out).exists() and not args.force:
        df = pd.read_parquet(args.out)
        print(f"turn table already built: {args.out} ({len(df):,} rows); "
              "pass --force to rebuild")
        summarise(df)
        return 0
    df = build_turns(args.data, combine=not args.game_only,
                     game=not args.combine_only)
    if df.empty:
        return 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"wrote {args.out} ({len(df):,} turn rows)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MissingInput as exc:
        raise SystemExit(f"turns.py: {exc}")
