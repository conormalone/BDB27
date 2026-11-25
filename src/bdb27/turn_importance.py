"""Turn importance: what a 30-60 degree DB turn costs, and what it is worth.

Implements ``notes/turn-importance-spec.md`` (D223). Two analyses, one module,
one code path shared by the deliverable notebook and the test suite.

Test 1
    Descriptive mechanical-cost curve for the 58,664 valid DB game turns, by
    absolute turn-angle band. No model is fitted.

Test 2
    DB player-play panel: each cohort DB's own ``player_play`` row, exposure =
    the sharpest turn he had on that play. OLS / LPM with player fixed effects,
    down, distance, coverage, position and season controls, two-way clustered
    standard errors (player x game).

Test 2 is an **association**, labelled ``ASSOCIATION (STAKES), NOT CAUSE``: a
turn exists because the route broke, so turn occurrence is endogenous to the
play. Nothing here estimates the causal effect of turning.

``separation_at_pass_forward`` is never read, used, or referenced as a variable
by any function in this module (there is a static test for that). No receiver
matching and no attribution set is used.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# --------------------------------------------------------------------- bands
#: (low, high, key, display). Half-open except the last, which is open-ended.
#: Reproduces ``bdb27_full_game_bands.csv`` exactly on the FULL turn table.
BANDS: tuple[tuple[float, float | None, str, str], ...] = (
    (30.0, 60.0, "b30_60", "30–60°"),
    (60.0, 90.0, "b60_90", "60–90°"),
    (90.0, 120.0, "b90_120", "90–120°"),
    (120.0, 150.0, "b120_150", "120–150°"),
    (150.0, 180.0, "b150_180", "150–180°"),
    (180.0, None, "b180p", ">180°"),
)
BAND_KEYS: tuple[str, ...] = tuple(b[2] for b in BANDS)
NO_TURN = "none"

#: Test 1 measures: (column, label, unit, direction). ``direction`` is which way
#: means "more costly"; context rows carry ``-``.
MEASURES: tuple[tuple[str, str, str, str], ...] = (
    ("entry_speed", "Entry speed", "yd/s", "-"),
    ("apex_speed", "Apex speed", "yd/s", "-"),
    ("exit_speed", "Exit speed", "yd/s", "-"),
    ("speed_retention", "Speed retention (exit/entry)", "ratio", "lower"),
    ("apex_retention", "Apex retention (apex/entry)", "ratio", "lower"),
    ("peak_omega_deg_s", "Peak turn rate", "deg/s", "higher"),
    ("peak_lateral_accel_yd_s2", "Peak lateral acceleration", "yd/s²", "higher"),
    ("path_radius_yd", "Path radius (usable circle fits)", "yd", "lower"),
    ("regain_entry_speed_s", "Time to regain entry speed", "s", "higher"),
    ("duration_s", "Turn duration", "s", "-"),
)
COST_COLUMNS: tuple[str, ...] = (
    "peak_omega_deg_s",
    "peak_lateral_accel_yd_s2",
    "speed_retention",
    "apex_retention",
    "path_radius_yd",
)
MEASURE_DIR = {m[0]: m[3] for m in MEASURES}

#: Test 2 outcomes: (column, label, sample, kind).
OUTCOMES: tuple[tuple[str, str, str, str], ...] = (
    ("epa_allowed", "EPA allowed", "pass", "continuous"),
    ("pass_success_allowed", "Pass success allowed (completion)", "pass", "binary"),
    ("yards_allowed", "Yards allowed", "pass", "continuous"),
    ("explosive_allowed", "Explosive play allowed (15+ yd)", "pass", "binary"),
    ("yac_allowed", "Yards after catch allowed", "completion", "continuous"),
)
PASS_RESULTS = ("C", "I", "IN", "S")

#: Pre-declared materiality floors (spec §3.8). Labels only, never gates.
EPA_FLOOR = 0.10
PASS_SUCCESS_FLOOR = 0.03

PRIMARY_RHS: tuple[str, ...] = (
    "C(band, Treatment('none'))",
    "C(down)",
    "yards_to_go",
    "C(coverage)",
    "C(pos_group)",
    "C(season)",
    "C(nfl_id)",
)

LABEL = "ASSOCIATION (STAKES), NOT CAUSE"
PROVENANCE = "FULL"

#: Required columns, checked with a printed inventory before anything runs.
REQUIRED_TURN_COLUMNS = (
    "setting", "game_id", "play_id", "nfl_id", "position_group", "valid",
    "abs_turn_angle", "entry_speed", "apex_speed", "exit_speed",
    "speed_retention", "apex_retention", "peak_omega_deg_s",
    "peak_lateral_accel_yd_s2", "path_radius_yd", "circle_fit_usable",
    "regain_entry_speed_s", "regain_exhausted", "duration_s", "plant",
    "abs_turn_angle_smoother_min", "abs_turn_angle_smoother_max",
)
REQUIRED_PP_COLUMNS = (
    "game_id", "play_id", "nfl_id", "team_abbr", "possession_team",
    "down", "yards_to_go", "team_coverage_type", "lined_up_position",
    "pass_result", "yards_gained", "expected_points_added",
    "play_nullified_by_penalty", "yards_after_catch",
)
REQUIRED_GAMES_COLUMNS = ("game_id", "season")


# ------------------------------------------------------------------ helpers
class MissingInput(RuntimeError):
    """A required file or column is absent. The only condition that raises."""


def band_of(angle: float | None) -> str | None:
    """Band key for an absolute turn angle, or ``None`` outside the detector range."""
    if angle is None or not np.isfinite(angle):
        return None
    for low, high, key, _ in BANDS:
        if high is None:
            if angle >= low:
                return key
        elif low <= angle < high:
            return key
    return None


band_of_vec = np.vectorize(band_of, otypes=[object])


def band_label(key: str | None) -> str:
    if key is None or key == NO_TURN:
        return "No turn"
    for _, _, k, display in BANDS:
        if k == key:
            return display
    return str(key)


def connect(memory_limit: str = "2GB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}")
    return con


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise MissingInput(
            f"required input not found: {path}\n"
            f"  paths tried: {path}\n"
            "  set the notebook's path variables to the folder holding the file."
        )
    return path


def _columns(con: duckdb.DuckDBPyConnection, path: Path) -> set[str]:
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')"
        ).fetchall()
    except Exception as exc:  # unreadable file is a legitimate stop
        raise MissingInput(f"required input unreadable: {path} ({exc})") from exc
    return {r[0] for r in rows}


def _require_columns(path: Path, present: set[str], required: tuple[str, ...]) -> None:
    missing = [c for c in required if c not in present]
    if missing:
        raise MissingInput(
            f"required column(s) missing from {path}: {', '.join(missing)}\n"
            f"  columns found ({len(present)}): {', '.join(sorted(present))}"
        )


# --------------------------------------------------------------- test 1 data
def load_turns(con: duckdb.DuckDBPyConnection, turns_path: Path) -> pd.DataFrame:
    """Valid DB game turns with the Test 1 columns."""
    cols = ", ".join(REQUIRED_TURN_COLUMNS)
    df = con.execute(
        f"""SELECT {cols}
            FROM read_parquet('{turns_path.as_posix()}')
            WHERE setting = 'game' AND position_group = 'DB' AND valid"""
    ).df()
    df["band"] = band_of_vec(df["abs_turn_angle"].to_numpy(dtype=float))
    return df


def _stats(values: pd.Series) -> dict:
    v = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return dict(n_nonnull=0, median=np.nan, q25=np.nan, q75=np.nan,
                    mean=np.nan, sd=np.nan)
    return dict(
        n_nonnull=int(v.size),
        median=float(np.median(v)),
        q25=float(np.percentile(v, 25)),
        q75=float(np.percentile(v, 75)),
        mean=float(np.mean(v)),
        sd=float(np.std(v, ddof=1)) if v.size > 1 else np.nan,
    )


def cost_table(turns: pd.DataFrame) -> pd.DataFrame:
    """Long Test 1 table: one row per (band, measure) with its own denominator."""
    n_total = len(turns)
    rows: list[dict] = []
    for low, high, key, display in BANDS:
        part = turns[turns["band"] == key]
        share = len(part) / n_total if n_total else np.nan
        for column, label, unit, direction in MEASURES:
            if column not in part.columns:
                continue
            usable = part
            note = ""
            if column == "path_radius_yd":
                usable = part[part["circle_fit_usable"].fillna(False)]
                note = (f"{len(usable):,} usable circle fits of {len(part):,}; "
                        f"{int((~part['circle_fit_usable'].fillna(False)).sum()):,} "
                        "flagged unusable")
            elif column == "regain_entry_speed_s":
                exhausted = int(part["regain_exhausted"].fillna(False).sum())
                usable = part[~part["regain_exhausted"].fillna(False)]
                note = (f"{len(usable):,} regained of {len(part):,}; "
                        f"{exhausted:,} exhausted the regain window")
            st = _stats(usable[column])
            rows.append(dict(
                provenance=PROVENANCE,
                band_key=key, band=display, band_low=low, band_high=high,
                n_turns=int(len(part)), share_of_turns=share,
                measure=column, measure_label=label, unit=unit,
                direction=direction, denominator_note=note,
                **st,
            ))
    # plant share is a proportion, reported as its own measure row
    for low, high, key, display in BANDS:
        part = turns[turns["band"] == key]
        rows.append(dict(
            provenance=PROVENANCE,
            band_key=key, band=display, band_low=low, band_high=high,
            n_turns=int(len(part)),
            share_of_turns=len(part) / n_total if n_total else np.nan,
            measure="plant_share", measure_label="Plant share",
            unit="share", direction="-", denominator_note="",
            n_nonnull=int(part["plant"].notna().sum()),
            median=np.nan, q25=np.nan, q75=np.nan,
            mean=float(part["plant"].mean()) if len(part) else np.nan, sd=np.nan,
        ))
    return pd.DataFrame(rows)


def cost_wide(turns: pd.DataFrame) -> pd.DataFrame:
    """One row per band, the headline Test 1 measures."""
    n_total = len(turns)
    rows = []
    for low, high, key, display in BANDS:
        part = turns[turns["band"] == key]
        radius = part[part["circle_fit_usable"].fillna(False)]
        regain = part[~part["regain_exhausted"].fillna(False)]
        rows.append(dict(
            provenance=PROVENANCE, band=display, band_key=key,
            n_turns=len(part), share_of_turns=len(part) / n_total,
            entry_speed_median=float(part["entry_speed"].median()),
            apex_speed_median=float(part["apex_speed"].median()),
            exit_speed_median=float(part["exit_speed"].median()),
            speed_retention_median=float(part["speed_retention"].median()),
            apex_retention_median=float(part["apex_retention"].median()),
            peak_omega_median=float(part["peak_omega_deg_s"].median()),
            peak_lat_accel_median=float(part["peak_lateral_accel_yd_s2"].median()),
            path_radius_median=float(radius["path_radius_yd"].median()),
            path_radius_n=int(radius["path_radius_yd"].notna().sum()),
            regain_median=float(regain["regain_entry_speed_s"].median()),
            regain_n=int(regain["regain_entry_speed_s"].notna().sum()),
            regain_exhausted_n=int(part["regain_exhausted"].fillna(False).sum()),
            duration_median=float(part["duration_s"].median()),
            plant_share=float(part["plant"].mean()),
            plant_n=int(part["plant"].notna().sum()),
        ))
    return pd.DataFrame(rows)


def smoother_sensitivity(turns: pd.DataFrame) -> pd.DataFrame:
    """30-60 share and headline medians under recorded / min / max angle."""
    rows = []
    for name, column in (
        ("recorded", "abs_turn_angle"),
        ("smoother_min", "abs_turn_angle_smoother_min"),
        ("smoother_max", "abs_turn_angle_smoother_max"),
    ):
        band = band_of_vec(turns[column].to_numpy(dtype=float))
        in_band = band == "b30_60"
        part = turns[in_band]
        rows.append(dict(
            provenance=PROVENANCE, angle_assignment=name,
            n_turns=int(in_band.sum()), n_total=int(len(turns)),
            share_30_60=float(in_band.mean()),
            peak_lat_accel_median=float(part["peak_lateral_accel_yd_s2"].median()),
            peak_omega_median=float(part["peak_omega_deg_s"].median()),
            speed_retention_median=float(part["speed_retention"].median()),
            plant_share=float(part["plant"].mean()),
        ))
    return pd.DataFrame(rows)


def spearman_table(turns: pd.DataFrame) -> pd.DataFrame:
    """Spearman rho of each measure against the recorded absolute angle."""
    from scipy import stats as sps

    rows = []
    angle = turns["abs_turn_angle"].to_numpy(dtype=float)
    usable = (turns["circle_fit_usable"].astype(bool).to_numpy()
              if "circle_fit_usable" in turns.columns
              else np.ones(len(turns), dtype=bool))
    for column, label, unit, direction in MEASURES:
        v = pd.to_numeric(turns[column], errors="coerce").to_numpy(dtype=float)
        mask = np.isfinite(angle) & np.isfinite(v)
        if column == "path_radius_yd":
            # spec §2.2: radius is only defined on usable circle fits
            mask &= usable
        if mask.sum() < 3:
            rows.append(dict(provenance=PROVENANCE, measure=column,
                             measure_label=label, n_nonnull=int(mask.sum()),
                             rho=np.nan, p=np.nan, direction=direction))
            continue
        rho, p = sps.spearmanr(angle[mask], v[mask])
        rows.append(dict(provenance=PROVENANCE, measure=column,
                         measure_label=label, n_nonnull=int(mask.sum()),
                         rho=float(rho), p=float(p), direction=direction))
    return pd.DataFrame(rows)


def rank_table(turns: pd.DataFrame) -> pd.DataFrame:
    """Rank bands 1-6 on each cost measure (1 = gentlest)."""
    wide = cost_wide(turns).set_index("band_key")
    colmap = {
        "peak_omega_deg_s": "peak_omega_median",
        "peak_lateral_accel_yd_s2": "peak_lat_accel_median",
        "speed_retention": "speed_retention_median",
        "apex_retention": "apex_retention_median",
        "path_radius_yd": "path_radius_median",
    }
    rows = []
    for measure, column in colmap.items():
        direction = MEASURE_DIR[measure]
        values = wide[column]
        # rank 1 = gentlest: ascending for "higher is costlier", descending otherwise
        ranks = values.rank(ascending=(direction == "higher"), method="min")
        for key in BAND_KEYS:
            rank_value = ranks.loc[key]
            rows.append(dict(
                provenance=PROVENANCE, measure=measure, band_key=key,
                band=band_label(key), value=float(values.loc[key]),
                rank=int(rank_value) if np.isfinite(rank_value) else None,
                direction=direction,
            ))
    return pd.DataFrame(rows)


def severity_label(rank_df: pd.DataFrame) -> dict:
    """Apply the pre-declared labelling rule (spec §2.4)."""
    sub = rank_df[rank_df["band_key"] == "b30_60"]
    ranks = [r for r in sub["rank"].tolist() if r is not None and np.isfinite(r)]
    mild = sum(r <= 2 for r in ranks)
    demanding = sum(r >= 5 for r in ranks)
    if mild >= 4:
        label = "physically mild"
    elif demanding >= 4:
        label = "physically demanding"
    else:
        label = "intermediate"
    return dict(
        label=label, n_measures=len(ranks), ranks=dict(zip(sub["measure"], ranks)),
        n_rank_le_2=int(mild), n_rank_ge_5=int(demanding),
    )


# --------------------------------------------------------------- test 2 data
def build_panel(
    con: duckdb.DuckDBPyConnection,
    turns_path: Path,
    pp_path: Path,
    games_path: Path,
) -> pd.DataFrame:
    """DB player-play panel with exposure, outcomes and controls."""
    any_flags = ",\n".join(
        f"MAX(CASE WHEN abs_turn_angle >= {low} AND "
        + (f"abs_turn_angle < {high}" if high is not None else "TRUE")
        + f" THEN 1 ELSE 0 END) AS any_{key}"
        for low, high, key, _ in BANDS
    )
    sql = f"""
    WITH cohort AS (
        SELECT DISTINCT nfl_id
        FROM read_parquet('{turns_path.as_posix()}')
        WHERE setting = 'game' AND position_group = 'DB' AND valid
    ),
    games_with_turns AS (
        SELECT DISTINCT game_id
        FROM read_parquet('{turns_path.as_posix()}')
        WHERE setting = 'game' AND position_group = 'DB' AND valid
    ),
    tt AS (
        SELECT game_id, play_id, nfl_id,
               COUNT(*) AS n_turns,
               MAX(abs_turn_angle) AS max_abs_turn_angle,
               {any_flags}
        FROM read_parquet('{turns_path.as_posix()}')
        WHERE setting = 'game' AND position_group = 'DB' AND valid
        GROUP BY 1, 2, 3
    ),
    yac AS (
        SELECT game_id, play_id,
               MAX(TRY_CAST(yards_after_catch AS DOUBLE)) AS yac_play,
               COUNT(yards_after_catch) AS n_yac_rows
        FROM read_parquet('{pp_path.as_posix()}')
        GROUP BY 1, 2
    )
    SELECT
        p.game_id, p.play_id, p.nfl_id, p.team_abbr, p.possession_team,
        p.down, p.yards_to_go, p.team_coverage_type, p.lined_up_position,
        p.pass_result, p.yards_gained, p.expected_points_added,
        p.play_nullified_by_penalty, p.quarter, p.game_clock,
        g.season,
        t.n_turns, t.max_abs_turn_angle,
        {', '.join('t.any_' + k for k in BAND_KEYS)},
        y.yac_play, y.n_yac_rows
    FROM read_parquet('{pp_path.as_posix()}') p
    JOIN (SELECT game_id, MAX(season) AS season FROM
          read_parquet('{games_path.as_posix()}') GROUP BY 1) g USING (game_id)
    JOIN cohort c ON c.nfl_id = p.nfl_id
    LEFT JOIN tt t
      ON t.game_id = p.game_id AND t.play_id = p.play_id AND t.nfl_id = p.nfl_id
    LEFT JOIN yac y
      ON y.game_id = p.game_id AND y.play_id = p.play_id
    WHERE p.game_id IN (SELECT game_id FROM games_with_turns)
    ORDER BY p.game_id, p.play_id, p.nfl_id
    """
    df = con.execute(sql).df()
    df["band"] = band_of_vec(df["max_abs_turn_angle"].to_numpy(dtype=float))
    df["band"] = df["band"].fillna(NO_TURN)
    return add_outcomes_and_controls(df)


def add_outcomes_and_controls(df: pd.DataFrame) -> pd.DataFrame:
    """Derive outcomes, control levels and sample flags. Pure, testable."""
    df = df.copy()
    for col in ("expected_points_added", "yards_gained", "yards_to_go",
                "max_abs_turn_angle", "yac_play"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # DuckDB LEFT JOINs produce nullable Int64; patsy cannot test NA in those.
    for key in BAND_KEYS:
        column = f"any_{key}"
        if column in df.columns:
            df[column] = (pd.to_numeric(df[column], errors="coerce")
                          .fillna(0.0).astype(float))
        else:
            df[column] = 0.0
    if "n_turns" in df.columns:
        df["n_turns"] = (pd.to_numeric(df["n_turns"], errors="coerce")
                         .fillna(0).astype(int))
    else:
        df["n_turns"] = 0

    # outcomes, all from the DB's own row except YAC which is play-level
    df["epa_allowed"] = df["expected_points_added"]
    df["yards_allowed"] = df["yards_gained"]
    df["pass_success_allowed"] = (df["pass_result"] == "C").astype(float)
    df["explosive_allowed"] = (df["yards_gained"] >= 15).astype(float).where(
        df["yards_gained"].notna())
    df["yac_allowed"] = df["yac_play"]

    pr = df["pass_result"]
    df["is_pass"] = pr.isin(PASS_RESULTS)
    df["is_completion"] = pr == "C"
    df["is_penalty"] = df["play_nullified_by_penalty"].astype(str).eq("Y")

    # controls
    df["down"] = pd.to_numeric(df["down"], errors="coerce").fillna(-1).astype(int).astype(str)
    cov = df["team_coverage_type"].astype("object")
    cov = cov.where(~cov.isin(["NA", ""]), np.nan)
    df["coverage"] = cov.fillna("COVERAGE_MISSING")
    pos = df["lined_up_position"].astype("object")
    pos = pos.where(~pos.isin(["NA", ""]), np.nan)
    mapping = {
        "CB": "CB", "SLOT_CB": "SLOT_CB",
        "HIGH_SAFETY": "SAFETY", "BOX_SAFETY": "SAFETY",
    }
    df["pos_group"] = pos.map(mapping).fillna("POSITION_OTHER")
    df["season"] = pd.to_numeric(df["season"], errors="coerce").astype("Int64").astype(str)
    df["player_key"] = df["nfl_id"].astype(str)
    df["play_key"] = df["game_id"].astype(str) + "-" + df["play_id"].astype(str)
    return df


def panel_counts(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for key in (NO_TURN, *BAND_KEYS):
        part = panel[panel["band"] == key]
        rows.append(dict(
            provenance=PROVENANCE, band_key=key, band=band_label(key),
            n_player_plays=len(part),
            share_of_player_plays=len(part) / len(panel) if len(panel) else np.nan,
            n_plays=part["play_key"].nunique(),
            n_players=part["nfl_id"].nunique(),
            n_games=part["game_id"].nunique(),
        ))
    return pd.DataFrame(rows)


def band_means(panel: pd.DataFrame, sample: str = "pass") -> pd.DataFrame:
    """Raw outcome means by band, with n and denominator."""
    if sample == "pass":
        base = panel[panel["is_pass"] & ~panel["is_penalty"]]
    elif sample == "all":
        base = panel[~panel["is_penalty"]]
    elif sample == "completion":
        base = panel[panel["is_completion"] & ~panel["is_penalty"]]
    else:
        raise ValueError(sample)
    rows = []
    for key in (NO_TURN, *BAND_KEYS):
        part = base[base["band"] == key]
        row = dict(provenance=PROVENANCE, sample=sample, band_key=key,
                   band=band_label(key), n_rows=len(part),
                   n_plays=part["play_key"].nunique(),
                   n_players=part["nfl_id"].nunique())
        for col in ("epa_allowed", "pass_success_allowed", "yards_allowed",
                    "explosive_allowed", "yac_allowed"):
            v = pd.to_numeric(part[col], errors="coerce")
            row[f"{col}_mean"] = float(v.mean()) if v.notna().any() else np.nan
            row[f"{col}_n"] = int(v.notna().sum())
        rows.append(row)
    return pd.DataFrame(rows)


def position_mix(panel: pd.DataFrame) -> pd.DataFrame:
    """Who carries each band? A diagnostic, never a gate."""
    tab = (panel.groupby(["band", "pos_group"], observed=False)
           .size().rename("n_rows").reset_index())
    totals = tab.groupby("band")["n_rows"].transform("sum")
    tab["share_within_band"] = tab["n_rows"] / totals
    tab["band"] = tab["band"].map(band_label)
    return tab


# ------------------------------------------------------------- estimation
@dataclass
class FitResult:
    params: pd.Series
    bse: pd.Series
    cov: pd.DataFrame
    resid: np.ndarray
    nobs: int
    rank: int
    used: pd.DataFrame = field(default_factory=pd.DataFrame)
    clusters: dict = field(default_factory=dict)

    def ci(self, term: str, alpha: float = 0.05) -> tuple[float, float, float, float]:
        from scipy import stats as sps
        b = float(self.params[term])
        se = float(self.bse[term])
        crit = float(sps.norm.ppf(1 - alpha / 2))
        return b, se, b - crit * se, b + crit * se


def _cluster_meat(scores: np.ndarray, groups: np.ndarray) -> np.ndarray:
    order = np.argsort(groups, kind="stable")
    s = scores[order]
    g = groups[order]
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    sums = np.add.reduceat(s, starts, axis=0)
    return sums.T @ sums


def cluster_cov(
    x: np.ndarray,
    resid: np.ndarray,
    groups: np.ndarray,
    xtx_inv: np.ndarray,
    rank: int,
) -> tuple[np.ndarray, int]:
    """CR1 cluster covariance for a dense design matrix."""
    scores = x * resid[:, None]
    meat = _cluster_meat(scores, groups)
    n = x.shape[0]
    g = len(np.unique(groups))
    correction = (g / max(g - 1, 1)) * ((n - 1) / max(n - rank, 1))
    return correction * (xtx_inv @ meat @ xtx_inv), g


def two_way_cluster_cov(
    x: np.ndarray,
    resid: np.ndarray,
    groups_a: np.ndarray,
    groups_b: np.ndarray,
    xtx_inv: np.ndarray,
    rank: int,
) -> tuple[np.ndarray, dict]:
    """Cameron-Gelman-Miller two-way cluster covariance."""
    va, ga = cluster_cov(x, resid, groups_a, xtx_inv, rank)
    vb, gb = cluster_cov(x, resid, groups_b, xtx_inv, rank)
    inter = pd.factorize(
        pd.Series(groups_a).astype(str) + "|" + pd.Series(groups_b).astype(str)
    )[0]
    vab, gab = cluster_cov(x, resid, inter, xtx_inv, rank)
    v = va + vb - vab
    # numerical guard: the subtraction can produce tiny negative diagonal entries
    diag = np.diag(v).copy()
    if np.any(diag < 0):
        v = v + np.diag(np.maximum(-diag, 0))
    return v, dict(n_a=ga, n_b=gb, n_ab=gab)


def fit_model(
    df: pd.DataFrame,
    outcome: str,
    rhs: tuple[str, ...] | list[str] = PRIMARY_RHS,
    cluster: tuple[str, ...] = ("player_key", "game_id"),
) -> FitResult:
    """OLS/LPM with fixed effects, clustered by one or two keys."""
    from patsy import dmatrices

    data = df.copy()
    data = data[pd.to_numeric(data[outcome], errors="coerce").notna()]
    formula = f"{outcome} ~ " + " + ".join(rhs)
    y, x = dmatrices(formula, data, return_type="dataframe", NA_action="drop")
    xmat = np.asarray(x, dtype=float)
    yvec = np.asarray(y).ravel()

    xtx_inv = np.linalg.pinv(xmat.T @ xmat)
    beta = xtx_inv @ xmat.T @ yvec
    resid = yvec - xmat @ beta
    rank = int(np.linalg.matrix_rank(xmat.T @ xmat))
    used = data.loc[y.index]

    if len(cluster) == 1:
        cov, g = cluster_cov(xmat, resid, used[cluster[0]].to_numpy(), xtx_inv, rank)
        clusters = {cluster[0]: g}
    elif len(cluster) == 2:
        cov, cinfo = two_way_cluster_cov(
            xmat, resid,
            used[cluster[0]].to_numpy(), used[cluster[1]].to_numpy(),
            xtx_inv, rank,
        )
        clusters = {cluster[0]: cinfo["n_a"], cluster[1]: cinfo["n_b"],
                    f"{cluster[0]}x{cluster[1]}": cinfo["n_ab"]}
    else:
        raise ValueError("cluster must have one or two keys")

    return FitResult(
        params=pd.Series(beta, index=x.columns),
        bse=pd.Series(np.sqrt(np.clip(np.diag(cov), 0, None)), index=x.columns),
        cov=pd.DataFrame(cov, index=x.columns, columns=x.columns),
        resid=resid, nobs=len(yvec), rank=rank, used=used, clusters=clusters,
    )


def band_terms(fit: FitResult, prefix: str = "band") -> dict[str, str]:
    """Map band key -> patsy term name, reading the ``[T.key]`` suffix only."""
    out = {}
    for term in fit.params.index:
        if prefix not in term:
            continue
        match = re.search(r"\[T\.([^\]]+)\]", term)
        if match and match.group(1) in BAND_KEYS:
            out[match.group(1)] = term
    return out


def _rows_for_terms(
    fit: FitResult, terms: dict[str, str], spec: str, outcome: str,
    sample: str,
) -> list[dict]:
    """One row per band term. Counts are the rows the fit actually used."""
    n_rows = len(fit.used)
    n_plays = int(fit.used["play_key"].nunique())
    n_players = int(fit.used["nfl_id"].nunique())
    rows = []
    for key, term in terms.items():
        b, se, lo, hi = fit.ci(term)
        from scipy import stats as sps
        z = b / se if se > 0 else np.nan
        p = float(2 * (1 - sps.norm.cdf(abs(z)))) if np.isfinite(z) else np.nan
        rows.append(dict(
            provenance=PROVENANCE, label=LABEL, spec=spec, outcome=outcome,
            sample=sample, term=key, band=band_label(key),
            coef=b, se=se, ci_lo=lo, ci_hi=hi, p=p,
            n_rows=n_rows, n_plays=n_plays, n_players=n_players,
            n_clusters=json.dumps(fit.clusters),
        ))
    return rows


def _counts(df: pd.DataFrame) -> tuple[int, int, int]:
    return len(df), df["play_key"].nunique(), df["nfl_id"].nunique()


def _prep(panel: pd.DataFrame, sample: str, include_penalty: bool = False,
          drop_no_turn: bool = False, winsor: bool = False) -> pd.DataFrame:
    df = panel
    if sample == "pass":
        df = df[df["is_pass"]]
    elif sample == "completion":
        df = df[df["is_completion"]]
    elif sample != "all":
        raise ValueError(sample)
    if not include_penalty:
        df = df[~df["is_penalty"]]
    if drop_no_turn:
        df = df[df["band"] != NO_TURN]
    df = df.copy()
    if winsor:
        v = pd.to_numeric(df["epa_allowed"], errors="coerce")
        lo, hi = np.nanpercentile(v, [1, 99])
        df["epa_allowed"] = v.clip(lo, hi)
    return df


def run_test2(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """All pre-declared specifications. Returns (estimates, gradient, labels)."""
    estimates: list[dict] = []
    labels: dict = {}

    # ---- primary outcome block -------------------------------------------
    for spec, outcome, sample in (
        ("primary_epa", "epa_allowed", "pass"),
        ("primary_pass_success", "pass_success_allowed", "pass"),
        ("primary_yards", "yards_allowed", "pass"),
        ("primary_explosive", "explosive_allowed", "pass"),
        ("secondary_yac", "yac_allowed", "completion"),
    ):
        df = _prep(panel, sample)
        fit = fit_model(df, outcome, PRIMARY_RHS, ("player_key", "game_id"))
        terms = band_terms(fit)
        estimates += _rows_for_terms(fit, terms, spec, outcome, sample)
        if spec in ("primary_epa", "primary_pass_success"):
            if "b30_60" not in terms:
                # no-gate rule: a band absent from this input is reported, not fatal
                labels[spec] = dict(
                    coef=np.nan, se=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    n=len(fit.used),
                    players=int(fit.used["nfl_id"].nunique()),
                    clusters=fit.clusters,
                    decision="NOT ESTIMABLE (30-60 band absent from input)",
                )
            else:
                b, se, lo, hi = fit.ci(terms["b30_60"])
                labels[spec] = dict(
                    coef=b, se=se, ci_lo=lo, ci_hi=hi, n=len(fit.used),
                    players=int(fit.used["nfl_id"].nunique()),
                    clusters=fit.clusters, decision=_decision(spec, b, lo, hi),
                )

    # 1. within-turn sample, reference = 30-60
    df = _prep(panel, "pass", drop_no_turn=True)
    rhs = tuple(["C(band, Treatment('b30_60'))"]
                + [t for t in PRIMARY_RHS if not t.startswith("C(band")])
    fit = fit_model(df, "epa_allowed", rhs, ("player_key", "game_id"))
    terms = {k: v for k, v in band_terms(fit).items() if k != "b30_60"}
    estimates += _rows_for_terms(fit, terms, "s1_within_turn_epa",
                                 "epa_allowed", "pass_within_turn")

    # 2. all plays
    for spec, outcome in (("s2_all_plays_epa", "epa_allowed"),
                          ("s2_all_plays_yards", "yards_allowed")):
        df = _prep(panel, "all")
        fit = fit_model(df, outcome, PRIMARY_RHS, ("player_key", "game_id"))
        estimates += _rows_for_terms(fit, band_terms(fit), spec, outcome, "all")

    # 3. include penalty-nullified plays
    df = _prep(panel, "pass", include_penalty=True)
    fit = fit_model(df, "epa_allowed", PRIMARY_RHS, ("player_key", "game_id"))
    estimates += _rows_for_terms(fit, band_terms(fit), "s3_include_penalty",
                                 "epa_allowed", "pass")

    # 4. any-turn-in-band indicators
    df = _prep(panel, "pass")
    rhs_any = ("any_b30_60", "any_b60_90", "any_b90_120", "any_b120_150",
               "any_b150_180", "any_b180p",
               "C(down)", "yards_to_go", "C(coverage)", "C(pos_group)",
               "C(season)", "C(nfl_id)")
    fit = fit_model(df, "epa_allowed", rhs_any, ("player_key", "game_id"))
    n_rows = len(fit.used)
    n_plays = int(fit.used["play_key"].nunique())
    n_players = int(fit.used["nfl_id"].nunique())
    for key in BAND_KEYS:
        b, se, lo, hi = fit.ci(f"any_{key}")
        estimates.append(dict(
            provenance=PROVENANCE, label=LABEL, spec="s4_any_turn_epa",
            outcome="epa_allowed", sample="pass", term=key,
            band=band_label(key), coef=b, se=se, ci_lo=lo, ci_hi=hi,
            p=_pvalue(b, se), n_rows=n_rows, n_plays=n_plays,
            n_players=n_players, n_clusters=json.dumps(fit.clusters),
        ))

    # 5. one row per play, sharpest DB turn on the play
    df = _prep(panel, "pass")
    df = (df.sort_values(["play_key", "max_abs_turn_angle", "nfl_id"],
                         ascending=[True, False, True], na_position="last")
            .drop_duplicates("play_key", keep="first"))
    rhs_play = ("C(band, Treatment('none'))", "C(down)", "yards_to_go",
                "C(coverage)", "C(pos_group)", "C(season)")
    fit = fit_model(df, "epa_allowed", rhs_play, ("game_id",))
    estimates += _rows_for_terms(fit, band_terms(fit), "s5_one_row_per_play",
                                 "epa_allowed", "pass_play_level")

    # 6. winsorised EPA
    df = _prep(panel, "pass", winsor=True)
    fit = fit_model(df, "epa_allowed", PRIMARY_RHS, ("player_key", "game_id"))
    estimates += _rows_for_terms(fit, band_terms(fit), "s6_winsorised_epa",
                                 "epa_allowed", "pass")

    # 7. one-way clusters for the primary coefficient
    df = _prep(panel, "pass")
    for spec, keys in (("s7_cluster_player", ("player_key",)),
                       ("s7_cluster_play", ("play_key",)),
                       ("s7_cluster_game", ("game_id",))):
        fit = fit_model(df, "epa_allowed", PRIMARY_RHS, keys)
        estimates += _rows_for_terms(fit, band_terms(fit), spec,
                                     "epa_allowed", "pass")

    # 8. completions only
    df = _prep(panel, "completion")
    fit = fit_model(df, "epa_allowed", PRIMARY_RHS, ("player_key", "game_id"))
    estimates += _rows_for_terms(fit, band_terms(fit), "s8_completions_epa",
                                 "epa_allowed", "completion")

    est = pd.DataFrame(estimates)
    grad = _gradient(panel)
    return est, grad, labels


def _pvalue(b: float, se: float) -> float:
    if not np.isfinite(se) or se <= 0:
        return float("nan")
    from scipy import stats as sps
    return float(2 * (1 - sps.norm.cdf(abs(b / se))))


def _decision(spec: str, b: float, lo: float, hi: float) -> str:
    if spec == "primary_epa":
        floor = EPA_FLOOR
    else:
        floor = PASS_SUCCESS_FLOOR
    if lo > 0:
        return "STAKES CONFIRMED" if b >= floor else "DIRECTIONAL ONLY"
    if hi < 0:
        return "OPPOSITE SIGN"
    return "NO DETECTABLE DIFFERENCE"


def _gradient(panel: pd.DataFrame) -> pd.DataFrame:
    """Difference between each sharper band and 30-60, delta-method CI."""
    df = _prep(panel, "pass", drop_no_turn=True)
    rhs = tuple(
        ["C(band, Treatment('b30_60'))"] +
        [t for t in PRIMARY_RHS if not t.startswith("C(band")]
    )
    fit = fit_model(df, "epa_allowed", rhs, ("player_key", "game_id"))
    rows = []
    for key in BAND_KEYS:
        if key == "b30_60":
            continue
        term = f"C(band, Treatment('b30_60'))[T.{key}]"
        if term not in fit.params.index:
            continue  # band absent on this input: reported by _rows_for_terms absence
        # Treatment('b30_60') makes 30-60 the baseline: its coefficient is zero
        # by construction and does not appear in params.
        diff = float(fit.params[term])
        var = float(fit.cov.loc[term, term])
        se = float(np.sqrt(max(var, 0.0)))
        from scipy import stats as sps
        crit = float(sps.norm.ppf(0.975))
        rows.append(dict(
            provenance=PROVENANCE, label=LABEL, outcome="epa_allowed",
            sample="pass_within_turn", term=f"{key}_minus_b30_60",
            band=band_label(key), coef=diff, se=se,
            ci_lo=diff - crit * se, ci_hi=diff + crit * se,
            n_rows=len(fit.used),
            n_plays=int(fit.used["play_key"].nunique()),
            n_players=int(fit.used["nfl_id"].nunique()),
            n_games=int(fit.used["game_id"].nunique()),
            n_clusters=json.dumps(fit.clusters),
        ))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ runner
def input_inventory(paths: dict[str, Path]) -> pd.DataFrame:
    rows = []
    for role, path in paths.items():
        rows.append(dict(
            role=role, path=str(path), exists=path.exists(),
            bytes=path.stat().st_size if path.exists() else None,
            sha256=_sha256(path) if path.exists() else None,
        ))
    return pd.DataFrame(rows)


def git_head(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            text=True, encoding="utf-8",
        ).strip()
    except Exception:
        return "unknown"


def run(
    turns_full: Path,
    pp_path: Path,
    games_path: Path,
    out_dir: Path,
    turns_sample: Path | None = None,
) -> dict:
    """Run both tests, print a log, write every artefact. No gates."""
    log: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        log.append(str(text))

    out_dir.mkdir(parents=True, exist_ok=True)
    con = connect()

    paths = {"turns_full": Path(turns_full), "player_play": Path(pp_path),
             "games": Path(games_path)}
    if turns_sample is not None:
        paths["turns_sample"] = Path(turns_sample)
    for p in paths.values():
        _require_file(p)

    say("=" * 78)
    say(f"BDB27 turn importance — {PROVENANCE} — {datetime.now(timezone.utc).isoformat()}")
    say(f"label: {LABEL}")
    say("=" * 78)

    # input inventory
    inv = input_inventory(paths)
    say("\nINPUTS")
    for _, r in inv.iterrows():
        say(f"  {r['role']:<14} {r['path']}")
        say(f"  {'':<14} exists={r['exists']} bytes={r['bytes']} sha256={str(r['sha256'])[:16]}…")

    turns_cols = _columns(con, Path(turns_full))
    _require_columns(Path(turns_full), turns_cols, REQUIRED_TURN_COLUMNS)
    pp_cols = _columns(con, Path(pp_path))
    _require_columns(Path(pp_path), pp_cols, REQUIRED_PP_COLUMNS)
    games_cols = _columns(con, Path(games_path))
    _require_columns(Path(games_path), games_cols, REQUIRED_GAMES_COLUMNS)

    # ---------------- TEST 1 ------------------------------------------------
    turns = load_turns(con, Path(turns_full))
    n_rows_all = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{Path(turns_full).as_posix()}') "
        "WHERE setting='game' AND position_group='DB'"
    ).fetchone()[0]
    say("\n" + "=" * 78)
    say("TEST 1 — WHAT THE TURN COSTS")
    say("=" * 78)
    say(f"turn rows (setting=game, position_group=DB): {n_rows_all:,}")
    say(f"  valid (analysed): {len(turns):,}  of  {n_rows_all:,}")
    say(f"  invalid dropped:  {n_rows_all - len(turns):,}  of  {n_rows_all:,}")
    say(f"players: {turns['nfl_id'].nunique():,}   games: {turns['game_id'].nunique():,}")

    band_counts = (turns.groupby("band", observed=False).size()
                   .rename("n").reset_index())
    band_counts["share"] = band_counts["n"] / len(turns)
    say("\nband counts (denominator 58,664 valid DB turns):")
    for key in BAND_KEYS:
        row = band_counts[band_counts["band"] == key]
        n = int(row["n"].iloc[0]) if len(row) else 0
        say(f"  {band_label(key):<10} {n:>7,}  {n / len(turns):>7.2%}")

    wide = cost_wide(turns)
    say("\nheadline medians by band:")
    say(wide[["band", "n_turns", "entry_speed_median", "apex_speed_median",
              "exit_speed_median", "speed_retention_median", "peak_omega_median",
              "peak_lat_accel_median", "path_radius_median", "plant_share"]]
        .round(3).to_string(index=False))

    long = cost_table(turns)
    long.to_csv(out_dir / "test1_cost_by_band.csv", index=False, encoding="utf-8")
    wide.to_csv(out_dir / "test1_bands_wide.csv", index=False, encoding="utf-8")
    smoother = smoother_sensitivity(turns)
    smoother.to_csv(out_dir / "test1_smoother_sensitivity.csv", index=False,
                    encoding="utf-8")
    spear = spearman_table(turns)
    spear.to_csv(out_dir / "test1_spearman.csv", index=False, encoding="utf-8")
    ranks = rank_table(turns)
    ranks.to_csv(out_dir / "test1_rank_table.csv", index=False, encoding="utf-8")
    sev = severity_label(ranks)

    say("\nspearman rho against recorded angle:")
    say(spear[["measure_label", "n_nonnull", "rho", "p"]].round(4)
        .to_string(index=False))
    say("\nrank table (1 = gentlest):")
    say(ranks.pivot(index="band", columns="measure", values="rank")
        .to_string())
    say(f"\n30–60° severity label: {sev['label']}  "
        f"(ranks {sev['ranks']}; ≤2 on {sev['n_rank_le_2']}/5, ≥5 on {sev['n_rank_ge_5']}/5)")

    # 30-45 vs 45-60
    sub = turns[(turns["abs_turn_angle"] >= 30) & (turns["abs_turn_angle"] < 60)].copy()
    sub["half"] = np.where(sub["abs_turn_angle"] < 45, "30–45°", "45–60°")
    say("\n30–60 band split (the proposed drill sits at 45°, a boundary value):")
    for half, part in sub.groupby("half"):
        say(f"  {half}  n={len(part):,} of {len(sub):,}  "
            f"peak_omega={part['peak_omega_deg_s'].median():.1f}  "
            f"peak_lat_accel={part['peak_lateral_accel_yd_s2'].median():.2f}  "
            f"speed_retention={part['speed_retention'].median():.3f}  "
            f"plant_share={part['plant'].mean():.1%}")

    say("\nsmoother sensitivity (30–60 band):")
    say(smoother[["angle_assignment", "n_turns", "share_30_60",
                  "peak_lat_accel_median", "peak_omega_median", "plant_share"]]
        .round(3).to_string(index=False))

    # ---------------- TEST 2 ------------------------------------------------
    say("\n" + "=" * 78)
    say("TEST 2 — WHAT THE TURN IS WORTH  " + LABEL)
    say("=" * 78)
    panel = build_panel(con, Path(turns_full), Path(pp_path), Path(games_path))
    say(f"DB player-play rows: {len(panel):,}")
    say(f"  distinct plays: {panel['play_key'].nunique():,}")
    say(f"  players: {panel['nfl_id'].nunique():,}")
    say(f"  games: {panel['game_id'].nunique():,}")
    counts = panel_counts(panel)
    counts.to_csv(out_dir / "test2_band_counts.csv", index=False, encoding="utf-8")
    say("\nexposure counts (max-angle band; denominator 84,225 DB player-plays):")
    say(counts[["band", "n_player_plays", "share_of_player_plays", "n_plays",
                "n_players"]].round(4).to_string(index=False))

    means = band_means(panel, "pass")
    means.to_csv(out_dir / "test2_band_means.csv", index=False, encoding="utf-8")
    mix = position_mix(panel)
    mix.to_csv(out_dir / "test2_position_mix.csv", index=False, encoding="utf-8")
    say("\nraw means, pass plays (no controls):")
    say(means[["band", "n_rows", "n_plays", "epa_allowed_mean",
               "pass_success_allowed_mean", "yards_allowed_mean",
               "explosive_allowed_mean"]].round(3).to_string(index=False))
    say("\nposition mix by band (row share within band):")
    say(mix.pivot(index="band", columns="pos_group",
                  values="share_within_band").fillna(0).round(3).to_string())

    est, grad, labels = run_test2(panel)
    est.to_csv(out_dir / "test2_estimates.csv", index=False, encoding="utf-8")
    grad.to_csv(out_dir / "test2_gradient.csv", index=False, encoding="utf-8")

    say("\nprimary estimates (30–60° vs no turn):")
    for spec, lab in labels.items():
        say(f"  {spec:<22} beta={lab['coef']:+.4f}  "
            f"95% CI [{lab['ci_lo']:+.4f}, {lab['ci_hi']:+.4f}]  "
            f"n={lab['n']:,}  players={lab['players']:,}  → {lab['decision']}")

    say("\nfull band contrast, primary EPA model:")
    p = est[(est["spec"] == "primary_epa")]
    say(p[["band", "coef", "se", "ci_lo", "ci_hi", "n_rows"]].round(4)
        .to_string(index=False))
    say("\ngradient vs 30–60° (delta method, within-turn):")
    say(grad[["band", "coef", "ci_lo", "ci_hi", "n_rows"]].round(4)
        .to_string(index=False))

    better = grad[grad["ci_lo"] > 0]
    say(f"\nbands whose EPA contrast exceeds 30–60° with a CI excluding 0: "
        f"{len(better)} of {len(grad)}")

    # ---------------- summary ----------------------------------------------
    summary = dict(
        provenance=PROVENANCE, label=LABEL,
        generated_utc=datetime.now(timezone.utc).isoformat(),
        git_head=git_head(Path(__file__).resolve().parents[2]),
        python=platform.python_version(),
        inputs={r["role"]: dict(path=r["path"], bytes=r["bytes"],
                                sha256=r["sha256"])
                for _, r in inv.iterrows()},
        test1=dict(
            n_turn_rows=int(n_rows_all), n_valid=int(len(turns)),
            n_players=int(turns["nfl_id"].nunique()),
            n_games=int(turns["game_id"].nunique()),
            severity_label=sev["label"], ranks=sev["ranks"],
            band_shares={band_label(k): float(
                (turns["band"] == k).mean()) for k in BAND_KEYS},
        ),
        test2=dict(
            n_player_plays=int(len(panel)),
            n_plays=int(panel["play_key"].nunique()),
            n_players=int(panel["nfl_id"].nunique()),
            n_games=int(panel["game_id"].nunique()),
            n_no_turn=int((panel["band"] == NO_TURN).sum()),
            n_pass_rows=int((panel["is_pass"] & ~panel["is_penalty"]).sum()),
            decisions={k: {kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
                           for kk, vv in v.items() if kk != "clusters"}
                       for k, v in labels.items()},
            n_yac_completions=int(
                (panel["is_completion"] & panel["yac_allowed"].notna()).sum()),
        ),
    )
    with (out_dir / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    (out_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")

    say(f"\nwrote artefacts to {out_dir}")
    return dict(turns=turns, panel=panel, cost=long, wide=wide, counts=counts,
                estimates=est, gradient=grad, labels=labels, summary=summary)


def main() -> None:  # pragma: no cover - convenience only
    root = Path(__file__).resolve().parents[2]
    run(root / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet",
        root / "data/interim/parquet/player_play.parquet",
        root / "data/interim/parquet/games.parquet",
        root / "outputs/turn_importance",
        root / "outputs/turns/turns.parquet")


if __name__ == "__main__":  # pragma: no cover
    main()
