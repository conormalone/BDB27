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

import re
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

#: V4's radius: the frozen rule's own 3 yd, used as a tie-break gate.
V4_RADIUS_YD = 3.0

#: V4 is a tie-break and must never wear the frozen rule's clothes. Its label
#: says both what it does and that the frozen rule refuses to do it.
V4_LABEL = "V4 INFERENCE — nearest inside 3 yd (TIE-BREAK)"
V4_ATTRIBUTION = (
    "INFERENCE — V4 nearest-man TIE-BREAK inside 3 yd; the frozen rule "
    "explicitly refuses tie-breaks, so this is a variant, not the frozen "
    "rule's result")

#: Margin buckets: second-nearest minus nearest distance, in yards. The edges
#: are report buckets only; no edge selects or drops a turn.
MARGIN_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("< 0.25 yd", 0.0, 0.25),
    ("0.25–0.5 yd", 0.25, 0.5),
    ("0.5–1 yd", 0.5, 1.0),
    ("> 1 yd", 1.0, float("inf")),
)

#: Where the cohort's display names live (spec §3 A2 resolves a parsed
#: ``X.Surname`` against them: first-initial + surname equality).
DEFAULT_PLAYERS = Path(__file__).resolve().parents[2] / "data" / "raw" / "players.csv"

#: The receiving classes the alignment table is defined over (§3 A3).
RECEIVING_CLASSES: tuple[str, ...] = ("WR", "SLOT_WR", "TE")

#: Suffixes dropped before a display name's surname is taken.
_NAME_SUFFIXES: frozenset[str] = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

#: ``pass … to X.Surname`` — the first such token after the pass verb (§3 A2).
#: The surname may carry an internal space ("A.St. Brown"), which is why the
#: second group allows one.
PASS_TO_NAME = re.compile(
    r"\bpass\b[^\n]*?\bto\s+([A-Z])\.\s*"
    r"([A-Za-z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*)?)")

#: The four slots of §0, in precedence order, with the label each carries.
SLOT_ORDER: tuple[str, ...] = ("frozen", "geo_5", "geo_8", "route_a")
SLOT_LABELS: dict[str, str] = {
    "frozen": LABEL_RULE,
    "geo_5": "INFERENCE — geometry 5 yd",
    "geo_8": "INFERENCE — geometry 8 yd",
    "route_a": "INFERENCE — Route A",
}

#: The fifteen tables one complete run writes. The notebook and
#: ``scripts/ambiguity_attribution.py`` write THIS set into the SAME directory:
#: when they differed, a reader could open a committed file the run never wrote
#: and read it as this run's result (Conor, 2026-11-17). ``precedence.csv`` was
#: added by spec Amendment 3 (2026-11-18): the slots' disjointness and the
#: Route A/geometry overlap are a table of their own, not a claim in a comment.
ARTEFACT_TABLES: tuple[str, ...] = (
    "funnel.csv", "multi_turns.csv", "bounds.csv", "variants.csv",
    "composition.csv", "random_pairings.csv", "geometry.csv",
    "route_a_turns.csv", "route_a_audit.csv", "assembly.csv",
    "single_turn_geometry.csv", "within_3yd.csv", "margin_buckets.csv",
    "v4_nearest.csv", "precedence.csv",
)

#: What the notebook/script must say whenever candidate geometry is requested
#: without frame-level tracking.
GEOMETRY_NOT_EVALUABLE = (
    "NOT EVALUABLE — candidate distances need frame-level tracking at the break "
    "frame, which this run did not have; the separation gap is not a distance "
    "between two players"
)

#: The documented full-data layout (``config.yaml`` mirrors it). Discovery is by
#: NAME PATTERN, not by this list (Conor, 2026-11-17): an export named anything
#: is found, because a hard-coded list is what silently missed his files.
DEFAULT_TRACKING_NAMES: tuple[str, ...] = (
    "game_tracking.csv", "game_tracking_2023.csv", "game_tracking_2024.csv",
    "game_tracking_2025.csv",
)

#: How a game-tracking CSV is recognised when a *directory* is searched:
#: ``*.csv`` whose name contains ``tracking``, case-insensitively. A file whose
#: name says it is a sample is never picked up — a truncation cannot answer the
#: distance questions and must not stand in for the full file — and the Combine
#: tracking export is a different table, not game tracking.
TRACKING_NAME_HINT = "tracking"
TRACKING_NAME_EXCLUDE: tuple[str, ...] = ("sample", "combine")

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
        "nearest_3yd": "single turns + the ambiguous turns with two or more "
                       "candidates inside 3 yd at the break frame, paired to the "
                       "nearest (a TIE-BREAK, not the frozen rule)",
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
            "nearest_3yd": V4_LABEL,
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
def multi_turn_table(multi: pd.DataFrame) -> pd.DataFrame:
    """One row per ambiguous turn: its candidate count and separation span.

    The script wrote this table and the notebook did not, which is how a reader
    came to open a ``multi_turns.csv`` this run had never produced.
    """
    key = list(ot.TURN_KEY)
    columns = [*key, "n_candidates", "n_candidates_with_separation",
               "separation_min_yd", "separation_max_yd", "separation_gap_yd",
               "n_target", "attribution"]
    if not len(multi):
        return pd.DataFrame(columns=columns)
    df = multi.drop_duplicates(subset=[*key, "receiver_nfl_id"])
    out = df.groupby(key, sort=True).agg(
        n_candidates=("receiver_nfl_id", "nunique"),
        n_candidates_with_separation=("separation_yd", lambda s: int(s.notna().sum())),
        separation_min_yd=("separation_yd", "min"),
        separation_max_yd=("separation_yd", "max"),
        n_target=("receiver_target", lambda s: int(s.fillna(False).sum())),
    ).reset_index()
    out["separation_gap_yd"] = out["separation_max_yd"] - out["separation_min_yd"]
    out["attribution"] = ATTRIBUTION
    return out


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


# ------------------------------------------------------------------ Route A (spec §3)
def _norm_name(token) -> str:
    """Letters only, lower case — ``Taylor-Britt`` and ``Taylor Britt`` agree."""
    return re.sub(r"[^a-z]", "", str(token).lower())


def _surname_key(display_name) -> str:
    """The surname of a display name, dropping a trailing generational suffix."""
    parts = [p for p in str(display_name).replace(".", " ").split() if p]
    while parts and parts[-1].lower() in _NAME_SUFFIXES:
        parts.pop()
    return _norm_name(parts[-1]) if parts else ""


def display_index(players_path: str | Path | None = None
                  ) -> dict[tuple[str, str], list[int]]:
    """``(first initial, surname key) -> [nfl_id, ...]`` from ``players.csv``.

    A list, not a single id: 17 of the cohort's 510 display names share an
    initial + surname, and A2 must be able to say ``multiple_matches`` rather
    than pick one of them.
    """
    path = Path(players_path) if players_path else DEFAULT_PLAYERS
    _require_files([path])
    df = pd.read_csv(path, usecols=["nfl_id", "display_name"])
    out: dict[tuple[str, str], list[int]] = {}
    for nfl_id, name in zip(df["nfl_id"], df["display_name"]):
        key = (str(name).strip()[:1].upper(), _surname_key(name))
        out.setdefault(key, []).append(int(nfl_id))
    return out


def parse_description(description) -> tuple[str, str] | None:
    """``(initial, surname_key)`` from ``… pass … to X.Surname``, or ``None``."""
    if not isinstance(description, str) or not description:
        return None
    m = PASS_TO_NAME.search(description)
    if not m:
        return None
    initial, surname = m.group(1).upper(), m.group(2)
    return initial, _norm_name(surname.split()[-1])


