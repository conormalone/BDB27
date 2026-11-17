"""Tests for the multi-candidate attribution diagnostic (notes/multi-candidate-spec.md).

Four things are pinned here, each against something already on the record rather
than against a number re-typed from the run:

* the label rule — ``BOUND CONTAINED IN CI`` / ``SIGN IDENTIFIED`` /
  ``BOUND — NOT IDENTIFIED`` / ``NOT FITTED`` — applied in the written order of
  the reading-rule table (spec 8726120 §4, kept unchanged by the amendment);
* the funnel — recomputed from the raw files and reconciled row by row against
  the committed feasibility artefact (AC2, AC10);
* the frame — the frozen base reproduces ``outputs/outcome_test/attributed_turns.csv``
  on its shared columns and all 1,731 rows (AC1), and 1,731 + 870 = 2,601 (AC2);
* the invariance — 20 fixed-seed random pairings (seed 20261117) all land inside
  the identified set and reproduce bit-for-bit on a second run (AC3, AC4).

Whatever a test asserts, it asserts about the code as it stands: no assertion
here writes a new threshold, and none of them is a gate on the run itself.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import ambiguity_attribution as aa  # noqa: E402
from bdb27 import outcome_test as ot  # noqa: E402

TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"
PLAYER_PLAY = ROOT / "data/raw/player_play.csv"
FEASIBILITY = ROOT / "outputs/outcome_link/feasibility_summary.json"
ATTRIBUTED = ROOT / "outputs/outcome_test/attributed_turns.csv"

KEY = list(ot.TURN_KEY)


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def tables():
    """Funnel and both frames, built once from the raw files."""
    con = aa.connect()
    try:
        funnel = aa.funnel(TURNS, PLAYER_PLAY, con=con)
        single = aa.build_frame(TURNS, PLAYER_PLAY, status="single", con=con)
        multiple = aa.build_frame(TURNS, PLAYER_PLAY, status="multiple", con=con)
    finally:
        con.close()
    return {"funnel": funnel, "single": single, "multiple": multiple}


@pytest.fixture(scope="module")
def single(tables):
    return tables["single"]


@pytest.fixture(scope="module")
def multiple(tables):
    return tables["multiple"]


@pytest.fixture(scope="module")
def analysis(single, multiple):
    return aa.analysis_frame(single, multiple)


# ------------------------------------------------------------------ label rule
@pytest.mark.parametrize("beta_min,beta_max,ci_lo,ci_hi,expected", [
    # A: the identified set sits inside the frozen-only CI.
    (-0.20, 0.20, -0.36, 0.46, aa.LABEL_CONTAINED),
    # C: the set excludes zero (and is not contained in the CI).
    (0.50, 1.20, -0.36, 0.46, aa.LABEL_SIGN),
    (-1.40, -0.90, -0.36, 0.46, aa.LABEL_SIGN),
    # B: it straddles zero and is wider than the CI — the primary's case.
    (-1.308, 1.361, -0.358, 0.460, aa.LABEL_NOT_IDENTIFIED),
])
def test_label_rule_applies_the_table_in_order(beta_min, beta_max, ci_lo, ci_hi,
                                               expected):
    got = aa._branch_label(beta_min, beta_max, ci_lo, ci_hi, False,
                           width=beta_max - beta_min, ci_width=ci_hi - ci_lo)
    assert got["branch"] == expected
    assert got["label"] == expected
    assert got["contains_zero"] is (beta_min <= 0.0 <= beta_max)
    assert got["inside_ci"] is (beta_min >= ci_lo and beta_max <= ci_hi)


def test_label_rule_flags_a_straddling_set_that_is_not_wider_than_the_ci():
    """man_only's shape: branch B by the table's order, with the fact stated.

    The table has three rows and this case fits only B's "straddles zero" half;
    the code assigns B (there is no fourth row) and records the two widths so
    the row is not read as the wider-than-CI case it is not.
    """
    got = aa._branch_label(-0.50, 0.50, -1.339, 0.414, False,
                           width=1.000, ci_width=1.753)
    assert got["branch"] == aa.LABEL_NOT_IDENTIFIED
    assert got["contains_zero"] is True
    assert got["inside_ci"] is False
    assert "NOT larger than the CI width" in got["branch_note"]


def test_label_rule_returns_not_fitted_when_there_is_nothing_to_bound():
    got = aa._branch_label(float("nan"), float("nan"), float("nan"), float("nan"),
                           False)
    assert got == {"branch": aa.LABEL_NOT_FITTED, "label": aa.LABEL_NOT_FITTED,
                   "contains_zero": None, "inside_ci": None, "branch_note": ""}


def test_under_determined_design_is_labelled_descriptive_not_promoted():
    got = aa._branch_label(-1.0, 1.0, -0.36, 0.46, True, width=2.0, ci_width=0.82)
    assert got["label"].startswith(aa.LABEL_DESCRIPTIVE)
    assert got["label"].endswith(got["branch"])


def test_the_primary_label_is_set_by_the_bound_and_nothing_else(analysis):
    b = aa.identified_set(analysis, "primary")
    assert b["branch"] == aa.LABEL_NOT_IDENTIFIED
    assert b["label"] == aa.LABEL_NOT_IDENTIFIED          # no DESCRIPTIVE prefix
    assert b["contains_zero"] is True
    assert b["inside_ci"] is False
    # AC1's estimator, through this module's own path.
    assert round(b["beta_frozen"], 3) == 0.051
    assert [round(v, 3) for v in (b["ci_lo"], b["ci_hi"])] == [-0.358, 0.460]
    assert (b["n_frozen"], b["frozen_clusters"]) == (1731, 102)


# ------------------------------------------------------------------ attribution labels
def test_only_the_frozen_frame_carries_rule(single, multiple):
    assert set(single["attribution"]) == {aa.LABEL_RULE}
    assert set(multiple["attribution"]) == {aa.ATTRIBUTION}
    assert aa.ATTRIBUTION.startswith("INFERENCE")
    assert "no opponent identifier" in aa.ATTRIBUTION


def test_every_inferred_row_says_inference(analysis):
    b = aa.identified_set(analysis, "primary")
    assert b["attribution"] == aa.ATTRIBUTION and b["label"] != aa.LABEL_RULE
    for rule in aa.PAIRING_RULES:
        v = aa.variant_fit(analysis, rule, "primary")
        assert v["attribution"] == aa.ATTRIBUTION
        assert v["variant"].startswith("V") and "INFERENCE" in v["variant"]


def test_the_multi_candidate_sql_is_derived_from_the_one_sample_text(monkeypatch):
    """AC1's "comparison, not a coincidence": the sample SQL has one home.

    ``outcome_test.SINGLE_CANDIDATE_SQL`` is imported, not copied, and the
    derivation raises if the text it edits is no longer there — so a change to
    the candidate rule cannot quietly leave ``build_frame`` behind.
    """
    assert aa._SINGLE_SQL == ot.SINGLE_CANDIDATE_SQL
    assert "n_opp_route_sep >= 2" in aa._MULTIPLE_SQL
    assert "n_opp_route >= 1" in aa._ALL_SQL
    monkeypatch.setattr(ot, "SINGLE_CANDIDATE_SQL", "SELECT 1")
    with pytest.raises(RuntimeError, match="cannot derive the multi-candidate SQL"):
        aa._derive_multiple_sql()


# ------------------------------------------------------------------ funnel (O1 / AC2 / AC10)
def _n(table: pd.DataFrame, step: str, grouping: str) -> int:
    hit = table[(table["step"] == step) & (table["grouping"] == grouping)]
    assert len(hit) == 1, f"{step!r} appears {len(hit)} times for {grouping!r}"
    return int(hit["n"].iloc[0])


def test_funnel_matches_the_numbers_held_in_section_1(tables):
    f = tables["funnel"]
    assert f["matches"].all(), f.loc[~f["matches"], ["step", "n", "expected_2026_11_14"]]
    assert _n(f, "band_turns", "band_row") == 7777
    assert (_n(f, "turns_with_exactly_one_candidate_separation", "band_row")
            + _n(f, "turns_with_two_or_more_candidate_separations", "band_row")
            == 2601)


def _visible_candidate_counts(table: pd.DataFrame, grouping: str) -> dict[int, int]:
    """``{n_visible_candidates: turns}`` for one grouping of the funnel table."""
    sel = table[(table["grouping"] == grouping)
                & table["step"].str.contains("visible_candidate")]
    return {int(re.search(r"turns_with_(\d+)_", step).group(1)): int(n)
            for step, n in zip(sel["step"], sel["n"])}


def test_funnel_reconciles_with_the_committed_feasibility_artefact(tables):
    f = tables["funnel"]
    committed = json.loads(FEASIBILITY.read_text())
    status = {r["pp_candidate_status"]: int(r["n_turns"])
              for r in committed["candidate_status"]["FULL"]}
    amb = committed["ambiguity_candidates"]["FULL"]

    # Band size: recomputed here, printed in the artefact.
    assert _n(f, "band_turns", "band_row") == int(committed["full"]["full_band_turns"])

    # The committed artefact's three candidate-status buckets, recomputed.
    band_row = _visible_candidate_counts(f, "band_row")
    assert band_row[1] == status["single"] == 2002
    assert sum(v for k, v in band_row.items() if k >= 2) == status["multiple"] == 1007
    assert sum(band_row.values()) == int(
        f.loc[(f["grouping"] == "band_row")
              & f["step"].str.contains("visible_candidate"), "denominator"].iloc[0])

    # Candidate-level ambiguity, on the grouping the artefact uses (the
    # four-column turn key): 815 two-candidate turns, 191 at three or more.
    turn_key = _visible_candidate_counts(f, "turn_key")
    assert turn_key[2] == int(amb["turns_with_two_candidates"]) == 815
    assert sum(v for k, v in turn_key.items() if k >= 3) == int(
        amb["turns_with_three_or_more_candidates"]) == 191

    # The three outcome buckets partition the band.
    assert (_n(f, "no_opposing_player_play_row", "band_row")
            + _n(f, "opposing_rows_no_route_runner", "band_row")
            + _n(f, "turns_with_opposing_route_runner", "band_row")
            == _n(f, "band_turns", "band_row"))

    # The per-turn block runs on the band rows whose DB has an own row, which
    # is why its denominator is smaller than the band: the shortfall is inside
    # ``no_opposing_player_play_row``, not outside the funnel.
    per_turn_total = sum(band_row.values())
    assert per_turn_total < _n(f, "band_turns", "band_row")
    assert (_n(f, "band_turns", "band_row") - per_turn_total
            <= _n(f, "no_opposing_player_play_row", "band_row"))


def test_every_funnel_row_carries_its_denominator(tables):
    f = tables["funnel"]
    assert (f["denominator"] > 0).all()
    assert f["denominator_name"].notna().all()
    assert (f["n"] <= f["denominator"]).all()


# ------------------------------------------------------------------ frame (AC1 / AC2)
def test_single_frame_reproduces_the_committed_attributed_turns(single):
    committed = pd.read_csv(ATTRIBUTED)
    shared = [c for c in committed.columns if c in single.columns]
    assert len(shared) >= 20, f"only {len(shared)} shared columns: {shared}"
    left = committed[shared].sort_values(KEY, kind="mergesort").reset_index(drop=True)
    right = single[shared].sort_values(KEY, kind="mergesort").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)


def test_analysis_population_is_1731_plus_870(single, multiple, analysis):
    assert len(single) == 1731
    assert multiple[KEY].drop_duplicates().shape[0] == 870
    assert len(multiple) == 1911
    assert len(analysis) == 1731 + 1911

    # The frame carries only candidates with a recorded separation, so its
    # per-turn counts are 704 / 161 / 5 — the artefact's own "704 of the 815
    # carry a separation for both", not the 815 visible-candidate turns.
    assert multiple["separation_yd"].notna().all()
    per_turn = multiple.groupby(KEY, sort=True).size().value_counts().to_dict()
    assert per_turn == {2: 704, 3: 161, 4: 5}
    assert sum(k * v for k, v in per_turn.items()) == len(multiple)

    committed = json.loads(FEASIBILITY.read_text())["ambiguity_candidates"]["FULL"]
    assert per_turn[2] == int(committed["two_candidate_turns_with_both_separations"])

    # One design row per band row: the ambiguous turns do not inflate n.
    assert aa._design_rows(analysis).shape[0] == analysis[
        KEY + ["abs_turn_angle"]].drop_duplicates().shape[0]


# ------------------------------------------------------------------ invariance (AC3 / AC4)
def test_twenty_fixed_seed_pairings_all_land_inside_the_bound(analysis):
    first = aa.random_assignments(analysis, "primary", reps=20, seed=20261117)
    assert len(first) == 20
    assert set(first["seed"]) == {20261117}
    assert first["inside_bound"].all(), first.loc[~first["inside_bound"]]
    # c . y is the estimator, not an approximation of it.
    assert first["abs_difference"].max() < 1e-9

    second = aa.random_assignments(analysis, "primary", reps=20, seed=20261117)
    pd.testing.assert_frame_equal(first, second)


def test_random_pairings_are_a_different_pairing_each_rep(analysis):
    """Determinism must not come from drawing the same pairing 20 times."""
    pairs = aa._pairing(analysis, "random",
                        rng=np.random.default_rng(20261117))
    assert 0 < len(pairs) <= 2601
    b = aa.identified_set(analysis, "primary")
    assert b["beta_min"] <= b["beta_max"]
    assert b["width"] == pytest.approx(b["beta_max"] - b["beta_min"], abs=1e-12)


# ------------------------------------------------------------------ no gates (AC7)
def test_empty_frame_returns_not_fitted_and_does_not_raise(analysis):
    out = aa.identified_set(analysis.iloc[0:0], "primary")
    assert out["label"] == aa.LABEL_NOT_FITTED
    assert out["n_population"] == 0 and out["n_frozen"] == 0


def test_geometry_is_not_evaluable_without_frames(multiple):
    geom = aa.candidate_distance_table(multiple, TURNS, PLAYER_PLAY)
    assert len(geom) == 870
    assert set(geom["geometry_status"]) == {aa.GEOMETRY_NOT_EVALUABLE}
    for col in ("dist_nearest_yd", "dist_second_yd", "nearest_gap_yd", "cand_gap_yd"):
        assert geom[col].isna().all(), col
    assert "not a distance between two players" in aa.GEOMETRY_NOT_EVALUABLE
    assert geom["paths_tried"].iloc[0]                    # names what it looked for


def test_sample_tracking_file_is_never_picked_up_as_full_tracking():
    """The truncation on this machine must not stand in for the full file."""
    assert all("sample" not in name for name in aa.DEFAULT_TRACKING_NAMES)
    found = [p.name for p in aa._tracking_candidates(None)]
    assert all("sample" not in name for name in found)


# ------------------------------------------------------------------ separation gap
def test_separation_gap_is_max_minus_min_and_says_so(single, multiple):
    comp = aa.composition(single, multiple)
    gap = comp[(comp["variable"] == "separation_gap_yd")
               & (comp["metric"] == "median")]
    assert len(gap) == 1
    note = str(gap["note"].iloc[0])
    assert "not a distance between two players" in note
    assert "MAX(sep) - MIN(sep)" in str(gap["level"].iloc[0])
