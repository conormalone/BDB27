#!/usr/bin/env python3
"""05_validate — Phase 5 validation of the burst-vs-jump decomposition.

Implements the five checks in ``TASK.md`` §5 ("Validation"), and nothing else:

1. **Permutation** — shuffle Combine physical time across players *within
   position* (the in-game position strata are EDGE / INTERIOR_LINE, the only
   position labels the release carries); the explained-variance share should fall
   to near zero.
2. **Placebo** — height and weight alone, after controlling for pre-snap depth,
   should not reproduce the physical-time effect. The same decomposition is
   fitted with a height/weight pseudo-phys and its share is reported beside the
   real one.
3. **Mechanical checks** — re-run with the depth effect removed, with the
   alignment effect removed, and with both removed; sweep the Combine onset
   threshold over a config grid and report the sensitivity of the shares.
   Stance-specific subsets are **not possible**: no stance label exists (B8).
4. **Snap quality** — restrict to snaps with clean onset detection; report the
   share dropped.
5. **Selection** — only drafted DL appear. Snap counts by player are reported
   and the decomposition is re-fitted with equal total weight per player.
   Draftee selection itself **cannot be tested** on this release.

Every check carries a synthetic world in which it *should* fire, and the result
of that world is written to the artefact (``synthetic_can_fail``). A check that
cannot fail is worse than no check: it manufactures confidence.

Honesty rules enforced in code
------------------------------
* Any quantity that cannot be computed is written as ``INSUFFICIENT_DATA`` with
  a reason, never as a zero placeholder.
* The artefact is stamped by :func:`common.write_json` (``PLACEHOLDER_SAMPLE:
  UNVALIDATED SAMPLE OUTPUT`` in sample mode) and carries its own provenance
  field, which the notebook reads from the artefact.
* A missing input raises :class:`common.GateRefused` **before** anything is
  written, so a refusal can never leave a file pretending success.
* No Phase 4 model semantics are changed. The validation adds optional
  ``weights`` / ``include_depth`` / ``include_alignment`` switches to
  ``models.decompose`` whose defaults reproduce the Phase 4 fit exactly.
* No pressure field (D112), no blocker term (D98), no stance term (B8), no
  offset of any kind (D110). ``player_get_off`` is not read.

Determinism
-----------
Every random draw comes from ``numpy.random.default_rng`` seeded from
``bdb27.permutation.seed`` (permutation) and ``bdb27.validation.synthetic_seed``
(synthetic worlds). The EM estimator has no random initialisation. Two runs on
the same inputs and config produce byte-identical artefacts.
"""

from __future__ import annotations

import copy
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import models as M  # noqa: E402
import stats as ST  # noqa: E402

STAGE = "05_validate"

#: Columns carried from the wide (label-side) snap query for the snap-quality
#: check. They are deliberately not part of the Phase 4 model frame.
QC_COLUMNS = ["game_id", "play_id", "nfl_id", "onset_status", "cross_status",
              "cross_gap_policy", "reaction_ready", "analysis_ready",
              "in_primary_set"]

