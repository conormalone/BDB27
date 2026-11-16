"""Reliability of the 30-60 degree band as a player-level exposure.

D206. The question is not whether a single break is measured well, but whether a
defensive back's *typical* retention in the band is a stable trait that could
carry a player-level correlation with the Combine. That is a one-way
random-effects ICC(1) on ``apex_retention`` with player as the group: the ratio
of the between-player variance component to the total variance under that
model. It is a variance-component ratio, **not** the observed share of variance
that sits between players; the raw descriptive share is a different quantity.

The estimator is **not reimplemented here**. :func:`bdb27.band_reliability.icc1`
is imported from :mod:`bdb27.fallback_validation` (D203), so both artefacts
report the identical ANOVA-moment ICC(1), including its clamp-at-zero display
convention and its behaviour on unbalanced groups.

Four bands are reported side by side, because the drill is run at 3-5 yd/s and a
correlation with the Combine would only be interpretable near that speed:

* every valid band turn;
* entry speed 3-5 yd/s;
* entry speed 3-5 yd/s on pass plays;
* pass plays only.

An *adjusted* ICC is also reported: ``apex_retention`` is residualised on
``entry_speed``, ``entry_speed**2``, ``abs_turn_angle`` and
``abs_turn_angle**2`` (OLS with an intercept), and ICC(1) is recomputed on the
residuals. This asks whether the between-player variance component survives the
obvious turn-level covariates, not whether it becomes large.

Every count is reported against its denominator. Nothing here gates, skips or
raises on a thin subset; the only failures are a missing file, an unreadable
file, or a missing required column, and each names what it tried.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from bdb27.fallback_validation import icc1
from bdb27.geometric_match import file_sha256, git_head

PROVENANCE = "FULL"
STAMP = "[FULL]"

#: The frozen band. Left-closed, right-open, as everywhere else in the project.
BAND_LO, BAND_HI = 30.0, 60.0
SETTING = "game"
POSITION_GROUP = "DB"

#: The Combine drill's own entry-speed window (TASK.md §3; D203 leg 3).
ENTRY_LO, ENTRY_HI = 3.0, 5.0

#: Join key for the play-type context, identical to ``bdb27.turn_context``.
KEY: tuple[str, ...] = ("game_id", "play_id", "nfl_id", "frame_start")

#: Columns the module cannot compute without. Missing any one is a hard error
#: that names the file and the column (never a silent drop).
REQUIRED_TURN_COLUMNS: tuple[str, ...] = (
    "setting", "position_group", "valid", "nfl_id", "apex_retention",
    "entry_speed", "abs_turn_angle", *KEY,
)
REQUIRED_CONTEXT_COLUMNS: tuple[str, ...] = (*KEY, "play_type")

#: The four scenarios, in the order Conor asked for them. ``None`` means "the
#: whole valid band"; the others are applied to the loaded frame.
SCENARIOS: tuple[dict[str, Any], ...] = (
    {"name": "all_band", "entry_3_5": False, "pass_only": False},
    {"name": "entry_3_5", "entry_3_5": True, "pass_only": False},
    {"name": "entry_3_5_pass", "entry_3_5": True, "pass_only": True},
    {"name": "pass_only", "entry_3_5": False, "pass_only": True},
)

#: Residualisation covariates, printed in the artefact so the adjustment cannot
#: drift into an unstated model.
ADJUSTMENT_COVARIATES: tuple[str, ...] = (
    "entry_speed", "entry_speed^2", "abs_turn_angle", "abs_turn_angle^2",
)

#: The two adjustment samples Conor asked for.
ADJUSTMENT_SAMPLES: tuple[str, ...] = ("all_band", "entry_3_5")


# ------------------------------------------------------------------ loading
def _require_columns(frame: pd.DataFrame, columns: Sequence[str], *,
                     path: str | Path, what: str) -> None:
    """Raise a single error naming the path and every missing column."""
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise KeyError(
            f"{what} is missing required column(s) {missing} in {path}; "
            f"found columns: {list(frame.columns)}")


def load_turns(path: str | Path) -> pd.DataFrame:
    """Read the turn parquet, failing loudly on a missing file or column."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"turn file not found: {path} (set TURNS to "
            "data/interim/full_run_20261111/bdb27_full_db_turns.parquet)")
    df = pd.read_parquet(path)
    _require_columns(df, REQUIRED_TURN_COLUMNS, path=path, what="turn file")
    return df


