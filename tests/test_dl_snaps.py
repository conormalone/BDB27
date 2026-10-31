"""dl_snaps: the DL pass-rush snap table, mechanically pinned.

Every test is mechanical.  It pins pr ereg §4–§6 (outcome, censoring rule,
primary snap set) and design §5.4 (columns), on small in-memory-style CSVs
written to ``tmp_path``.  No fitted number is asserted anywhere.
"""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from dl import dl_snaps as S

#: player_play columns this module reads (order matters only for the CSV).
PP_COLUMNS = list(S._PP_COLUMNS)


def _row(**kw):
    """One player_play row; missing values are written as the ``NA`` marker."""
    base = {
        "game_id": 100, "play_id": 1, "nfl_id": 1, "team_abbr": "AAA",
        "possession_team": "BBB", "lined_up_position": "EDGE",
        "quarter": 1, "down": 1, "yards_to_go": 10, "game_clock": "10:00",
        "pre_snap_home_score": 0, "pre_snap_visitor_score": 0,
        "yardline_side": "BBB", "yardline_number": 35, "play_direction": "left",
        "dropback_duration": "NA", "pressure_allowed": "NA",
        "quick_pressure": "NA", "unblocked_pressure": "NA",
        "blitzing": "NA", "player_get_off": "NA", "sack": "NA",
        "time_to_pressure": "NA",
    }
    base.update(kw)
    return base


def _write(path, columns, rows):
    frame = pd.DataFrame(rows, columns=columns).astype(object)
    frame.to_csv(path, index=False, na_rep="NA")
    return path


def _files(tmp_path, *, extra_plays=(), tracking_rows=None):
    """Write the minimal four-file fixture and return the ``files`` override."""
    df_players = [
        {"nfl_id": 1, "nfl_position": "DE"},
        {"nfl_id": 2, "nfl_position": "DT"},
        {"nfl_id": 3, "nfl_position": "WR"},
        {"nfl_id": 10, "nfl_position": "G"},
        {"nfl_id": 11, "nfl_position": "T"},
    ]
    df_games = [{"game_id": 100, "game_key": 1, "season": 2023,
                 "season_type": "REG", "week": 1,
                 "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"}]

    rows = []
    # p1: two rush snaps, one pressure; the play facts sit on TWO OL rows so a
    # naive join would double both DL rows (the 17,481 -> 25,088 trap).
    rows += [
        _row(play_id=1, nfl_id=10, dropback_duration=3.0, pressure_allowed="TRUE"),
        _row(play_id=1, nfl_id=11, dropback_duration=3.0, pressure_allowed="TRUE"),
        _row(play_id=1, nfl_id=1, blitzing="TRUE", time_to_pressure=2.0,
             quick_pressure="TRUE", sack=0.0, unblocked_pressure="FALSE",
             player_get_off=0.8),
        _row(play_id=1, nfl_id=2, blitzing="TRUE", sack=0.0,
             unblocked_pressure="FALSE", player_get_off=0.9,
             lined_up_position="INTERIOR_LINE"),
    ]
    # p2: no dropback_duration anywhere => null censor, both snaps excluded.
    rows += [
        _row(play_id=2, nfl_id=1, blitzing="TRUE"),
        _row(play_id=2, nfl_id=2, blitzing="TRUE",
             lined_up_position="INTERIOR_LINE"),
    ]
    # p3: pressure after the ball is out => event 0, counted as inconsistent.
    rows += [
        _row(play_id=3, nfl_id=10, dropback_duration=2.5,
             pressure_allowed="FALSE"),
        _row(play_id=3, nfl_id=1, blitzing="TRUE", time_to_pressure=5.0),
    ]
    # p4: blitzing FALSE => coverage row, excluded and counted.
    rows += [
        _row(play_id=4, nfl_id=10, dropback_duration=3.0,
             pressure_allowed="FALSE"),
        _row(play_id=4, nfl_id=1, blitzing="FALSE"),
    ]
    # p5-p8: garbage-time boundaries (quarter x margin).
    for play_id, quarter, home, visitor in (
            (5, 4, 24, 3), (6, 1, 28, 0), (7, 1, 27, 0), (8, 4, 20, 0)):
        rows += [
            _row(play_id=play_id, nfl_id=10, quarter=quarter,
                 pre_snap_home_score=home, pre_snap_visitor_score=visitor,
                 dropback_duration=3.0, pressure_allowed="FALSE"),
            _row(play_id=play_id, nfl_id=2, quarter=quarter, blitzing="TRUE",
                 pre_snap_home_score=home, pre_snap_visitor_score=visitor,
                 lined_up_position="INTERIOR_LINE"),
        ]
    # p9: an out-of-family lined_up_position => alignment NA, counted.
    rows += [
        _row(play_id=9, nfl_id=10, dropback_duration=3.0,
             pressure_allowed="FALSE"),
        _row(play_id=9, nfl_id=1, blitzing="TRUE", lined_up_position="MLB"),
    ]
    # p10: the tracking play (one tracked rusher, one untracked).
    rows += [
        _row(play_id=10, nfl_id=10, dropback_duration=3.0,
             pressure_allowed="FALSE"),
        _row(play_id=10, nfl_id=1, blitzing="TRUE"),
        _row(play_id=10, nfl_id=2, blitzing="TRUE",
             lined_up_position="INTERIOR_LINE"),
    ]
    rows += list(extra_plays)

    files = {
        "players": _write(tmp_path / "players.csv",
                          ["nfl_id", "nfl_position"], df_players),
        "games": _write(tmp_path / "games.csv",
                        ["game_id", "game_key", "season", "season_type",
                         "week", "home_team_abbr", "visitor_team_abbr"],
                        df_games),
        "player_play": _write(tmp_path / "player_play.csv", PP_COLUMNS, rows),
        "tracking": None,
    }
    if tracking_rows is not None:
        files["tracking"] = _write(
            tmp_path / "tracking.csv",
            ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "event"],
            tracking_rows)
    return files


