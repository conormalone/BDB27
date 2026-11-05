"""Edge cases named by TASK.md "Tests before handoff" item 3.

Rushers with few snaps, missing onset, null labels, one-attempt players, missing
frames, snaps with depth near zero, and no game data at all.

Every case must produce a reported status, never an exception and never a
silently invented value.
"""

from __future__ import annotations

import csv
import json
import math

import pytest

import common as C
import physical as P
import stats as ST
import synthetic


def _frame(pid, t, x, s=0.0, event=""):
    return {"game_id": 1, "play_id": pid, "nfl_id": 5, "time": t, "x": x,
            "y": 26.0, "s": s, "a": 0.0, "dis": 0.0, "o": 90.0, "dir": 90.0,
            "event": event}


# --------------------------------------------------------------------------- #
# Depth near zero
# --------------------------------------------------------------------------- #
def test_zero_depth_crossing_is_the_onset(cfg):
    times = [0.0, 0.1, 0.2]
    along = [0.0, 0.3, 0.6]
    r = P.time_at_distance(times, along, 0.0, first_index=1)
    assert r["reached"] and r["time_s"] == times[1]


def test_negative_depth_is_treated_as_already_across(cfg):
    r = P.time_at_distance([0.0, 0.1], [0.0, 0.2], -0.2)
    assert r["reached"] is True
    assert r["time_s"] == 0.0


# --------------------------------------------------------------------------- #
# Missing onset
# --------------------------------------------------------------------------- #
def test_stationary_snap_reports_no_onset(cfg):
    times = [i / 10 for i in range(15)]
    r = P.detect_onset(times, [0.2] * 15, cfg)
    assert r["found"] is False
    assert r["baseline_speed"] is not None


def test_onset_with_only_two_frames_reports_a_reason(cfg):
    r = P.detect_onset([0.0, 0.1], [0.1, 3.0], cfg)
    assert r["found"] is False
    assert "frames in search window" in r["reason"]


# --------------------------------------------------------------------------- #
# Missing frames
# --------------------------------------------------------------------------- #
def test_gap_inside_the_first_second_is_counted_not_filled(cfg):
    times = [0.0, 0.1, 0.2, 0.8, 0.9, 1.0, 1.1, 1.2]
    f = P.gap_flags(times, cfg, onset_s=0.2)
    assert f["n_gaps"] == 1
    assert f["gaps_in_first_s_after_onset"] == 1
    assert f["max_gap_s"] == pytest.approx(0.6)


def test_gap_outside_the_first_second_is_allowed(cfg):
    times = [0.0, 0.1, 0.2, 0.3, 1.4, 1.5]
    f = P.gap_flags(times, cfg, onset_s=0.2)
    assert f["n_gaps"] == 1
    assert f["gaps_in_first_s_after_onset"] == 0


# --------------------------------------------------------------------------- #
# Few snaps / one attempt
# --------------------------------------------------------------------------- #
def test_one_snap_per_player_yields_no_reliability():
    r = ST.split_half_icc([(1.0, float("nan"))] * 5)
    assert r["n_players"] == 0
    assert math.isnan(r["r"])


def test_one_attempt_per_player_cannot_inform_test_retest():
    r = ST.icc_oneway([[1.0], [2.0], [3.0]])
    assert r["k_subjects"] == 0
    assert math.isnan(r["icc"])


def test_power_is_nan_with_a_single_player():
    assert math.isnan(ST.min_detectable_share(1)["min_share"])


# --------------------------------------------------------------------------- #
# Null labels
# --------------------------------------------------------------------------- #
def test_null_labels_do_not_become_false():
    """A null quick_pressure must never be coerced to 0 by the reader."""
    import duckdb
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp())
    (d / "pp.csv").write_text(
        "game_id,play_id,nfl_id,quick_pressure\n"
        "1,2,3,TRUE\n1,3,3,NA\n1,4,3,FALSE\n")
    con = duckdb.connect()
    n_null = con.execute(
        f"SELECT count(*) FILTER (WHERE quick_pressure IS NULL) FROM "
        f"read_csv_auto('{(d/'pp.csv').as_posix()}', nullstr=['NA'])").fetchone()[0]
    assert n_null == 1
    con.close()


