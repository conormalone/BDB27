"""Gate-free pins: the run reaches a number on whatever data it is given.

Conor, 2026-11-15: "there was no need to have it fail on arbitrary limits, never
have a notebook fail arbitrarily again". These tests hold that in place:

* a cohort-only file is described, not judged;
* the 1,048,575-row truncation is a printed finding, not a stop;
* a four-turn subset runs to a coefficient with its interval and a
  descriptive-only label;
* an empty subset runs to zeros and a "not fitted" line;
* nothing in either path raises.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import geometric_match as gm
from bdb27 import outcome_test

REPO = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO / "notebooks" / "bdb27_geometric_attribution.ipynb"

#: What the real player_play.csv looks like: the 497-prospect cohort, two rows
#: per play. This is the data's design (D198), not a defect.
COHORT_COUNTS = {
    "path": "player_play.csv", "rows": 9_000_000, "games": 272,
    "plays": 114_954, "players": 497, "median_players_on_play": 2.0,
    "max_players_on_play": 11,
}


def notebook_cells() -> list[dict]:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def cell_source(predicate) -> str:
    for cell in notebook_cells():
        src = "".join(cell["source"])
        if predicate(src):
            return src
    raise AssertionError("no cell matched")


def thin_frame(n: int) -> pd.DataFrame:
    """An analysis-ready frame of ``n`` turns, deterministic, with spread."""
    rng = np.random.default_rng(3)
    pos = ["CB", "SS", "FS"]
    cov = ["MAN", "ZONE"]
    route = ["OUT", "IN", "OTHER"]
    rows = []
    for i in range(n):
        rows.append(dict(
            game_id=i // 3 + 1, play_id=i, nfl_id=[101, 102, 103, 104][i % 4],
            frame_start=i, position=pos[int(rng.integers(0, 3))],
            season=int(rng.choice([2022, 2023])),
            separation_yd=float(rng.uniform(0.5, 6.0)),
            apex_retention=float(rng.uniform(0.4, 1.0)),
            speed_retention=float(rng.uniform(0.4, 1.0)),
            entry_speed=float(rng.uniform(5.0, 9.0)),
            coverage_assignment=cov[int(rng.integers(0, 2))],
            route_fe=route[int(rng.integers(0, 3))], one_turn_per_play=True,
            _play=str(i), pass_result=["C", "I"][i % 2]))
    df = pd.DataFrame(rows)
    df["coverage_family"] = df["coverage_assignment"]
    df["position_fe"] = df["position"]
    df["season_fe"] = df["season"].astype(str)
    return df


def four_turn_frame() -> pd.DataFrame:
    return thin_frame(4)


# ------------------------------------------------------------- pre-flight text

def test_a_cohort_only_file_is_described_and_never_stopped():
    report = gm.preflight_verdict(COHORT_COUNTS, band_plays=7198)
    text = " ".join(report["findings"])

    assert report["ok"] is True
    assert report["stops"] == []
    assert report["truncated"] is False
    assert "497" in text and "2.0" in text
    assert "players" in text and "plays" in text
    for word in ("anomal", "STOP", "defect", "too few"):
        assert word not in text, word


def test_the_truncation_is_reported_as_a_finding_and_the_run_continues():
    counts = dict(COHORT_COUNTS, path="game_tracking_sample.csv",
                  rows=gm.TRUNCATION_ROWS, players=154,
                  median_players_on_play=1.0, max_players_on_play=4)
    report = gm.preflight_verdict(counts, band_plays=7198)
    text = " ".join(report["findings"])

    assert report["truncated"] is True
    assert report["stops"] == [] and report["ok"] is True
    assert "1,048,575" in text
    assert "continues" in text


@pytest.mark.parametrize("counts", [
    COHORT_COUNTS,
    dict(COHORT_COUNTS, rows=gm.TRUNCATION_ROWS),
    dict(COHORT_COUNTS, players=2, median_players_on_play=1.0, plays=3),
    dict(COHORT_COUNTS, players=0, plays=0, rows=0),
])
def test_preflight_never_returns_a_stop(counts):
    report = gm.preflight_verdict(counts, band_plays=7198)
    assert report["ok"] is True and report["stops"] == []
    assert isinstance(report["findings"], list) and report["findings"]


def test_preflight_counts_reads_a_cohort_shaped_file(tmp_path):
    """One game, 300 plays, two cohort players per play, 497 distinct players."""
    rows = []
    for play in range(300):
        if play < 249:
            # Two fresh players per play, covering ids 1..497 exactly once each.
            players = [2 * play + 1, min(2 * play + 2, 497)]
        else:
            players = [1 + (play % 497), 1 + ((play * 7) % 497)]
        for player in players:
            rows.append({"game_id": 1, "play_id": play, "nfl_id": int(player),
                         "x": 0.0})
    path = tmp_path / "game_tracking_cohort.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    counts = gm.preflight_counts(path)
    report = gm.preflight_verdict(counts, band_plays=7198)

    assert counts["players"] == 497
    assert counts["median_players_on_play"] <= 2.0
    assert counts["rows"] == 600
    assert report["truncated"] is False
    assert report["stops"] == []


# ------------------------------------------------------------- thin subsets

def test_a_four_turn_subset_runs_to_an_estimate():
    df = four_turn_frame()
    fits, boot = outcome_test.run_all(df)

    primary = fits[0]
    assert primary.n == 4
    assert np.isfinite(primary.beta), "a four-turn subset must still produce a number"
    # 4 turns against 8 parameters is saturated: the interval is not identified,
    # so it must be reported as not finite rather than as a point.
    assert not np.isfinite(primary.lo) and not np.isfinite(primary.hi)
    assert "not identified" in primary.note
    verdict = outcome_test.verdict(fits, boot)
    assert isinstance(verdict["label"], str) and verdict["label"]
    assert boot["n_clusters"] == 4  # four distinct players


def test_a_thin_subset_reports_a_wide_interval():
    """Twelve turns: enough to be non-saturated, far too few to be precise."""
    fits, _ = outcome_test.run_all(thin_frame(12))
    primary = fits[0]
    width = primary.hi - primary.lo
    assert np.isfinite(width) and width > 1.0, "a thin subset must show a wide interval"
    assert primary.n == 12


def test_four_turns_flow_to_a_descriptive_only_verdict():
    band_turns = 7777
    match = pd.DataFrame({
        "game_id": [1, 1, 2, 2], "play_id": [10, 11, 20, 21],
        "nfl_id": [101, 101, 102, 103], "frame_start": [5, 7, 9, 11],
        "match_status": ["unique"] * 4,
    })
    match = gm.empty_match(match).assign(
        match_status="unique", n_in_radius_3=1)
    coverage = gm.coverage_table(match, band_turns)
    assert coverage.loc[coverage["primary"], "unique"].item() == len(match)

    summary = {
        "misattribution": {"agree": 3, "disagree": 1, "judgeable": 4,
                           "elimination_turns": 4, "misattribution_rate": 0.25},
        "gate": {"attributed_turns": 4, "attributed_fraction": 0.005,
                 "min_turns": 500, "min_share": 0.30, "gate_met": False},
        "estimate": None,
        "estimate_status": "0 of 4 attributed turns carry separation",
    }
    lines = gm.plain_english_verdict(summary)
    text = " ".join(lines)
    assert "4 judgeable" in text or "of 4" in text
    assert "0.25" in text or "25.0%" in text
    assert "descriptive only" in text.lower()
    assert "not fitted" in text.lower()


def test_an_empty_subset_prints_zeros_and_does_not_raise():
    turns = pd.DataFrame({"game_id": [1, 2], "play_id": [10, 20],
                          "nfl_id": [101, 102], "frame_start": [5, 7]})
    match = gm.empty_match(turns)
    coverage = gm.coverage_table(match, len(turns))
    assert (coverage[["unique", "multiple", "none",
                      "unobservable"]].to_numpy() == 0).all()
    assert coverage["untracked"].eq(len(turns)).all()
    assert coverage["attributed_fraction"].eq(0).all()

    elim = pd.DataFrame({"game_id": [1, 2], "play_id": [10, 20],
                         "nfl_id": [101, 102], "frame_start": [5, 7],
                         "receiver_nfl_id": [201, 202]})
    miss = gm.compare_elimination(match, elim)
    assert miss["judgeable"] == 0
    assert miss["misattribution_rate"] is None
    assert miss["label"] == "NO JUDGEABLE TURNS"

    assert gm.geometric_sample(match, pd.DataFrame(), turns).empty

    summary = {
        "misattribution": {k: v for k, v in miss.items() if k != "rows"},
        "gate": {"attributed_turns": 0, "attributed_fraction": 0.0,
                 "min_turns": 500, "min_share": 0.30, "gate_met": False},
        "estimate": None,
        "estimate_status": "0 of 0 attributed turns carry separation",
    }
    text = " ".join(gm.plain_english_verdict(summary))
    assert "0 of 0" in text or "0 of" in text
    assert "not fitted" in text.lower()


def test_a_saturated_subset_says_the_interval_is_not_finite():
    summary = {
        "misattribution": {"agree": 3, "disagree": 0, "judgeable": 3,
                           "elimination_turns": 3, "misattribution_rate": 0.0},
        "gate": {"attributed_turns": 4, "attributed_fraction": 0.001,
                 "min_turns": 500, "min_share": 0.30, "gate_met": False},
        "estimate": {"beta": -3.02, "lo": float("nan"), "hi": float("nan"),
                     "n": 4, "clusters": 4, "elimination_beta": None,
                     "label": "DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE"},
    }
    text = " ".join(gm.plain_english_verdict(summary))
    assert "not finite" in text
    assert "too thin" in text


def test_compare_with_d199_does_not_raise_on_a_shape_mismatch(tmp_path):
    csv = tmp_path / "estimates.csv"
    pd.DataFrame({
        "spec": ["primary: OLS + controls, SE clustered by player"],
        "beta_yd_per_unit_retention": [-1.2], "ci95_lo": [-2.0],
        "ci95_hi": [-0.4], "n_turns": [400],
    }).to_csv(csv, index=False)

    fits, _ = outcome_test.run_all(four_turn_frame())
    out = gm.compare_with_d199(fits, csv)
    assert len(out) == 1 and out.iloc[0]["spec"] == fits[0].name


# ------------------------------------------------------------- notebook shape

def test_the_stamp_is_computable_from_the_config_cell_alone():
    """No other cell may have run — a bare exec must still define `stamp`."""
    config = cell_source(lambda s: "CONFIG — the only cell to edit" in s)
    namespace: dict = {}
    exec(compile(config, "config", "exec"), namespace)
    assert namespace["stamp"] == "FULL"
    assert namespace["ALLOW_SAMPLE_TRACKING"] is False


def test_a_downstream_cell_run_alone_names_what_is_missing():
    """Config defines `stamp` and `require`; a lone summary cell says run above."""
    config = cell_source(lambda s: "CONFIG — the only cell to edit" in s)
    summary_cell = cell_source(lambda s: s.lstrip().startswith('require("summary")'))
    namespace: dict = {}
    exec(compile(config, "config", "exec"), namespace)  # only the config cell ran
    with pytest.raises(SystemExit):
        exec(compile(summary_cell, "summary", "exec"), namespace)


def test_no_cell_stops_on_a_data_condition():
    forbidden = ("MIN_JUDGEABLE", "MIN_SEPARATION_TURNS", "STOP_UNOBSERVED_SHARE",
                 "pre-flight stop", "empty extraction",
                 "TRACKING DOES NOT OBSERVE OPPONENTS")
    for cell in notebook_cells():
        if cell["cell_type"] != "code":
            continue
        src = "".join(cell["source"])
        for word in forbidden:
            assert word not in src, f"gate still present: {word}"


def test_every_code_cell_still_compiles():
    for cell in notebook_cells():
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), "cell", "exec")
