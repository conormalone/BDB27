#!/usr/bin/env python3
"""02_combine_physical_time — Phase 2: Combine physical time as f(distance).

Implements, verbatim from TASK.md:

  "**2. Combine physical time** (run for real if the Combine data fits in RAM)."

  "Define motion onset in the Combine run with the prereg threshold (speed or
   displacement), identical to the in-game definition. Measure time from onset
   to reaching distance *d* along the direction of travel by interpolating the
   tracked distance curve (model-free). Use a parametric speed-time fit
   (v(t) = vmax(1 - exp(-t/tau))) only as a cross-check; it is poorly
   constrained in the first yard."

  "Compute physical time for each in-game pre-snap depth *d* ..., per attempt,
   then pool attempts per player with hierarchical shrinkage. Carry posterior
   SEs forward; never use a best attempt."

  "Global (not per-player) calibration offsets for stance and start type,
   estimated in the full run and logged in outputs/calibration.json ... Never
   calibrate from the sample."

  "Keep original attempt numbers and real clock time. Sanity checks: flag
   speeds above 12 yd/s, duplicate attempts, gaps inside the first second.
   Interpolate gaps under 0.5 s only outside the first second."

One code path for --mode sample|full. The Combine file is complete on this
machine (463,189 rows / 84 MB) and fits in RAM, so this stage computes real
Combine measurements; the *mode* still controls provenance, because the in-game
side of the eventual calibration is sample-only. Every artefact is therefore
stamped "UNVALIDATED SAMPLE OUTPUT" and no number here is a finding.

Outputs (all under outputs/phase2/ unless noted)
------------------------------------------------
combine_attempts.csv         one row per (nfl_id, attempt): onset, QC, fit
combine_attempt_times.csv    long: attempt x distance, model-free + cross-check
combine_attempt_curves.csv   per frame from onset: elapsed, along-distance
physical_time_by_player.csv  DELIVERABLE: physical time vs distance + uncertainty
hierarchical_fit.csv         per-distance variance components (auditability)
position_conflicts.csv       Q3 documented exclusion/sensitivity list
phase2_summary.json          counts, flags, disagreements, design floor
outputs/calibration.json     PLACEHOLDER (sample mode), never used downstream
"""

from __future__ import annotations

import math
import sys
from datetime import timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import physical as P  # noqa: E402
import schema as S  # noqa: E402
import stats as ST  # noqa: E402

STAGE = "02_combine_physical_time"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_combine(con, cfg) -> "object":
    """DL-and-everyone 40-yard attempts, with combine/player metadata.

    No position filter: physical time is measured at the Combine, where the
    position label is irrelevant to the kinematics. Position enters later as a
    *tag* (Q3 rule: ``lined_up_position`` is the in-game authority) and as the
    ``primary_analysis_set`` flag.
    """
    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    cr = C.read_csv_sql(C.source_path(cfg, "combine_results"), cfg)
    pl = C.read_csv_sql(C.source_path(cfg, "players"), cfg)
    drill = C.section(cfg, "physical")["drill"]
    return con.execute(f"""
        SELECT t.nfl_id, t.attempt, t.event_id, t.draft_year, t.time,
               t.x, t.y, t.s, t.a, t.dis, t.dir,
               r.combine_position, r.combine_height, r.combine_weight,
               r.forty, r.ten_yd_split,
               p.display_name
        FROM {ct} t
        LEFT JOIN {cr} r ON r.nfl_id = t.nfl_id
        LEFT JOIN {pl} p ON p.nfl_id = t.nfl_id
        WHERE t.drill_name = '{drill}'
          AND t.entity_type = 'PLAYER'
        ORDER BY t.nfl_id, t.attempt, t.time""").df()


