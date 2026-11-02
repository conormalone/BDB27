"""Stage 03 rusher/blocker wiring: coverage guard, labels, cross-checks.

Hand-built CSVs only (no fixture generator): each world is small enough to
reason about by hand, and every assertion pins ``notes/dl-design.md`` §5.4a
behaviour rather than a fitted number.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_blockers as B
from dl import dl_snaps as S
from dl import stage_03_snaps as stage03

PP_COLUMNS = list(S._PP_COLUMNS)
TRACK_COLUMNS = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s",
                 "event"]
GAME_ID = 100
#: LOS candidates for yardline_number 35: 10 + 35 = 45 and 110 - 35 = 75.
LOS_X = 45.0
TIMES = (0.0, 0.5, 1.0, 1.5, 2.0)


# --------------------------------------------------------------------------- #
# CSV world builders
# --------------------------------------------------------------------------- #
def _ts(base: datetime, offset: float) -> str:
    return (base + timedelta(seconds=offset)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")[:-3]


def _pp_row(**kw):
    base = {
        "game_id": GAME_ID, "play_id": 1, "nfl_id": 10, "team_abbr": "AAA",
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


def _fact(play_id: int, nfl_id: int, *, pressure="FALSE", clock="10:00"):
    """One OL row: the play-level facts (dropback duration, pressure)."""
    return _pp_row(play_id=play_id, nfl_id=nfl_id, team_abbr="AAA",
                   dropback_duration=3.0, pressure_allowed=pressure,
                   game_clock=clock)


def _dl_pp(play_id: int, nfl_id: int, position: str, *, ttp="NA",
           clock="10:00"):
    return _pp_row(play_id=play_id, nfl_id=nfl_id, team_abbr="AAA",
                   blitzing="TRUE", lined_up_position=position,
                   player_get_off=0.9, sack=0.0, unblocked_pressure="FALSE",
                   time_to_pressure=ttp, game_clock=clock)


def _emit_play(play_id, base_time, *, off, deff, track, roster):
    """Append five frames per player; return nothing (lists are mutated).

    ``off`` items are ``(nfl_id, x, y, position)`` and constant in time.
    ``deff`` items are ``(nfl_id, x0, x1, y, position)`` and move linearly
    from ``x0`` to ``x1`` across the 2 s window.
    """
    for nfl_id, x, y, pos in off:
        roster[nfl_id] = pos
        for dt in TIMES:
            track.append({
                "game_id": GAME_ID, "play_id": play_id, "nfl_id": nfl_id,
                "time": _ts(base_time, dt), "x": x, "y": y, "s": 0.0,
                "event": "ball_snap" if dt == 0.0 else "NA",
            })
    for nfl_id, x0, x1, y, pos in deff:
        roster[nfl_id] = pos
        for dt in TIMES:
            x = x0 + (x1 - x0) * dt / TIMES[-1]
            track.append({
                "game_id": GAME_ID, "play_id": play_id, "nfl_id": nfl_id,
                "time": _ts(base_time, dt), "x": x, "y": y, "s": 0.0,
                "event": "ball_snap" if dt == 0.0 else "NA",
            })


def _offense(ids, positions, *, drop=()):
    """5 OL + QB + RB + TE + 3 WR, at the hand-checked coordinates."""
    coords = {
        "ol": [(46.5, 16.0, "T"), (46.0, 19.0, "G"), (46.2, 21.0, "C"),
               (46.5, 30.0, "G"), (46.5, 34.0, "T")],
        "skill": [(49.0, 24.0, "QB"), (48.0, 40.0, "RB"),
                  (46.5, 10.0, "TE"), (46.5, 4.0, "WR"),
                  (46.5, 44.0, "WR"), (46.5, 48.0, "WR")],
    }
    out = []
    it = iter(ids)
    for (x, y, _pos), pos in zip(coords["ol"], positions):
        out.append((next(it), x, y, pos))
    for nid, (x, y, pos) in zip(it, coords["skill"]):
        out.append((nid, x, y, pos))
    return [row for row in out if row[0] not in drop]


def _defense(ids, *, rushers=(16.0, 20.0, 30.0, 34.0), drop=()):
    """4 rushers advancing 2 yd, 7 static defenders."""
    out = []
    it = iter(ids)
    for y in rushers:
        out.append((next(it), 43.5, 45.5, y, "DE"))
    for y in (6.0, 10.0, 14.0, 24.0, 26.0, 38.0, 42.0):
        out.append((next(it), 43.5, 43.5, y, "DT"))
    return [row for row in out if row[0] not in drop]


def _write(path, columns, rows):
    pd.DataFrame(rows, columns=columns).astype(object).to_csv(
        path, index=False, na_rep="NA")
    return path


def _build_world(tmp_path, *, plays, tracking=True):
    """Write players/games/player_play/tracking and return the files map."""
    pp: list[dict] = []
    track: list[dict] = []
    roster: dict[int, str] = {}
    for spec in plays:
        _emit_play(spec["play_id"], spec["base_time"], off=spec["off"],
                   deff=spec["deff"], track=track, roster=roster)
        pp.append(_fact(spec["play_id"], spec["fact_id"],
                        pressure=spec.get("pressure", "FALSE"),
                        clock=spec.get("clock", "10:00")))
        for nfl_id, position, ttp in spec["dl"]:
            pp.append(_dl_pp(spec["play_id"], nfl_id, position, ttp=ttp,
                             clock=spec.get("clock", "10:00")))

    track = sorted(track, key=lambda r: (r["play_id"], r["nfl_id"], r["time"]))
    files = {
        "players": _write(
            tmp_path / "players.csv", ["nfl_id", "nfl_position"],
            [{"nfl_id": nid, "nfl_position": pos}
             for nid, pos in sorted(roster.items())]),
        "games": _write(
            tmp_path / "games.csv",
            ["game_id", "game_key", "season", "season_type", "week",
             "home_team_abbr", "visitor_team_abbr"],
            [{"game_id": GAME_ID, "game_key": 1, "season": 2023,
              "season_type": "REG", "week": 1, "home_team_abbr": "AAA",
              "visitor_team_abbr": "BBB"}]),
        "player_play": _write(tmp_path / "player_play.csv", PP_COLUMNS, pp),
        "tracking": None,
    }
    if tracking:
        files["tracking"] = _write(tmp_path / "game_tracking_sample.csv",
                                   TRACK_COLUMNS, track)
    return files


def _standard_world(tmp_path, *, tracking=True):
    """Two all-22 plays: 4 rushers, 5 protectors, different primary groups."""
    b1 = datetime(2023, 9, 10, 13, 0, 0)
    b2 = datetime(2023, 9, 10, 13, 5, 0)
    plays = [
        {"play_id": 1, "base_time": b1, "fact_id": 101, "pressure": "TRUE",
         "off": _offense(range(101, 112),
                         ["T", "G", "C", "G", "T"]),
         "deff": _defense(range(201, 212)),
         "dl": [(201, "EDGE", 2.0), (202, "INTERIOR_LINE", "NA"),
                (203, "EDGE", "NA"), (204, "INTERIOR_LINE", "NA")]},
        {"play_id": 2, "base_time": b2, "fact_id": 301,
         "off": _offense(range(301, 312),
                         ["G", "G", "C", "G", "T"]),
         "deff": _defense(range(401, 412)),
         "dl": [(401, "EDGE", "NA"), (402, "INTERIOR_LINE", "NA"),
                (403, "EDGE", "NA"), (404, "INTERIOR_LINE", "NA")]},
    ]
    return _build_world(tmp_path, plays=plays, tracking=tracking)


def _single_play_world(tmp_path, *, tracking="standard", drop=(), unknown=False):
    """One play world; ``tracking`` may be ``standard``/4/None."""
    b = datetime(2023, 9, 10, 13, 0, 0)
    off = _offense(range(101, 112), ["T", "G", "C", "G", "T"], drop=drop)
    deff = _defense(range(201, 212), drop=drop)
    if unknown:
        # A player inside the LOS band whose motion neither rushes nor stays.
        deff = [row for row in deff if row[0] != 211]
        deff.append((211, 44.5, 45.0, 42.0, "DT"))
        off = [row for row in off if row[0] != 111]
    play = {"play_id": 1, "base_time": b, "fact_id": 101,
            "off": off, "deff": deff,
            "dl": [(201, "EDGE", 2.0), (202, "INTERIOR_LINE", "NA"),
                   (203, "EDGE", "NA"), (204, "INTERIOR_LINE", "NA")]}
    if tracking == 4:
        play["off"] = []
        play["deff"] = [row for row in play["deff"] if row[0] <= 204]
    elif tracking is None:
        return _build_world(tmp_path, plays=[play], tracking=False)
    return _build_world(tmp_path, plays=[play], tracking=True)


def _stage_cfg(cfg, tmp_path):
    clone = copy.deepcopy(cfg)
    block = clone["bdb27_dl"]
    block["paths"].update({
        "raw": str(tmp_path),
        "interim": str(tmp_path / "interim"),
        "parquet": str(tmp_path / "interim" / "parquet"),
        "checkpoints": str(tmp_path / "interim" / "checkpoints"),
        "tmp": str(tmp_path / "interim" / "tmp"),
        "outputs": str(tmp_path / "outputs"),
        "figures": str(tmp_path / "outputs" / "figures"),
        "fixtures": str(tmp_path / "fixtures"),
        "thresholds": str(tmp_path / "outputs" / "thresholds.json"),
        "run_log": str(tmp_path / "outputs" / "run_log.csv"),
        "prereg": str(tmp_path / "notes" / "prereg.md"),
    })
    block["runtime"].update({
        "min_free_mb_root": 1,
        "min_free_mb_interim": 1,
        "temp_directory": str(tmp_path / "interim" / "tmp"),
    })
    return clone


# --------------------------------------------------------------------------- #
# a. all-22 play: the derivation lands on every rusher row
# --------------------------------------------------------------------------- #
def test_all22_play_lands_rushers_blockers_and_groups(tmp_path, cfg):
    files = _standard_world(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert len(df) == 8                       # 4 DL rows on each of 2 plays
    assert df["n_rushers"].eq(4.0).all()
    assert df["blitz"].eq(0.0).all()
    assert df["n_blockers"].eq(5.0).all()
    assert df["primary_blocker_group"].notna().all()
    assert df["primary_blocker_group_source"].notna().all()
    for col in ("n_rushers", "n_blockers"):
        assert int((df[col] == 0).sum()) == 0
    # The play-level label differs between the two hand-built plays, so the
    # labels are not identical across rusher rows.
    assert df["primary_blocker_group"].nunique() == 2
    assert set(df["primary_blocker_group"]) == {"OT", "interior"}

    block = summary["blockers"]
    assert block["n_plays_with_all22"] == 2
    assert block["n_plays_scored"] == 2
    assert block["n_plays_derivable"] == 2
    assert block["n_snaps_with_n_rushers"] == 8
    assert block["n_snaps_blockers_na"] == 0


# --------------------------------------------------------------------------- #
# b/c. coverage guard: sample-like and 19-player tracking
# --------------------------------------------------------------------------- #
def test_sample_like_tracking_is_na_and_counted(tmp_path, cfg):
    files = _single_play_world(tmp_path, tracking=4)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    for col in B.BLOCKER_COLUMNS:
        assert df[col].isna().all(), col
    block = summary["blockers"]
    assert block["n_plays_with_all22"] == 0
    assert block["n_plays_scored"] == 0
    assert block["n_snaps_with_n_rushers"] == 0
    assert block["n_snaps_blockers_na"] == len(df)
    assert "1-4 players per play" in block["coverage_reason"]
    assert "sample_cannot_exercise" in block


def test_nineteen_tracked_players_are_below_the_guard(tmp_path, cfg):
    files = _single_play_world(tmp_path, drop=(110, 111, 211))
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert summary["blockers"]["n_players_at_snap"]["max"] == 19
    for col in ("n_rushers", "blitz", "n_blockers",
                "primary_blocker_group", "primary_blocker_group_source"):
        assert df[col].isna().all(), col
    assert summary["blockers"]["n_plays_with_all22"] == 0
    assert summary["blockers"]["n_snaps_blockers_na"] == len(df)


# --------------------------------------------------------------------------- #
# d. no tracking input: the existing omission path is unchanged
# --------------------------------------------------------------------------- #
def test_no_tracking_input_records_the_omission(tmp_path, cfg):
    files = _single_play_world(tmp_path, tracking=None)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert summary["omitted_columns"]["blockers"] == "no tracking input"
    for col in ("n_rushers", "blitz", "n_blockers",
                "primary_blocker_group", "primary_blocker_group_source"):
        assert df[col].isna().all(), col


# --------------------------------------------------------------------------- #
# e. determinism
# --------------------------------------------------------------------------- #
def test_two_builds_are_identical(tmp_path, cfg):
    files = _standard_world(tmp_path)
    a = S.build_snaps(cfg, "sample", files=files)
    b = S.build_snaps(cfg, "sample", files=files)
    pd.testing.assert_frame_equal(a, b)


def test_two_stage_runs_are_byte_identical(tmp_path, cfg):
    _standard_world(tmp_path)
    stage_cfg = _stage_cfg(cfg, tmp_path)
    out = stage03._out_dir(stage_cfg, "sample")
    stage03.run(stage_cfg, "sample", force=True)
    first_snaps = (out / "snaps.csv").read_bytes()
    first_cross = (out / "derivation_crosscheck.csv").read_bytes()
    stage03.run(stage_cfg, "sample", force=True)
    assert (out / "snaps.csv").read_bytes() == first_snaps
    assert (out / "derivation_crosscheck.csv").read_bytes() == first_cross


# --------------------------------------------------------------------------- #
# f. an undecidable player withholds the whole play
# --------------------------------------------------------------------------- #
def test_unclassified_player_withholds_all_five_columns(tmp_path, cfg):
    files = _single_play_world(tmp_path, unknown=True)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert summary["blockers"]["n_players_at_snap"]["max"] == 21
    assert summary["blockers"]["n_plays_with_all22"] == 1
    assert summary["blockers"]["reason_counts"] == {
        "unclassified_players": 1}
    for col in ("n_rushers", "blitz", "n_blockers",
                "primary_blocker_group", "primary_blocker_group_source"):
        assert df[col].isna().all(), col
    assert int((df["n_rushers"] == 0).sum()) == 0


# --------------------------------------------------------------------------- #
# g. cross-check helper: NOT_RUN on all-NA blitz, arithmetic means otherwise
# --------------------------------------------------------------------------- #
def _cross_frame(blitz):
    return pd.DataFrame({
        "alignment": ["edge", "edge", "interior", "interior"],
        "event": [1, 0, 0, 0],
        "blitz": blitz,
        "unblocked_pressure": [1.0, 0.0, 0.0, 0.0],
    })


def test_crosscheck_pressure_rates_are_arithmetic_means():
    rows = S._crosscheck(_cross_frame([0.0, 0.0, 1.0, 1.0]))
    rates = {r["stratum"]: r for r in rows
             if r["check"] == "pressure_rate_by_blitz"}
    assert rates["blitz=0"]["value"] == pytest.approx(0.5)
    assert rates["blitz=1"]["value"] == pytest.approx(0.0)
    assert rates["blitz=0"]["n"] == 2
    assert rates["blitz=1"]["n"] == 2
    assert all(r["status"] == "OK" for r in rates.values())


def test_crosscheck_all_na_blitz_is_not_run_with_a_reason():
    rows = S._crosscheck(_cross_frame(pd.Series([pd.NA] * 4, dtype=object)))
    rates = [r for r in rows if r["check"] == "pressure_rate_by_blitz"]
    assert len(rates) == 2
    assert all(r["status"] == "NOT_RUN" for r in rates)
    assert all(r["reason"] for r in rates)
    assert all("NA in sample mode" in r["reason"] for r in rates)


# --------------------------------------------------------------------------- #
# h. string group columns are empty cells, never "nan"/"NA"
# --------------------------------------------------------------------------- #
def test_group_columns_are_empty_csv_cells_when_missing(tmp_path, cfg):
    _single_play_world(tmp_path, tracking=4)
    stage_cfg = _stage_cfg(cfg, tmp_path)
    stage03.run(stage_cfg, "sample", force=True)
    path = stage03._out_dir(stage_cfg, "sample") / "snaps.csv"
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    for col in ("primary_blocker_group", "primary_blocker_group_source"):
        assert set(raw[col]) == {""}
        assert not raw[col].isin(["nan", "NA"]).any()


# --------------------------------------------------------------------------- #
# i. alignment_split compares the DL cohort on both sides
# --------------------------------------------------------------------------- #
def _cohort_world(tmp_path, *, add_non_cohort=True):
    """One play whose DL rows are EDGE / INTERIOR_LINE / unmapped.

    The player_play table also carries an EDGE-labelled defender whom
    ``players.csv`` does not list (the sample's non-prospect defender case):
    the formation label alone would count him, the DL cohort must not.
    """
    b = datetime(2023, 9, 10, 13, 0, 0)
    play = {
        "play_id": 1, "base_time": b, "fact_id": 101,
        "off": _offense(range(101, 112), ["T", "G", "C", "G", "T"]),
        "deff": _defense(range(201, 212)),
        # 203 is labelled outside the EDGE/INTERIOR_LINE pair: unmapped.
        "dl": [(201, "EDGE", 2.0), (202, "INTERIOR_LINE", "NA"),
               (203, "BOX", "NA"), (204, "INTERIOR_LINE", "NA")],
    }
    files = _build_world(tmp_path, plays=[play], tracking=True)
    if add_non_cohort:
        path = files["player_play"]
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        extra = _pp_row(nfl_id=999, lined_up_position="EDGE",
                        blitzing="TRUE", player_get_off=0.9, sack=0.0,
                        unblocked_pressure="FALSE", time_to_pressure="NA")
        frame = pd.concat([frame, pd.DataFrame([extra], columns=PP_COLUMNS)],
                          ignore_index=True)
        frame.to_csv(path, index=False)
    return files


def test_alignment_split_compares_the_dl_cohort_not_formation_labels(
        tmp_path, cfg):
    files = _cohort_world(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    # The non-cohort defender is absent from players.csv, so stage 03 never
    # emits him and the cross-check must not count him either.
    assert 999 not in set(df["nfl_id"])
    assert len(df) == 4

    rows = {(r["check"], r["stratum"]): r for r in summary["crosscheck"]}
    snap = {k: rows[("alignment_split", f"snap_table:{k}")]
            for k in ("edge", "interior", "unmapped")}
    pp = {k: rows[("alignment_split", f"player_play_rush_rows:{k}")]
          for k in ("EDGE", "INTERIOR_LINE", "unmapped")}

    # pp side equals the table's alignment value counts, stratum by stratum.
    assert snap["edge"]["n"] == int((df["alignment"] == "edge").sum()) == 1
    assert snap["interior"]["n"] == int(
        (df["alignment"] == "interior").sum()) == 2
    assert snap["unmapped"]["n"] == int(df["alignment"].isna().sum()) == 1
    assert pp["EDGE"]["n"] == snap["edge"]["n"]
    assert pp["INTERIOR_LINE"]["n"] == snap["interior"]["n"]
    assert pp["unmapped"]["n"] == snap["unmapped"]["n"]
    # The EDGE-labelled non-cohort row is not in the pp count (would be 2).
    assert pp["EDGE"]["n"] == 1
    # Nothing is dropped: the three strata cover the whole population.
    assert sum(r["n"] for r in pp.values()) == len(df) == 4
    for side in (snap, pp):
        for row in side.values():
            assert row["status"] == "OK"
            assert row["value"] == pytest.approx(row["n"] / 4)


def test_unmapped_lined_up_position_is_a_stratum_never_dropped(tmp_path, cfg):
    files = _cohort_world(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    rows = {r["stratum"]: r for r in summary["crosscheck"]
            if r["check"] == "alignment_split"}

    assert int(df["alignment"].isna().sum()) == 1
    assert rows["player_play_rush_rows:unmapped"]["n"] == 1
    assert rows["snap_table:unmapped"]["n"] == 1
    assert rows["player_play_rush_rows:unmapped"]["status"] == "OK"


def test_unblocked_pressure_share_by_position_group_is_reported(tmp_path, cfg):
    files = _cohort_world(tmp_path)
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)
    rows = {r["stratum"]: r for r in summary["crosscheck"]
            if r["check"] == "unblocked_pressure_share_by_position_group"}

    # Every DL row in this world is a DE; DT/NT are empty and say so.
    assert set(df["position_group"]) == {"DE"}
    assert rows["DE"]["n"] == len(df) == 4
    assert rows["DE"]["status"] == "OK"
    assert rows["DE"]["value"] == pytest.approx(0.0)
    for group in ("DT", "NT"):
        assert rows[group]["n"] == 0
        assert rows[group]["status"] == "NOT_RUN"
        assert rows[group]["reason"]


def test_unblocked_pressure_share_by_position_group_arithmetic():
    frame = pd.DataFrame({
        "alignment": ["edge", "edge", "interior", "interior", "interior"],
        "position_group": ["DE", "DE", "DT", "DT", "NT"],
        "event": [1, 0, 0, 0, 0],
        "blitz": [pd.NA] * 5,
        "unblocked_pressure": [1.0, 0.0, 0.0, 1.0, pd.NA],
    })
    rows = {r["stratum"]: r for r in S._crosscheck(frame)
            if r["check"] == "unblocked_pressure_share_by_position_group"}

    assert rows["DE"]["n"] == 2
    assert rows["DE"]["value"] == pytest.approx(0.5)
    assert rows["DT"]["n"] == 2
    assert rows["DT"]["value"] == pytest.approx(0.5)
    assert rows["NT"]["n"] == 1
    assert rows["NT"]["status"] == "NOT_RUN"
    assert "NA on every row" in rows["NT"]["reason"]
    assert "rare" in rows["DE"]["note"]


def test_crosscheck_rows_carry_status_and_never_a_verdict(tmp_path, cfg):
    files = _cohort_world(tmp_path)
    _, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert summary["crosscheck"]
    for row in summary["crosscheck"]:
        assert set(row) == set(S._CROSSCHECK_FIELDS)
        assert "verdict" not in row
        assert row["status"] in {"OK", "NOT_RUN"}
        if row["status"] == "NOT_RUN":
            assert row["reason"]


# --------------------------------------------------------------------------- #
# j. planted all-22 worlds the brief names explicitly
# --------------------------------------------------------------------------- #
# Five linemen on the line (x is the distance from the line of scrimmage, so the
# line is x = 45.0 and a rusher starts 0.6 yd in front of it), a tight end on
# the line, a back 3 yd deep and three receivers.  Roster positions are supplied,
# exactly as the stage supplies them from players.csv.
_LINE_OFFENCE = [
    (101, 45.0, 17.0, "T"), (102, 45.0, 19.0, "G"), (103, 45.0, 21.0, "C"),
    (104, 45.0, 23.0, "G"), (105, 45.0, 25.0, "T"), (106, 50.5, 21.0, "QB"),
    (107, 48.0, 22.0, "RB"), (108, 45.0, 27.5, "TE"), (109, 46.5, 4.0, "WR"),
    (110, 46.5, 38.0, "WR"), (111, 46.5, 44.0, "WR"),
]


def _line_defence(ids, *, rushers, static):
    """Rushers advance 2.0 yd through the line; the rest hold their ground."""
    out = []
    it = iter(ids)
    for y in rushers:
        out.append((next(it), 44.4, 46.4, y, "DE"))
    for y in static:
        out.append((next(it), 44.4, 44.4, y, "DT"))
    return out


def _line_play(base_time, *, rushers, static):
    return {"play_id": 1, "base_time": base_time, "fact_id": 101,
            "off": list(_LINE_OFFENCE),
            "deff": _line_defence(range(201, 212), rushers=rushers,
                                  static=static),
            "dl": [(201 + i, "EDGE" if i in (0, 3) else "INTERIOR_LINE", "NA")
                   for i in range(len(rushers))]}


def test_six_man_pressure_world_is_a_blitz(tmp_path, cfg):
    """Planted truth: six rushers, five linemen plus the tight end in."""
    b = datetime(2023, 9, 10, 13, 0, 0)
    play = _line_play(b, rushers=(17.0, 19.0, 21.5, 23.0, 25.0, 27.5),
                      static=(6.0, 10.0, 14.0, 32.0, 36.0))
    files = _build_world(tmp_path, plays=[play])
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert len(df) == 6
    assert df["n_rushers"].eq(6.0).all()
    assert df["blitz"].eq(1.0).all()            # 6 > blitz_rusher_threshold
    assert df["n_blockers"].eq(6.0).all()       # five linemen + the tight end
    by_id = df.set_index("nfl_id")["primary_blocker_group"]
    assert by_id.loc[206] == "TE"               # the tight end's own rusher
    assert by_id.loc[201] == "OT"
    assert set(by_id) == {"OT", "interior", "TE"}
    assert summary["blockers"]["n_plays_derivable"] == 1


def test_te_stays_in_and_a_double_team_is_recovered(tmp_path, cfg):
    """Planted truth: five rushers, six protectors, one double-teamed rusher."""
    b = datetime(2023, 9, 10, 13, 0, 0)
    play = _line_play(b, rushers=(17.0, 19.9, 23.0, 25.0, 27.5),
                      static=(6.0, 10.0, 14.0, 32.0, 36.0, 40.0))
    files = _build_world(tmp_path, plays=[play])
    df, summary = S.build_snaps(cfg, "sample", files=files, with_summary=True)

    assert len(df) == 5
    assert df["n_rushers"].eq(5.0).all()
    assert df["blitz"].eq(1.0).all()
    assert df["n_blockers"].eq(6.0).all()       # five linemen + the tight end
    by_id = df.set_index("nfl_id")["primary_blocker_group"]
    assert by_id.loc[205] == "TE"               # the tight end stayed in
    # the second rusher is between the guard and the centre; the guard is nearer
    # in every window frame, so the double team resolves to the interior guard
    assert by_id.loc[202] == "interior"
    assert df["primary_blocker_group_source"].eq("roster").all()
    assert summary["blockers"]["n_plays_derivable"] == 1
