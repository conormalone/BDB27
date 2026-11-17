"""Multi-candidate attribution: the identified set, three variants, and the frame.

Specification: ``notes/multi-candidate-spec.md`` (committed as 8726120, before
any code or coefficient). Its §9 is the API contract this module implements.

What the question is, and what it is not
----------------------------------------
The frozen rule (``outcome_link.MATCH_RULE``, D197) attaches a turn to **the
single** opposing route-runner within 3 yd of the DB at the break frame; two or
more candidates are ambiguous and dropped, never tie-broken. On the FULL band
that is 870 turns. The dataset records **no opponent identifier**, so "which man
was he covering" is not knowable from the file and is not asked here. The
question that does have an answer is how far the choice among the *visible*
candidates can move the D199 coefficient.

That answer is exact, not estimated. For a specification whose design matrix
``D`` is built only from the DB's own turn and the DB's own ``player_play`` row,
``D`` does not depend on which candidate is attached, so

    beta(pairing) = c . y,      c = row 1 of pinv(D'D) D'

is linear in the per-turn choice and

    beta_min = sum_i min_j (c_i * y_ij)
    beta_max = sum_i max_j (c_i * y_ij)

encloses the coefficient under **every** admissible pairing at once, with no
search. The interval is the identified set. It carries no sampling error: it is
reported beside the 95% CI, never combined with it.

Turns that share a turn key (three band rows on this machine are second
detections of the same ``frame_start``) share one choice: the per-row weights
``c_i`` are summed per turn key before the min/max, so a pairing can never split
one coverage across two candidates.

Honesty rules carried in the code
---------------------------------
* Every frame carries ``attribution = "INFERENCE — ..."``. No output names a man.
* ``two_candidate_separation_gap_yd`` is MAX(sep) - MIN(sep) of the candidates'
  separation values. It is **not** a distance between two players, and no column
  or sentence here calls it one.
* No gates. Zero ambiguous turns, zero separations, an empty band: each returns
  zeros and ``NOT FITTED``. Only a missing/unreadable required file or a missing
  required column raises, and the file error names every path it tried.
* Route fixed effects make ``D`` depend on the attached candidate, so that
  specification carries no bound and is reported as a variant only.

Sample construction is ``outcome_test``'s own SQL: :func:`_prepare` builds the
same tables and :func:`build_sample` delegates to the same single-candidate
query, so an identity check against ``outputs/outcome_test/attributed_turns.csv``
is a comparison, not a coincidence.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import duckdb
import numpy as np
import pandas as pd

from . import outcome_test as ot

# ------------------------------------------------------------------ constants
#: The four bounded specifications of §3/O2. Route fixed effects are excluded on
#: purpose: their design depends on the attached candidate, so no closed form.
SPECS: tuple[str, ...] = ("primary", "player_fe", "pass_only", "man_only")

#: The three resolution variants of §3 of the first spec text (the diagnostic's
#: labelling of the two separation-order extremes).
PAIRING_RULES: tuple[str, ...] = ("target", "min_sep", "max_sep")

#: Route A's sub-rules, in the fixed precedence of the amended spec §3.
ROUTE_A_RULES: tuple[str, ...] = ("target", "description", "alignment")

#: Route A's labels. The frozen single match is the only ``RULE`` attribution.
LABEL_RULE = "RULE"
ROUTE_A_LABELS: dict[str, str] = {
    "target": "INFERENCE — target",
    "description": "INFERENCE — description",
    "alignment": "INFERENCE — alignment",
}

#: The alignment implication table, fixed by the amended spec §3 A3. Both
#: players' alignments are observed; only the pairing implication is inferred,
#: and only where it is a positional correspondence rather than a coverage
#: theory. Safety pairs are counted and never used.
ALIGNMENT_IMPLIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "CB": ("WR", ("SLOT_WR", "TE")),
    "SLOT_CB": ("SLOT_WR", ("WR", "TE")),
}

#: The candidate-level columns every frame carries (the contract's names).
CANDIDATE_FIELDS: tuple[str, ...] = (
    "receiver_nfl_id", "receiver_position", "receiver_route", "separation_yd",
    "receiver_target",
)

#: Fixed seed and replication count for the AC3 invariant. Not tuned.
RANDOM_REPS = 20
RANDOM_SEED = 20261117

#: The D199 sensitivity's pass results (``notes/outcome-test-spec.md`` §5).
PASS_RESULTS: tuple[str, ...] = ("C", "I")

#: §4 reading-rule labels.
LABEL_CONTAINED = "BOUND CONTAINED IN CI"
LABEL_SIGN = "SIGN IDENTIFIED"
LABEL_NOT_IDENTIFIED = "BOUND — NOT IDENTIFIED"
LABEL_NOT_FITTED = "NOT FITTED"
#: Prefix used only when the design has no more rows than columns, i.e. when OLS
#: is structurally under-determined. It is a description of the arithmetic, not
#: a gate: the coefficient is still printed, with its n and its parameter count.
LABEL_DESCRIPTIVE = "DESCRIPTIVE ONLY — under-determined (n <= parameters)"

ATTRIBUTION = (
    "INFERENCE — multi-candidate attribution; the dataset records no opponent "
    "identifier, so no pairing here identifies the man covered"
)

#: What the notebook/script must say whenever candidate geometry is requested
#: without frame-level tracking.
GEOMETRY_NOT_EVALUABLE = (
    "NOT EVALUABLE — candidate distances need frame-level tracking, which is not "
    "on this machine; the separation gap is not a distance between two players"
)

#: Tracking files the geometry step looks for when none is passed explicitly.
#: A file whose name says it is a sample is never picked up: the truncation on
#: this machine cannot answer these questions and must not stand in for the full
#: file (geometric-attribution-spec.md §3).
DEFAULT_TRACKING_NAMES: tuple[str, ...] = (
    "game_tracking.csv", "game_tracking_2023.csv", "game_tracking_2024.csv",
    "game_tracking_2025.csv",
)

#: The separations are read from ``player_play.csv`` as text then cast, exactly
#: as ``outcome_test`` does; the raw file is never loaded into pandas.
_TURN_COLUMNS = ("game_id", "play_id", "nfl_id", "frame_start", "position",
                 "season", "abs_turn_angle", "entry_speed", "apex_speed",
                 "apex_retention", "speed_retention", "plant", "turn_type")

# ------------------------------------------------------------------ plumbing
def connect(memory_limit: str = "1400MB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    """The project's scratch connection (memory capped, threads fixed)."""
    return ot.connect(memory_limit=memory_limit, threads=threads)