def load_player_game_profile(con, cfg) -> "object":
    """Per-player in-game rows and alignment roles (complete label file)."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    pos = C.section(cfg, "positions")
    edge = ", ".join(f"'{p}'" for p in pos["edge_linedup"])
    interior = ", ".join(f"'{p}'" for p in pos["interior_linedup"])
    cb = ", ".join(f"'{p}'" for p in ("CB", "SLOT_CB"))
    return con.execute(f"""
        SELECT nfl_id,
               count(*) FILTER (WHERE player_get_off IS NOT NULL
                                  AND lined_up_position IN ({edge})) AS reps_edge,
               count(*) FILTER (WHERE player_get_off IS NOT NULL
                                  AND lined_up_position IN ({interior})) AS reps_interior,
               count(*) FILTER (WHERE player_get_off IS NOT NULL
                                  AND lined_up_position IN ({edge}, {interior})) AS reps_dl,
               count(*) FILTER (WHERE player_get_off IS NOT NULL
                                  AND lined_up_position IN ({cb})) AS reps_cb_slot,
               count(*) FILTER (WHERE player_get_off IS NOT NULL) AS reps_total,
               count(DISTINCT game_id) FILTER (WHERE player_get_off IS NOT NULL)
                   AS games_with_get_off,
               string_agg(DISTINCT lined_up_position, ';') AS linedup_positions
        FROM {pp}
        GROUP BY nfl_id""").df()


def load_player_directory(con, cfg) -> "object":
    """All Combine attendees with their Combine position vocabulary."""
    cr = C.read_csv_sql(C.source_path(cfg, "combine_results"), cfg)
    pl = C.read_csv_sql(C.source_path(cfg, "players"), cfg)
    return con.execute(f"""
        SELECT r.nfl_id, r.combine_position,
               p.display_name, p.draft_year, p.nfl_position
        FROM {cr} r
        LEFT JOIN {pl} p ON p.nfl_id = r.nfl_id
        ORDER BY r.nfl_id""").df()


# --------------------------------------------------------------------------- #
# Duplicate detection (data, not prose)
# --------------------------------------------------------------------------- #
def duplicate_report(df) -> dict:
    """Count duplicate attempts and duplicate frames.

    Two independent meanings, both reported:

    * ``attempt_keys``: one ``(nfl_id, attempt)`` carrying more than one
      ``event_id``. In this release ``event_id`` is the attempt identifier, so
      two events under one attempt number is a real duplication.
    * ``frame_rows``: rows beyond the first for a duplicated
      ``(nfl_id, attempt, time)`` key (the same frame emitted twice).
    """
    keys: set[tuple[int, int]] = set()
    n_attempt_keys = 0
    for (nfl_id, attempt), sub in df.groupby(["nfl_id", "attempt"], sort=True):
        if sub["event_id"].nunique(dropna=True) > 1:
            n_attempt_keys += 1
            keys.add((int(nfl_id), int(attempt)))
    dup_frame = df.duplicated(["nfl_id", "attempt", "time"], keep="first")
    n_frame_rows = int(dup_frame.sum())
    for (nfl_id, attempt) in df.loc[dup_frame, ["nfl_id", "attempt"]] \
            .drop_duplicates().itertuples(index=False, name=None):
        keys.add((int(nfl_id), int(attempt)))
    return {"attempt_keys": n_attempt_keys, "frame_rows": n_frame_rows,
            "flagged_keys": keys}


def assert_sampling_rate(df, cfg) -> dict:
    """Runtime assertion that the Combine grid is the 10 Hz the policy assumes.

    Unit/rate errors are the failure mode that silently rescales every time.
    The gate is structural (20 ms tolerance), not an analysis threshold.
    """
    hz = float(C.section(cfg, "onset")["hz"])
    tol = float(C.section(cfg, "onset")["sampling_tolerance_s"])
    nominal = 1.0 / hz
    dt = (df.sort_values(["nfl_id", "attempt", "time"])
            .groupby(["nfl_id", "attempt"], sort=True)["time"]
            .diff().dt.total_seconds().dropna())
    if dt.empty:
        raise C.SchemaError(
            "COMBINE SAMPLING GATE FAILED: no consecutive frames per attempt")
    median = float(dt.median())
    share = float(((dt > nominal - tol) & (dt < nominal + tol)).mean())
    if abs(median - nominal) > tol:
        raise C.SchemaError(
            f"COMBINE SAMPLING GATE FAILED: median frame spacing "
            f"{median:.3f}s != expected {nominal:.3f}s ({hz:g} Hz)")
    return {"hz": hz, "median_frame_s": round(median, 4),
            "tolerance_s": tol,
            "share_within_tolerance": round(share, 4), "n_gaps": int(len(dt))}


# --------------------------------------------------------------------------- #
# One attempt
# --------------------------------------------------------------------------- #
def measure_attempt(sub, cfg, duplicate_keys: set) -> tuple[dict, list, list]:
    """Measure one (nfl_id, attempt) segment.

    Returns ``(attempt_row, distance_rows, curve_rows)``. Never raises for a
    degenerate attempt: it reports ``ok=False`` and a reason, so the stage can
    count failures instead of dying on one bad trace.
    """
    o = C.section(cfg, "onset")
    phys = C.section(cfg, "physical")
    hz = float(o["hz"])
    nominal = 1.0 / hz
    distances = [float(d) for d in phys["distances_yd"]]
    first_second = float(phys["first_second_s"])

    sub = sub.sort_values("time", kind="mergesort")
    n_raw = len(sub)
    dup_mask = sub["time"].duplicated(keep="first")
    sub = sub.loc[~dup_mask]
    times_abs = sub["time"].tolist()
    if len(sub) < 4:
        row = {"nfl_id": int(sub["nfl_id"].iloc[0]),
               "attempt": int(sub["attempt"].iloc[0]),
               "event_id": str(sub["event_id"].iloc[0]),
               "draft_year": _int_or_none(sub["draft_year"].iloc[0]),
               "n_frames_raw": n_raw, "n_frames_used": len(sub),
               "duplicate_frame_rows": int(dup_mask.sum()),
               "onset_found": False, "ok": False,
               "reason": "too few frames after de-duplication"}
        return row, [], []

    t0 = times_abs[0]
    times = [(t - t0).total_seconds() for t in times_abs]
    xs = [float(x) for x in sub["x"].tolist()]
    ys = [float(y) for y in sub["y"].tolist()]
    speeds = [float(s) for s in sub["s"].tolist()]
    speeds_xy = P.speed_series(xs, ys, times)

    key = (int(sub["nfl_id"].iloc[0]), int(sub["attempt"].iloc[0]))
    row = {
        "nfl_id": key[0], "attempt": key[1],
        "event_id": str(sub["event_id"].iloc[0]),
        "draft_year": _int_or_none(sub["draft_year"].iloc[0]),
        "display_name": _str_or_none(sub["display_name"].iloc[0]),
        "combine_position": _str_or_none(sub["combine_position"].iloc[0]),
        "n_frames_raw": n_raw, "n_frames_used": len(sub),
        "duplicate_frame_rows": int(dup_mask.sum()),
        "duplicate_attempt_flag": key in duplicate_keys,
        "first_frame_utc": _iso(t0),
        "last_frame_utc": _iso(times_abs[-1]),
        "max_speed_provided": round(max(speeds), 4),
        "max_speed_xy": round(max(speeds_xy), 4),
        "speed_cap_flag": bool(max(max(speeds), max(speeds_xy))
                               > float(o["speed_cap_yds"])),
    }
    gaps = P.gap_flags(times, cfg, onset_s=None)
    row.update({"n_frame_gaps": gaps["n_gaps"],
                "max_gap_s": round(gaps["max_gap_s"], 4)})

    onset = P.detect_onset(times, speeds, cfg)
    if not onset["found"]:
        row.update({"onset_found": False, "ok": False,
                    "reason": f"onset: {onset['reason']}"})
        return row, [], []

    i0 = int(onset["index"])
    onset_s = float(onset["onset_s"])
    onset_xy = P.detect_onset(times, speeds_xy, cfg)
    onset_disp = P.detect_onset_displacement(times, xs, ys, cfg)
    first_gaps = P.gap_flags(times, cfg, onset_s=onset_s)
    row.update({
        "onset_found": True,
        "onset_s": round(onset_s, 4),
        "onset_frame_index": i0,
        "onset_utc": _iso(t0 + timedelta(seconds=onset_s)),
        "onset_threshold_speed_yds": onset["threshold"],
        "baseline_speed_yds": (round(onset["baseline_speed"], 4)
                               if onset["baseline_speed"] is not None else None),
        "gaps_first_second_after_onset": first_gaps["gaps_in_first_s_after_onset"],
    })
    if onset_xy["found"]:
        row["onset_speed_xy_s"] = round(float(onset_xy["onset_s"]), 4)
        row["onset_speed_xy_delta_s"] = round(
            float(onset_xy["onset_s"]) - onset_s, 4)
    else:
        row["onset_speed_xy_s"] = None
        row["onset_speed_xy_delta_s"] = None
    if onset_disp["found"]:
        row["onset_displacement_s"] = round(float(onset_disp["onset_s"]), 4)
        row["onset_displacement_delta_s"] = round(
            float(onset_disp["onset_s"]) - onset_s, 4)
    else:
        row["onset_displacement_s"] = None
        row["onset_displacement_delta_s"] = None

    try:
        ux, uy = P.travel_direction(xs, ys, start=i0)
    except ValueError as exc:
        row.update({"ok": False, "reason": f"direction: {exc}"})
        return row, [], []
    along = P.project_along(xs, ys, ux, uy, origin=(xs[i0], ys[i0]))
    backward = max(0.0, -min(along[i0:]))
    max_backward = float(phys["max_backward_excursion_yd"])
    row["backward_excursion_yd"] = round(backward, 4)
    row["qc_exclude"] = bool(backward > max_backward)
    row["qc_reason"] = ("backward_start" if row["qc_exclude"] else "")

    fit = P.fit_exponential_speed(times, speeds, cfg,
                                  onset_s=onset_s, onset_index=i0)
    row["crosscheck_found"] = bool(fit["found"])
    row["vmax_hat_yds"] = _round_or_none(fit.get("vmax"))
    row["tau_hat_s"] = _round_or_none(fit.get("tau"))
    row["crosscheck_rmse_yds"] = _round_or_none(fit.get("rmse"))
    row["crosscheck_n_samples"] = int(fit.get("n_samples") or 0)
    if fit["found"]:
        t_full = P.exponential_time_to_distance(fit["vmax"], fit["tau"],
                                                max(distances))
        row["crosscheck_fit_time_s"] = _round_or_none(t_full)
    else:
        row["crosscheck_fit_time_s"] = None

    distance_rows = []
    for d in distances:
        qc = P.time_at_distance_qc(
            times, along, d, first_index=i0, onset_s=onset_s,
            nominal_dt=nominal, max_gap_s=float(o["interpolate_max_gap_s"]),
            first_second_s=first_second)
        param_t = None
        disagreement = None
        if fit["found"] and qc["ok"]:
            param_t = P.exponential_time_to_distance(
                fit["vmax"], fit["tau"], d)
            if param_t is not None:
                disagreement = param_t - float(qc["elapsed_s"])
        distance_rows.append({
            "nfl_id": key[0], "attempt": key[1],
            "distance_yd": d,
            "in_game_depth_range": d <= float(
                C.section(cfg, "snaps")["max_depth_yd"]),
            "modelfree_time_s": (_round_or_none(qc["elapsed_s"])),
            "crossed": bool(qc["crossed"]),
            "interpolated": bool(qc["interpolated"]),
            "gap_policy": qc["gap_policy"],
            "bracket_gap_s": _round_or_none(qc["gap_s"]),
            "param_time_s": _round_or_none(param_t),
            "param_minus_modelfree_s": _round_or_none(disagreement),
            "ok": bool(qc["ok"]),
            "reason": qc["reason"],
        })

    curve_rows = []
    for j in range(i0, len(times)):
        curve_rows.append({
            "nfl_id": key[0], "attempt": key[1],
            "frame_index": j,
            "elapsed_s": round(times[j] - onset_s, 4),
            "along_yd": round(along[j], 4),
            "speed_provided_yds": round(speeds[j], 4),
            "speed_xy_yds": round(speeds_xy[j], 4),
        })

    row["ok"] = True
    row["reason"] = "ok"
    return row, distance_rows, curve_rows


# --------------------------------------------------------------------------- #
# Pooling
# --------------------------------------------------------------------------- #
def pool_players(attempt_rows: list[dict], distance_rows: list[dict],
                 distances: list[float], max_depth_yd: float,
                 group_by_player: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """Per-player hierarchical pooling at each distance.

    Every usable attempt contributes; there is no "best attempt" path. The
    posterior mean and SE are carried into the per-player table.
    """
    players = sorted({int(r["nfl_id"]) for r in attempt_rows})
    obs: dict[tuple[int, float], list[float]] = {}
    attempts_at: dict[tuple[int, float], list[int]] = {}
    excluded = {(int(r["nfl_id"]), int(r["attempt"])) for r in attempt_rows
                if r.get("qc_exclude")}
    for r in distance_rows:
        if (int(r["nfl_id"]), int(r["attempt"])) in excluded:
            continue
        if r["ok"] and r["modelfree_time_s"] is not None:
            key = (int(r["nfl_id"]), float(r["distance_yd"]))
            obs.setdefault(key, []).append(float(r["modelfree_time_s"]))
            attempts_at.setdefault(key, []).append(int(r["attempt"]))

    player_rows: list[dict] = []
    fit_rows: list[dict] = []
    pooling_groups = sorted(set(group_by_player.get(pid, "other")
                                for pid in players))
    for d in distances:
        for group in pooling_groups:
            members = [pid for pid in players
                       if group_by_player.get(pid, "other") == group]
            groups = [(pid, obs[(pid, d)]) for pid in members
                      if (pid, d) in obs]
            fit = ST.hierarchical_normal(groups)
            fit_rows.append({
                "pooling_group": group,
                "distance_yd": d, "n_players": fit["n_subjects"],
                "n_attempts": fit["n_obs"],
                "mu_s": _round_or_none(fit["mu"]),
                "sigma2_s2": _round_or_none(fit["sigma2"]),
                "tau2_s2": _round_or_none(fit["tau2"]),
                "pooled": bool(fit["pooled"]),
                "method": "empirical Bayes normal-normal; MoM variance components",
                "reason": fit["reason"],
            })
            by_subject = {int(rec["subject"]): rec for rec in fit["records"]}
            for pid in members:
                key = (pid, d)
                if key not in obs:
                    player_rows.append({
                        "nfl_id": pid, "pooling_group": group,
                        "distance_yd": d,
                        "in_game_depth_range": d <= float(max_depth_yd),
                        "n_attempts_used": 0,
                        "posterior_mean_s": None, "posterior_sd_s": None,
                        "posterior_var_s2": None, "raw_mean_s": None,
                        "raw_sd_s": None, "shrinkage_weight": None,
                        "pooled": False,
                        "pooling_note": "no usable attempt at this distance",
                        "attempt_numbers": "",
                    })
                    continue
                rec = by_subject[pid]
                player_rows.append({
                    "nfl_id": pid, "pooling_group": group,
                    "distance_yd": d,
                    "in_game_depth_range": d <= float(max_depth_yd),
                    "n_attempts_used": int(rec["n"]),
                    "posterior_mean_s": _round_or_none(rec["posterior_mean"]),
                    "posterior_sd_s": _round_or_none(rec["posterior_sd"]),
                    "posterior_var_s2": _round_or_none(rec["posterior_var"]),
                    "raw_mean_s": _round_or_none(rec["mean"]),
                    "raw_sd_s": _round_or_none(rec["raw_sd"]),
                    "shrinkage_weight": _round_or_none(rec["shrinkage_weight"]),
                    "pooled": bool(rec["pooled"]),
                    "pooling_note": fit["reason"],
                    "attempt_numbers": ";".join(
                        str(a) for a in sorted(attempts_at[key])),
                })
    return player_rows, fit_rows


# --------------------------------------------------------------------------- #
# Q3: position conflict report (exclusion, not silent inclusion)
# --------------------------------------------------------------------------- #
def position_profile(directory_df, game_df,
                     forty_ids: set[int]) -> tuple[list[dict], dict]:
    """Tag each Combine player with the Q3 reconciliation.

    Conor, 2026-11-05 (Q3): ``lined_up_position`` is the position/alignment
    authority; the two vocabularies are NOT required to agree. Players whose
    only get-off reps are on CB/SLOT_CB rows are excluded from the primary set
    and listed here with their rep counts, so the exclusion is visible and can
    be a sensitivity analysis. Players with EDGE reps but a DB Combine label
    stay in the primary set (the in-game label wins) and are also listed.
    """
    gmap = {int(r.nfl_id): r for r in game_df.itertuples(index=False)}
    rows: list[dict] = []
    for r in directory_df.itertuples(index=False):
        pid = int(r.nfl_id)
        g = gmap.get(pid)
        reps_edge = int(g.reps_edge) if g is not None else 0
        reps_interior = int(g.reps_interior) if g is not None else 0
        reps_dl = int(g.reps_dl) if g is not None else 0
        reps_cb = int(g.reps_cb_slot) if g is not None else 0
        reps_total = int(g.reps_total) if g is not None else 0
        roles = _str_or_none(g.linedup_positions) if g is not None else None
        cpos = _str_or_none(r.combine_position)
        if reps_dl > 0 and cpos == "DB":
            klass = "DL_REPS_ATTENDED_DB"
        elif reps_cb > 0 and cpos == "DL":
            klass = "CB_SLOT_REPS_ATTENDED_DL"
        elif reps_dl > 0 and cpos == "DL":
            klass = "CONSISTENT"
        elif reps_cb > 0:
            klass = "NON_DL_REPS"
        elif reps_total == 0:
            klass = "NO_GAME_REPS"
        else:
            klass = "OTHER"
        rows.append({
            "nfl_id": pid,
            "display_name": _str_or_none(r.display_name),
            "combine_position": cpos,
            "nfl_position": _str_or_none(getattr(r, "nfl_position", None)),
            "linedup_positions": roles,
            "reps_edge": reps_edge,
            "reps_interior": reps_interior,
            "reps_dl": reps_dl,
            "reps_cb_slot": reps_cb,
            "reps_total": reps_total,
            "primary_analysis_set": reps_dl > 0,
            "has_forty_attempt": pid in forty_ids,
            "conflict_class": klass,
        })
    edge_db = sum(1 for r in rows
                  if r["reps_edge"] > 0 and r["combine_position"] == "DB")
    int_db = sum(1 for r in rows
                 if r["reps_interior"] > 0 and r["combine_position"] == "DB")
    cbslot_dl = sum(1 for r in rows
                    if r["reps_cb_slot"] > 0 and r["combine_position"] == "DL")
    cbslot_reps = sum(r["reps_cb_slot"] for r in rows)
    summary = {
        "policy": ("Q3 (Conor, 2026-11-05): trust player_play.lined_up_position "
                   "as the position/alignment authority; do not require the "
                   "combine_position vocabulary to agree. Conflicting players "
                   "are reported and excluded where the in-game label says "
                   "non-DL; no second rule."),
        "edge_attended_db_players": edge_db,
        "interior_attended_db_players": int_db,
        "cb_slot_reps_attended_dl_players": cbslot_dl,
        "cb_slot_get_off_reps": cbslot_reps,
        "players_with_dl_reps": sum(1 for r in rows if r["primary_analysis_set"]),
        "players_with_forty_attempt": len(forty_ids),
        "note": ("The CB/SLOT_CB get-off reps are excluded from the primary DL "
                 "set but are listed per player so a sensitivity analysis can "
                 "add them back explicitly."),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# Calibration placeholder
# --------------------------------------------------------------------------- #
def calibration_payload(cfg, mode: str) -> dict:
    """Phase 2 calibration record.

    It cannot be estimated in sample mode, and it cannot be estimated in a full
    run either with the files as released: no column anywhere labels stance
    (three-point vs stand-up) or Combine start type. The global start-type
    offset also needs Phase 3's in-game onset-to-cross times. This is reported
    as a blocker rather than replaced by a guessed number.
    """
    sample = mode == "sample"
    return {
        "status": "PLACEHOLDER" if sample else "NOT_ESTIMABLE",
        "estimated": False,
        "mode": mode,
        "estimates": {"stance": None, "start_type": None,
                      "global_combine_to_game_offset_s": None},
        "reason": (
            ("TASK.md Phase 2 forbids calibrating from the sample; calibration "
             "is estimated only in a full run. " if sample else "")
            + "Stance (three-point vs stand-up) and Combine start type are not "
              "labelled in any provided file, and the global Combine-to-game "
              "offset needs Phase 3 in-game onset-to-cross times, which do not "
              "exist yet. No offset is estimated rather than inventing one."),
        "estimator_spec": {
            "target": ("per-rep residual r_ij = onset_to_cross_ij - "
                       "phys_i(d_ij)"),
            "global_offset": "precision-weighted mean of r_ij over all reps",
            "stance_offsets": ("one additive term per stance level, only if a "
                               "stance label (or a Conor-approved inference "
                               "rule) exists"),
            "applied_as": "phys_adjusted = phys_model_free + offset",
            "scope": "global, never per player",
        },
        "required_inputs": [
            "full game tracking (Phase 3 onset-to-cross times)",
            "a stance label or an approved stance-inference rule",
        ],
        "downstream_contract": (
            "Full-mode consumers must call common.require_calibration_full; it "
            "refuses a placeholder or non-estimable record. Sample-mode "
            "consumers must not apply these numbers (there are none)."),
        "blocker": "B8",
    }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _iso(value) -> str:
    return value.isoformat(timespec="milliseconds")


def _int_or_none(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return int(value)


def _str_or_none(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def _round_or_none(value, ndigits: int = 5):
    if value is None:
        return None
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, ndigits)


def _describe(values: list[float]) -> dict:
    d = ST.describe([v for v in values if v is not None])
    if d["n"] == 0:
        return {"n": 0}
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    cfg, mode, force = C.stage_bootstrap(argv, STAGE)
    if C.is_done(cfg, STAGE, mode, force):
        print(f"{STAGE}: checkpoint present for mode={mode}; use --force to rerun")
        return 0

    p = C.paths(cfg, mode)
    out = Path(p["phase2"])
    out.mkdir(parents=True, exist_ok=True)
    distances = [float(d) for d in C.section(cfg, "physical")["distances_yd"]]
    calibration = calibration_payload(cfg, mode)

    with C.timed_stage(cfg, STAGE, mode,
                       note="Phase 2 Combine physical time") as st:
        con = C.duckdb_connect(cfg)
        try:
            S.assert_schema(con, cfg, "combine_tracking")
            units = S.assert_units(con, cfg, "combine_tracking")
            for key in ("combine_results", "players", "player_play"):
                S.assert_schema(con, cfg, key)
            st.note("schema+units ok")
            df = load_combine(con, cfg)
            sampling = assert_sampling_rate(df, cfg)
            game_df = load_player_game_profile(con, cfg)
            directory = load_player_directory(con, cfg)
        finally:
            con.close()

        if df.empty:
            raise C.GateRefused(
                "STAGE 02: no FORTY_YARD_DASH rows for entity_type='PLAYER'; "
                "TASK.md Phase 2 cannot run without a straight-line sprint.")

        dup = duplicate_report(df)
        attempt_rows, distance_rows, curve_rows = [], [], []
        for (nfl_id, attempt), sub in df.groupby(["nfl_id", "attempt"],
                                                 sort=True):
            a_row, d_rows, c_rows = measure_attempt(sub, cfg, dup["flagged_keys"])
            attempt_rows.append(a_row)
            distance_rows.extend(d_rows)
            curve_rows.extend(c_rows)

        # Q3 profile first: the pooling groups depend on the primary-set rule.
        forty_ids = {int(v) for v in df["nfl_id"].unique()}
        profile_rows, profile_summary = position_profile(directory, game_df,
                                                         forty_ids)
        pmap = {r["nfl_id"]: r for r in profile_rows}
        group_by_player = {
            int(pid): ("primary_dl" if meta.get("primary_analysis_set") else "other")
            for pid, meta in pmap.items()
        }
        player_rows, fit_rows = pool_players(
            attempt_rows, distance_rows, distances,
            float(C.section(cfg, "snaps")["max_depth_yd"]),
            group_by_player)

        # Attach player-level metadata (Q3 rule + primary-set flag).
        draft_by_player = {}
        for a in attempt_rows:
            draft_by_player.setdefault(a["nfl_id"], a.get("draft_year"))
        for row in player_rows:
            meta = pmap.get(int(row["nfl_id"]), {})
            row.update({
                "display_name": meta.get("display_name"),
                "draft_year": draft_by_player.get(int(row["nfl_id"])),
                "combine_position": meta.get("combine_position"),
                "linedup_positions": meta.get("linedup_positions"),
                "reps_dl": meta.get("reps_dl", 0),
                "reps_cb_slot": meta.get("reps_cb_slot", 0),
                "primary_analysis_set": bool(meta.get("primary_analysis_set")),
                "conflict_class": meta.get("conflict_class", "NO_GAME_REPS"),
            })

        # --- design floor (Q1): computed, printed, never hardcoded -----------
        a = C.section(cfg, "audit")
        min_reps = int(a["min_pass_rush_snaps_per_rusher"])
        eligible_all = [r for r in profile_rows if r["reps_total"] >= min_reps]
        eligible_dl = [r for r in profile_rows
                       if r["primary_analysis_set"] and r["reps_dl"] >= min_reps]
        eligible_measured = [r for r in eligible_dl if r["has_forty_attempt"]]

        def _floor(rows):
            return (_round_or_none(ST.min_detectable_share(len(rows))["min_share"])
                    if rows else None)

        design = {
            "min_reps_per_player": min_reps,
            "n_players_min_reps_all_get_off": len(eligible_all),
            "floor_all_get_off": _floor(eligible_all),
            "n_players_min_reps_primary_dl": len(eligible_dl),
            "floor_primary_dl": _floor(eligible_dl),
            "n_players_min_reps_primary_dl_with_forty": len(eligible_measured),
            "median_reps_eligible": (
                _round_or_none(ST.describe(
                    [r["reps_dl"] for r in eligible_measured])["median"])
                if eligible_measured else None),
            "smallest_effect_of_interest": float(a["smallest_effect_of_interest"]),
            # The realised floor is the one that requires a physical measurement:
            # a player without a 40-yard attempt cannot enter the model.
            "smallest_detectable_share": _floor(eligible_measured),
            "method": "z*sqrt(2/P), computed from the eligible player count",
            "scope": "DESIGN (complete label file; not a sample result)",
            "note": ("Q1 cited 105 players / 0.271 from the unfiltered get-off "
                     "count. Applying the Q3 primary-set rule and requiring a "
                     "Combine 40-yard attempt reduces the design to the count "
                     "above; both floors are reported, neither is hardcoded."),
        }
        design["resolves_effect_of_interest"] = bool(
            design["smallest_detectable_share"] is not None
            and design["smallest_detectable_share"]
            <= design["smallest_effect_of_interest"])

        # --- summary --------------------------------------------------------
        ok_attempts = [r for r in attempt_rows if r.get("ok")]
        failures = {}
        for r in attempt_rows:
            if not r.get("ok"):
                key = str(r.get("reason", "unknown")).split(":")[0]
                failures[key] = failures.get(key, 0) + 1
        cross_by_distance = []
        for d in distances:
            diffs = [float(r["param_minus_modelfree_s"]) for r in distance_rows
                     if r["distance_yd"] == d
                     and r["param_minus_modelfree_s"] is not None]
            cross_by_distance.append({
                "distance_yd": d,
                "n": len(diffs),
                "median_signed_s": (_round_or_none(ST.describe(diffs)["median"])
                                    if diffs else None),
                "median_abs_s": (_round_or_none(
                    ST.describe([abs(v) for v in diffs])["median"])
                    if diffs else None),
                "p95_abs_s": (_round_or_none(
                    ST.describe([abs(v) for v in diffs])["p95"])
                    if diffs else None),
            })
        speed_deltas = [float(r["onset_speed_xy_delta_s"]) for r in attempt_rows
                        if r.get("onset_speed_xy_delta_s") is not None]
        disp_deltas = [float(r["onset_displacement_delta_s"])
                       for r in attempt_rows
                       if r.get("onset_displacement_delta_s") is not None]
        taus = [float(r["tau_hat_s"]) for r in attempt_rows
                if r.get("tau_hat_s") is not None]
        vmaxes = [float(r["vmax_hat_yds"]) for r in attempt_rows
                  if r.get("vmax_hat_yds") is not None]
        onset_vals = [float(r["onset_s"]) for r in attempt_rows
                      if r.get("onset_s") is not None]
        gap_attempts = sum(1 for r in attempt_rows
                           if int(r.get("gaps_first_second_after_onset") or 0) > 0)
        ok_counts: dict[int, int] = {}
        for r in attempt_rows:
            if r.get("ok"):
                ok_counts[int(r["nfl_id"])] = ok_counts.get(int(r["nfl_id"]), 0) + 1
        summary = {
            "stage": STAGE,
            "mode": mode,
            "drill": C.section(cfg, "physical")["drill"],
            "distance_grid_yd": distances,
            "data_scope": ("Combine tracking/results/players are complete files; "
                           "player_play is complete; game tracking is not read "
                           "by this stage. Provenance follows --mode."),
            "combine": {
                "sampling": sampling,
                "players_with_attempts": len({a["nfl_id"] for a in attempt_rows}),
                "attempts": len(attempt_rows),
                "attempts_measured_ok": len(ok_attempts),
                "attempts_failed": len(attempt_rows) - len(ok_attempts),
                "attempts_qc_excluded": sum(
                    1 for r in attempt_rows if r.get("qc_exclude")),
                "attempts_used_in_pooling": sum(
                    1 for r in attempt_rows
                    if r.get("ok") and not r.get("qc_exclude")),
                "failure_reasons": failures,
                "speed_cap_flagged_attempts": sum(
                    1 for r in attempt_rows if r.get("speed_cap_flag")),
                "duplicate_attempt_keys": dup["attempt_keys"],
                "duplicate_frame_rows": dup["frame_rows"],
                "attempts_with_first_second_gaps": gap_attempts,
                "max_gap_s": (_round_or_none(max(
                    [float(r["max_gap_s"]) for r in attempt_rows
                     if r.get("max_gap_s") is not None] or [0.0]))),
                "schema_units": {
                    "max_speed_yds": _round_or_none(units.get("smax")),
                    "max_abs_accel": _round_or_none(max(
                        abs(units.get("amin") or 0.0),
                        abs(units.get("amax") or 0.0))),
                },
            },
            "onset": {
                "method": "speed threshold, identical to the in-game definition",
                "speed_threshold_yds": float(C.section(cfg, "onset")["speed_yds"]),
                "sustained_frames": int(C.section(cfg, "onset")["sustained_frames"]),
                "hz": float(C.section(cfg, "onset")["hz"]),
                "median_onset_s": (_round_or_none(ST.describe(onset_vals)["median"])
                                   if onset_vals else None),
                "speed_xy_sensitivity": {
                    "n": len(speed_deltas),
                    "median_delta_s": (_round_or_none(
                        ST.describe(speed_deltas)["median"]) if speed_deltas else None),
                    "median_abs_delta_s": (_round_or_none(
                        ST.describe([abs(v) for v in speed_deltas])["median"])
                        if speed_deltas else None),
                },
                "displacement_sensitivity": {
                    "threshold_yd": float(
                        C.section(cfg, "onset")["displacement_yds"]),
                    "n": len(disp_deltas),
                    "median_delta_s": (_round_or_none(
                        ST.describe(disp_deltas)["median"]) if disp_deltas else None),
                    "median_abs_delta_s": (_round_or_none(
                        ST.describe([abs(v) for v in disp_deltas])["median"])
                        if disp_deltas else None),
                },
            },
            "gap_policy": {
                "first_second_clock": "from motion onset (Combine)",
                "first_second_s": float(C.section(cfg, "physical")["first_second_s"]),
                "interpolate_max_gap_s": float(
                    C.section(cfg, "onset")["interpolate_max_gap_s"]),
                "rule": ("a crossing inside a frame gap in the first second is "
                         "reported crossed-but-not-measured; gaps below the cap "
                         "are bridged only outside that second"),
            },
            "crosscheck": {
                "model": "v(t) = vmax*(1-exp(-t/tau)); CROSS-CHECK ONLY",
                "window_s": float(C.section(cfg, "physical")["crosscheck"]["window_s"]),
                "n_fits": len(taus),
                "tau_s": _describe(taus),
                "vmax_yds": _describe(vmaxes),
                "disagreement_param_minus_modelfree_by_distance": cross_by_distance,
                "note": ("reported, never used for the estimand; the fit is "
                         "poorly constrained in the first yard"),
            },
            "pooling": {
                "model": ("y_ai = theta_i + e_ai; theta_i ~ N(mu, tau2); "
                          "empirical-Bayes posterior mean and SE"),
                "per_distance": fit_rows,
                "players_with_single_attempt": sum(
                    1 for n in ok_counts.values() if n == 1),
            },
            "positions_q3": profile_summary,
            "design": design,
            "calibration": {
                "path": str(C.calibration_path(cfg)),
                "status": calibration["status"],
                "notes": calibration["reason"],
            },
            "outputs": {
                "per_player_table": str(out / "physical_time_by_player.csv"),
                "attempts": str(out / "combine_attempts.csv"),
                "attempt_times": str(out / "combine_attempt_times.csv"),
                "curves": str(out / "combine_attempt_curves.csv"),
            },
        }

        # --- write ----------------------------------------------------------
        C.write_csv(out / "combine_attempts.csv", attempt_rows,
                    ATTEMPT_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out / "combine_attempt_times.csv", distance_rows,
                    ATTEMPT_TIME_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out / "combine_attempt_curves.csv", curve_rows,
                    CURVE_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out / "physical_time_by_player.csv", player_rows,
                    PLAYER_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out / "hierarchical_fit.csv", fit_rows,
                    FIT_FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out / "position_conflicts.csv",
                    [r for r in profile_rows
                     if r["conflict_class"] in
                     ("DL_REPS_ATTENDED_DB", "CB_SLOT_REPS_ATTENDED_DL")],
                    PROFILE_FIELDS, mode=mode, cfg=cfg)
        C.write_json(C.calibration_path(cfg), calibration, mode=mode, cfg=cfg)
        C.write_json(out / "phase2_summary.json", summary, mode=mode, cfg=cfg)

        st.note(f"attempts_ok={len(ok_attempts)}/{len(attempt_rows)}; "
                f"P={design['n_players_min_reps_primary_dl_with_forty']}; "
                f"floor={design['smallest_detectable_share']}")
        print(f"{STAGE}: {len(attempt_rows)} attempts from "
              f"{summary['combine']['players_with_attempts']} players; "
              f"{len(ok_attempts)} measured")
        print(f"  per-player table: {out / 'physical_time_by_player.csv'}")
        print(f"  design floor (computed): "
              f"{design['smallest_detectable_share']} vs effect of interest "
              f"{design['smallest_effect_of_interest']}")
        print(f"  design counts: all get-off={design['n_players_min_reps_all_get_off']} "
              f"(floor {design['floor_all_get_off']}), "
              f"DL={design['n_players_min_reps_primary_dl']} "
              f"(floor {design['floor_primary_dl']}), "
              f"+ Combine 40={design['n_players_min_reps_primary_dl_with_forty']}")
        print(f"  calibration: {calibration['status']} "
              f"(placeholder; downstream full-mode consumers refuse it)")
        print(f"  Q3: EDGE-attended-as-DB players="
              f"{profile_summary['edge_attended_db_players']}, "
              f"CB/SLOT get-off reps excluded="
              f"{profile_summary['cb_slot_get_off_reps']}")

    if mode == "full" and calibration["status"] != "ESTIMATED":
        raise C.GateRefused(
            f"STAGE {STAGE}: refusing to complete a full run — calibration "
            f"status is {calibration['status']!r} (TASK.md stop condition: "
            f"calibration provenance is not FULL in a full run).")

    C.mark_done(cfg, STAGE, mode, {"attempts": len(attempt_rows),
                                   "calibration": calibration["status"]})
    return 0


ATTEMPT_FIELDS = [
    "nfl_id", "attempt", "event_id", "draft_year", "display_name",
    "combine_position", "n_frames_raw", "n_frames_used",
    "duplicate_frame_rows", "duplicate_attempt_flag",
    "first_frame_utc", "last_frame_utc",
    "max_speed_provided", "max_speed_xy", "speed_cap_flag",
    "n_frame_gaps", "max_gap_s", "gaps_first_second_after_onset",
    "onset_found", "onset_s", "onset_frame_index", "onset_utc",
    "onset_threshold_speed_yds", "baseline_speed_yds",
    "backward_excursion_yd", "qc_exclude", "qc_reason",
    "onset_speed_xy_s", "onset_speed_xy_delta_s",
    "onset_displacement_s", "onset_displacement_delta_s",
    "crosscheck_found", "vmax_hat_yds", "tau_hat_s", "crosscheck_rmse_yds",
    "crosscheck_n_samples", "crosscheck_fit_time_s",
    "ok", "reason",
]

ATTEMPT_TIME_FIELDS = [
    "nfl_id", "attempt", "distance_yd", "in_game_depth_range",
    "modelfree_time_s", "crossed", "interpolated", "gap_policy",
    "bracket_gap_s", "param_time_s", "param_minus_modelfree_s",
    "ok", "reason",
]

CURVE_FIELDS = [
    "nfl_id", "attempt", "frame_index", "elapsed_s", "along_yd",
    "speed_provided_yds", "speed_xy_yds",
]

PLAYER_FIELDS = [
    "nfl_id", "pooling_group", "display_name", "draft_year", "combine_position",
    "linedup_positions", "reps_dl", "reps_cb_slot", "primary_analysis_set",
    "conflict_class", "distance_yd", "in_game_depth_range",
    "n_attempts_used", "posterior_mean_s", "posterior_sd_s",
    "posterior_var_s2", "raw_mean_s", "raw_sd_s", "shrinkage_weight",
    "pooled", "pooling_note", "attempt_numbers",
]

FIT_FIELDS = [
    "pooling_group", "distance_yd", "n_players", "n_attempts", "mu_s",
    "sigma2_s2", "tau2_s2",
    "pooled", "method", "reason",
]

PROFILE_FIELDS = [
    "nfl_id", "display_name", "combine_position", "nfl_position",
    "linedup_positions",
    "reps_edge", "reps_interior", "reps_dl", "reps_cb_slot", "reps_total",
    "primary_analysis_set", "has_forty_attempt", "conflict_class",
]


if __name__ == "__main__":
    sys.exit(main())