def load_context(path: str | Path) -> pd.DataFrame:
    """Read the turn-context parquet that carries ``play_type``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"turn-context file not found: {path} (set CONTEXT to "
            "outputs/turn_context/turn_context.parquet; build it with "
            "scripts/turn_context.py)")
    df = pd.read_parquet(path)
    _require_columns(df, REQUIRED_CONTEXT_COLUMNS, path=path, what="context file")
    return df


def build_turn_frame(turns: pd.DataFrame, context: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Filter the frozen band and join ``play_type`` without fanning out.

    The ``valid == True`` filter is mandatory: the parquet holds one band row
    with ``valid == False``, so the committed 7,777 falls out of 7,778 band rows.
    ``context`` has three duplicated four-column keys (two overlapping
    segmentations of the same turn); they are deduplicated on the key and the
    join is ``many_to_one`` because the duplicate turn rows share the play.
    """
    diag: dict[str, Any] = {"rows_in_file": int(len(turns))}
    band = turns[
        (turns["setting"] == SETTING)
        & (turns["position_group"] == POSITION_GROUP)
        & (turns["abs_turn_angle"] >= BAND_LO)
        & (turns["abs_turn_angle"] < BAND_HI)
    ]
    diag["band_rows_before_valid"] = int(len(band))
    diag["band_rows_invalid_dropped"] = int((~band["valid"].astype(bool)).sum())
    band = band[band["valid"].astype(bool)].copy()
    diag["band_turns"] = int(len(band))
    diag["players"] = int(band["nfl_id"].nunique())
    diag["plays"] = int(band[["game_id", "play_id"]].drop_duplicates().shape[0])
    diag["games"] = int(band["game_id"].nunique())
    diag["turns_per_player"] = {
        "min": int(band.groupby("nfl_id").size().min()) if len(band) else 0,
        "median": float(band.groupby("nfl_id").size().median()) if len(band) else 0.0,
        "max": int(band.groupby("nfl_id").size().max()) if len(band) else 0,
    }

    ctx = context[list(KEY) + ["play_type"]].drop_duplicates(list(KEY))
    frame = band.merge(ctx, on=list(KEY), how="left", validate="many_to_one")
    diag["turns_without_context"] = int(frame["play_type"].isna().sum())
    diag["play_type"] = {
        str(k): int(v) for k, v in frame["play_type"].value_counts(dropna=False).items()
    }
    diag["join_rows"] = int(len(frame))
    diag["join_key"] = list(KEY)
    diag["join_validate"] = "many_to_one"
    diag["context_keys_deduplicated"] = int(
        len(context) - len(ctx))
    return frame, diag


def scenario_frame(frame: pd.DataFrame, scenario: dict[str, Any]) -> pd.DataFrame:
    """Apply one scenario's filters. No count-based gate, only stated filters."""
    out = frame
    if scenario["entry_3_5"]:
        out = out[(out["entry_speed"] >= ENTRY_LO) & (out["entry_speed"] <= ENTRY_HI)]
    if scenario["pass_only"]:
        out = out[out["play_type"] == "pass"]
    return out


# --------------------------------------------------------------- adjustment
def residualise(frame: pd.DataFrame) -> tuple[np.ndarray, float, int]:
    """OLS residuals of ``apex_retention`` on the four stated covariates.

    Returns ``(residuals, r_squared, n_parameters)``. ``n_parameters`` includes
    the intercept. No interval is computed here, so saturation is only a count.
    """
    y = frame["apex_retention"].to_numpy(float)
    X = np.column_stack([
        np.ones(len(frame)),
        frame["entry_speed"].to_numpy(float),
        frame["entry_speed"].to_numpy(float) ** 2,
        frame["abs_turn_angle"].to_numpy(float),
        frame["abs_turn_angle"].to_numpy(float) ** 2,
    ])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return resid, r2, int(X.shape[1])


def adjusted_icc(frame: pd.DataFrame) -> dict:
    """R-squared and ICC(1) on the residualised retention."""
    resid, r2, n_par = residualise(frame)
    out = icc1(resid, frame["nfl_id"].to_numpy())
    return {
        "r_squared": None if r2 is None else float(r2),
        "n_parameters": n_par,
        "covariates": list(ADJUSTMENT_COVARIATES),
        "players": int(out["n_groups"]),
        "turns": int(out["n_obs"]),
        "icc": out["icc"],
        "clamped": bool(out["clamped"]),
        "status": out["status"],
    }


# --------------------------------------------------------- derived quantities
def reliability_of_mean(k: float, rho: float) -> float:
    """Spearman-Brown reliability of a player mean: ``R = k*rho/(1+(k-1)*rho)``."""
    return (k * rho) / (1.0 + (k - 1.0) * rho)