def _descriptions(player_play_path: str | Path) -> dict[tuple[int, int], str]:
    """``(game_id, play_id) -> play_description``, one row per play."""
    path = Path(player_play_path)
    _require_files([path])
    con = connect()
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE pp_desc AS SELECT * FROM read_csv(
              '{_posix(path)}', header=true, all_varchar=true, nullstr=['NA',''])""")
        df = con.execute("""
            SELECT DISTINCT CAST(game_id AS BIGINT) AS game_id,
                            CAST(play_id AS BIGINT) AS play_id,
                            play_description
            FROM pp_desc WHERE play_description IS NOT NULL""").df()
    finally:
        con.close()
    return {(int(g), int(p)): str(d)
            for g, p, d in zip(df["game_id"], df["play_id"], df["play_description"])}


def _route_a_one(rows: pd.DataFrame, description, index, rules: Sequence[str]
                 ) -> tuple[str | None, int | None, list[tuple[str, str]]]:
    """One turn through A1 → A2 → A3, in the fixed order. First hit wins.

    Returns ``(rule, attributed_nfl_id, attempts)``. ``attempts`` is one
    ``(rule, outcome)`` per rule that looked at the turn, in order, ending at
    the first attribution; every miss carries its own reason, so a turn that
    fails A2 and then A3 is counted once under each and never double-counted
    within a rule (AC5).
    """
    rows = rows.drop_duplicates(subset="receiver_nfl_id")
    visible = [int(v) for v in rows["receiver_nfl_id"]]
    attempts: list[tuple[str, str]] = []

    if "target" in rules:
        hits = rows.loc[rows["receiver_target"].fillna(False).astype(bool)]
        hits = hits.drop_duplicates(subset="receiver_nfl_id")
        if len(hits) == 1:
            attempts.append(("target", "attributed"))
            return "target", int(hits["receiver_nfl_id"].iloc[0]), attempts
        attempts.append(("target", "no_single_target" if len(hits) == 0
                         else "multiple_targets"))

    if "description" in rules:
        if not description:
            attempts.append(("description", "no_description"))
        else:
            parsed = parse_description(description)
            if parsed is None:
                attempts.append(("description", "no_name_after_pass_verb"))
            else:
                matches = [i for i in index.get(parsed, []) if i in visible]
                if len(matches) == 1:
                    attempts.append(("description", "attributed"))
                    return "description", int(matches[0]), attempts
                if matches:
                    attempts.append(("description", "multiple_matches"))
                else:
                    named_in_cohort = bool(index.get(parsed))
                    attempts.append(("description",
                                     "named_man_not_visible" if named_in_cohort
                                     else "named_man_not_a_cohort_player"))

    if "alignment" in rules:
        if len(rows) != 2:
            attempts.append(("alignment", "not_two_candidates"))
        else:
            positions = [str(p) for p in rows["receiver_position"]]
            if positions[0] == positions[1]:
                attempts.append(("alignment", "same_class"))
            else:
                own = str(rows["own_position"].iloc[0])
                mapping = ALIGNMENT_IMPLIES.get(own)
                if mapping is None:
                    attempts.append(("alignment", "safety_pair" if "SAFETY" in own
                                     else "own_class_unmapped"))
                else:
                    implies, others = mapping
                    hit = rows.loc[rows["receiver_position"] == implies,
                                   "receiver_nfl_id"]
                    other = [p for p in positions if p != implies]
                    if len(hit) == 1 and other and other[0] in others:
                        attempts.append(("alignment", "attributed"))
                        return "alignment", int(hit.iloc[0]), attempts
                    attempts.append(("alignment", "pair_not_supported"))

    return None, None, attempts


def _route_a_pass(frame_all: pd.DataFrame, player_play_path: str | Path, *,
                  rules: Sequence[str] = ROUTE_A_RULES,
                  exclude: Iterable[tuple] | None = None,
                  players_path: str | Path | None = None,
                  descriptions: dict | None = None,
                  index: dict | None = None
                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The attributed turns and the audit table, from one pass over the frame."""
    for rule in rules:
        if rule not in ROUTE_A_RULES:
            raise ValueError(f"rule must be one of {ROUTE_A_RULES}, got {rule!r}")
    key = list(ot.TURN_KEY)
    frame = frame_all.drop_duplicates(subset=[*key, "receiver_nfl_id"],
                                      keep="first")
    if exclude is not None:
        taken = pd.DataFrame(list(exclude), columns=key)
        frame = frame.merge(taken.assign(_taken=True), on=key, how="left")
        frame = frame[frame["_taken"].isna()].drop(columns="_taken")
    desc = descriptions if descriptions is not None else _descriptions(
        player_play_path)
    idx = index if index is not None else display_index(players_path)

    attributed: list[dict] = []
    attempts: list[dict] = []
    for k, grp in frame.groupby(key, sort=True):
        rule, nfl_id, tried = _route_a_one(
            grp, desc.get((int(k[0]), int(k[1]))), idx, rules)
        row = dict(zip(key, k))
        for tried_rule, outcome in tried:
            attempts.append({**row, "route_a_rule": tried_rule,
                             "outcome": outcome})
        if rule:
            attributed.append({
                **row, "route_a_rule": rule,
                "attribution_label": ROUTE_A_LABELS[rule],
                "attributed_nfl_id": nfl_id,
                "coverage_family": grp["coverage_family"].iloc[0],
                "attribution": ATTRIBUTION,
            })

    out = pd.DataFrame(attributed) if attributed else pd.DataFrame(
        columns=[*key, "route_a_rule", "attribution_label",
                 "attributed_nfl_id", "coverage_family", "attribution"])
    seen = pd.DataFrame(attempts)
    rows: list[dict] = []
    total = int(frame[key].drop_duplicates().shape[0])
    for rule in rules:
        sub = seen[seen["route_a_rule"] == rule]
        rows.append({"route_a_rule": rule, "outcome": "attributed",
                     "reason": "",
                     "n": int((sub["outcome"] == "attributed").sum()),
                     "denominator": total})
        for reason, n in sub.loc[sub["outcome"] != "attributed",
                                 "outcome"].value_counts().items():
            rows.append({"route_a_rule": rule, "outcome": "unresolved",
                         "reason": str(reason), "n": int(n),
                         "denominator": total})
    return out, pd.DataFrame(rows)


def route_a_tables(frame_all: pd.DataFrame, player_play_path: str | Path, *,
                   rules: Sequence[str] = ROUTE_A_RULES,
                   exclude: Iterable[tuple] | None = None,
                   players_path: str | Path | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The attributed turns and the audit table, from one pass over the frame."""
    return _route_a_pass(frame_all, player_play_path, rules=rules, exclude=exclude,
                         players_path=players_path)


def route_a(frame_all: pd.DataFrame, player_play_path: str | Path, *,
            rules: Sequence[str] = ROUTE_A_RULES,
            exclude: Iterable[tuple] | None = None,
            players_path: str | Path | None = None) -> pd.DataFrame:
    """One row per turn Route A can attribute, with its rule and its man (§9)."""
    return route_a_tables(frame_all, player_play_path, rules=rules,
                          exclude=exclude, players_path=players_path)[0]


def route_a_audit(frame_all: pd.DataFrame, player_play_path: str | Path, *,
                  rules: Sequence[str] = ROUTE_A_RULES,
                  exclude: Iterable[tuple] | None = None,
                  players_path: str | Path | None = None) -> pd.DataFrame:
    """Every turn Route A saw, by rule and by unresolved reason (AC5)."""
    return route_a_tables(frame_all, player_play_path, rules=rules,
                          exclude=exclude, players_path=players_path)[1]


def alignment_counts(player_play_path: str | Path, *,
                     con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """AC10: plays with exactly two receivers, by the pair's two classes.

    Recomputed from ``player_play.csv``, printed with its denominator and
    compared with the anchor the spec quotes (9,341 plays; 7,009 mixed —
    WR+slot 4,585, WR+TE 1,626, slot+TE 609 — and 2,332 same-class). Where the
    recomputation disagrees the row says so; nothing is adjusted to match.
    """
    _require_files([player_play_path])
    own_con = con is None
    con = con or connect()
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE pp_ac AS SELECT * FROM read_csv(
              '{_posix(player_play_path)}', header=true, all_varchar=true,
              nullstr=['NA',''])""")
        table = con.execute("""
            WITH r AS (
              SELECT DISTINCT CAST(game_id AS BIGINT) AS game_id,
                              CAST(play_id AS BIGINT) AS play_id,
                              CAST(nfl_id AS BIGINT) AS nfl_id,
                              lined_up_position AS pos
              FROM pp_ac
              WHERE lined_up_position IN ('WR', 'SLOT_WR', 'TE')),
            k AS (
              SELECT game_id, play_id, COUNT(*) AS n,
                     MIN(pos) AS a, MAX(pos) AS b
              FROM r GROUP BY 1, 2)
            SELECT CASE WHEN a = b THEN a || '+' || a ELSE a || '+' || b END AS pair,
                   COUNT(*) AS n_plays
            FROM k WHERE n = 2 GROUP BY 1 ORDER BY 1""").df()
        total_plays = int(con.execute(
            "SELECT COUNT(DISTINCT game_id || '-' || play_id) FROM pp_ac"
        ).fetchone()[0])
    finally:
        if own_con:
            con.close()

    #: Per-pair anchors only for the three mixed pairs the spec itemises; the
    #: same-class total and the grand total are anchored on their own rows.
    expected = {"SLOT_WR+WR": 4585, "TE+WR": 1626, "SLOT_WR+TE": 609}
    rows = []
    two = int(table["n_plays"].sum())
    for _, r in table.iterrows():
        pair, n = str(r["pair"]), int(r["n_plays"])
        parts = pair.split("+")
        mixed = len(parts) == 2 and parts[0] != parts[1]
        rows.append({"pair": pair, "mixed": mixed, "n_plays": n,
                     "two_receiver_plays": two, "all_plays": total_plays,
                     "share_of_two_receiver_plays": round(n / two, 6) if two else np.nan,
                     "spec_anchor": expected.get(pair) if mixed else None,
                     "matches": ((expected[pair] == n) if pair in expected
                                 else None)})
    mixed_n = int(table.loc[table["pair"].map(
        lambda p: p.split("+")[0] != p.split("+")[1]), "n_plays"].sum())
    rows.append({"pair": "mixed (total)", "mixed": True, "n_plays": mixed_n,
                 "two_receiver_plays": two, "all_plays": total_plays,
                 "share_of_two_receiver_plays": round(mixed_n / two, 6) if two else np.nan,
                 "spec_anchor": 7009, "matches": 7009 == mixed_n})
    rows.append({"pair": "same_class (total)", "mixed": False,
                 "n_plays": two - mixed_n, "two_receiver_plays": two,
                 "all_plays": total_plays,
                 "share_of_two_receiver_plays": round((two - mixed_n) / two, 6) if two else np.nan,
                 "spec_anchor": 2332, "matches": 2332 == (two - mixed_n)})
    rows.append({"pair": "two-receiver plays (total)", "mixed": None,
                 "n_plays": two, "two_receiver_plays": two,
                 "all_plays": total_plays,
                 "share_of_two_receiver_plays": 1.0,
                 "spec_anchor": 9341, "matches": 9341 == two})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ the one number (§0)