def _posix(path: str | Path) -> str:
    return Path(path).as_posix()


def _require_files(paths: Sequence[str | Path]) -> None:
    """Raise once, naming **every** path that was tried and does not exist."""
    tried = [Path(p) for p in paths]
    missing = [str(p) for p in tried if not p.exists()]
    if missing:
        listing = "\n".join(f"  - {p}" for p in tried)
        raise FileNotFoundError(
            "required input(s) missing — every path tried:\n"
            f"{listing}\nmissing:\n"
            + "\n".join(f"  - {p}" for p in missing))


def _derive_multiple_sql() -> str:
    """``SINGLE_CANDIDATE_SQL`` with the candidate join loosened, plus a rank.

    The text is *derived*, never re-typed: the only edits are the two lines that
    require the candidate to carry a separation, the ``WHERE`` clause, and one
    added column. A failed derivation raises at import rather than silently
    producing a frame identical to the single one.
    """
    sql = ot.SINGLE_CANDIDATE_SQL
    edits = (
        ("                 AND r.separation_at_pass_forward IS NOT NULL\n", ""),
        ("        WHERE k.n_opp_route = 1 AND k.n_opp_route_sep = 1",
         "        WHERE k.n_opp_route_sep >= 2"),
        ("               k.n_opp_route, k.n_opp_route_sep,",
         "               k.n_opp_route, k.n_opp_route_sep,\n"
         "               ROW_NUMBER() OVER (\n"
         "                 PARTITION BY b.game_id, b.play_id, b.nfl_id, b.frame_start,\n"
         "                              b.abs_turn_angle\n"
         "                 ORDER BY r.nfl_id) AS candidate_rank,"),
    )
    for old, new in edits:
        if old not in sql:
            raise RuntimeError(
                "ambiguity_attribution: cannot derive the multi-candidate SQL — "
                f"outcome_test.SINGLE_CANDIDATE_SQL no longer contains {old!r}")
        sql = sql.replace(old, new, 1)
    return sql


def _derive_all_sql() -> str:
    """Every turn with at least one visible candidate, one row per candidate.

    Route A must see the turns neither frame holds (two candidates with one
    separation, one candidate with none), so it runs on this wider frame. It is
    the multiple-candidate text with the ``WHERE`` widened and the DB's own
    ``lined_up_position`` added — A3 needs the DB's alignment class.
    """
    sql = _MULTIPLE_SQL.replace(
        "        WHERE k.n_opp_route_sep >= 2",
        "        WHERE k.n_opp_route >= 1").replace(
        "               o.own_team,",
        "               o.own_team,\n"
        "               d.lined_up_position AS own_position,", 1)
    if "own_position" not in sql or "k.n_opp_route >= 1" not in sql:
        raise RuntimeError("ambiguity_attribution: cannot derive the all-candidate SQL")
    return sql


_SINGLE_SQL = ot.SINGLE_CANDIDATE_SQL
_MULTIPLE_SQL = _derive_multiple_sql()
_ALL_SQL = _derive_all_sql()
_prepare = ot.prepare_tables


def build_sample(con: duckdb.DuckDBPyConnection,
                 turns_path: str | Path,
                 player_play_path: str | Path) -> pd.DataFrame:
    """One row per elimination-attributed 30-60 degree DB game turn (D199).

    Unchanged behaviour: the same tables, the same query, the same
    ``derive_columns`` call. It is the frozen-only frame, and it exists here as
    well as in ``outcome_test`` because the two must never disagree.
    """
    _require_files([turns_path, player_play_path])
    _prepare(con, turns_path, player_play_path)
    df = con.execute(_SINGLE_SQL + """
        ORDER BY game_id, play_id, nfl_id, frame_start""").df()
    return ot.derive_columns(df)


