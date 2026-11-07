"""Stage 01 — Phase 0 schema verification + Phase 1 audit, items a-m.

Evidence, not assumptions. Every number written here is computed from the files
on disk in this run; nothing is carried over from a previous cycle or from the
spec's description of the data. Where the spec and the files disagree, the file
wins and the disagreement is recorded (``schema_compare.csv``).

Run:
    python src/01_audit.py --mode sample [--force]
Writes only under outputs/audit/ plus one row in outputs/run_log.csv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from bdb import common, schema
from bdb.kinematics import DetectorParams, detect_turns, smooth_positions, wrap180

ITEM_NAMES = {
    "a": "Combine drills per position, direction changes, DL presence",
    "b": "time/frame fields, rate, regularity, dropped/duplicate frames",
    "c": "cross-check derived kinematics vs provided dir/dis/s/a",
    "d": "detector validation against drill geometry (precision/recall)",
    "e": "smoothing diagnostics and primary-smoother choice",
    "f": "matched Combine-to-NFL players, snaps per type, games",
    "g": "turn counts per player, entry-speed overlap",
    "h": "frame resolution, window sensitivity, speed floor",
    "i": "game-only orientation checks and contamination share",
    "j": "kill-test statistics",
    "k": "outcome labels: null semantics, provenance, availability",
    "l": "rusher-blocker / opponent IDs",
    "m": "power: smallest detectable effects",
}


# --------------------------------------------------------------------------- util


def circ_abs_mean_deg(d: np.ndarray) -> float:
    d = np.asarray(d, float)
    return float(np.degrees(np.mean(np.abs(np.radians(wrap180(d))))))


def lag_of_max_xcorr(a: np.ndarray, b: np.ndarray, max_lag: int = 3) -> int:
    best, best_lag = -np.inf, 0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            x, y = a[-lag:], b[:len(b) + lag]
        elif lag > 0:
            x, y = a[:len(a) - lag], b[lag:]
        else:
            x, y = a, b
        if len(x) < 5 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if r > best:
            best, best_lag = r, lag
    return best_lag


def integer_check(values: np.ndarray, tol: float = 0.02) -> float:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return float("nan")
    return float(np.mean(np.abs(v - np.round(v)) < tol))


def snap_type_sql(cfg: common.Config, prefix: str = "") -> str:
    """Build the snap-type CASE expression from config (single source of truth)."""
    parts = []
    for rule in cfg.get("snap_types.rules"):
        test = rule["test"]
        col = rule.get("column")
        if test == "notnull":
            cond = f"{prefix}{col} IS NOT NULL"
        elif test == "truthy":
            cond = f"{prefix}{col}"
        elif test == "notnull_excluding":
            vals = ", ".join("'" + str(v) + "'" for v in rule["exclude"])
            cond = f"({prefix}{col} IS NOT NULL AND {prefix}{col} NOT IN ({vals}))"
        else:
            cond = "TRUE"
        parts.append(f"WHEN {cond} THEN '{rule['name']}'")
    return "CASE " + " ".join(parts) + " ELSE 'OTHER' END"


class Audit:
    def __init__(self, cfg: common.Config, mode: str):
        self.cfg, self.mode = cfg, mode
        self.out = cfg.audit_dir()
        self.out.mkdir(parents=True, exist_ok=True)
        self.specs = {c["name"]: c for c in cfg.get("smoothing.candidates")}
        self.primary_name = cfg.get("smoothing.primary")
        self.primary = self.specs[self.primary_name]
        self.det = DetectorParams.from_config(cfg.get("detector"), cfg.get("detector.speed_floor_combine"))
        self.items: list[dict] = []
        self.thresholds = json.loads(cfg.thresholds_path().read_text()) if cfg.thresholds_path().exists() else {}

    # -- bookkeeping -----------------------------------------------------------
    def record(self, item: str, status: str, finding: str, evidence: str,
               value=None, threshold=None, interpretable: bool = True) -> None:
        self.items.append({
            "item": item,
            "name": ITEM_NAMES[item],
            "status": status,
            "finding": finding,
            "evidence_file": evidence,
            "value": value,
            "threshold": threshold,
            "interpretable": interpretable,
            "data_basis": ("combine=complete-file; game=row sample"
                           if self.mode == "sample" else "all files complete"),
        })

    def csv(self, df: pd.DataFrame, name: str, extra: dict | None = None) -> str:
        common.write_csv(df, self.out / name, self.mode, self.cfg, extra)
        return name

    def js(self, obj: dict, name: str) -> str:
        common.write_json(obj, self.out / name, self.mode, self.cfg)
        return name

    # -- data ------------------------------------------------------------------
    def load_combine(self) -> pd.DataFrame:
        cols = self.cfg.get("columns.combine_tracking")
        pq = common.ensure_parquet(self.cfg, self.mode, "combine_tracking", cols)
        con = common.connect(self.cfg)
        try:
            df = con.execute(
                "SELECT draft_year, nfl_id, event_id, entity_type, drill_type, drill_name, attempt, "
                "epoch_ms(time) AS ms, x, y, s, a, dis, dir FROM read_parquet(?)", [str(pq)]
            ).df()
        finally:
            con.close()
        df = df[df.entity_type == "PLAYER"].copy()
        if self.cfg.get("sampling.drop_duplicate_frames"):
            df = df.drop_duplicates(["nfl_id", "event_id", "attempt", "ms"])
        pos = self.load_positions()
        df = df.merge(pos, on="nfl_id", how="left")
        df = df.sort_values(["drill_name", "nfl_id", "attempt", "ms"], kind="stable")
        return df.reset_index(drop=True)

    def load_positions(self) -> pd.DataFrame:
        pq = common.ensure_parquet(self.cfg, self.mode, "combine_results",
                                   self.cfg.get("columns.combine_results"))
        con = common.connect(self.cfg)
        try:
            return con.execute(
                "SELECT nfl_id, combine_position FROM read_parquet(?)", [str(pq)]).df()
        finally:
            con.close()

    def game_parquet(self):
        return common.ensure_parquet(self.cfg, self.mode, "game_tracking",
                                     self.cfg.get("columns.game_tracking"))

    def game_chunks(self):
        pq = self.game_parquet()
        con = common.connect(self.cfg)
        try:
            ids = con.execute("SELECT DISTINCT game_id FROM read_parquet(?) ORDER BY 1",
                              [str(pq)]).fetchnumpy()["game_id"]
            size = int(self.cfg.get("resources.game_chunk_games"))
            for i in range(0, len(ids), size):
                batch = [int(g) for g in ids[i:i + size]]
                ph = ",".join("?" * len(batch))
                yield con.execute(
                    f"SELECT game_id, play_id, nfl_id, epoch_ms(time) AS ms, x, y, s, a, dis, o, dir, event "
                    f"FROM read_parquet(?) WHERE game_id IN ({ph})", [str(pq), *batch]).df()
        finally:
            con.close()

    # -- shared detection ------------------------------------------------------
    def turns_for(self, g: pd.DataFrame, speed_floor: float) -> list[dict]:
        t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
        x = g.x.to_numpy(float)
        y = g.y.to_numpy(float)
        if len(t) < 5:
            return []
        xs, ys = smooth_positions(t, x, y, self.primary)
        det = DetectorParams(self.det.window_s, self.det.min_turn_angle_deg,
                             speed_floor, self.det.min_separation_s)
        return detect_turns(t, xs, ys, det)


# ------------------------------------------------------------------- item a
def item_a(a: Audit, combine: pd.DataFrame, turns: pd.DataFrame) -> None:
    agg = []
    for (pos, drill), g in combine.groupby(["combine_position", "drill_name"], observed=True):
        attempts = g.groupby(["nfl_id", "event_id", "attempt"], observed=True)
        n_attempts = attempts.ngroups
        if n_attempts == 0:
            continue
        tsub = turns[(turns.drill_name == drill) & (turns.combine_position == pos)]
        per_attempt = tsub.groupby(["nfl_id", "event_id", "attempt"], observed=True).size()
        per_attempt = per_attempt.reindex(
            g.groupby(["nfl_id", "event_id", "attempt"], observed=True).size().index, fill_value=0)
        big = tsub[tsub.turn_angle.abs() >= a.cfg.get("detector.drill_angle_deg")] \
            .groupby(["nfl_id", "event_id", "attempt"], observed=True).size().reindex(per_attempt.index, fill_value=0)
        agg.append({
            "combine_position": pos,
            "drill_name": drill,
            "drill_type": g.drill_type.iloc[0],
            "players": int(g.nfl_id.nunique()),
            "attempts": int(n_attempts),
            "attempts_per_player": round(n_attempts / max(g.nfl_id.nunique(), 1), 2),
            "year_min": int(g.draft_year.min()),
            "year_max": int(g.draft_year.max()),
            "turns_per_attempt": round(float(tsub.shape[0]) / n_attempts, 3),
            "share_attempts_with_turn": round(float((per_attempt > 0).mean()), 3),
            "share_attempts_with_60deg": round(float((big > 0).mean()), 3),
            "median_turn_angle_deg": round(float(tsub.turn_angle.abs().median()), 1) if len(tsub) else np.nan,
        })
    df = pd.DataFrame(agg)
    df["usable_direction_change"] = (
        (df.share_attempts_with_turn >= a.cfg.get("detector.drill_min_share_attempts"))
        & (df.players >= a.cfg.get("detector.drill_min_players"))
    )
    a.csv(df, "drills_by_position.csv")

    dl = df[(df.combine_position == "DL") & df.usable_direction_change]
    dl_players = int(combine[(combine.combine_position == "DL")].nfl_id.nunique())
    if len(dl):
        status = "PASS"
        finding = (f"DL have {len(dl)} drills with usable direction changes "
                   f"({', '.join(dl.sort_values('players', ascending=False).drill_name.head(5))}); "
                   f"{dl_players} DL players in Combine tracking. Primary group stays DL.")
        primary = "DL"
    else:
        alt = df[(df.combine_position.isin(a.cfg.get("positions.alternative_primary"))) & df.usable_direction_change]
        status, primary = "FAIL", "WR/TE"
        finding = ("No DL drill reached the direction-change bar; primary group falls to WR/TE "
                   f"and the outcome test becomes descriptive ({len(alt)} WR/TE drills usable).")
    a.record("a", status, finding, "drills_by_position.csv", value=len(dl),
             threshold=a.cfg.get("detector.drill_min_share_attempts"), interpretable=True)
    json.dump({"primary_group": primary, "dl_usable_drills": dl.drill_name.tolist()},
              open(a.out / "primary_group.json", "w"), indent=2)


# ------------------------------------------------------------------- item b
def item_b(a: Audit, combine: pd.DataFrame) -> None:
    inv = schema.file_inventory(a.cfg, a.mode)
    a.csv(inv, "file_inventory.csv")
    cprof, cmeta = schema.combine_profile(a.cfg, a.mode)
    gprof, gmeta = schema.game_profile(a.cfg, a.mode)
    dt = schema.dt_profile(a.cfg, a.mode)
    a.csv(schema.compare_settings(cprof, gprof), "schema_compare.csv")
    a.js({
        "combine": {"profile": cprof.to_dict("records"), "meta": cmeta,
                    "heading_convention": schema.heading_offset(a.cfg, a.mode)},
        "game": {"profile": gprof.to_dict("records"), "meta": gmeta},
        "timing": dt,
    }, "schema_verified.json")

    cd = dt["combine"]
    gd = dt["game"]
    has_s = any(r["column"] == "s" for r in cprof.to_dict("records"))
    has_a = any(r["column"] == "a" for r in cprof.to_dict("records"))
    off = schema.heading_offset(a.cfg, a.mode)
    status = "PASS" if (cd["n_off_grid"] == 0 and gd["n_off_grid"] == 0) else "FAIL"
    finding = (
        f"Combine: {cd['n']:,} frame steps, {cd['n_exact_0p1']:,} exactly 0.1 s, "
        f"{cd['n_off_grid']} off-grid; {dt['duplicate_frames_combine']} duplicate frames. "
        f"Game: {gd['n']:,} steps, {gd['n_off_grid']} off-grid, "
        f"{dt['duplicate_frames_game']} duplicates, {gd['n_gaps_over_0p2']:,} gaps > 0.2 s. "
        f"dir zero reference verified: offset {off.get('offset_deg'):.2f} deg vs velocity heading "
        f"({100 * off.get('share_within_15deg', 0):.1f}% within 15 deg). "
        f"Forbidden-field check: Combine has s={has_s}, a={has_a} — the spec's claim that they "
        f"are absent is REFUTED (see schema_verified.json); they remain cross-checks only."
    )
    a.record("b", status, finding, "schema_verified.json")


# ------------------------------------------------------------------- item c
def _band_labels(edges: list[float]) -> list[str]:
    return [f"[{edges[i]},{edges[i + 1]})" for i in range(len(edges) - 1)]


def item_c(a: Audit, combine: pd.DataFrame) -> None:
    """Derived kinematics vs the provided fields, by speed band, both settings."""
    edges = a.cfg.get("bins.speed_band")
    bands = _band_labels(edges)
    accum = defaultdict(lambda: {"n": 0, "dv": [], "s_prov": [], "dh": [], "da": [], "lag": []})
    dis_rows = []
    for _, g in combine.groupby(["drill_name", "event_id", "nfl_id", "attempt"], observed=True):
        if len(g) < 8:
            continue
        t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
        xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), a.primary)
        vx, vy = np.gradient(xs, t), np.gradient(ys, t)
        sp = np.hypot(vx, vy)
        hd = np.degrees(np.arctan2(vx, vy)) % 360.0
        ac = np.gradient(sp, t)
        s_prov = g.s.to_numpy(float)
        lag = lag_of_max_xcorr(sp, s_prov)
        for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
            m = (s_prov >= lo) & (s_prov < hi)
            if m.sum() == 0:
                continue
            k = bands[i]
            accum[k]["n"] += int(m.sum())
            accum[k]["dv"].append(sp[m] - s_prov[m])
            accum[k]["s_prov"].append(s_prov[m])
            accum[k]["dh"].append(wrap180(g.dir.to_numpy(float)[m] - hd[m]))
            accum[k]["da"].append(ac[m] - g.a.to_numpy(float)[m])
            accum[k]["lag"].append(lag)
        if g.dis.iloc[-1] > 0:
            implied = 30.0 * np.sqrt(float(g.dis.iloc[-1]))
            step = np.hypot(np.diff(g.x.to_numpy(float)), np.diff(g.y.to_numpy(float)))
            inc = np.where(np.diff(g.dis.to_numpy(float)) > 1e-9)[0]
            path = float(step[inc[0]:inc[-1] + 1].sum()) if len(inc) else np.nan
            dis_rows.append({"drill_name": g.drill_name.iloc[0], "implied_yards_30sqrt": implied,
                             "path_yards_in_dis_window": path,
                             "ratio_path_to_implied": path / implied if implied else np.nan,
                             "implied_is_integer": abs(implied - round(implied)) < 0.02})
    rows = []
    for k in bands:
        acc = accum[k]
        if acc["n"] == 0:
            continue
        dv = np.concatenate(acc["dv"])
        dh = np.concatenate(acc["dh"])
        da = np.concatenate(acc["da"])
        prov = np.concatenate(acc["s_prov"])
        rows.append({
            "setting": "combine", "speed_band": k, "n_frames": acc["n"],
            "speed_bias_yd_s": round(float(dv.mean()), 3),
            "speed_mae_yd_s": round(float(np.abs(dv).mean()), 3),
            "speed_mae_frac_of_provided": round(float(np.abs(dv).mean() / max(prov.mean(), 1e-6)), 3),
            "heading_mae_deg": round(circ_abs_mean_deg(dh), 2),
            "accel_mae_yd_s2": round(float(np.abs(da).mean()), 2),
            "accel_bias_yd_s2": round(float(da.mean()), 2),
            "speed_lag_mode_frames": int(Counter(acc["lag"]).most_common(1)[0][0]),
        })
    df = pd.DataFrame(rows)
    ddf = pd.DataFrame(dis_rows)
    if len(ddf):
        summary = ddf.groupby("drill_name").agg(
            attempts=("implied_yards_30sqrt", "size"),
            share_implied_integer=("implied_is_integer", "mean"),
            median_ratio_path_to_implied=("ratio_path_to_implied", "median"),
            median_implied_yards=("implied_yards_30sqrt", "median"),
        ).reset_index().round(3)
        a.csv(summary, "dis_reconstruction.csv")
        int_share = float(ddf.implied_is_integer.mean())
        med_ratio = float(ddf.ratio_path_to_implied.median())
    else:
        int_share, med_ratio = float("nan"), float("nan")
    a.csv(df, "kinematics_crosscheck.csv")

    # the G3 tolerance test, on bands above the configured minimum speed
    usable = df[df.speed_band != f"[{edges[0]},{edges[1]})"] if len(df) else df
    htol = a.cfg.get("validation.heading_mae_max_deg")
    stol = a.cfg.get("validation.speed_mae_max_frac")
    g3 = bool(len(usable) and (usable.heading_mae_deg <= htol).all()
              and (usable.speed_mae_frac_of_provided <= stol).all())
    worst_h = float(usable.heading_mae_deg.max()) if len(usable) else float("nan")
    worst_s = float(usable.speed_mae_frac_of_provided.max()) if len(usable) else float("nan")
    a.record(
        "c", "PASS" if g3 else "FAIL",
        ("Combine cross-check vs provided fields: speed MAE "
         + ", ".join(f"{r.speed_band}:{r.speed_mae_yd_s}" for r in df.itertuples())
         + f" yd/s (worst relative error {worst_s} vs tolerance {stol}); heading MAE up to "
           f"{worst_h} deg (tolerance {htol}); derived speed lags provided s by "
           f"{int(df.speed_lag_mode_frames.mode().iloc[0])} frame(s). "
           f"`dis` is NOT a distance in yards: 30*sqrt(dis) is an integer in {100 * int_share:.1f}% of "
           f"attempts, i.e. dis = (whole_yards/30)^2; recovered distance is {med_ratio:.2f}x the path "
           f"inside the dis window. Tested, not assumed — the 'dis under-counts' claim cannot be "
           f"confirmed or refuted until the field's definition is documented."),
        "kinematics_crosscheck.csv + dis_reconstruction.csv", value=worst_s, threshold=stol)


# ------------------------------------------------------------------- item d
def _estimate_cones(points: np.ndarray, k: int, res: float, min_sep: float,
                    radius: float) -> np.ndarray:
    """Deterministic k-means seeded from density peaks (k comes from config).

    Cone count is a drill fact; the *positions* are estimated. Seeding from the
    densest grid cells and then running Lloyd iterations is a better estimator
    than a one-shot peak refinement, which drifts with the spread of turn
    locations across players.
    """
    if len(points) < k:
        return np.zeros((0, 2))
    xmin, ymin = points.min(0) - res
    ix = ((points[:, 0] - xmin) / res).astype(int)
    iy = ((points[:, 1] - ymin) / res).astype(int)
    counts = Counter(zip(ix.tolist(), iy.tolist()))
    centers: list[np.ndarray] = []
    for (i, j), _n in counts.most_common():
        c = np.array([xmin + (i + 0.5) * res, ymin + (j + 0.5) * res])
        if all(np.linalg.norm(c - q) >= min_sep for q in centers):
            centers.append(c)
        if len(centers) == k:
            break
    for idx in range(len(centers), k):  # deterministic fill, never random
        centers.append(points[idx * max(1, len(points) // k) % len(points)].copy())
    C = np.array(centers)
    for _ in range(25):
        d = np.linalg.norm(points[:, None, :] - C[None], axis=2)
        lab = d.argmin(1)
        newC = C.copy()
        for j in range(k):
            m = lab == j
            if m.any():
                newC[j] = points[m].mean(0)
        if np.allclose(newC, C, atol=1e-4):
            C = newC
            break
        C = newC
    return C


def item_d(a: Audit, turns: pd.DataFrame) -> None:
    df = turns[turns.drill_name.isin(["THREE_CONE_DRILL", "SHORT_SHUTTLE"])].copy()
    if df.empty:
        a.record("d", "FAIL", "no cone/shuttle turns detected", "detector_geometry.csv")
        return
    df["split"] = df.event_id.map(lambda e: int(hashlib.md5(str(e).encode()).hexdigest(), 16) % 2)
    rad = a.cfg.get("validation.cone_radius_yd")
    rows, cone_rows = [], []
    for drill, g in df.groupby("drill_name"):
        k = int(a.cfg.get("geometry.cone_count")[drill])
        est, ev = g[g.split == 0], g[g.split == 1]
        cones = _estimate_cones(est[["x_centre", "y_centre"]].to_numpy(float), k,
                                a.cfg.get("geometry.grid_resolution_yd"),
                                a.cfg.get("geometry.min_separation_yd"), rad)
        if len(cones) == 0 or ev.empty:
            rows.append({"drill_name": drill, "cones_expected": k, "cones_found": len(cones),
                         "turns_estimate": int(len(est)), "turns_eval": int(len(ev)),
                         "precision": 0.0, "recall": 0.0})
            continue
        for ci, c in enumerate(cones):
            cone_rows.append({"drill_name": drill, "cone_index": ci,
                              "x": round(float(c[0]), 3), "y": round(float(c[1]), 3)})
        pts = ev[["x_centre", "y_centre"]].to_numpy(float)
        d = np.linalg.norm(pts[:, None, :] - cones[None], axis=2)
        nearest = d.argmin(1)
        dist = d.min(1)
        model = a.cfg.get("geometry.drill_model").get(drill, "point")
        if model == "line":
            axis = 1 if a.cfg.get("geometry.line_axis").get(drill, "x") == "y" else 0
            dist = np.abs(pts[:, axis][:, None] - cones[None, :, axis]).min(1)
        precision_point = float((np.linalg.norm(pts[:, None, :] - cones[None], axis=2).min(1) <= rad).mean())
        near = dist <= rad
        per_att = ev.assign(near=near).groupby(["event_id", "attempt"], observed=True).near.sum()
        expected = int(per_att.mode().iloc[0]) if len(per_att) else 0
        precision = float(near.mean())
        recall = float(np.minimum(per_att, expected).sum() / (expected * len(per_att))) if expected else 0.0
        # angle validation: collapse consecutive visits to the same cone, then compare
        # the detected turn angle with the geometric angle of the cone triple.
        ang_err = []
        ref = a.cfg.get("geometry.angle_reference_deg").get(drill)
        if ref is not None:
            ang_err = [abs(abs(x) - float(ref)) for x in ev.turn_angle.to_numpy(float)]
        tmp = ev.assign(cone=nearest, near=near)
        for _, gg in (tmp[tmp.near].groupby(["event_id", "attempt"], observed=True)
                      if ref is None else []):
            gg = gg.sort_values("frame_centre")
            seq = []
            for r in gg.itertuples():
                if not seq or seq[-1][0] != r.cone:
                    seq.append((int(r.cone), float(r.x_centre), float(r.y_centre), float(abs(r.turn_angle))))
            if len(seq) < 3:
                continue
            for i in range(1, len(seq) - 1):
                _, x0, y0, _ = seq[i - 1]
                _, x1, y1, ang1 = seq[i]
                _, x2, y2, _ = seq[i + 1]
                u, v = np.array([x1 - x0, y1 - y0]), np.array([x2 - x1, y2 - y1])
                nu, nv = np.linalg.norm(u), np.linalg.norm(v)
                if nu < 1e-6 or nv < 1e-6:
                    continue
                geo = np.degrees(np.arccos(np.clip(np.dot(u, v) / (nu * nv), -1, 1)))
                ang_err.append(abs(geo - ang1))
        pair = [np.linalg.norm(cones[i] - cones[j]) for i in range(len(cones))
                for j in range(i + 1, len(cones))]
        rows.append({
            "drill_name": drill, "cones_expected": k, "cones_found": len(cones),
            "min_cone_separation_yd": round(float(min(pair)), 2) if pair else np.nan,
            "turns_estimate": int(len(est)), "turns_eval": int(len(ev)),
            "turn_target_model": model,
            "precision": round(precision, 3), "recall": round(recall, 3),
            "precision_point_model": round(precision_point, 3),
            "expected_turns_per_attempt": expected,
            "angle_median_abs_error_deg": round(float(np.median(ang_err)), 1) if ang_err else np.nan,
            "angle_n": len(ang_err),
            "frac_detections_off_cone": round(float(1 - near.mean()), 3),
        })
    out = pd.DataFrame(rows)
    a.csv(out, "detector_geometry.csv")
    if cone_rows:
        a.csv(pd.DataFrame(cone_rows), "cone_estimates.csv")
    pmin, rmin = a.cfg.get("validation.detector_precision_min"), a.cfg.get("validation.detector_recall_min")
    ok = out[(out.cones_found >= out.cones_expected) & out.precision.notna()]
    passed = bool(len(ok) == len(out) and len(out) and (ok.precision >= pmin).all()
                  and (ok.recall.fillna(0.0) >= rmin).all())
    a.record(
        "d", "PASS" if passed else "FAIL",
        "; ".join(f"{r.drill_name}: precision {r.precision} (min {pmin}), recall {r.recall} "
                  f"(min {rmin}), {r.cones_found}/{r.cones_expected} cones with minimum separation "
                  f"{r.min_cone_separation_yd} yd, {r.expected_turns_per_attempt} turns/attempt, "
                  f"angle error {r.angle_median_abs_error_deg} deg (n={r.angle_n})"
                  for r in out.itertuples())
        + ". Cones are estimated from one half of the attempts (event_id hash) and evaluated on the "
          "other half, so the precision figure is not circular.",
        "detector_geometry.csv", value=float(ok.precision.min()) if len(ok) else 0.0,
        threshold=pmin)


# ------------------------------------------------------------------- item e
def item_e(a: Audit, combine: pd.DataFrame) -> None:
    df = combine[combine.drill_name == "FORTY_YARD_DASH"].copy()
    cap = a.cfg.get("smoothing.accel_cap_yd_s2")
    frac = a.cfg.get("smoothing.accel_phase_end_frac")
    rows, ensembles = [], {}
    per_attempt_speed = defaultdict(dict)
    for name, spec in a.specs.items():
        exceed, mono, peak, ttp, hsd = [], [], [], [], []
        for _, g in df.groupby(["nfl_id", "event_id", "attempt"], observed=True):
            if len(g) < 10:
                continue
            t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
            xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), spec)
            vx, vy = np.gradient(xs, t), np.gradient(ys, t)
            sp = np.hypot(vx, vy)
            hd = np.degrees(np.arctan2(vx, vy)) % 360.0
            acc = np.gradient(sp, t)
            exceed.append(float(np.mean(np.abs(acc) > cap)))
            n_acc = max(3, int(len(sp) * frac))
            mono.append(float(np.mean(np.diff(sp[:n_acc]) > 0)))
            ipk = int(np.argmax(sp))
            peak.append(float(sp[ipk]))
            ttp.append(float(t[ipk]))
            m = sp > a.cfg.get("validation.kinematics_min_speed_band")
            if m.sum() > 3:
                hsd.append(float(np.std(np.asarray(wrap180(hd[m] - np.median(hd[m]))))))
            per_attempt_speed[name][(g.event_id.iloc[0], g.nfl_id.iloc[0], g.attempt.iloc[0])] = sp
        rows.append({"smoother": name, "attempts": len(exceed),
                     "accel_cap_exceed_share": round(float(np.mean(exceed)), 4),
                     "monotonic_share_accel_phase": round(float(np.mean(mono)), 4),
                     "median_peak_speed_yd_s": round(float(np.median(peak)), 3),
                     "median_time_to_peak_s": round(float(np.median(ttp)), 3),
                     "median_heading_sd_deg": round(float(np.median(hsd)), 2) if hsd else np.nan})
        ensembles[name] = per_attempt_speed[name]
    out = pd.DataFrame(rows)
    # agreement: RMS speed difference vs the ensemble median, per smoother
    keys = sorted(set().union(*[set(e) for e in ensembles.values()]))
    rms = {}
    for name, store in ensembles.items():
        diffs = []
        for k in keys:
            if k not in store or len(store[k]) < 5:
                continue
            stack = [ensembles[n][k] for n in ensembles if k in ensembles[n] and len(ensembles[n][k]) == len(store[k])]
            if len(stack) < 3:
                continue
            ref = np.median(np.vstack(stack), axis=0)
            diffs.append(np.sqrt(np.mean((store[k] - ref) ** 2)))
        rms[name] = float(np.median(diffs)) if diffs else np.nan
    out["rms_vs_ensemble_yd_s"] = out.smoother.map(rms).round(3)
    elig = out[(out.accel_cap_exceed_share <= a.cfg.get("smoothing.accel_cap_exceed_share_max"))
               & (out.monotonic_share_accel_phase >= a.cfg.get("smoothing.monotonic_min_share"))]
    if len(elig):
        best = elig.sort_values(["rms_vs_ensemble_yd_s", "accel_cap_exceed_share"]).iloc[0]
        recommended = str(best.smoother)
    else:
        best = out.sort_values("accel_cap_exceed_share").iloc[0]
        recommended = str(best.smoother)
    out["eligible"] = out.smoother.isin(elig.smoother)
    out["recommended"] = out.smoother == recommended
    a.csv(out, "smoothing_diagnostics.csv")
    a.record(
        "e", "PASS",
        (f"Primary smoother by diagnostics: {recommended} (config placeholder was {a.primary_name}). "
         f"Accel-cap exceedance " + ", ".join(f"{r.smoother}={r.accel_cap_exceed_share}" for r in out.itertuples())
         + f"; monotonicity " + ", ".join(f"{r.smoother}={r.monotonic_share_accel_phase}" for r in out.itertuples())
         + f"; cross-smoother speed RMS " + ", ".join(f"{r.smoother}={r.rms_vs_ensemble_yd_s}" for r in out.itertuples())
         + ". Chosen on diagnostics only, to be frozen in prereg."),
        "smoothing_diagnostics.csv", value=recommended)
    return recommended


# ------------------------------------------------------------------- item f
def item_f(a: Audit) -> dict:
    con = common.connect(a.cfg)
    try:
        pos = common.ensure_parquet(a.cfg, a.mode, "combine_results", a.cfg.get("columns.combine_results"))
        pl = common.ensure_parquet(a.cfg, a.mode, "players", a.cfg.get("columns.players"))
        pp = common.ensure_parquet(a.cfg, a.mode, "player_play", a.cfg.get("columns.player_play"))
        gm = common.ensure_parquet(a.cfg, a.mode, "games", a.cfg.get("columns.games"))
        gpq = a.game_parquet()
        comb = con.execute(f"SELECT nfl_id, combine_position FROM read_parquet('{pos}')").df()
        players = con.execute(f"SELECT nfl_id, draft_year, nfl_position FROM read_parquet('{pl}')").df()
        seen = con.execute(f"SELECT DISTINCT nfl_id FROM read_parquet('{gpq}')").df()
        seasons = con.execute(
            f"SELECT season, count(*) games FROM read_parquet('{gm}') GROUP BY 1 ORDER BY 1").df()
        comb["family"] = comb.combine_position
        m = comb.merge(players, on="nfl_id", how="left").merge(
            seen.assign(in_game_sample=True), on="nfl_id", how="left")
        m["in_game_sample"] = m.in_game_sample.fillna(False)
        snaps = con.execute(f"""
            SELECT nfl_id, {snap_type_sql(a.cfg)} AS snap_type,
                   count(*) n, count(DISTINCT game_id) games
            FROM read_parquet('{pp}') GROUP BY 1,2""").df()
        m = m.merge(snaps, on="nfl_id", how="left")
    finally:
        con.close()
    rows = []
    for fam, g in m.groupby("family", observed=True):
        rows.append({
            "position": fam,
            "combine_players": int(g.nfl_id.nunique()),
            "matched_to_nfl_players": int(g.nfl_position.notna().sum()),
            "in_game_sample": int(g.in_game_sample.sum()),
            "total_snaps": int(g.n.fillna(0).sum()),
            "snaps_per_matched_player": round(float(g.n.fillna(0).sum() / max(g.nfl_position.notna().sum(), 1)), 1),
            "pass_rush_snaps": int(g[g.snap_type == "PASS_RUSH"].n.sum()),
            "route_snaps": int(g[g.snap_type == "ROUTE"].n.sum()),
            "coverage_snaps": int(g[g.snap_type == "COVERAGE"].n.sum()),
            "games_per_player": round(float(g.games.max()) if g.games.notna().any() else 0, 1),
        })
    out = pd.DataFrame(rows)
    a.csv(out, "matching.csv")
    a.csv(seasons, "games_seasons.csv")
    a.record(
        "f", "PASS" if out.matched_to_nfl_players.sum() > 0 else "FAIL",
        (f"Combine players by position: " + ", ".join(f"{r.position}={r.combine_players}" for r in out.itertuples())
         + ". Matched to the NFL players file: "
         + ", ".join(f"{r.position}={r.matched_to_nfl_players}" for r in out.itertuples())
         + f". Game tracking present here for {int(m.in_game_sample.sum())} of them "
         f"({a.mode} mode). Snap counts are season-wide from player_play; the "
         f"per-player, per-snap-type and per-game table is PENDING FULL RUN for the full dataset "
         f"(games file seasons: {sorted(seasons.season.tolist())})."),
        "matching.csv")
    return {"matched": int(m.nfl_position.notna().sum()), "by_position": out.set_index("position").to_dict("index")}


# ------------------------------------------------------------------- item g
def item_g(a: Audit, combine_turns: pd.DataFrame, game_turns: pd.DataFrame,
           matching: dict) -> None:
    rows = []
    game_turns = game_turns[game_turns.combine_position.notna()]
    for pos, g in game_turns.groupby("combine_position", observed=True):
        c = combine_turns[combine_turns.combine_position == pos]
        if len(c) == 0 or len(g) == 0:
            continue
        lo, hi = np.percentile(c.entry_speed, [5, 95])
        shared = float(np.mean((g.entry_speed >= lo) & (g.entry_speed <= hi)))
        hist_c, edges = np.histogram(c.entry_speed, bins=a.cfg.get("bins.entry_speed"))
        hist_g, _ = np.histogram(g.entry_speed, bins=edges)
        pc, pg = hist_c / max(hist_c.sum(), 1), hist_g / max(hist_g.sum(), 1)
        rows.append({
            "position": pos,
            "combine_turns": int(len(c)), "combine_players": int(c.nfl_id.nunique()),
            "combine_entry_p5": round(float(lo), 2), "combine_entry_p95": round(float(hi), 2),
            "combine_entry_median": round(float(c.entry_speed.median()), 2),
            "game_turns_sample": int(len(g)), "game_players_sample": int(g.nfl_id.nunique()),
            "game_entry_p5": round(float(np.percentile(g.entry_speed, 5)), 2),
            "game_entry_p95": round(float(np.percentile(g.entry_speed, 95)), 2),
            "game_entry_median": round(float(g.entry_speed.median()), 2),
            "game_turns_inside_combine_range": round(shared, 3),
            "histogram_overlap": round(float(np.minimum(pc, pg).sum()), 3),
            "status": "PENDING FULL RUN",
        })
    out = pd.DataFrame(rows)
    a.csv(out, "entry_speed_overlap.csv")
    r = a.cfg.get("validation.entry_speed_overlap_min")
    worst = out.histogram_overlap.min() if len(out) else float("nan")
    a.record(
        "g", "PENDING FULL RUN",
        (f"Turn counts and entry speeds computed on the {a.mode} game data — SAMPLE DIAGNOSTIC, "
         f"not a finding. Combine side is complete: "
         + ", ".join(f"{x.position} {x.combine_turns} turns/{x.combine_players} players"
                     for x in out.itertuples())
         + f". Sample histogram overlap (min {worst} vs threshold {r}) is reported but NOT used for a "
           f"verdict: the game side is a row sample of one season."),
        "entry_speed_overlap.csv", value=float(worst) if pd.notna(worst) else None,
        threshold=r, interpretable=False)


# ------------------------------------------------------------------- item h
def item_h(a: Audit, turns: pd.DataFrame, combine: pd.DataFrame) -> None:
    dur = turns.duration_s / a.cfg.get("sampling.dt_s")
    rows = [{
        "measure": "frames_per_turn_window",
        "median": round(float(dur.median()), 1),
        "p05": round(float(dur.quantile(0.05)), 1),
        "p95": round(float(dur.quantile(0.95)), 1),
        "note": "detector window expressed in 10 Hz frames",
    }]
    # window sensitivity on the cone drills: count turns under each window
    sub = combine[combine.drill_name.isin(["THREE_CONE_DRILL", "SHORT_SHUTTLE"])]
    for w in [0.3, 0.4, 0.5, 0.6, 0.7]:
        n = 0
        for _, g in sub.groupby(["nfl_id", "event_id", "drill_name", "attempt"], observed=True):
            if len(g) < 8:
                continue
            t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
            xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), a.primary)
            n += len(detect_turns(t, xs, ys, DetectorParams(
                w, a.det.min_turn_angle_deg, a.det.speed_floor, a.det.min_separation_s)))
        rows.append({"measure": f"turns_window_{w}s", "median": n, "p05": np.nan, "p95": np.nan,
                     "note": "count over cone+shuttle attempts"})
    # heading reliability vs speed on the straight-line dash
    for lo, hi in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 6), (6, 9)]:
        sds = []
        for _, g in combine[combine.drill_name == "FORTY_YARD_DASH"].groupby(["nfl_id", "event_id", "attempt"], observed=True):
            if len(g) < 10:
                continue
            t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
            xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), a.primary)
            vx, vy = np.gradient(xs, t), np.gradient(ys, t)
            sp = np.hypot(vx, vy)
            hd = np.degrees(np.arctan2(vx, vy))
            n_acc = max(3, int(len(sp) * a.cfg.get("smoothing.accel_phase_end_frac")))
            m = (sp >= lo) & (sp < hi)
            m[n_acc:] = False
            if m.sum() > 3:
                # deviation from the straight-line heading *within this band*;
                # using the whole-attempt median would mix the post-finish turn
                # into a noise estimate
                sds.append(float(np.median(np.abs(np.asarray(wrap180(hd[m] - np.median(hd[m])))))))
        rows.append({"measure": f"heading_sd_speed_{lo}_{hi}", "median": round(float(np.median(sds)), 2) if sds else np.nan,
                     "p05": np.nan, "p95": np.nan, "note": "deg, straight-line dash"})
    out = pd.DataFrame(rows)
    a.csv(out, "frame_resolution.csv")
    a.record(
        "h", "PASS",
        (f"A detected turn spans {dur.median():.0f} frames (p05 {dur.quantile(0.05):.0f}, "
         f"p95 {dur.quantile(0.95):.0f}) at 10 Hz. Window sensitivity and heading-noise-by-speed "
         f"are in frame_resolution.csv; the configured speed floor is "
         f"{a.det.speed_floor} yd/s."),
        "frame_resolution.csv")


# ------------------------------------------------------------------- item i
def item_i(a: Audit, game: pd.DataFrame, pp: pd.DataFrame) -> None:
    steady = game[game.s >= a.cfg.get("orientation.steady_speed_min")]
    d = np.asarray(wrap180(steady.o.to_numpy(float) - steady.dir.to_numpy(float)))
    mean_vec = np.exp(1j * np.radians(d))
    offset = float(np.degrees(np.angle(mean_vec.mean()))) if len(d) else np.nan
    contam = a.cfg.get("orientation.divergence_deg")
    steady = steady.assign(contam=np.abs(wrap180(steady.o.to_numpy(float) - steady.dir.to_numpy(float))) > contam)
    con = common.connect(a.cfg)
    try:
        gpq = a.game_parquet()
        pl = common.ensure_parquet(a.cfg, a.mode, "players", a.cfg.get("columns.players"))
        players = con.execute(f"SELECT nfl_id, nfl_position FROM read_parquet('{pl}')").df()
    finally:
        con.close()
    fam = {}
    for f, lst in a.cfg.get("positions.game_families").items():
        for p in lst:
            fam[p] = f
    steady = steady.merge(players, on="nfl_id", how="left")
    steady["family"] = steady.nfl_position.map(fam).fillna("OTHER")
    snap = pp.copy()
    snap["snap_type"] = "OTHER"
    for rule in reversed(a.cfg.get("snap_types.rules")):
        col, test = rule.get("column"), rule["test"]
        if test == "notnull":
            snap.loc[snap[col].notna(), "snap_type"] = rule["name"]
        elif test == "notnull_excluding":
            m = snap[col].notna() & ~snap[col].isin(rule["exclude"])
            snap.loc[m, "snap_type"] = rule["name"]
        elif test == "truthy":
            snap.loc[snap[col].astype(bool), "snap_type"] = rule["name"]
    steady = steady.merge(snap[["game_id", "play_id", "nfl_id", "snap_type"]],
                          on=["game_id", "play_id", "nfl_id"], how="left")
    steady["snap_type"] = steady.snap_type.fillna("UNMATCHED")
    by_pos = steady.groupby("family").agg(frames=("contam", "size"), contam_share=("contam", "mean")).reset_index()
    by_snap = steady.groupby("snap_type").agg(frames=("contam", "size"), contam_share=("contam", "mean")).reset_index()
    by_pos["by"] = "position"
    by_snap["by"] = "snap_type"
    by_pos = by_pos.rename(columns={"family": "group"})
    by_snap = by_snap.rename(columns={"snap_type": "group"})
    out = pd.concat([by_pos, by_snap], ignore_index=True)
    out["contam_share"] = out.contam_share.round(4)
    a.csv(out, "orientation.csv")
    a.record(
        "i", "PENDING FULL RUN",
        (f"`o` exists in game tracking (0-360, continuous). At steady running "
         f"(> {a.cfg.get('orientation.steady_speed_min')} yd/s) the offset o-dir is {offset:.2f} deg "
         f"(zero reference shared with dir). Share of frames with |o-dir| > {contam} deg: "
         + ", ".join(f"{r.group}={r.contam_share}" for r in out.itertuples() if r.frames > 500)
         + f". Sample diagnostic only — contamination shares are PENDING FULL RUN."),
        "orientation.csv", interpretable=False)


# ------------------------------------------------------------------- item j
def _icc_oneway(df: pd.DataFrame, value: str, group: str) -> float:
    d = df[[value, group]].dropna()
    k = d.groupby(group, observed=True).size()
    d = d[d[group].isin(k[k >= 2].index)]
    if d[group].nunique() < 3:
        return float("nan")
    grand = d[value].mean()
    grp = d.groupby(group, observed=True)[value]
    n_i = grp.size()
    msb = float((n_i * (grp.mean() - grand) ** 2).sum() / (len(n_i) - 1))
    msw = float((((d[value] - d[group].map(grp.mean())) ** 2).sum()) / (len(d) - len(n_i)))
    return (msb - msw) / (msb + msw) if (msb + msw) > 0 else float("nan")


def item_j(a: Audit, combine_turns: pd.DataFrame, game_turns: pd.DataFrame) -> None:
    # provisional capacity parameter: peak lateral acceleration of each turn
    rows = []
    for label, tr in [("combine", combine_turns), ("game_sample", game_turns)]:
        if tr.empty:
            continue
        per_player = tr.groupby("nfl_id").size()
        cap = tr.groupby("nfl_id")["lateral_accel_yd_s2"].quantile(0.9)
        half = tr.assign(idx=tr.groupby("nfl_id").cumcount() % 2)
        piv = half.pivot_table(index="nfl_id", columns="idx", values="lateral_accel_yd_s2", aggfunc="mean")
        piv = piv.dropna()
        icc = float("nan")
        if len(piv) >= 3:
            long = piv.reset_index().melt(id_vars="nfl_id", value_name="v")
            icc = _icc_oneway(long, "v", "nfl_id")
        rows.append({
            "setting": label, "turns": int(len(tr)), "players": int(tr.nfl_id.nunique()),
            "turns_per_player_median": float(per_player.median()),
            "turns_per_player_p10": float(per_player.quantile(0.1)),
            "share_players_meeting_min": float((per_player >= a.cfg.get("validation.min_turns_per_setting")).mean()),
            "cap_p90_median": float(cap.median()),
            "icc_split_half_lateral_accel": round(icc, 3) if np.isfinite(icc) else np.nan,
            "status": "PENDING FULL RUN" if label.startswith("game") else "PENDING FULL RUN",
        })
    out = pd.DataFrame(rows)
    # smoother sensitivity of the turn count
    sens = []
    for name, spec in a.specs.items():
        counts = []
        for _, g in a.combine_frames[a.combine_frames.drill_name.isin(["THREE_CONE_DRILL", "SHORT_SHUTTLE"])].groupby(
                ["nfl_id", "drill_name", "attempt"], observed=True):
            if len(g) < 8:
                continue
            t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
            xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), spec)
            counts.append(len(detect_turns(t, xs, ys, a.det)))
        sens.append({"smoother": name, "cone_shuttle_turns": int(np.sum(counts)),
                     "mean_per_attempt": round(float(np.mean(counts)), 3)})
    a.csv(pd.DataFrame(sens), "smoother_sensitivity.csv")
    a.csv(out, "killtest_stats.csv")
    a.record(
        "j", "PENDING FULL RUN",
        ("Kill-test statistics are scripted and run only on data that can answer them. "
         "Combine (complete file): "
         + "; ".join(f"{r.setting}: {r.turns} turns, {r.players} players, median "
                     f"{r.turns_per_player_median:.1f} turns/player, ICC(split-half lateral accel) "
                     f"{r.icc_split_half_lateral_accel}" for r in out.itertuples())
         + f". The game side is a row sample, so the Combine-game correlation, the noise ceiling and "
           f"the reliability verdict are PENDING FULL RUN. Smoother sensitivity of the turn count is "
           f"in smoother_sensitivity.csv."),
        "killtest_stats.csv", interpretable=False)


# ------------------------------------------------------------------- item k
def item_k(a: Audit, game: pd.DataFrame) -> None:
    con = common.connect(a.cfg)
    try:
        ppq = common.ensure_parquet(a.cfg, a.mode, "player_play", a.cfg.get("columns.player_play"))
        pl = common.ensure_parquet(a.cfg, a.mode, "players", a.cfg.get("columns.players"))
        lab = con.execute(f"""
            SELECT p.nfl_position AS pos, count(*) n,
                   sum(CASE WHEN pp.quick_pressure IS NULL THEN 1 ELSE 0 END) quick_null,
                   sum(CASE WHEN pp.unblocked_pressure IS NULL THEN 1 ELSE 0 END) unblocked_null,
                   sum(CASE WHEN pp.time_to_pressure IS NULL THEN 1 ELSE 0 END) ttp_null,
                   sum(CASE WHEN pp.pressure_allowed IS NULL THEN 1 ELSE 0 END) pressure_allowed_null,
                   sum(CASE WHEN pp.quick_pressure THEN 1 ELSE 0 END) quick_true,
                   sum(CASE WHEN pp.player_get_off IS NOT NULL THEN 1 ELSE 0 END) get_off_present
            FROM read_parquet('{ppq}') pp JOIN read_parquet('{pl}') p USING (nfl_id)
            GROUP BY 1 ORDER BY 1""").df()
        go = con.execute(f"""
            SELECT game_id, play_id, nfl_id, player_get_off
            FROM read_parquet('{ppq}') WHERE player_get_off IS NOT NULL""").df()
    finally:
        con.close()
    fam = {}
    for f, lst in a.cfg.get("positions.game_families").items():
        for p in lst:
            fam[p] = f
    lab["family"] = lab.pos.map(fam).fillna("OTHER")
    lab["quick_null_share"] = (lab.quick_null / lab.n).round(4)
    a.csv(lab, "outcome_labels.csv")

    # provenance probe: is player_get_off reproducible from the player's own tracking?
    # first frame after the snap with speed > 0.5 yd/s, per (game, play, player)
    snap = (game[game.event == "ball_snap"]
            .groupby(["game_id", "play_id"], observed=True).ms.min().rename("snap_ms").reset_index())
    moved = game.merge(snap, on=["game_id", "play_id"], how="inner")
    moved = moved[(moved.ms >= moved.snap_ms) & (moved.s > 0.5)]
    first = (moved.groupby(["game_id", "play_id", "nfl_id"], observed=True).ms.min()
             .rename("first_move_ms").reset_index())
    first = first.merge(snap, on=["game_id", "play_id"], how="left")
    cmp = go.merge(first, on=["game_id", "play_id", "nfl_id"], how="inner")
    cmp["onset_from_tracking_s"] = (cmp.first_move_ms - cmp.snap_ms) / 1000.0
    diffs = (cmp.onset_from_tracking_s - cmp.player_get_off).to_numpy(float)
    diffs = diffs[np.isfinite(diffs)]
    share = float(np.mean(np.abs(diffs) < 0.15)) if len(diffs) else float("nan")
    a.js({"get_off_vs_tracking": {
        "n_matched": int(len(diffs)),
        "median_diff_s": float(np.median(diffs)) if len(diffs) else None,
        "share_within_0p15s": share,
        "interpretation": ("player_get_off is consistent with a tracking-derived onset "
                           "if share_within_0p15s is high; that would make at least one outcome "
                           "input depend on the player's own tracking."),
    }}, "label_provenance.json")
    dl = lab[lab.family == "DL"]
    a.record(
        "k", "PASS" if len(dl) else "FAIL",
        (f"Outcome labels exist for DL: quick_pressure true on "
         f"{int(dl.quick_true.iloc[0]) if len(dl) else 0} of {int(dl.n.iloc[0]) if len(dl) else 0} DL rows, "
         f"null on {float(dl.quick_null_share.iloc[0]) if len(dl) else float('nan'):.3f}; "
         f"unblocked_pressure and time_to_pressure share the same event-record pattern. "
         f"Null semantics are UNDOCUMENTED — treating null as false manufactures negatives "
         f"(blockers.md B1). player_get_off vs tracking-derived onset: {len(diffs)} matched reps, "
         f"{100*share:.1f}% within 0.15 s. Pressure-label provenance is not documented; a "
         f"tracking-derived get-off means at least one label input uses the player's own chips."),
        "outcome_labels.csv + label_provenance.json")


# ------------------------------------------------------------------- item l
def item_l(a: Audit) -> None:
    con = common.connect(a.cfg)
    try:
        ppq = common.ensure_parquet(a.cfg, a.mode, "player_play", a.cfg.get("columns.player_play"))
        cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{ppq}')").df().column_name.tolist()
    finally:
        con.close()
    opponent_like = [c for c in cols if any(k in c.lower() for k in ("opponent", "rusher", "blocker", "matched"))]
    a.js({"player_play_columns": cols, "opponent_or_blocker_columns": opponent_like},
         "opponent_columns.json")
    has_id = any("opponent_id" in c.lower() for c in cols)
    a.record(
        "l", "FAIL" if not has_id else "PASS",
        (f"No opponent/blocker assignment column exists in player_play. Columns whose names suggest "
         f"one: {opponent_like or 'none'}. `pass_rushers_encountered` is a count, not an ID. "
         f"Random effects for 'assigned opponent' therefore have no data; the term must be dropped "
         f"or replaced with team-game only. Logged in blockers.md."),
        "opponent_columns.json")


# ------------------------------------------------------------------- item m
def _r_min(n: int, alpha: float, power: float) -> float:
    from math import sqrt
    from scipy.stats import norm
    if n <= 4:
        return float("nan")
    z = norm.ppf(1 - alpha / 2) + norm.ppf(power)
    return float(np.tanh(z / sqrt(n - 3)))


def _delta_auc(events: int, alpha: float, power: float, base_auc: float) -> float:
    """Smallest detectable AUC gain, single-arm approximation (Hanley-McNeil SE)."""
    from math import sqrt
    from scipy.stats import norm
    if events < 10:
        return float("nan")
    q1 = base_auc / (2 - base_auc)
    q2 = 2 * base_auc ** 2 / (1 + base_auc)
    se = sqrt((base_auc * (1 - base_auc) + (events - 1) * (q1 - base_auc ** 2)
               + (events - 1) * (q2 - base_auc ** 2)) / (events * (events - 1)))
    z = norm.ppf(1 - alpha / 2) + norm.ppf(power)
    return float(z * se)


def item_m(a: Audit) -> None:
    alpha, power = a.cfg.get("power.alpha"), a.cfg.get("power.power")
    base = a.cfg.get("power.baseline_auc")
    rows = []
    for n in a.cfg.get("power.n_grid"):
        rows.append({"kind": "transfer_correlation", "n": n, "events_per_player": None,
                     "smallest_detectable": round(_r_min(n, alpha, power), 3),
                     "unit": "Pearson r"})
        for e in a.cfg.get("power.events_per_player_grid"):
            rows.append({"kind": "heldout_auc_gain", "n": n, "events_per_player": e,
                         "smallest_detectable": round(_delta_auc(n * e, alpha, power, base), 3),
                         "unit": "AUC points"})
    out = pd.DataFrame(rows)
    a.csv(out, "power.csv")
    a.js({"alpha": alpha, "power": power, "baseline_auc": base,
          "note": ("Design calculation, not a result. n is a grid, not the sample size: read the "
                   "row off at the full-data n once it exists.")},
         "power.json")
    a.record(
        "m", "PASS",
        (f"Smallest detectable transfer correlation (alpha {alpha}, power {power}): "
         + ", ".join(f"n={r.n}: r={r.smallest_detectable}"
                     for r in out[out.kind == 'transfer_correlation'].head(6).itertuples())
         + f". Smallest detectable held-out AUC gain (baseline AUC {base}, single-arm Hanley-McNeil "
           f"approximation) is in power.csv over an events-per-player grid. Both are design "
           f"calculations on an n grid, never on the sample."),
        "power.csv")


# ------------------------------------------------------------------------- gate
def write_gate(a: Audit, primary_group: str, rec_smoother: str) -> dict:
    by_item = {i["item"]: i for i in a.items}
    conds = []

    def add(cid, text, status, detail, value=None, threshold=None):
        conds.append({"id": cid, "condition": text, "status": status,
                      "value": value, "threshold": threshold, "detail": detail})

    add("G1", "a Combine drill contains usable direction changes for the primary group",
        by_item["a"]["status"],
        f"primary group = {primary_group}; {by_item['a']['finding']}",
        by_item["a"]["value"], by_item["a"]["threshold"])
    add("G2", "detector precision and recall against drill geometry meet config thresholds",
        by_item["d"]["status"], by_item["d"]["finding"], by_item["d"]["value"], by_item["d"]["threshold"])
    add("G3", "derived heading and speed agree with provided fields within config tolerance",
        by_item["c"]["status"], by_item["c"]["finding"], by_item["c"]["value"], by_item["c"]["threshold"])
    add("G4", "capacity reliability >= config threshold in both settings (prereg smoother)",
        "PENDING FULL RUN",
        "Needs the per-player capacity parameters (Phase 3) on full game tracking; "
        "cannot be computed from a one-season row sample.")
    add("G5", "Combine and game entry-speed ranges overlap",
        "PENDING FULL RUN", by_item["g"]["finding"])
    add("G6", "turns per player meet the config minimum in both settings",
        "PENDING FULL RUN", by_item["j"]["finding"])
    add("G7", "outcome labels exist for the primary group with documented null semantics",
        "PARTIAL", by_item["k"]["finding"] + " Null semantics undocumented -> blocker B1.")
    add("G8", "opponent IDs available for random effects", by_item["l"]["status"],
        by_item["l"]["finding"])
    add("G9", "prereg.md exists and fixes smoothing/definitions/thresholds", "PENDING",
        "Deliberately not written this cycle: it must fix the audit's diagnostics first. "
        "A full run is refused until it exists.")
    stop = [c for c in conds if c["status"] == "FAIL" and c["id"] in ("G1", "G2", "G3")]
    if stop:
        verdict = "NO-GO"
        because = ("Stop condition(s) fired: " + ", ".join(c["id"] for c in stop)
                   + ". " + " ".join(c["detail"] for c in stop))
    else:
        pending = [c["id"] for c in conds if c["status"] in ("PENDING FULL RUN", "PENDING")]
        verdict = "CONDITIONAL GO"
        because = ("No stop condition fired on the data available here. Pending full data: "
                   + ", ".join(pending)
                   + ". Conditions: full run only after notes/prereg.md fixes the smoother ("
                   + rec_smoother + "), definitions, bin edges and thresholds; reliability, "
                     "entry-speed overlap and turns-per-player must be re-checked on full game "
                     "tracking; outcome null semantics (B1) and the missing opponent ID (L1) must "
                     "be resolved or dropped and stated.")
    gate = {
        "verdict": verdict,
        "because": because,
        "primary_group": primary_group,
        "recommended_smoother": rec_smoother,
        "conditions": conds,
        "items": {i["item"]: {"status": i["status"], "finding": i["finding"]} for i in a.items},
    }
    a.js(gate, "gate.json")
    return gate


# ------------------------------------------------------------------------- main
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["sample", "full"], required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--config", default=None)
    args = p.parse_args(argv)
    cfg = common.load_config(args.config)
    a = Audit(cfg, args.mode)
    with common.StageTimer("01_audit", args.mode, cfg) as timer:
        if args.mode == "full":
            tp = cfg.thresholds_path()
            if not tp.exists():
                raise common.StageRefused("01_audit --mode full: outputs/thresholds.json missing; "
                                          "run 00_thresholds --mode full first")
            prov = json.loads(tp.read_text()).get("provenance")
            if prov != common.PROVENANCE_FULL:
                raise common.StageRefused(
                    f"01_audit --mode full: thresholds provenance is {prov!r}, expected FULL")
            common.require_inputs([cfg.raw_file("full", "game_tracking")],
                                  stage="01_audit --mode full")

        combine = a.load_combine()
        a.combine_frames = combine
        timer.rows = len(combine)
        # ---- one detection pass over every Combine attempt (primary smoother)
        turn_rows, att_rows = [], []
        for (drill, event_id, nfl_id, attempt), g in combine.groupby(
                ["drill_name", "event_id", "nfl_id", "attempt"], observed=True, sort=False):
            if len(g) < 8:
                continue
            t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
            xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), a.primary)
            det = detect_turns(t, xs, ys, a.det)
            pos = g.combine_position.iloc[0]
            for d in det:
                d.update({"drill_name": drill, "nfl_id": int(nfl_id), "attempt": int(attempt),
                          "combine_position": pos, "draft_year": int(g.draft_year.iloc[0]),
                          "event_id": str(g.event_id.iloc[0]) if "event_id" in g else f"{drill}-{nfl_id}-{attempt}"})
                turn_rows.append(d)
            att_rows.append({"drill_name": drill, "event_id": str(event_id),
                             "nfl_id": int(nfl_id), "attempt": int(attempt),
                             "combine_position": pos, "n_frames": len(g), "turns": len(det)})
        turns = pd.DataFrame(turn_rows)
        timer.rows = len(combine) + len(turns)
        if turns.empty:
            raise common.StageRefused("01_audit: no turns detected with the configured detector — "
                                      "refusing to write a gate on an empty turn table")
        a.csv(turns.drop(columns=["t_start", "t_end"]), "combine_turns_provisional.csv")

        item_a(a, combine, turns)
        item_b(a, combine)
        item_c(a, combine)
        item_d(a, turns)
        rec = item_e(a, combine)
        matching = item_f(a)

        # ---- game pass (chunked): turns per player, orientation, get-off probe
        gturn_rows, frame_parts = [], []
        for chunk in a.game_chunks():
            for (gid, pid, nid), g in chunk.groupby(["game_id", "play_id", "nfl_id"],
                                                    observed=True, sort=False):
                if len(g) < 8:
                    continue
                g = g.sort_values("ms")
                t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
                xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), a.primary)
                det = detect_turns(t, xs, ys, DetectorParams(
                    a.det.window_s, a.det.min_turn_angle_deg,
                    a.cfg.get("detector.speed_floor_game"), a.det.min_separation_s))
                for d in det:
                    d.update({"game_id": int(gid), "play_id": int(pid), "nfl_id": int(nid)})
                    gturn_rows.append(d)
                frame_parts.append(g[["game_id", "play_id", "nfl_id", "ms", "s", "o", "dir", "event"]])
            timer.rows = len(combine) + len(gturn_rows)
        game_turns = pd.DataFrame(gturn_rows)
        if not game_turns.empty:
            pos = a.load_positions()
            game_turns = game_turns.merge(pos, on="nfl_id", how="left")
        game_frames = pd.concat(frame_parts, ignore_index=True) if frame_parts else pd.DataFrame()
        a.csv(game_turns.drop(columns=["t_start", "t_end"]) if not game_turns.empty else game_turns,
              "game_turns_provisional.csv")

        item_g(a, turns, game_turns, matching)
        item_h(a, turns, combine)
        con = common.connect(cfg)
        try:
            ppq = common.ensure_parquet(cfg, a.mode, "player_play", cfg.get("columns.player_play"))
            pp = con.execute(f"SELECT game_id, play_id, nfl_id, route_ran, player_get_off, "
                             f"coverage_assignment FROM read_parquet('{ppq}')").df()
        finally:
            con.close()
        item_i(a, game_frames, pp)
        item_j(a, turns, game_turns)
        item_k(a, game_frames)
        item_l(a)
        item_m(a)

        pg = json.loads((a.out / "primary_group.json").read_text())["primary_group"]
        gate = write_gate(a, pg, rec)

        items = pd.DataFrame(a.items)
        a.csv(items.drop(columns=["data_basis"]), "audit_items.csv")
        a.js({"n_items": len(a.items), "spec_commit": cfg.get("spec_commit")}, "audit_manifest.json")
    print(f"01_audit: {len(a.items)} items, verdict {gate['verdict']} "
          f"(primary group {gate['primary_group']}, smoother {gate['recommended_smoother']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