def assemble_attribution(counts: dict) -> dict:
    """``N = N_frozen + N_geo(5) + N_geo(8) + N_routeA`` (§0, §9).

    ``counts`` carries the four slot counts plus what the composition needs:
    ``band_turns``, ``status`` (per slot, e.g. ``NOT EVALUABLE``),
    ``route_a_by_rule`` and ``coverage``. The slots are added as given and never
    adjusted; a slot with no evidence is 0 **and** carries its status, so the
    number cannot quietly pass off a missing route as a zero measurement.
    """
    need = ("n_frozen", "n_geo_5", "n_geo_8", "n_route_a")
    missing = [k for k in need if k not in counts]
    if missing:
        raise ValueError("assemble_attribution is missing: " + ", ".join(missing))
    slots = {k: int(counts[k]) for k in need}
    negative = [k for k, v in slots.items() if v < 0]
    if negative:
        raise ValueError("counts cannot be negative: " + ", ".join(negative))
    n = sum(slots.values())
    # AC3 is checked where the counts are assembled, not only in prose: a turn
    # in two slots is a sum that counts it twice, so the number that carries it
    # is called out and ``disjoint`` says so (Amendment 3, 2026-11-18).
    n_double = int(counts.get("n_double_counted") or 0)
    band = counts.get("band_turns")
    band = int(band) if band is not None else None
    status = dict(counts.get("status") or {})
    by_rule = dict(counts.get("route_a_by_rule") or {})
    coverage = dict(counts.get("coverage") or {})

    composition = [{
        "slot": slot,
        "label": SLOT_LABELS[slot],
        "n": slots[key],
        "share_of_N": (slots[key] / n) if n else np.nan,
        "share_of_band": (slots[key] / band) if band else np.nan,
        "status": status.get(slot, ""),
    } for slot, key in zip(SLOT_ORDER, need)]

    return {
        "N": n,
        "slots": slots,
        "composition": composition,
        "band_turns": band,
        "share_of_band": (n / band) if band else None,
        "rule_share_of_N": (slots["n_frozen"] / n) if n else None,
        "inference_share_of_N": ((n - slots["n_frozen"]) / n) if n else None,
        "route_a_by_rule": by_rule,
        "coverage": coverage,
        "status": status,
        "n_double_counted": n_double,
        "disjoint": bool(n_double == 0),
        "attribution": ATTRIBUTION,
    }


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
def is_tracking_csv(path: str | Path) -> bool:
    """Is this file a game-tracking CSV, judged by name alone?

    Case-insensitive, so a Windows export named ``Game_Tracking_2024.CSV`` is
    found. ``sample`` is excluded (a truncation cannot answer a distance
    question) and ``combine`` is excluded (that is the Combine table, not game
    tracking).
    """
    name = Path(path).name.lower()
    return (name.endswith(".csv") and TRACKING_NAME_HINT in name
            and not any(word in name for word in TRACKING_NAME_EXCLUDE))


def _config_raw_dirs() -> list[Path]:
    """The raw directories ``config.yaml`` names, best-effort and optional.

    A config that cannot be parsed adds nothing and raises nothing: this is a
    search hint, never a required input.
    """
    root = Path(__file__).resolve().parents[2]
    cfg = root / "config.yaml"
    if not cfg.is_file():
        return []
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        raw = (((data.get("turn_coverage") or {}).get("paths") or {}).get("raw"))
        if not raw:
            return []
        p = Path(str(raw)).expanduser()
        return [p if p.is_absolute() else root / p]
    except Exception:                                     # noqa: BLE001 - optional
        return []


def default_tracking_roots() -> list[Path]:
    """Every directory ``TRACKING = []`` searches: the repo's raw dir + config's."""
    root = Path(__file__).resolve().parents[2]
    roots = [root / "data" / "raw"]
    for p in _config_raw_dirs():
        if p not in roots:
            roots.append(p)
    return roots


def _scan_root(root: Path) -> dict:
    """What one searched directory holds, and which files in it qualify."""
    entry: dict = {"path": str(root), "exists": root.is_dir(), "kind": "dir",
                   "files": [], "entries": []}
    if not entry["exists"]:
        return entry
    try:
        listing = sorted(root.iterdir(), key=lambda q: q.name.lower())
    except OSError as exc:                                # unreadable, not fatal
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry
    entry["entries"] = [q.name + ("/" if q.is_dir() else "") for q in listing]
    try:
        candidates = [q for q in root.rglob("*")
                      if q.is_file() and is_tracking_csv(q)]
    except OSError:                                       # no recursion: top level
        candidates = [q for q in listing if q.is_file() and is_tracking_csv(q)]
    entry["files"] = sorted(candidates, key=lambda q: str(q).lower())
    return entry


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        try:
            key = p.resolve().as_posix()
        except OSError:
            key = p.as_posix()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def discover_tracking(tracking: Sequence[str | Path] | None = None,
                      *, roots: Sequence[str | Path] | None = None) -> dict:
    """Find the tracking files, and record everywhere that was searched.

    With ``tracking`` given, a directory is globbed by :func:`is_tracking_csv`
    (recursively) and a **file is taken as given**, whatever its name — that is
    how a sample or a private export can be pointed at deliberately. With
    ``tracking`` empty, :func:`default_tracking_roots` is searched, so a
    differently-named or per-season export is found without editing code.

    Returns ``{"files": [Path], "roots": [{path, exists, kind, files,
    entries, error?}]}``. Nothing raises on a missing directory or file.
    """
    searched: list[dict] = []
    files: list[Path] = []
    if tracking:
        for raw in tracking:
            p = Path(raw).expanduser()
            if p.is_dir():
                entry = _scan_root(p)
                files.extend(entry["files"])
            else:
                entry = {"path": str(p), "exists": p.is_file(), "kind": "file",
                         "files": [p] if p.is_file() else [], "entries": []}
                if p.is_file():                           # explicitly named: as given
                    files.append(p)
            searched.append(entry)
    else:
        for r in (roots if roots is not None else default_tracking_roots()):
            entry = _scan_root(Path(r).expanduser())
            searched.append(entry)
            files.extend(entry["files"])
    return {"files": _dedupe(files), "roots": searched}


def _tracking_candidates(explicit: Sequence[str | Path] | None) -> list[Path]:
    """The files the geometry step will try — discovery, never a fixed list."""
    return discover_tracking(explicit)["files"]


def _seasons_from_names(files: Iterable[Path]) -> list[str]:
    """Seasons a filename states (``game_tracking_2024.csv`` -> 2024).

    The tracking table carries no season column, so this is the only honest
    source; when a name states none, the banner says so rather than guessing.
    """
    out: set[str] = set()
    for f in files:
        out.update(re.findall(r"(?<!\d)(20\d{2})(?!\d)", Path(f).name))
    return sorted(out)


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "size unknown"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} GB"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} MB"
    return f"{value:,} bytes"


def search_summary(tracking: Sequence[str | Path] | None = None) -> str:
    """One line naming every place searched and what was actually there.

    Used as ``paths_tried`` when discovery finds nothing, so an empty result
    still says where to look rather than printing nothing.
    """
    found = discover_tracking(tracking)
    parts: list[str] = []
    for e in found["roots"]:
        if e.get("kind") == "file":
            state = "found" if e["exists"] else "NOT FOUND"
            parts.append(f"{e['path']} (file, {state})")
        elif not e["exists"]:
            parts.append(f"{e['path']} (directory does not exist)")
        elif e.get("error"):
            parts.append(f"{e['path']} (unreadable: {e['error']})")
        else:
            names = ", ".join(e["entries"]) if e["entries"] else "empty"
            parts.append(f"{e['path']} [contains: {names}]")
    return "; ".join(parts)