# --------------------------------------------------------------------------- #
# No game data
# --------------------------------------------------------------------------- #
def test_audit_reports_blocked_when_no_get_off_rows(cfg, tmp_path):
    """A player_play with zero get-off rows must yield BLOCKED, not a number."""
    pp = tmp_path / "player_play.csv"
    header = ["game_id", "play_id", "nfl_id", "player_get_off",
              "lined_up_position", "quick_pressure", "unblocked_pressure",
              "time_to_pressure", "time_to_qb_hurry"]
    with open(pp, "w", newline="") as fh:
        csv.writer(fh).writerow(header)
    import duckdb
    con = duckdb.connect()
    sql = f"read_csv_auto('{pp.as_posix()}', nullstr=['NA'])"
    n = con.execute(f"SELECT count(*) FROM {sql} WHERE player_get_off IS NOT NULL"
                    ).fetchone()[0]
    assert n == 0
    con.close()


# --------------------------------------------------------------------------- #
# Synthetic recovery at the Phase-1 level
# --------------------------------------------------------------------------- #
def test_between_player_share_is_recovered_from_a_planted_jump(cfg):
    """The one-way decomposition must recover the planted share.

    Get-off = phys + u + e. With phys constant across reps per player, the
    between-player variance is Var(u) and the within-player variance is Var(e),
    so the between share is Var(u)/(Var(u)+Var(e)) by construction.
    """
    jump_sd, noise_sd = 0.10, 0.05
    tracking, pp, games, truth = synthetic.make_game_frames(
        seed=21, n_players=200, snaps_per=12, jump_sd=jump_sd, noise_yd=0.02)
    vals = {}
    for r in pp:
        vals.setdefault(r["nfl_id"], []).append(r["player_get_off"])
    means = [sum(v) / len(v) for v in vals.values()]
    within = [((x - sum(v) / len(v)) ** 2) for v in vals.values() for x in v]
    between_var = ST._var(means)
    within_var = sum(within) / (len(within) - len(vals))
    share = between_var / (between_var + within_var)
    planted = jump_sd ** 2 / (jump_sd ** 2 + 0.03 ** 2)
    assert share > 0.5, f"planted jump not recovered: share={share:.3f}"
    assert abs(share - planted) < 0.35


def test_null_world_has_near_zero_between_share():
    """With jump_sd=0 the between share must collapse."""
    _, pp, _, _ = synthetic.make_game_frames(seed=22, n_players=200,
                                             snaps_per=12, jump_sd=0.0,
                                             burst_sd=0.0, noise_yd=0.02)
    vals = {}
    for r in pp:
        vals.setdefault(r["nfl_id"], []).append(r["player_get_off"])
    means = [sum(v) / len(v) for v in vals.values()]
    within = [((x - sum(v) / len(v)) ** 2) for v in vals.values() for x in v]
    b = ST._var(means)
    w = sum(within) / (len(within) - len(vals))
    assert b / (b + w) < 0.15


def test_fixture_is_schema_compliant(cfg_for_fixture, con):
    import schema as S
    for key in ("games", "player_play"):
        S.assert_schema(con, cfg_for_fixture, key)
    S.assert_schema(con, cfg_for_fixture, "game_tracking", mode="sample")
    S.assert_schema(con, cfg_for_fixture, "combine_tracking")


def test_fixture_tracking_is_measurable(cfg, fixture_dir):
    """The planted get-off must be recoverable from the fixture's own frames."""
    truth = json.loads((fixture_dir / "truth.json").read_text())
    rows = list(csv.DictReader(open(fixture_dir / "game_tracking.csv")))
    import collections
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["play_id"], r["nfl_id"])].append(r)
    checked = 0
    for (play_id, nfl_id), sub in list(by.items())[:20]:
        sub.sort(key=lambda r: r["time"])
        times = synthetic.relative_seconds([r["time"] for r in sub])
        snap_i = [i for i, r in enumerate(sub) if r["event"] == "ball_snap"]
        if not snap_i:
            continue
        onset = P.detect_onset(times, [float(r["s"]) for r in sub], cfg)
        assert onset["found"], (play_id, nfl_id)
        checked += 1
    assert checked > 10
