"""Phase 4–5 — the game demand map, Turn Coverage and the Cut Card data.

Binding spec: ``TASK.md`` @ ``52015cd``, sections **4. Demand map** and
**5. Coverage and the Cut Card**.

What this module does
---------------------
* **Demand map.** Per position group, the distribution of *game* turns over the
  configured ``bins.entry_speed × bins.turn_angle`` grid, restricted to the
  snap types that position actually plays (``demand.snap_types``). Frequency is
  the default weighting; for DL an **outcome-weighted** map (reps with quick
  pressure or a short time to pressure) and an **unblocked-excluded**
  sensitivity are built beside it. Bin intervals are a **bootstrap over
  players** — the player is the resampling unit, because turns inside one
  player are not independent.
* **Turn Coverage.** For every Combine player, the demand-weighted share of the
  group's game-turn cells that fall inside his Combine-derived feasible region,
  reported separately for **observed** and **extrapolated** cells and
  **combined**. Intervals are a *marginal* Bernoulli draw over the persisted
  per-cell ``inside_share`` from Stage 03; Stage 03 persists shares, not joint
  draws, so this is explicitly an approximation (``joint_bootstrap_draws=false``).
* **Cut gaps.** The highest-demand cells that the player probably cannot execute
  (``inside_share < feasibility.inside_share_min``), ranked by demand weight and
  scored as ``weight_share × (1 - inside_share)``.
* **Position × role ranking** and the **data** for an illustrative Cut Card. No
  figure is drawn here: rendering is deferred to Stage 06.

Boundary policy (inherited, non-negotiable)
-------------------------------------------
Every Combine position group is ``AT_BOUNDARY`` for ``plant_retention`` on the
sample (all five). Coverage is therefore **computed once per distinct feasible
region and shared by every player in that region**: it is labelled
``coverage_shared_group`` with a ``shared_reason``, the manifest records
``coverage.all_group_level``, and no player-to-player spread is invented.

Rules honoured here
-------------------
* Never invent a label: a turn whose ``(game_id, play_id, nfl_id)`` has no
  ``player_play`` row is **excluded from outcome weighting**, never counted as
  a non-pressure rep. A null ``quick_pressure`` on a *present* row is false
  (D101), and that count is recorded.
* Every number comes from ``config.yaml``; nothing is tuned on the sample.
* Deterministic under a fixed seed: identical inputs ⇒ identical CSV/Parquet
  data (no timestamps in the Parquet metadata).
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa

from . import common

STAGE = "04_demand_coverage"

#: Bump when the persisted artefact contract changes so a stale checkpoint is
#: rebuilt instead of silently reused.
ARTEFACT_VERSION = 1

#: Columns the demand map cannot be built without.
TURN_COLUMNS = [
    "setting", "game_id", "play_id", "nfl_id", "player_name", "position",
    "position_group", "alignment", "snap_type", "entry_speed",
    "abs_turn_angle", "valid",
]

FEASIBLE_COLUMNS = [
    "setting", "nfl_id", "player_name", "position", "position_group",
    "cell_index", "entry_lo", "entry_hi", "angle_lo", "angle_hi",
    "observed", "retention_pred", "retention_p05", "retention_p95",
    "inside_share", "inside_point",
]

PLAYER_COLUMNS = [
    "setting", "nfl_id", "player_name", "position", "position_group",
    "retention_at_boundary", "tau2_at_boundary", "reliable",
]

VARIANCE_COLUMNS = ["setting", "position_group", "measure", "tau2_status"]

#: Explicit label columns (never ``SELECT *``); D101 null semantics apply.
LABEL_COLUMNS = [
    "game_id", "play_id", "nfl_id", "quick_pressure", "unblocked_pressure",
    "time_to_pressure", "blitzing", "player_get_off",
]

#: Outcome statuses and their reasons.
OUTCOME_OK = "OK"
OUTCOME_NOT_ESTIMABLE = "NOT_ESTIMABLE"
OUTCOME_REASONS = (
    "label_file_absent",
    "no_player_play_columns",
    "labels_null_on_all_joined_reps",
    "no_qualifying_turns",
)

#: Coverage statuses.
COVERAGE_STATUSES = ("OK", "NO_DEMAND_MASS", "NO_FEASIBLE_REGION")

#: Long demand table columns (the three provenance columns are prepended by
#: ``common.write_csv``).
DEMAND_TABLE_COLUMNS = [
    "position_group", "exploratory", "snap_type_scope", "weight_mode",
    "estimable", "status", "cell_index", "entry_lo", "entry_hi", "angle_lo",
    "angle_hi", "n_turns", "n_turns_weighted", "n_players",
    "n_players_nonzero_weight", "weight", "weight_share", "weight_share_p05",
    "weight_share_p95", "bootstrap_reps", "bootstrap_unit", "note",
]

SCOPES = ("combined", "observed", "extrapolated")


# --------------------------------------------------------------------- schemas

COVERAGE_SCHEMA = pa.schema([
    ("setting_region", pa.string()),
    ("nfl_id", pa.int64()),
    ("player_name", pa.string()),
    ("position", pa.string()),
    ("position_group", pa.string()),
    ("role", pa.string()),
    ("role_source", pa.string()),
    ("n_role_turns", pa.int32()),
    ("coverage_combined", pa.float64()),
    ("coverage_observed", pa.float64()),
    ("coverage_extrapolated", pa.float64()),
    ("coverage_expected_combined", pa.float64()),
    ("coverage_expected_observed", pa.float64()),
    ("coverage_expected_extrapolated", pa.float64()),
    ("coverage_combined_p05", pa.float64()),
    ("coverage_combined_p95", pa.float64()),
    ("coverage_combined_sd", pa.float64()),
    ("coverage_observed_p05", pa.float64()),
    ("coverage_observed_p95", pa.float64()),
    ("coverage_observed_sd", pa.float64()),
    ("coverage_extrapolated_p05", pa.float64()),
    ("coverage_extrapolated_p95", pa.float64()),
    ("coverage_extrapolated_sd", pa.float64()),
    ("status", pa.string()),
    ("status_combined", pa.string()),
    ("status_observed", pa.string()),
    ("status_extrapolated", pa.string()),
    ("demand_mass", pa.float64()),
    ("demand_mass_observed", pa.float64()),
    ("demand_mass_extrapolated", pa.float64()),
    ("n_cells_with_demand", pa.int32()),
    ("n_cut_gaps", pa.int32()),
    ("coverage_shared_group", pa.bool_()),
    ("shared_reason", pa.string()),
    ("region_group_size", pa.int32()),
    ("n_distinct_regions_in_group", pa.int32()),
    ("region_id", pa.int32()),
    ("retention_at_boundary", pa.bool_()),
    ("tau2_status", pa.string()),
    ("tau2_at_boundary", pa.bool_()),
    ("reliable", pa.bool_()),
    ("interval_method", pa.string()),
    ("joint_bootstrap_draws", pa.bool_()),
    ("note", pa.string()),
])

CUT_CARD_CELL_SCHEMA = pa.schema([
    ("card_id", pa.string()),
    ("nfl_id", pa.int64()),
    ("player_name", pa.string()),
    ("position", pa.string()),
    ("position_group", pa.string()),
    ("role", pa.string()),
    ("coverage_combined", pa.float64()),
    ("coverage_observed", pa.float64()),
    ("coverage_extrapolated", pa.float64()),
    ("cell_index", pa.int16()),
    ("entry_lo", pa.float64()),
    ("entry_hi", pa.float64()),
    ("angle_lo", pa.float64()),
    ("angle_hi", pa.float64()),
    ("observed", pa.bool_()),
    ("retention_pred", pa.float64()),
    ("retention_p05", pa.float64()),
    ("retention_p95", pa.float64()),
    ("inside_share", pa.float64()),
    ("inside_point", pa.bool_()),
    ("demand_weight", pa.float64()),
    ("demand_weight_share", pa.float64()),
    ("gap_score", pa.float64()),
    ("is_cut_gap", pa.bool_()),
])

CUT_GAP_PLAYER_COLUMNS = [
    "position_group", "role", "nfl_id", "player_name", "region_id",
    "cell_index", "entry_lo", "entry_hi", "angle_lo", "angle_hi", "gap_rank",
    "demand_weight", "demand_weight_share", "inside_share", "gap_score",
]

CUT_GAP_POSITION_COLUMNS = [
    "position_group", "role", "cell_index", "entry_lo", "entry_hi",
    "angle_lo", "angle_hi", "n_players", "n_distinct_regions",
    "n_occurrences", "mean_demand_weight_share", "mean_inside_share",
    "mean_gap_score",
]

RANKING_COLUMNS = [
    "position_group", "role", "exploratory", "n_players",
    "n_distinct_regions", "coverage_shared_group", "demand_mass",
    "mean_combined", "median_combined", "min_combined", "max_combined",
    "p05_combined", "p95_combined",
    "mean_observed", "median_observed", "min_observed", "max_observed",
    "p05_observed", "p95_observed",
    "mean_extrapolated", "median_extrapolated", "min_extrapolated",
    "max_extrapolated", "p05_extrapolated", "p95_extrapolated",
    "rank_coverage_combined",
]

CUT_CARD_PLAYER_COLUMNS = [
    "card_id", "nfl_id", "player_name", "position", "position_group", "role",
    "coverage_combined", "coverage_observed", "coverage_extrapolated",
    "demand_mass", "n_cut_gaps", "top_gap_cell", "top_gap_entry_lo",
    "top_gap_entry_hi", "top_gap_angle_lo", "top_gap_angle_hi",
    "top_gap_gap_score", "top_gap_cells", "position_mean_coverage_combined",
    "position_n_players", "position_rank", "position_summary",
    "selection", "rendering",
]


# ------------------------------------------------------------------ small maths

def _num(df: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _truthy(value) -> bool:
    """A vendor boolean that may be True/False/None/``'True'``/``'NA'``."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _bool(df: pd.DataFrame, col: str) -> np.ndarray:
    return df[col].map(_truthy).to_numpy(dtype=bool)


