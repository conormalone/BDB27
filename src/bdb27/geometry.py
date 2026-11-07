"""Drill geometry: cone estimation and precision/recall scoring.

Geometry is a *scoring* input, never a detector input. The cone COUNT is a drill
fact from ``config.yaml``; the cone POSITIONS are estimated from the aggregate
density of detected turns on one half of the attempts and evaluated on the other
half, so precision is not circular (TASK.md Phase 1d).

The turning constraint differs by drill (D125, still OPEN with Conor):

* ``point`` — the athlete runs around a cone, so the target is a 2-D distance;
* ``line``  — the athlete must touch a line, so only one coordinate is constrained.

Both models are scored and reported; ``geometry.drill_model`` decides which one is
the gate. The alternative is never removed.
"""

from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np
import pandas as pd


def estimate_cones(points: np.ndarray, k: int, res: float, min_sep: float,
                   radius: float = 0.0) -> np.ndarray:
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


def split_of(event_id, modulus: int = 2) -> int:
    """Stable half-assignment by hash: no random state, no row-order dependence."""
    return int(hashlib.md5(str(event_id).encode()).hexdigest(), 16) % int(modulus)


def _distance(turns: pd.DataFrame, cones: np.ndarray, model: str, axis: str) -> np.ndarray:
    pts = turns[["x_centre", "y_centre"]].to_numpy(float)
    if model == "line":
        ax = 1 if str(axis) == "y" else 0
        return np.abs(pts[:, ax][:, None] - cones[None, :, ax]).min(1)
    return np.linalg.norm(pts[:, None, :] - cones[None], axis=2).min(1)


def _cone_sequence_angle_errors(turns: pd.DataFrame, cones: np.ndarray) -> list[float]:
    """Detected angle vs the geometric angle of the cone triple it walks through.

    Consecutive detections at the same cone are collapsed first: one physical
    corner sampled over several frames is one visit, not several turns.
    """
    pts = turns[["x_centre", "y_centre"]].to_numpy(float)
    cone_idx = np.linalg.norm(pts[:, None, :] - cones[None], axis=2).argmin(1)
    tmp = turns.assign(cone=cone_idx)
    errs: list[float] = []
    for _, gg in tmp.groupby(["event_id", "attempt"], observed=True):
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
            errs.append(abs(geo - ang1))
    return errs


def score_geometry(turns: pd.DataFrame, cfg) -> pd.DataFrame:
    """Precision/recall of detected turns against estimated drill geometry.

    Returns one row per drill with BOTH target models, the configured gate model
    and the median absolute angle error. Empty input returns an empty frame.
    """
    drills = list(cfg.get("geometry.cone_count"))
    cols = ["drill_name", "turn_target_model", "cones_expected", "cones_found",
            "min_cone_separation_yd", "turns_estimate", "turns_eval",
            "matched_point", "matched_line",
            "precision_point", "recall_point", "precision_line", "recall_line",
            "precision", "recall", "angle_median_abs_error_deg", "angle_n",
            "expected_turns_per_attempt", "frac_detections_off_cone"]
    if turns is None or turns.empty or "drill_name" not in turns:
        return pd.DataFrame(columns=cols)
    df = turns[turns.drill_name.isin(drills)].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    rad = float(cfg.get("validation.cone_radius_yd"))
    modulus = int(cfg.get("geometry.hash_modulus"))
    rows = []
    for drill, g in df.groupby("drill_name", observed=True):
        k = int(cfg.get("geometry.cone_count")[drill])
        split = g.event_id.map(lambda e: split_of(e, modulus))
        est, ev = g[split == 0], g[split == 1]
        cones = estimate_cones(est[["x_centre", "y_centre"]].to_numpy(float), k,
                               float(cfg.get("geometry.grid_resolution_yd")),
                               float(cfg.get("geometry.min_separation_yd")), rad)
        gate_model = str(cfg.get("geometry.drill_model").get(drill, "point"))
        axis = cfg.get("geometry.line_axis").get(drill, "x")
        if len(cones) == 0 or ev.empty:
            rows.append({"drill_name": drill, "turn_target_model": gate_model,
                         "cones_expected": k, "cones_found": len(cones),
                         "turns_estimate": int(len(est)), "turns_eval": int(len(ev)),
                         "matched_point": 0, "matched_line": 0,
                         "precision_point": 0.0, "recall_point": 0.0,
                         "precision_line": 0.0, "recall_line": 0.0,
                         "precision": 0.0, "recall": 0.0})
            continue
        out = {"drill_name": drill, "turn_target_model": gate_model,
               "cones_expected": k, "cones_found": len(cones),
               "turns_estimate": int(len(est)), "turns_eval": int(len(ev))}
        pair = [np.linalg.norm(cones[i] - cones[j]) for i in range(len(cones))
                for j in range(i + 1, len(cones))]
        out["min_cone_separation_yd"] = round(float(min(pair)), 2) if pair else np.nan
        for model in ("point", "line"):
            dist = _distance(ev, cones, model, axis)
            near = dist <= rad
            per_att = ev.assign(near=near).groupby(["event_id", "attempt"], observed=True).near.sum()
            expected = int(per_att.mode().iloc[0]) if len(per_att) else 0
            prec = float(near.mean())
            rec = float(np.minimum(per_att, expected).sum() / (expected * len(per_att))) if expected else 0.0
            out[f"precision_{model}"] = round(prec, 3)
            out[f"recall_{model}"] = round(rec, 3)
            out[f"matched_{model}"] = int(near.sum())
            if model == gate_model:
                out["precision"] = round(prec, 3)
                out["recall"] = round(rec, 3)
                out["expected_turns_per_attempt"] = expected
                out["frac_detections_off_cone"] = round(float(1 - near.mean()), 3)
        ref = cfg.get("geometry.angle_reference_deg").get(drill)
        if ref is not None:
            errs = [abs(abs(x) - float(ref)) for x in ev.turn_angle.to_numpy(float)]
        else:
            errs = _cone_sequence_angle_errors(ev, cones)
        out["angle_median_abs_error_deg"] = round(float(np.median(errs)), 1) if errs else np.nan
        out["angle_n"] = len(errs)
        rows.append(out)
    return pd.DataFrame(rows)[cols]