def build_frame(turns_path: str | Path,
                player_play_path: str | Path,
                *,
                status: str = "single",
                con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """The analysis frame, one code path for the notebook and the script.

    ``status="single"``   one row per turn: the D199 sample (AC1).
    ``status="multiple"`` one row per (turn, candidate) for the turns with two or
    more candidates carrying a separation (the 870).

    Both carry ``attribution`` and ``status``; nothing else differs in shape, so
    ``derive_columns`` and every specification apply to either without a branch.
    """
    if status not in ("single", "multiple"):
        raise ValueError(f"status must be 'single' or 'multiple', got {status!r}")
    _require_files([turns_path, player_play_path])
    own_con = con is None
    con = con or connect()
    try:
        _prepare(con, turns_path, player_play_path)
        sql = _SINGLE_SQL if status == "single" else _MULTIPLE_SQL
        order = "game_id, play_id, nfl_id, frame_start"
        if status == "multiple":
            order += ", abs_turn_angle, candidate_rank"
        df = con.execute(sql + f" ORDER BY {order}").df()
    finally:
        if own_con:
            con.close()
    out = ot.derive_columns(df)
    out["status"] = status
    # The frozen single match is the only ``RULE`` attribution; everything else
    # is an inference (amended spec §9).
    out["attribution"] = LABEL_RULE if status == "single" else ATTRIBUTION
    return out


def all_candidates_frame(turns_path: str | Path, player_play_path: str | Path,
                         *,
                         con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """One row per (turn, candidate) for every turn with a visible candidate.

    Route A's input: the two frames above deliberately hold only the turns with
    a usable separation, and Route A must also see the 111 turns with two
    candidates of which one has a separation, and the 271 with one candidate and
    no separation.
    """
    _require_files([turns_path, player_play_path])
    own_con = con is None
    con = con or connect()
    try:
        _prepare(con, turns_path, player_play_path)
        df = con.execute(_ALL_SQL + """
            ORDER BY game_id, play_id, nfl_id, frame_start, abs_turn_angle,
                     candidate_rank""").df()
    finally:
        if own_con:
            con.close()
    out = ot.derive_columns(df)
    out["status"] = "all"
    out["attribution"] = ATTRIBUTION
    return out


def analysis_frame(frame_single: pd.DataFrame,
                   frame_multiple: pd.DataFrame) -> pd.DataFrame:
    """Single turns (fixed y) stacked above the ambiguous turns' candidates.

    This is the population the identified set is defined on: 1,731 + 870 =
    2,601 turns, one row per candidate for the 870. Order is deterministic.
    """
    cols = [c for c in frame_single.columns if c in frame_multiple.columns]
    extra = [c for c in frame_multiple.columns if c not in cols]
    both = pd.concat([frame_single[cols], frame_multiple[cols + extra]],
                     ignore_index=True)
    return both.sort_values(list(ot.TURN_KEY) + ["abs_turn_angle"],
                            kind="mergesort").reset_index(drop=True)


def candidate_columns(df: pd.DataFrame) -> list[str]:
    """The candidate-level columns present in a frame, in contract order."""
    return [c for c in CANDIDATE_FIELDS if c in df.columns]


# ------------------------------------------------------------------ frame checks
_DESIGN_REQUIRED: tuple[str, ...] = (
    "separation_yd", "apex_retention", "entry_speed", "position_fe",
    "season_fe", "coverage_family", "nfl_id", "pass_result",
)


def _check_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Name every missing required column at once, then get out of the way."""
    missing = [c for c in (*ot.TURN_KEY, *_DESIGN_REQUIRED) if c not in frame.columns]
    if missing:
        raise ValueError("frame is missing required column(s): " + ", ".join(missing))
    return frame


def _rows_of(frame: pd.DataFrame, status: str) -> pd.DataFrame:
    """Rows of one status. A frame without a ``status`` column is split by key.

    Turns whose key appears once are fixed-y (single); turns whose key repeats
    are the ambiguous ones. That keeps the function honest on a hand-built
    frame while the normal path uses the explicit column.
    """
    if "status" in frame.columns:
        return frame.loc[frame["status"] == status]
    sizes = frame.groupby(list(ot.TURN_KEY), sort=False)[ot.TURN_KEY[0]].transform("size")
    is_single = sizes.to_numpy() == 1
    return frame.loc[is_single] if status == "single" else frame.loc[~is_single]


def _design_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per band row: the candidate with the smallest id represents it.

    ``abs_turn_angle`` is part of the collapse key because three band rows on
    this machine are second detections of the same ``frame_start`` and are
    genuinely two rows of the turn table. The choice is shared across them
    (see :func:`_weighted_choices`), the design row is not.
    """
    key = [*ot.TURN_KEY, "abs_turn_angle"]
    order = frame.sort_values([*ot.TURN_KEY, "abs_turn_angle", "receiver_nfl_id"],
                              kind="mergesort")
    return order.drop_duplicates(subset=key, keep="first").reset_index(drop=True)


# ------------------------------------------------------------------ specifications
def _spec_kwargs(spec: str) -> dict:
    if spec == "player_fe":
        return {"player_fe": True}
    return {}


def _apply_spec(df: pd.DataFrame, spec: str) -> tuple[pd.DataFrame, dict, str]:
    """The population and design switches one specification adds to D199's."""
    if spec not in SPECS:
        raise ValueError(f"spec must be one of {SPECS}, got {spec!r}")
    if spec == "pass_only":
        sub = df.loc[df["pass_result"].isin(PASS_RESULTS)]
        return sub, {}, "pass plays only (pass_result in C/I)"
    if spec == "man_only":
        sub = df.loc[df["coverage_family"] == "MAN"]
        return sub, {}, "MAN coverage only (coverage_family == 'MAN')"
    if spec == "player_fe":
        return df, _spec_kwargs(spec), "player fixed effects (within-player slope)"
    return df, {}, "D199 primary: controls, SE clustered by player"


def _choice_sets(frame: pd.DataFrame) -> dict[tuple, np.ndarray]:
    """Per turn key, the distinct separations a pairing may choose from."""
    sub = frame.loc[frame["separation_yd"].notna(),
                    [*ot.TURN_KEY, "receiver_nfl_id", "separation_yd"]]
    out: dict[tuple, np.ndarray] = {}
    if not len(sub):
        return out
    keep = sub.drop_duplicates(subset=[*ot.TURN_KEY, "receiver_nfl_id"])
    for key, grp in keep.groupby(list(ot.TURN_KEY), sort=True):
        out[tuple(key)] = np.sort(grp["separation_yd"].to_numpy(float))
    return out


def _leveraged_weights(X: np.ndarray) -> np.ndarray:
    """``c`` from the spec: row 1 of ``pinv(D'D) D'``, so ``beta = c . y``.

    Uses the same ``np.linalg.pinv`` as ``outcome_test.cluster_ols``, which is
    why the closed form reproduces the estimator to floating-point precision
    rather than approximately (AC4).
    """
    XtX_inv = np.linalg.pinv(X.T @ X)
    return X @ XtX_inv[1, :]


def _weights_by_turn(one: pd.DataFrame, c: np.ndarray) -> pd.Series:
    """Sum ``c_i`` per turn key: a pairing may not split one coverage."""
    w = pd.DataFrame({**{k: one[k].to_numpy() for k in ot.TURN_KEY}, "c": c})
    return w.groupby(list(ot.TURN_KEY), sort=True)["c"].sum()


def _finite(*values) -> bool:
    return all(v is not None and np.isfinite(v) for v in values)


def _branch_label(beta_min, beta_max, ci_lo, ci_hi, under_determined: bool,
                  width: float | None = None,
                  ci_width: float | None = None) -> dict:
    """§4's reading rule, applied in the table's own order: A, then C, then B.

    If an interval straddles zero, is not contained in the CI and is *narrower*
    than it, neither A's nor B's written condition holds. The branch is still B
    (the table has no fourth row), and ``branch_note`` says so with the two
    widths — the measured flags are on every row so the case is visible rather
    than papered over.
    """
    if not _finite(beta_min, beta_max):
        return {"branch": LABEL_NOT_FITTED, "label": LABEL_NOT_FITTED,
                "contains_zero": None, "inside_ci": None, "branch_note": ""}
    contains_zero = bool(beta_min <= 0.0 <= beta_max)
    inside_ci = bool(_finite(ci_lo, ci_hi) and beta_min >= ci_lo and beta_max <= ci_hi)
    note = ""
    if inside_ci:
        branch = LABEL_CONTAINED
    elif not contains_zero:
        branch = LABEL_SIGN
    else:
        branch = LABEL_NOT_IDENTIFIED
        if _finite(width, ci_width) and width <= ci_width:
            note = (
                f"straddles zero and is not contained in the CI, but its width "
                f"({width:.3f} yd) is NOT larger than the CI width ({ci_width:.3f} yd), "
                "so §4 branch B's written condition is only half met; the branch is "
                "assigned by the table's order (A, then C, then B)")
    label = f"{LABEL_DESCRIPTIVE} — {branch}" if under_determined else branch
    return {"branch": branch, "label": label, "contains_zero": contains_zero,
            "inside_ci": inside_ci, "branch_note": note}


def _empty_identified(spec: str, note: str) -> dict:
    return {"spec": spec, "beta_frozen": np.nan, "n_frozen": 0, "ci_lo": np.nan,
            "ci_hi": np.nan, "beta_min": np.nan, "beta_max": np.nan,
            "width": np.nan, "ci_width": np.nan, "branch": LABEL_NOT_FITTED,
            "label": LABEL_NOT_FITTED, "n_ambiguous": 0, "n_with_separation": 0,
            "n_population": 0, "contains_zero": None, "inside_ci": None,
            "attribution": ATTRIBUTION, "note": note}


def identified_set(frame: pd.DataFrame, spec: str = "primary") -> dict:
    """O2: the frozen-only coefficient, its 95% CI, and the identified set.

    ``frame`` is the analysis frame (single turns plus the ambiguous turns'
    candidates); :func:`analysis_frame` builds it. The bound is exact under
    every admissible pairing of the ambiguous turns, conditional on the true
    receiver being among the visible candidates — which is optimistic in the
    direction of precision, because the cohort hides candidates.
    """
    frame = _check_frame(frame)
    if spec not in SPECS:
        raise ValueError(f"spec must be one of {SPECS}, got {spec!r}")

    single = _rows_of(frame, "single")
    one = _design_rows(frame)
    fit_rows, kwargs, spec_note = _apply_spec(one, spec)
    frozen_rows, frozen_kwargs, _ = _apply_spec(single, spec)

    if len(fit_rows) == 0:
        return _empty_identified(
            spec, "empty population: no turn in this specification carries a "
                  "separation to pair")

    X, _, names = ot.design(fit_rows, **kwargs)
    c = _leveraged_weights(X)
    wkey = _weights_by_turn(fit_rows, c)
    choices = _choice_sets(frame)

    beta_min = 0.0
    beta_max = 0.0
    n_ambiguous = 0
    n_used = 0
    for key, w in wkey.items():
        ys = choices.get(tuple(key))
        if ys is None or not len(ys):
            continue
        n_used += 1
        if len(ys) > 1:
            n_ambiguous += 1
        terms = w * ys
        beta_min += float(terms.min())
        beta_max += float(terms.max())
    if n_used == 0:
        return _empty_identified(
            spec, "no turn in this specification has a candidate separation")

    frozen = None
    if len(frozen_rows) >= 2:
        frozen = ot.fit(frozen_rows, f"frozen-only, {spec}", **frozen_kwargs)
    beta_frozen = frozen.beta if frozen else np.nan
    ci_lo = frozen.lo if frozen else np.nan
    ci_hi = frozen.hi if frozen else np.nan
    under = len(fit_rows) <= len(names)
    width = float(beta_max - beta_min)
    ci_width = float(ci_hi - ci_lo) if _finite(ci_lo, ci_hi) else float("nan")
    branch = _branch_label(beta_min, beta_max, ci_lo, ci_hi, under,
                           width=width, ci_width=ci_width)

    note = (
        f"{spec_note}; population {n_used:,} turns "
        f"({len(single):,} single-candidate + {n_ambiguous:,} with two or more). "
        "Bound is conditional on the true receiver being among the visible "
        "candidates, so the true set can only be wider; it carries no sampling "
        "error and is not a confidence interval. The frozen-only CI is the last "
        "line of notes/multi-candidate-spec.md §1 (D199)."
    )
    return {
        "spec": spec,
        "beta_frozen": float(beta_frozen),
        "n_frozen": int(frozen.n) if frozen else 0,
        "ci_lo": float(ci_lo),
        "ci_hi": float(ci_hi),
        "beta_min": float(beta_min),
        "beta_max": float(beta_max),
        "width": float(beta_max - beta_min),
        "ci_width": ci_width,
        "branch": branch["branch"],
        "branch_note": branch["branch_note"],
        "label": branch["label"],
        "n_ambiguous": int(n_ambiguous),
        "n_with_separation": int(n_used),
        "n_population": int(len(fit_rows)),
        "n_parameters": int(len(names)),
        "frozen_clusters": int(frozen.n_clusters) if frozen else 0,
        "frozen_beta_rounded": round(float(beta_frozen), 2) if frozen else None,
        "frozen_ci_rounded": ([round(float(ci_lo), 2), round(float(ci_hi), 2)]
                              if frozen else None),
        "contains_zero": branch["contains_zero"],
        "inside_ci": branch["inside_ci"],
        "attribution": ATTRIBUTION,
        "note": note,
    }


# ------------------------------------------------------------------ variants
def _pairing(frame: pd.DataFrame, rule: str,
             rng: np.random.Generator | None = None) -> dict[tuple, float]:
    """One separation per turn key under a named rule.

    ``target``  the candidate carrying ``target`` where exactly one does; turns
                with zero or several are absent, which changes the population.
    ``min_sep`` / ``max_sep``  the smallest / largest visible separation.
    ``random``  a uniform draw among the visible separations (AC3).
    """
    if rule not in (*PAIRING_RULES, "random"):
        raise ValueError(f"rule must be one of {(*PAIRING_RULES, 'random')}, got {rule!r}")
    out: dict[tuple, float] = {}
    sub = frame.loc[frame["separation_yd"].notna(),
                    [*ot.TURN_KEY, "receiver_nfl_id", "separation_yd",
                     "receiver_target"]]
    if not len(sub):
        return out
    for key, grp in sub.groupby(list(ot.TURN_KEY), sort=True):
        grp = grp.drop_duplicates(subset="receiver_nfl_id").sort_values("receiver_nfl_id")
        ys = grp["separation_yd"].to_numpy(float)
        if rule == "target":
            hits = grp["receiver_target"].fillna(False).to_numpy(dtype=bool)
            if int(hits.sum()) != 1:
                continue
            out[tuple(key)] = float(ys[hits][0])
        elif rule == "min_sep":
            out[tuple(key)] = float(np.nanmin(ys))
        elif rule == "max_sep":
            out[tuple(key)] = float(np.nanmax(ys))
        else:  # random
            out[tuple(key)] = float(ys[int(rng.integers(len(ys)))])
    return out


def _assigned_rows(frame: pd.DataFrame, spec: str,
                   pairs: dict[tuple, float]):
    """The design rows with one chosen ``y`` per turn, restricted to the chosen."""
    one = _design_rows(frame)
    one, kwargs, _ = _apply_spec(one, spec)
    if not len(one):
        return None, None, kwargs
    keys = list(zip(*[one[k].to_numpy() for k in ot.TURN_KEY]))
    keep = np.array([k in pairs for k in keys], dtype=bool)
    if not keep.any():
        return None, None, kwargs
    assigned = one.loc[keep].copy()
    assigned["separation_yd"] = [pairs[k] for k, ok in zip(keys, keep) if ok]
    return assigned, keys, kwargs


def _variant_from_pairs(frame: pd.DataFrame, spec: str, rule: str,
                        pairs: dict[tuple, float]) -> dict:
    assigned, _, kwargs = _assigned_rows(frame, spec, pairs)
    populations = {
        "target": "single turns + the ambiguous turns where exactly one candidate "
                  "carries target (targeted passes only — a population change)",
        "min_sep": "single turns + every ambiguous turn, paired to its smaller "
                   "separation (no population change)",
        "max_sep": "single turns + every ambiguous turn, paired to its larger "
                   "separation (no population change)",
        "random": "single turns + every ambiguous turn, uniform draw per turn",
    }
    base = {"spec": spec, "rule": rule, "variant": _variant_name(rule),
            "population": populations[rule], "attribution": ATTRIBUTION}
    if assigned is None or len(assigned) < 2:
        base.update({"n": 0 if assigned is None else int(len(assigned)),
                     "beta": np.nan, "se": np.nan, "lo": np.nan, "hi": np.nan,
                     "p": np.nan, "n_clusters": 0, "label": LABEL_NOT_FITTED,
                     "note": "no turn is assignable under this rule"})
        return base
    fit = ot.fit(assigned, f"{spec} / {rule}", **kwargs)
    n_parameters = int(ot.design(assigned, **kwargs)[0].shape[1])
    base.update({"n": int(fit.n), "beta": float(fit.beta), "se": float(fit.se),
                 "lo": float(fit.lo), "hi": float(fit.hi), "p": float(fit.p),
                 "n_clusters": int(fit.n_clusters),
                 "label": (LABEL_DESCRIPTIVE
                           if fit.n <= n_parameters else ""),
                 "note": ""})
    return base


def variant_fit(frame: pd.DataFrame, rule: str = "min_sep",
                spec: str = "primary") -> dict:
    """O3: the coefficient under one resolution rule, with its population.

    Every variant is an ``INFERENCE``. V1 (``target``) selects targeted passes
    and therefore changes the population; its selection is printed, and it is
    not allowed to set the label.
    """
    frame = _check_frame(frame)
    if rule not in PAIRING_RULES:
        raise ValueError(f"rule must be one of {PAIRING_RULES}, got {rule!r}")
    return _variant_from_pairs(frame, spec, rule, _pairing(frame, rule))


def _variant_name(rule: str) -> str:
    return {"target": "V1 INFERENCE — target",
            "min_sep": "V2 INFERENCE — smallest separation",
            "max_sep": "V3 INFERENCE — largest separation",
            "random": "INFERENCE — random pairing"}[rule]


def closed_form_beta(frame: pd.DataFrame, spec: str,
                     pairs: dict[tuple, float]) -> float:
    """``c . y`` for one fixed pairing — the same number the fit returns."""
    frame = _check_frame(frame)
    one, _, _ = _assigned_rows(frame, spec, pairs)
    if one is None or len(one) == 0:
        return float("nan")
    X, y, _ = ot.design(one, **_spec_kwargs(spec))
    return float(_leveraged_weights(X) @ y)


def random_assignments(frame: pd.DataFrame, spec: str = "primary",
                       reps: int = RANDOM_REPS,
                       seed: int = RANDOM_SEED) -> pd.DataFrame:
    """AC3: fixed-seed random pairings, each inside the identified set.

    Returns one row per replicate: the closed-form coefficient, the coefficient
    from ``outcome_test.fit`` on the same pairing, and whether both lie inside
    ``[beta_min, beta_max]``. The two are printed together because a pairing
    outside the bound would mean the bound, not the pairing, is wrong.
    """
    frame = _check_frame(frame)
    bound = identified_set(frame, spec)
    rng = np.random.default_rng(seed)
    rows = []
    for rep in range(int(reps)):
        pairs = _pairing(frame, "random", rng=rng)
        closed = closed_form_beta(frame, spec, pairs)
        fitted = _variant_from_pairs(frame, spec, "random", pairs)
        lo, hi = bound["beta_min"], bound["beta_max"]
        inside = bool(_finite(lo, hi, closed) and lo <= closed <= hi)
        rows.append({
            "spec": spec, "rep": rep, "seed": int(seed),
            "beta_closed_form": closed,
            "beta_fit": fitted["beta"],
            "abs_difference": (abs(closed - fitted["beta"])
                               if _finite(closed, fitted["beta"]) else np.nan),
            "beta_min": lo, "beta_max": hi,
            "inside_bound": inside,
            "n_assigned": fitted["n"],
            "attribution": ATTRIBUTION,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ composition
def composition(frame_single: pd.DataFrame,
                frame_multiple: pd.DataFrame) -> pd.DataFrame:
    """O4: the 870 against the 1,731, every share with its denominator.

    They are a different population until shown otherwise: if a variable differs,
    the identified set describes that population and the difference is reported,
    never adjusted away.
    """
    single = frame_single
    # One row per turn on the ambiguous side too: the comparison is 870 turns
    # against 1,731, not 1,911 candidate rows against 1,731 turns. The
    # representative candidate is the smallest id — a deterministic choice that
    # prefers no one, since every variable here except ``separation_yd`` is the
    # DB's own row and identical across a turn's candidates.
    multi_rows = frame_multiple.drop_duplicates(subset=[*ot.TURN_KEY, "receiver_nfl_id"])
    multi = (multi_rows.sort_values([*ot.TURN_KEY, "receiver_nfl_id"], kind="mergesort")
             .drop_duplicates(subset=list(ot.TURN_KEY), keep="first"))
    n_single, n_multi = int(len(single)), int(len(multi))
    rows: list[dict] = []

    def add(variable, level, metric, a, b, total_a, total_b, diff):
        rows.append({
            "variable": variable, "level": level, "metric": metric,
            "single": a, "ambiguous": b,
            "single_total": total_a, "ambiguous_total": total_b,
            "single_share": (a / total_a) if metric == "share" and total_a else np.nan,
            "ambiguous_share": (b / total_b) if metric == "share" and total_b else np.nan,
            "difference": diff,
            "attribution": ATTRIBUTION,
        })

    for variable in ("coverage_family", "position_fe", "season_fe", "turn_type",
                     "team_coverage_man_zone"):
        if variable not in single.columns:
            continue
        sa = single[variable].fillna("MISSING").astype(str).value_counts()
        mb = multi[variable].fillna("MISSING").astype(str).value_counts()
        for level in sorted(set(sa.index) | set(mb.index)):
            a = int(sa.get(level, 0))
            b = int(mb.get(level, 0))
            add(variable, level, "share", a, b, n_single, n_multi,
                round(b / n_multi - a / n_single, 6) if n_single and n_multi else np.nan)

    for variable in ("entry_speed", "apex_retention", "abs_turn_angle",
                     "separation_yd", "cushion"):
        if variable not in single.columns:
            continue
        a_ser = pd.to_numeric(single[variable], errors="coerce").dropna()
        b_ser = pd.to_numeric(multi[variable], errors="coerce").dropna()
        for metric, fn in (("mean", lambda s: float(s.mean())),
                           ("median", lambda s: float(s.median())),
                           ("sd", lambda s: float(s.std(ddof=1)) if len(s) > 1 else np.nan),
                           ("n", lambda s: float(len(s)))):
            a = fn(a_ser) if len(a_ser) else np.nan
            b = fn(b_ser) if len(b_ser) else np.nan
            add(variable, "value", metric, a, b, len(a_ser), len(b_ser),
                round(b - a, 6) if _finite(a, b) else np.nan)

    # The artefact's separation gap, carried with the sentence that it is not a
    # distance between two players.
    gap_multi = (multi_rows.groupby(list(ot.TURN_KEY))["separation_yd"]
                 .agg(lambda s: float(s.max() - s.min())))
    add("separation_gap_yd", "MAX(sep) - MIN(sep)", "median", np.nan,
        float(gap_multi.median()) if len(gap_multi) else np.nan, 0, len(gap_multi),
        np.nan)
    rows[-1]["note"] = ("not a distance between two players; shown only for the "
                        "870, whose separation span the pairing can choose within")
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ funnel (O1)
_FUNNEL_EXPECTED: tuple[tuple[str, int], ...] = (
    ("band_turns", 7777),
    ("no_opposing_player_play_row", 1462),
    ("opposing_rows_no_route_runner", 3306),
    ("turns_with_opposing_route_runner", 3009),
    ("turns_with_a_candidate_separation", 2601),
    ("turns_with_exactly_one_candidate_separation", 1731),
    ("turns_with_two_or_more_candidate_separations", 870),
)


def funnel(turns_path, player_play_path, *,
           con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """O1: §1's counts recomputed from the raw files, each with its denominator.

    A reconciliation, not a result. ``expected_2026_11_14`` is the number the
    committed artefact carries; ``matches`` says whether this recomputation
    agrees. Nothing here is a gate — a disagreement is printed, not raised.
    """
    _require_files([turns_path, player_play_path])
    own_con = con is None
    con = con or connect()
    try:
        _prepare(con, turns_path, player_play_path)
        band = int(con.execute("SELECT COUNT(*) FROM band").fetchone()[0])
        row = con.execute("""
            WITH k AS (
              SELECT b.game_id, b.play_id, b.nfl_id, b.frame_start, b.abs_turn_angle,
                COUNT(*) FILTER (WHERE p.team_abbr IS NOT NULL
                                   AND p.team_abbr <> o.own_team) AS opp_rows,
                COUNT(*) FILTER (WHERE p.team_abbr IS NOT NULL
                                   AND p.team_abbr <> o.own_team
                                   AND p.route_ran IS NOT NULL) AS opp_route,
                COUNT(*) FILTER (WHERE p.team_abbr IS NOT NULL
                                   AND p.team_abbr <> o.own_team
                                   AND p.route_ran IS NOT NULL
                                   AND p.separation_at_pass_forward IS NOT NULL)
                  AS opp_sep
              FROM band b
              LEFT JOIN own o ON o.game_id = b.game_id AND o.play_id = b.play_id
                             AND o.nfl_id = b.nfl_id
              LEFT JOIN pp p ON p.game_id = b.game_id AND p.play_id = b.play_id
                            AND p.nfl_id <> b.nfl_id
              GROUP BY 1, 2, 3, 4, 5)
            SELECT
              COUNT(*) FILTER (WHERE opp_rows = 0),
              COUNT(*) FILTER (WHERE opp_rows > 0 AND opp_route = 0),
              COUNT(*) FILTER (WHERE opp_route >= 1),
              COUNT(*) FILTER (WHERE opp_sep >= 1),
              COUNT(*) FILTER (WHERE opp_sep = 1),
              COUNT(*) FILTER (WHERE opp_sep >= 2)
            FROM k""").fetchone()
        # Two groupings, on purpose. Band rows are the unit of the frames; the
        # four-column turn key is the unit of the committed artefact, which
        # merges the second detections that share a ``frame_start``.
        per_turn = {}
        for grouping, extra, group_by in (("band_row", ", b.abs_turn_angle",
                                          "1, 2, 3, 4, 5"),
                                         ("turn_key", "", "1, 2, 3, 4")):
            per_turn[grouping] = con.execute(f"""
                WITH k AS (
                  SELECT b.game_id, b.play_id, b.nfl_id, b.frame_start{extra},
                    COUNT(*) FILTER (WHERE p.team_abbr IS NOT NULL
                                       AND p.team_abbr <> o.own_team
                                       AND p.route_ran IS NOT NULL) AS n_candidates
                  FROM band b
                  JOIN own o ON o.game_id = b.game_id AND o.play_id = b.play_id
                            AND o.nfl_id = b.nfl_id
                  JOIN pp p ON p.game_id = b.game_id AND p.play_id = b.play_id
                           AND p.nfl_id <> b.nfl_id
                  GROUP BY {group_by})
                SELECT n_candidates, COUNT(*) AS n_turns FROM k
                GROUP BY 1 ORDER BY 1""").df()
    finally:
        if own_con:
            con.close()

    (no_row, no_route, with_route, with_sep, single, multi) = (int(v) for v in row)
    counts = {
        "band_turns": band,
        "no_opposing_player_play_row": no_row,
        "opposing_rows_no_route_runner": no_route,
        "turns_with_opposing_route_runner": with_route,
        "turns_with_a_candidate_separation": with_sep,
        "turns_with_exactly_one_candidate_separation": single,
        "turns_with_two_or_more_candidate_separations": multi,
    }
    expected = dict(_FUNNEL_EXPECTED)
    steps = [
        ("band_turns", band, band,
         "band rows: valid game DB turns with 30 <= |angle| < 60"),
        ("no_opposing_player_play_row", no_row, band,
         "no opposing player_play row at all — the cohort hides the candidates"),
        ("opposing_rows_no_route_runner", no_route, band,
         "opposing rows exist but none carries route_ran"),
        ("turns_with_opposing_route_runner", with_route, band,
         "at least one visible opposing route-runner"),
        ("turns_with_a_candidate_separation", with_sep, band,
         "at least one candidate with separation_at_pass_forward"),
        ("turns_with_exactly_one_candidate_separation", single, with_sep,
         "exactly one candidate with a separation: the D199 sample"),
        ("turns_with_two_or_more_candidate_separations", multi, with_sep,
         "two or more: ambiguous under the frozen rule, dropped by it"),
    ]
    rows = [{"step": name, "n": int(n), "denominator": int(den),
             "denominator_name": ("band_turns" if den == band else
                                  "turns_with_a_candidate_separation"),
             "share": round(n / den, 6) if den else np.nan,
             "expected_2026_11_14": expected.get(name),
             "matches": expected.get(name) == int(n),
             "grouping": "band_row",
             "note": note}
            for name, n, den, note in steps]

    # The candidate-level counts of §1: 815 / 185 / 5 / 1 at two or more. The
    # committed artefact groups on the four-column turn key, so that block is the
    # one that must match it; the band-row block beside it is the frame's unit.
    committed = {1: 2002, 2: 815, 3: 185, 4: 5, 6: 1}
    for grouping, table in per_turn.items():
        counts = {int(r["n_candidates"]): int(r["n_turns"])
                  for _, r in table.iterrows()}
        total = int(sum(counts.values()))
        for n_cand in sorted(counts):
            expected = committed.get(n_cand) if grouping == "turn_key" else None
            rows.append({
                "step": f"turns_with_{n_cand}_visible_candidate"
                        f"{'s' if n_cand != 1 else ''}",
                "n": counts[n_cand], "denominator": total,
                "denominator_name": "turns_with_a_joinable_own_row",
                "share": round(counts[n_cand] / total, 6) if total else np.nan,
                "expected_2026_11_14": expected,
                "matches": (expected == counts[n_cand]) if expected is not None else None,
                "grouping": grouping,
                "note": ("counted per band row — the unit of the frames" if grouping
                         == "band_row" else
                         "counted per (game_id, play_id, nfl_id, frame_start), as the "
                         "committed feasibility artefact counts: band rows that are "
                         "second detections sharing a frame_start merge here"),
            })
    return pd.DataFrame(rows)

# ------------------------------------------------------------------ geometry (O5)
def _tracking_candidates(explicit: Sequence[str | Path] | None) -> list[Path]:
    if explicit:
        out: list[Path] = []
        for raw in explicit:
            p = Path(raw).expanduser()
            if p.is_dir():
                # Auto-discovery skips sample-named files; a file named
                # explicitly is taken as given.
                out.extend(sorted(q for q in p.glob("*.csv")
                                  if "sample" not in q.name.lower()))
            else:
                out.append(p)
        return out
    root = Path(__file__).resolve().parents[2]
    return [root / "data" / "raw" / name for name in DEFAULT_TRACKING_NAMES]


def candidate_distance_table(frames: pd.DataFrame, turns, player_play,
                             *, tracking: Sequence[str | Path] | None = None,
                             con: duckdb.DuckDBPyConnection | None = None
                             ) -> pd.DataFrame:
    """O5: how far apart the candidates are, for the two-player gap and the margin.

    ``dist_yd`` is the frozen rule's own distance applied to **every** candidate;
    ``nearest_gap_yd`` is second-nearest minus nearest (the margin);
    ``cand_gap_yd`` is the distance between the two nearest candidates — the
    physical quantity the artefact's ``separation gap`` is not.

    All three need frame-level tracking. With no tracking file found the function
    returns one row per ambiguous turn with those columns null and
    ``geometry_status`` naming the paths it tried — it does not raise, and it
    does not substitute the separation gap for a distance.
    """
    key = list(ot.TURN_KEY)
    if "status" in frames.columns:
        amb = frames.loc[frames["status"] == "multiple"]
    else:
        sizes = frames.groupby(key, sort=False)[key[0]].transform("size")
        amb = frames.loc[sizes.to_numpy() > 1]
    turn_keys = amb.drop_duplicates(subset=key)[key].reset_index(drop=True)
    base = turn_keys.copy()
    per_turn = (amb.groupby(key, sort=False)["receiver_nfl_id"].nunique()
                .rename("n_candidates").reset_index())
    base = base.merge(per_turn, on=key, how="left")
    base["attribution"] = ATTRIBUTION

    tried = _tracking_candidates(tracking)
    found = [p for p in tried if p.exists()]
    if not found:
        out = base.copy()
        for col in ("dist_nearest_yd", "dist_second_yd", "nearest_gap_yd",
                    "cand_gap_yd"):
            out[col] = np.nan
        out["n_candidates_tracked"] = 0
        out["geometry_status"] = GEOMETRY_NOT_EVALUABLE
        out["paths_tried"] = "; ".join(str(p) for p in tried)
        out["order_reversed_snap_to_break"] = pd.NA
        return out

    from . import geometric_match as gm  # local: only the tracked path needs it
    band = turns if not isinstance(turns, (str, Path)) else gm.load_band(turns)
    band = band[band["valid"] & (band["setting"] == "game")
                & (band["position_group"] == "DB")]
    band = band[(band["abs_turn_angle"] >= ot.BAND_LO)
                & (band["abs_turn_angle"] < ot.BAND_HI)]
    band = band[band.set_index(key).index.isin(turn_keys.set_index(key).index)]
    band = band.reset_index(drop=True)
    shard_dir = Path(tempfile.mkdtemp(prefix="bdb27_ambiguity_"))
    shards = gm.extract_plays(found, band[key[:2]].drop_duplicates(), shard_dir,
                              resume=False)
    own_con = con is None
    con = con or gm.connect()
    try:
        bps = gm.recover_break_frames(con, band, shards)
        cf = gm.candidate_frame(con, bps, shards)
    finally:
        if own_con:
            con.close()
    cand = amb[key + ["receiver_nfl_id"]].drop_duplicates()
    cf = cf.rename(columns={"cand_nfl_id": "receiver_nfl_id"})
    cf = cf.merge(cand, on=key + ["receiver_nfl_id"], how="inner")
    cf = cf.sort_values(key + ["dist_yd", "receiver_nfl_id"], kind="mergesort")
    rows = []
    for k, grp in cf.groupby(key, sort=True):
        d = grp["dist_yd"].to_numpy(float)
        rec = dict(zip(key, k))
        rec["n_candidates_tracked"] = int(len(d))
        rec["dist_nearest_yd"] = float(d.min()) if len(d) else np.nan
        rec["dist_second_yd"] = float(np.sort(d)[1]) if len(d) > 1 else np.nan
        rec["nearest_gap_yd"] = (rec["dist_second_yd"] - rec["dist_nearest_yd"]
                                 if len(d) > 1 else np.nan)
        if len(d) > 1:
            two = grp.iloc[:2]
            rec["cand_gap_yd"] = float(np.hypot(
                two["x"].iloc[0] - two["x"].iloc[1],
                two["y"].iloc[0] - two["y"].iloc[1]))
        else:
            rec["cand_gap_yd"] = np.nan
        rows.append(rec)
    dist = pd.DataFrame(rows)
    out = base.merge(dist, on=key, how="left") if len(dist) else base
    if "dist_nearest_yd" not in out.columns:
        for col in ("dist_nearest_yd", "dist_second_yd", "nearest_gap_yd",
                    "cand_gap_yd"):
            out[col] = np.nan
        out["n_candidates_tracked"] = 0
    # Per-turn status, so a turn whose play the tracker does not cover says so
    # instead of silently carrying nulls (the truncation covers 12,638 of
    # 31,720 plays, so "tracked" as a file-level label would be a lie per turn).
    recovered = set(map(tuple, bps.loc[bps["break_frame_ok"], key]
                        .to_numpy().tolist()))
    covered = set(map(tuple, band[key].to_numpy().tolist()))

    def _status(row):
        k = tuple(row[c] for c in key)
        if k not in covered:
            return "untracked_play"
        if k not in recovered:
            return "break_frame_unrecovered"
        if not np.isfinite(row.get("dist_nearest_yd", np.nan)):
            return "no_candidate_tracked"
        return "tracked"

    out["geometry_status"] = out.apply(_status, axis=1)
    out["paths_tried"] = "; ".join(str(p) for p in found)
    # The snap-frame ordering needs the snap coordinates, which the turn table
    # does not carry: null with a reason rather than an invented alignment.
    out["order_reversed_snap_to_break"] = pd.NA
    out["order_reversed_note"] = (
        "not computed: the turn table carries no snap-frame coordinates, so the "
        "relative-motion test in spec §6 needs an alignment this module does not "
        "invent")
    return out


# ------------------------------------------------------------------ provenance
def file_sha256(path: str | Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(start: str | Path | None = None) -> str | None:
    import subprocess
    try:
        cwd = str(start or Path(__file__).resolve().parents[2])
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                              capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # a checkout without git still produces a manifest
        return None


def write_manifest(out_dir: str | Path, inputs: Sequence[str | Path],
                   outputs: Sequence[str | Path], config: dict,
                   extra: dict | None = None) -> dict:
    """sha256 of every input and output, the git HEAD and a UTC stamp."""
    out = Path(out_dir)
    root = Path(__file__).resolve().parents[2]
    manifest = {
        "written_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_head": git_head(root),
        "spec": "notes/multi-candidate-spec.md",
        "attribution": ATTRIBUTION,
        "config": config,
        "inputs": {},
        "outputs": {},
    }
    for p in inputs:
        p = Path(p)
        if p.exists():
            manifest["inputs"][p.resolve().relative_to(root.resolve()).as_posix()
                               if str(p.resolve()).startswith(str(root)) else str(p)] = {
                "sha256": file_sha256(p), "bytes": p.stat().st_size}
    for p in outputs:
        p = Path(p)
        if p.exists():
            manifest["outputs"][p.resolve().relative_to(root.resolve()).as_posix()
                                if str(p.resolve()).startswith(str(root)) else str(p)] = {
                "sha256": file_sha256(p), "bytes": p.stat().st_size}
    if extra:
        manifest.update(extra)
    (out / "manifest.json").write_text(
        __import__("json").dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


# ------------------------------------------------------------------ verdict text
def plain_english_verdict(summary: dict) -> list[str]:
    """The run's verdict, built only from measured fields. No label upgrades."""
    lines: list[str] = []
    fn = summary.get("funnel", {})
    lines.append(
        f"Funnel [FULL]: {fn.get('band_turns', 0):,} band turns; "
        f"{fn.get('no_opposing_player_play_row', 0):,} "
        f"({_pct(fn.get('no_opposing_player_play_row'), fn.get('band_turns'))}) have no "
        f"opposing player row; {fn.get('turns_with_opposing_route_runner', 0):,} carry a "
        f"visible opposing route-runner; "
        f"{fn.get('turns_with_a_candidate_separation', 0):,} carry a separation, of which "
        f"{fn.get('turns_with_exactly_one_candidate_separation', 0):,} are single-candidate "
        f"and {fn.get('turns_with_two_or_more_candidate_separations', 0):,} are ambiguous.")
    for row in summary.get("bounds", []):
        lines.append(
            f"{row['spec']}: frozen only beta {_num(row['beta_frozen'])} "
            f"95% CI [{_num(row['ci_lo'])}, {_num(row['ci_hi'])}], "
            f"n={row['n_frozen']:,}, {row['frozen_clusters']:,} clusters | "
            f"identified set [{_num(row['beta_min'])}, {_num(row['beta_max'])}], "
            f"width {_num(row['width'])} yd vs CI width {_num(row['ci_width'])} yd | "
            f"{row['label']}")
    primary = next((r for r in summary.get("bounds", [])
                    if r["spec"] == "primary"), None)
    if primary:
        lines.append("")
        lines.append(f"Label set by O2 only (spec §4): {primary['branch']}.")
        if primary["branch"] == LABEL_CONTAINED:
            lines.append(
                "The multi-candidate turns cannot move the primary coefficient beyond "
                "its own sampling error; D199's null is not an artefact of dropping them.")
        elif primary["branch"] == LABEL_NOT_IDENTIFIED:
            lines.append(
                "The primary coefficient is bounded, not identified: the interval "
                "straddles zero and is wider than the CI. The null is neither "
                "established nor overturned on this population.")
        elif primary["branch"] == LABEL_SIGN:
            lines.append(
                "Under every admissible pairing the primary sign is the same. "
                "This is not a claim in TASK.md.")
        else:
            lines.append("The primary bound is NOT FITTED — see the note on the row.")
    for row in summary.get("variants", []):
        lines.append(
            f"{row['variant']}: beta {_num(row['beta'])}, n={row['n']:,} — "
            f"{row['population']}")
    lines.append("")
    lines.append(
        "Every pairing above is an INFERENCE: the dataset records no opponent "
        "identifier, so none of them names the man covered. two_candidate_"
        "separation_gap_yd is MAX(sep) - MIN(sep), not a distance between two players.")
    return lines


def _num(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(v):
        return "n/a"
    return f"{v:+.3f}"


def _pct(n, d) -> str:
    try:
        n, d = float(n), float(d)
    except (TypeError, ValueError):
        return "n/a"
    return f"{n / d:.1%}" if d else "n/a"