def attenuation_ceiling(r: float) -> float:
    """``|r_obs| = sqrt(R) * |r_true|``; return ``sqrt(R)``."""
    return math.sqrt(r) if r >= 0 else math.nan


def rho_for_reliability(target: float, k: float) -> float:
    """``rho = R / (k - (k-1)R)`` — ICC needed for a target mean reliability."""
    return target / (k - (k - 1.0) * target)


def k_for_reliability(target: float, rho: float) -> float:
    """``k = R(1-rho) / (rho(1-R))`` — turns per player for a target reliability."""
    return target * (1.0 - rho) / (rho * (1.0 - target))


def clusters_for_effect(observed: float, se: float, clusters: int,
                        *, z_alpha: float = 1.959964, z_power: float = 0.841621) -> dict:
    """Clusters needed for 80% power to detect an already-observed effect.

    ``n_req = n_achieved * (SE_achieved / (|b| / (z_alpha + z_power)))^2``.
    This is a scaling of the achieved standard error, not a new fit.
    """
    if observed == 0 or se <= 0 or clusters <= 0:
        return {"clusters_required": None, "status": "not estimable"}
    mde_constant = z_alpha + z_power
    se_needed = abs(observed) / mde_constant
    factor = (se / se_needed) ** 2
    return {
        "observed_effect": float(observed),
        "se_achieved": float(se),
        "clusters_achieved": int(clusters),
        "mde_constant": float(mde_constant),
        "se_needed": float(se_needed),
        "scaling_factor": float(factor),
        "clusters_required": float(clusters * factor),
        "status": "ok",
    }