def _finite_or_none(x) -> float | None:
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _pct(values: np.ndarray, q: float) -> float:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    return float(np.percentile(v, q)) if len(v) else float("nan")


def assert_one_to_one(df: pd.DataFrame, keys: Iterable[str], what: str) -> None:
    """Refuse when a label table is not unique on its join key."""
    keys = list(keys)
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise common.StageRefused(f"{STAGE}: {what} is missing key column(s): "
                                  + ", ".join(missing))
    dup = df.duplicated(subset=keys, keep=False)
    if bool(dup.any()):
        raise common.StageRefused(
            f"{STAGE}: {what} is not unique on {tuple(keys)} "
            f"({int(dup.sum())} duplicated row(s)); refusing to join a label "
            "table that would multiply the turn table")


# ------------------------------------------------------------------- loaders

def _read_parquet(path: Path, required: list[str], what: str) -> pd.DataFrame:
    import pyarrow.parquet as pq

    path = Path(path)
    if not path.exists():
        raise common.StageRefused(f"{STAGE}: {what} is absent ({path})")
    names = set(pq.ParquetFile(path).schema_arrow.names)
    missing = [c for c in required if c not in names]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: {what} is missing required columns: " + ", ".join(missing))
    df = pd.read_parquet(path, columns=[c for c in required if c in names])
    if len(df) == 0:
        raise common.StageRefused(f"{STAGE}: {what} has zero rows ({path})")
    return df


def load_turns(path: Path) -> pd.DataFrame:
    return _read_parquet(path, TURN_COLUMNS, "the per-turn table (run 02_turns first)")


def load_feasible(path: Path) -> pd.DataFrame:
    return _read_parquet(path, FEASIBLE_COLUMNS,
                         "the feasible region (run 03_envelope first)")


def load_envelope_players(path: Path) -> pd.DataFrame:
    return _read_parquet(path, PLAYER_COLUMNS,
                         "the per-player envelope (run 03_envelope first)")


