"""Tests for geometric attribution — synthetic, no full tracking needed.

The frozen rule, the pre-flight stop, the misattribution rate and the estimator
wiring are each pinned here. The estimator itself is ``outcome_test``'s and is
not re-implemented, so these tests assert the wiring and the identity of the
functions, not a second set of coefficients.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import geometric_match as gm
from bdb27 import outcome_test

TURN_COLS = list(gm.TURN_KEY) + ["x_centre", "y_centre"]
BASE = pd.Timestamp("2023-09-01T00:00:00")


def _ts(seconds: float) -> str:
    return (BASE + pd.Timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def write_tracking(path: Path, rows: list[tuple]) -> Path:
    df = pd.DataFrame(rows, columns=["game_id", "play_id", "nfl_id", "time",
                                     "x", "y", "event"])
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def player_play_frame(spec: list[dict]) -> pd.DataFrame:
    """A player_play frame with every column the module reads."""
    defaults = dict(lined_up_position="WR", route_ran="GO", target=False,
                    separation_at_pass_forward=3.0, cushion=5.0,
                    coverage_assignment="MAN", down=1, yards_to_go=10.0,
                    team_coverage_man_zone="MAN", possession_team="OFF",
                    pass_result="C")
    out = []
    for row in spec:
        out.append({**defaults, **row})
    return pd.DataFrame(out)


def one_play(*, db=(0.0, 0.0), receivers=((2.0, 0.0),), db_team="DEF",
             recv_team="OFF", route="GO", separation=3.0, game=1, play=10):
    """Tracking rows + player_play rows for one synthetic play."""
    # The DB moves 0.06 yd between frames so the centre frame is uniquely the
    # nearest row: two identical positions would tie and pick the earlier time.
    rows = [(game, play, 900, _ts(0.0), db[0] - 0.06, db[1], "NA"),
            (game, play, 900, _ts(0.1), db[0], db[1], "NA")]
    pp = [dict(game_id=game, play_id=play, nfl_id=900, team_abbr=db_team,
               lined_up_position="CB", route_ran=None, separation_at_pass_forward=None)]
    for i, (x, y) in enumerate(receivers):
        nid = 100 + i
        rows.append((game, play, nid, _ts(0.1), x, y, "NA"))
        rows.append((game, play, nid, _ts(1.0), x, y, "pass_forward"))
        pp.append(dict(game_id=game, play_id=play, nfl_id=nid, team_abbr=recv_team,
                       route_ran=route, separation_at_pass_forward=separation))
    return rows, pp


def run_case(tmp_path: Path, tracking_rows, pp_rows, turn):
    tk = write_tracking(tmp_path / "tk.csv", tracking_rows)
    pq = tmp_path / "tk.parquet"
    pd.read_csv(tk, dtype={"game_id": "int64", "play_id": "int64", "nfl_id": "int64"}
                ).to_parquet(pq, index=False)
    turns = pd.DataFrame([turn], columns=TURN_COLS)
    con = gm.connect()
    shards = [{"shard": str(pq)}]
    bps = gm.recover_break_frames(con, turns, shards)
    frames = gm.candidate_frame(con, bps, shards)
    match = gm.match_turns(turns, frames, bps, player_play_frame(pp_rows))
    con.close()
    return match


def turn_row(game=1, play=10, nfl=900, frame_start=10, x=0.0, y=0.0):
    return dict(game_id=game, play_id=play, nfl_id=nfl, frame_start=frame_start,
                x_centre=x, y_centre=y)


# ------------------------------------------------------------------ break frame
def test_break_frame_exact_alignment_is_recovered(tmp_path):
    rows, pp = one_play()
    m = run_case(tmp_path, rows, pp, turn_row())
    assert m.iloc[0]["match_status"] == "unique"
    assert m.iloc[0]["min_distance_yd"] == pytest.approx(2.0)


def test_break_frame_drift_within_gate_is_accepted(tmp_path):
    rows, pp = one_play()
    m = run_case(tmp_path, rows, pp, turn_row(x=0.1))
    assert m.iloc[0]["match_status"] == "unique"


def test_break_frame_beyond_gate_is_dropped(tmp_path):
    rows, pp = one_play()
    m = run_case(tmp_path, rows, pp, turn_row(x=0.40))
    assert m.iloc[0]["match_status"] == "break_frame_unrecovered"
    assert m.iloc[0]["align_yd"] == pytest.approx(0.40, abs=1e-6)


# ------------------------------------------------------------------ statuses
def test_unique_match_names_the_single_receiver(tmp_path):
    rows, pp = one_play(receivers=((2.0, 0.0),))
    m = run_case(tmp_path, rows, pp, turn_row())
    r = m.iloc[0]
    assert r["match_status"] == "unique"
    assert int(r["matched_receiver_nfl_id"]) == 100
    assert bool(r["is_geometric_match"]) is True


def test_two_candidates_inside_radius_are_ambiguous_and_dropped(tmp_path):
    rows, pp = one_play(receivers=((2.0, 0.0), (2.5, 0.0)))
    m = run_case(tmp_path, rows, pp, turn_row())
    r = m.iloc[0]
    assert r["match_status"] == "multiple"
    assert pd.isna(r["matched_receiver_nfl_id"]), "a tie must never be resolved"
    assert bool(r["is_geometric_match"]) is False


def test_opposing_players_present_but_none_inside_radius(tmp_path):
    rows, pp = one_play(receivers=((4.0, 0.0),))
    m = run_case(tmp_path, rows, pp, turn_row())
    r = m.iloc[0]
    assert r["match_status"] == "none"
    assert int(r["n_opposing_players"]) == 1
    assert int(r["n_in_radius_3"]) == 0


def test_no_opposing_player_tracked_is_unobservable(tmp_path):
    # an offensive receiver is absent from the frame entirely
    rows = [(1, 10, 900, _ts(0.0), -0.06, 0.0, "NA"),
            (1, 10, 900, _ts(0.1), 0.0, 0.0, "NA")]
    pp = [dict(game_id=1, play_id=10, nfl_id=900, team_abbr="DEF",
               lined_up_position="CB", route_ran=None,
               separation_at_pass_forward=None)]
    m = run_case(tmp_path, rows, pp, turn_row())
    assert m.iloc[0]["match_status"] == "unobservable"


def test_play_absent_from_tracking_is_untracked(tmp_path):
    rows, pp = one_play(game=2, play=99)
    m = run_case(tmp_path, rows, pp, turn_row())  # turn is game 1, play 10
    assert m.iloc[0]["match_status"] == "untracked"


def test_radius_counts_are_monotone(tmp_path):
    rows, pp = one_play(receivers=((1.5, 0.0), (4.0, 0.0)))
    m = run_case(tmp_path, rows, pp, turn_row())
    r = m.iloc[0]
    assert r["n_in_radius_2"] <= r["n_in_radius_3"] <= r["n_in_radius_5"]
    assert r["n_in_radius_2"] == 1 and r["n_in_radius_3"] == 1
    assert r["n_in_radius_5"] == 2, "both receivers are inside 5 yd"


# ------------------------------------------------------------------ pre-flight
def test_preflight_stops_on_the_exact_truncation_row_count():
    v = gm.preflight_verdict({"path": "x.csv", "rows": gm.TRUNCATION_ROWS,
                              "players": 154, "median_players_on_play": 1.0,
                              "plays": 12638})
    assert v["ok"] is False
    assert any("1,048,575" in s for s in v["stops"])


def test_preflight_stops_on_too_few_players():
    v = gm.preflight_verdict({"path": "x.csv", "rows": 9_000_000, "players": 154,
                              "median_players_on_play": 22.0, "plays": 30000})
    assert any("154" in s for s in v["stops"])


def test_preflight_stops_on_low_median_players_per_play():
    v = gm.preflight_verdict({"path": "x.csv", "rows": 9_000_000, "players": 2000,
                              "median_players_on_play": 4.0, "plays": 30000})
    assert any("median" in s for s in v["stops"])


def test_preflight_stops_when_the_band_is_not_covered():
    v = gm.preflight_verdict({"path": "x.csv", "rows": 9_000_000, "players": 2000,
                              "median_players_on_play": 22.0, "plays": 1000},
                             band_plays=7198)
    assert any("90%" in s for s in v["stops"])


def test_preflight_accepts_a_full_looking_file():
    v = gm.preflight_verdict({"path": "x.csv", "rows": 30_000_000, "players": 2200,
                              "median_players_on_play": 22.0, "plays": 31720},
                             band_plays=7198)
    assert v["ok"] is True and v["stops"] == []


# ------------------------------------------------------------------ ambiguity
def test_misattribution_rate_and_labels():
    match = pd.DataFrame([
        dict(game_id=1, play_id=1, nfl_id=9, frame_start=1, match_status="unique",
             matched_receiver_nfl_id=101, min_distance_yd=2.0),
        dict(game_id=1, play_id=2, nfl_id=9, frame_start=1, match_status="unique",
             matched_receiver_nfl_id=102, min_distance_yd=2.1),
        dict(game_id=1, play_id=3, nfl_id=9, frame_start=1, match_status="unique",
             matched_receiver_nfl_id=103, min_distance_yd=2.2),
        dict(game_id=1, play_id=4, nfl_id=9, frame_start=1, match_status="multiple",
             matched_receiver_nfl_id=None, min_distance_yd=1.9),
    ])
    elim = pd.DataFrame([
        dict(game_id=1, play_id=1, nfl_id=9, frame_start=1, receiver_nfl_id=101),
        dict(game_id=1, play_id=2, nfl_id=9, frame_start=1, receiver_nfl_id=102),
        dict(game_id=1, play_id=3, nfl_id=9, frame_start=1, receiver_nfl_id=999),
        dict(game_id=1, play_id=4, nfl_id=9, frame_start=1, receiver_nfl_id=101),
    ])
    res = gm.compare_elimination(match, elim)
    assert res["agree"] == 2 and res["disagree"] == 1
    assert res["ambiguous"] == 1 and res["judgeable"] == 3
    assert res["misattribution_rate"] == pytest.approx(1 / 3)
    assert res["label"] == "TOO FEW JUDGEABLE TURNS FOR A RATE"


# ------------------------------------------------------------------ extraction
def test_extraction_resume_reuses_the_shard(tmp_path):
    rows, _ = one_play()
    tk = write_tracking(tmp_path / "tk.csv", rows)
    need = pd.DataFrame([dict(game_id=1, play_id=10)])
    first = gm.extract_plays([tk], need, tmp_path / "out", chunk_rows=2,
                             resume=True)
    shard = Path(first[0]["shard"])
    mtime = shard.stat().st_mtime_ns
    second = gm.extract_plays([tk], need, tmp_path / "out", chunk_rows=2,
                              resume=True)
    assert second[0]["reused"] is True
    assert shard.stat().st_mtime_ns == mtime
    assert first[0]["rows_kept"] == 4


def test_extraction_is_deterministic(tmp_path):
    rows, _ = one_play()
    tk = write_tracking(tmp_path / "tk.csv", rows + [(1, 10, 999, _ts(0.0), 5.0, 5.0, "NA")])
    need = pd.DataFrame([dict(game_id=1, play_id=10)])
    a = gm.extract_plays([tk], need, tmp_path / "a", chunk_rows=2, resume=False)
    b = gm.extract_plays([tk], need, tmp_path / "b", chunk_rows=2, resume=False)
    da = pd.read_parquet(a[0]["shard"])
    db = pd.read_parquet(b[0]["shard"])
    pd.testing.assert_frame_equal(da, db)


# ------------------------------------------------------------------ estimator
def test_estimator_is_outcome_tests_own_code():
    assert outcome_test.fit.__module__ == "bdb27.outcome_test"
    assert outcome_test.run_all.__module__ == "bdb27.outcome_test"
    assert outcome_test.verdict.__module__ == "bdb27.outcome_test"


def test_geometric_sample_feeds_the_shared_estimator():
    rng = np.random.default_rng(7)
    n_players, per = 4, 30
    turns, match, pp = [], [], []
    for j in range(n_players):
        nid = 500 + j
        for i in range(per):
            g, p, fs = 1, 100 + j * per + i, 10
            ret = float(rng.uniform(0.3, 1.2))
            turns.append(dict(game_id=g, play_id=p, nfl_id=nid, frame_start=fs,
                              position="CB", season=2023, abs_turn_angle=40.0,
                              entry_speed=float(rng.uniform(2, 6)), apex_speed=3.0,
                              apex_retention=ret, speed_retention=ret,
                              plant=False, turn_type="arc"))
            match.append(dict(game_id=g, play_id=p, nfl_id=nid, frame_start=fs,
                              match_status="unique", matched_receiver_nfl_id=700 + i))
            pp.append(dict(game_id=g, play_id=p, nfl_id=nid, team_abbr="DEF",
                           lined_up_position="CB", route_ran=None,
                           separation_at_pass_forward=None))
            pp.append(dict(game_id=g, play_id=p, nfl_id=700 + i, team_abbr="OFF",
                           route_ran="GO", separation_at_pass_forward=float(rng.uniform(0, 6))))
    frame = gm.geometric_sample(pd.DataFrame(match), player_play_frame(pp),
                                pd.DataFrame(turns))
    assert len(frame) == n_players * per
    for col in ("position_fe", "coverage_family", "route_fe", "season_fe",
                "one_turn_per_play", "_play", "separation_yd", "apex_retention"):
        assert col in frame.columns
    fits, boot = outcome_test.run_all(frame)
    assert len(fits) == 9
    assert np.isfinite(fits[0].beta)
    assert fits[0].n_clusters == n_players
    verdict = outcome_test.verdict(fits, boot)
    assert verdict["label"] in {
        "FINDING", "DETECTABLE BUT TOO SMALL TO BE A FINDING",
        "ESTIMATE ONLY — NO CLAIM", "ESTIMATE ONLY — OPPOSITE TO THE STATED EXPECTATION"}


# ------------------------------------------------------------------ verdict text
def test_verdict_never_upgrades_a_null_under_a_failed_gate():
    summary = {
        "misattribution": {"label": "MISATTRIBUTION RATE", "judgeable": 900,
                           "agree": 700, "disagree": 200,
                           "misattribution_rate": 200 / 900},
        "gate": {"attributed_turns": 300, "attributed_fraction": 0.04,
                 "min_turns": 500, "min_share": 0.30, "gate_met": False},
        "estimate": {"beta": -0.9, "lo": -1.2, "hi": -0.6,
                     "elimination_beta": 0.05, "label":
                     "DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE"},
    }
    text = " ".join(gm.plain_english_verdict(summary))
    assert "22.2%" in text
    assert "2026" not in text
    assert "NOT MET" in text
    assert "not met" in text.lower()
    assert "finding" not in text.lower()


def test_verdict_says_not_fitted_when_there_is_no_estimate():
    summary = {"misattribution": {"label": "TOO FEW JUDGEABLE TURNS FOR A RATE",
                                  "judgeable": 3, "agree": 3, "disagree": 0},
               "gate": {"attributed_turns": 4, "attributed_fraction": 0.001,
                        "min_turns": 500, "min_share": 0.30, "gate_met": False}}
    text = " ".join(gm.plain_english_verdict(summary))
    assert "not fitted" in text.lower()
    assert "unrevised" in text.lower()


def test_manifest_hashes_every_file(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("x\n1\n", encoding="utf-8")
    b = tmp_path / "b.json"
    b.write_text("{}", encoding="utf-8")
    m = gm.write_manifest(tmp_path, [a], [b], config={"k": 1})
    assert len(m["inputs"][str(a)]["sha256"]) == 64
    assert len(m["outputs"][str(b)]["sha256"]) == 64
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
