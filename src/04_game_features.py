"""Phase 4 — in-game effort features + per-player ramp/decay slopes (BDB27).

Streams the game tracking frames with DuckDB over Parquet (never ``pd.read_csv``),
joins play context (``player_play.csv``) and game context (``games.csv``), and
aggregates to per-``(game_id, play_id, nfl_id)``. From that frame it builds, per
player, a **shrunken per-player slope** of high-intensity effort intensity on
cumulative in-game load using the **same model-based reliability formula + stop
threshold** as Phase 3 (``R = tau2 / (tau2 + mean_i(SE_i^2))``,
``model.reliability_stop_threshold``).

Reframe (D25, 2026-10-11). The pre-registered fatigue-slope -> game-decay link is
GATED OFF (D20: combine fatigue-slope reliability 0.0). This module therefore
produces the *game side* of Conor's pivot: a per-player **in-game ramping** slope
(effort intensity vs cumulative load; a positive slope = ramping up). The link is
EXPLORATORY (D24: warm-up de-confounding not robust).

Effort intensity (per play) = **threshold-sum** (matches panel L2 HMLD construction):
``hi_speed_dist_yd + accel_dist_yd`` where a frame is "high speed" iff ``s > hs_thr``
and "high accel" iff ``|a| > ac_thr`` (``thresholds.json``). Thresholds are FROZEN in
``outputs/thresholds.json``; in ``--mode full`` their provenance MUST be ``FULL``.

Outputs (``outputs/game/``): ``game_player_slopes.{parquet,csv}``,
``game_features_summary.csv``, ``game_model_fixed_effects.csv``,
``game_reliability.csv``, ``SUMMARY.md``, ``PROVENANCE.txt``. Sample outputs carry
``UNVALIDATED SAMPLE OUTPUT``.

Run:
    python src/04_game_features.py --mode sample
    python src/04_game_features.py --mode full
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    append_run_log,
    connect,
    get_logger,
    load_config,
    peak_ram_mb,
    provenance,
    resolve,
    seed_everything,
    timed_stage,
)

_HERE = Path(__file__).resolve().parent
CORE_PATH = _HERE / "03_combine_model.py"

TRACKING_COLS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a", "dis", "o", "dir", "event"]
PLAY_CONTEXT_COLS = [
    "game_id", "play_id", "nfl_id", "down", "yards_to_go", "quarter", "game_clock",
    "pre_snap_home_score", "pre_snap_visitor_score", "possession_team",
    "lined_up_position", "route_ran", "pass_result",
]
GAMES_COLS = ["game_id", "season", "week"]

SLOPE_COLS = [
    "provenance", "nfl_id", "n_games", "n_plays", "n_effort_plays", "ramp_slope_raw",
    "ramp_slope_shrunk", "ramp_slope_se", "ci_lo", "ci_hi", "shrinkage_weight",
    "method", "reliability", "meets_min_snaps", "early_output_z", "mean_intensity_z",
]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #
def load_thresholds(cfg: dict[str, Any], mode: str, logger) -> dict[str, Any]:
    """Read ``outputs/thresholds.json``; assert FULL provenance in full mode."""
    path = resolve(cfg, "outputs", cfg["game"]["thresholds_file"])
    if not path.exists():
        raise FileNotFoundError(f"missing frozen thresholds: {path} (run src/00_thresholds.py first)")
    with open(path, "r", encoding="utf-8") as fh:
        th = json.load(fh)
    if mode == "full" and not str(th.get("provenance", "")).startswith("FULL"):
        raise AssertionError(
            f"thresholds provenance is {th.get('provenance')!r}, expected FULL in full mode "
            "(TASK.md stop rule: never set thresholds from the sample)"
        )
    logger.info("thresholds provenance=%s hs=%.4f ac=%.4f min_snaps=%d",
                th.get("provenance"), float(th["high_speed_threshold_yds"]),
                float(th["accel_threshold_yds2"]), int(th["min_snaps"]))
    return th


# --------------------------------------------------------------------------- #
# Parquet caches (streamed by DuckDB)
# --------------------------------------------------------------------------- #
def _cache_game_tracking(cfg: dict[str, Any], mode: str, force: bool, logger) -> Path:
    """Convert the game-tracking CSV(s) to a single Parquet cache (explicit columns)."""
    dst = resolve(cfg, "parquet") / f"game_tracking_{mode}.parquet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        logger.info("parquet cache hit: %s", dst.name)
        return dst
    if mode == "sample":
        srcs = [resolve(cfg, "raw", cfg["files"]["game_tracking_sample"])]
    else:
        srcs = [resolve(cfg, "raw", n) for n in (cfg["files"].get("game_tracking_full") or [])]
        if not srcs:
            raise FileNotFoundError("config.files.game_tracking_full is empty (full mode)")
    for p in srcs:
        if not p.exists():
            raise FileNotFoundError(f"missing game tracking source: {p}")
    con = connect(threads=int(cfg.get("runtime", {}).get("duckdb_threads", 1)))
    lst = ", ".join(f"'{p}'" for p in srcs)
    cols = ", ".join(f'"{c}"' for c in TRACKING_COLS)
    con.execute(
        f"COPY (SELECT {cols} FROM read_csv_auto([{lst}], header=true, union_by_name=true, "
        f"nullstr=['NA',''])) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    logger.info("cached game tracking -> %s", dst.name)
    return dst


def _cache_csv(cfg: dict[str, Any], key: str, dst_name: str, cols: list[str],
               force: bool, logger) -> Path:
    """Convert a raw CSV (selected columns) to a Parquet cache."""
    dst = resolve(cfg, "parquet") / dst_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        return dst
    src = resolve(cfg, "raw", cfg["files"][key])
    if not src.exists():
        raise FileNotFoundError(f"missing source CSV: {src}")
    con = connect(threads=int(cfg.get("runtime", {}).get("duckdb_threads", 1)))
    colsql = ", ".join(f'"{c}"' for c in cols)
    con.execute(
        f"COPY (SELECT {colsql} FROM read_csv_auto('{src}', header=true, nullstr=['NA',''])) "
        f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    logger.info("cached %s -> %s", key, dst.name)
    return dst


# --------------------------------------------------------------------------- #
# Frame build (all heavy lifting in one streaming DuckDB query)
# --------------------------------------------------------------------------- #
def build_play_frame(cfg: dict[str, Any], th: dict[str, Any], tracking_pq: Path,
                     play_pq: Path, games_pq: Path) -> pd.DataFrame:
    """Per-``(game_id, play_id, nfl_id)`` effort features + context + load windows.

    Everything (per-frame thresholds, per-play aggregation, play/game join, drive
    detection, cumulative distance, time-since-rest, snaps-in-drive) is computed by
    DuckDB over Parquet — only the small per-play-per-player result enters pandas.
    """
    hs = float(th["high_speed_threshold_yds"])
    ac = float(th["accel_threshold_yds2"])
    con = connect(threads=int(cfg.get("runtime", {}).get("duckdb_threads", 1)))
    sql = f"""
    WITH frames AS (
        SELECT game_id, play_id, nfl_id, time, s, a,
               COALESCE(dis, 0.0) AS step_dis,
               CASE WHEN s IS NOT NULL AND s > {hs} THEN 1 ELSE 0 END AS hi_speed,
               CASE WHEN a IS NOT NULL AND abs(a) > {ac} THEN 1 ELSE 0 END AS hi_accel
        FROM read_parquet('{tracking_pq}')
        WHERE nfl_id IS NOT NULL AND play_id IS NOT NULL
    ),
    ppa AS (
        SELECT game_id, play_id, nfl_id,
               min(time) AS play_start, max(time) AS play_end,
               count(*) AS n_frames,
               sum(step_dis) AS play_dist_yd,
               sum(CASE WHEN hi_speed=1 THEN step_dis ELSE 0 END) AS hi_speed_dist_yd,
               sum(CASE WHEN hi_accel=1 THEN step_dis ELSE 0 END) AS accel_dist_yd,
               max(s) AS max_speed_yds
        FROM frames GROUP BY 1,2,3
    ),
    ppa2 AS (
        SELECT *, (hi_speed_dist_yd + accel_dist_yd) AS effort_intensity_yd FROM ppa
    ),
    ctx AS (
        SELECT CAST(game_id AS BIGINT) AS game_id, CAST(play_id AS BIGINT) AS play_id,
               CAST(nfl_id AS BIGINT) AS nfl_id, down, yards_to_go, quarter, game_clock,
               pre_snap_home_score, pre_snap_visitor_score, possession_team,
               lined_up_position, route_ran, pass_result
        FROM read_parquet('{play_pq}')
    ),
    gm AS (
        SELECT CAST(game_id AS BIGINT) AS game_id, season, week FROM read_parquet('{games_pq}')
    ),
    j AS (
        SELECT a.*, c.down, c.yards_to_go, c.quarter, c.possession_team, c.lined_up_position,
               CASE WHEN c.pass_result IS NOT NULL THEN 1 ELSE 0 END AS is_pass_play,
               (c.pre_snap_home_score - c.pre_snap_visitor_score) AS score_margin,
               g.season, g.week
        FROM ppa2 a
        LEFT JOIN ctx c ON a.game_id=c.game_id AND a.play_id=c.play_id AND a.nfl_id=c.nfl_id
        LEFT JOIN gm g ON a.game_id=g.game_id
    ),
    -- play-level possession timeline (for drive detection)
    playtl AS (
        SELECT game_id, play_id, min(play_start) AS play_start,
               any_value(possession_team) AS poss
        FROM j GROUP BY 1,2
    ),
    pdrive AS (
        SELECT game_id, play_id,
               sum(CASE WHEN poss IS DISTINCT FROM lag_poss THEN 1 ELSE 0 END)
                 OVER (PARTITION BY game_id ORDER BY play_start, play_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS drive_id
        FROM (SELECT *, lag(poss) OVER (PARTITION BY game_id ORDER BY play_start, play_id) AS lag_poss
              FROM playtl) t
    ),
    jd AS (
        SELECT j.*, d.drive_id FROM j LEFT JOIN pdrive d USING (game_id, play_id)
    )
    SELECT *,
      sum(play_dist_yd) OVER (PARTITION BY game_id, nfl_id
            ORDER BY play_start, play_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_dist_yd,
      date_diff('millisecond', lag(play_end) OVER (PARTITION BY game_id, nfl_id ORDER BY play_start, play_id), play_start)/1000.0 AS time_since_rest_s,
      row_number() OVER (PARTITION BY game_id, nfl_id, drive_id ORDER BY play_start, play_id) AS snaps_in_drive
    FROM jd
    """
    df = con.execute(sql).fetchdf()
    # downcast
    for c in ["game_id", "play_id", "nfl_id", "is_pass_play", "snaps_in_drive", "drive_id", "season", "week", "quarter", "down"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int32")
    for c in ["play_dist_yd", "hi_speed_dist_yd", "accel_dist_yd", "effort_intensity_yd",
              "max_speed_yds", "cum_dist_yd", "time_since_rest_s", "yards_to_go", "score_margin"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    df = df.rename(columns={"game_id": "game_id", "nfl_id": "nfl_id"})
    return df


# --------------------------------------------------------------------------- #
# Standardisation + per-player slope
# --------------------------------------------------------------------------- #
def standardize_within_player(df: pd.DataFrame, col: str, out: str) -> pd.DataFrame:
    """Z-score ``col`` within each ``nfl_id`` (ddof=0); zero-variance players -> NaN."""
    z = np.full(len(df), np.nan, dtype=np.float64)
    for pid, g in df.groupby("nfl_id", sort=True):
        v = g[col].to_numpy(dtype=np.float64)
        vf = v[np.isfinite(v)]
        if vf.size < 2:
            continue
        mu = float(vf.mean())
        sd = float(vf.std(ddof=0))
        if sd > 0:
            z[g.index.to_numpy()] = (v - mu) / sd
        else:
            z[g.index.to_numpy()] = 0.0
    df = df.copy()
    df[out] = z
    return df


def _design(df: pd.DataFrame, load_col: str, control_cols: list[str]) -> tuple[np.ndarray, int]:
    """Intercept + standardised controls + load column; slope index = last column."""
    cols = []
    C = np.ones((len(df), 1), dtype=np.float64)
    for c in control_cols:
        v = df[c].to_numpy(dtype=np.float64)
        m = np.nanmean(v) if np.isfinite(v).any() else 0.0
        v = np.where(np.isfinite(v), v, m)
        sd = np.nanstd(v)
        if sd > 0:
            v = (v - m) / sd
        cols.append(v)
    load = df[load_col].to_numpy(dtype=np.float64)
    cols.append(load)
    X = np.column_stack([C] + [c.reshape(-1, 1) for c in cols]) if cols else C
    return X, X.shape[1] - 1


def per_player_slopes_multi(df: pd.DataFrame, cfg: dict[str, Any], load_col: str,
                            outcome_col: str, control_cols: list[str],
                            min_snaps: int) -> pd.DataFrame:
    """Per-player OLS slope of ``outcome`` on ``load`` (net of controls) + SE.

    Requires ``>= min_snaps`` rows, load variation, and outcome variation.
    Returns ``nfl_id, n, slope, se``.
    """
    rows: list[dict[str, Any]] = []
    for pid, g in df.groupby("nfl_id", sort=True):
        g = g[np.isfinite(g[outcome_col].to_numpy(dtype=np.float64))]
        if len(g) < int(min_snaps):
            continue
        x = g[load_col].to_numpy(dtype=np.float64)
        y = g[outcome_col].to_numpy(dtype=np.float64)
        if np.unique(x).size < 2 or np.var(y) == 0.0:
            continue
        X, idx = _design(g, load_col, control_cols)
        try:
            xtx_inv = np.linalg.inv(X.T @ X)
        except np.linalg.LinAlgError:
            continue
        beta = xtx_inv @ (X.T @ y)
        resid = y - X @ beta
        dof = len(y) - X.shape[1]
        if dof <= 0:
            continue
        s2 = float(resid @ resid / dof)
        var = s2 * float(xtx_inv[idx, idx])
        if not np.isfinite(var) or var < 0:
            continue
        rows.append({"nfl_id": int(pid), "n": int(len(g)),
                     "slope": float(beta[idx]), "se": float(math.sqrt(var))})
    out = pd.DataFrame(rows, columns=["nfl_id", "n", "slope", "se"])
    if len(out) == 0:
        out = out.astype({"nfl_id": "int64", "n": "int64", "slope": "float64", "se": "float64"})
    return out


def fit_ramp_slopes(df: pd.DataFrame, cfg: dict[str, Any], core) -> dict[str, Any]:
    """EB-shrunk per-player ramp slopes + reliability (Phase-3 machinery, re-used)."""
    G = cfg["game"]
    m = cfg["model"]
    load_col = str(G["load"])
    outcome_col = str(G["outcome"])
    control_cols = list(G["controls"])
    min_snaps = int(cfg["game"]["thresholds"]["min_snaps"])

    slopes = per_player_slopes_multi(df, cfg, load_col, outcome_col, control_cols, min_snaps)
    tau2 = core.dl_tau2(slopes)
    mu, mu_se, k = core.inv_var_mean(slopes)
    mean_se2 = float(np.mean(slopes["se"].to_numpy() ** 2)) if len(slopes) else float("nan")
    rel = float(tau2 / (tau2 + mean_se2)) if (np.isfinite(mean_se2) and (tau2 + mean_se2) > 0) else 0.0
    if k < int(G["reliability_min_players"]):
        rel = 0.0
    stop = bool(not (rel >= float(G["reliability_stop_threshold"])))
    if not np.isfinite(mu):
        mu = 0.0
    sh = core.eb_shrink(slopes, tau2, mu)
    return {"slopes": slopes, "shrunken": sh, "tau2": float(tau2), "mu": float(mu),
            "mu_se": float(mu_se) if np.isfinite(mu_se) else float("nan"),
            "mean_se2": mean_se2, "reliability": rel, "n_players": int(k),
            "stop_triggered": stop, "min_snaps": min_snaps,
            "estimator": "per_player_eb", "method": "per_player_eb"}


def population_fixed_effects(df: pd.DataFrame, cfg: dict[str, Any], core) -> pd.DataFrame:
    """Pooled OLS of outcome on load + controls (population effect table)."""
    G = cfg["game"]
    import statsmodels.api as sm

    load_col = str(G["load"])
    cols = [load_col] + list(G["controls"])
    sub = df.dropna(subset=[str(G["outcome"])])
    y = sub[str(G["outcome"])].to_numpy(dtype=np.float64)
    data: dict[str, np.ndarray] = {}
    for c in cols:
        v = sub[c].to_numpy(dtype=np.float64)
        m = float(np.nanmean(v)) if np.isfinite(v).any() else 0.0
        data[c] = np.where(np.isfinite(v), v, m)
    X = sm.add_constant(pd.DataFrame(data, columns=cols), has_constant="add")
    res = sm.OLS(y, X.astype(np.float64)).fit()
    return pd.DataFrame({
        "term": list(res.params.index),
        "estimate": res.params.to_numpy(dtype=np.float64),
        "se": res.bse.to_numpy(dtype=np.float64),
        "t": res.tvalues.to_numpy(dtype=np.float64),
        "p": res.pvalues.to_numpy(dtype=np.float64),
    })


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
def build_slope_table(df: pd.DataFrame, fit: dict[str, Any], cfg: dict[str, Any],
                      prov: str) -> pd.DataFrame:
    """Assemble the Phase-5 handoff table (EB-shrunk ramp slopes)."""
    G = cfg["game"]
    level = float(G["player_slope_ci_level"])
    from scipy import stats as st
    zc = float(st.norm.ppf(1.0 - (1.0 - level) / 2.0))

    agg = df.groupby("nfl_id", sort=True).agg(
        n_games=("game_id", "nunique"),
        n_plays=("play_id", "size"),
    ).reset_index()
    eff = df[df["effort_intensity_yd"].fillna(0) > 0].groupby("nfl_id").size().rename("n_effort_plays")
    eo = df.groupby("nfl_id")["early_output_z"].mean().rename("early_output_z")
    mi = df.groupby("nfl_id")[str(G["outcome"])].mean().rename("mean_intensity_z")

    base = agg.merge(eff.reset_index(), on="nfl_id", how="left")
    base = base.merge(eo.reset_index(), on="nfl_id", how="left")
    base = base.merge(mi.reset_index(), on="nfl_id", how="left")
    sh = fit["shrunken"][["nfl_id", "n", "slope", "se", "slope_shrunk", "posterior_se", "shrinkage_weight"]]
    base["nfl_id"] = pd.to_numeric(base["nfl_id"], errors="coerce").astype("int64")
    sh = sh.copy()
    sh["nfl_id"] = pd.to_numeric(sh["nfl_id"], errors="coerce").astype("int64")
    base = base.merge(sh, on="nfl_id", how="left")

    out = pd.DataFrame({
        "provenance": prov,
        "nfl_id": base["nfl_id"].astype("Int64"),
        "n_games": base["n_games"].fillna(0).astype("Int64"),
        "n_plays": base["n_plays"].fillna(0).astype("Int64"),
        "n_effort_plays": base["n_effort_plays"].fillna(0).astype("Int64"),
        "ramp_slope_raw": base["slope"],
        "ramp_slope_shrunk": base["slope_shrunk"],
        "ramp_slope_se": base["se"],
        "ci_lo": base["slope_shrunk"] - zc * base["se"],
        "ci_hi": base["slope_shrunk"] + zc * base["se"],
        "shrinkage_weight": base["shrinkage_weight"],
        "method": fit["method"],
        "reliability": float(fit["reliability"]),
        "meets_min_snaps": base["n_plays"].fillna(0) >= int(fit["min_snaps"]),
        "early_output_z": base["early_output_z"],
        "mean_intensity_z": base["mean_intensity_z"],
    })
    return out.sort_values("nfl_id", kind="mergesort").reset_index(drop=True)


def _reliability_table(fit: dict[str, Any], cfg: dict[str, Any], prov: str) -> pd.DataFrame:
    G = cfg["game"]
    return pd.DataFrame([{
        "provenance": prov,
        "slope_metric": str(G["outcome"]),
        "source": "per_player_eb",
        "n_players": fit["n_players"],
        "tau2": fit["tau2"],
        "mean_se2": fit["mean_se2"],
        "reliability": fit["reliability"],
        "reliability_met": bool(fit["reliability"] >= float(G["reliability_stop_threshold"])),
        "stop_triggered": fit["stop_triggered"],
        "min_snaps": fit["min_snaps"],
    }])


def _summary_md(fit: dict[str, Any], slope_tbl: pd.DataFrame, summary: dict[str, Any],
                cfg: dict[str, Any], prov: str) -> str:
    G = cfg["game"]
    lines = [
        "# Phase 4 — in-game effort features & ramp slopes (SUMMARY)",
        "",
        f"**Provenance:** `{prov}`  ",
        f"**Outcome:** `{G['outcome']}` · **Load:** `{G['load']}` · "
        f"**controls:** `{', '.join(G['controls'])}`  ",
        f"**Min snaps:** {fit['min_snaps']}",
        "",
        "## Reliability (same formula as Phase 3: `tau2/(tau2+mean_i(SE_i^2))`)",
        "",
        f"- players with a usable slope: **{fit['n_players']}**",
        f"- tau2 = {fit['tau2']:.6g}, mean_i(SE_i^2) = {fit['mean_se2']:.6g}",
        f"- **reliability = {fit['reliability']:.4f}** "
        f"(threshold {cfg['game']['reliability_stop_threshold']}) -> "
        f"{'STOP (null)' if fit['stop_triggered'] else '> threshold'}",
        "",
        "## Frame summary",
        "",
    ]
    for k in sorted(summary.keys()):
        lines.append(f"- `{k}`: {summary[k]}")
    n_ok = int(slope_tbl["meets_min_snaps"].sum()) if len(slope_tbl) else 0
    lines += [
        "",
        "## Per-player slopes",
        "",
        f"- players in table: {len(slope_tbl)}; meeting min-snaps rule: {n_ok}",
        "",
        "## Note",
        "",
        "Reframe (D25): the pre-registered fatigue-slope -> decay link is GATED OFF (D20). "
        "The viable link is reliable combine **warm-up** phenotypes -> in-game **ramping** "
        "(this table's `ramp_slope_shrunk`). Exploratory (D24: de-confounding not robust).",
        "",
    ]
    if prov == "UNVALIDATED SAMPLE OUTPUT":
        lines.insert(1, "\n> ⚠️ **UNVALIDATED SAMPLE OUTPUT** — never interpret or tune against this.\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(mode: str, force: bool) -> int:
    cfg = load_config()
    logger = get_logger()
    seed_everything(int(cfg["seed"]))
    prov = provenance(cfg, mode)
    G = cfg["game"]
    out_dir = resolve(cfg, "outputs", G["out_subdir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 72)
    logger.info("BDB27 Phase 4 in-game features | mode=%s | provenance=%s", mode, prov)
    logger.info("=" * 72)

    slope_path = out_dir / "game_player_slopes.parquet"
    if slope_path.exists() and not force:
        logger.info("checkpoint hit: %s exists (use --force)", slope_path.name)
        append_run_log(cfg, "04_game_features:checkpoint", mode, 0.0, peak_ram_mb(), "",
                       {"skipped": True, "reason": "game_player_slopes.parquet exists"})
        return 0

    core = _load_module("combine03", CORE_PATH)

    with timed_stage(cfg, "04_game_features:thresholds", mode):
        th = load_thresholds(cfg, mode, logger)

    with timed_stage(cfg, "04_game_features:cache", mode):
        tracking_pq = _cache_game_tracking(cfg, mode, force, logger)
        play_pq = _cache_csv(cfg, "player_play", "player_play.parquet", PLAY_CONTEXT_COLS, force, logger)
        games_pq = _cache_csv(cfg, "games", "games.parquet", GAMES_COLS, force, logger)

    with timed_stage(cfg, "04_game_features:build", mode):
        df = build_play_frame(cfg, th, tracking_pq, play_pq, games_pq)
        df = standardize_within_player(df, "effort_intensity_yd", str(G["outcome"]))
        # early-game output: mean intensity_z over the player's first N plays (by time)
        df = df.sort_values(["nfl_id", "game_id", "play_start", "play_id"], kind="mergesort").reset_index(drop=True)
        df["_rank"] = df.groupby("nfl_id").cumcount()
        eo = df[df["_rank"] < int(G["early_plays_n"])].groupby("nfl_id")[str(G["outcome"])].mean()
        df["early_output_z"] = df["nfl_id"].map(eo)
        df = df.drop(columns=["_rank"])

    logger.info("per-play frame rows=%d players=%d games=%d",
                len(df), df["nfl_id"].nunique(), df["game_id"].nunique())

    with timed_stage(cfg, "04_game_features:slopes", mode):
        fit = fit_ramp_slopes(df, cfg, core)
        slope_tbl = build_slope_table(df, fit, cfg, prov)
        rel_tbl = _reliability_table(fit, cfg, prov)

    with timed_stage(cfg, "04_game_features:popfe", mode):
        try:
            fe = population_fixed_effects(df, cfg, core)
        except Exception as exc:  # noqa: BLE001
            logger.warning("population fixed effects failed: %s", exc)
            fe = pd.DataFrame(columns=["term", "estimate", "se", "t", "p"])

    with timed_stage(cfg, "04_game_features:write", mode):
        # per-player slopes (parquet + csv)
        sdf = slope_tbl.copy()
        for c in ["n_games", "n_plays", "n_effort_plays"]:
            sdf[c] = sdf[c].astype("Int64")
        sdf.to_parquet(slope_path, index=False)
        sdf.to_csv(out_dir / "game_player_slopes.csv", index=False)
        # fixed effects
        fe2 = fe.copy()
        fe2.insert(0, "provenance", prov)
        fe2.to_csv(out_dir / "game_model_fixed_effects.csv", index=False)
        # reliability
        rel_tbl.to_csv(out_dir / "game_reliability.csv", index=False)
        # summary table
        summary = {
            "n_rows_per_play_player": int(len(df)),
            "n_players": int(df["nfl_id"].nunique()),
            "n_games": int(df["game_id"].nunique()),
            "n_plays": int(df["play_id"].nunique()),
            "mean_effort_intensity_yd": float(np.nanmean(df["effort_intensity_yd"].to_numpy(dtype=np.float64))),
            "mean_plays_per_player": float(df.groupby("nfl_id").size().mean()),
            "high_speed_threshold_yds": float(th["high_speed_threshold_yds"]),
            "accel_threshold_yds2": float(th["accel_threshold_yds2"]),
            "thresholds_provenance": str(th.get("provenance")),
            "min_snaps": int(fit["min_snaps"]),
            "n_players_with_slope": int(fit["n_players"]),
            "reliability": float(fit["reliability"]),
            "stop_triggered": bool(fit["stop_triggered"]),
        }
        summ = pd.DataFrame([{"provenance": prov, "key": k, "value": v} for k, v in summary.items()])
        summ.to_csv(out_dir / "game_features_summary.csv", index=False)

    with timed_stage(cfg, "04_game_features:docs", mode):
        (out_dir / "SUMMARY.md").write_text(_summary_md(fit, slope_tbl, summary, cfg, prov), encoding="utf-8")
        (out_dir / "PROVENANCE.txt").write_text(
            f"provenance: {prov}\nmode: {mode}\nstage: 04_game_features\n"
            f"thresholds_provenance: {th.get('provenance')}\n"
            f"thresholds_file: {resolve(cfg, 'outputs', cfg['game']['thresholds_file'])}\n",
            encoding="utf-8")

    logger.info("wrote %d-player slope table (rel=%.4f, stop=%s)",
                len(slope_tbl), fit["reliability"], fit["stop_triggered"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="BDB27 Phase 4 — in-game features")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="rebuild outputs (ignore checkpoint)")
    args = ap.parse_args()
    cfg = load_config()
    mode = args.mode or str(cfg["mode"]["default"])
    return run(mode, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