def load_variance(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(f"{STAGE}: variance_components.csv is absent ({path})")
    df = pd.read_csv(path)
    missing = [c for c in VARIANCE_COLUMNS if c not in df.columns]
    if missing:
        raise common.StageRefused(
            f"{STAGE}: variance_components.csv is missing columns: "
            + ", ".join(missing))
    return df


def _csv_header(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


def load_outcome_labels(cfg: common.Config, mode: str
                        ) -> tuple[pd.DataFrame | None, dict]:
    """Load the explicit label columns, or explain why they are absent.

    A missing label file **must not fail the stage**: the frequency demand map
    is still deliverable and the outcome map is reported ``NOT_ESTIMABLE``.
    """
    meta: dict = {
        "join_key": ["game_id", "play_id", "nfl_id"],
        "columns": list(LABEL_COLUMNS),
        "file": None,
    }
    try:
        raw = cfg.raw_file(mode, "player_play")
    except common.StageRefused as exc:  # pragma: no cover - config always has it
        meta.update({"status": OUTCOME_NOT_ESTIMABLE,
                     "reason": "label_file_absent", "detail": str(exc)})
        return None, meta
    meta["file"] = str(raw)
    if not raw.exists():
        meta.update({"status": OUTCOME_NOT_ESTIMABLE,
                     "reason": "label_file_absent"})
        return None, meta
    header = [h.strip() for h in _csv_header(raw)]
    missing = [c for c in LABEL_COLUMNS if c not in header]
    if missing:
        meta.update({"status": OUTCOME_NOT_ESTIMABLE,
                     "reason": "no_player_play_columns",
                     "missing_columns": missing})
        return None, meta
    parquet = common.ensure_parquet(cfg, mode, "player_play", LABEL_COLUMNS)
    df = pd.read_parquet(parquet, columns=LABEL_COLUMNS)
    meta["rows"] = int(len(df))
    return df, meta


# --------------------------------------------------------------------- cells

def cells_from_config(cfg: common.Config) -> list[dict]:
    """The configured (entry speed × turn angle) grid, in Stage 03's index order."""
    entry = [float(x) for x in cfg.get("bins.entry_speed")]
    angle = [float(x) for x in cfg.get("bins.turn_angle")]
    n_angle = len(angle) - 1
    cells: list[dict] = []
    for i in range(len(entry) - 1):
        for j in range(n_angle):
            cells.append({
                "cell_index": i * n_angle + j,
                "entry_lo": entry[i], "entry_hi": entry[i + 1],
                "angle_lo": angle[j], "angle_hi": angle[j + 1],
            })
    return cells


def _bin_index(values: np.ndarray, edges: list[float]) -> np.ndarray:
    return np.searchsorted(np.asarray(edges, dtype=float), values, side="right") - 1


# -------------------------------------------------------------------- result

@dataclass
class DemandCoverageResult:
    demand_map: pd.DataFrame
    coverage: pd.DataFrame
    ranking: pd.DataFrame
    cut_gaps_players: pd.DataFrame
    cut_gaps_position: pd.DataFrame
    cut_card_cells: pd.DataFrame
    cut_card_players: pd.DataFrame
    manifest: dict
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- engine

class DemandCoverage:
    """Build the demand map, coverage, cut gaps, ranking and Cut Card data."""

    def __init__(self, turns: pd.DataFrame, feasible: pd.DataFrame,
                 players: pd.DataFrame, variance: pd.DataFrame,
                 cfg: common.Config, labels: pd.DataFrame | None = None,
                 label_meta: dict | None = None):
        self.cfg = cfg
        self.turns = turns.reset_index(drop=True)
        self.feasible = feasible
        self.players = players
        self.variance = variance
        self.labels = labels
        self.label_meta = dict(label_meta or {})
        self.warnings: list[str] = []
        self._validate_config()

        self.positions = [str(p) for p in cfg.get("demand.positions")]
        self.snap_map = {str(k): [str(x) for x in v]
                         for k, v in cfg.get("demand.snap_types").items()}
        self.exploratory = [str(p) for p in cfg.get("demand.exploratory_positions")]
        self.weight_modes = [str(m) for m in cfg.get("demand.weight_modes")]
        self.outcome_positions = [str(p) for p in cfg.get("demand.outcome_weight_positions")]
        self.roles_cfg = {str(k): {str(a): str(b) for a, b in (v or {}).items()}
                          for k, v in (cfg.get("demand.roles") or {}).items()}
        self.cells = cells_from_config(cfg)
        self.n_cells = len(self.cells)
        self.group_code = {g: i for i, g in enumerate(sorted(self.positions))}

        self._prepare_eligibility()
        self._assert_cell_grid()
        self._prepare_labels()

        self.freq_by_group: dict[str, np.ndarray] = {}
        self.mass_by_group: dict[str, float] = {}
        self.group_turn_counts: dict[str, int] = {}

    # -- validation -----------------------------------------------------------
    def _validate_config(self) -> None:
        modes = [str(m) for m in self.cfg.get("demand.weight_modes")]
        if "frequency" not in modes:
            raise common.StageRefused(
                f"{STAGE}: demand.weight_modes must contain 'frequency' "
                f"(got {modes!r}); frequency is the default demand weight")
        if not self.cfg.get("demand.positions"):
            raise common.StageRefused(f"{STAGE}: demand.positions is empty")
        missing = [p for p in self.cfg.get("demand.positions")
                   if str(p) not in (self.cfg.get("demand.snap_types") or {})]
        if missing:
            raise common.StageRefused(
                f"{STAGE}: demand.snap_types has no entry for position(s): "
                + ", ".join(missing))

    # -- eligibility ----------------------------------------------------------
    def _prepare_eligibility(self) -> None:
        cfg = self.cfg
        game = self.turns[self.turns["setting"].astype(str) == "game"]
        self.game = game.reset_index(drop=True)
        df = self.game
        n = len(df)
        self.turns_in = int(n)

        group = df["position_group"].where(df["position_group"].notna(), "").astype(str).to_numpy()
        snap = df["snap_type"].where(df["snap_type"].notna(), "").astype(str).to_numpy()
        valid = _bool(df, "valid")
        entry = _num(df, "entry_speed")
        angle = _num(df, "abs_turn_angle")
        self.entry = entry
        self.angle = angle
        self.group = group

        entry_edges = [float(x) for x in cfg.get("bins.entry_speed")]
        angle_edges = [float(x) for x in cfg.get("bins.turn_angle")]

        relevant = np.zeros(n, dtype=bool)
        for pos in self.positions:
            relevant |= (group == pos) & np.isin(snap, self.snap_map.get(pos, []))

        reason = np.full(n, "", dtype=object)
        used = np.zeros(n, dtype=bool)

        m = ~valid
        reason[m] = "invalid"
        used |= m

        m = ~used & ~np.isin(group, self.positions)
        reason[m] = "position_not_mapped"
        used |= m

        m = ~used & ~relevant
        reason[m] = "snap_type_not_relevant"
        used |= m

        finite = np.isfinite(entry) & np.isfinite(angle)
        m = ~used & ~finite
        reason[m] = "nonfinite_geometry"
        used |= m

        lo, hi = entry_edges[0], entry_edges[-1]
        m = ~used & ((entry < lo) | (entry >= hi))
        reason[m] = "entry_outside_bins"
        used |= m

        lo, hi = angle_edges[0], angle_edges[-1]
        m = ~used & ((angle < lo) | (angle >= hi))
        reason[m] = "angle_outside_bins"
        used |= m

        self.eligible = ~used
        self.reason = reason
        self.reasons = ["invalid", "position_not_mapped", "snap_type_not_relevant",
                        "nonfinite_geometry", "entry_outside_bins",
                        "angle_outside_bins"]
        self.exclusion_counts = {r: int((reason == r).sum()) for r in self.reasons}
        self.n_eligible = int(self.eligible.sum())

        n_angle = len(angle_edges) - 1
        ei = _bin_index(np.where(self.eligible, entry, np.nan), entry_edges)
        ai = _bin_index(np.where(self.eligible, angle, np.nan), angle_edges)
        cell = ei * n_angle + ai
        self.cell_of = np.where(self.eligible, cell, -1).astype(int)

        self.nfl = pd.to_numeric(df["nfl_id"], errors="coerce").fillna(-1).astype(np.int64).to_numpy()

    # -- grid contract --------------------------------------------------------
    def _assert_cell_grid(self) -> None:
        feas = self.feasible[self.feasible["setting"].astype(str) == "combine"]
        cols = ["cell_index", "entry_lo", "entry_hi", "angle_lo", "angle_hi"]
        if len(feas) == 0:
            raise common.StageRefused(
                f"{STAGE}: the feasible region has no 'combine' rows; coverage "
                "cannot be computed without a Combine-derived region")
        unique = feas[cols].drop_duplicates().sort_values("cell_index").reset_index(drop=True)
        mine = pd.DataFrame(self.cells)[cols].sort_values("cell_index").reset_index(drop=True)
        if len(unique) != len(mine):
            raise common.StageRefused(
                f"{STAGE}: the Stage 03 feasible region has {len(unique)} cells "
                f"but config bins define {len(mine)}; refusing to score coverage "
                "on a different grid")
        for col in cols:
            a = np.asarray(unique[col].to_numpy(), dtype=float)
            b = np.asarray(mine[col].to_numpy(), dtype=float)
            if not np.allclose(a, b, rtol=0.0, atol=1e-9):
                raise common.StageRefused(
                    f"{STAGE}: feasible-region column {col} does not match the "
                    "configured bins; refusing to score coverage on a different grid")

    # -- labels ---------------------------------------------------------------
    def _prepare_labels(self) -> None:
        n = len(self.game)
        self.label_present = np.zeros(n, dtype=bool)
        self.quick_pressure = np.zeros(n, dtype=bool)
        self.short_ttp = np.zeros(n, dtype=bool)
        self.unblocked = np.zeros(n, dtype=bool)
        self.qualifying = np.zeros(n, dtype=bool)
        self.n_null_quick_pressure = 0
        self.n_reps_missing_label = int(self.eligible.sum())
        self.label_counts: dict[str, int] = {}
        if self.labels is None:
            return

        lab = self.labels.copy()
        for key in ("game_id", "play_id", "nfl_id"):
            lab[key] = pd.to_numeric(lab[key], errors="coerce")
        lab = lab.dropna(subset=["game_id", "play_id", "nfl_id"])
        lab[["game_id", "play_id", "nfl_id"]] = lab[
            ["game_id", "play_id", "nfl_id"]].astype(np.int64)
        assert_one_to_one(lab, ["game_id", "play_id", "nfl_id"], "player_play labels")

        keys = ["game_id", "play_id", "nfl_id"]
        left = self.game[keys].copy()
        for key in keys:
            left[key] = pd.to_numeric(left[key], errors="coerce")
        left["_row"] = np.arange(n, dtype=np.int64)
        merged = left.merge(lab, on=keys, how="left", indicator=True, sort=False)
        if len(merged) != len(left):
            raise common.StageRefused(
                f"{STAGE}: the label join changed the turn row count "
                f"({len(left)} -> {len(merged)}); refusing a many-to-many join")
        merged = merged.sort_values("_row", kind="stable")
        if len(merged) != len(left) or not np.array_equal(
                merged["_row"].to_numpy(), left["_row"].to_numpy()):
            raise common.StageRefused(
                f"{STAGE}: the label join reordered the turn table; refusing to "
                "assign labels to the wrong turns")

        present = (merged["_merge"] == "both").to_numpy()
        self.label_present = present
        self.n_reps_missing_label = int((self.eligible & ~present).sum())

        qp_raw = merged["quick_pressure"]
        qp_null = qp_raw.isna().to_numpy() & present
        self.n_null_quick_pressure = int(qp_null.sum())
        qp = qp_raw.map(_truthy).to_numpy(dtype=bool) & present

        ttp = pd.to_numeric(merged["time_to_pressure"], errors="coerce").to_numpy(dtype=float)
        threshold = float(self.cfg.get("demand.short_time_to_pressure_s"))
        short = np.isfinite(ttp) & (ttp <= threshold) & present

        self.quick_pressure = qp
        self.short_ttp = short
        self.unblocked = merged["unblocked_pressure"].map(_truthy).to_numpy(dtype=bool) & present
        self.qualifying = present & (qp | short)

        self.label_counts = {
            "turns_joined": int(present.sum()),
            "reps_missing_label": int(self.n_reps_missing_label),
            "null_quick_pressure_on_present_rows": int(self.n_null_quick_pressure),
            "qualifying_turns": int(self.qualifying.sum()),
            "unblocked_turns": int(self.unblocked.sum()),
        }
        # The outcome map is defined for the outcome positions only, so the
        # counts that explain it are scoped to those eligible turns.
        scope = self.eligible & np.isin(self.group, self.outcome_positions)
        self.label_counts.update({
            "turns_in_scope": int(scope.sum()),
            "turns_joined_in_scope": int((present & scope).sum()),
            "reps_missing_label_in_scope": int((scope & ~present).sum()),
            "null_quick_pressure_on_scope_rows": int((qp_null & scope).sum()),
            "qualifying_turns_in_scope": int((self.qualifying & scope).sum()),
            "unblocked_turns_in_scope": int((self.unblocked & scope).sum()),
        })

    # -- demand ---------------------------------------------------------------
    def _bootstrap_shares(self, matrix: np.ndarray, group: str
                          ) -> tuple[np.ndarray, np.ndarray]:
        reps = int(self.cfg.get("demand.bootstrap_reps"))
        min_players = int(self.cfg.get("demand.bootstrap_min_players"))
        lo, hi = [float(x) * 100.0 for x in self.cfg.get("demand.coverage_interval")]
        J = int(matrix.shape[0])
        if J < min_players or reps < 2:
            return (np.full(self.n_cells, np.nan), np.full(self.n_cells, np.nan))
        seed = np.random.SeedSequence([
            int(self.cfg.seed), int(self.cfg.get("demand.bootstrap_seed_offset")),
            int(self.group_code[group])])
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, J, size=(reps, J))
        sums = matrix[draws].sum(axis=1)            # (reps, n_cells)
        totals = sums.sum(axis=1, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            shares = np.where(totals > 0, sums / np.where(totals > 0, totals, 1.0),
                              np.nan)
        p05 = np.full(self.n_cells, np.nan)
        p95 = np.full(self.n_cells, np.nan)
        for c in range(self.n_cells):
            col = shares[:, c]
            col = col[np.isfinite(col)]
            if len(col):
                p05[c] = float(np.percentile(col, lo))
                p95[c] = float(np.percentile(col, hi))
        return p05, p95

    def _demand_group(self, group: str) -> list[dict]:
        cfg = self.cfg
        mask = self.eligible & (self.group == group)
        gcount = int(mask.sum())
        self.group_turn_counts[group] = gcount
        cell = self.cell_of[mask]
        players = self.nfl[mask]
        n_turns = np.bincount(cell, minlength=self.n_cells) if gcount else np.zeros(self.n_cells, int)

        # distinct players per cell (same for every weight mode)
        if gcount:
            pairs = np.unique(np.stack([players, cell], axis=1), axis=0)
            n_players_cell = np.bincount(pairs[:, 1], minlength=self.n_cells)
        else:
            pairs = np.zeros((0, 2), dtype=np.int64)
            n_players_cell = np.zeros(self.n_cells, dtype=int)

        uniq_players = np.unique(players) if gcount else np.zeros(0, dtype=np.int64)
        index = {int(p): i for i, p in enumerate(uniq_players)}
        row_of = np.array([index[int(p)] for p in players], dtype=np.int64) if gcount else np.zeros(0, np.int64)

        reps = int(cfg.get("demand.bootstrap_reps"))
        rows: list[dict] = []
        for mode in self.weight_modes:
            if mode != "frequency" and group not in self.outcome_positions:
                # The outcome maps are defined for the DL only (TASK.md §4).
                continue
            estimable = True
            status = OUTCOME_OK
            note = ""
            weight = np.zeros(self.n_cells, dtype=float)
            n_weighted = np.zeros(self.n_cells, dtype=int)
            n_nonzero = np.zeros(self.n_cells, dtype=int)
            p05 = np.full(self.n_cells, np.nan)
            p95 = np.full(self.n_cells, np.nan)

            if mode == "frequency":
                w_rows = np.ones(gcount)
                mat = np.zeros((len(uniq_players), self.n_cells))
                if gcount:
                    np.add.at(mat, (row_of, cell), w_rows)
                weight = mat.sum(axis=0)
                n_weighted = n_turns.astype(int)
                n_nonzero = n_players_cell.copy()
                p05, p95 = self._bootstrap_shares(mat, group)
                note = "all eligible turns weight 1"
            else:
                present = self.label_present[mask]
                usable = present & (self.quick_pressure[mask] | self.short_ttp[mask])
                if not present.any():
                    estimable, status = False, OUTCOME_NOT_ESTIMABLE
                    note = self.label_meta.get("reason", "labels_null_on_all_joined_reps")
                elif mode == "outcome_quick_pressure_excl_unblocked":
                    usable = usable & ~self.unblocked[mask]
                if estimable and not usable.any():
                    estimable, status = False, OUTCOME_NOT_ESTIMABLE
                    note = "no_qualifying_turns"
                if estimable:
                    keep = present
                    w_rows = usable[keep].astype(float)
                    mat = np.zeros((len(uniq_players), self.n_cells))
                    if keep.any():
                        np.add.at(mat, (row_of[keep], cell[keep]), w_rows)
                    weight = mat.sum(axis=0)
                    positive = usable
                    n_weighted = np.bincount(cell[positive], minlength=self.n_cells)
                    if positive.any():
                        wpairs = np.unique(np.stack(
                            [players[positive], cell[positive]], axis=1), axis=0)
                        n_nonzero = np.bincount(wpairs[:, 1], minlength=self.n_cells)
                    else:
                        n_nonzero = np.zeros(self.n_cells, dtype=int)
                    p05, p95 = self._bootstrap_shares(mat, group)
                    note = (f"labels present on {int(present.sum())}/{gcount} eligible "
                            f"turns; missing labels excluded, never set to false")
                    if mode == "outcome_quick_pressure_excl_unblocked":
                        note += "; unblocked_pressure reps weight 0"
            total = float(weight.sum())
            for c, meta in enumerate(self.cells):
                if not estimable:
                    w_val = None
                    share = None
                    p05c = None
                    p95c = None
                else:
                    w_val = float(weight[c])
                    share = (w_val / total) if total > 0 else None
                    p05c = _finite_or_none(p05[c]) if share is not None else None
                    p95c = _finite_or_none(p95[c]) if share is not None else None
                rows.append({
                    "position_group": group,
                    "exploratory": bool(group in self.exploratory),
                    "snap_type_scope": ",".join(self.snap_map.get(group, [])),
                    "weight_mode": mode,
                    "estimable": bool(estimable),
                    "status": status,
                    "cell_index": int(meta["cell_index"]),
                    "entry_lo": float(meta["entry_lo"]),
                    "entry_hi": float(meta["entry_hi"]),
                    "angle_lo": float(meta["angle_lo"]),
                    "angle_hi": float(meta["angle_hi"]),
                    "n_turns": int(n_turns[c]),
                    "n_turns_weighted": int(n_weighted[c] if estimable else 0),
                    "n_players": int(n_players_cell[c]),
                    "n_players_nonzero_weight": int(n_nonzero[c]),
                    "weight": w_val,
                    "weight_share": share,
                    "weight_share_p05": p05c,
                    "weight_share_p95": p95c,
                    "bootstrap_reps": int(reps),
                    "bootstrap_unit": "player",
                    "note": note,
                })
            if mode == "frequency":
                self.freq_by_group[group] = weight.copy()
                self.mass_by_group[group] = total
        return rows

    # -- coverage -------------------------------------------------------------
    def _region_arrays(self, group: str) -> dict[int, dict]:
        """Per-player arrays from the persisted Combine feasible region."""
        feas = self.feasible
        sub = feas[(feas["setting"].astype(str) == "combine")
                   & (feas["position_group"].astype(str) == group)]
        out: dict[int, dict] = {}
        for nid, rows in sub.groupby("nfl_id", sort=True):
            rows = rows.sort_values("cell_index")
            cell = rows["cell_index"].to_numpy(dtype=int)
            if len(cell) != self.n_cells or not np.array_equal(
                    cell, np.arange(self.n_cells)):
                self.warnings.append(
                    f"{group}/{int(nid)}: feasible region is not the full "
                    f"{self.n_cells}-cell grid; region skipped")
                continue
            out[int(nid)] = {
                "inside_share": pd.to_numeric(rows["inside_share"], errors="coerce")
                .to_numpy(dtype=float),
                "inside_point": rows["inside_point"].map(_truthy).to_numpy(dtype=bool),
                "observed": rows["observed"].map(_truthy).to_numpy(dtype=bool),
                "retention_pred": pd.to_numeric(rows["retention_pred"], errors="coerce")
                .to_numpy(dtype=float),
                "retention_p05": pd.to_numeric(rows["retention_p05"], errors="coerce")
                .to_numpy(dtype=float),
                "retention_p95": pd.to_numeric(rows["retention_p95"], errors="coerce")
                .to_numpy(dtype=float),
            }
        return out

    @staticmethod
    def _region_signature(region: dict) -> str:
        share = np.round(np.asarray(region["inside_share"], dtype=float), 12)
        digest = hashlib.sha1()
        digest.update(share.tobytes())
        digest.update(np.asarray(region["inside_point"], dtype=bool).tobytes())
        return digest.hexdigest()

    def _coverage_for_region(self, group: str, region: dict, region_code: int) -> dict:
        cfg = self.cfg
        w = np.asarray(self.freq_by_group.get(group, np.zeros(self.n_cells)), dtype=float)
        total = float(w.sum())
        share = np.nan_to_num(np.asarray(region["inside_share"], dtype=float), nan=0.0)
        point = np.asarray(region["inside_point"], dtype=bool)
        observed = np.asarray(region["observed"], dtype=bool)
        draws_n = int(cfg.get("demand.coverage_draws"))
        lo, hi = [float(x) * 100.0 for x in cfg.get("demand.coverage_interval")]
        seed = np.random.SeedSequence([
            int(cfg.seed), int(cfg.get("demand.coverage_seed_offset")),
            int(self.group_code[group]), int(region_code)])
        children = seed.spawn(len(SCOPES))

        out: dict[str, Any] = {}
        for i, scope in enumerate(SCOPES):
            active = w > 0
            if scope == "observed":
                active = active & observed
            elif scope == "extrapolated":
                active = active & ~observed
            mass = float(w[active].sum())
            out[f"mass_{scope}"] = mass
            if total <= 0.0 or mass <= 0.0:
                out[f"coverage_{scope}"] = None
                out[f"expected_{scope}"] = None
                out[f"p05_{scope}"] = None
                out[f"p95_{scope}"] = None
                out[f"sd_{scope}"] = None
                out[f"status_{scope}"] = "NO_DEMAND_MASS"
                continue
            ww = w[active]
            out[f"coverage_{scope}"] = float((ww * point[active]).sum() / mass)
            out[f"expected_{scope}"] = float((ww * share[active]).sum() / mass)
            rng = np.random.default_rng(children[i])
            p = np.clip(share[active], 0.0, 1.0)
            draws = rng.random((draws_n, int(active.sum()))) < p[None, :]
            vals = (draws * ww[None, :]).sum(axis=1) / mass
            out[f"p05_{scope}"] = float(np.percentile(vals, lo))
            out[f"p95_{scope}"] = float(np.percentile(vals, hi))
            out[f"sd_{scope}"] = float(np.std(vals, ddof=1)) if len(vals) > 1 else None
            out[f"status_{scope}"] = "OK"
        if total <= 0.0:
            out["status_combined"] = "NO_DEMAND_MASS"
        out["demand_mass"] = total
        out["n_cells_with_demand"] = int((w > 0).sum())
        return out

    def _roles(self) -> dict[int, dict]:
        game = self.game
        out: dict[int, dict] = {}
        by_player = {int(k): v for k, v in game.groupby(
            pd.to_numeric(game["nfl_id"], errors="coerce").fillna(-1).astype(np.int64),
            sort=False)}
        for nid, sub in by_player.items():
            group_rows = sub[sub["position_group"].astype(str).isin(self.positions)]
            if len(group_rows) == 0:
                out[int(nid)] = {"role": "unknown",
                                 "role_source": "game_turn_alignment_mode",
                                 "n_role_turns": 0}
                continue
            group = str(group_rows["position_group"].astype(str).iloc[0])
            wanted = self.snap_map.get(group, [])
            relevant = group_rows[group_rows["snap_type"].astype(str).isin(wanted)]
            use = relevant if len(relevant) else group_rows
            labels = use["alignment"].where(use["alignment"].notna(), "").astype(str)
            labels = labels[labels.str.len() > 0]
            n_used = int(len(use))
            if len(labels) == 0:
                out[int(nid)] = {"role": "unknown",
                                 "role_source": "game_turn_alignment_mode",
                                 "n_role_turns": n_used}
                continue
            counts: dict[str, int] = {}
            for value in labels:
                counts[value] = counts.get(value, 0) + 1
            mode = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            mapped = self.roles_cfg.get(group, {}).get(mode)
            out[int(nid)] = {
                "role": mapped if mapped else "other",
                "role_source": "game_turn_alignment_mode",
                "n_role_turns": n_used,
            }
        return out

    def _boundary(self, group: str, player_rows: pd.DataFrame) -> dict:
        ret = self.variance[
            (self.variance["setting"].astype(str) == "combine")
            & (self.variance["position_group"].astype(str) == group)
            & (self.variance["measure"].astype(str) == "plant_retention")]
        status = str(ret["tau2_status"].iloc[0]) if len(ret) else "NOT_AVAILABLE"
        return status

    def _build_coverage(self) -> tuple[pd.DataFrame, dict]:
        cfg = self.cfg
        players = self.players[self.players["setting"].astype(str) == "combine"].copy()
        players = players[players["position_group"].astype(str).isin(self.positions)]
        players["nfl_id"] = pd.to_numeric(players["nfl_id"], errors="coerce")
        players = players.dropna(subset=["nfl_id"]).copy()
        players["nfl_id"] = players["nfl_id"].astype(np.int64)
        players = players.sort_values(["position_group", "nfl_id"], kind="stable")

        roles = self._roles()
        top_n = int(cfg.get("demand.cut_gap_top_n"))
        share_min = float(cfg.get("feasibility.inside_share_min"))

        region_cache: dict[tuple[str, str], dict] = {}
        region_codes: dict[str, dict[str, int]] = {}
        regions_by_group: dict[str, dict[int, dict]] = {}
        for group in self.positions:
            regions = self._region_arrays(group)
            regions_by_group[group] = regions
            signatures = {}
            for nid, region in regions.items():
                signatures[nid] = self._region_signature(region)
            uniq = sorted(set(signatures.values()))
            region_codes[group] = {sig: i for i, sig in enumerate(uniq)}
            for nid, sig in signatures.items():
                key = (group, sig)
                if key not in region_cache:
                    region_cache[key] = self._coverage_for_region(
                        group, regions[nid], region_codes[group][sig])

        group_tau2: dict[str, str] = {g: self._boundary(g, players) for g in self.positions}
        group_region_count = {g: len(region_codes.get(g, {})) for g in self.positions}
        group_size = {g: int((players["position_group"].astype(str) == g).sum())
                      for g in self.positions}

        rows: list[dict] = []
        gap_rows: list[dict] = []
        player_region: dict[int, str] = {}
        for _, prow in players.iterrows():
            nid = int(prow["nfl_id"])
            group = str(prow["position_group"])
            region = regions_by_group.get(group, {}).get(nid)
            role_info = roles.get(nid, {"role": "unknown",
                                        "role_source": "game_turn_alignment_mode",
                                        "n_role_turns": 0})
            w = np.asarray(self.freq_by_group.get(group, np.zeros(self.n_cells)),
                           dtype=float)
            total = float(w.sum())
            tau2_status = group_tau2.get(group, "NOT_AVAILABLE")
            shared_reason = ("tau2_at_boundary" if tau2_status == "AT_BOUNDARY"
                             else "identical_feasible_region")
            region_count = int(group_region_count.get(group, 0))
            shared = bool(region_count == 1 and region is not None)
            retention_boundary = _truthy(prow.get("retention_at_boundary", False))
            tau2_boundary = _truthy(prow.get("tau2_at_boundary", False))
            reliable = _truthy(prow.get("reliable", False))

            if region is None:
                region_id = -1
                values = {
                    "coverage_combined": None, "coverage_observed": None,
                    "coverage_extrapolated": None,
                    "coverage_expected_combined": None,
                    "coverage_expected_observed": None,
                    "coverage_expected_extrapolated": None,
                    "coverage_combined_p05": None, "coverage_combined_p95": None,
                    "coverage_combined_sd": None,
                    "coverage_observed_p05": None, "coverage_observed_p95": None,
                    "coverage_observed_sd": None,
                    "coverage_extrapolated_p05": None,
                    "coverage_extrapolated_p95": None,
                    "coverage_extrapolated_sd": None,
                    "demand_mass": total, "demand_mass_observed": None,
                    "demand_mass_extrapolated": None,
                    "n_cells_with_demand": int((w > 0).sum()),
                    "status": "NO_FEASIBLE_REGION",
                    "status_combined": "NO_FEASIBLE_REGION",
                    "status_observed": "NO_FEASIBLE_REGION",
                    "status_extrapolated": "NO_FEASIBLE_REGION",
                }
                gaps: list[dict] = []
            else:
                sig = self._region_signature(region)
                region_id = int(region_codes[group][sig])
                player_region[nid] = sig
                cached = region_cache[(group, sig)]
                values = {
                    "coverage_combined": cached["coverage_combined"],
                    "coverage_observed": cached["coverage_observed"],
                    "coverage_extrapolated": cached["coverage_extrapolated"],
                    "coverage_expected_combined": cached["expected_combined"],
                    "coverage_expected_observed": cached["expected_observed"],
                    "coverage_expected_extrapolated": cached["expected_extrapolated"],
                    "coverage_combined_p05": cached["p05_combined"],
                    "coverage_combined_p95": cached["p95_combined"],
                    "coverage_combined_sd": cached["sd_combined"],
                    "coverage_observed_p05": cached["p05_observed"],
                    "coverage_observed_p95": cached["p95_observed"],
                    "coverage_observed_sd": cached["sd_observed"],
                    "coverage_extrapolated_p05": cached["p05_extrapolated"],
                    "coverage_extrapolated_p95": cached["p95_extrapolated"],
                    "coverage_extrapolated_sd": cached["sd_extrapolated"],
                    "demand_mass": cached["demand_mass"],
                    "demand_mass_observed": cached["mass_observed"],
                    "demand_mass_extrapolated": cached["mass_extrapolated"],
                    "n_cells_with_demand": cached["n_cells_with_demand"],
                    "status": "OK" if total > 0 else "NO_DEMAND_MASS",
                    "status_combined": cached["status_combined"],
                    "status_observed": cached["status_observed"],
                    "status_extrapolated": cached["status_extrapolated"],
                }
                gaps = self._cut_gaps(nid, prow, group,
                                      role_info["role"], w, region, top_n, share_min,
                                      region_id)

            for g in gaps:
                gap_rows.append(g)

            rows.append({
                "setting_region": "combine",
                "nfl_id": nid,
                "player_name": prow.get("player_name"),
                "position": prow.get("position"),
                "position_group": group,
                "role": role_info["role"],
                "role_source": role_info["role_source"],
                "n_role_turns": int(role_info["n_role_turns"]),
                **values,
                "n_cut_gaps": int(len(gaps)),
                "coverage_shared_group": shared,
                "shared_reason": shared_reason,
                "region_group_size": int(group_size[group]),
                "n_distinct_regions_in_group": region_count,
                "region_id": region_id,
                "retention_at_boundary": retention_boundary,
                "tau2_status": tau2_status,
                "tau2_at_boundary": tau2_boundary,
                "reliable": reliable,
                "interval_method": "marginal_bernoulli_over_stage03_inside_share",
                "joint_bootstrap_draws": False,
                "note": ("group-level: every player in this region shares one "
                         "feasible region" if shared else ""),
            })

        coverage = pd.DataFrame(rows, columns=[f.name for f in COVERAGE_SCHEMA])
        self.coverage = coverage
        self.gap_rows = gap_rows
        meta = {
            "group_tau2": group_tau2,
            "group_region_count": group_region_count,
            "group_size": group_size,
            "n_shared_groups": int(sum(
                1 for g in self.positions
                if group_size.get(g, 0) > 0 and group_region_count.get(g, 0) == 1)),
            "n_groups_with_players": int(sum(
                1 for g in self.positions if group_size.get(g, 0) > 0)),
            "n_groups": int(len(self.positions)),
            "region_cache_size": len(region_cache),
        }
        return coverage, meta

    def _cut_gaps(self, nid: int, prow, group: str, role: str,
                  w: np.ndarray, region: dict, top_n: int, share_min: float,
                  region_id: int = -1) -> list[dict]:
        total = float(w.sum())
        if total <= 0:
            return []
        share = np.nan_to_num(np.asarray(region["inside_share"], dtype=float), nan=0.0)
        share_w = w / total
        candidate = np.flatnonzero((w > 0) & (share < share_min))
        order = sorted(candidate, key=lambda c: (-share_w[c], share[c], int(c)))
        out: list[dict] = []
        for rank, c in enumerate(order[:top_n], start=1):
            meta = self.cells[int(c)]
            out.append({
                "position_group": group,
                "role": role,
                "nfl_id": int(nid),
                "player_name": prow.get("player_name"),
                "cell_index": int(c),
                "entry_lo": float(meta["entry_lo"]),
                "entry_hi": float(meta["entry_hi"]),
                "angle_lo": float(meta["angle_lo"]),
                "angle_hi": float(meta["angle_hi"]),
                "gap_rank": int(rank),
                "region_id": int(region_id),
                "demand_weight": float(w[c]),
                "demand_weight_share": float(share_w[c]),
                "inside_share": float(share[c]),
                "gap_score": float(share_w[c] * (1.0 - share[c])),
            })
        return out

    # -- aggregates -----------------------------------------------------------
    def _position_gaps(self) -> pd.DataFrame:
        rows = self.gap_rows
        if not rows:
            return pd.DataFrame(columns=CUT_GAP_POSITION_COLUMNS)
        df = pd.DataFrame(rows)
        out: list[dict] = []
        for group, gdf in df.groupby("position_group", sort=False):
            roles = sorted(gdf["role"].unique())
            for role in list(roles) + ["all"]:
                sub = gdf if role == "all" else gdf[gdf["role"] == role]
                if len(sub) == 0:
                    continue
                for cell, cdf in sub.groupby("cell_index", sort=True):
                    meta = self.cells[int(cell)]
                    out.append({
                        "position_group": group,
                        "role": role,
                        "cell_index": int(cell),
                        "entry_lo": float(meta["entry_lo"]),
                        "entry_hi": float(meta["entry_hi"]),
                        "angle_lo": float(meta["angle_lo"]),
                        "angle_hi": float(meta["angle_hi"]),
                        "n_players": int(cdf["nfl_id"].nunique()),
                        "n_distinct_regions": int(cdf["region_id"].nunique()),
                        "n_occurrences": int(len(cdf)),
                        "mean_demand_weight_share": float(cdf["demand_weight_share"].mean()),
                        "mean_inside_share": float(cdf["inside_share"].mean()),
                        "mean_gap_score": float(cdf["gap_score"].mean()),
                    })
        frame = pd.DataFrame(out, columns=CUT_GAP_POSITION_COLUMNS)
        frame = frame.sort_values(
            ["position_group", "role", "n_occurrences", "mean_demand_weight_share"],
            ascending=[True, True, False, False], kind="stable").reset_index(drop=True)
        return frame

    def _ranking(self) -> pd.DataFrame:
        coverage = self.coverage
        out: list[dict] = []
        for group in self.positions:
            gdf = coverage[coverage["position_group"].astype(str) == group]
            if len(gdf) == 0:
                continue
            roles = sorted(set(gdf["role"].astype(str)))
            for role in ["all"] + roles:
                sub = gdf if role == "all" else gdf[gdf["role"].astype(str) == role]
                if len(sub) == 0:
                    continue
                row = {
                    "position_group": group,
                    "role": role,
                    "exploratory": bool(group in self.exploratory),
                    "n_players": int(len(sub)),
                    "n_distinct_regions": int(
                        sub.loc[sub["region_id"] >= 0, "region_id"].nunique()),
                    "coverage_shared_group": bool(sub["coverage_shared_group"].all()),
                    "demand_mass": float(self.mass_by_group.get(group, 0.0)),
                }
                for scope in SCOPES:
                    vals = pd.to_numeric(sub[f"coverage_{scope}"], errors="coerce").dropna()
                    for stat, func in (("mean", "mean"), ("median", "median"),
                                       ("min", "min"), ("max", "max")):
                        row[f"{stat}_{scope}"] = (
                            float(getattr(vals, func)()) if len(vals) else None)
                    row[f"p05_{scope}"] = _finite_or_none(
                        np.percentile(vals.to_numpy(dtype=float), 5)) if len(vals) else None
                    row[f"p95_{scope}"] = _finite_or_none(
                        np.percentile(vals.to_numpy(dtype=float), 95)) if len(vals) else None
                out.append(row)
        frame = pd.DataFrame(out)
        if len(frame) == 0:
            return pd.DataFrame(columns=RANKING_COLUMNS)
        frame = frame.sort_values(["position_group", "role"], kind="stable").reset_index(drop=True)
        frame["rank_coverage_combined"] = (
            frame["mean_combined"].rank(method="min", ascending=True, na_option="bottom")
            .astype(int))
        frame = frame.sort_values(["rank_coverage_combined", "position_group", "role"],
                                  kind="stable").reset_index(drop=True)
        return frame[RANKING_COLUMNS]

    # -- cut cards ------------------------------------------------------------
    def _cut_cards(self, ranking: pd.DataFrame
                   ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
        cfg = self.cfg
        groups = [str(g) for g in cfg.get("demand.card_groups")]
        per_group = int(cfg.get("demand.card_players_per_group"))
        selection = str(cfg.get("demand.card_selection"))
        coverage = self.coverage
        cards: list[dict] = []
        cells_out: list[dict] = []

        for group in groups:
            gdf = coverage[(coverage["position_group"].astype(str) == group)
                           & (coverage["status"].astype(str) == "OK")
                           & (~coverage["role"].astype(str).isin(["unknown", "other"]))
                           & (pd.to_numeric(coverage["coverage_combined"], errors="coerce").notna())]
            if len(gdf) == 0:
                continue
            gdf = gdf.sort_values(["coverage_combined", "nfl_id"], kind="stable")
            take: list[pd.Series] = []
            if selection == "median_coverage":
                for k in range(per_group):
                    offset = (len(gdf) // 2 + k) % len(gdf)
                    take.append(gdf.iloc[offset])
            else:  # pragma: no cover - config only supports median_coverage
                take = [gdf.iloc[k] for k in range(min(per_group, len(gdf)))]
            # de-duplicate a repeated pick in a one-player group
            seen: set[int] = set()
            for prow in take:
                nid = int(prow["nfl_id"])
                if nid in seen:
                    continue
                seen.add(nid)
                cards.append({"player": prow, "group": group})
        if len(cards) == 0:
            return (pd.DataFrame(columns=[f.name for f in CUT_CARD_CELL_SCHEMA]),
                    pd.DataFrame(columns=CUT_CARD_PLAYER_COLUMNS), [])

        rank_lookup = {(r["position_group"], r["role"]): r
                       for _, r in ranking.iterrows()}
        card_rows: list[dict] = []
        for i, card in enumerate(cards, start=1):
            prow = card["player"]
            group = card["group"]
            nid = int(prow["nfl_id"])
            card_id = f"card{i:02d}-{group}-{nid}"
            region = self._region_arrays(group).get(nid)
            w = np.asarray(self.freq_by_group.get(group, np.zeros(self.n_cells)),
                           dtype=float)
            total = float(w.sum())
            share = np.nan_to_num(np.asarray(region["inside_share"], dtype=float), nan=0.0)
            gap_meta = {g["cell_index"]: g for g in self.gap_rows
                        if int(g["nfl_id"]) == nid}
            for c, meta in enumerate(self.cells):
                gap = gap_meta.get(int(c))
                cells_out.append({
                    "card_id": card_id,
                    "nfl_id": nid,
                    "player_name": prow.get("player_name"),
                    "position": prow.get("position"),
                    "position_group": group,
                    "role": str(prow["role"]),
                    "coverage_combined": _finite_or_none(prow["coverage_combined"]),
                    "coverage_observed": _finite_or_none(prow["coverage_observed"]),
                    "coverage_extrapolated": _finite_or_none(prow["coverage_extrapolated"]),
                    "cell_index": int(c),
                    "entry_lo": float(meta["entry_lo"]),
                    "entry_hi": float(meta["entry_hi"]),
                    "angle_lo": float(meta["angle_lo"]),
                    "angle_hi": float(meta["angle_hi"]),
                    "observed": bool(region["observed"][c]),
                    "retention_pred": _finite_or_none(region["retention_pred"][c]),
                    "retention_p05": _finite_or_none(region["retention_p05"][c]),
                    "retention_p95": _finite_or_none(region["retention_p95"][c]),
                    "inside_share": _finite_or_none(region["inside_share"][c]),
                    "inside_point": bool(region["inside_point"][c]),
                    "demand_weight": float(w[c]),
                    "demand_weight_share": (float(w[c] / total) if total > 0 else None),
                    "gap_score": (float(gap["gap_score"]) if gap else 0.0),
                    "is_cut_gap": bool(gap is not None),
                })
            role = str(prow["role"])
            rrow = rank_lookup.get((group, role))
            if rrow is None:
                rrow = rank_lookup.get((group, "all"))
            rrow = dict(rrow) if rrow is not None and len(rrow) else {}
            top = [g for g in self.gap_rows if int(g["nfl_id"]) == nid]
            top_gap = top[0] if top else None
            card_rows.append({
                "card_id": card_id,
                "nfl_id": nid,
                "player_name": prow.get("player_name"),
                "position": prow.get("position"),
                "position_group": group,
                "role": role,
                "coverage_combined": _finite_or_none(prow["coverage_combined"]),
                "coverage_observed": _finite_or_none(prow["coverage_observed"]),
                "coverage_extrapolated": _finite_or_none(prow["coverage_extrapolated"]),
                "demand_mass": total,
                "n_cut_gaps": int(len(top)),
                "top_gap_cell": (int(top_gap["cell_index"]) if top_gap else None),
                "top_gap_entry_lo": (top_gap["entry_lo"] if top_gap else None),
                "top_gap_entry_hi": (top_gap["entry_hi"] if top_gap else None),
                "top_gap_angle_lo": (top_gap["angle_lo"] if top_gap else None),
                "top_gap_angle_hi": (top_gap["angle_hi"] if top_gap else None),
                "top_gap_gap_score": (top_gap["gap_score"] if top_gap else None),
                "top_gap_cells": ";".join(
                    f"{int(g['cell_index'])}:{float(g['gap_score']):.6f}"
                    for g in top),
                "position_mean_coverage_combined": (
                    _finite_or_none(rrow.get("mean_combined")) if rrow else None),
                "position_n_players": (int(rrow["n_players"]) if rrow else None),
                "position_rank": (int(rrow["rank_coverage_combined"]) if rrow else None),
                "position_summary": (f"{group}: mean combined coverage "
                                     f"{_fmt(rrow.get('mean_combined'))} over "
                                     f"{int(rrow['n_players'])} player(s), rank "
                                     f"{int(rrow['rank_coverage_combined'])}"
                                     if rrow else ""),
                "selection": selection,
                "rendering": "deferred_to_stage_06_figures",
            })
        cell_frame = pd.DataFrame(cells_out,
                                  columns=[f.name for f in CUT_CARD_CELL_SCHEMA])
        player_frame = pd.DataFrame(card_rows, columns=CUT_CARD_PLAYER_COLUMNS)
        return cell_frame, player_frame, cards

    # -- manifest -------------------------------------------------------------
    def _manifest(self, coverage_meta: dict, ranking: pd.DataFrame,
                  card_players: pd.DataFrame, n_cards: int) -> dict:
        cfg = self.cfg
        coverage = self.coverage
        dist: dict[str, dict] = {}
        for scope in SCOPES:
            vals = pd.to_numeric(coverage[f"coverage_{scope}"], errors="coerce").dropna()
            dist[scope] = {
                "n": int(len(vals)),
                "mean": _finite_or_none(vals.mean()) if len(vals) else None,
                "min": _finite_or_none(vals.min()) if len(vals) else None,
                "max": _finite_or_none(vals.max()) if len(vals) else None,
                "p05": _finite_or_none(np.percentile(vals.to_numpy(dtype=float), 5))
                if len(vals) else None,
                "p95": _finite_or_none(np.percentile(vals.to_numpy(dtype=float), 95))
                if len(vals) else None,
            }

        boundary_components = {}
        for group in self.positions:
            boundary_components[group] = coverage_meta["group_tau2"].get(
                group, "NOT_AVAILABLE")
        n_at_boundary = sum(1 for v in boundary_components.values() if v == "AT_BOUNDARY")

        n_players = int(len(coverage))
        n_shared_players = int(coverage["coverage_shared_group"].sum())
        all_group_level = bool(
            coverage_meta["n_groups_with_players"] > 0
            and coverage_meta["n_shared_groups"]
            == coverage_meta["n_groups_with_players"])

        gaps_df = pd.DataFrame(self.gap_rows)
        gap_summary = {
            "n_players_with_gaps": int(gaps_df["nfl_id"].nunique()) if len(gaps_df) else 0,
            "n_player_cell_rows": int(len(gaps_df)),
            "mean_gap_score": _finite_or_none(gaps_df["gap_score"].mean())
            if len(gaps_df) else None,
            "top_gap_cells": [],
        }
        if len(gaps_df):
            top = (gaps_df.groupby(["position_group", "cell_index"], sort=True)
                   .size().reset_index(name="n_occurrences")
                   .sort_values(["n_occurrences", "position_group", "cell_index"],
                                ascending=[False, True, True], kind="stable")
                   .head(int(cfg.get("demand.cut_gap_top_n"))))
            gap_summary["top_gap_cells"] = [
                {"position_group": str(r.position_group),
                 "cell_index": int(r.cell_index),
                 "n_occurrences": int(r.n_occurrences)} for r in top.itertuples()]

        outcome_status = self.label_meta.get("status", OUTCOME_OK)
        outcome_reason = self.label_meta.get("reason")
        if self.labels is None:
            outcome_status = OUTCOME_NOT_ESTIMABLE
            outcome_reason = outcome_reason or "label_file_absent"
        elif self.label_counts.get("qualifying_turns_in_scope", 0) == 0:
            outcome_status = OUTCOME_NOT_ESTIMABLE
            outcome_reason = "no_qualifying_turns"

        manifest = {
            "stage": STAGE,
            "artefact_version": ARTEFACT_VERSION,
            "inputs": {
                "turns": {"path": _rel(cfg, cfg.repo_path("turns") / "turns.parquet"),
                          "rows": int(len(self.turns))},
                "feasible_region": {"path": _rel(
                    cfg, cfg.repo_path("envelope") / "feasible_region.parquet"),
                                    "rows": int(len(self.feasible))},
                "envelope_players": {"path": _rel(
                    cfg, cfg.repo_path("envelope") / "envelope_players.parquet"),
                                     "rows": int(len(self.players))},
                "variance_components": {"path": _rel(
                    cfg, cfg.repo_path("envelope") / "variance_components.csv"),
                                        "rows": int(len(self.variance))},
                "player_play": {"path": _rel(cfg, self.label_meta.get("file")),
                                "rows": int(self.label_meta.get("rows", 0))},
            },
            "bins": {
                "entry_speed": [float(x) for x in cfg.get("bins.entry_speed")],
                "turn_angle": [float(x) for x in cfg.get("bins.turn_angle")],
                "n_entry_bins": len(cfg.get("bins.entry_speed")) - 1,
                "n_angle_bins": len(cfg.get("bins.turn_angle")) - 1,
                "n_cells": int(self.n_cells),
            },
            "positions": list(self.positions),
            "snap_types": {k: list(v) for k, v in self.snap_map.items()},
            "exploratory_positions": list(self.exploratory),
            "weight_modes": list(self.weight_modes),
            "outcome_weighting": {
                "positions": list(self.outcome_positions),
                "status": outcome_status,
                "reason": (outcome_reason if outcome_status
                           == OUTCOME_NOT_ESTIMABLE else None),
                "join_key": ["game_id", "play_id", "nfl_id"],
                "columns": list(LABEL_COLUMNS),
                "counts": dict(self.label_counts),
                "null_semantics": (
                    "quick_pressure null -> false only on a PRESENT player_play "
                    "row (D101); a turn with no player_play row is excluded from "
                    "outcome weighting, never treated as a non-pressure rep"),
                "short_time_to_pressure_s": float(
                    cfg.get("demand.short_time_to_pressure_s")),
                "excluded_unblocked_mode": "outcome_quick_pressure_excl_unblocked",
            },
            "demand_counts": {
                "turns_in": int(self.turns_in),
                "eligible": int(self.n_eligible),
                "excluded": dict(self.exclusion_counts),
                "sum_frequency_n_turns": int(sum(
                    int(self.freq_by_group.get(g, np.zeros(self.n_cells)).sum())
                    for g in self.positions)),
                "per_group": {g: int(self.group_turn_counts.get(g, 0))
                              for g in self.positions},
                "total_demand_mass": float(sum(self.mass_by_group.values())),
            },
            "bootstrap": {
                "unit": "player",
                "reps": int(cfg.get("demand.bootstrap_reps")),
                "seed": [int(cfg.seed), int(cfg.get("demand.bootstrap_seed_offset"))],
                "interval": [float(x) for x in cfg.get("demand.coverage_interval")],
                "min_players": int(cfg.get("demand.bootstrap_min_players")),
                "note": ("players are resampled with replacement within a position "
                         "group; turns inside one player are not independent"),
            },
            "coverage": {
                "method": "demand_weighted_share_of_cells_inside_point",
                "setting_region": "combine",
                "interval_method":
                    "marginal_bernoulli_over_stage03_inside_share",
                "joint_bootstrap_draws": False,
                "draws": int(cfg.get("demand.coverage_draws")),
                "n_players": n_players,
                "n_shared_players": n_shared_players,
                "n_groups": int(coverage_meta["n_groups"]),
                "n_groups_with_players": int(coverage_meta["n_groups_with_players"]),
                "n_shared_groups": int(coverage_meta["n_shared_groups"]),
                "n_distinct_regions_by_group": {
                    g: int(coverage_meta["group_region_count"].get(g, 0))
                    for g in self.positions},
                "group_sizes": {g: int(coverage_meta["group_size"].get(g, 0))
                                for g in self.positions},
                "all_group_level": all_group_level,
                "boundary": {
                    "plant_retention_tau2_status_by_group": boundary_components,
                    "n_components_at_boundary": int(n_at_boundary),
                    "n_components": int(len(self.positions)),
                },
                "distribution": dist,
                "note": (
                    "Coverage is computed once per distinct feasible region and "
                    "shared by every player in that region. Every Combine "
                    "position group is AT_BOUNDARY for plant_retention on this "
                    "sample, so coverage is group-level here and player-to-player "
                    "variation is NOT estimated."
                    if all_group_level else
                    "Coverage is shared inside each group where the feasible "
                    "region is identical; groups with more than one distinct "
                    "region are labelled per player."),
                "limitation": (
                    "Coverage intervals are a marginal approximation: Stage 03 "
                    "persists per-cell inside_share, not joint draws, so cell "
                    "correlation is not propagated."),
            },
            "cut_gaps": {
                "rule": ("cells with demand weight > 0 and inside_share < "
                         "feasibility.inside_share_min, ranked by weight_share "
                         "desc then inside_share asc"),
                "inside_share_min": float(cfg.get("feasibility.inside_share_min")),
                "top_n": int(cfg.get("demand.cut_gap_top_n")),
                "gap_score": "weight_share * (1 - inside_share)",
                **gap_summary,
            },
            "cut_cards": {
                "selection": str(cfg.get("demand.card_selection")),
                "groups": list(cfg.get("demand.card_groups")),
                "players_per_group": int(cfg.get("demand.card_players_per_group")),
                "n_cards": int(n_cards),
                "cards": [
                    {"card_id": str(r["card_id"]), "nfl_id": int(r["nfl_id"]),
                     "position_group": str(r["position_group"]),
                     "role": str(r["role"]),
                     "coverage_combined": _finite_or_none(r["coverage_combined"])}
                    for _, r in card_players.iterrows()],
                "rendering": "deferred_to_stage_06_figures",
            },
            "primary_group": str(cfg.get("positions.primary_group")),
            "limitations": [
                "demand map uses tracked players only (drafted/in the data), a "
                "selected group — not all NFL players",
                "coverage intervals are a marginal approximation from persisted "
                "per-cell inside_share",
                "Combine groups are AT_BOUNDARY, so coverage is group-level and "
                "does not estimate player-to-player variation on this sample",
                "outcome weighting is defined for DL only and is NOT_ESTIMABLE "
                "when player_play labels are absent",
            ],
            "warnings": list(dict.fromkeys(self.warnings)),
            "open_decisions": ["D124", "D125"],
            "prereg_needs": [
                "demand positions and snap-type scope", "demand bin edges",
                "outcome weighting definition and null semantics",
                "coverage formula and observed/extrapolated split",
                "cut-gap rule and top-n", "bootstrap unit and reps",
                "coverage interval method"],
            "note": ("This stage writes Cut Card DATA only. Rendering belongs to "
                     "stage 06 and is not performed here."),
        }
        return manifest

    # -- run ------------------------------------------------------------------
    def run(self) -> DemandCoverageResult:
        rows: list[dict] = []
        for group in self.positions:
            rows.extend(self._demand_group(group))
        demand_map = pd.DataFrame(rows, columns=DEMAND_TABLE_COLUMNS)

        total_eligible = int(demand_map[demand_map["weight_mode"] == "frequency"]["n_turns"].sum())
        if total_eligible != self.n_eligible:
            raise common.StageRefused(
                f"{STAGE}: the frequency demand map sums to {total_eligible} turns "
                f"but {self.n_eligible} turns are eligible; refusing to publish an "
                "inconsistent demand map")

        coverage, coverage_meta = self._build_coverage()
        ranking = self._ranking()
        gap_players = pd.DataFrame(self.gap_rows, columns=CUT_GAP_PLAYER_COLUMNS)
        if len(gap_players):
            gap_players = gap_players.sort_values(
                ["position_group", "nfl_id", "gap_rank"], kind="stable").reset_index(drop=True)
        gap_position = self._position_gaps()
        card_cells, card_players, cards = self._cut_cards(ranking)
        manifest = self._manifest(coverage_meta, ranking, card_players, len(cards))
        return DemandCoverageResult(
            demand_map=demand_map, coverage=coverage, ranking=ranking,
            cut_gaps_players=gap_players, cut_gaps_position=gap_position,
            cut_card_cells=card_cells, cut_card_players=card_players,
            manifest=manifest, warnings=list(self.warnings))


# ------------------------------------------------------------------ formatting

def _rel(cfg: common.Config, path) -> str:
    """A repo-relative path when possible (the manifest names inputs portably)."""
    if not path:
        return ""
    for candidate in (Path(path), Path(path).resolve()):
        try:
            return str(candidate.relative_to(Path(cfg.path).parent))
        except (ValueError, OSError):
            continue
    return str(path)


def _fmt(x, spec: str = ".3f") -> str:
    try:
        value = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return format(value, spec) if np.isfinite(value) else "n/a"
