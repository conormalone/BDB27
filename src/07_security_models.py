#!/usr/bin/env python3
"""07_security_models.py — Phase 5 B–G: the incentive-exposure security models (BDB27).

**The spine (D38 / PI override).** Analysis A (the old "does the market reward speed?" gate) is **not**
a gate and **not** part of the paper argument any more; it survives only as a short descriptive
appendix (`outputs/security/analysis_a_attenuation.csv`, written by ``06_security``). The new spine is
**incentive exposure**: a player whose draft stock is slipping entering the Combine has a stronger
reason to invest in the speed mechanism, so he should show a **smaller** Combine→game gap (higher
ceiling realisation) than a high/secure player. This is operationalised strictly as incentive
exposure — never as measured belief or intent. The writeup may say "consistent with an incentive
mechanism"; it must never say players tried harder, intended to, or gamed the Combine.

**Sort key (pre-Combine information ONLY).** ``security_features.assign_security_groups``:

* trend stratum (``has_trend``): **D40 asymmetric threshold** — ``moved_down`` iff
  ``edp_trend >= fall_threshold`` (primary **+3.0** EDP spots; ``edp_trend`` = 1-week-pre −
  2-weeks-pre, positive = worse / less secure), else ``high_secure``. ``high_secure`` is therefore
  **not falling 3+ (stable or rising)** — deliberately *not* a "low EDP" or "no movement" group.
  The declared sensitivity re-runs the same rule at **+5.0** (`panel_threshold_T5`);
* level-fallback stratum (no trend, ``has_primary``): tertiles of ``log_edp_pre`` **within position
  group**; bottom = ``high_secure``, top = ``moved_down``, middle = ``unclassified`` (excluded from
  the two-group contrast, retained in the continuous model);
* otherwise ``unclassified``.

Group assignment reads **no** post-Combine field. ``assert_pre_combine_only`` is a runtime assertion on
the input columns and the assignment path projects onto a pre-Combine whitelist, so mutating
``edp_post`` / ``edp_change`` cannot change the assignment (tested). No post-Combine column is read
anywhere in this stage: the only post-Combine quantities used are the outcomes themselves (game speed
and the derived utilisation gap).

**Sign convention.** A **NEGATIVE** ``group_coef`` means ``moved_down`` sits further below its ceiling
(a LARGER gap) — i.e. **against** the incentive hypothesis. The hypothesis predicts **POSITIVE**
(``moved_down`` has a smaller gap / higher realisation). Stated in ``SUMMARY.md`` too.

Models (all config-driven, ``config.yaml::security_models``):

* **B (primary)** — effort-level OLS on the top-k efforts, ``log(peak_speed)`` on
  ``log(shrunken Combine speed)`` + ``moved_down`` + route family + pass length + snap share +
  position + height + weight + years-since-Combine; player-clustered SEs. WR primary, TE replication,
  pooled secondary; robustness panel over the other retained Combine features; a per-player
  ``log(top-k mean speed)`` variant (HC1). Both the naive and the **ICC-attenuation-corrected**
  (Frisch–Waugh + moment-matrix) coefficients are reported — never silently replaced.
* **C** — continuous piecewise model in ``log_edp_pre``; fitted gap at the 10th/50th/90th percentile
  (at mean ceiling and within-player); overall exceedance rate FIRST (Wilson CI), then by group.
* **D** — skippers (exploratory): high/secure players missing the dash vs a size + drills prediction.
* **E** — selection: logistic appearance model + IPW refit of B (stabilized, trimmed) with a
  truncation-sensitivity grid.
* **F** — falsification: height/weight placebos, top-k vs bottom-k efforts, Combine peaking markers.
* **G** — ceiling/tail quantile regressions at the FROZEN τ ladder, player-clustered bootstrap, with
  the pre-declared instability fallbacks (null rather than a forced number).

Run modes / provenance:

* ``--mode full`` — gated (`gate.enforce(..., requires_game_side=True)`); reads the real Phase-3/4/5
  summaries; requires ``outputs/thresholds.json`` provenance ``FULL``.
* ``--mode sample`` — game-side thresholds are not ``FULL`` and the Pi sample is non-representative, so
  this writes a clearly labelled ``PENDING FULL RUN`` / ``UNVALIDATED SAMPLE OUTPUT`` placeholder and
  exits 0 (same house pattern as 03/04/05).
* ``--synthetic [--synthetic-regime planted|null|selection]`` — the identical code path on the
  committed game-side fixture; every output is stamped ``SYNTHETIC FIXTURE (not a finding)``.

Discipline (TASK.md): no full CSV into pandas (DuckDB column-selective reads + numpy); ARM/Python 3.12
compatible; every output CSV's FIRST column is ``provenance``; wall time + peak RAM via
``common.timed_stage``; checkpoint + skip unless ``--force``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import gate
import model_features as mf
import security_features as sf

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"
GROUP_POS, GROUP_NEG = "moved_down", "high_secure"   # sign convention: positive => moved_down advantage

# Every input column this stage reads (explicit; no ``SELECT *`` into pandas).
EXPOSURE_COLS = ["nfl_id", "draft_year", "position", "position_group", "edp_pre", "log_edp_pre",
                 "edp_trend", "consensus_share", "n_snapshots", "has_primary", "has_trend",
                 "participation_pattern", "n_drills"]
ENVELOPE_COLS = ["nfl_id", "combine_position", "draft_year", "n_attempts", "peak_speed",
                 "speed_at_20yd", "env_vmax", "env_vmax_se"]
SUMMARY_COLS = ["nfl_id", "n_route_snaps", "eligible", "topk_mean_speed", "ceiling_speed_yds"]
TOPK_COLS = ["nfl_id", "rank", "game_id", "play_id", "route_ran", "pass_length", "peak_speed"]
EFFORT_COLS = ["nfl_id", "game_id", "play_id", "route_ran", "pass_length", "peak_speed"]
ATTEMPT_COLS = ["nfl_id", "event_id", "peak_speed"]
COV_COLS = ["nfl_id", "position", "height", "weight", "age", "draft_position", "draft_year"]

FLOAT_FIELDS = ("edp_pre", "log_edp_pre", "edp_trend", "consensus_share", "peak_speed",
                "speed_at_20yd", "env_vmax", "env_vmax_se", "topk_mean_speed", "ceiling_speed_yds",
                "pass_length", "height", "weight", "age", "log_combine_speed")


# --------------------------------------------------------------------------- #
# emit helpers (house convention: provenance FIRST)
# --------------------------------------------------------------------------- #
def _f(x: Any) -> Any:
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return "" if not np.isfinite(v) else round(v, 4)
    if isinstance(x, (int, np.integer)):
        return int(x)
    return x


def _emit(path: Path, rows: Sequence[dict], prov: str, log) -> None:
    rows = [dict(r) for r in rows] or [{}]
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    fields = ["provenance"] + [f for f in fields if f != "provenance"]
    norm = []
    for r in rows:
        d = {"provenance": prov}
        for k, v in r.items():
            if k != "provenance":
                d[k] = _f(v)
        norm.append(d)
    common.write_csv(path, norm, fieldnames=fields)
    log.info("wrote %s (%d rows)", path.name, len(norm))


def record_blocker(cfg: dict, msg: str, log=None) -> None:
    path = common.resolve(cfg, "notes") / "blockers.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **BDB27 07_security_models BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


# --------------------------------------------------------------------------- #
# paths + DuckDB input readers (explicit columns; no full-CSV pandas)
# --------------------------------------------------------------------------- #
def stage_dir(cfg: dict, synthetic: bool, regime: str) -> Path:
    base = common.resolve(cfg, "outputs", cfg["security_models"]["out_subdir"])
    return base / "synthetic" / regime if synthetic else base


def fixture_dir(cfg: dict, regime: str) -> Path:
    return common.resolve(cfg, "fixtures", cfg["security_models"]["synthetic"]["root"]) / regime


def _src(path: Path) -> str:
    return common.pq(path) if str(path).endswith(".parquet") else f"read_csv_auto('{path}', header=true)"


def assert_input_columns(con, path: Path, required: Sequence[str]) -> list[str]:
    """Fail loudly when a required input column is absent (schema/units guard)."""
    if not Path(path).exists():
        raise FileNotFoundError(f"required input missing: {path}")
    names = [r[0] for r in con.execute(f"describe select * from {_src(Path(path))}").fetchall()]
    missing = [c for c in required if c not in names]
    if missing:
        raise AssertionError(f"{Path(path).name}: missing required column(s) {missing}; got {names}")
    return names


def _read(con, path: Path, cols: Sequence[str]) -> list[dict]:
    assert_input_columns(con, path, cols)
    sel = ", ".join('"%s"' % c for c in cols)
    rows = con.execute(f"SELECT {sel} FROM {_src(Path(path))}").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("true", "t", "1", "yes"):
        return True
    if s in ("false", "f", "0", "no"):
        return False
    return None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load_exposures(con, path: Path) -> dict[int, dict]:
    """Per-player pre-Combine exposure rows (one per board player), keyed by ``nfl_id``."""
    rows = _read(con, path, EXPOSURE_COLS)
    sf.assert_pre_combine_only(list(rows[0].keys()) if rows else EXPOSURE_COLS)
    out: dict[int, dict] = {}
    for r in rows:
        nid = int(r["nfl_id"])
        out[nid] = dict(
            nfl_id=nid, draft_year=(int(r["draft_year"]) if r["draft_year"] is not None else None),
            position=(str(r["position"]) if r["position"] is not None else None),
            position_group=(str(r["position_group"]) if r["position_group"] is not None else "OTHER"),
            edp_pre=_num(r["edp_pre"]), log_edp_pre=_num(r["log_edp_pre"]),
            edp_trend=_num(r["edp_trend"]), consensus_share=_num(r["consensus_share"]),
            n_snapshots=(int(r["n_snapshots"]) if r["n_snapshots"] is not None else None),
            has_primary=bool(_bool(r["has_primary"])), has_trend=bool(_bool(r["has_trend"])),
            participation_pattern=(str(r["participation_pattern"])
                                   if r["participation_pattern"] is not None else None),
            n_drills=(int(r["n_drills"]) if r["n_drills"] is not None else None),
        )
    return out


def load_envelope(con, path: Path) -> dict[int, dict]:
    rows = _read(con, path, ENVELOPE_COLS)
    out: dict[int, dict] = {}
    for r in rows:
        out[int(r["nfl_id"])] = dict(
            nfl_id=int(r["nfl_id"]),
            combine_position=(str(r["combine_position"]) if r["combine_position"] is not None else None),
            draft_year=(int(r["draft_year"]) if r["draft_year"] is not None else None),
            n_attempts=(int(r["n_attempts"]) if r["n_attempts"] is not None else None),
            peak_speed=_num(r["peak_speed"]), speed_at_20yd=_num(r["speed_at_20yd"]),
            env_vmax=_num(r["env_vmax"]), env_vmax_se=_num(r["env_vmax_se"]),
        )
    return out


def load_player_summary(con, path: Path) -> dict[int, dict]:
    rows = _read(con, path, SUMMARY_COLS)
    return {int(r["nfl_id"]): dict(
        nfl_id=int(r["nfl_id"]), n_route_snaps=(int(r["n_route_snaps"])
                                                if r["n_route_snaps"] is not None else 0),
        eligible=bool(_bool(r["eligible"])), topk_mean_speed=_num(r["topk_mean_speed"]),
        ceiling_speed_yds=_num(r["ceiling_speed_yds"])) for r in rows}


def load_efforts(con, path: Path) -> list[dict]:
    rows = _read(con, path, EFFORT_COLS)
    out = []
    for r in rows:
        out.append(dict(nfl_id=int(r["nfl_id"]), game_id=int(r["game_id"]),
                        play_id=int(r["play_id"]),
                        route_ran=(str(r["route_ran"]) if r["route_ran"] is not None else None),
                        pass_length=_num(r["pass_length"]), peak_speed=_num(r["peak_speed"])))
    return out


def load_topk(con, path: Path) -> list[dict]:
    rows = _read(con, path, TOPK_COLS)
    out = []
    for r in rows:
        out.append(dict(nfl_id=int(r["nfl_id"]), rank=int(r["rank"]), game_id=int(r["game_id"]),
                        play_id=int(r["play_id"]),
                        route_ran=(str(r["route_ran"]) if r["route_ran"] is not None else None),
                        pass_length=_num(r["pass_length"]), peak_speed=_num(r["peak_speed"])))
    return out


def load_attempt_markers(con, path: Path) -> dict[int, dict]:
    """Per-player Combine peaking markers: best-minus-mean attempt speed and attempt count."""
    rows = _read(con, path, ATTEMPT_COLS)
    acc: dict[int, list[float]] = {}
    for r in rows:
        v = _num(r["peak_speed"])
        if v is not None:
            acc.setdefault(int(r["nfl_id"]), []).append(v)
    out: dict[int, dict] = {}
    for nid, vals in acc.items():
        arr = np.asarray(vals, float)
        out[nid] = dict(n_attempts=int(arr.size),
                        best_minus_mean=(float(arr.max() - arr.mean()) if arr.size else None))
    return out


def load_reliability(cfg: dict, synthetic: bool, regime: str, log) -> dict[str, dict]:
    """Pre-declared reliability (ICC) of each Combine feature, with its source.

    Synthetic: the fixture's declared reliability file. Real: the shared
    ``security_features.load_feature_icc`` (selection table → audit ICC → selection.json).
    Never invented: an absent ICC leaves the corrected estimate null with a reason.
    """
    if synthetic:
        out: dict[str, dict] = {}
        p = fixture_dir(cfg, regime) / cfg["security_models"]["inputs"]["reliability"]
        if p.exists():
            for r in _read_csv_simple(p):
                icc = _num(r.get("icc"))
                if icc is not None:
                    out[str(r["feature"])] = {"icc": icc, "source": f"fixture:{p.name}"}
        return out
    return sf.load_feature_icc(cfg, log)


def _read_csv_simple(path: Path) -> list[dict]:
    import csv as _csv
    with open(path, newline="", encoding="utf-8") as fh:
        return list(_csv.DictReader(fh))


def load_covariates(con, cfg: dict, synthetic: bool, regime: str) -> dict[int, dict]:
    """Per-player covariates: fixture CSV (synthetic) or the shared Phase-4 loader (real)."""
    if synthetic:
        path = fixture_dir(cfg, regime) / cfg["security_models"]["inputs"]["covariates"]
        rows = _read(con, path, COV_COLS)
        out: dict[int, dict] = {}
        for r in rows:
            dy = _num(r["draft_year"])
            out[int(r["nfl_id"])] = dict(
                position=(str(r["position"]) if r["position"] is not None else None),
                height=_num(r["height"]), weight=_num(r["weight"]), age=_num(r["age"]),
                draft_position=(str(r["draft_position"]) if r["draft_position"] is not None else None),
                draft_year=dy)
        return out
    parq = common.resolve(cfg, "parquet")
    con2 = con
    cr_pq = parq / (Path(cfg["files"]["combine_results"]).stem + ".parquet")
    pl_pq = parq / (Path(cfg["files"]["players"]).stem + ".parquet")
    if not cr_pq.exists():
        common.to_parquet(con2, common.csv_path(cfg, "combine_results"), cr_pq)
    if not pl_pq.exists():
        common.to_parquet(con2, common.csv_path(cfg, "players"), pl_pq)
    return mf.load_covariates(con2, cr_pq, pl_pq, cfg)


# --------------------------------------------------------------------------- #
# design matrix / fits
# --------------------------------------------------------------------------- #
def design(rows: Sequence[dict], numeric: Sequence[str], categorical: Sequence[str],
           focal: Sequence[str] = ()) -> tuple[np.ndarray, list[str], list[tuple[str, str]]]:
    """``[const] + numeric + one-hot(categorical)`` with rank pruning from the end.

    Focal terms (the Combine speed and the group indicator) are protected: if a focal term would be
    dropped the design raises instead of silently changing the estimand.
    """
    n = len(rows)
    if n == 0:
        raise ValueError("empty design")
    names = ["const"]
    cols = [np.ones(n)]
    dropped: list[tuple[str, str]] = []
    for k in numeric:
        v = np.array([_num(r.get(k)) if _num(r.get(k)) is not None else np.nan for r in rows], float)
        if not np.all(np.isfinite(v)):
            dropped.append((k, "non-finite"))
            continue
        if np.allclose(v, v[0]):
            dropped.append((k, "constant"))
            continue
        names.append(k)
        cols.append(v)
    for k in categorical:
        levels = sorted({str(r.get(k)) for r in rows})
        if len(levels) <= 1:
            dropped.append((k, "constant"))
            continue
        for lv in levels[1:]:
            names.append(f"{k}={lv}")
            cols.append(np.array([1.0 if str(r.get(k)) == lv else 0.0 for r in rows], float))
    X = np.column_stack(cols)
    while X.shape[1] > 1 and np.linalg.matrix_rank(X) < X.shape[1]:
        dropped.append((names[-1], "rank-deficient"))
        names.pop()
        X = X[:, :-1]
    bad = [f for f in focal if f not in names]
    if bad:
        raise ValueError(f"focal term(s) {bad} dropped from the design ({dropped})")
    if np.linalg.matrix_rank(X) < X.shape[1]:
        raise ValueError("design not full rank after pruning")
    return X, names, dropped


def ols(y: np.ndarray, X: np.ndarray, clusters: np.ndarray | None = None,
        weights: np.ndarray | None = None):
    """OLS / WLS with player-clustered SEs (HC1 for player-level fits)."""
    import statsmodels.api as sm
    model = sm.WLS(y, X, weights=weights) if weights is not None else sm.OLS(y, X)
    if clusters is None:
        return model.fit(cov_type="HC1")
    return model.fit(cov_type="cluster", cov_kwds={"groups": clusters})


def _coef(res, names: list[str], key: str) -> dict:
    i = names.index(key)
    ci = np.asarray(res.conf_int(), float)
    return dict(coef=float(res.params[i]), se=float(res.bse[i]), p=float(res.pvalues[i]),
                ci_lo=float(ci[i, 0]), ci_hi=float(ci[i, 1]))


def _safe_coef(res, names: list[str], key: str) -> dict:
    if key not in names:
        return dict(coef=None, se=None, p=None, ci_lo=None, ci_hi=None)
    return _coef(res, names, key)


def _group_player_counts(rows: Sequence[dict], groups: Sequence[str]) -> dict[str, int]:
    """Distinct players per contrast group (the unit ``min_group_n`` is defined on)."""
    return {g: len({r["nfl_id"] for r in rows if r.get("group") == g}) for g in groups}


def _thin_group_note(rows: Sequence[dict], groups: Sequence[str], min_n: int) -> str:
    """Non-empty note when any *one* contrast group is below ``min_group_n`` (never fitted silently)."""
    counts = _group_player_counts(rows, groups)
    thin = [f"{g}={n}" for g, n in counts.items() if n < int(min_n)]
    if not thin:
        return ""
    return (f"thin group(s): {', '.join(thin)} players < min_group_n={int(min_n)} "
            f"[contrast total={sum(counts.values())}]")


def _mark_thin(rec: dict, rows: Sequence[dict], groups: Sequence[str], min_n: int) -> str:
    """Record per-group player counts + an explicit thin flag on a B row.

    ``min_group_n`` is a *per-group* floor: a 5 vs 100 split is thin even though the 105-player
    total clears it. The flag is emitted in the CSV and rendered in ``SUMMARY.md``.
    """
    counts = _group_player_counts(rows, groups)
    rec["n_players_moved_down"] = counts.get("moved_down", 0)
    rec["n_players_high_secure"] = counts.get("high_secure", 0)
    note = _thin_group_note(rows, groups, min_n)
    rec["thin"] = bool(note)
    return note


def _apply_attenuation(rec: dict, fin: Sequence[dict], xkey: str, y: np.ndarray, X: np.ndarray,
                       names: list[str], icc: dict, num_controls: Sequence[str],
                       cat_controls: Sequence[str], p: dict, cfg: dict,
                       feature: str | None = None) -> None:
    """Populate the naive + ICC-attenuation-corrected estimates on ``rec``.

    Shared by the primary/robustness rows, the one-stratum sensitivity panels and the per-player
    top-k-mean variant so every B row reports the same pair of estimates with the same control set
    (intercept + numeric controls + the categorical dummies actually used in the fit).
    """
    if not p.get("attenuation"):
        return
    lam_raw = (icc or {}).get("icc")
    if lam_raw is None:
        rec["note"] = (rec["note"] + " | " if rec["note"] else "") + \
                      (f"attenuation unavailable: no reliability ICC for "
                       f"{feature or xkey.lstrip('log_')}")
        return
    lam = float(lam_raw)
    if not (0.0 < lam <= 1.0):
        rec["note"] = (rec["note"] + " | " if rec["note"] else "") + \
                      f"attenuation unavailable: reliability_icc={lam} outside (0, 1]"
        return
    C = np.column_stack([np.ones(len(fin))] +
                        [np.array([_num(r.get(k)) for r in fin], float)
                         for k in num_controls if k in names] +
                        [X[:, j] for j, nm in enumerate(names)
                         if "=" in nm and nm.split("=", 1)[0] in cat_controls])
    corr = fwl_attenuation(
        y, np.array([_num(r[xkey]) for r in fin], float),
        np.array([_num(r["moved_down"]) for r in fin], float), C, lam,
        np.array([r["nfl_id"] for r in fin]),
        int(p["attenuation_reps"]), int(cfg["seed"]) + int(p["attenuation_seed_offset"]))
    rec["speed_coef_corrected"] = (rec["speed_coef"] / lam
                                   if rec["speed_coef"] is not None else None)
    if corr["ok"]:
        rec["group_coef_corrected"] = float(corr["beta_corr"][1])
        rec["group_se_corrected"] = corr["se_group"]
        rec["group_p_corrected"] = corr["p_group"]
    else:
        rec["note"] = (rec["note"] + " | " if rec["note"] else "") + \
                      f"attenuation unavailable: {corr['reason']}"


# --------------------------------------------------------------------------- #
# FWL + ICC attenuation (classical measurement error in the Combine feature)
# --------------------------------------------------------------------------- #
def fwl_attenuation(y: np.ndarray, x: np.ndarray, g: np.ndarray, C: np.ndarray,
                    lam: float, clusters: np.ndarray, reps: int, seed: int) -> dict:
    """Naive and ICC-attenuation-corrected 2-regressor coefficients.

    Frisch–Waugh: residualise ``y, x, g`` on ``C`` (intercept + controls). The 2-regressor moment
    matrix ``S = [[Var(x), Cov(x,g)], [Cov(x,g), Var(g)]]`` and ``s = [Cov(x,y), Cov(g,y)]`` give the
    naive ``β = S⁻¹ s``. Classical measurement error in ``x`` with reliability ``λ`` (test–retest ICC)
    gives ``S* = [[λ·Var(x), Cov(x,g)], [Cov(x,g), Var(g)]]`` and ``β* = S*⁻¹ s`` (``Cov([x,g],y)``
    unchanged). The corrected group SE/p come from a **player-clustered bootstrap** of ``β*`` so the
    corrected number is never presented with a fabricated precision.
    """
    lam = float(lam)
    if not (0.0 < lam <= 1.0):
        return dict(ok=False, reason=f"reliability ICC={lam} outside (0, 1]",
                    beta_naive=None, beta_corr=None, se_group=None, p_group=None, n_boot=0)

    def _resid(A: np.ndarray, M: np.ndarray) -> np.ndarray:
        b, *_ = np.linalg.lstsq(M, A, rcond=None)
        return A - M @ b

    def _moments(idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Cs, ys, xs, gs = C[idx], y[idx], x[idx], g[idx]
        ry, rx, rg = _resid(ys, Cs), _resid(xs, Cs), _resid(gs, Cs)
        S = np.array([[float(np.mean(rx * rx)), float(np.mean(rx * rg))],
                      [float(np.mean(rx * rg)), float(np.mean(rg * rg))]])
        s = np.array([float(np.mean(rx * ry)), float(np.mean(rg * ry))])
        return S, s

    idx_all = np.arange(y.size)
    S, s = _moments(idx_all)
    try:
        beta_naive = np.linalg.solve(S, s)
    except np.linalg.LinAlgError:
        return dict(ok=False, reason="singular naive moment matrix", beta_naive=None,
                    beta_corr=None, se_group=None, p_group=None, n_boot=0)
    Sstar = np.array([[lam * S[0, 0], S[0, 1]], [S[0, 1], S[1, 1]]])
    try:
        beta_corr = np.linalg.solve(Sstar, s)
    except np.linalg.LinAlgError:
        return dict(ok=False, reason="singular corrected moment matrix", beta_naive=beta_naive,
                    beta_corr=None, se_group=None, p_group=None, n_boot=0)

    rng = np.random.default_rng(int(seed))
    uniq = sorted(set(clusters.tolist()))
    by_group = {c: np.where(clusters == c)[0] for c in uniq}
    draws: list[float] = []
    for _ in range(int(reps)):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([by_group[c] for c in pick])
        Sb, sb = _moments(idx)
        Sstarb = np.array([[lam * Sb[0, 0], Sb[0, 1]], [Sb[0, 1], Sb[1, 1]]])
        try:
            draws.append(float(np.linalg.solve(Sstarb, sb)[1]))
        except np.linalg.LinAlgError:
            continue
    arr = np.asarray(draws, float)
    if arr.size < 2:
        return dict(ok=False, reason="bootstrap produced <2 usable replicates", beta_naive=beta_naive,
                    beta_corr=beta_corr, se_group=None, p_group=None, n_boot=int(arr.size))
    se = float(np.std(arr, ddof=1))
    n_le = int(np.sum(arr <= 0.0))
    n_ge = int(np.sum(arr >= 0.0))
    p = float(min(1.0, 2.0 * min((n_le + 1) / (arr.size + 1), (n_ge + 1) / (arr.size + 1))))
    return dict(ok=True, reason="", beta_naive=beta_naive, beta_corr=beta_corr,
                se_group=se, p_group=p, n_boot=int(arr.size))


# --------------------------------------------------------------------------- #
# model rows
# --------------------------------------------------------------------------- #
def _season(game_id: Any, cfg: dict) -> int | None:
    p = cfg["security_models"]["primary"]
    s = str(int(game_id))
    d = int(p["season_digits"])
    if len(s) < d:
        return None
    try:
        y = int(s[:d])
    except (TypeError, ValueError):
        return None
    if y < int(p["season_min"]) or y > int(p["season_max"]):
        return None
    return y


def _route_family(route: Any, cfg: dict) -> str:
    p = cfg["security_models"]["primary"]
    if route is None:
        return str(p["route_family_default"])
    return str(p["route_family_map"].get(str(route).upper(), p["route_family_default"]))


def _assign_map(assign: dict) -> dict[int, dict]:
    return {int(a["nfl_id"]): a for a in assign["assignments"]}


def build_effort_rows(efforts: Sequence[dict], assign: dict, envelope: dict[int, dict],
                      cov: dict[int, dict], psum: dict[int, dict], cfg: dict,
                      keep_unclassified: bool = False) -> tuple[list[dict], dict]:
    """Effort-level model rows with every pre-computed control; drops counted by reason.

    ``unclassified`` players (the middle tertile of the level fallback, and board rows with no
    pre-Combine information) are excluded from the two-group contrast. They are keyed by
    ``keep_unclassified=True`` so the continuous ``log(EDP_pre)`` dose-response (C) still sees them,
    per ``security_features.py`` / DV-3 / limitations item 24.
    """
    p = cfg["security_models"]["primary"]
    contrast = set(cfg["security_models"]["sort"]["groups"])
    unclass = str(cfg["security_models"]["sort"].get("unclassified_label", "unclassified"))
    allowed = contrast | {unclass} if keep_unclassified else contrast
    snap_denom = float(cfg["game"]["route_snap_count"])
    clip = float(p["snap_share_clip"])
    amap = _assign_map(assign)
    out: list[dict] = []
    dropped: dict[str, int] = {}

    def _drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for e in efforts:
        nid = int(e["nfl_id"])
        a = amap.get(nid)
        if a is None or a["group"] not in allowed:
            _drop("group_not_in_contrast")
            continue
        env = envelope.get(nid)
        if env is None:
            _drop("no_combine_envelope")
            continue
        c = cov.get(nid)
        if c is None:
            _drop("no_covariates")
            continue
        ps = psum.get(nid)
        if ps is None:
            _drop("no_player_summary")
            continue
        peak = _num(e["peak_speed"])
        if peak is None or peak <= 0:
            _drop("non_positive_game_speed")
            continue
        season = _season(e["game_id"], cfg)
        years = (float(season) - float(c["draft_year"])
                 if season is not None and c.get("draft_year") is not None else None)
        n_snaps = int(ps["n_route_snaps"] or 0)
        snap_share = min(float(n_snaps) / snap_denom, clip) if snap_denom > 0 else None
        row = dict(
            nfl_id=nid, game_id=int(e["game_id"]), play_id=int(e["play_id"]),
            group=a["group"], stratum=a["stratum"],
            moved_down=1.0 if a["group"] == GROUP_POS else 0.0,
            y=math.log(peak), peak_speed=peak,
            log_edp_pre=a["log_edp_pre"], edp_pre=a["edp_pre"],
            route_family=_route_family(e["route_ran"], cfg),
            pass_length=_num(e["pass_length"]),
            position=(c.get("position") or a.get("position_group")),
            height=_num(c.get("height")), weight=_num(c.get("weight")),
            years_since_combine=years,
            snap_share=snap_share, n_route_snaps=n_snaps,
        )
        for feat in ["env_vmax"] + list(p["robustness_features"]):
            v = _num(env.get(feat))
            row[f"log_{feat}"] = (math.log(v) if v is not None and v > 0 else None)
        env_vmax = _num(env.get("env_vmax"))
        row["log_ceiling"] = row["log_env_vmax"]
        row["gap"] = (row["y"] - math.log(env_vmax)) if env_vmax and env_vmax > 0 else None
        row["utilization"] = (peak / env_vmax) if env_vmax and env_vmax > 0 else None
        out.append(row)
    return out, dropped


def _complete(rows: Iterable[dict], keys: Sequence[str]) -> list[dict]:
    out = []
    for r in rows:
        if all(_num(r.get(k)) is not None for k in keys):
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# B — primary effort-level model (+ attenuation + group means)
# --------------------------------------------------------------------------- #
def run_b(rows: list[dict], cov: dict[int, dict], rel: dict[str, dict], cfg: dict,
          dropped: dict, alt_rows: list[dict] | None = None,
          alt_spec: str = "threshold_T5") -> tuple[list[dict], list[dict], dict]:
    p = cfg["security_models"]["primary"]
    scopes = list(p["scopes"])
    min_obs = int(p["min_obs"])
    groups = list(cfg["security_models"]["sort"]["groups"])
    # B is the two-group contrast: only the contrast groups enter, even when C keeps the middle
    # tertile in the continuous dose-response.
    rows = [r for r in rows if r.get("group") in set(groups)]
    min_group_n = int(cfg["security_models"]["sort"]["min_group_n"])
    num_controls = [c for c in p["controls"] if c not in ("route_family", "position")]
    cat_controls = [c for c in p["controls"] if c in ("route_family", "position")]
    if p.get("stratum_fe", True):
        cat_controls = cat_controls + ["stratum"]
    req = ["y", "log_ceiling", "moved_down"] + num_controls
    out: list[dict] = []
    means: list[dict] = []

    def _scope_rows(scope: str, spec_rows: list[dict]) -> list[dict]:
        if scope == "pooled":
            return spec_rows
        return [r for r in spec_rows if str(r.get("position")) == scope]

    specs = [("primary_env_vmax", "log_env_vmax")]
    for feat in p["robustness_features"]:
        specs.append((f"robust_{feat}", f"log_{feat}"))

    for scope in scopes:
        for spec, xkey in specs:
            rows_s = _scope_rows(scope, rows)
            keys = [xkey if k == "log_ceiling" else k for k in req]
            fin = _complete(rows_s, keys)
            rec = dict(spec=spec, scope=scope, n_obs=len(fin),
                       n_players=len({r["nfl_id"] for r in fin}),
                       n_players_moved_down=0, n_players_high_secure=0, thin=False,
                       group_coef=None, group_se=None,
                       group_p=None, group_ci_lo=None, group_ci_hi=None, speed_coef=None,
                       speed_se=None, speed_p=None, speed_coef_corrected=None,
                       group_coef_corrected=None, group_se_corrected=None,
                       group_p_corrected=None, reliability_icc=None, reliability_source=None,
                       r2=None, method="ols_cluster" if p["cluster"] == "player" else "ols_hc1",
                       note="")
            feature = "env_vmax" if spec == "primary_env_vmax" else spec.replace("robust_", "")
            icc = rel.get(feature, {})
            rec["reliability_icc"] = icc.get("icc")
            rec["reliability_source"] = icc.get("source")
            thin = _mark_thin(rec, fin, groups, min_group_n)
            if thin:
                rec["note"] = thin
                out.append(rec)
                continue
            if rec["n_obs"] < min_obs:
                rec["note"] = f"underpowered: n_obs={rec['n_obs']} < min_obs={min_obs}"
                out.append(rec)
                continue
            try:
                X, names, dr = design(fin, [xkey, "moved_down"] + num_controls, cat_controls,
                                      focal=[xkey, "moved_down"])
            except ValueError as exc:
                rec["note"] = f"design failed: {exc}"
                out.append(rec)
                continue
            y = np.array([r["y"] for r in fin], float)
            clusters = np.array([r["nfl_id"] for r in fin]) if p["cluster"] == "player" else None
            res = ols(y, X, clusters=clusters)
            gc, sc = _coef(res, names, "moved_down"), _coef(res, names, xkey)
            rec.update(n_obs=int(res.nobs), group_coef=gc["coef"], group_se=gc["se"],
                       group_p=gc["p"], group_ci_lo=gc["ci_lo"], group_ci_hi=gc["ci_hi"],
                       speed_coef=sc["coef"], speed_se=sc["se"], speed_p=sc["p"],
                       r2=float(res.rsquared))
            if dr:
                rec["note"] = "dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dr)
            # ---- attenuation (FWL + ICC moment matrix) ----
            _apply_attenuation(rec, fin, xkey, y, X, names, icc, num_controls, cat_controls, p, cfg,
                               feature=feature)
            out.append(rec)
        # ---- pre-declared sensitivity panels: one stratum at a time (same spec) ----
        # Panels are the PRIMARY specification split by stratum, so they always use the primary
        # Combine feature — never a leftover loop variable from the robustness specs above.
        panel_xkey = f"log_{p['speed_feature']}"
        panel_icc = rel.get(p["speed_feature"]) or {}
        for panel in list(p.get("panels") or []):
            stratum = "trend" if str(panel).startswith("trend") else "level_fallback"
            rows_s = [r for r in _scope_rows(scope, rows) if r.get("stratum") == stratum]
            keys = [panel_xkey if k == "log_ceiling" else k for k in req]
            fin = _complete(rows_s, keys)
            rec = dict(spec=f"panel_{panel}", scope=scope, n_obs=len(fin),
                       n_players=len({r["nfl_id"] for r in fin}),
                       n_players_moved_down=0, n_players_high_secure=0, thin=False,
                       group_coef=None, group_se=None,
                       group_p=None, group_ci_lo=None, group_ci_hi=None, speed_coef=None,
                       speed_se=None, speed_p=None, speed_coef_corrected=None,
                       group_coef_corrected=None, group_se_corrected=None, group_p_corrected=None,
                       reliability_icc=panel_icc.get("icc"),
                       reliability_source=panel_icc.get("source"),
                       r2=None, method="ols_cluster" if p["cluster"] == "player" else "ols_hc1",
                       note=f"sensitivity panel: {stratum} stratum only")
            thin = _mark_thin(rec, fin, groups, min_group_n)
            if thin:
                rec["note"] += f" | {thin}"
                out.append(rec)
                continue
            if rec["n_obs"] < min_obs:
                rec["note"] += f" | underpowered: n_obs={rec['n_obs']} < min_obs={min_obs}"
                out.append(rec)
                continue
            try:
                X, names, dr = design(fin, [panel_xkey, "moved_down"] + num_controls,
                                      cat_controls, focal=[panel_xkey, "moved_down"])
                y = np.array([r["y"] for r in fin], float)
                res = ols(y, X, clusters=np.array([r["nfl_id"] for r in fin])
                          if p["cluster"] == "player" else None)
                gc, sc = _coef(res, names, "moved_down"), _coef(res, names, panel_xkey)
                rec.update(n_obs=int(res.nobs), group_coef=gc["coef"], group_se=gc["se"],
                           group_p=gc["p"], group_ci_lo=gc["ci_lo"], group_ci_hi=gc["ci_hi"],
                           speed_coef=sc["coef"], speed_se=sc["se"], speed_p=sc["p"],
                           r2=float(res.rsquared))
                if dr:
                    rec["note"] += " | dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dr)
                _apply_attenuation(rec, fin, panel_xkey, y, X, names, panel_icc,
                                   num_controls, cat_controls, p, cfg,
                                   feature=p["speed_feature"])
            except ValueError as exc:
                rec["note"] += f" | design failed: {exc}"
            out.append(rec)
        # ---- D40 declared sensitivity: the SAME primary specification at the T=5 threshold ----
        # This changes the GROUP DEFINITION (moved_down iff edp_trend >= 5.0), not the estimator:
        # same feature, controls, scope population and stratum fixed effect as the primary.
        if alt_rows is not None:
            sort = cfg["security_models"]["sort"]
            alt_keys = [panel_xkey if k == "log_ceiling" else k for k in req]
            rows_a = [r for r in _scope_rows(scope, alt_rows) if r.get("group") in set(groups)]
            fin = _complete(rows_a, alt_keys)
            rec = dict(spec=f"panel_{alt_spec}", scope=scope, n_obs=len(fin),
                       n_players=len({r["nfl_id"] for r in fin}),
                       n_players_moved_down=0, n_players_high_secure=0, thin=False,
                       group_coef=None, group_se=None,
                       group_p=None, group_ci_lo=None, group_ci_hi=None, speed_coef=None,
                       speed_se=None, speed_p=None, speed_coef_corrected=None,
                       group_coef_corrected=None, group_se_corrected=None, group_p_corrected=None,
                       reliability_icc=panel_icc.get("icc"),
                       reliability_source=panel_icc.get("source"),
                       r2=None, method="ols_cluster" if p["cluster"] == "player" else "ols_hc1",
                       note=(f"declared D40 sensitivity: moved_down iff edp_trend >= "
                             f"{float(sort['sensitivity_threshold']):g} (primary T="
                             f"{float(sort['fall_threshold']):g})"))
            thin = _mark_thin(rec, fin, groups, min_group_n)
            if thin:
                rec["note"] += f" | {thin}"
                out.append(rec)
                continue
            if rec["n_obs"] < min_obs:
                rec["note"] += f" | underpowered: n_obs={rec['n_obs']} < min_obs={min_obs}"
                out.append(rec)
                continue
            try:
                X, names, dr = design(fin, [panel_xkey, "moved_down"] + num_controls,
                                      cat_controls, focal=[panel_xkey, "moved_down"])
                y = np.array([r["y"] for r in fin], float)
                res = ols(y, X, clusters=np.array([r["nfl_id"] for r in fin])
                          if p["cluster"] == "player" else None)
                gc, sc = _coef(res, names, "moved_down"), _coef(res, names, panel_xkey)
                rec.update(n_obs=int(res.nobs), group_coef=gc["coef"], group_se=gc["se"],
                           group_p=gc["p"], group_ci_lo=gc["ci_lo"], group_ci_hi=gc["ci_hi"],
                           speed_coef=sc["coef"], speed_se=sc["se"], speed_p=sc["p"],
                           r2=float(res.rsquared))
                if dr:
                    rec["note"] += " | dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dr)
                _apply_attenuation(rec, fin, panel_xkey, y, X, names, panel_icc,
                                   num_controls, cat_controls, p, cfg,
                                   feature=p["speed_feature"])
            except ValueError as exc:
                rec["note"] += f" | design failed: {exc}"
            out.append(rec)
        # ---- per-player top-k-mean variant (HC1) ----
        if p.get("topk_mean_variant", True):
            rows_s = _scope_rows(scope, rows)
            by_id: dict[int, list[dict]] = {}
            for r in rows_s:
                by_id.setdefault(r["nfl_id"], []).append(r)
            prow = []
            for nid, rs in by_id.items():
                rs = [r for r in rs if r.get("topk_mean_speed") is not None]
                base = next((r for r in rs if _num(r.get("topk_mean_speed"))), None)
                if base is None:
                    continue
                v = _num(base.get("topk_mean_speed"))
                if v is None or v <= 0:
                    continue
                pr = dict(base)
                pr["y"] = math.log(v)
                prow.append(pr)
            fin = _complete(prow, ["y", "log_env_vmax", "moved_down"] + num_controls)
            rec = dict(spec="topk_mean_player", scope=scope, n_obs=len(fin),
                       n_players=len({r["nfl_id"] for r in fin}),
                       n_players_moved_down=0, n_players_high_secure=0, thin=False,
                       group_coef=None, group_se=None,
                       group_p=None, group_ci_lo=None, group_ci_hi=None, speed_coef=None,
                       speed_se=None, speed_p=None, speed_coef_corrected=None,
                       group_coef_corrected=None, group_se_corrected=None, group_p_corrected=None,
                       reliability_icc=(rel.get("env_vmax") or {}).get("icc"),
                       reliability_source=(rel.get("env_vmax") or {}).get("source"),
                       r2=None, method="ols_hc1", note="per-player log(top-k mean speed)")
            thin = _mark_thin(rec, fin, groups, min_group_n)
            if not thin:
                try:
                    X, names, dr = design(fin, ["log_env_vmax", "moved_down"] + num_controls,
                                          cat_controls, focal=["log_env_vmax", "moved_down"])
                    y = np.array([r["y"] for r in fin], float)
                    res = ols(y, X)
                    gc, sc = _coef(res, names, "moved_down"), _coef(res, names, "log_env_vmax")
                    rec.update(n_obs=int(res.nobs), group_coef=gc["coef"], group_se=gc["se"],
                               group_p=gc["p"], group_ci_lo=gc["ci_lo"], group_ci_hi=gc["ci_hi"],
                               speed_coef=sc["coef"], speed_se=sc["se"], speed_p=sc["p"],
                               r2=float(res.rsquared))
                    if dr and not rec["note"].endswith("speed"):
                        rec["note"] += " | "
                    if dr:
                        rec["note"] += "dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dr)
                    _apply_attenuation(rec, fin, "log_env_vmax", y, X, names,
                                       rel.get("env_vmax") or {}, num_controls, cat_controls,
                                       p, cfg, feature="env_vmax")
                except ValueError as exc:
                    rec["note"] += f" | design failed: {exc}"
            else:
                rec["note"] += f" | {thin}"
            out.append(rec)

        # ---- group means (top-k efforts, finite env_vmax) ----
        rows_s = _scope_rows(scope, rows)
        rows_s = [r for r in rows_s if r.get("gap") is not None]
        for g in groups:
            sub = [r for r in rows_s if r["group"] == g]
            if not sub:
                continue
            means.append(dict(
                scope=scope, group=g, n_obs=len(sub),
                n_players=len({r["nfl_id"] for r in sub}),
                mean_log_game_speed=float(np.mean([r["y"] for r in sub])),
                mean_log_ceiling=float(np.mean([r["log_ceiling"] for r in sub])),
                mean_gap=float(np.mean([r["gap"] for r in sub])),
                mean_utilization=float(np.mean([r["utilization"] for r in sub
                                                if r.get("utilization") is not None]))
                if any(r.get("utilization") is not None for r in sub) else None))
    return out, means, dropped


# --------------------------------------------------------------------------- #
# C — which end drives it (continuous piecewise) + exceedance
# --------------------------------------------------------------------------- #
def _piecewise(x: np.ndarray, knots: Sequence[float]) -> list[np.ndarray]:
    return [x] + [np.maximum(0.0, x - float(k)) for k in knots]


def run_c(rows: list[dict], topk: Sequence[dict], envelope: dict[int, dict], assign: dict,
          cfg: dict) -> tuple[list[dict], list[dict]]:
    c = cfg["security_models"]["c"]
    p = cfg["security_models"]["primary"]
    pcts = [float(q) for q in c["security_percentiles"]]
    num_controls = [k for k in p["controls"] if k not in ("route_family", "position")]
    cat_controls = [k for k in p["controls"] if k in ("route_family", "position")]
    req = ["y", "log_ceiling", "log_edp_pre"] + num_controls
    end_rows: list[dict] = []
    exc_rows: list[dict] = []

    # ---- exceedance FIRST (overall, then by group) ----
    amap = _assign_map(assign)
    margin = float(cfg["audit"]["exceedance_margin_rel"])
    z = float(c["wilson_z"])
    for scope in list(c["scopes"]):
        sub = []
        for e in topk:
            nid = int(e["nfl_id"])
            a = amap.get(nid)
            env = envelope.get(nid)
            if a is None or env is None:
                continue
            ceil = _num(env.get("env_vmax"))
            peak = _num(e["peak_speed"])
            if ceil is None or ceil <= 0 or peak is None:
                continue
            if scope != "pooled" and str(a["position_group"]) != scope:
                continue
            sub.append((a["group"], peak > ceil * (1.0 + margin)))
        n_all = len(sub)
        n_exc = sum(1 for _, x in sub if x)
        rate, lo, hi = _wilson(n_exc, n_all, z)
        exc_rows.append(dict(scope=scope, group="overall", n_efforts=n_all, n_exceed=n_exc,
                             exceedance_rate=rate, wilson_lo=lo, wilson_hi=hi,
                             margin_rel=margin, note="overall reported first"))
        for g in list(cfg["security_models"]["sort"]["groups"]) + \
                [str(cfg["security_models"]["sort"]["unclassified_label"])]:
            gg = [x for grp, x in sub if grp == g]
            if not gg:
                continue
            n_e = sum(1 for x in gg if x)
            r, l, h = _wilson(n_e, len(gg), z)
            exc_rows.append(dict(scope=scope, group=g, n_efforts=len(gg), n_exceed=n_e,
                                 exceedance_rate=r, wilson_lo=l, wilson_hi=h,
                                 margin_rel=margin, note=""))

    # ---- continuous piecewise model ----
    for scope in list(c["scopes"]):
        rows_s = rows if scope == "pooled" else [r for r in rows if str(r.get("position")) == scope]
        fin = _complete(rows_s, req)
        if len(fin) < int(c["min_obs"]) or len({r["nfl_id"] for r in fin}) < 2:
            end_rows.append(dict(scope=scope, percentile=None, log_edp_pre=None,
                                 basis="unavailable", fitted_gap=None, se=None, ci_lo=None,
                                 ci_hi=None, n_obs=len(fin),
                                 n_players=len({r["nfl_id"] for r in fin}),
                                 note=f"underpowered: n_obs={len(fin)} < min_obs={c['min_obs']}"))
            continue
        x = np.array([r["log_edp_pre"] for r in fin], float)
        knots = [float(np.quantile(x, q)) for q in c["knot_percentiles"]]
        pw = _piecewise(x, knots)
        for r, c1, c2 in zip(fin, pw[1], pw[2]):
            r["pw_linear"], r["pw_knot1"], r["pw_knot2"] = r["log_edp_pre"], float(c1), float(c2)
        X, names, dr = design(fin, ["log_ceiling", "pw_linear", "pw_knot1", "pw_knot2"] + num_controls,
                              cat_controls, focal=["log_ceiling", "pw_linear"])
        y = np.array([r["y"] for r in fin], float)
        clusters = np.array([r["nfl_id"] for r in fin])
        res = ols(y, X, clusters=clusters)
        beta = np.asarray(res.params, float)
        V = np.asarray(res.cov_params(), float)
        ilc = names.index("log_ceiling")
        mean_row = X.mean(axis=0)
        lc_mean = float(mean_row[ilc])
        for q in pcts:
            basis = _piecewise(np.array([float(np.quantile(x, q))]), knots)
            a = mean_row.copy()
            a[names.index("pw_linear")] = float(basis[0][0])
            a[names.index("pw_knot1")] = float(basis[1][0])
            a[names.index("pw_knot2")] = float(basis[2][0])
            # (a) reference at the mean ceiling
            a_mean = a.copy()
            a_mean[ilc] = lc_mean
            gap_mean = float(a_mean @ beta - lc_mean)
            se_mean = float(math.sqrt(max(0.0, a_mean @ V @ a_mean)))
            # (b) within-player (own ceiling): average fitted gap at this log(EDP) percentile
            A = X.copy()
            A[:, names.index("pw_linear")] = float(basis[0][0])
            A[:, names.index("pw_knot1")] = float(basis[1][0])
            A[:, names.index("pw_knot2")] = float(basis[2][0])
            gap_own = float(np.mean(A @ beta - X[:, ilc]))
            g_own = A.mean(axis=0).copy()
            g_own[ilc] -= 1.0
            se_own = float(math.sqrt(max(0.0, g_own @ V @ g_own)))
            note = "knots=" + ",".join(f"{k:.4f}" for k in knots) + \
                   ("; dropped=" + ";".join(f"{a_}:{b}" for a_, b in dr) if dr else "")
            for basis_name, gap, se in (("mean_ceiling", gap_mean, se_mean),
                                        ("own_ceiling", gap_own, se_own)):
                end_rows.append(dict(scope=scope, percentile=q,
                                     log_edp_pre=float(np.quantile(x, q)), basis=basis_name,
                                     fitted_gap=gap, se=se, ci_lo=gap - z * se, ci_hi=gap + z * se,
                                     n_obs=len(fin), n_players=len({r["nfl_id"] for r in fin}),
                                     note=note))
    return end_rows, exc_rows


def _wilson(k: int, n: int, z: float) -> tuple[float | None, float | None, float | None]:
    if n <= 0:
        return None, None, None
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return float(p), float(centre - half), float(centre + half)


# --------------------------------------------------------------------------- #
# D — skippers (exploratory)
# --------------------------------------------------------------------------- #
def run_d(rows: list[dict], exposures: dict[int, dict], cfg: dict) -> list[dict]:
    d = cfg["security_models"]["d"]
    by_id: dict[int, dict] = {}
    for r in rows:
        by_id.setdefault(r["nfl_id"], r)
    num_controls = [k for k in ("height", "weight") ]
    dash, skip = [], []
    for nid, r in by_id.items():
        ex = exposures.get(nid) or {}
        pat = ex.get("participation_pattern")
        rec = dict(r)
        rec["participation_pattern"] = pat
        rec["n_drills"] = ex.get("n_drills")
        if r.get("gap") is None or _num(r.get("height")) is None or _num(r.get("weight")) is None:
            continue
        if pat in set(d["dash_patterns"]):
            dash.append(rec)
        elif r["group"] == GROUP_NEG and pat in set(d["skipper_patterns"]):
            skip.append(rec)
    base = dict(n_dash=len(dash), n_skippers=len(skip), alpha=d["alpha"], min_n=d["min_n"])
    if len(skip) < int(d["min_n"]) or len(dash) < int(d["min_n"]):
        return [dict(base, n=len(skip), mean_residual=None, ci_lo=None, ci_hi=None,
                     observed_mean=None, predicted_mean=None,
                     note=f"reported null: n_skippers={len(skip)} < min_n={d['min_n']}")]
    try:
        X, names, _ = design(dash, num_controls + ["n_drills"], ["position"],
                             focal=["height", "weight"])
        y = np.array([r["gap"] for r in dash], float)
        res = ols(y, X)
        # rebuild the skipper design with the DASH column layout (predict, do not refit)
        X_full = _apply_layout(skip, names, dash, ["position"])
        pred = X_full @ np.asarray(res.params, float)
        obs = np.array([r["gap"] for r in skip], float)
        resid = obs - pred
        n = len(resid)
        mean = float(np.mean(resid))
        se = float(np.std(resid, ddof=1) / math.sqrt(n)) if n > 1 else None
        from scipy.stats import norm
        z = float(norm.ppf(1.0 - float(d["alpha"]) / 2.0))
        return [dict(base, n=n, mean_residual=mean,
                     ci_lo=(mean - z * se if se is not None else None),
                     ci_hi=(mean + z * se if se is not None else None),
                     observed_mean=float(np.mean(obs)), predicted_mean=float(np.mean(pred)),
                     note="observed minus predicted gap (log units); size + completed drills model")]
    except ValueError as exc:
        return [dict(base, n=len(skip), mean_residual=None, ci_lo=None, ci_hi=None,
                     observed_mean=None, predicted_mean=None,
                     note=f"reported null: design unavailable ({exc})")]


def _apply_layout(target: list[dict], names: list[str], source: list[dict],
                  categorical: Sequence[str]) -> np.ndarray:
    """Build a design for ``target`` with the column layout of ``names`` (prediction only)."""
    if not target:
        return np.zeros((0, len(names)))
    cols = []
    for nm in names:
        if nm == "const":
            cols.append(np.ones(len(target)))
        elif "=" in nm:
            k, lv = nm.split("=", 1)
            cols.append(np.array([1.0 if str(r.get(k)) == lv else 0.0 for r in target], float))
        else:
            cols.append(np.array([_num(r.get(nm)) for r in target], float))
    return np.column_stack(cols)


# --------------------------------------------------------------------------- #
# E — selection + IPW
# --------------------------------------------------------------------------- #
def build_selection_pool(exposures: dict[int, dict], envelope: dict[int, dict],
                         cov: dict[int, dict], appeared: set[int], cfg: dict) -> list[dict]:
    """Board players with Combine tracking: pre-Combine EDP + size + Combine speed + appeared flag."""
    out: list[dict] = []
    for nid, ex in exposures.items():
        env, c = envelope.get(nid), cov.get(nid)
        if env is None or c is None:
            continue
        spd = _num(env.get("env_vmax"))
        out.append(dict(
            nfl_id=nid, position_group=ex["position_group"],
            position=(c.get("position") or ex.get("position")),
            log_edp_pre=ex["log_edp_pre"], edp_pre=ex["edp_pre"], edp_trend=ex["edp_trend"],
            has_trend=ex["has_trend"], has_primary=ex["has_primary"],
            participation_pattern=ex.get("participation_pattern"),
            height=_num(c.get("height")), weight=_num(c.get("weight")),
            log_combine_speed=(math.log(spd) if spd and spd > 0 else None),
            draft_year=c.get("draft_year"),
            appeared=bool(nid in appeared)))
    out.sort(key=lambda r: r["nfl_id"])
    return out


def run_e(pool: list[dict], rows: list[dict], cfg: dict) -> tuple[list[dict], list[dict], dict]:
    e = cfg["security_models"]["e"]
    p = cfg["security_models"]["primary"]
    fin = [r for r in pool if all(_num(r.get(k)) is not None
                                  for k in ("log_edp_pre", "log_combine_speed", "height", "weight"))]
    n_app = sum(1 for r in fin if r["appeared"])
    meta = dict(n_pool=len(fin), n_appeared=n_app, min_n=e["min_n"], min_appeared=e["min_appeared"])
    pool_floor = int(cfg["security_models"]["selection"]["min_n"])
    if len(fin) < pool_floor or n_app < int(e["min_appeared"]):
        return ([dict(term="unavailable", coef=None, se=None, z=None, p=None, odds_ratio=None,
                      or_ci_lo=None, or_ci_hi=None, n_obs=len(fin), n_appeared=n_app,
                      note=f"reported null: pool={len(fin)}, appeared={n_app} below floors")],
                [dict(weighting="unavailable", group_coef=None, group_se=None, group_p=None,
                      n_obs=0, n_players=0, trim_lo=None, trim_hi=None,
                      note="reported null: selection model unavailable")], meta)
    try:
        import statsmodels.api as sm
        # standardize the two log predictors so the reported odds ratios are per SD (the fitted
        # model is unchanged; the raw-scale coefficient on log speed is not interpretable)
        for key, zkey in (("log_edp_pre", "z_log_edp_pre"),
                          ("log_combine_speed", "z_log_combine_speed")):
            vals = np.array([_num(r[key]) for r in fin], float)
            mu, sdv = float(np.mean(vals)), float(np.std(vals, ddof=0)) or 1.0
            for r, v in zip(fin, vals):
                r[zkey] = (float(v) - mu) / sdv
        X, names, dr = design(fin, ["z_log_edp_pre", "z_log_combine_speed", "height", "weight"],
                              ["position"], focal=["z_log_edp_pre"])
        y = np.array([1.0 if r["appeared"] else 0.0 for r in fin], float)
        res = sm.Logit(y, X).fit(disp=0, maxiter=int(e["max_iter"]))
    except Exception as exc:  # noqa: BLE001 - separation / non-convergence -> reported null
        return ([dict(term="unavailable", coef=None, se=None, z=None, p=None, odds_ratio=None,
                      or_ci_lo=None, or_ci_hi=None, n_obs=len(fin), n_appeared=n_app,
                      note=f"reported null: logit failed ({type(exc).__name__}: {exc})")],
                [dict(weighting="unavailable", group_coef=None, group_se=None, group_p=None,
                      n_obs=0, n_players=0, trim_lo=None, trim_hi=None,
                      note="reported null: selection model unavailable")], meta)
    ci = np.asarray(res.conf_int(), float)
    sel_rows = []
    for i, nm in enumerate(names):
        if nm == "const":
            continue
        sel_rows.append(dict(term=nm, coef=float(res.params[i]), se=float(res.bse[i]),
                             z=float(res.tvalues[i]), p=float(res.pvalues[i]),
                             odds_ratio=float(math.exp(res.params[i])),
                             or_ci_lo=float(math.exp(ci[i, 0])), or_ci_hi=float(math.exp(ci[i, 1])),
                             n_obs=len(fin), n_appeared=n_app,
                             note=("standardized predictor (OR per SD)" if nm.startswith("z_")
                                   else "raw units")
                             + (("; dropped: " + ";".join(f"{a}:{b}" for a, b in dr)) if dr else "")))
    p_hat = np.clip(np.asarray(res.predict(X), float), 1e-6, 1 - 1e-6)
    prob = {r["nfl_id"]: float(ph) for r, ph in zip(fin, p_hat)}
    w_raw = {nid: 1.0 / ph for nid, ph in prob.items()}
    return sel_rows, _ipw_rows(rows, w_raw, prob, cfg), meta


def _ipw_rows(rows: list[dict], w_raw: dict[int, float], prob: dict[int, float],
              cfg: dict) -> list[dict]:
    e = cfg["security_models"]["e"]
    p = cfg["security_models"]["primary"]
    num_controls = [k for k in p["controls"] if k not in ("route_family", "position")]
    cat_controls = [k for k in p["controls"] if k in ("route_family", "position")]
    fin = _complete([r for r in rows if r["nfl_id"] in w_raw], ["y", "log_env_vmax", "moved_down"]
                    + num_controls)
    out: list[dict] = []
    if not fin or len(fin) < int(e["min_n"]):
        return [dict(weighting="unavailable", group_coef=None, group_se=None, group_p=None,
                     n_obs=0, n_players=0, trim_lo=None, trim_hi=None,
                     note=("reported null: no overlap between the effort rows and the pool"
                           if not fin else
                           f"reported null: n_obs={len(fin)} < e.min_n={e['min_n']}"))]
    X, names, _ = design(fin, ["log_env_vmax", "moved_down"] + num_controls, cat_controls,
                         focal=["log_env_vmax", "moved_down"])
    y = np.array([r["y"] for r in fin], float)
    clusters = np.array([r["nfl_id"] for r in fin])
    w = np.array([w_raw[r["nfl_id"]] for r in fin], float)
    w = w * float(np.mean([prob[r["nfl_id"]] for r in fin]))    # stabilized (scale only)
    res0 = ols(y, X, clusters=clusters)
    gc = _coef(res0, names, "moved_down")
    out.append(dict(weighting="unweighted", group_coef=gc["coef"], group_se=gc["se"],
                    group_p=gc["p"], n_obs=len(fin), n_players=len(set(clusters.tolist())),
                    trim_lo=None, trim_hi=None, note="baseline (no IPW)"))
    base = list(e["weight_grid"])
    primary_trim = [float(x) for x in e["weight_trim"]]
    if not any(abs(primary_trim[0] - float(a)) < 1e-12 and abs(primary_trim[1] - float(b)) < 1e-12
               for a, b in base):
        base.append(primary_trim)
    for lo, hi in base:
        lo, hi = float(lo), float(hi)
        if lo <= 0.0 and hi >= 1.0:
            ww = w.copy()
        else:
            ql, qh = np.quantile(w, lo), np.quantile(w, hi)
            ww = np.clip(w, ql, qh)
        rr = ols(y, X, clusters=clusters, weights=ww)
        gc = _coef(rr, names, "moved_down")
        out.append(dict(weighting="ipw", group_coef=gc["coef"], group_se=gc["se"], group_p=gc["p"],
                        n_obs=len(fin), n_players=len(set(clusters.tolist())),
                        trim_lo=lo, trim_hi=hi,
                        note=("primary trim" if abs(lo - primary_trim[0]) < 1e-12
                              and abs(hi - primary_trim[1]) < 1e-12 else "truncation grid")))
    return out


# --------------------------------------------------------------------------- #
# F — falsification
# --------------------------------------------------------------------------- #
def run_f(rows: list[dict], all_rows: list[dict], top_keys: set[tuple[int, int, int]],
          attempts: dict[int, dict], cfg: dict) -> list[dict]:
    """Falsification panels (F). ``rows`` are the top-k model rows; ``all_rows`` every effort."""
    f = cfg["security_models"]["f"]
    p = cfg["security_models"]["primary"]
    num_controls = [k for k in p["controls"] if k not in ("route_family", "position")]
    cat_controls = [k for k in p["controls"] if k in ("route_family", "position")]
    out: list[dict] = []

    # (1) placebo focal exposures: standardized height / weight in place of the group term
    base = _complete([r for r in rows if str(r.get("position")) == "WR"],
                     ["y", "log_env_vmax", "moved_down"] + num_controls)
    for placebo in list(f["placebo"]):
        rec = dict(test="placebo", stratum="WR", term=placebo, coef=None, se=None, p=None,
                   n_obs=0, n_players=0, expectation="should NOT reproduce the group pattern", note="")
        vals = [r.get(placebo) for r in base]
        if any(_num(v) is None for v in vals) or not base:
            rec["note"] = "reported null: placebo unavailable"
            out.append(rec)
            continue
        arr = np.array([_num(v) for v in vals], float)
        sd = float(np.std(arr, ddof=0)) or 1.0
        for r, v in zip(base, arr):
            r[f"z_{placebo}"] = (float(v) - float(np.mean(arr))) / sd
        try:
            X, names, _ = design(base, [f"z_{placebo}", "log_env_vmax"] + num_controls,
                                 cat_controls, focal=[f"z_{placebo}"])
            y = np.array([r["y"] for r in base], float)
            res = ols(y, X, clusters=np.array([r["nfl_id"] for r in base]))
            c = _coef(res, names, f"z_{placebo}")
            rec.update(coef=c["coef"], se=c["se"], p=c["p"], n_obs=len(base),
                       n_players=len({r["nfl_id"] for r in base}))
        except ValueError as exc:
            rec["note"] = f"reported null: {exc}"
        out.append(rec)

    # (2) top-k vs bottom-k (low-effort placebo)
    k = int(f["effort_split_k"])
    bottom = _bottom_k(all_rows, top_keys, k)
    for label, subset in (("top_k", rows), ("bottom_k", bottom)):
        sub = _complete([r for r in subset if str(r.get("position")) == "WR"],
                        ["y", "log_env_vmax", "moved_down"] + num_controls)
        rec = dict(test="effort_split", stratum=label, term="moved_down", coef=None, se=None,
                   p=None, n_obs=len(sub), n_players=len({r["nfl_id"] for r in sub}),
                   expectation=("present / stronger on top-k" if label == "top_k"
                                else "absent on bottom-k (low effort)"), note="")
        if len(sub) < int(f["min_obs"]):
            rec["note"] = f"reported null: n_obs={len(sub)} < min_obs={f['min_obs']}"
            out.append(rec)
            continue
        try:
            X, names, _ = design(sub, ["log_env_vmax", "moved_down"] + num_controls, cat_controls,
                                 focal=["log_env_vmax", "moved_down"])
            y = np.array([r["y"] for r in sub], float)
            res = ols(y, X, clusters=np.array([r["nfl_id"] for r in sub]))
            c = _coef(res, names, "moved_down")
            rec.update(coef=c["coef"], se=c["se"], p=c["p"])
        except ValueError as exc:
            rec["note"] = f"reported null: {exc}"
        out.append(rec)

    # (3) Combine peaking marker x group (player level): does peaking predict a smaller gap?
    by_id: dict[int, dict] = {}
    for r in rows:
        by_id.setdefault(r["nfl_id"], r)
    prow = []
    for nid, r in by_id.items():
        m = attempts.get(nid)
        if not m or m.get("best_minus_mean") is None or r.get("gap") is None:
            continue
        rr = dict(r)
        rr["marker_best_minus_mean"] = float(m["best_minus_mean"])
        rr["marker_attempts"] = float(m["n_attempts"])
        rr["marker_x_group"] = rr["marker_best_minus_mean"] * float(r["moved_down"])
        prow.append(rr)
    rec = dict(test="peaking_marker", stratum="WR", term="marker_x_group", coef=None, se=None,
               p=None, n_obs=0, n_players=0,
               expectation="positive: peaking predicts a smaller gap penalty among moved_down",
               note="")
    pw = [r for r in prow if str(r.get("position")) == "WR"]
    pw = _complete(pw, ["gap", "moved_down", "marker_best_minus_mean", "marker_x_group",
                        "height", "weight"])
    rec.update(n_obs=len(pw), n_players=len({r["nfl_id"] for r in pw}))
    if len(pw) < int(f["min_obs"]):
        rec["note"] = f"reported null: n_obs={len(pw)} < min_obs={f['min_obs']}"
    else:
        try:
            X, names, _ = design(pw, ["moved_down", "marker_best_minus_mean", "marker_x_group",
                                      "height", "weight"], ["position"],
                                 focal=["marker_x_group"])
            y = np.array([r["gap"] for r in pw], float)
            res = ols(y, X)
            c = _coef(res, names, "marker_x_group")
            rec.update(coef=c["coef"], se=c["se"], p=c["p"])
        except ValueError as exc:
            rec["note"] = f"reported null: {exc}"
    out.append(rec)
    return out


def _bottom_k(efforts: Sequence[dict], exclude: set[tuple[int, int, int]], k: int) -> list[dict]:
    """The k lowest-speed effort rows per player, excluding any effort already in the top-k."""
    by_id: dict[int, list[dict]] = {}
    for e in efforts:
        key = (int(e["nfl_id"]), int(e["game_id"]), int(e["play_id"]))
        if key in exclude:
            continue
        by_id.setdefault(int(e["nfl_id"]), []).append(e)
    out: list[dict] = []
    for nid, effs in by_id.items():
        effs = sorted([e for e in effs if _num(e.get("y")) is not None],
                      key=lambda e: (_num(e["y"]), e["game_id"], e["play_id"]))
        out.extend(effs[:k])
    return out


# --------------------------------------------------------------------------- #
# G — ceiling / tail quantile regressions
# --------------------------------------------------------------------------- #
def run_g(rows: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    g = cfg["security_models"]["g"]
    p = cfg["security_models"]["primary"]
    taus = [float(q) for q in cfg["models"]["quantiles"]]
    num_controls = [k for k in p["controls"] if k not in ("route_family", "position")]
    cat_controls = [k for k in p["controls"] if k in ("route_family", "position")]
    mcfg = dict(cfg["models"])
    mcfg["fit_max_iter"] = int(g["max_iter"])
    mcfg["fit_p_tol"] = float(g["p_tol"])
    cfg_qr = dict(cfg)
    cfg_qr["models"] = mcfg
    out: list[dict] = []
    tail: list[dict] = []
    for scope in list(g["scopes"]):
        sub = rows if scope == "pooled" else [r for r in rows if str(r.get("position")) == scope]
        fin = _complete(sub, ["y", "log_env_vmax", "moved_down"] + num_controls)
        for group in list(cfg["security_models"]["sort"]["groups"]):
            gg = [r for r in fin if r["group"] == group]
            u = [r["utilization"] for r in gg if r.get("utilization") is not None]
            row = dict(scope=scope, group=group, n_efforts=len(gg),
                       n_players=len({r["nfl_id"] for r in gg}))
            for q in [float(x) for x in g["utilization_percentiles"]]:
                key = f"util_p{int(round(q * 100))}"
                row[key] = float(np.quantile(u, q)) if u else None
            tail.append(row)
        if len(fin) < int(g["min_obs"]) or len({r["nfl_id"] for r in fin}) < 2:
            out.append(dict(scope=scope, tau=None, group_coef=None, boot_se=None, ci_lo=None,
                            ci_hi=None, n_obs=len(fin), n_players=len({r["nfl_id"] for r in fin}),
                            n_boot_ok=0, unstable=None, reported=None, action="report_null",
                            note=f"underpowered: n_obs={len(fin)} < min_obs={g['min_obs']}"))
            continue
        try:
            X, names, dr = design(fin, ["log_env_vmax", "moved_down"] + num_controls,
                                  cat_controls, focal=["log_env_vmax", "moved_down"])
        except ValueError as exc:
            out.append(dict(scope=scope, tau=None, group_coef=None, boot_se=None, ci_lo=None,
                            ci_hi=None, n_obs=len(fin), n_players=len({r["nfl_id"] for r in fin}),
                            n_boot_ok=0, unstable=None, reported=None, action="report_null",
                            note=f"design failed: {exc}"))
            continue
        y = np.array([r["y"] for r in fin], float)
        groups = np.array([r["nfl_id"] for r in fin])
        gi = names.index("moved_down")
        for tau in taus:
            rec = dict(scope=scope, tau=tau, group_coef=None, boot_se=None, ci_lo=None, ci_hi=None,
                       n_obs=len(fin), n_players=len({r["nfl_id"] for r in fin}), n_boot_ok=0,
                       unstable=None, reported=None, action="", note="")
            pnt = mf.qr_fit(y, X, tau, cfg_qr, int(g["max_iter"]))
            if pnt is None:
                rec["action"] = "refit_increased_max_iter"
                pnt = mf.qr_fit(y, X, tau, cfg_qr, int(cfg["models"]["fit_max_iter_retry"]))
            if pnt is None:
                rec["action"] = "report_null"
                rec["note"] = "no convergence after refit_increased_max_iter"
                out.append(rec)
                continue
            est = float(pnt[gi])
            rec["group_coef"] = est
            boot = mf.cluster_bootstrap(y, X, groups, gi, tau, int(g["bootstrap_reps"]),
                                        int(cfg["seed"]) + int(g["seed_offset"]), cfg_qr)
            rec.update(boot_se=boot["se"], ci_lo=boot["ci_lo"], ci_hi=boot["ci_hi"],
                       n_boot_ok=boot["n_ok"])
            if boot["se"] is not None and abs(boot["se"]) > float(g["instability_ratio"]) * abs(est):
                rec["unstable"] = True
                rec["action"] = "adjacent_tau_report" if tau < max(taus) else "report_null"
                rec["note"] = ("bootstrap unstable (boot_se > |estimate|); "
                               "reported as a NULL, never a forced number")
            else:
                rec["unstable"] = False
                rec["reported"] = est
                if dr:
                    rec["note"] = "dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dr)
            out.append(rec)
    return out, tail


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _inputs(cfg: dict, synthetic: bool, regime: str) -> dict[str, Path]:
    names = cfg["security_models"]["inputs"]
    if synthetic:
        d = fixture_dir(cfg, regime)
        return dict(
            efforts=d / names["efforts"], topk=d / names["top_k_efforts"],
            players=d / names["player_summary"], thresholds=d / names["thresholds_applied"],
            exposure=d / names["security_exposure_csv"],
            envelope=d / names["per_player_envelope"], attempts=d / names["attempt_features"],
            meta=d / names["meta"])
    game = common.resolve(cfg, "outputs", cfg["game"]["out_subdir"])
    sec = common.resolve(cfg, "outputs", cfg["security"]["out_subdir"])
    env = common.resolve(cfg, "outputs", cfg["envelope"]["out_subdir"])
    return dict(efforts=game / names["efforts"], topk=game / names["top_k_efforts"],
                players=game / names["player_summary"], thresholds=game / names["thresholds_applied"],
                exposure=sec / names["security_exposure"], envelope=env / names["per_player_envelope"],
                attempts=env / names["attempt_features"],
                meta=common.resolve(cfg, "outputs", cfg["game"]["thresholds_file"]))


def _check_thresholds(cfg: dict, synthetic: bool, path: Path, log) -> dict:
    if synthetic:
        return {}
    if not path.exists():
        record_blocker(cfg, f"thresholds missing at {path}; run `python src/00_thresholds.py "
                            f"--mode full` first", log)
        raise SystemExit(2)
    thr = json.loads(path.read_text(encoding="utf-8"))
    if str(thr.get("provenance")) != "FULL":
        record_blocker(cfg, f"thresholds provenance is '{thr.get('provenance')}', not FULL — a full "
                            f"run requires FULL (TASK.md blockers)", log)
        raise SystemExit(2)
    return thr


def run(cfg: dict, mode: str, synthetic: bool, regime: str, out_dir: Path, prov: str, log) -> dict:
    p = cfg["security_models"]["primary"]
    inp = _inputs(cfg, synthetic, regime)
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    _check_thresholds(cfg, synthetic,
                      inp["thresholds"] if synthetic
                      else common.resolve(cfg, "outputs", cfg["game"]["thresholds_file"]), log)

    exposures = load_exposures(con, inp["exposure"])
    envelope = load_envelope(con, inp["envelope"])
    psum = load_player_summary(con, inp["players"])
    topk = load_topk(con, inp["topk"])
    efforts = load_efforts(con, inp["efforts"])
    attempts = load_attempt_markers(con, inp["attempts"])
    cov = load_covariates(con, cfg, synthetic, regime)
    rel = load_reliability(cfg, synthetic, regime, log)
    log.info("%s: %d exposures, %d envelope, %d top-k, %d efforts, %d covariates",
             prov, len(exposures), len(envelope), len(topk), len(efforts), len(cov))

    # ---- group assignment (pre-Combine only) ----
    pool_rows = [dict(nfl_id=nid, position_group=ex["position_group"], edp_pre=ex["edp_pre"],
                      log_edp_pre=ex["log_edp_pre"], edp_trend=ex["edp_trend"],
                      has_trend=ex["has_trend"], has_primary=ex["has_primary"])
                 for nid, ex in sorted(exposures.items())]
    assign = sf.assign_security_groups(pool_rows, cfg)
    threshold_counts = sf.group_counts_by_threshold(pool_rows, cfg)
    amap = _assign_map(assign)
    # D40 declared sensitivity: the same pre-Combine rule at the T=5 threshold.
    alt_threshold = float(cfg["security_models"]["sort"]["sensitivity_threshold"])
    assign_t5 = sf.assign_security_groups(pool_rows, cfg, threshold=alt_threshold)

    # ---- model rows ----
    # C's continuous dose-response keeps the middle tertile; the two-group models (B, D–G) do not.
    rows_all, dropped = build_effort_rows(topk, assign, envelope, cov, psum, cfg,
                                          keep_unclassified=True)
    contrast = set(cfg["security_models"]["sort"]["groups"])
    rows = [r for r in rows_all if r["group"] in contrast]
    rows_t5, dropped_t5 = build_effort_rows(topk, assign_t5, envelope, cov, psum, cfg)
    all_rows, dropped_all = build_effort_rows(efforts, assign, envelope, cov, psum, cfg)
    # attach the per-player top-k mean (from player_summary) for the per-player variant
    for r in rows_all + all_rows:
        ps = psum.get(r["nfl_id"]) or {}
        r["topk_mean_speed"] = ps.get("topk_mean_speed")
    dropped_cov = sum(1 for nid in cov if _num(cov[nid].get("height")) is None
                      or _num(cov[nid].get("weight")) is None)
    log.info("model rows: %d in the two-group contrast, %d unclassified retained for C "
             "(dropped reasons: %s); players with a missing size covariate: %d",
             len(rows), len(rows_all) - len(rows), dropped or "none", dropped_cov)

    b_rows, b_means, _ = run_b(rows, cov, rel, cfg, dropped, alt_rows=rows_t5)
    c_end, c_exc = run_c(rows_all, topk, envelope, assign, cfg)
    d_rows = run_d(rows, exposures, cfg)
    pool = build_selection_pool(exposures, envelope, cov, set(psum.keys()), cfg)
    e_sel, e_ipw, e_meta = run_e(pool, rows, cfg)
    top_keys = {(int(e["nfl_id"]), int(e["game_id"]), int(e["play_id"])) for e in topk}
    f_rows = run_f(rows, all_rows, top_keys, attempts, cfg)
    g_rows, g_tail = run_g(rows, cfg)

    # ---- ground truth for the synthetic fixture only (never a result) ----
    ground = None
    meta_path = fixture_dir(cfg, regime) / cfg["security_models"]["inputs"]["meta"]
    if synthetic and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ground = dict(regime=meta.get("regime"), delta=meta.get("design", {}).get("delta"),
                          dose_gamma=meta.get("design", {}).get("dose_gamma"),
                          base_gap_log=meta.get("design", {}).get("base_gap_log"),
                          n_wr=meta.get("design", {}).get("n_wr"),
                          n_te=meta.get("design", {}).get("n_te"),
                          note="SYNTHETIC FIXTURE ground truth — not a finding")
        except (ValueError, OSError):
            ground = None

    # ---- persist ----
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit(out_dir / "group_assignment.csv", assign["assignments"], prov, log)
    _emit(out_dir / "b_primary.csv", b_rows, prov, log)
    _emit(out_dir / "b_group_means.csv", b_means, prov, log)
    _emit(out_dir / "c_end.csv", c_end, prov, log)
    _emit(out_dir / "c_exceedance.csv", c_exc, prov, log)
    _emit(out_dir / "d_skippers.csv", d_rows, prov, log)
    _emit(out_dir / "e_selection.csv", e_sel, prov, log)
    _emit(out_dir / "e_ipw.csv", e_ipw, prov, log)
    _emit(out_dir / "falsification.csv", f_rows, prov, log)
    _emit(out_dir / "g_tail.csv", g_rows, prov, log)
    _emit(out_dir / "g_tail_summary.csv", g_tail, prov, log)

    primary_wr = next((r for r in b_rows if r["scope"] == "WR"
                       and r["spec"] == "primary_env_vmax"), {})
    group_totals = {g: sum(1 for a in assign["assignments"] if a["group"] == g)
                    for g in cfg["security_models"]["sort"]["groups"]}
    min_group_n = int(cfg["security_models"]["sort"]["min_group_n"])
    thin_groups = sorted(g for g, n in group_totals.items() if n < min_group_n)
    summary = dict(
        provenance=prov, mode=mode, synthetic=synthetic, regime=regime,
        sign_convention=("a NEGATIVE group_coef means moved_down sits further below its ceiling "
                         "(larger gap) = AGAINST the incentive hypothesis; the hypothesis predicts "
                         "POSITIVE"),
        analysis_a_status=("RETIRED as a gate by D38; appendix-only (see outputs/security/"
                           "analysis_a_attenuation.csv)"),
        n_exposures=len(exposures), n_with_combine_speed=len(envelope),
        n_players_in_game=len(psum), n_effort_rows=len(rows), n_model_players=len(
            {r["nfl_id"] for r in rows}),
        n_effort_rows_c=len(rows_all),
        n_model_players_c=len({r["nfl_id"] for r in rows_all}),
        n_unclassified_rows_c=sum(1 for r in rows_all if r["group"] ==
                                  str(cfg["security_models"]["sort"]["unclassified_label"])),
        n_unclassified_players_c=len({r["nfl_id"] for r in rows_all
                                      if r["group"] ==
                                      str(cfg["security_models"]["sort"]["unclassified_label"])}),
        n_all_effort_rows=len(all_rows),
        n_topk_rows=len(topk), n_efforts_tracked=len(efforts),
        dropped_reasons=dropped, dropped_reasons_all_efforts=dropped_all,
        dropped_reasons_threshold_T5=dropped_t5,
        n_missing_size_covariate_players=dropped_cov,
        sort=dict(fall_threshold=assign["fall_threshold"],
                  sensitivity_threshold=alt_threshold,
                  group_semantics=("moved_down = edp_trend >= fall_threshold (fell 3+ EDP spots); "
                                   "high_secure = not falling 3+ (stable or rising)"),
                  level_tertiles=assign["level_tertiles"],
                  cutoffs=assign["cutoffs"], counts=assign["counts"],
                  group_totals=group_totals, min_group_n=min_group_n,
                  thin_groups=thin_groups,
                  by_position=assign["by_position"],
                  by_position_threshold_T5=assign_t5["by_position"],
                  counts_by_threshold=threshold_counts),
        reliability=rel,
        primary_wr=primary_wr,
        b_primary=b_rows, b_group_means=b_means,
        c_end=c_end, c_exceedance=c_exc, d_skippers=d_rows,
        e_selection=e_sel, e_ipw=e_ipw, e_meta=e_meta,
        falsification=f_rows, g_tail=g_rows, g_tail_summary=g_tail,
        ground_truth=ground,
        figures_note="no figures written by this stage; the notebook renders the security figure",
    )
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True,
                                                     default=str), encoding="utf-8")
    (out_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- #
# SUMMARY renderer
# --------------------------------------------------------------------------- #
def _fmt(v: Any) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def render_summary(s: dict) -> str:
    L: list[str] = [f"# Phase 5 B–G — Security / incentive-exposure models ({s['provenance']})", ""]
    if s.get("synthetic"):
        L.append("> **SYNTHETIC FIXTURE (not a finding).** Every number below is generated by "
                 "`src/synthetic_security_game_fixture.py`; it proves the plumbing, nothing about "
                 "the NFL.")
        L.append("")
    if s.get("mode") == "sample" and not s.get("synthetic"):
        L.append(f"> **{PENDING} / UNVALIDATED SAMPLE OUTPUT** — no real game-side model has run.")
        L.append("")
    L.append(f"- **mode:** `{s['mode']}` · **regime:** `{s['regime']}` · **synthetic:** "
             f"{s['synthetic']}")
    L.append(f"- **Analysis A:** {s['analysis_a_status']}")
    L.append(f"- **Sign convention:** {s['sign_convention']}.")
    L.append(f"- **Population:** exposures {s['n_exposures']} · with Combine speed "
             f"{s['n_with_combine_speed']} · in game data {s['n_players_in_game']} · "
             f"two-group effort rows {s['n_effort_rows']} ({s['n_model_players']} players) · "
             f"C continuous rows {s['n_effort_rows_c']} ({s['n_model_players_c']} players; "
             f"{s['n_unclassified_players_c']} unclassified retained: "
             f"{s['n_unclassified_rows_c']} efforts)")
    L.append("")
    # group assignment
    L.append("## Group assignment (pre-Combine sort key)")
    L.append(f"- **D40 asymmetric threshold:** `moved_down` iff `edp_trend >= "
             f"+{s['sort']['fall_threshold']:g}` EDP spots (fell 3+ between the 2-week and 1-week "
             f"pre-Combine snapshots).")
    L.append(f"- **`high_secure` = not falling 3+ (stable or rising)** — it is deliberately *not* a "
             f"\"low EDP\" or \"no movement\" group.")
    L.append(f"- Declared sensitivity: the same rule at +{s['sort']['sensitivity_threshold']:g} "
             f"(`panel_threshold_T5`). Level tertiles {s['sort']['level_tertiles']} "
             f"(within position group) for players with no 2-week snapshot.")
    if s["sort"]["cutoffs"]:
        L.append("")
        L.append("| position_group | q_lo | q_hi | n (level stratum) |")
        L.append("|---|---:|---:|---:|")
        for pos, c in s["sort"]["cutoffs"].items():
            L.append(f"| {pos} | {c['q_lo']:.4f} | {c['q_hi']:.4f} | {c['n']} |")
    L.append("")
    L.append("| stratum | group | n |")
    L.append("|---|---|---:|")
    for stratum in sorted(s["sort"]["counts"]):
        for group in sorted(s["sort"]["counts"][stratum]):
            L.append(f"| {stratum} | {group} | {s['sort']['counts'][stratum][group]} |")
    L.append("")
    cbt = s["sort"].get("counts_by_threshold") or []
    if cbt:
        keys = sorted({k for r in cbt for k in r if ":" in k})
        L.append("| threshold | panel | " + " | ".join(keys) + " |")
        L.append("|---:|---|---:|" if len(keys) == 1 else
                 "|---:|---|" + "|".join("---:" for _ in keys) + "|")
        for r in cbt:
            L.append(f"| +{r['threshold']:g} | {r['panel']} | "
                     + " | ".join(str(r.get(k, "—")) for k in keys) + " |")
        L.append("")
    if s["sort"].get("thin_groups"):
        L.append(f"> ⚠ **THIN GROUP(S):** {', '.join(s['sort']['thin_groups'])} below "
                 f"`min_group_n={s['sort']['min_group_n']}` — the two-group contrast is "
                 f"underpowered; the affected rows are flagged `thin=True` and are not fitted.")
        L.append("")
    else:
        L.append(f"Both contrast groups clear `min_group_n={s['sort']['min_group_n']}` "
                 f"(totals: {', '.join(f'{g}={n}' for g, n in s['sort']['group_totals'].items())}).")
        L.append("")
    L.append("Post-Combine EDP / pre→post change are **never** used for assignment or any primary "
             "model (runtime assertion `security_features.assert_pre_combine_only`).")
    L.append("")
    # B
    L.append("## B — primary effort-level model (player-clustered SEs)")
    L.append("| scope | spec | n_obs | n_players | n_md | n_hs | thin | group_coef | group_se | group_p | "
             "group_ci_lo | group_ci_hi | speed_coef | group_coef_corrected | ICC |")
    L.append("|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in s["b_primary"]:
        L.append(f"| {r['scope']} | {r['spec']} | {r['n_obs']} | {r['n_players']} | "
                 f"{r.get('n_players_moved_down', '—')} | {r.get('n_players_high_secure', '—')} | "
                 f"{'yes' if r.get('thin') else 'no'} | "
                 f"{_fmt(r['group_coef'])} | {_fmt(r['group_se'])} | {_fmt(r['group_p'])} | "
                 f"{_fmt(r['group_ci_lo'])} | {_fmt(r['group_ci_hi'])} | "
                 f"{_fmt(r['speed_coef'])} | {_fmt(r['group_coef_corrected'])} | "
                 f"{_fmt(r['reliability_icc'])} |")
    L.append("")
    L.append("`group_coef_corrected` is the Frisch–Waugh + ICC moment-matrix correction with a "
             "player-clustered bootstrap SE; the naive estimate is never replaced. A positive "
             "`group_coef` = moved_down has a **smaller** gap (supports the incentive hypothesis).")
    L.append("The headline is the **pooled two-group split with a stratum fixed effect** "
             "(`stratum_fe`); `panel_trend_only` and `panel_level_fallback` are the pre-declared "
             "one-stratum sensitivity panels and `panel_threshold_T5` re-runs the primary spec at "
             "the declared +5 threshold (a panel below `min_group_n` is reported as a null).")
    L.append("")
    L.append("### group means (top-k efforts)")
    L.append("| scope | group | n_obs | n_players | mean log game speed | mean log ceiling | "
             "mean gap | mean utilization |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in s["b_group_means"]:
        L.append(f"| {r['scope']} | {r['group']} | {r['n_obs']} | {r['n_players']} | "
                 f"{_fmt(r['mean_log_game_speed'])} | {_fmt(r['mean_log_ceiling'])} | "
                 f"{_fmt(r['mean_gap'])} | {_fmt(r['mean_utilization'])} |")
    L.append("")
    # C
    L.append("## C — which end drives it (piecewise log-EDP) + exceedance")
    L.append("**Overall exceedance first** (share of top-k efforts beating the ceiling, Wilson CI):")
    L.append("")
    L.append("| scope | group | n_efforts | n_exceed | rate | wilson_lo | wilson_hi |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for r in s["c_exceedance"]:
        L.append(f"| {r['scope']} | {r['group']} | {r['n_efforts']} | {r['n_exceed']} | "
                 f"{_fmt(r['exceedance_rate'])} | {_fmt(r['wilson_lo'])} | {_fmt(r['wilson_hi'])} |")
    L.append("")
    L.append("| scope | percentile | basis | fitted gap | se | ci_lo | ci_hi | n_obs |")
    L.append("|---|---:|---|---:|---:|---:|---:|---:|")
    for r in s["c_end"]:
        L.append(f"| {r['scope']} | {_fmt(r['percentile'])} | {r['basis']} | "
                 f"{_fmt(r['fitted_gap'])} | {_fmt(r['se'])} | {_fmt(r['ci_lo'])} | "
                 f"{_fmt(r['ci_hi'])} | {r['n_obs']} |")
    L.append("")
    # D
    L.append("## D — skippers (exploratory)")
    for r in s["d_skippers"]:
        L.append(f"- n = {r.get('n')} · mean residual = {_fmt(r.get('mean_residual'))} · "
                 f"CI [{_fmt(r.get('ci_lo'))}, {_fmt(r.get('ci_hi'))}] · "
                 f"observed {_fmt(r.get('observed_mean'))} vs predicted "
                 f"{_fmt(r.get('predicted_mean'))} · {r.get('note', '')}")
    L.append("")
    # E
    L.append("## E — selection model + IPW")
    L.append(f"- pool {s['e_meta']['n_pool']} · appeared {s['e_meta']['n_appeared']}")
    L.append("")
    L.append("| term | coef | se | p | odds_ratio | or_ci_lo | or_ci_hi |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in s["e_selection"]:
        L.append(f"| {r['term']} | {_fmt(r['coef'])} | {_fmt(r['se'])} | {_fmt(r['p'])} | "
                 f"{_fmt(r['odds_ratio'])} | {_fmt(r['or_ci_lo'])} | {_fmt(r['or_ci_hi'])} |")
    L.append("")
    L.append("| weighting | group_coef | group_se | group_p | n_obs | trim |")
    L.append("|---|---:|---:|---:|---:|---|")
    for r in s["e_ipw"]:
        L.append(f"| {r['weighting']} | {_fmt(r['group_coef'])} | {_fmt(r['group_se'])} | "
                 f"{_fmt(r['group_p'])} | {r['n_obs']} | "
                 f"{_fmt(r['trim_lo'])}–{_fmt(r['trim_hi'])} |")
    L.append("")
    # F
    L.append("## F — falsification")
    L.append("| test | stratum | term | coef | se | p | n_obs | expectation |")
    L.append("|---|---|---|---:|---:|---:|---:|---|")
    for r in s["falsification"]:
        L.append(f"| {r['test']} | {r['stratum']} | {r['term']} | {_fmt(r['coef'])} | "
                 f"{_fmt(r['se'])} | {_fmt(r['p'])} | {r['n_obs']} | {r['expectation']} |")
    L.append("")
    # G
    L.append("## G — ceiling / tail quantile regressions")
    L.append("| scope | τ | group_coef | boot_se | ci_lo | ci_hi | reported | unstable | action |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for r in s["g_tail"]:
        L.append(f"| {r['scope']} | {_fmt(r['tau'])} | {_fmt(r['group_coef'])} | "
                 f"{_fmt(r['boot_se'])} | {_fmt(r['ci_lo'])} | {_fmt(r['ci_hi'])} | "
                 f"{_fmt(r['reported'])} | {r['unstable']} | {r['action']} |")
    L.append("")
    if s["ground_truth"]:
        g = s["ground_truth"]
        L.append("## Fixture ground truth (NOT a finding)")
        L.append(f"- regime `{g['regime']}` · planted moved_down advantage δ = {_fmt(g['delta'])} "
                 f"(log units) · dose γ = {_fmt(g['dose_gamma'])} · base gap = "
                 f"{_fmt(g['base_gap_log'])} · n_wr = {g['n_wr']} · n_te = {g['n_te']}")
        L.append("")
    L.append("## Caveats")
    L.append("- Post-Combine EDP and the pre→post change are outcomes of the Combine and are never "
             "exposures; they are not read by the assignment or any model here.")
    L.append("- Claims are limited to players who reached the NFL (game tracking exists only for "
             "them); selection on being drafted is addressed in E and `notes/limitations.md`.")
    L.append("- The attenuation correction is an approximation that depends on the pre-declared ICC; "
             "both naive and corrected estimates are reported.")
    L.append("")
    return "\n".join(L)


def sample_placeholder(cfg: dict, prov: str, out_dir: Path, log) -> None:
    note = ("game-side security models need the full game-tracking files and FULL-provenance "
            "thresholds; the 2023 sample is a non-representative prefix and the Pi sample is not "
            "the analysis population")
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit(out_dir / "security_models_summary.csv",
          [dict(stage="07_security_models", status=PENDING, dataset="sample", note=note)], prov, log)
    (out_dir / "SUMMARY.md").write_text(
        f"# Phase 5 B–G — Security / incentive-exposure models ({prov})\n\n"
        f"- **status:** `{PENDING}` — {note}\n"
        f"- **Analysis A:** retired as a gate by D38; appendix-only "
        f"(`outputs/security/analysis_a_attenuation.csv`).\n", encoding="utf-8")


def _done_ok(done: Path, mode: str, prov: str, synthetic: bool, regime: str) -> bool:
    """Checkpoint hit only on an exact mode/provenance/synthetic/regime marker match."""
    return common.checkpoint_match(common.read_done_marker(done), mode=mode, provenance=prov,
                                   synthetic=synthetic, regime=regime)


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 Phase 5 B–G — security / incentive-exposure models")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild")
    ap.add_argument("--synthetic", action="store_true",
                    help="run the identical path on the committed game-side fixture (not a finding)")
    ap.add_argument("--synthetic-regime", choices=["planted", "null", "selection"], default="planted")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()

    gate.enforce(cfg, mode, "07_security_models", requires_game_side=True, logger=log)

    regime = args.synthetic_regime
    prov = PROV_SYNTH if args.synthetic else common.provenance(cfg, mode)
    out_dir = stage_dir(cfg, args.synthetic, regime)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = f"07_security_models[{mode}{'-synthetic-' + regime if args.synthetic else ''}]"

    done = out_dir / "_DONE.json"
    if _done_ok(done, mode, prov, args.synthetic, regime) and not args.force:
        log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
        common.append_run_log(cfg, stage, mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
        return
    if done.exists() and not args.force:
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding",
                 done.name, common.checkpoint_mismatch_note(
                     common.read_done_marker(done), mode=mode, provenance=prov,
                     synthetic=args.synthetic, regime=regime))

    if mode == "sample" and not args.synthetic:
        with common.timed_stage(cfg, stage, mode, synthetic=False):
            sample_placeholder(cfg, prov, out_dir, log)
    else:
        with common.timed_stage(cfg, stage, mode, synthetic=args.synthetic):
            run(cfg, "synthetic" if args.synthetic else mode, args.synthetic, regime, out_dir,
                prov, log)

    done.write_text(json.dumps({"stage": "07_security_models", "mode": mode, "provenance": prov,
                                "synthetic": bool(args.synthetic), "regime": regime}, indent=2),
                    encoding="utf-8")
    log.info("Phase 5 B–G security models complete -> %s", out_dir)


if __name__ == "__main__":
    main()