def tracking_status(tracking: Sequence[str | Path] | None = None,
                    *, roots: Sequence[str | Path] | None = None,
                    counts: bool = True) -> dict:
    """FOUND / NONE FOUND, with the evidence a reader needs in one glance.

    ``files`` (absolute path + bytes), ``rows``/``plays``/``players`` read in one
    DuckDB pass, and the ``seasons`` the file names state. A file that is found
    but cannot be read is reported in ``read_error`` — it never raises, because
    the notebook runs on whatever it finds and the geometry step reports its own
    error if there is one.
    """
    found = discover_tracking(tracking, roots=roots)
    files = found["files"]
    info: dict = {
        "status": "FOUND" if files else "NONE FOUND",
        "files": [],
        "roots": found["roots"],
        "rows": None, "plays": None, "players": None, "rows_per_play": None,
        "seasons": _seasons_from_names(files),
        "total_bytes": 0,
        "read_error": None,
    }
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        info["files"].append({"path": Path(p).resolve().as_posix(), "bytes": size})
        info["total_bytes"] += int(size or 0)
    if files and counts:
        paths = [f["path"] for f in info["files"]]
        try:
            con = duckdb.connect()
            try:
                row = con.execute(
                    "SELECT COUNT(*) AS rows, "
                    "COUNT(DISTINCT game_id || '-' || play_id) AS plays, "
                    "COUNT(DISTINCT nfl_id) AS players "
                    f"FROM read_csv({paths!r}, header=true, all_varchar=true, "
                    "union_by_name=true)").fetchone()
            finally:
                con.close()
            info["rows"], info["plays"], info["players"] = (int(row[0]), int(row[1]),
                                                           int(row[2]))
            info["rows_per_play"] = (info["rows"] / info["plays"]
                                     if info["plays"] else None)
        except Exception as exc:                          # noqa: BLE001 - reported
            info["read_error"] = f"{type(exc).__name__}: {exc}"
    return info


def tracking_banner(info: dict) -> str:
    """The unmissable block the notebook prints before any geometry runs.

    FOUND names every file, its size, the rows and plays read and the seasons the
    names state. NONE FOUND names every directory searched, lists what was in it,
    says what a tracking file is and what to set in the config cell. It is a
    banner, never an exception.
    """
    lines: list[str] = []
    if info.get("status") == "FOUND":
        lines.append("=" * 79)
        lines.append("TRACKING: FOUND — the geometric slots are evaluated on these files")
        lines.append("=" * 79)
        n = len(info["files"])
        lines.append(f"tracking: FOUND — {n} file(s), "
                     f"{_fmt_bytes(info.get('total_bytes'))} total")
        for i, f in enumerate(info["files"], start=1):
            lines.append(f"  {i}. {f['path']}  ({_fmt_bytes(f.get('bytes'))})")
        if info.get("read_error"):
            lines.append(f"  rows/plays: UNREADABLE — {info['read_error']}")
            lines.append("  the geometry step reports its own error; nothing is invented")
        else:
            lines.append(f"  rows read {info['rows']:,} · plays {info['plays']:,} · "
                         f"players {info['players']:,} · "
                         f"{info['rows_per_play']:.1f} rows/play")
            seasons = info.get("seasons") or []
            lines.append("  seasons covered: "
                         + (", ".join(seasons) + " (read from the file names)"
                            if seasons else
                            "not stated in the file names — the paths above are the "
                            "only season evidence"))
        lines.append("=" * 79)
        return "\n".join(lines)

    lines.append("!" * 79)
    lines.append("TRACKING: NONE FOUND — the geometric slots are NOT evaluated")
    lines.append("!" * 79)
    lines.append("tracking: NONE FOUND")
    for e in info.get("roots") or []:
        if e.get("kind") == "file":
            state = "found" if e["exists"] else "NOT FOUND"
            lines.append(f"  looked for {e['path']} — file {state}")
        elif not e["exists"]:
            lines.append(f"  looked in {e['path']} — that directory does not exist")
        elif e.get("error"):
            lines.append(f"  looked in {e['path']} — unreadable: {e['error']}")
        else:
            entries = e.get("entries") or []
            lines.append(f"  looked in {e['path']} — {len(entries)} entries, none is "
                         "a game-tracking CSV:")
            for name in entries:
                lines.append(f"    - {name}")
            if not entries:
                lines.append("    (empty directory)")
    lines.append('  a tracking CSV is a *.csv whose name contains "tracking" (any')
    lines.append('  case) and not "sample" or "combine"; nothing above matched.')
    lines.append("  every geometric row below prints NOT EVALUABLE and adds 0 to N.")
    lines.append("  TO EVALUATE GEOMETRY, set TRACKING in cell 1 to the folder that")
    lines.append("  holds the full game-tracking CSVs, or to the files themselves:")
    for value, note in (
            (r"/full/path/to/the/folder", "a folder is globbed"),
            (r"/full/path/to/game_tracking_2023.csv", "a file is used as given"),
    ):
        example = f'      TRACKING = [r"{value}"]'
        lines.append(f"{example:<61}  # {note}")
    lines.append("  nothing here is an error: the counts below are the no-tracking counts.")
    lines.append("!" * 79)
    return "\n".join(lines)


def _paths_tried(tried: Sequence[Path],
                 tracking: Sequence[str | Path] | None) -> str:
    """The files tried, or — when discovery found none — where it looked."""
    text = "; ".join(str(p) for p in tried)
    return text or search_summary(tracking)


# ------------------------------------------------------- run status on disk
#: The file a run writes before it measures anything, and appends to as it goes.
#: A run that dies in a later cell then leaves the last step it reached on disk,
#: which console output does not (Conor, 2026-11-17).
RUN_STATUS_NAME = "run_status.txt"


def _status_path_line(label: str, value) -> str:
    if isinstance(value, (list, tuple)):
        if not value:
            return f"{label:<16}(auto-discovery)"
        parts = []
        for raw in value:
            p = Path(raw).expanduser()
            parts.append(f"{p}{'' if p.exists() else ' [MISSING]'}")
        return f"{label:<16}" + "; ".join(parts)
    p = Path(value).expanduser()
    return f"{label:<16}{p}  [{'exists' if p.exists() else 'MISSING'}]"


def _dir_listing(path: Path) -> list[str]:
    try:
        entries = sorted(path.iterdir(), key=lambda q: q.name.lower())
    except OSError as exc:
        return [f"  unreadable: {type(exc).__name__}: {exc}"]
    lines = []
    for q in entries:
        try:
            size = "<dir>" if q.is_dir() else f"{q.stat().st_size:,} bytes"
        except OSError:
            size = "unreadable"
        lines.append(f"  {q.name + ('/' if q.is_dir() else ''):<34}{size}")
    return lines or ["  (empty)"]


def run_status_header(root: str | Path, out_dir: str | Path,
                      inputs: dict, tracking: dict | None = None,
                      started: str | None = None) -> str:
    """The status block written before any analysis runs.

    Names the run's UTC start, the resolved repo root and every input path with
    its existence, what the tracking search found and what the raw directory
    actually holds. ``tracking=None`` means discovery had not finished when this
    was written — the file exists either way.
    """
    root, out_dir = Path(root), Path(out_dir)
    lines = [
        "BDB27 — multi-candidate attribution notebook, run status",
        f"started (UTC)   {started or pd.Timestamp.now(tz='UTC').isoformat()}",
        f"repo            {root}",
        f"out             {out_dir}",
    ]
    for label, value in inputs.items():
        lines.append(_status_path_line(label, value))
    if tracking is None:
        lines.append("tracking: (discovery not finished)")
    elif tracking.get("status") == "FOUND":
        lines.append(f"tracking: FOUND {len(tracking.get('files') or [])} files")
        for f in tracking.get("files") or []:
            lines.append(f"  {f['path']}  [{_fmt_bytes(f.get('bytes'))}]"
                         "  [exists]")
    else:
        lines.append("tracking: NONE")
    for entry in (tracking or {}).get("roots") or []:
        if entry.get("kind") == "file":
            continue
        lines.append(f"searched        {entry['path']}"
                     + ("" if entry.get("exists") else "  [does not exist]"))
        lines.extend(_dir_listing(Path(entry["path"])) if entry.get("exists")
                     else ["  (not a directory)"])
    lines.append("---- progress ----")
    return "\n".join(lines) + "\n"


def write_run_status(path: str | Path, text: str, *,
                     keep_progress: bool = True) -> Path:
    """Create or overwrite the run status file (parents created).

    ``keep_progress`` keeps the progress lines already appended below the
    ``---- progress ----`` marker, so refreshing the header mid-run does not
    erase the trail. The first write of a run passes ``False``, which is what
    makes every run start from a clean file rather than inheriting the last one.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    marker = "---- progress ----\n"
    if keep_progress and p.is_file():
        old = p.read_text(encoding="utf-8")
        if marker in old:
            tail = old.split(marker, 1)[1]
            if tail.strip():
                text = text.rstrip("\n") + "\n" + tail
    p.write_text(text, encoding="utf-8")
    return p


def note_run_status(path: str | Path, line: str,
                    *, reset: bool = False) -> Path:
    """Append one timestamped step to the run status file.

    ``reset=True`` truncates first, which is how a new run starts: the file is
    overwritten on every run, never appended across runs.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    with p.open("w" if reset else "a", encoding="utf-8") as fh:
        fh.write(f"{stamp}  {line}\n")
    return p


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
                    "cand_gap_yd", "n_in_radius_3"):
            out[col] = np.nan
        out["nearest_nfl_id"] = np.nan
        out["n_candidates_tracked"] = 0
        out["geometry_status"] = GEOMETRY_NOT_EVALUABLE
        out["paths_tried"] = _paths_tried(tried, tracking)
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
        rec["n_in_radius_3"] = int((d <= V4_RADIUS_YD).sum()) if len(d) else np.nan
        rec["nearest_nfl_id"] = (int(grp.iloc[0]["receiver_nfl_id"])
                                 if len(d) else np.nan)
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
                    "cand_gap_yd", "n_in_radius_3"):
            out[col] = np.nan
        out["nearest_nfl_id"] = np.nan
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