def _track(play_id=10, nfl_id=1, t="2023-09-10 13:00:", x=44.0, s="0.0"):
    return {"game_id": 100, "play_id": play_id, "nfl_id": nfl_id,
            "time": t, "x": x, "y": 26.0, "s": s, "event": "NA"}


def _tracking_rows():
    rows = [
        # snap frame at 13:00:00.0, x = 44.0 (BBB is the visitor, so the LOS is
        # 10 + 35 = 45 and a 'left' play gives depth = 45 - 44 = 1.0)
        {**_track(t="2023-09-10 13:00:00.0", s="0.0"), "event": "ball_snap"},
        _track(t="2023-09-10 13:00:00.5", s="1.0"),
        _track(t="2023-09-10 13:00:01.0", s="3.0"),
        _track(t="2023-09-10 13:00:01.1", s="3.4"),   # nearest frame wins
        _track(t="2023-09-10 13:00:02.0", s="4.5"),
    ]
    return rows


# --------------------------------------------------------------------------- #
def test_join_is_deduplicated_to_one_row_per_play(tmp_path, cfg):
    """Two OL rows per play must not multiply the DL rows."""
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    # p1(2) + p3(1) + p5..p8(4) + p9(1) + p10(2) = 10; p2 (null censor) and
    # p4 (coverage) contribute none.
    assert len(df) == 10
    assert summary["counts"]["n_rush_rows"] == 12      # 2 p2 rows included
    assert summary["counts"]["n_excluded_null_censor"] == 2
    assert summary["counts"]["n_excluded_coverage"] == 1
    # the naive join would have produced 22 rows (11 DL rows x 2 OL rows)
    assert df[["game_id", "play_id", "nfl_id"]].duplicated().sum() == 0


def test_null_censor_rows_are_excluded_and_counted(tmp_path, cfg):
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    assert df["censor_time"].notna().all()
    assert summary["counts"]["n_excluded_null_censor"] == 2
    # a null-censor snap never reaches the table: no p2 rows survive
    assert (df["play_id"] == 2).sum() == 0


def test_time_to_pressure_after_censor_is_event_zero(tmp_path, cfg):
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    row = df[(df["play_id"] == 3) & (df["nfl_id"] == 1)].iloc[0]
    assert row["event"] == 0
    assert row["time_to_pressure"] == 5.0          # kept, not silently dropped
    assert row["censor_time"] == 2.5
    assert summary["counts"]["n_ttp_gt_censor_event_set_to_0"] == 1


def test_event_rate_assertion_fires_above_half(tmp_path, cfg):
    """All-pressured fixtures are exactly what prereg §5 forbids fitting on."""
    plays = []
    for play_id in range(20, 40):      # 20 pressured snaps vs 10 baseline rows
        plays += [
            _row(play_id=play_id, nfl_id=10, dropback_duration=3.0,
                 pressure_allowed="TRUE"),
            _row(play_id=play_id, nfl_id=1, blitzing="TRUE",
                 time_to_pressure=1.5),
        ]
    files = _files(tmp_path, extra_plays=plays)
    with pytest.raises(AssertionError, match="event rate"):
        S.build_snaps(cfg, "sample", files=files)