# ------------------------------------------------------------------- build
def build(turns: pd.DataFrame, context: pd.DataFrame) -> dict:
    """Assemble the whole reliability artefact. Pure: no file I/O."""
    _require_columns(turns, REQUIRED_TURN_COLUMNS, path="<turn frame>", what="turn frame")
    _require_columns(context, REQUIRED_CONTEXT_COLUMNS, path="<context frame>",
                     what="context frame")
    frame, diag = build_turn_frame(turns, context)

    scenarios: list[dict[str, Any]] = []
    by_name: dict[str, pd.DataFrame] = {}
    for scenario in SCENARIOS:
        sub = scenario_frame(frame, scenario)
        by_name[scenario["name"]] = sub
        ic = icc1(sub["apex_retention"].to_numpy(), sub["nfl_id"].to_numpy())
        scenarios.append({
            "name": scenario["name"],
            "rule": ("entry_speed in [3, 5]" if scenario["entry_3_5"] else "no speed filter")
                    + (" AND play_type == 'pass'" if scenario["pass_only"] else ""),
            "players": int(ic["n_groups"]),
            "turns": int(ic["n_obs"]),
            "kbar": float(ic["kbar_arithmetic"]) if ic["kbar_arithmetic"] is not None else None,
            "icc": ic["icc"],
            "clamped": bool(ic["clamped"]),
            "msb": ic["msb"],
            "msw": ic["msw"],
            "sigma2_a": ic["sigma2_a"],
            "sigma2_e": ic["sigma2_e"],
            "status": ic["status"],
        })

    adjustment = {name: adjusted_icc(by_name[name]) for name in ADJUSTMENT_SAMPLES}

    kbar_entry = next(s["kbar"] for s in scenarios if s["name"] == "entry_3_5")
    rho_entry = next(s["icc"] for s in scenarios if s["name"] == "entry_3_5")
    rho_entry_adj = adjustment["entry_3_5"]["icc"]
    rho_all = next(s["icc"] for s in scenarios if s["name"] == "all_band")

    k_target = 30.0
    n_players_entry = next(s["players"] for s in scenarios if s["name"] == "entry_3_5")
    turns_entry = next(s["turns"] for s in scenarios if s["name"] == "entry_3_5")

    r_at_target = reliability_of_mean(k_target, rho_entry_adj or 0.0)
    k_needed = k_for_reliability(0.70, rho_entry_adj or 0.0)

    # The geometric outcome estimate (D204): -0.38 yd per unit retention,
    # 95% CI [-1.08, +0.33], 84 player clusters. SE from the published interval.
    geo_b, geo_lo, geo_hi, geo_clusters = -0.38, -1.08, 0.33, 84
    geo_se = (geo_hi - geo_lo) / (2.0 * 1.959964)
    cluster_calc = clusters_for_effect(geo_b, geo_se, geo_clusters)

    derived = {
        "formulas": {
            "reliability_of_player_mean": "R = k*rho / (1 + (k-1)*rho)",
            "attenuation_ceiling": "|r_obs| = sqrt(R) * |r_true|",
            "rho_for_target_reliability": "rho = R / (k - (k-1)*R)",
            "k_for_target_reliability": "k = R(1-rho) / (rho(1-R))",
            "clusters_for_observed_effect":
                "n_req = n_achieved * (SE_achieved / (|b| / (z_.975 + z_.80)))^2",
        },
        "inputs": {
            "kbar_entry_3_5": kbar_entry,
            "rho_all_band": rho_all,
            "rho_entry_3_5": rho_entry,
            "rho_entry_3_5_adjusted": rho_entry_adj,
            "players_entry_3_5": n_players_entry,
            "turns_entry_3_5": turns_entry,
            "target_RELIABILITY": 0.70,
        },
        "reliability_of_player_mean": [
            {"k": k_target, "rho": rho_entry_adj, "R": r_at_target,
             "note": "at the adjusted 3-5 yd/s ICC and k=30"},
            {"k": k_target, "rho": rho_entry, "R": reliability_of_mean(k_target, rho_entry or 0.0),
             "note": "at the unadjusted 3-5 yd/s ICC and k=30"},
        ],
        "attenuation_ceiling_sqrt_R": {
            "at_adjusted_rho": attenuation_ceiling(r_at_target),
            "at_unadjusted_rho": attenuation_ceiling(
                reliability_of_mean(k_target, rho_entry or 0.0)),
        },
        "rho_needed_for_R_0_70_at_k30": rho_for_reliability(0.70, k_target),
        "k_needed_for_R_0_70_at_adjusted_rho": k_needed,
        "turns_needed_for_R_0_70": {
            "k_needed": k_needed,
            "players": n_players_entry,
            "turns_needed": k_needed * n_players_entry,
            "turns_held": turns_entry,
            "note": "order of magnitude: rho is a plug-in estimate with its own "
                    "uncertainty, so this target inherits it",
        },
        "clusters_for_observed_effect": {
            **cluster_calc,
            "source": "D204 geometric point estimate -0.38, 95% CI [-1.08, +0.33], "
                      "84 clusters; SE from the published interval",
        },
        "caveat": "rho is a plug-in point estimate with non-trivial uncertainty; "
                  "every target here is order-of-magnitude, not a design guarantee",
    }

    return {
        "provenance": PROVENANCE,
        "stamp": STAMP,
        "band": {
            "lo": BAND_LO, "hi": BAND_HI, "interval": "left-closed, right-open",
            "setting": SETTING, "position_group": POSITION_GROUP, "valid_only": True,
        },
        "scenario_rules": {
            "entry_speed_window_yd_s": [ENTRY_LO, ENTRY_HI],
            "pass_only": "play_type == 'pass' (includes scrambles, per D205)",
        },
        "sample": diag,
        "scenarios": scenarios,
        "adjustment": adjustment,
        "derived": derived,
        "estimator": {
            "name": "ICC(1), one-way random effects, unbalanced (ANOVA moments)",
            "implementation": "bdb27.fallback_validation.icc1 (imported, not duplicated)",
            "group": "nfl_id",
            "value": "apex_retention (residualised for the adjusted columns)",
        },
    }


# ----------------------------------------------------------------- manifest
def _display(path: Path, root: Path) -> str:
    try:
        return str(Path(path).absolute().relative_to(root))
    except ValueError:  # pragma: no cover - only for paths outside the repo
        return str(Path(path).absolute())


def write_manifest(out_dir: str | Path, inputs: Sequence[str | Path],
                   outputs: Sequence[str | Path], config: dict,
                   extra: dict | None = None) -> dict:
    """sha256 and size for every input and output, plus git HEAD (house style)."""
    out = Path(out_dir)
    root = Path(__file__).resolve().parents[2]
    manifest: dict[str, Any] = {
        "written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_head": git_head(root),
        "rule": "ICC(1) of apex_retention within player on the 30-60 deg DB band; "
                "four scenarios reported side by side; adjusted ICC on residuals of "
                "entry_speed, entry_speed^2, abs_turn_angle, abs_turn_angle^2; "
                "no gate, every count against its denominator",
        "provenance": PROVENANCE,
        "config": config,
        "inputs": {_display(Path(p), root): {"sha256": file_sha256(p),
                                             "bytes": Path(p).stat().st_size}
                   for p in inputs if Path(p).exists()},
        "outputs": {_display(Path(p), root): {"sha256": file_sha256(p),
                                              "bytes": Path(p).stat().st_size}
                    for p in outputs if Path(p).exists()},
    }
    if extra:
        manifest.update(extra)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                      encoding="utf-8")
    return manifest
