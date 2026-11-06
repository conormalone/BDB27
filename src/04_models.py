#!/usr/bin/env python3
"""04_models — Phase 4 variance decomposition.

Reads Phase 2's per-player physical-time curves and Phase 3's per-snap table,
interpolates each player's physical time at every rep's pre-snap depth, and
fits the TASK.md Phase 4 model::

    cross_time_ij = a + b * phys_i(d_ij) + alignment + depth
                    + u_i (player) + v_g (team-game) + e_ij

The model is fitted by EM on Henderson's mixed model equations (see
``src/models.py``). The estimator, the measurement-error correction and the
fallback ladder are named in every output.

No pressure field is read, no blocker term exists, and stance is not a term
(no stance label; B8). The 0.86 yd plane offset and `player_get_off` never enter.

In sample mode every number written here is ``UNVALIDATED SAMPLE OUTPUT``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import models as M  # noqa: E402

STAGE = "04_models"

SNAP_COLUMNS = [
    "nfl_id", "game_id", "season", "team_abbr", "alignment_class",
    "depth_yd", "cross_time_s", "onset_latency_s", "onset_to_cross_s",
    "analysis_ready",
]

PHYS_COLUMNS = [
    "nfl_id", "distance_yd", "posterior_mean_s", "posterior_sd_s",
    "n_attempts_used", "pooled",
]


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_physical_curves(path: Path) -> dict[int, dict]:
    """Player -> sorted depth/mean/se arrays, with a (0, 0, 0) anchor."""
    df = pd.read_csv(path, usecols=PHYS_COLUMNS)
    df = df[df["pooled"].astype(str).str.lower().isin(("true", "1"))]
    curves: dict[int, dict] = {}
    for pid, sub in df.groupby("nfl_id", sort=True):
        sub = sub.sort_values("distance_yd")
        d = sub["distance_yd"].to_numpy(dtype=float)
        m = sub["posterior_mean_s"].to_numpy(dtype=float)
        s = sub["posterior_sd_s"].to_numpy(dtype=float)
        keep = np.isfinite(d) & np.isfinite(m) & np.isfinite(s)
        d, m, s = d[keep], m[keep], s[keep]
        if len(d) == 0:
            continue
        # The curve is measured from motion onset; zero distance costs zero
        # time by definition. The anchor lets depths in (0, first grid point)
        # interpolate instead of extrapolating.
        if d[0] > 0:
            d = np.r_[0.0, d]
            m = np.r_[0.0, m]
            s = np.r_[0.0, s]
        # Duplicate distances are averaged deterministically.
        uniq, inv = np.unique(d, return_inverse=True)
        if len(uniq) != len(d):
            m = np.bincount(inv, weights=m) / np.bincount(inv)
            s = np.bincount(inv, weights=s) / np.bincount(inv)
            d = uniq
        curves[int(pid)] = {
            "d": d, "m": m, "s": s,
            "n_attempts": int(sub["n_attempts_used"].iloc[0])
            if len(sub) and pd.notna(sub["n_attempts_used"].iloc[0]) else 0,
            "max_depth": float(d[-1]),
        }
    return curves


def load_snaps(con, cfg, mode: str,
               extra_columns: Sequence[str] = ()) -> pd.DataFrame:
    """Load the analysis-ready Phase 3 snap table.

    ``extra_columns`` appends columns to the projection. Phase 4 passes nothing,
    so its query and its frame are unchanged. Phase 5 passes ``play_id`` because
    the label-side QC merge needs a per-rep key; that changes the query, never
    the model, which reads only the named columns.
    """
    path = C.paths(cfg, mode)["outputs"] / "phase3" / "snaps.parquet"
    if not path.exists():
        raise C.GateRefused(
            f"{STAGE}: {path} does not exist. Run 03_game_snaps first.")
    cols = ", ".join(list(SNAP_COLUMNS) + [c for c in extra_columns
                                           if c not in SNAP_COLUMNS])
    df = con.execute(
        f"SELECT {cols} FROM '{path.as_posix()}' "
        "WHERE analysis_ready AND cross_time_s IS NOT NULL "
        "AND depth_yd IS NOT NULL"
    ).df()
    df["player"] = df["nfl_id"].astype("int64")
    df["team_game"] = (df["game_id"].astype(str) + ":"
                       + df["team_abbr"].astype(str))
    df["alignment_class"] = df["alignment_class"].astype(str).str.upper()
    return df


def attach_physical(df: pd.DataFrame, curves: dict[int, dict]) -> tuple[pd.DataFrame, dict]:
    """Interpolate each player's physical curve at each rep's depth."""
    pieces = []
    status = {"OK": 0, "NO_CURVE": 0, "NONPOSITIVE_DEPTH": 0,
              "BEYOND_CURVE": 0}
    for pid, sub in df.groupby("player", sort=True):
        sub = sub.copy()
        curve = curves.get(int(pid))
        if curve is None:
            sub["phys_s"] = np.nan
            sub["phys_se_s"] = np.nan
            sub["phys_status"] = "NO_CURVE"
            sub["n_attempts_used"] = 0
            status["NO_CURVE"] += len(sub)
            pieces.append(sub)
            continue
        depth = sub["depth_yd"].to_numpy(dtype=float)
        phys = np.full(len(sub), np.nan)
        se = np.full(len(sub), np.nan)
        st = np.empty(len(sub), dtype=object)
        for j, d in enumerate(depth):
            if not np.isfinite(d):
                st[j] = "NONPOSITIVE_DEPTH"
                continue
            if d < 0:
                st[j] = "NONPOSITIVE_DEPTH"
                continue
            if d > curve["max_depth"]:
                st[j] = "BEYOND_CURVE"
                phys[j] = float(curve["m"][-1])
                se[j] = float(curve["s"][-1])
                continue
            st[j] = "OK"
            phys[j] = float(np.interp(d, curve["d"], curve["m"]))
            se[j] = float(np.interp(d, curve["d"], curve["s"]))
        sub["phys_s"] = phys
        sub["phys_se_s"] = se
        sub["phys_status"] = st
        sub["n_attempts_used"] = int(curve.get("n_attempts", 0))
        for k, v in pd.Series(st).value_counts().items():
            status[str(k)] = status.get(str(k), 0) + int(v)
        pieces.append(sub)
    out = pd.concat(pieces, ignore_index=True) if pieces else df.iloc[0:0].copy()
    out = out[out["phys_status"] == "OK"].copy()
    out["y"] = out["cross_time_s"].astype(float)
    out = out[np.isfinite(out["y"]) & np.isfinite(out["phys_s"])
              & np.isfinite(out["phys_se_s"])].copy()
    return out, status


# --------------------------------------------------------------------------- #
# Output rows
# --------------------------------------------------------------------------- #
def _ci(boot: dict, key: str) -> tuple[float, float]:
    ci = boot.get(f"{key}_ci95") if isinstance(boot, dict) else None
    if not ci or len(ci) != 2:
        return float("nan"), float("nan")
    return float(ci[0]), float(ci[1])


def variance_rows(result: dict) -> list[dict]:
    rows = []
    for label in ("pooled", "edge", "interior"):
        res = result.get(label) or {}
        if res.get("status") != "OK":
            rows.append({"label": label, "rung": res.get("rung_used", ""),
                         "status": res.get("status", "MISSING")})
            continue
        boot = res.get("bootstrap") or {}
        for rung, src in (("0_regression_calibration", res),
                          ("1_no_me_correction", res.get("naive_rung1") or {})):
            if not src or src.get("status") not in (None, "OK"):
                continue
            if rung.startswith("1_"):
                b = src.get("b")
                su = src.get("sigma_u2")
                vp = src.get("between_phys_var")
                shu = src.get("share_u")
                shp = src.get("share_phys")
                wi = src.get("within_player_remainder_total")
                if b is None:
                    continue
            else:
                b = res.get("b")
                su = res.get("sigma_u2")
                vp = res.get("between_phys_var")
                shu = res.get("share_u")
                shp = res.get("share_phys")
                wi = res.get("within_player_remainder_total")
            rows.append({
                "label": label,
                "rung": rung,
                "rung_used": res.get("rung_used"),
                "status": src.get("status", "OK"),
                "n_reps": res.get("n_reps"),
                "n_players": res.get("n_players"),
                "n_team_games": res.get("n_team_games"),
                "b": b,
                "b_ci_low": _ci(boot, "b")[0] if rung.startswith("0_") else float("nan"),
                "b_ci_high": _ci(boot, "b")[1] if rung.startswith("0_") else float("nan"),
                "sigma_u2": su,
                "sigma_v2": res.get("sigma_v2"),
                "sigma_e2": res.get("sigma_e2"),
                "between_phys_var": vp,
                "share_u": shu,
                "share_u_ci_low": _ci(boot, "share_u")[0] if rung.startswith("0_") else float("nan"),
                "share_u_ci_high": _ci(boot, "share_u")[1] if rung.startswith("0_") else float("nan"),
                "share_phys": shp,
                "share_phys_ci_low": _ci(boot, "share_phys")[0] if rung.startswith("0_") else float("nan"),
                "share_phys_ci_high": _ci(boot, "share_phys")[1] if rung.startswith("0_") else float("nan"),
                "within_player_remainder_total": wi,
                "jump_sd_ms": res.get("jump_sd_ms"),
                "converged": res.get("converged"),
                "iters": res.get("iters"),
            })
    return rows


def player_rows(result: dict) -> list[dict]:
    res = result.get("pooled") or {}
    pt = res.get("_player_table")
    if pt is None or len(pt) == 0:
        return []
    rows = []
    for _, r in pt.iterrows():
        rows.append({
            "player_id": int(r["player"]),
            "n_reps": int(r["n_reps"]),
            "n_games": int(r["n_games"]),
            "phys_dev_mean_s": float(r["phys_dev"]),
            "phys_shared_se_s": float(r["phys_se"]),
            "u_s": float(r["u_s"]),
            "u_se_s": float(r["u_se_s"]),
            "n_attempts_used": int(r.get("n_attempts_used", 0))
            if "n_attempts_used" in r else 0,
        })
    return rows


def _strip(result: dict) -> dict:
    """Drop the large private frames before JSON serialisation."""
    out = {}
    for label in ("pooled", "edge", "interior"):
        res = result.get(label)
        if not isinstance(res, dict):
            out[label] = res
            continue
        out[label] = {k: v for k, v in res.items()
                      if k not in ("_player_table", "_data", "resid", "resid_u")}
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    cfg, mode, force = C.stage_bootstrap(argv, STAGE)
    p = C.paths(cfg, mode)
    out_dir = p["phase4"]
    out_dir.mkdir(parents=True, exist_ok=True)
    mcfg = C.section(cfg, "models")

    if C.is_done(cfg, STAGE, mode) and not force:
        print(f"{STAGE}: already done (checkpoint); use --force to re-run")
        return 0

    phys_path = p["phase2"] / "physical_time_by_player.csv"
    if not phys_path.exists():
        raise C.GateRefused(
            f"{STAGE}: {phys_path} does not exist. Run 02_combine_physical_time "
            "first.")
    snaps_path = p["outputs"] / "phase3" / "snaps.parquet"
    if not snaps_path.exists():
        raise C.GateRefused(
            f"{STAGE}: {snaps_path} does not exist. Run 03_game_snaps first.")

    summary: dict = {}
    with C.timed_stage(cfg, STAGE, mode,
                       note="Phase 4 variance decomposition") as st:
        con = C.duckdb_connect(cfg)
        try:
            curves = load_physical_curves(phys_path)
            snaps = load_snaps(con, cfg, mode)
        finally:
            con.close()
        df, phys_status = attach_physical(snaps, curves)
        st.note(f"reps with a physical curve: {len(df)}; "
                f"players: {int(df['player'].nunique())}")
        st.note(f"phys interpolation: {phys_status}")

        if df.empty or df["player"].nunique() < int(mcfg["min_players"]):
            summary = {
                "status": "INSUFFICIENT_DATA",
                "stage": STAGE,
                "mode": mode,
                "rows_with_curve": int(len(df)),
                "players_with_curve": int(df["player"].nunique()) if len(df) else 0,
                "phys_status": phys_status,
                "note": "Phase 4 needs at least min_players players with a "
                        "tracked crossing and a Combine curve.",
            }
            C.write_json(out_dir / "decomposition.json", summary, mode=mode, cfg=cfg)
            if mode == "full":
                raise C.GateRefused(
                    f"{STAGE}: only {summary['players_with_curve']} players with "
                    f"a physical curve; need at least {mcfg['min_players']}.")
            C.mark_done(cfg, STAGE, mode, {"status": "INSUFFICIENT_DATA"})
            return 0

        common = dict(
            cfg=cfg,
            min_reps=int(mcfg["min_reps_per_player"]),
            bootstrap_resamples=int(mcfg["bootstrap_resamples"]),
            bootstrap_seed=int(mcfg["bootstrap_seed"]),
            population_degree=int(mcfg["population_curve_degree"]),
            max_iter=int(mcfg["max_iter"]),
            tol=float(mcfg["tol"]),
            accept_delta=float(mcfg.get("accept_delta", 1e-4)),
            min_players=int(mcfg["min_players"]),
            min_bootstrap_ok_share=float(mcfg.get("min_bootstrap_ok_share", 0.5)),
        )
        pooled = M.fit_set(df, label="pooled", **common)
        edge = M.fit_set(df[df["alignment_class"] == "EDGE"], label="edge", **common)
        interior = M.fit_set(df[df["alignment_class"] == "INTERIOR"],
                             label="interior", **common)
        result = {"pooled": pooled, "edge": edge, "interior": interior}

        # Resolution statement: FULL evidence from the frozen prereg's power
        # artefact. It is a statement about what the design can resolve, not a
        # pass/fail test.
        power_path = C.abs_path(C.section(cfg, "paths")["prereg_outputs"]) / "power.json"
        resolution = {"source": str(power_path), "status": "MISSING"}
        if power_path.exists():
            import json
            pw = json.loads(power_path.read_text(encoding="utf-8"))
            table = pw.get("table", [])
            row20 = next((r for r in table
                          if int(r.get("min_reps_per_player", -1)) == 20), None)
            resolution = {
                "source": str(power_path),
                "provenance": pw.get("provenance"),
                "smallest_detectable_jump_sd_ms_at_20_reps": (
                    row20.get("smallest_detectable_jump_sd_ms_effective")
                    if row20 else None),
                "smallest_detectable_jump_sd_ms_best_case": pw.get(
                    "answers", {}).get("smallest_detectable_jump_sd_ms_best_case"),
                "statement": (
                    "Reported as a resolution statement, not a threshold: the "
                    "design resolves a jump SD of roughly 52-54 ms at the "
                    "candidate cuts. No confirmatory smallest-effect-of-interest "
                    "test is applied (Conor 2026-11-05 22:16; frozen prereg)."),
            }

        decomposition = _strip(result)
        decomposition.update({
            "stage": STAGE,
            "mode": mode,
            "model_form": (
                "y_ij = a + b*phys_ij + alignment + depth + u_i + v_g + e_ij; "
                "u_i ~ N(0,sigma_u^2), v_g ~ N(0,sigma_v^2), "
                "e_ij ~ N(0,sigma_e^2). y is the tracked LOS crossing "
                "(cross_time_s). Stance is not a term (no label, B8); no "
                "blocker term; no pressure field."),
            "estimator": {
                "name": "EM on Henderson mixed model equations (two crossed "
                        "random intercepts)",
                "measurement_error": "regression calibration of the shared "
                                     "player-level phys error",
                "intervals": "player-cluster basic (pivotal) bootstrap intervals",
                "bayesian_fit": "deferred: PyMC/Stan not available on this host",
            },
            "measurement_error": {
                "shared_error_variance": "s2_i = (mean_j se_ij)^2; subtracted "
                                         "from sigma_u^2 and from the "
                                         "between-player phys variance",
                "note": "The uncorrected fit is reported as the ladder's rung 1.",
            },
            "min_reps_per_player": int(mcfg["min_reps_per_player"]),
            "phys_interpolation_status": phys_status,
            "resolution": resolution,
            "no_pressure_outcome": True,
            "no_blocker_term": True,
            "stance_term": "absent (no stance label; absorbed in u and e)",
        })
        C.write_json(out_dir / "decomposition.json", decomposition, mode=mode, cfg=cfg)

        C.write_csv(out_dir / "variance_components.csv", variance_rows(result),
                    ["label", "rung", "rung_used", "status", "n_reps", "n_players",
                     "n_team_games", "b", "b_ci_low", "b_ci_high",
                     "sigma_u2", "sigma_v2", "sigma_e2", "between_phys_var",
                     "share_u", "share_u_ci_low", "share_u_ci_high",
                     "share_phys", "share_phys_ci_low", "share_phys_ci_high",
                     "within_player_remainder_total", "jump_sd_ms",
                     "converged", "iters"],
                    mode=mode, cfg=cfg)

        prows = player_rows(result)
        C.write_csv(out_dir / "player_effects.csv", prows,
                    ["player_id", "n_reps", "n_games", "phys_dev_mean_s",
                     "phys_shared_se_s", "u_s", "u_se_s", "n_attempts_used"],
                    mode=mode, cfg=cfg)

        rel = {
            "pooled": {"reliability": pooled.get("reliability"),
                       "agreement": pooled.get("agreement"),
                       "year_to_year": pooled.get("year_to_year"),
                       "ladder": pooled.get("ladder"),
                       "rung_used": pooled.get("rung_used")},
            "edge": {"reliability": edge.get("reliability"),
                     "agreement": edge.get("agreement"),
                     "rung_used": edge.get("rung_used")},
            "interior": {"reliability": interior.get("reliability"),
                         "agreement": interior.get("agreement"),
                         "rung_used": interior.get("rung_used")},
            "note": "Reliability is of u; the split-half is odd/even games. "
                    "Agreement uses mean snap-to-onset latency and mean "
                    "onset-to-cross time. Disagreement is a finding.",
        }
        C.write_json(out_dir / "reliability_agreement.json", rel, mode=mode, cfg=cfg)

        model_form = {
            "stage": STAGE,
            "mode": mode,
            "model_form": decomposition["model_form"],
            "estimator": decomposition["estimator"],
            "convergence": {
                label: {
                    "status": (result[label] or {}).get("status"),
                    "converged": (result[label] or {}).get("converged"),
                    "iters": (result[label] or {}).get("iters"),
                    "delta": (result[label] or {}).get("delta"),
                }
                for label in ("pooled", "edge", "interior")
            },
            "sample": {
                "rows_with_curve": int(len(df)),
                "players_with_curve": int(df["player"].nunique()),
                "min_reps_per_player": int(mcfg["min_reps_per_player"]),
            },
            "constraints": {
                "pressure_fields_read": [],
                "blocker_term": False,
                "stance_term": False,
                "plane_offset_hardcoded": False,
                "player_get_off_used": False,
            },
        }
        C.write_json(out_dir / "model_form.json", model_form, mode=mode, cfg=cfg)

        summary = {
            "status": "OK",
            "pooled_status": pooled.get("status"),
            "edge_status": edge.get("status"),
            "interior_status": interior.get("status"),
            "pooled_rung": pooled.get("rung_used"),
            "rows_with_curve": int(len(df)),
            "players_with_curve": int(df["player"].nunique()),
            "phys_interpolation_status": phys_status,
        }
        st.note(f"pooled {pooled.get('status')} rung {pooled.get('rung_used')}; "
                f"edge {edge.get('status')}; interior {interior.get('status')}")
        C.mark_done(cfg, STAGE, mode, summary)
        print(f"{STAGE}: done ({mode}); pooled={pooled.get('status')} "
              f"rung={pooled.get('rung_used')} rows={len(df)} "
              f"players={df['player'].nunique()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