def test_event_rate_exposed_and_below_half(tmp_path, cfg):
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    assert summary["event_rate"] == pytest.approx(df["event"].mean())
    assert 0.0 < summary["event_rate"] < 0.5
    assert summary["checks"]["event_rate_below_half"] is True


def test_garbage_time_boundaries(tmp_path, cfg):
    files = _files(tmp_path)
    df = S.build_snaps(cfg, "sample", files=files)
    got = {int(r.play_id): bool(r.garbage_time)
           for r in df[df["nfl_id"] == 2].itertuples()}
    assert got[5] is True      # q4, margin exactly 21 => garbage
    assert got[6] is True      # any quarter, margin 28 => garbage
    assert got[7] is False     # 27 in q1 => not garbage
    assert got[8] is False     # q4 margin 20 => not garbage


def test_score_margin_sign_is_defender_perspective(tmp_path, cfg):
    """AAA is the HOME team here, so a home lead is a positive margin."""
    files = _files(tmp_path)
    df = S.build_snaps(cfg, "sample", files=files)
    row = df[(df["play_id"] == 5) & (df["nfl_id"] == 2)].iloc[0]
    assert row["score_margin"] == 21.0


def test_game_clock_seconds_is_minutes_seconds_not_hours_minutes(tmp_path, cfg):
    """``game_clock`` is mm:ss; TIME parsing puts minutes in the hour slot."""
    files = _files(tmp_path)          # every fixture play is stamped 10:00
    df = S.build_snaps(cfg, "sample", files=files)
    assert df["game_clock_s"].eq(600.0).all()


def test_underivable_fields_are_na_never_zero(tmp_path, cfg):
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    for col in ("blitz", "n_rushers", "n_blockers", "stunt",
                "absolute_load", "relative_load"):
        assert df[col].isna().all(), col
        assert not (df[col] == 0).any(), col
        assert summary["not_derivable"][col]


def test_tracking_features_and_depth(tmp_path, cfg):
    files = _files(tmp_path, tracking_rows=_tracking_rows())
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    row = df[(df["play_id"] == 10) & (df["nfl_id"] == 1)].iloc[0]
    # LOS = 10 + 35 = 45 (BBB is the visitor side), left play => 45 - 44
    assert row["pre_snap_depth_yd"] == pytest.approx(1.0)
    assert row["in_game_speed_1_0"] == pytest.approx(3.0)   # nearest frame wins
    assert row["in_game_speed_2_0"] == pytest.approx(4.5)
    # the untracked rusher on the same snap stays NA and is counted
    other = df[(df["play_id"] == 10) & (df["nfl_id"] == 2)].iloc[0]
    assert pd.isna(other["pre_snap_depth_yd"])
    assert summary["counts"]["n_snaps_with_depth"] == 1


def test_missing_tracking_is_reported_not_faked(tmp_path, cfg):
    files = _files(tmp_path)          # tracking override is None
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    assert df["pre_snap_depth_yd"].isna().all()
    assert "tracking_features" in summary["omitted_columns"]


def test_depth_uses_the_los_candidate_the_rushers_stand_on(tmp_path, cfg):
    """A team defends opposite end zones in different halves, so the LOS is
    picked from the two candidate x positions per play, not from a fixed
    home/visitor rule."""
    plays = [
        _row(play_id=11, nfl_id=10, dropback_duration=3.0,
             pressure_allowed="FALSE", yardline_side="BBB",
             yardline_number=39, play_direction="left"),
        _row(play_id=11, nfl_id=1, blitzing="TRUE", yardline_side="BBB",
             yardline_number=39, play_direction="left"),
    ]
    track = _tracking_rows() + [
        {**_track(play_id=11, x=71.0, t="2023-09-10 13:05:00.0"),
         "event": "ball_snap"},
        _track(play_id=11, x=71.0, t="2023-09-10 13:05:01.0", s="2.0"),
    ]
    files = _files(tmp_path, extra_plays=plays, tracking_rows=track)
    df = S.build_snaps(cfg, "sample", files=files)
    row = df[(df["play_id"] == 11)].iloc[0]
    # candidates: 10+39 = 49 (22 yd away) and 110-39 = 71 (on the money)
    assert row["pre_snap_depth_yd"] == pytest.approx(0.0, abs=0.01)