# ------------------------------------------------------------------ the geometric slots (§0, §4)
def geometry_slots(turns_path, player_play_path, frame_all: pd.DataFrame, *,
                   tracking: Sequence[str | Path] | None = None,
                   exclude: Iterable[tuple] | None = None,
                   radii: Sequence[float] = (5.0, 8.0),
                   con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """``N_geo(5)`` and ``N_geo(8)``: the radius rule, extended on what is left.

    The frozen slot is never re-picked: ``exclude`` holds the turn keys it took,
    and they are removed before anything is measured. For each remaining turn
    the distance from the DB to **every** visible candidate is taken at the
    break frame (``geometric_match.candidate_frame``, the frozen definition);
    exactly one candidate inside 5 yd takes the turn, else exactly one inside
    8 yd, else it stays for Route A. No tie is broken — a turn with two men
    inside a radius is simply not taken by that radius.

    Without tracking the slots are 0 with ``NOT EVALUABLE`` and the rows carry
    nulls; nothing is approximated from the separation gap.
    """
    key = list(ot.TURN_KEY)
    frame = frame_all.drop_duplicates(subset=[*key, "receiver_nfl_id"], keep="first")
    if exclude is not None:
        taken = pd.DataFrame(list(exclude), columns=key)
        frame = frame.merge(taken.assign(_taken=True), on=key, how="left")
        frame = frame[frame["_taken"].isna()].drop(columns="_taken")
    turn_keys = frame.drop_duplicates(subset=key)[key].reset_index(drop=True)
    tried = _tracking_candidates(tracking)
    found = [p for p in tried if p.exists()]

    if not found:
        rows = turn_keys.copy()
        rows["slot"] = ""
        rows["attributed_nfl_id"] = np.nan
        rows["dist_yd"] = np.nan
        rows["n_candidates_tracked"] = 0
        rows["geometry_status"] = GEOMETRY_NOT_EVALUABLE
        return {"n_geo_5": 0, "n_geo_8": 0, "status": GEOMETRY_NOT_EVALUABLE,
                "rows": rows, "paths_tried": _paths_tried(tried, tracking),
                "n_turns_considered": int(len(rows))}

    info = _distances_for_turns(turns_path, turn_keys,
                                frame[key + ["receiver_nfl_id"]], found, con=con)
    cf, recovered, tracked_plays = info["cf"], info["recovered"], info["tracked_plays"]

    r5, r8 = (float(radii[0]), float(radii[1]))
    rows: list[dict] = []
    for k in map(tuple, turn_keys[key].to_numpy().tolist()):
        grp = cf[(cf["game_id"] == k[0]) & (cf["play_id"] == k[1])
                 & (cf["nfl_id"] == k[2]) & (cf["frame_start"] == k[3])]
        d = grp["dist_yd"].to_numpy(float)
        rec = dict(zip(key, k))
        rec["n_candidates_tracked"] = int(len(d))
        rec["dist_yd"] = float(d.min()) if len(d) else np.nan
        slot, man = "", np.nan
        if len(d):
            in5 = grp.loc[grp["dist_yd"] <= r5, "receiver_nfl_id"].to_numpy()
            in8 = grp.loc[grp["dist_yd"] <= r8, "receiver_nfl_id"].to_numpy()
            if len(in5) == 1:
                slot, man = "5", int(in5[0])
            elif len(in8) == 1:
                slot, man = "8", int(in8[0])
        rec["slot"], rec["attributed_nfl_id"] = slot, man
        rec["geometry_status"] = _turn_status(k, tracked_plays, recovered, len(d))
        rows.append(rec)
    out = pd.DataFrame(rows)
    return {"n_geo_5": int((out["slot"] == "5").sum()),
            "n_geo_8": int((out["slot"] == "8").sum()),
            "status": (GEOMETRY_NOT_EVALUABLE if not (out["geometry_status"]
                                                      == "tracked").any()
                       else "tracked"),
            "rows": out, "paths_tried": "; ".join(str(p) for p in found),
            "n_turns_considered": int(len(out))}


def _distances_for_turns(turns_path, turn_keys: pd.DataFrame,
                         candidates: pd.DataFrame, found: Sequence[Path], *,
                         con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """The break-frame distance from each DB to each visible candidate.

    Shared by the geometric slots and the single-turn 3-yd check so the two can
    never disagree about what "distance at the break frame" means. Returns the
    matched frame plus the two coverage sets used to label a turn honestly.
    """
    key = list(ot.TURN_KEY)
    from . import geometric_match as gm
    band = gm.load_band(turns_path)
    band = band[band["valid"] & (band["setting"] == "game")
                & (band["position_group"] == "DB")]
    band = band[(band["abs_turn_angle"] >= ot.BAND_LO)
                & (band["abs_turn_angle"] < ot.BAND_HI)]
    band = band[band.set_index(key).index.isin(turn_keys.set_index(key).index)]
    band = band.reset_index(drop=True)
    shard_dir = Path(tempfile.mkdtemp(prefix="bdb27_geomslots_"))
    shards = gm.extract_plays(found, band[key[:2]].drop_duplicates(), shard_dir,
                              resume=False)
    own_con = con is None
    con = con or gm.connect()
    try:
        bps = gm.recover_break_frames(con, band, shards)
        cf = gm.candidate_frame(con, bps, shards)
        # Which plays the tracking actually covers — a play absent from the
        # file is untracked, not a recovered-frame failure, and the two must
        # not be reported as the same thing.
        tracked_plays = set(map(tuple, con.execute(
            "SELECT DISTINCT game_id, play_id FROM gm_tracking").df()
            .to_numpy().tolist()))
    finally:
        if own_con:
            con.close()
    cf = cf.rename(columns={"cand_nfl_id": "receiver_nfl_id"})
    cf = cf.merge(candidates.drop_duplicates(subset=key + ["receiver_nfl_id"]),
                  on=key + ["receiver_nfl_id"], how="inner")
    cf = cf.sort_values(key + ["dist_yd", "receiver_nfl_id"], kind="mergesort")

    recovered = set(map(tuple, bps.loc[bps["break_frame_ok"], key]
                        .to_numpy().tolist()))
    return {"cf": cf, "recovered": recovered, "tracked_plays": tracked_plays}


def _turn_status(key_tuple, tracked_plays: set, recovered: set, n_tracked: int) -> str:
    """Why a turn is or is not measured — one vocabulary for both tables."""
    if (key_tuple[0], key_tuple[1]) not in tracked_plays:
        return "untracked_play"
    if key_tuple not in recovered:
        return "break_frame_unrecovered"
    if not n_tracked:
        return "no_candidate_tracked"
    return "tracked"


def single_candidate_geometry(turns_path, frame_single: pd.DataFrame, *,
                              tracking: Sequence[str | Path] | None = None,
                              radius: float | None = None,
                              con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """The frozen 3-yd test on the 1,731 single-candidate turns — the 471 check.

    These turns are already inside ``N_frozen``: this table is a **check on the
    frozen set**, not a recovery route, and it adds nothing to ``N``. It exists
    so the 1,731 → "how many are within 3 yd" step is visible on the machine
    doing the run instead of taken on trust from a pasted laptop number (D204
    reported 471).

    Each single turn carries exactly one visible opposing route-runner, so its
    distance is that man's distance at the break frame. A turn whose play the
    tracking does not cover carries a null and a status; nothing is imputed.
    """
    from . import outcome_link as ol
    frozen_radius = float(radius if radius is not None else ol.PRIMARY_RADIUS_YD)
    key = list(ot.TURN_KEY)
    frame = frame_single.drop_duplicates(subset=[*key, "receiver_nfl_id"],
                                         keep="first")
    turn_keys = frame.drop_duplicates(subset=key)[key].reset_index(drop=True)
    tried = _tracking_candidates(tracking)
    found = [p for p in tried if p.exists()]
    base = turn_keys.copy()
    # These are the frozen single matches, so their label is ``RULE`` — the same
    # label ``build_frame(status="single")`` carries. An INFERENCE stamp here
    # would label a rule-attributed turn as an inference.
    base["attribution"] = LABEL_RULE
    common = {"radius_yd": frozen_radius, "n_turns": int(len(base)),
              "paths_tried": _paths_tried(tried, tracking),
              "d204_reported": 471}
    if not found:
        base["dist_nearest_yd"] = np.nan
        base["n_candidates_tracked"] = 0
        base["within_radius"] = pd.NA
        base["geometry_status"] = GEOMETRY_NOT_EVALUABLE
        return {**common, "status": GEOMETRY_NOT_EVALUABLE, "n_tracked": 0,
                "n_within_radius": 0, "rows": base}

    info = _distances_for_turns(turns_path, turn_keys,
                                frame[key + ["receiver_nfl_id"]], found, con=con)
    cf, recovered, tracked_plays = info["cf"], info["recovered"], info["tracked_plays"]
    rows: list[dict] = []
    for k in map(tuple, turn_keys[key].to_numpy().tolist()):
        grp = cf[(cf["game_id"] == k[0]) & (cf["play_id"] == k[1])
                 & (cf["nfl_id"] == k[2]) & (cf["frame_start"] == k[3])]
        d = grp["dist_yd"].to_numpy(float)
        rec = dict(zip(key, k))
        rec["n_candidates_tracked"] = int(len(d))
        rec["dist_nearest_yd"] = float(d.min()) if len(d) else np.nan
        rec["within_radius"] = (bool(d.min() <= frozen_radius) if len(d) else pd.NA)
        rec["geometry_status"] = _turn_status(k, tracked_plays, recovered, len(d))
        rows.append(rec)
    out = pd.DataFrame(rows)
    out["attribution"] = LABEL_RULE
    tracked = out.loc[out["geometry_status"] == "tracked"]
    return {**common, "status": ("tracked" if len(tracked) else GEOMETRY_NOT_EVALUABLE),
            "n_tracked": int(len(tracked)),
            "n_within_radius": int(tracked["within_radius"].fillna(False).astype(bool).sum()),
            "rows": out}


# --------------------------------------------------- precedence (§0, Amendment 3)
def _turn_keys_of(frame: pd.DataFrame) -> set[tuple]:
    """The distinct turn keys of a frame, as a set of tuples."""
    key = list(ot.TURN_KEY)
    if frame is None or not len(frame):
        return set()
    return set(map(tuple, frame[key].drop_duplicates().to_numpy().tolist()))


def geometry_taken_keys(geometry_rows: pd.DataFrame) -> set[tuple]:
    """The turn keys a radius slot took — ``slot`` is ``"5"``, ``"8"`` or empty."""
    if geometry_rows is None or not len(geometry_rows):
        return set()
    if "slot" not in geometry_rows.columns:
        return set()
    taken = geometry_rows.loc[geometry_rows["slot"].astype(str) != ""]
    return _turn_keys_of(taken)


def precedence_table(*, n_frozen: int, n_geo_5: int, n_geo_8: int,
                     n_geo_taken: int, geo_considered: int,
                     n_route_a: int, route_pool: int,
                     n_reference: int, reference_pool: int,
                     overlap: int, n_double_counted: int,
                     frame_turns: int, band_turns: int | None) -> pd.DataFrame:
    """The four slots in order, with each one's own denominator, in one table.

    Amendment 3's artefact: the slots, the overlap between the geometric slots
    and Route A's reference pass, the double-count check, and the number the
    defect used to report. Every row carries a denominator; none is a gate.
    """
    n = n_frozen + n_geo_5 + n_geo_8 + n_route_a
    summed = n_frozen + n_geo_taken + n_reference

    def row(item: str, count: int, denominator, note: str) -> dict:
        d = None if denominator is None else int(denominator)
        return {"item": item, "n": int(count), "denominator": d,
                "share": (int(count) / d) if d else np.nan, "note": note}

    rows = [
        row("pool_with_visible_route_runner", frame_turns, band_turns,
            "turns carrying at least one visible opposing route-runner"),
        row("N_frozen", n_frozen, frame_turns,
            "RULE — the frozen 3-yd rule; never re-picked"),
        row("N_geo_5", n_geo_5, geo_considered,
            f"{SLOT_LABELS['geo_5']} — exactly one candidate inside 5 yd"),
        row("N_geo_8", n_geo_8, geo_considered,
            f"{SLOT_LABELS['geo_8']} — exactly one inside 8 yd, else stays"),
        row("geo_5_plus_8", n_geo_taken, geo_considered,
            "turns a radius took; the pool Route A may not see"),
        row("pool_for_route_a_slot", route_pool, frame_turns,
            "what the three earlier slots left for Route A"),
        row("N_route_a", n_route_a, route_pool,
            f"{SLOT_LABELS['route_a']} — the slot, on the residual only"),
        row("route_a_reference", n_reference, reference_pool,
            "Route A on the frozen-excluded pool: the §4 cross-check pass, "
            "NOT a slot, added to nothing"),
        row("overlap_reference_with_geo", overlap, n_reference,
            "Route A turns a radius also took; the count that was missing. "
            "N = N_frozen + geo(5) + geo(8) + (reference - this)"),
        row("n_double_counted", n_double_counted, n,
            "turns appearing in more than one slot; must be 0 (AC3)"),
        row("N", n, band_turns,
            "the four slots, disjoint, summed"),
        row("N_if_both_passes_summed", summed, band_turns,
            "the double-counting number the pre-Amendment-3 code reported; "
            "an upper bound on N, never N"),
    ]
    return pd.DataFrame(rows)


def attribution_pipeline(turns_path, player_play_path, frame_all: pd.DataFrame, *,
                         frozen_keys: Iterable[tuple],
                         tracking: Sequence[str | Path] | None = None,
                         radii: Sequence[float] = (5.0, 8.0),
                         players_path: str | Path | None = None,
                         band_turns: int | None = None,
                         rules: Sequence[str] = ROUTE_A_RULES,
                         con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """The four slots of §0, computed **in the specified precedence** (§0, AC3).

    ``frozen → geo(5) → geo(8) → Route A``, each pass blind to nothing and
    allowed to see only what the passes before it left. The four sets are then
    checked for disjointness instead of assumed disjoint — a turn in two slots
    is counted and reported, not summed as if it were two.

    Route A is also run a second time on the frozen-excluded pool (the
    ``reference`` pass). That pass is **not a slot and is added to nothing**: it
    is the counterfactual the pre-Amendment-3 code mistook for the slot, and it
    is kept because §4's cross-check needs the turns both routes fire on, and
    because the overlap between it and the geometric slots is what turns
    "2,884 or thereabouts" into a number with a stated derivation.
    """
    key = list(ot.TURN_KEY)
    frozen = {tuple(k) for k in frozen_keys}
    frame_turns = int(frame_all[key].drop_duplicates().shape[0])

    # 1-2. the radii, on what the frozen rule left.
    geo = geometry_slots(turns_path, player_play_path, frame_all,
                         tracking=tracking, exclude=sorted(frozen),
                         radii=radii, con=con)
    geo_taken = geometry_taken_keys(geo["rows"])

    # 3. Route A on the residual — the slot.
    route_pool = sorted(frozen | geo_taken)
    route_turns, route_audit = route_a_tables(
        frame_all, player_play_path, rules=rules, exclude=route_pool,
        players_path=players_path)

    # 4. Route A on the frozen-excluded pool — the reference pass, never a slot.
    reference_turns, reference_audit = route_a_tables(
        frame_all, player_play_path, rules=rules, exclude=sorted(frozen),
        players_path=players_path)
    reference_keys = _turn_keys_of(reference_turns)
    overlap = len(reference_keys & geo_taken)

    # 5. disjointness, computed rather than asserted in prose.
    slot_of: dict[tuple, int] = {}
    for name, keys in (("frozen", frozen), ("geo", geo_taken),
                       ("route_a", _turn_keys_of(route_turns))):
        for k in keys:
            slot_of[k] = slot_of.get(k, 0) + 1
    double_counted = sum(1 for v in slot_of.values() if v > 1)

    counts = {
        "n_frozen": len(frozen),
        "n_geo_5": int(geo["n_geo_5"]),
        "n_geo_8": int(geo["n_geo_8"]),
        "n_route_a": int(len(route_turns)),
        "n_double_counted": int(double_counted),
        "band_turns": band_turns,
        "status": {"geo_5": geo["status"], "geo_8": geo["status"]},
        "route_a_by_rule": (route_turns["route_a_rule"].value_counts().to_dict()
                            if len(route_turns) else {}),
        "coverage": (route_turns["coverage_family"].fillna("MISSING")
                     .value_counts().to_dict() if len(route_turns) else {}),
    }
    assembled = assemble_attribution(counts)
    precedence = precedence_table(
        n_frozen=len(frozen), n_geo_5=int(geo["n_geo_5"]),
        n_geo_8=int(geo["n_geo_8"]), n_geo_taken=len(geo_taken),
        geo_considered=int(geo["n_turns_considered"]),
        n_route_a=int(len(route_turns)), route_pool=frame_turns
        - len(frozen) - len(geo_taken),
        n_reference=int(len(reference_turns)),
        reference_pool=frame_turns - len(frozen), overlap=int(overlap),
        n_double_counted=int(double_counted), frame_turns=frame_turns,
        band_turns=band_turns)
    return {
        "geo": geo,
        "route_turns": route_turns, "route_audit": route_audit,
        "route_reference_turns": reference_turns,
        "route_reference_audit": reference_audit,
        "geo_taken_keys": geo_taken,
        "overlap": int(overlap),
        "n_double_counted": int(double_counted),
        "disjoint": bool(double_counted == 0),
        "route_eligible": int(frame_turns - len(frozen) - len(geo_taken)),
        "route_eligible_reference": int(frame_turns - len(frozen)),
        "frame_turns": frame_turns,
        "assembled": assembled,
        "precedence": precedence,
        # kept under its own name so a reader cannot mistake it for N
        "N_if_both_passes_summed": int(len(frozen) + len(geo_taken)
                                       + len(reference_turns)),
    }


# ------------------------------------------------------------------ within 3 yd (§ amendment)
def _complete_geometry(geom: pd.DataFrame) -> pd.Series:
    """Turns where **every** visible candidate has a measured distance.

    ``geometry_status == "tracked"`` only means at least one candidate was
    measured. A count of men inside 3 yd and the identity of the nearest man are
    only exact when no visible candidate is missing from the tracking frame, so
    the amendment's tables use this stricter mask. When the candidate-count
    columns are absent (hand-built frames), completeness is assumed.
    """
    if "n_candidates" in geom.columns and "n_candidates_tracked" in geom.columns:
        have = pd.to_numeric(geom["n_candidates_tracked"], errors="coerce")
        want = pd.to_numeric(geom["n_candidates"], errors="coerce")
        return have.notna() & want.notna() & (have == want) & (want > 0)
    return pd.Series(True, index=geom.index)


def within_radius_distribution(geom: pd.DataFrame,
                               radius: float = V4_RADIUS_YD) -> dict:
    """How many candidates sit inside 3 yd on the ambiguous turns.

    ``geom`` is :func:`candidate_distance_table`'s per-turn output. The buckets
    are zero / exactly one / exactly two / three or more, each with its count
    and its share of the **evaluated** turns and of all ambiguous turns. This is
    the table that says whether the frozen rule's no-tie-break clause was
    guarding genuine ambiguity or discarding turns it could have resolved.

    With no frame-level tracking the function returns ``NOT EVALUABLE`` and the
    denominators it does know (the ambiguous-turn total); every bucket count is
    null, never zero. It does not raise and it does not substitute the
    separation gap for a distance.
    """
    radius = float(radius)
    if radius != V4_RADIUS_YD:
        raise ValueError(f"only the frozen {V4_RADIUS_YD:g}-yd radius is defined "
                         f"for this table, got {radius:g}")
    n_turns = int(len(geom))
    cols = set(geom.columns)
    complete = _complete_geometry(geom)
    if "geometry_status" in cols:
        evaluated = geom.loc[(geom["geometry_status"] == "tracked")
                             & complete].copy()
    else:
        evaluated = geom.loc[complete].copy()
    if "geometry_status" in cols:
        n_partial = int(((geom["geometry_status"] == "tracked")
                         & ~complete).sum())
    else:
        n_partial = int((~complete).sum())
    n_in = (pd.to_numeric(evaluated.get("n_in_radius_3"), errors="coerce")
            if "n_in_radius_3" in evaluated.columns
            else pd.Series(dtype="float64"))
    if len(evaluated) and "n_in_radius_3" not in evaluated.columns:
        n_in = pd.Series(np.nan, index=evaluated.index, dtype="float64")
    if not isinstance(n_in, pd.Series):
        n_in = pd.Series(np.nan, index=evaluated.index, dtype="float64")
    n_evaluated = int(n_in.notna().sum())
    per_turn_candidates = (pd.to_numeric(evaluated.get("n_candidates_tracked"),
                                         errors="coerce")
                           if "n_candidates_tracked" in evaluated.columns
                           else pd.Series(dtype="float64"))
    n_candidate_rows = int(per_turn_candidates.fillna(0).sum()) if len(evaluated) else 0

    buckets = []
    for label, lo, hi in (("zero", 0, 0), ("exactly one", 1, 1),
                          ("exactly two", 2, 2),
                          ("three or more", 3, float("inf"))):
        if n_evaluated:
            count = int(((n_in >= lo) & (n_in <= hi)).sum())
        else:
            count = None
        buckets.append({
            "bucket": label,
            "n_turns": count,
            "share_of_evaluated": (count / n_evaluated
                                   if count is not None and n_evaluated else np.nan),
            "share_of_ambiguous": (count / n_turns
                                   if count is not None and n_turns else np.nan),
        })

    return {
        "radius_yd": radius,
        "n_turns": n_turns,
        "n_evaluated": n_evaluated,
        "n_not_evaluable": n_turns - n_evaluated,
        "n_partially_tracked": n_partial,
        "complete_rule": ("all visible candidates measured at the break frame"
                          if "n_candidates" in cols
                          else "candidate counts absent; completeness assumed"),
        "n_candidate_rows_tracked": n_candidate_rows,
        "status": "tracked" if n_evaluated else GEOMETRY_NOT_EVALUABLE,
        "buckets": buckets,
        "paths_tried": (str(geom["paths_tried"].iloc[0])
                        if n_turns and "paths_tried" in geom.columns else ""),
        "attribution": ATTRIBUTION,
    }


def margin_distribution(geom: pd.DataFrame) -> dict:
    """``second-nearest − nearest`` at the break frame, in fixed buckets.

    The margin is the evidence for whether "nearest" is a discrimination or a
    coin flip. Buckets are left-closed: ``[0, 0.25)``, ``[0.25, 0.5)``,
    ``[0.5, 1.0]``, ``(1.0, inf)``. A turn with fewer than two tracked
    candidates has no margin and is counted separately, not dropped silently.
    Every count carries its denominator; no bucket is a gate.
    """
    n_turns = int(len(geom))
    complete = _complete_geometry(geom)
    if "geometry_status" in geom.columns:
        complete = complete & (geom["geometry_status"] == "tracked")
    margins = (pd.to_numeric(geom.get("nearest_gap_yd"), errors="coerce")
               if "nearest_gap_yd" in geom.columns
               else pd.Series(np.nan, index=geom.index, dtype="float64"))
    margins = margins.where(complete)
    finite = margins.dropna()
    n_evaluated = int(len(finite))
    buckets = []
    for label, lo, hi in MARGIN_BUCKETS:
        if n_evaluated:
            if np.isinf(hi):
                mask = finite > lo
            else:
                mask = (finite >= lo) & (finite <= hi if hi == 1.0 else finite < hi)
            count = int(mask.sum())
        else:
            count = None
        buckets.append({
            "bucket": label,
            "n_turns": count,
            "share_of_evaluated": (count / n_evaluated
                                   if count is not None and n_evaluated else np.nan),
        })
    return {
        "n_turns": n_turns,
        "n_evaluated": n_evaluated,
        "n_without_two_candidates": n_turns - n_evaluated,
        "n_partially_tracked": int((~complete).sum()),
        "median_yd": float(finite.median()) if n_evaluated else np.nan,
        "mean_yd": float(finite.mean()) if n_evaluated else np.nan,
        "status": "tracked" if n_evaluated else GEOMETRY_NOT_EVALUABLE,
        "buckets": buckets,
        "attribution": ATTRIBUTION,
    }


def nearest_variant(frame: pd.DataFrame, geom: pd.DataFrame,
                    *, spec: str = "primary",
                    route_turns: pd.DataFrame | None = None) -> dict:
    """V4: on turns with two or more candidates inside 3 yd, pair to the nearest.

    This is a **tie-break**, so it is a variant and never the frozen rule's
    result. Its population is the frozen single turns plus the ambiguous turns
    the geometry can resolve by nearest-man; its ``n`` is printed with every
    coefficient.

    Precedence when composed with the headline ``N``: the additive chain is
    frozen → geometry 5 → geometry 8 → Route A, and V4 is **not** one of its
    slots. It is evaluated beside that chain, and its turns are checked against
    Route A's so an overlap is named rather than quietly added. If V4 took
    precedence it would sit immediately after the frozen rule (a 3-yd geometric
    tie-break is stronger evidence than a description-only route); the returned
    ``overlap_route_a`` and ``n_new_if_precedence`` are the cost of that
    decision, and the headline ``N`` stays 2,295 because V4 does not enter it.
    """
    frame = _check_frame(frame)
    key = list(ot.TURN_KEY)
    status = ("tracked" if "geometry_status" in geom.columns
              and (geom["geometry_status"] == "tracked").any() else GEOMETRY_NOT_EVALUABLE)
    n_ambiguous = int(len(geom))
    if "n_in_radius_3" in geom.columns:
        in3 = pd.to_numeric(geom["n_in_radius_3"], errors="coerce")
    else:
        in3 = pd.Series(np.nan, index=geom.index, dtype="float64")
    complete = _complete_geometry(geom)
    if "geometry_status" in geom.columns:
        complete = complete & (geom["geometry_status"] == "tracked")
    eligible = geom.loc[(in3 >= 2) & complete
                        & geom.get("nearest_nfl_id", pd.Series(index=geom.index,
                                                             dtype="float64")).notna()].copy()
    n_eligible = int(len(eligible))

    base_pairs: dict[tuple, float] = {}
    single = _rows_of(frame, "single")
    for k, grp in single.groupby(key, sort=True):
        ys = pd.to_numeric(grp["separation_yd"], errors="coerce").dropna()
        if len(ys):
            base_pairs[tuple(k)] = float(ys.iloc[0])
    n_frozen_turns = int(len(base_pairs))

    v4_keys: set[tuple] = set()
    skipped_no_separation = 0
    if n_eligible:
        multi = _rows_of(frame, "multiple")
        cand = (multi[key + ["receiver_nfl_id", "separation_yd"]]
                .drop_duplicates(subset=key + ["receiver_nfl_id"]))
        want = eligible[key + ["nearest_nfl_id"]].rename(
            columns={"nearest_nfl_id": "receiver_nfl_id"})
        want = want.drop_duplicates(subset=key)
        chosen = want.merge(cand, on=key + ["receiver_nfl_id"], how="left")
        for _, row in chosen.iterrows():
            sep = pd.to_numeric(pd.Series([row["separation_yd"]]),
                                errors="coerce").iloc[0]
            if pd.isna(sep):
                skipped_no_separation += 1
                continue
            k = tuple(row[c] for c in key)
            v4_keys.add(k)
            base_pairs[k] = float(sep)

    pairs = dict(base_pairs)
    fitted = _variant_from_pairs(frame, spec, "nearest_3yd", pairs)
    fitted["attribution"] = V4_ATTRIBUTION
    fitted["population"] = (f"single turns + the {len(v4_keys):,} ambiguous turns "
                            "with >= 2 candidates inside 3 yd at the break frame, "
                            "paired to the nearest (a TIE-BREAK)")
    fitted["tie_break"] = True
    fitted["status"] = status
    fitted["n_ambiguous_turns"] = n_ambiguous
    fitted["n_nearest_turns"] = int(len(v4_keys))
    fitted["n_frozen_turns"] = n_frozen_turns
    fitted["n_turns"] = n_frozen_turns + int(len(v4_keys))
    fitted["n_skipped_no_separation"] = int(skipped_no_separation)

    overlap = 0
    if route_turns is not None and len(route_turns) and v4_keys:
        route_keys = set(map(tuple, route_turns[key].drop_duplicates()
                             .to_numpy().tolist()))
        overlap = len(v4_keys & route_keys)
    fitted["overlap_route_a"] = int(overlap)
    fitted["n_new_if_precedence"] = int(len(v4_keys) - overlap)

    if status == GEOMETRY_NOT_EVALUABLE:
        fitted.update({"beta": np.nan, "se": np.nan, "lo": np.nan, "hi": np.nan,
                       "p": np.nan, "n_clusters": 0,
                       "label": "NOT EVALUABLE — no frame-level tracking",
                       "note": ("no candidate distance is measurable on this "
                                "machine; the frozen base is carried for its n "
                                "only and no V4 coefficient is claimed")})
    elif n_eligible and not v4_keys:
        fitted["label"] = "NO TURNS RESOLVED"
    elif fitted.get("label") == "":
        fitted["label"] = "FIT"
    return fitted


def agreement(route_a_turns: pd.DataFrame, geometry_rows: pd.DataFrame) -> pd.DataFrame:
    """§4's cross-check: on turns where both routes fired, do they name the same man?

    Reports ``agree / (agree + disagree)`` with both counts, overall and by
    Route A sub-rule. A high disagreement rate is the error rate to quote for
    the no-tracking turns, and it ships whether or not it flatters Route A.
    """
    key = list(ot.TURN_KEY)
    if not len(route_a_turns) or not len(geometry_rows):
        return pd.DataFrame(columns=["scope", "agree", "disagree", "n_both", "rate"])
    geo = geometry_rows.loc[geometry_rows["slot"].astype(str) != "",
                            key + ["attributed_nfl_id"]]
    geo = geo.rename(columns={"attributed_nfl_id": "geom_nfl_id"})
    both = route_a_turns[key + ["route_a_rule", "attributed_nfl_id"]].merge(
        geo, on=key, how="inner")
    if not len(both):
        return pd.DataFrame(columns=["scope", "agree", "disagree", "n_both", "rate"])
    both["agree"] = (both["attributed_nfl_id"].astype("Int64")
                     == both["geom_nfl_id"].astype("Int64"))
    rows = []
    scopes = [("overall", both)] + [
        (f"route_a:{rule}", both[both["route_a_rule"] == rule])
        for rule in ROUTE_A_RULES]
    for scope, sub in scopes:
        a, d = int(sub["agree"].sum()), int((~sub["agree"]).sum())
        rows.append({"scope": scope, "agree": a, "disagree": d,
                     "n_both": a + d, "rate": (a / (a + d)) if (a + d) else np.nan})
    return pd.DataFrame(rows)


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


def write_artefacts(out_dir: str | Path, tables: dict[str, pd.DataFrame],
                    *, encoding: str = "utf-8") -> list[Path]:
    """Write every one of :data:`ARTEFACT_TABLES`; return the paths in order.

    A missing table is a bug in the run, not a data condition, so it is named
    and raised here rather than silently omitted — a table that exists only in
    memory is the defect this function exists to stop.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    missing = [name for name in ARTEFACT_TABLES if name not in tables]
    if missing:
        raise KeyError(f"the run did not build {len(missing)} of its "
                       f"{len(ARTEFACT_TABLES)} tables: {', '.join(missing)}")
    for name in ARTEFACT_TABLES:
        path = out / name
        tables[name].to_csv(path, index=False, encoding=encoding)
        written.append(path)
    return written


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

    asm = summary.get("assembly")
    if asm:
        lines.append("")
        lines.append(
            f"N = {asm['N']:,} of {asm['band_turns']:,} band turns "
            f"({_pct(asm['N'], asm['band_turns'])}): "
            + " + ".join(f"{c['slot']} {c['n']:,}" for c in asm["composition"]))
        for c in asm["composition"]:
            note = f" — {c['status']}" if c["status"] else ""
            lines.append(f"  {c['label']:<28s} {c['n']:>6,} of N "
                         f"({_pct(c['n'], asm['N'])}){note}")
        lines.append(
            f"  RULE share of N {_pct(asm['slots']['n_frozen'], asm['N'])} · "
            f"INFERENCE share {_pct(asm['N'] - asm['slots']['n_frozen'], asm['N'])}")
        if asm.get("route_a_by_rule"):
            lines.append("  Route A by rule: " + " · ".join(
                f"{k} {v:,}" for k, v in asm["route_a_by_rule"].items()))

    chk = summary.get("frozen_3yd_check")
    if chk:
        lines.append("")
        lines.append(
            f"Frozen 3-yd check on the {chk['n_turns']:,} single-candidate turns: "
            f"{chk['n_within_radius']:,} of {chk['n_tracked']:,} tracked turns are "
            f"within {chk['radius_yd']:g} yd ({chk['status']}); D204 reported "
            f"{chk['d204_reported']:,} on the full file. This is a check on "
            "N_frozen — it adds nothing to N.")

    w3 = summary.get("within_3yd")
    if w3:
        lines.append("")
        lines.append(
            f"Within {w3['radius_yd']:g} yd at the break frame, on the "
            f"{w3['n_turns']:,} ambiguous turns ({w3['status']}; "
            f"{w3['n_evaluated']:,} evaluable):")
        for b in w3["buckets"]:
            n = "n/a" if b["n_turns"] is None else f"{b['n_turns']:,}"
            lines.append(f"  {b['bucket']:<14s} {n} of {w3['n_evaluated']:,} "
                         f"evaluated ({_pct(b['n_turns'], w3['n_evaluated'])})")
    mg = summary.get("margin")
    if mg:
        lines.append(
            f"Margin second-nearest − nearest: {mg['n_evaluated']:,} of "
            f"{mg['n_turns']:,} evaluable"
            + "".join(f" · {b['bucket']} "
                      + ("n/a" if b["n_turns"] is None else f"{b['n_turns']:,}")
                      for b in mg["buckets"])
            + "; no bucket is a gate.")
    v4 = summary.get("v4_nearest")
    if v4:
        lines.append(
            f"{v4['variant']}: beta {_num(v4['beta'])}, n={v4['n']:,} "
            f"({v4['n_frozen_turns']:,} frozen + {v4['n_nearest_turns']:,} "
            f"nearest-resolved) — {v4['label']}. A TIE-BREAK the frozen rule "
            "refuses; it is not a slot of N and N is unchanged.")

    agree = summary.get("agreement") or []
    overall = next((r for r in agree if r.get("scope") == "overall"), None)
    if overall and overall.get("n_both"):
        lines.append(
            f"Cross-check where both routes fired: {overall['agree']:,} agree / "
            f"{overall['disagree']:,} disagree ({_pct(overall['agree'], overall['n_both'])}).")
    else:
        lines.append(
            "Cross-check where both routes fired: NOT EVALUABLE — no tracking on "
            "this machine, so no turn was paired by both routes.")

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