BRIEF_FIELDS = (
    "status", "n_reps", "n_players", "n_team_games", "b", "b_fit",
    "share_phys", "share_u", "sigma_u2", "sigma_u2_fit", "sigma_v2",
    "sigma_e2", "between_phys_var", "between_player_phys_var_observed",
    "shared_me_var", "jump_sd_ms", "within_player_remainder_total",
    "share_phys_total", "share_u_total", "converged", "iters", "delta",
    "weighting", "include_depth", "include_alignment",
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _load_stage_module(name: str):
    """Import ``src/<name>.py`` fresh (filenames start with digits)."""
    path = _SRC / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"bdb27_05_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _f(value, ndigits: int | None = None):
    """JSON-safe float (``None`` for a non-finite value — never a zero fill)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return round(out, ndigits) if ndigits is not None else out


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _brief(result: dict | None) -> dict:
    """The JSON-safe summary of one decomposition fit."""
    if not isinstance(result, dict):
        return {"status": "NOT_ESTIMABLE"}
    out = {}
    for key in BRIEF_FIELDS:
        if key not in result:
            continue
        value = result[key]
        if key == "status":
            out[key] = str(value) if value is not None else "OK"
        elif isinstance(value, (bool, np.bool_)):
            out[key] = bool(value)
        elif isinstance(value, (int, np.integer)):
            out[key] = int(value)
        elif key in ("weighting",):
            out[key] = value
        elif key in ("include_depth", "include_alignment"):
            out[key] = bool(value)
        else:
            out[key] = _f(value)
    out.setdefault("status", "OK" if result.get("status") is None else str(result["status"]))
    if out.get("status") == "OK":
        boundary = {
            "sigma_u_zero": bool(_finite(out.get("sigma_u2"))
                                 and float(out["sigma_u2"]) <= 0.0),
            "between_phys_var_zero": bool(_finite(out.get("between_phys_var"))
                                          and float(out["between_phys_var"]) <= 0.0),
        }
        out["boundary"] = boundary
    return out


def _refit(df: pd.DataFrame, cfg, **kwargs) -> dict:
    """Fit the Phase 4 decomposition on ``df`` with Phase 4's configured stops."""
    mc = C.section(cfg, "models")
    try:
        return M.decompose(
            df,
            correct_measurement_error=True,
            population_degree=int(mc["population_curve_degree"]),
            max_iter=int(mc["max_iter"]),
            tol=float(mc["tol"]),
            **kwargs,
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        return {"status": "NOT_ESTIMABLE", "reason": f"{type(exc).__name__}: {exc}"}


def _percentiles(values: list[float]) -> dict:
    vals = np.asarray([v for v in values if _finite(v)], dtype=float)
    if len(vals) == 0:
        return {"n": 0}
    return {
        "n": int(len(vals)),
        "mean": _f(np.mean(vals)),
        "sd": _f(np.std(vals, ddof=1)) if len(vals) > 1 else None,
        "min": _f(np.min(vals)),
        "p2_5": _f(np.percentile(vals, 2.5)),
        "p50": _f(np.percentile(vals, 50)),
        "p97_5": _f(np.percentile(vals, 97.5)),
        "max": _f(np.max(vals)),
    }


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #
def load_frames(cfg, mode: str) -> dict:
    """Load the Phase 2 curves, the Phase 3 snaps and the wide QC frame.

    Raises :class:`common.GateRefused` when a required input is absent. Called
    before any output directory is created.
    """
    p = C.paths(cfg, mode)
    phase4 = p["phase4"] / "decomposition.json"
    phys_path = p["phase2"] / "physical_time_by_player.csv"
    snaps_path = p["outputs"] / "phase3" / "snaps.parquet"
    combine = C.source_path(cfg, "combine_results")[0]

    missing = [str(path) for path in (phase4, phys_path, snaps_path, combine)
               if not Path(path).exists()]
    if missing:
        raise C.GateRefused(
            f"{STAGE}: refusing to run — required input(s) absent:\n  "
            + "\n  ".join(missing)
            + "\nPhase 5 validates the Phase 2/3/4 artefacts; it does not "
              "invent them and it writes nothing when they are missing.")

    m04 = _load_stage_module("04_models")
    curves = m04.load_physical_curves(phys_path)
    con = C.duckdb_connect(cfg)
    try:
        # The QC frame is a label-side view of the same parquet. Check its
        # schema first: a missing column is a Phase 3 schema mismatch and is
        # refused by name here, before any output directory exists.
        have = set(con.execute(
            f"DESCRIBE SELECT * FROM '{snaps_path.as_posix()}'").df()["column_name"])
        absent = [c for c in QC_COLUMNS if c not in have]
        if absent:
            raise C.GateRefused(
                f"{STAGE}: refusing to run — {snaps_path.name} lacks "
                f"{', '.join(absent)}, which the snap-quality check reads. "
                "Rebuild 03_game_snaps; this stage writes nothing.")
        # ``play_id`` is carried so the QC merge has a per-rep key. It is not a
        # model column: the Phase 4 design reads only its named columns.
        snaps = m04.load_snaps(con, cfg, mode, extra_columns=("play_id",))
        wide = con.execute(
            f"SELECT {', '.join(QC_COLUMNS)} FROM '{snaps_path.as_posix()}' "
            "WHERE in_primary_set").df()
    finally:
        con.close()
    df, status = m04.attach_physical(snaps, curves)
    return {"phase4_path": phase4, "phys_path": phys_path,
            "snaps_path": snaps_path, "combine_path": combine,
            "curves": curves, "snaps": snaps, "wide": wide,
            "df": df, "phys_status": status, "m04": m04}


def load_height_weight(cfg) -> pd.DataFrame:
    """Combine height (inches) and weight (lb), one row per player.

    Returns an empty frame when the Combine results file lacks one of the
    columns. The placebo check then reports ``NOT RUN — <field> absent``; it
    never treats a missing column as a zero.
    """
    path = C.source_path(cfg, "combine_results")[0]
    wanted = ["nfl_id", "draft_year", "combine_height", "combine_weight"]
    try:
        hw = pd.read_csv(path, usecols=wanted)
    except (ValueError, KeyError):
        return pd.DataFrame(columns=wanted)
    hw = hw.sort_values(["nfl_id", "draft_year"], kind="mergesort")
    hw = hw.drop_duplicates("nfl_id", keep="first")
    hw["nfl_id"] = hw["nfl_id"].astype("int64")
    return hw.set_index("nfl_id")


def _apply_curve_mapping(df: pd.DataFrame, curves: dict, mapping: dict) -> pd.DataFrame:
    """Replace each rep's physical time with the curve of its mapped donor."""
    out = df.copy()
    phys = np.full(len(out), np.nan)
    se = np.full(len(out), np.nan)
    depth = out["depth_yd"].to_numpy(dtype=float)
    players = out["player"].to_numpy()
    for pid in np.unique(players):
        mask = players == pid
        donor = curves.get(int(mapping.get(int(pid), pid)))
        if donor is None:
            continue
        d = depth[mask]
        clipped = np.clip(d, donor["d"][0], donor["d"][-1])
        phys[mask] = np.interp(clipped, donor["d"], donor["m"])
        se[mask] = np.interp(clipped, donor["d"], donor["s"])
    out["phys_s"] = phys
    out["phys_se_s"] = se
    return out[np.isfinite(phys) & np.isfinite(se)].copy()


def _curves_from_player_rows(rows: list[dict]) -> dict[int, dict]:
    """Build the Phase 2 curve dict from ``pool_players`` output rows.

    Same rules as ``04_models.load_physical_curves``: pooled rows only, a
    ``(0 s, 0 yd)`` anchor so depths below the first grid point interpolate
    rather than extrapolate, duplicate depths averaged.
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    df = df[df["pooled"].astype(str).str.lower().isin(("true", "1"))]
    df = df[df["posterior_mean_s"].notna() & df["posterior_sd_s"].notna()]
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
        if d[0] > 0:
            d = np.r_[0.0, d]
            m = np.r_[0.0, m]
            s = np.r_[0.0, s]
        uniq, inv = np.unique(d, return_inverse=True)
        if len(uniq) != len(d):
            m = np.bincount(inv, weights=m) / np.bincount(inv)
            s = np.bincount(inv, weights=s) / np.bincount(inv)
            d = uniq
        curves[int(pid)] = {"d": d, "m": m, "s": s, "max_depth": float(d[-1])}
    return curves


def _dominant_alignment(df: pd.DataFrame) -> dict[int, str]:
    """Modal in-game position per player (the permutation strata)."""
    out = {}
    for pid, sub in df.groupby("player", sort=True):
        counts = sub["alignment_class"].astype(str).value_counts()
        out[int(pid)] = str(counts.index[0]) if len(counts) else "UNKNOWN"
    return out


# --------------------------------------------------------------------------- #
# 1. Permutation
# --------------------------------------------------------------------------- #
def permutation_check(cfg, df: pd.DataFrame, curves: dict, *, base: dict,
                      n_permutations: int, seed: int) -> dict:
    """Shuffle phys across players within position and re-fit the decomposition."""
    players = sorted(int(x) for x in df["player"].unique())
    align = _dominant_alignment(df)
    strata: dict[str, list[int]] = {}
    for pid in players:
        strata.setdefault(align[pid], []).append(pid)
    mixed = int((df.groupby("player")["alignment_class"].nunique() > 1).sum())

    observed = _brief(base)
    if observed.get("status") != "OK":
        return {"status": "INSUFFICIENT_DATA",
                "reason": f"base fit is {observed.get('status')}",
                "observed": observed}

    rng = np.random.default_rng(int(seed))
    shares_phys, shares_u, bs = [], [], []
    n_ok = n_skipped = n_failed = 0
    for _ in range(int(n_permutations)):
        mapping: dict[int, int] = {}
        for plist in strata.values():
            donors = [int(x) for x in rng.permutation(plist)]
            for pid, donor in zip(plist, donors):
                mapping[pid] = donor
        if all(pid == donor for pid, donor in mapping.items()):
            n_skipped += 1
            continue
        permuted = _apply_curve_mapping(df, curves, mapping)
        res = _refit(permuted, cfg)
        if res.get("status") != "OK":
            n_failed += 1
            continue
        n_ok += 1
        shares_phys.append(res.get("share_phys"))
        shares_u.append(res.get("share_u"))
        bs.append(res.get("b"))

    obs_phys = observed.get("share_phys")
    dist = _percentiles(shares_phys)
    exceeds = None
    percentile = None
    p_one_sided = None
    if dist.get("n", 0) > 0 and _finite(obs_phys):
        vals = np.asarray([v for v in shares_phys if _finite(v)], dtype=float)
        exceeds = bool(float(obs_phys) > float(np.max(vals)))
        percentile = _f(100.0 * float(np.mean(vals <= float(obs_phys))))
        p_one_sided = _f((1.0 + float(np.sum(vals >= float(obs_phys))))
                         / (len(vals) + 1.0))

    if dist.get("n", 0) == 0:
        status = "INSUFFICIENT_DATA"
        reading = ("No permutation fit completed, so the permutation "
                   "distribution is absent. It is not reported as zero.")
    elif not _finite(obs_phys):
        status = "INSUFFICIENT_DATA"
        reading = "The observed share is absent; no comparison is possible."
    else:
        status = "OK"
        if float(obs_phys) <= 0.0:
            reading = (
                "The observed physical share is at the zero boundary, so it "
                "cannot fall further. The permutation distribution is a check on "
                "a signal this data does not show at all; it is reported as "
                "uninformative on this data, not as a pass. The synthetic world "
                "below shows the same code detecting a real effect it destroys.")
        elif exceeds:
            reading = ("The observed share exceeds every permutation draw: the "
                       "physical-time signal is not an artefact of the "
                       "decomposition. This is the expected outcome when the "
                       "physical term carries real between-player signal.")
        elif dist.get("p97_5") is not None and float(obs_phys) > float(dist["p97_5"]):
            reading = ("The observed share lies above the 97.5th percentile of "
                       "the permutation distribution.")
        else:
            reading = ("The observed share is inside the permutation "
                       "distribution: the physical-time term is not distinguishable "
                       "from a shuffled assignment on this data. Reported as it is.")

    return {
        "status": status,
        "question": ("does shuffling Combine physical time across players "
                     "within position remove the explained-variance share?"),
        "position_strata": {k: len(v) for k, v in sorted(strata.items())},
        "n_players_with_mixed_alignment": mixed,
        "observed": observed,
        "n_requested": int(n_permutations),
        "n_ok": n_ok,
        "n_skipped_identity": n_skipped,
        "n_failed": n_failed,
        "permutation_share_phys": dist,
        "permutation_share_u": _percentiles(shares_u),
        "permutation_b": _percentiles(bs),
        "observed_share_phys_percentile": percentile,
        "observed_exceeds_permutation_max": exceeds,
        "one_sided_monte_carlo_p": p_one_sided,
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# 2. Placebo
# --------------------------------------------------------------------------- #
def placebo_check(cfg, df: pd.DataFrame, *, base: dict) -> dict:
    """Height and weight alone, after controlling for pre-snap depth."""
    players = sorted(int(x) for x in df["player"].unique())
    hw = load_height_weight(cfg)
    if hw.empty or not {"combine_height", "combine_weight"} <= set(hw.columns):
        return {
            "status": "NOT RUN",
            "reason": ("NOT RUN — combine_height / combine_weight absent: the "
                       "Combine results file does not carry both columns, so "
                       "the height/weight pseudo-phys cannot be built. No "
                       "share is substituted."),
            "question": ("do Combine height and weight alone reproduce the "
                         "physical-time effect after controlling for pre-snap "
                         "depth?"),
        }
    rows = []
    for pid in players:
        if pid not in hw.index:
            continue
        h = hw.loc[pid, "combine_height"]
        w = hw.loc[pid, "combine_weight"]
        if _finite(h) and _finite(w):
            rows.append((pid, float(h), float(w)))
    if len(rows) < 3:
        return {"status": "INSUFFICIENT_DATA",
                "reason": (f"only {len(rows)} of {len(players)} fitted players "
                           "have a finite Combine height and weight")}

    hwdf = pd.DataFrame(rows, columns=["player", "height_in", "weight_lb"])
    mc = C.section(cfg, "models")
    mu = M.population_phys_curve(
        df["depth_yd"].to_numpy(dtype=float),
        df["phys_s"].to_numpy(dtype=float),
        int(mc["population_curve_degree"]))
    work = df.copy()
    work["phys_dev"] = work["phys_s"].to_numpy(dtype=float) - mu
    pdev = (work.groupby("player", sort=True)["phys_dev"].mean()
            .rename("phys_dev").reset_index())
    m = pdev.merge(hwdf, on="player", how="inner")
    if len(m) < 3 or float(np.ptp(m["phys_dev"])) <= 0:
        return {"status": "INSUFFICIENT_DATA",
                "reason": "player-level physical deviation has no spread"}

    hs = (m["height_in"] - m["height_in"].mean()) / m["height_in"].std(ddof=0)
    ws = (m["weight_lb"] - m["weight_lb"].mean()) / m["weight_lb"].std(ddof=0)
    X = np.column_stack([np.ones(len(m)), hs.to_numpy(), ws.to_numpy()])
    beta, *_ = np.linalg.lstsq(X, m["phys_dev"].to_numpy(dtype=float), rcond=None)
    fitted = X @ beta
    ss_res = float(np.sum((m["phys_dev"] - fitted) ** 2))
    ss_tot = float(np.sum((m["phys_dev"] - m["phys_dev"].mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None

    pseudo = dict(zip(m["player"].astype(int), fitted))
    work["phys_s"] = mu + work["player"].map(pseudo).astype(float)
    # The pseudo physical time has no measurement-error SE of its own: the
    # height/weight regression residual is not a Phase 2 posterior SD. The same
    # decomposition is fitted with the mean Phase 2 SE so the placebo is not
    # helped or hurt by the calibration step.
    work["phys_se_s"] = float(df["phys_se_s"].mean())
    work = work[np.isfinite(work["phys_s"])].copy()
    placebo = _refit(work, cfg)

    real = _brief(base)
    plac = _brief(placebo)
    return {
        "status": "OK" if plac.get("status") == "OK" else "INSUFFICIENT_DATA",
        "question": ("do Combine height and weight alone reproduce the "
                     "physical-time effect after controlling for pre-snap depth?"),
        "n_players": int(len(m)),
        "missing_height_weight": [int(p) for p in players
                                  if p not in set(m["player"].astype(int))],
        "height_weight_regression": {
            "response": "player mean physical time net of the population depth curve",
            "predictors": ["intercept", "height (standardised, in)",
                           "weight (standardised, lb)"],
            "coefficients": {"intercept": _f(beta[0]),
                             "height_per_sd_s": _f(beta[1]),
                             "weight_per_sd_s": _f(beta[2])},
            "r2": _f(r2),
        },
        "corr_phys_dev_height": _f(ST.pearson(
            m["phys_dev"].tolist(), m["height_in"].tolist())),
        "corr_phys_dev_weight": _f(ST.pearson(
            m["phys_dev"].tolist(), m["weight_lb"].tolist())),
        "real": {"share_phys": real.get("share_phys"),
                 "share_u": real.get("share_u"), "b": real.get("b"),
                 "n_reps": real.get("n_reps"), "n_players": real.get("n_players")},
        "placebo": {"share_phys": plac.get("share_phys"),
                    "share_u": plac.get("share_u"), "b": plac.get("b"),
                    "n_reps": plac.get("n_reps"), "n_players": plac.get("n_players"),
                    "status": plac.get("status")},
        "placebo_minus_real_share_phys": (
            _f(float(plac["share_phys"]) - float(real["share_phys"]))
            if _finite(plac.get("share_phys")) and _finite(real.get("share_phys"))
            else None),
        "reading": ("The placebo share is the height/weight pseudo-phys share in "
                    "the same decomposition, with the same depth control. If it "
                    "is not near zero, height/weight are carrying between-player "
                    "get-off variance that the physical term would otherwise be "
                    "credited with. Reported as it is; no threshold is applied."),
    }


# --------------------------------------------------------------------------- #
# 3. Mechanical checks
# --------------------------------------------------------------------------- #
def onset_sensitivity(cfg, df: pd.DataFrame, players: list[int],
                      frozen_curves: dict) -> dict:
    """Re-measure Combine physical time on an onset-threshold grid, then re-fit.

    The in-game onset threshold does not enter the Phase 4 model: the outcome is
    the snap-to-crossing time and ``onset_latency_s`` / ``onset_to_cross_s`` are
    agreement measures only. The only model path an onset threshold can move is
    the Phase 2 physical time, so the grid is applied there, by re-running
    Phase 2's own ``measure_attempt`` / ``pool_players`` code at each threshold.
    """
    v = C.section(cfg, "validation")
    grid = [float(x) for x in v["onset_speed_grid_yds"]]
    mod02 = _load_stage_module("02_combine_physical_time")
    con = C.duckdb_connect(cfg)
    try:
        all_attempts = mod02.load_combine(con, cfg)
    finally:
        con.close()
    sub = all_attempts[all_attempts["nfl_id"].isin(players)].copy()
    if sub.empty:
        return {"status": "INSUFFICIENT_DATA",
                "reason": "no Combine 40-yard attempts for the fitted players"}

    dups = mod02.duplicate_report(sub)
    distances = [float(d) for d in C.section(cfg, "physical")["distances_yd"]]
    max_depth = float(C.section(cfg, "snaps")["max_depth_yd"])
    groups = {int(p): "primary_dl" for p in players}

    rows = []
    for threshold in grid:
        cfg2 = copy.deepcopy(cfg)
        C.block(cfg2)["onset"]["speed_yds"] = threshold
        attempt_rows, distance_rows = [], []
        for _, part in sub.groupby(["nfl_id", "attempt"], sort=True):
            a_row, d_rows, _ = mod02.measure_attempt(part, cfg2,
                                                     dups["flagged_keys"])
            attempt_rows.append(a_row)
            distance_rows.extend(d_rows)
        player_rows, _ = mod02.pool_players(attempt_rows, distance_rows,
                                            distances, max_depth, groups)
        curves = _curves_from_player_rows(player_rows)
        identity = {int(p): int(p) for p in players}
        remeasured = _apply_curve_mapping(df, curves, identity)
        res = _brief(_refit(remeasured, cfg))

        max_diff = None
        if abs(threshold - float(C.section(cfg, "onset")["speed_yds"])) < 1e-9:
            diffs = []
            for pid in players:
                a, b = frozen_curves.get(int(pid)), curves.get(int(pid))
                if not a or not b:
                    continue
                grid_cmp = np.intersect1d(a["d"], b["d"])
                if len(grid_cmp):
                    diffs.append(float(np.max(np.abs(
                        np.interp(grid_cmp, a["d"], a["m"])
                        - np.interp(grid_cmp, b["d"], b["m"])))))
            max_diff = max(diffs) if diffs else None

        rows.append({
            "onset_speed_yds": threshold,
            "n_attempts_measured": int(len(attempt_rows)),
            "n_attempts_onset_found": int(sum(1 for r in attempt_rows
                                              if r.get("onset_found"))),
            "n_players_with_curve": int(len(curves)),
            "n_reps_refit": res.get("n_reps"),
            "n_players_refit": res.get("n_players"),
            "b": res.get("b"),
            "share_phys": res.get("share_phys"),
            "share_u": res.get("share_u"),
            "jump_sd_ms": res.get("jump_sd_ms"),
            "status": res.get("status"),
            "max_abs_diff_vs_frozen_s": _f(max_diff),
        })

    ok_rows = [r for r in rows if r.get("status") == "OK"]
    spans = {}
    for key in ("share_phys", "share_u", "b"):
        vals = [float(r[key]) for r in ok_rows if _finite(r.get(key))]
        spans[key] = (max(vals) - min(vals)) if len(vals) > 1 else None
    return {
        "status": "OK" if ok_rows else "INSUFFICIENT_DATA",
        "grid": rows,
        "span_share_phys": _f(spans.get("share_phys")),
        "span_share_u": _f(spans.get("share_u")),
        "span_b": _f(spans.get("b")),
        "reproduction_check": (
            "At the frozen threshold the re-measured curves are compared with "
            "the committed Phase 2 curves (max_abs_diff_vs_frozen_s). A large "
            "value means the re-measurement, not the threshold, is what moved."),
    }


def mechanical_check(cfg, df: pd.DataFrame, curves: dict, *, base: dict,
                     players: list[int]) -> dict:
    """Depth/alignment removal, onset-threshold sweep, and the stance statement."""
    depth_removed = _brief(_refit(df, cfg, include_depth=False))
    alignment_removed = _brief(_refit(df, cfg, include_alignment=False))
    both_removed = _brief(_refit(df, cfg, include_depth=False,
                                 include_alignment=False))
    onset = onset_sensitivity(cfg, df, players, curves)
    real = _brief(base)
    variants = {"depth_removed": depth_removed,
                "alignment_removed": alignment_removed,
                "both_removed": both_removed,
                "onset_sweep": {"status": onset.get("status")}}
    failed = [k for k, v in variants.items() if v.get("status") != "OK"]
    status = ("OK" if not failed
              else ("PARTIAL" if len(failed) < len(variants) else "NOT RUN"))
    return {
        "status": status,
        **({"reason": "NOT RUN — " + ", ".join(failed) + " could not be fitted"}
           if failed else {}),
        "question": ("do the variance shares survive removal of the depth and "
                     "alignment effects, and an onset-threshold sweep?"),
        "base": {"b": real.get("b"), "share_phys": real.get("share_phys"),
                 "share_u": real.get("share_u")},
        "depth_removed": depth_removed,
        "alignment_removed": alignment_removed,
        "both_removed": both_removed,
        "onset": onset,
        "stance": {
            "status": "NOT POSSIBLE",
            "reason": ("no stance label exists in any released file (B8); "
                       "stance is not a covariate, not a stratifier and is not "
                       "imputed. Stance-specific subsets cannot be run, and this "
                       "is stated rather than silently dropped."),
        },
        "reading": ("Each variant is the same estimator with one effect removed. "
                    "A material change means that effect was carrying part of the "
                    "between-player split. No threshold is applied: the numbers "
                    "are reported side by side."),
    }


# --------------------------------------------------------------------------- #
# 4. Snap quality
# --------------------------------------------------------------------------- #
def snap_quality_check(cfg, df: pd.DataFrame, wide: pd.DataFrame, *, base: dict,
                       n_primary_label_side: int) -> dict:
    """Restrict to snaps with clean onset detection; report the share dropped.

    A missing key or QC column is reported as ``NOT RUN — <field> absent``. The
    stage never crashes on an absent column: an absent column is a fact to
    report, not an exception to raise halfway through a run that has already
    written two other artefacts.
    """
    keys = ["game_id", "play_id", "nfl_id"]
    qc_fields = ["onset_status", "cross_status", "cross_gap_policy",
                 "reaction_ready", "analysis_ready"]
    missing = [c for c in keys if c not in df.columns]
    missing += [c for c in keys + qc_fields if c not in wide.columns]
    if missing:
        fields = list(dict.fromkeys(missing))
        return {
            "status": "NOT RUN",
            "reason": (f"NOT RUN — {fields[0]} absent: the snap-quality "
                       f"restriction needs {', '.join(fields)}, which the "
                       "fitted frame or the label-side QC frame does not "
                       "carry. The share dropped is not reported as zero."),
            "missing_fields": fields,
            "question": ("do the shares survive restricting to snaps whose "
                         "onset was detected cleanly?"),
            "primary_set_label_side": {"n_reps": int(n_primary_label_side),
                                       "players": int(wide["nfl_id"].nunique())
                                       if "nfl_id" in wide.columns else None},
            "base": {"share_phys": _brief(base).get("share_phys"),
                     "share_u": _brief(base).get("share_u"),
                     "b": _brief(base).get("b")},
            "reading": ("Reported as NOT RUN. A filter that cannot be applied "
                        "is not a filter that dropped nothing."),
        }
    qc = wide[keys + ["onset_status", "cross_status", "cross_gap_policy",
                      "reaction_ready", "analysis_ready"]].copy()
    qc = qc.drop_duplicates(keys, keep="first")
    merged = df.merge(qc, on=keys, how="left")
    clean_mask = (
        merged["onset_status"].astype(str).eq("FOUND")
        & merged["cross_status"].astype(str).eq("OK")
        & merged["reaction_ready"].fillna(False).astype(bool)
        & merged["cross_gap_policy"].astype(str).isin(["none", "bridged"])
    )
    clean = merged[clean_mask].copy()
    tracked = qc[qc["analysis_ready"].fillna(False).astype(bool)]
    tracked_clean = tracked[
        tracked["onset_status"].astype(str).eq("FOUND")
        & tracked["cross_status"].astype(str).eq("OK")
        & tracked["reaction_ready"].fillna(False).astype(bool)
        & tracked["cross_gap_policy"].astype(str).isin(["none", "bridged"])]

    res = _refit(clean, cfg) if len(clean) else {"status": "INSUFFICIENT_DATA"}
    real = _brief(base)
    cleaned = _brief(res)
    n_tracked = int(len(tracked))
    n_tracked_clean = int(len(tracked_clean))
    return {
        "status": ("OK" if cleaned.get("status") == "OK"
                   else "INSUFFICIENT_DATA"),
        "question": ("do the shares survive restricting to snaps whose onset was "
                     "detected cleanly?"),
        "primary_set_label_side": {"n_reps": int(n_primary_label_side),
                                   "players": int(wide["nfl_id"].nunique())},
        "tracked_primary_set": {
            "n_reps": n_tracked,
            "n_clean": n_tracked_clean,
            "n_dropped": n_tracked - n_tracked_clean,
            "share_dropped": _f((n_tracked - n_tracked_clean) / n_tracked)
            if n_tracked else None,
            "dropped_by_status": {
                "cross_status_crossed_before_onset": int(
                    (tracked["cross_status"].astype(str)
                     == "CROSSED_BEFORE_ONSET").sum()),
                "cross_status_not_reached": int(
                    (tracked["cross_status"].astype(str) == "NOT_REACHED").sum()),
                "onset_not_found": int(
                    (~tracked["onset_status"].astype(str).eq("FOUND")).sum()),
            },
        },
        "fitted_set": {
            "n_reps": int(len(df)),
            "n_clean": int(len(clean)),
            "n_dropped": int(len(df) - len(clean)),
            "share_dropped": _f((len(df) - len(clean)) / len(df)) if len(df) else None,
        },
        "base": {"share_phys": real.get("share_phys"),
                 "share_u": real.get("share_u"), "b": real.get("b")},
        "clean": {"share_phys": cleaned.get("share_phys"),
                  "share_u": cleaned.get("share_u"), "b": cleaned.get("b"),
                  "n_reps": cleaned.get("n_reps"),
                  "n_players": cleaned.get("n_players"),
                  "status": cleaned.get("status")},
        "reading": ("The share dropped is reported at both levels: over the "
                    "tracked primary set (where the onset gate binds) and inside "
                    "the fitted set (where the model already requires a "
                    "computable crossing). A filter that drops nothing inside the "
                    "fitted set is reported as unable to fail on this data; the "
                    "synthetic world shows the same filter firing."),
    }


# --------------------------------------------------------------------------- #
# 5. Selection
# --------------------------------------------------------------------------- #
def selection_check(cfg, df: pd.DataFrame, *, base: dict) -> tuple[dict, list[dict]]:
    """Snap counts by player, equal-snap weighting, and the draftee limitation."""
    v = C.section(cfg, "validation")
    has_attempts = "n_attempts_used" in df.columns
    agg = {"n_reps": ("y", "size"), "n_games": ("team_game", "nunique")}
    if has_attempts:
        agg["n_attempts_used"] = ("n_attempts_used", "max")
    counts = df.groupby("player", sort=True).agg(**agg).reset_index()
    if not has_attempts:
        counts["n_attempts_used"] = np.nan
    total = int(counts["n_reps"].sum())
    counts["share_of_reps"] = counts["n_reps"] / total if total else None
    counts["equal_snap_weight"] = 1.0 / counts["n_reps"]
    table = [{
        "player_id": int(r["player"]),
        "n_reps": int(r["n_reps"]),
        "n_games": int(r["n_games"]),
        "share_of_reps": _f(r["share_of_reps"]),
        "equal_snap_weight": _f(r["equal_snap_weight"]),
        "n_attempts_used": int(r["n_attempts_used"])
        if _finite(r["n_attempts_used"]) else None,
    } for _, r in counts.iterrows()]

    weighted = _refit(df, cfg, weighting="equal_snaps")
    boot = {"status": "SKIPPED", "n_requested": 0, "n_ok": 0}
    if weighted.get("status") == "OK":
        mc = C.section(cfg, "models")
        boot = M.bootstrap_decomposition(
            df, correct_measurement_error=True,
            resamples=int(v["equal_snap_bootstrap_resamples"]),
            seed=int(C.section(cfg, "permutation")["seed"]),
            population_degree=int(mc["population_curve_degree"]),
            max_iter=int(mc["max_iter"]), tol=float(mc["tol"]),
            weighting="equal_snaps", point=weighted,
            min_ok_share=float(mc.get("min_bootstrap_ok_share", 0.5)))

    real = _brief(base)
    weighted_brief = _brief(weighted)
    ratio = (float(counts["n_reps"].max()) / float(counts["n_reps"].min())
             if len(counts) and counts["n_reps"].min() > 0 else None)
    return {
        "status": "OK" if weighted_brief.get("status") == "OK" else "INSUFFICIENT_DATA",
        "question": ("do the shares depend on how many snaps a player happened to "
                     "play?"),
        "snap_counts_by_player": table,
        "n_players": int(len(counts)),
        "n_reps": total,
        "max_over_min_reps": _f(ratio),
        "share_of_reps_top_player": _f(counts["share_of_reps"].max())
        if len(counts) else None,
        "unweighted": {"share_phys": real.get("share_phys"),
                       "share_u": real.get("share_u"), "b": real.get("b"),
                       "n_reps": real.get("n_reps")},
        "equal_snap_weighted": {
            "share_phys": weighted_brief.get("share_phys"),
            "share_u": weighted_brief.get("share_u"),
            "b": weighted_brief.get("b"),
            "n_reps": weighted_brief.get("n_reps"),
            "status": weighted_brief.get("status"),
            "bootstrap": {
                "status": boot.get("status"),
                "n_requested": boot.get("n_requested"),
                "n_ok": boot.get("n_ok"),
                "share_phys_ci95": boot.get("share_phys_ci95"),
                "share_u_ci95": boot.get("share_u_ci95"),
                "b_ci95": boot.get("b_ci95"),
            },
            "estimator_note": ("each rep of player i is given weight 1/n_i "
                               "(normalised to mean 1) and the identical EM "
                               "estimator is run on the weighted data; the "
                               "variance components are on that weighted scale"),
        },
        "equal_snap_minus_unweighted_share_phys": (
            _f(float(weighted_brief["share_phys"]) - float(real["share_phys"]))
            if _finite(weighted_brief.get("share_phys"))
            and _finite(real.get("share_phys")) else None),
        "draftee_selection": {
            "status": "CANNOT BE TESTED",
            "reason": ("the dataset is pre-filtered to 510 Combine attendees "
                       "(B5) and only drafted DL have game tracking. There is no "
                       "undrafted or non-attendee comparison group in the "
                       "release, so selection cannot be tested. Claims are "
                       "limited to DL who reached the NFL; nothing here "
                       "generalises to undrafted DL."),
        },
    }, table


# --------------------------------------------------------------------------- #
# Resolution statement
# --------------------------------------------------------------------------- #
def resolution_statement(cfg, base: dict) -> dict:
    """Smallest detectable jump SD as a resolution statement (D114), not a test."""
    pt = base.get("player_table")
    out = {"status": "INSUFFICIENT_DATA", "kind": "resolution_statement"}
    if pt is None or len(pt) == 0 or base.get("status") != "OK":
        out["reason"] = "the base fit has no player table"
        return out
    if "u_se_s" not in getattr(pt, "columns", []):
        out["reason"] = "NOT RUN — u_se_s absent from the player table"
        return out
    su2 = base.get("sigma_u2")
    vp = base.get("between_phys_var")
    if not _finite(su2) or not _finite(vp):
        out["reason"] = "sigma_u2 or between_phys_var is absent"
        return out
    between_sd = math.sqrt(max(0.0, float(su2)) + max(0.0, float(vp)))
    rel = []
    for _, row in pt.iterrows():
        se2 = float(row["u_se_s"]) ** 2
        denom = float(su2) + se2
        if denom > 0:
            rel.append(float(su2) / denom)
    floor = ST.min_detectable_share_effective(rel)
    p_eff = floor.get("p_eff")
    share_floor = floor.get("min_share")
    jump_sd = (ST.sd_from_variance_share(share_floor, between_sd)
               if _finite(share_floor) and between_sd > 0 else None)

    prereg_ref = None
    power_path = C.abs_path(C.section(cfg, "paths")["prereg_outputs"]) / "power.json"
    if power_path.exists():
        try:
            import json
            pw = json.loads(power_path.read_text(encoding="utf-8"))
            row20 = next((r for r in pw.get("table", [])
                          if int(r.get("min_reps_per_player", -1)) == 20), None)
            prereg_ref = {
                "source": str(power_path),
                "provenance": pw.get("provenance"),
                "smallest_detectable_jump_sd_ms_at_20_reps": (
                    row20.get("smallest_detectable_jump_sd_ms_effective")
                    if row20 else None),
                "smallest_detectable_jump_sd_ms_best_case": pw.get(
                    "answers", {}).get("smallest_detectable_jump_sd_ms_best_case"),
            }
        except (json.JSONDecodeError, OSError):  # pragma: no cover - defensive
            prereg_ref = {"status": "UNREADABLE"}

    out.update({
        "status": "OK",
        "final_sample": {"n_reps": int(base.get("n_reps", 0)),
                         "n_players": int(base.get("n_players", 0)),
                         "n_team_games": int(base.get("n_team_games", 0)),
                         "min_reps_per_player": int(
                             C.section(cfg, "models")["min_reps_per_player"])},
        "between_player_sd_s": _f(between_sd),
        "p_eff_realised": _f(p_eff),
        "floor_share_realised": _f(share_floor),
        "smallest_detectable_jump_sd_ms_realised": _f(
            1000.0 * jump_sd if jump_sd is not None else None),
        "prereg_reference": prereg_ref,
        "statement": (
            "Reported as a resolution statement, not a threshold. With this "
            "sample the design resolves a jump SD of roughly "
            f"{1000.0 * jump_sd:.0f} ms at the design floor z*sqrt(2/P_eff). "
            "No smallest-effect-of-interest test is applied and there is no "
            "confirmatory gate (Conor 2026-11-05 22:16; frozen prereg §10). A "
            "small share with a wide interval is reported as a small share with "
            "a wide interval."
            if jump_sd is not None else
            "The realised resolution could not be computed; no value is "
            "substituted."),
    })
    return out


# --------------------------------------------------------------------------- #
# Synthetic worlds: every check must be provably able to fail
# --------------------------------------------------------------------------- #
def _sim_frame(rng, n_players: int = 30, reps_base: int = 20,
               *, reps_spread: int = 0) -> pd.DataFrame:
    rows = []
    for i in range(n_players):
        player = i + 1
        alignment = "INTERIOR" if player % 3 == 0 else "EDGE"
        reps = int(reps_base + (rng.integers(-reps_spread, reps_spread + 1)
                                if reps_spread else 0))
        reps = max(2, reps)
        for _ in range(reps):
            rows.append({
                "player": player,
                "team_game": f"g{player % 7}",
                "depth_yd": float(rng.uniform(0.4, 1.6)),
                "alignment_class": alignment,
            })
    return pd.DataFrame(rows)


def _finish_world(df: pd.DataFrame, rng, *, phys_dev, y_extra=None,
                  u_sd: float = 0.10, e_sd: float = 0.08,
                  phys_sd: float = 0.01) -> pd.DataFrame:
    """Give a simulated frame its physical time and outcome."""
    players = sorted(df["player"].unique())
    dev = dict(zip(players, phys_dev))
    u = dict(zip(players, rng.normal(0.0, u_sd, len(players))))
    base = 0.10 + 0.35 * df["depth_yd"].to_numpy(dtype=float)
    df = df.copy()
    df["phys_s"] = base + df["player"].map(dev).astype(float)
    df["phys_se_s"] = float(phys_sd)
    df["y"] = (1.0 * df["phys_s"]
               + df["player"].map(u).astype(float)
               + rng.normal(0.0, e_sd, len(df)))
    if y_extra is not None:
        df["y"] = df["y"] + np.asarray(y_extra, dtype=float)
    df["n_attempts_used"] = 2
    return df


def _perm_world(cfg, rng, n_permutations: int) -> dict:
    df = _sim_frame(rng)
    players = sorted(df["player"].unique())
    planted = rng.normal(0.0, 0.075, len(players))
    df = _finish_world(df, rng, phys_dev=planted)
    curves = {}
    for pid, sub in df.groupby("player", sort=True):
        d = np.r_[0.0, np.sort(sub["depth_yd"].unique())]
        m = np.r_[0.0, [float(sub[sub["depth_yd"] == x]["phys_s"].mean())
                        for x in d[1:]]]
        curves[int(pid)] = {"d": d, "m": m, "s": np.full(len(d), 0.01),
                            "max_depth": float(d[-1])}
    base = _refit(df, cfg)
    check = permutation_check(cfg, df, curves, base=base,
                              n_permutations=n_permutations, seed=20270112)
    obs = _f(base.get("share_phys"))
    perm = check.get("permutation_share_phys") or {}
    fired = bool(obs is not None and perm.get("p97_5") is not None
                 and obs > perm["p97_5"])
    return {
        "status": "OK",
        "world": ("phys carries a real between-player effect (planted SD 0.075 s "
                  "against a 0.10 s jump SD) and the outcome is built from it"),
        "observed_share_phys": obs,
        "permutation_mean_share_phys": perm.get("mean"),
        "permutation_p97_5": perm.get("p97_5"),
        "expected_detector": "observed share above the permutation distribution",
        "detector_fired": fired,
        "n_permutations": int(check.get("n_ok", 0)),
    }


def _placebo_world(cfg, rng) -> dict:
    df = _sim_frame(rng)
    players = sorted(df["player"].unique())
    h = rng.normal(75.0, 2.5, len(players))
    w = rng.normal(300.0, 18.0, len(players))
    dev = 0.012 * (h - 75.0) + 0.004 * (w - 300.0) + rng.normal(0.0, 0.02, len(players))
    real = _finish_world(df, rng, phys_dev=dev)
    null_dev = rng.normal(0.0, 0.075, len(players))
    null = _finish_world(df, rng, phys_dev=null_dev)

    def with_hw(frame):
        out = frame.copy()
        out["height_in"] = out["player"].map(dict(zip(players, h)))
        out["weight_lb"] = out["player"].map(dict(zip(players, w)))
        return out

    def placebo_share(frame):
        work = frame.copy()
        mc = C.section(cfg, "models")
        mu = M.population_phys_curve(work["depth_yd"].to_numpy(dtype=float),
                                     work["phys_s"].to_numpy(dtype=float),
                                     int(mc["population_curve_degree"]))
        work["phys_dev"] = work["phys_s"].to_numpy(dtype=float) - mu
        pdev = work.groupby("player")["phys_dev"].mean()
        hw = work.groupby("player")[["height_in", "weight_lb"]].first()
        X = np.column_stack([
            np.ones(len(pdev)),
            (hw["height_in"] - hw["height_in"].mean()) / hw["height_in"].std(ddof=0),
            (hw["weight_lb"] - hw["weight_lb"].mean()) / hw["weight_lb"].std(ddof=0),
        ])
        beta, *_ = np.linalg.lstsq(X, pdev.to_numpy(dtype=float), rcond=None)
        fitted = dict(zip(pdev.index.astype(int), X @ beta))
        work["phys_s"] = mu + work["player"].map(fitted).astype(float)
        work["phys_se_s"] = float(frame["phys_se_s"].mean())
        res = _refit(work, cfg)
        return _f(res.get("share_phys"))

    carries = placebo_share(with_hw(real))
    nothing = placebo_share(with_hw(null))
    fired = bool(carries is not None and nothing is not None
                 and carries > 0.15 and nothing < 0.05)
    return {
        "status": "OK",
        "world": ("height/weight genuinely generate the physical deviation, so "
                  "the placebo MUST detect them; the control world draws the "
                  "physical deviation independently of height/weight"),
        "placebo_share_when_height_weight_carry_signal": carries,
        "placebo_share_in_null_control": nothing,
        "expected_detector": ("a high placebo share when height/weight carry the "
                              "signal and a near-zero share when they do not"),
        "detector_fired": fired,
    }


def _mechanical_world(cfg, rng) -> dict:
    df = _sim_frame(rng)
    players = sorted(df["player"].unique())
    dev = rng.normal(0.0, 0.075, len(players))
    edge = df["alignment_class"].eq("EDGE").astype(float)
    # Depth and alignment carry real signal in this world.
    extra = 0.45 * (df["depth_yd"].to_numpy(dtype=float) - 1.0) - 0.10 * edge
    work = _finish_world(df, rng, phys_dev=dev, y_extra=extra)
    base = _refit(work, cfg)
    no_depth = _refit(work, cfg, include_depth=False)
    no_align = _refit(work, cfg, include_alignment=False)
    changes = []
    for res in (no_depth, no_align):
        if res.get("status") == "OK" and base.get("status") == "OK":
            changes.append(abs(float(res["b"]) - float(base["b"])))
    fired = bool(changes and max(changes) > 0.10)

    # Onset-threshold world: a threshold error adds a player-level delay that is
    # uncorrelated with the outcome, which attenuates the physical share.
    df2 = _sim_frame(rng)
    players2 = sorted(df2["player"].unique())
    true_dev = dict(zip(players2, rng.normal(0.0, 0.065, len(players2))))
    jump = dict(zip(players2, rng.normal(0.0, 0.10, len(players2))))
    onset_err = dict(zip(players2, rng.normal(0.0, 0.05, len(players2))))
    base_time = 0.10 + 0.35 * df2["depth_yd"].to_numpy(dtype=float)
    true_time = base_time + df2["player"].map(true_dev).astype(float)
    y = (true_time + df2["player"].map(jump).astype(float)
         + rng.normal(0.0, 0.05, len(df2)))
    shares = []
    for factor in (0.0, 0.5, 1.0):
        w2 = df2.copy()
        w2["phys_s"] = (true_time
                         - factor * w2["player"].map(onset_err).astype(float))
        w2["phys_se_s"] = 0.01
        w2["y"] = y
        w2["n_attempts_used"] = 2
        res = _refit(w2, cfg)
        shares.append(_f(res.get("share_phys")))
    vals = [s for s in shares if s is not None]
    onset_span = (max(vals) - min(vals)) if len(vals) > 1 else None
    return {
        "status": "OK",
        "depth_alignment_world": {
            "world": ("depth and alignment carry real effects, so removing them "
                      "must move b"),
            "b_base": _f(base.get("b")),
            "b_depth_removed": _f(no_depth.get("b")),
            "b_alignment_removed": _f(no_align.get("b")),
            "expected_detector": "a material b change when an effect is removed",
            "detector_fired": fired,
        },
        "onset_world": {
            "world": ("a threshold error adds a player-level delay uncorrelated "
                      "with the outcome; the sweep must move the share"),
            "share_phys_by_shift": shares,
            "span": _f(onset_span),
            "expected_detector": "a material share span across the sweep",
            "detector_fired": bool(onset_span is not None and onset_span > 0.03),
        },
    }


def _snap_quality_world(cfg, rng) -> dict:
    df = _sim_frame(rng)
    players = sorted(df["player"].unique())
    dev = rng.normal(0.0, 0.075, len(players))
    dirty_players = set(players[::2])
    extra = np.where(df["player"].isin(dirty_players)
                     & (rng.random(len(df)) < 0.25), 0.25, 0.0)
    work = _finish_world(df, rng, phys_dev=dev, y_extra=extra)
    work["clean"] = extra == 0.0
    all_fit = _refit(work, cfg)
    clean_fit = _refit(work[work["clean"]].copy(), cfg)
    change = (abs(float(clean_fit["share_u"]) - float(all_fit["share_u"]))
              if clean_fit.get("status") == "OK" and all_fit.get("status") == "OK"
              else None)
    return {
        "status": "OK",
        "world": ("25% of reps for half the players carry a +0.25 s onset-definition "
                  "error, so restricting to clean reps must move the shares"),
        "n_dropped": int((~work["clean"]).sum()),
        "share_dropped": _f(float((~work["clean"]).mean())),
        "share_u_all": _f(all_fit.get("share_u")),
        "share_u_clean": _f(clean_fit.get("share_u")),
        "expected_detector": "a material share change after the restriction",
        "detector_fired": bool(change is not None and change > 0.03),
    }


def _selection_world(cfg, rng) -> dict:
    df = _sim_frame(rng, reps_base=20, reps_spread=70)
    players = sorted(df["player"].unique())
    dev = dict(zip(players, rng.normal(0.0, 0.075, len(players))))
    u = dict(zip(players, rng.normal(0.0, 0.10, len(players))))
    counts = df.groupby("player").size().astype(float)
    z = (counts - counts.mean()) / counts.std(ddof=0)
    # Player-specific phys slope that falls with snap count: the unweighted fit
    # is dominated by the high-rep players, so equal-snap weighting must move
    # the estimate. This is the mechanism the check exists to expose.
    #
    # ``phys_s`` needs rep-level variation that is *independent of depth* — the
    # Phase 2 posterior does vary rep to rep. Without it phys is a deterministic
    # function of depth within a player, the fixed slope is identified only
    # between players, and the player random intercept absorbs the very
    # variation this world plants (that version of the world did not fire, which
    # is how the requirement to add the noise was found).
    b_i = dict(zip(counts.index.astype(int), 1.0 - 0.3 * z))
    work = df.copy()
    work["phys_s"] = (0.10 + 0.35 * work["depth_yd"].to_numpy(dtype=float)
                      + work["player"].map(dev).astype(float)
                      + rng.normal(0.0, 0.02, len(work)))
    work["phys_se_s"] = 0.01
    work["y"] = (work["player"].map(b_i).astype(float) * work["phys_s"]
                 + work["player"].map(u).astype(float)
                 + rng.normal(0.0, 0.08, len(work)))
    work["n_attempts_used"] = 2
    plain = _refit(work, cfg)
    weighted = _refit(work, cfg, weighting="equal_snaps")
    change = (abs(float(weighted["share_phys"]) - float(plain["share_phys"]))
              if weighted.get("status") == "OK" and plain.get("status") == "OK"
              else None)
    return {
        "status": "OK",
        "world": ("snap counts vary widely per player and each player's physical "
                  "slope is correlated with his snap count, so the unweighted "
                  "fit over-weights high-rep players and equal-snap weighting "
                  "must move the shares"),
        "reps_max_over_min": _f(float(counts.max() / counts.min())),
        "share_phys_unweighted": _f(plain.get("share_phys")),
        "share_phys_equal_snaps": _f(weighted.get("share_phys")),
        "b_unweighted": _f(plain.get("b")),
        "b_equal_snaps": _f(weighted.get("b")),
        "change_share_phys": _f(change),
        "expected_detector": "a material share change under equal-snap weighting",
        "detector_fired": bool(change is not None and change > 0.03),
    }


def synthetic_evidence(cfg) -> dict:
    """Run one synthetic world per check, seeded, and report whether it fired."""
    seed = int(C.section(cfg, "validation")["synthetic_seed"])
    n_perm = int(C.section(cfg, "validation")["synthetic_permutation_resamples"])
    rng = np.random.default_rng(seed)
    out = {
        "seed": seed,
        "purpose": ("each world is built so the check SHOULD fire; a check that "
                    "cannot fail manufactures confidence and is worse than none"),
    }
    out["permutation"] = _perm_world(cfg, rng, n_perm)
    out["placebo"] = _placebo_world(cfg, rng)
    out["mechanical"] = _mechanical_world(cfg, rng)
    out["snap_quality"] = _snap_quality_world(cfg, rng)
    out["selection"] = _selection_world(cfg, rng)
    fired = {
        "permutation": bool(out["permutation"].get("detector_fired")),
        "placebo": bool(out["placebo"].get("detector_fired")),
        "mechanical_depth_alignment": bool(
            out["mechanical"]["depth_alignment_world"].get("detector_fired")),
        "mechanical_onset": bool(
            out["mechanical"]["onset_world"].get("detector_fired")),
        "snap_quality": bool(out["snap_quality"].get("detector_fired")),
        "selection": bool(out["selection"].get("detector_fired")),
    }
    out["all_detectors_fired"] = all(fired.values())
    out["detectors"] = fired
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_checks(cfg, mode: str, frames: dict, *, n_permutations: int | None = None,
               with_synthetic: bool = True) -> dict:
    """Run the five checks on already-loaded frames (pure of file I/O)."""
    mc = C.section(cfg, "models")
    v = C.section(cfg, "validation")
    min_reps = int(mc["min_reps_per_player"])
    df = frames["df"]
    base_frame = M._eligible(df, min_reps)
    base = _refit(base_frame, cfg)
    players = sorted(int(x) for x in base_frame["player"].unique())

    artefact: dict = {
        "stage": STAGE,
        "mode": mode,
        "estimator": {
            "name": "same estimator as Phase 4 (EM on Henderson mixed model "
                    "equations, two crossed random intercepts)",
            "measurement_error": "regression calibration, as Phase 4",
            "intervals": "player-cluster basic bootstrap (equal-snap check only)",
            "semantics_changed": False,
        },
        "constraints": {
            "pressure_fields_read": [],
            "blocker_term": False,
            "stance_term": False,
            "plane_offset_hardcoded": False,
            "player_get_off_used": False,
        },
        "min_reps_per_player": min_reps,
        "sample": {
            "n_reps": int(base.get("n_reps", 0)) if base.get("status") == "OK" else 0,
            "n_players": len(players),
            "n_team_games": int(base.get("n_team_games", 0))
            if base.get("status") == "OK" else 0,
            "phys_interpolation_status": frames.get("phys_status"),
            "note": ("the fitted set is the Phase 4 pooled set at the frozen "
                     "rep minimum; the permutation/placebo/mechanical/selection "
                     "checks all use it, and the check-level blocks say when a "
                     "variant cannot be run"),
        },
        "base": _brief(base),
    }

    if base.get("status") != "OK":
        artefact["status"] = "INSUFFICIENT_DATA"
        artefact["reason"] = (f"the base decomposition is {base.get('status')}; "
                              f"no check can be computed")
        artefact["checks"] = {}
        artefact["synthetic_can_fail"] = (synthetic_evidence(cfg)
                                          if with_synthetic else {"status": "SKIPPED"})
        return artefact

    n_perm = int(n_permutations if n_permutations is not None
                 else v["permutation_resamples"])
    seed = int(C.section(cfg, "permutation")["seed"])

    perm = permutation_check(cfg, base_frame, frames["curves"], base=base,
                             n_permutations=n_perm, seed=seed)
    placebo = placebo_check(cfg, base_frame, base=base)
    mech = mechanical_check(cfg, base_frame, frames["curves"], base=base,
                            players=players)
    wide = frames["wide"]
    if "in_primary_set" in wide.columns:
        primary_label_side = int(wide["in_primary_set"]
                                 .fillna(False).astype(bool).sum())
    else:                       # the query already filters on it
        primary_label_side = int(len(wide))
    quality = snap_quality_check(cfg, base_frame, frames["wide"], base=base,
                                 n_primary_label_side=primary_label_side)
    selection, table = selection_check(cfg, base_frame, base=base)

    artefact["checks"] = {
        "permutation": perm,
        "placebo": placebo,
        "mechanical": mech,
        "snap_quality": quality,
        "selection": selection,
    }
    artefact["snap_counts_by_player"] = table
    artefact["resolution"] = resolution_statement(cfg, base)
    artefact["synthetic_can_fail"] = (synthetic_evidence(cfg)
                                      if with_synthetic else {"status": "SKIPPED"})
    detectors = (artefact["synthetic_can_fail"].get("detectors") or {})
    artefact["status"] = "OK" if all(
        artefact["checks"].get(k, {}).get("status") == "OK"
        for k in ("permutation", "placebo", "mechanical", "snap_quality",
                  "selection")) else "PARTIAL"
    artefact["all_detectors_fired"] = bool(
        artefact["synthetic_can_fail"].get("all_detectors_fired")) and bool(detectors)
    artefact["determinism"] = {
        "permutation_seed": seed,
        "synthetic_seed": int(v["synthetic_seed"]),
        "note": ("all draws are seeded; the EM has no random initialisation. "
                 "Two runs on identical inputs and config write identical "
                 "artefacts."),
    }
    return artefact


def main(argv=None) -> int:
    cfg, mode, force = C.stage_bootstrap(argv, STAGE)
    p = C.paths(cfg, mode)
    out_dir = p["outputs"] / "validation"

    if C.is_done(cfg, STAGE, mode) and not force and (out_dir / "validation.json").exists():
        print(f"{STAGE}: already done (checkpoint); use --force to re-run")
        return 0

    # Load and validate every input BEFORE creating an output directory: a
    # refusal must leave no file that could be mistaken for a result.
    frames = load_frames(cfg, mode)

    with C.timed_stage(cfg, STAGE, mode,
                       note="Phase 5 validation of the Phase 4 decomposition") as st:
        artefact = run_checks(cfg, mode, frames)
        # "The whole stage cannot run" is a different fact from "one check could
        # not be applied". Only the first is a non-zero exit; the second is
        # reported as NOT RUN/PARTIAL inside an artefact that is still written.
        whole_stage_ran = artefact.get("status") in ("OK", "PARTIAL")
        out_dir.mkdir(parents=True, exist_ok=True)
        C.write_json(out_dir / "validation.json", artefact, mode=mode, cfg=cfg)
        C.write_csv(
            out_dir / "validation_snap_counts.csv",
            artefact.get("snap_counts_by_player", []),
            ["player_id", "n_reps", "n_games", "share_of_reps",
             "equal_snap_weight", "n_attempts_used"],
            mode=mode, cfg=cfg)
        grid = ((artefact.get("checks", {}).get("mechanical", {})
                 or {}).get("onset", {}) or {}).get("grid", [])
        C.write_csv(
            out_dir / "validation_onset_grid.csv", grid,
            ["onset_speed_yds", "n_attempts_measured", "n_attempts_onset_found",
             "n_players_with_curve", "n_reps_refit", "n_players_refit", "b",
             "share_phys", "share_u", "jump_sd_ms", "status",
             "max_abs_diff_vs_frozen_s"],
            mode=mode, cfg=cfg)
        checks = artefact.get("checks", {})
        st.note(f"status {artefact.get('status')}; "
                f"perm n_ok={(checks.get('permutation') or {}).get('n_ok')}; "
                f"detectors_fired={artefact.get('all_detectors_fired')}")
        if whole_stage_ran:
            C.mark_done(cfg, STAGE, mode, {
                "status": artefact.get("status"),
                "n_reps": artefact.get("sample", {}).get("n_reps"),
                "n_players": artefact.get("sample", {}).get("n_players"),
            })
        print(f"{STAGE}: done ({mode}); status={artefact.get('status')} "
              f"reps={artefact.get('sample', {}).get('n_reps')} "
              f"players={artefact.get('sample', {}).get('n_players')} "
              f"detectors_fired={artefact.get('all_detectors_fired')}")
        print(f"{STAGE}: wrote {out_dir / 'validation.json'}")
    if not whole_stage_ran:
        print(f"{STAGE}: NOT RUN — {artefact.get('reason', 'the stage could not '
                                                'compute any check')}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