def test_columns_and_order_without_load_module(tmp_path, cfg, monkeypatch):
    """A load module with no hook omits LOAD_COLUMNS and says so.

    The real module now has ``add_load_columns``, so the fallback is exercised
    by substituting a hook-less module -- the contract under test is the
    omission path, not the absence of the module.
    """
    fake = types.ModuleType("dl.dl_load")
    monkeypatch.setitem(sys.modules, "dl.dl_load", fake)
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    expected = [c for c in S.SNAP_COLUMNS
                if c not in ("total_distance_before", "snaps_in_drive",
                             "seconds_since_last_snap")]
    assert list(df.columns) == expected
    assert summary["checks"]["columns_in_declared_order"] is True
    for col in ("total_distance_before", "snaps_in_drive",
                "seconds_since_last_snap"):
        assert col in summary["omitted_columns"]


def test_load_hook_materialises_the_sequence_columns(tmp_path, cfg):
    """The real ``dl_load.add_load_columns`` owns LOAD_COLUMNS on the table."""
    files = _files(tmp_path, tracking_rows=_tracking_rows())
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    for col in S.LOAD_COLUMNS:
        assert col in df.columns, col
        assert df[col].dtype == "float64", col
    assert summary["checks"]["columns_in_declared_order"] is True
    assert list(df.columns) == [c for c in S.SNAP_COLUMNS if c in df.columns]

    per_player = df.sort_values(["nfl_id", "game_id", "quarter",
                                "game_clock_s", "play_id"], ascending=[True,
                                True, True, False, True])
    first = per_player.groupby(["nfl_id", "game_id"]).head(1)
    assert first["seconds_since_last_snap"].isna().all()
    assert first["total_distance_before"].eq(0.0).all()
    # no threshold is frozen in sample mode => the flat columns stay NA
    assert df["absolute_load"].isna().all()
    assert df["relative_load"].isna().all()
    assert df["snaps_in_drive"].isna().all()


def test_first_snap_seconds_since_last_snap_is_na(tmp_path, cfg, monkeypatch):
    """With a load-module hook present, the game's first snap has no gap."""
    calls = {}

    def add_load_columns(frame, cfg_, mode):
        calls["n"] = len(frame)
        frame = frame.sort_values(["nfl_id", "game_id", "play_id"])
        frame["seconds_since_last_snap"] = (
            frame.groupby(["nfl_id", "game_id"])["play_id"].diff().astype(float))
        frame["snaps_in_drive"] = (
            frame.groupby(["nfl_id", "game_id"]).cumcount().astype(float))
        frame["total_distance_before"] = 0.0
        frame["absolute_load"] = pd.NA
        frame["relative_load"] = pd.NA
        return frame

    fake = types.ModuleType("dl.dl_load")
    fake.add_load_columns = add_load_columns
    monkeypatch.setitem(sys.modules, "dl.dl_load", fake)

    files = _files(tmp_path)
    df = S.build_snaps(cfg, "sample", files=files)
    assert calls["n"] == len(df)
    first = df.sort_values(["nfl_id", "game_id", "play_id"]).groupby(
        ["nfl_id", "game_id"]).head(1)
    assert first["seconds_since_last_snap"].isna().all()
    assert first["snaps_in_drive"].eq(0.0).all()


def test_determinism(tmp_path, cfg):
    files = _files(tmp_path, tracking_rows=_tracking_rows())
    first = S.build_snaps(cfg, "sample", files=files)
    second = S.build_snaps(cfg, "sample", files=files)
    pd.testing.assert_frame_equal(first, second)


def test_player_get_off_and_alignment_mapping(tmp_path, cfg):
    files = _files(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    edge = df[(df["play_id"] == 1) & (df["nfl_id"] == 1)].iloc[0]
    interior = df[(df["play_id"] == 1) & (df["nfl_id"] == 2)].iloc[0]
    assert edge["alignment"] == "edge"
    assert interior["alignment"] == "interior"
    assert edge["position_group"] == "DE"
    assert interior["position_group"] == "DT"
    assert edge["player_get_off"] == pytest.approx(0.8)
    # the MLB alignment row is unmapped, not silently coerced
    assert pd.isna(df[(df["play_id"] == 9)].iloc[0]["alignment"])
    assert summary["counts"]["n_alignment_unmapped"] == 1
