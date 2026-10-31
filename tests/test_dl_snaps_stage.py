"""DL stage 03: the snap table, the drive key, and the provenance gate.

Every test is mechanical: it pins ``notes/dl-design.md`` §5.4 (schema, counts,
null semantics) and the D72 drive rule on small CSVs written to ``tmp_path``.
No fitted number is asserted anywhere.
"""

from __future__ import annotations

import copy
import json

import pandas as pd
import pytest
import yaml

from dl import dl_common as D
from dl import dl_snaps as S
from dl import stage_03_snaps as stage03

PP_COLUMNS = list(S._PP_COLUMNS)
SNAP_FIELDS = ["provenance"] + list(S.SNAP_COLUMNS)


# --------------------------------------------------------------------------- #
# Fixture world
# --------------------------------------------------------------------------- #
def _row(**kw):
    base = {
        "game_id": 100, "play_id": 1, "nfl_id": 10, "team_abbr": "AAA",
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


def _off(play_id, *, game_id=100, clock="10:00", quarter=1, poss="BBB",
         home=0, visitor=0, dropback=3.0, pressure="FALSE"):
    """One OL row: it carries the play-level facts for the whole play."""
    return _row(game_id=game_id, play_id=play_id, nfl_id=10, team_abbr="AAA",
                possession_team=poss, quarter=quarter, game_clock=clock,
                pre_snap_home_score=home, pre_snap_visitor_score=visitor,
                dropback_duration=dropback, pressure_allowed=pressure)


def _dl(play_id, nfl_id=1, *, game_id=100, clock="10:00", quarter=1,
        poss="BBB", home=0, visitor=0, blitzing="TRUE",
        lined_up="EDGE", ttp="NA"):
    return _row(game_id=game_id, play_id=play_id, nfl_id=nfl_id,
                team_abbr="AAA", possession_team=poss, quarter=quarter,
                game_clock=clock, pre_snap_home_score=home,
                pre_snap_visitor_score=visitor, blitzing=blitzing,
                lined_up_position=lined_up, time_to_pressure=ttp,
                player_get_off=0.8, sack=0.0, unblocked_pressure="FALSE")


def _rows():
    """A world built to exercise every drive boundary and every exclusion.

    rusher 1's expected ``snaps_in_drive`` sequence, in play order:
    p1 0 (first play), p2 1 (same drive), p3 0 (possession change),
    p4 0 (score change, same possession), p5 NA (unknown possession),
    p6 0 (after unknown), p7 1, p8 2 (drive spans the quarter change),
    p11 3 and p12 4 (p9 is coverage and p10 has no censor time: neither is a
    table row, so neither increments the count).
    """
    rows = [
        # p1: pressure; rusher 1 and (single-snap) rusher 2.
        _off(1, pressure="TRUE"),
        _dl(1, 1, ttp=2.0),
        _dl(1, 2, lined_up="INTERIOR_LINE"),
        # p2: same drive, no pressure.
        _off(2, clock="09:40"),
        _dl(2, 1, clock="09:40"),
        # p3: possession change -> new drive.
        _off(3, clock="09:20", poss="AAA"),
        _dl(3, 1, clock="09:20", poss="AAA"),
        # p4: same possession, score changes -> new drive.
        _off(4, clock="09:00", poss="AAA", home=7),
        _dl(4, 1, clock="09:00", poss="AAA", home=7),
        # p5: possession unknown on every row -> drive NA.
        _off(5, clock="08:40", poss="NA"),
        _dl(5, 1, clock="08:40", poss="NA"),
        # p6: the play after an unknown possession starts a new drive.
        _off(6, clock="08:20", home=7),
        _dl(6, 1, clock="08:20", home=7),
        # p7 -> p8: same drive across the quarter change.
        _off(7, clock="08:00", home=7),
        _dl(7, 1, clock="08:00", home=7),
        _off(8, clock="15:00", quarter=2, home=7),
        _dl(8, 1, clock="15:00", quarter=2, home=7),
        # p9: dropped into coverage -> excluded and counted.
        _off(9, clock="14:40", quarter=2, home=7),
        _dl(9, 1, clock="14:40", quarter=2, home=7, blitzing="FALSE"),
        # p10: no dropback duration -> null censor, excluded and counted.
        _off(10, clock="14:20", quarter=2, home=7, dropback="NA"),
        _dl(10, 1, clock="14:20", quarter=2, home=7),
        # p11: pressure after the ball is out -> kept, event demoted to 0.
        _off(11, clock="14:00", quarter=2, home=7, dropback=2.5),
        _dl(11, 1, clock="14:00", quarter=2, home=7, ttp=5.0),
        # p12: alignment outside the mapping -> NA, counted.
        _off(12, clock="13:40", quarter=2, home=7),
        _dl(12, 1, clock="13:40", quarter=2, home=7, lined_up="MLB"),
        # p13: conflicting possession rows -> unknown, counted.
        _off(13, clock="13:20", quarter=2, home=7, poss="AAA"),
        _dl(13, 1, clock="13:20", quarter=2, home=7, poss="BBB"),
        # game 200: drive numbering starts at 0 in every game.
        _off(1, game_id=200, clock="10:00", poss="DDD"),
        _dl(1, 1, game_id=200, clock="10:00", poss="DDD"),
        _off(2, game_id=200, clock="09:40", poss="DDD"),
        _dl(2, 1, game_id=200, clock="09:40", poss="DDD"),
    ]
    return rows


def _make_cfg(tmp_path) -> dict:
    cfg = copy.deepcopy(D.load_config())
    block = cfg["bdb27_dl"]
    block["paths"].update({
        "raw": str(tmp_path / "raw"),
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
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (tmp_path / "notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "notes" / "prereg.md").write_text("# prereg\n\n**FROZEN**\n",
                                                  encoding="utf-8")
    return cfg


def _write_world(cfg: dict, rows, tmp_path):
    raw = tmp_path / "raw"
    pd.DataFrame([
        {"nfl_id": 1, "nfl_position": "DE"},
        {"nfl_id": 2, "nfl_position": "DT"},
        {"nfl_id": 10, "nfl_position": "G"},
    ]).to_csv(raw / "players.csv", index=False)
    pd.DataFrame([
        {"game_id": 100, "game_key": 1, "season": 2023, "season_type": "REG",
         "week": 1, "home_team_abbr": "AAA", "visitor_team_abbr": "BBB"},
        {"game_id": 200, "game_key": 2, "season": 2023, "season_type": "REG",
         "week": 1, "home_team_abbr": "CCC", "visitor_team_abbr": "DDD"},
    ]).to_csv(raw / "games.csv", index=False)
    frame = pd.DataFrame(rows, columns=PP_COLUMNS).astype(object)
    frame.to_csv(raw / "player_play.csv", index=False, na_rep="NA")


@pytest.fixture
def world(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_world(cfg, _rows(), tmp_path)
    return cfg


def _run(cfg, mode="sample", **kw):
    return stage03.run(cfg, mode, force=True, **kw)


def _summary(cfg):
    path = stage03._out_dir(cfg, "sample") / "snaps_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _snaps(cfg, mode="sample"):
    path = stage03._out_dir(cfg, mode) / "snaps.csv"
    return pd.read_csv(path, keep_default_na=True)


# --------------------------------------------------------------------------- #
# Schema / dtypes / column order
# --------------------------------------------------------------------------- #
def test_snap_csv_schema_and_column_order(world):
    _run(world)
    path = stage03._out_dir(world, "sample") / "snaps.csv"
    header = pd.read_csv(path, nrows=0).columns.tolist()
    assert header == SNAP_FIELDS
    # the declared order is exactly SNAP_COLUMNS after the stamped key
    assert header[1:] == list(S.SNAP_COLUMNS)
    snaps = pd.read_csv(path)
    assert len(snaps) == len(S.build_snaps(world, "sample"))
    assert not snaps[["game_id", "play_id", "nfl_id"]].duplicated().any()


def test_frame_dtypes_are_the_declared_ones(world):
    frame = S.build_snaps(world, "sample")
    for col in ("game_id", "play_id", "nfl_id", "season", "week", "event",
                "down", "yards_to_go", "quarter"):
        assert str(frame[col].dtype) == "Int64", col
    for col in S.LOAD_COLUMNS + ("time_to_pressure", "censor_time",
                                 "game_clock_s", "score_margin",
                                 "pre_snap_depth_yd", "in_game_speed_1_0",
                                 "in_game_speed_2_0"):
        assert str(frame[col].dtype) == "float64", col
    assert str(frame["garbage_time"].dtype) == "boolean"
    assert frame["alignment"].dropna().isin(["edge", "interior"]).all()


def test_csv_has_no_stringified_nulls_and_no_zero_fill(world):
    _run(world)
    raw = (stage03._out_dir(world, "sample") / "snaps.csv").read_text(
        encoding="utf-8")
    assert "nan" not in raw.lower().splitlines()[1:]
    snaps = _snaps(world)
    for col in ("blitz", "n_rushers", "n_blockers", "stunt",
                "absolute_load", "relative_load"):
        assert snaps[col].isna().all(), col
        assert not (snaps[col] == 0).any(), col


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_determinism_is_byte_identical(world):
    _run(world)
    out = stage03._out_dir(world, "sample")
    first = (out / "snaps.csv").read_bytes(), (out / "snaps_summary.json").read_bytes()
    _run(world)
    second = (out / "snaps.csv").read_bytes(), (out / "snaps_summary.json").read_bytes()
    assert first == second


# --------------------------------------------------------------------------- #
# The drive key (D72)
# --------------------------------------------------------------------------- #
def test_drive_boundaries_possession_score_unknown_and_quarter(world):
    _run(world)
    snaps = _snaps(world)
    got = snaps[(snaps["game_id"] == 100) & (snaps["nfl_id"] == 1)] \
        .sort_values("play_id")
    assert list(got["play_id"]) == [1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13]
    expected = [
        0.0, 1.0,          # p1-p2: first drive
        0.0,               # p3: possession change
        0.0,               # p4: score change, same possession
        float("nan"),      # p5: unknown possession => NA, never guessed
        0.0, 1.0, 2.0,     # p6-p8: after unknown, drive crosses the quarter
        3.0, 4.0,          # p11-p12: same drive (p9/p10 are not table rows)
        float("nan"),      # p13: conflicting possession => NA
    ]
    got_values = list(got["snaps_in_drive"])
    assert got_values[:4] == expected[:4]
    assert pd.isna(got_values[4])
    assert got_values[5:10] == expected[5:10]
    assert pd.isna(got_values[10])


def test_first_play_and_single_snap_player_start_at_zero(world):
    _run(world)
    snaps = _snaps(world)
    # rusher 2 has exactly one snap: it is the first (and only) snap of its
    # drive sequence, so the count is 0 — not NA, not 1.
    only = snaps[snaps["nfl_id"] == 2]
    assert len(only) == 1
    assert only.iloc[0]["snaps_in_drive"] == 0.0
    # a new game restarts the numbering
    game_two = snaps[snaps["game_id"] == 200].sort_values("play_id")
    assert list(game_two["snaps_in_drive"]) == [0.0, 1.0]


def test_summary_reports_the_drive_rule_and_its_gaps(world):
    _run(world)
    drive = _summary(world)["drive"]
    assert drive["rule"] == S.DRIVE_RULE
    assert drive["n_games_in_list"] == 2
    # p5 has no possession and p13 has conflicting ones: both counted, never
    # assigned a drive.
    assert drive["n_plays_missing_possession"] == 1
    assert drive["n_plays_conflicting_possession"] == 1
    assert drive["boundaries"]["first_play"] == 2
    # p3 is the only clean possession change; p13 is *conflicting*, so it is
    # counted as an unknown possession, not as a change.
    assert drive["boundaries"]["possession_change"] == 1
    assert drive["boundaries"]["score_change_same_possession"] == 1
    assert drive["boundaries"]["unknown_possession"] == 2
    # only p6 follows an unknown-possession play (p13 is the game's last play)
    assert drive["boundaries"]["after_unknown_possession"] == 1
    assert drive["n_clock_order_inversions"] == 0
    assert drive["n_drives"] > 0


# --------------------------------------------------------------------------- #
# Null semantics / exclusions
# --------------------------------------------------------------------------- #
def test_time_to_pressure_after_censor_is_kept_as_event_zero(world):
    _run(world)
    snaps = _snaps(world)
    row = snaps[(snaps["game_id"] == 100) & (snaps["play_id"] == 11)].iloc[0]
    assert row["event"] == 0
    assert row["time_to_pressure"] == 5.0
    assert row["censor_time"] == 2.5
    assert _summary(world)["counts"]["n_ttp_gt_censor_event_set_to_0"] == 1


def test_exclusion_counts_are_all_present(world):
    _run(world)
    excl = _summary(world)["exclusions"]
    assert excl["coverage_rows_excluded"]["count"] == 1
    assert excl["null_censor_time_excluded"]["count"] == 1
    assert excl["ttp_after_censor_demoted"]["count"] == 1
    assert excl["unmapped_alignment"]["count"] == 1
    assert excl["missing_drive_key"]["count"] == 2
    assert excl["snaps_in_drive_unavailable"]["count"] == 2
    for block in excl.values():
        assert block["disposition"]


def test_unmapped_alignment_stays_na(world):
    _run(world)
    snaps = _snaps(world)
    row = snaps[(snaps["game_id"] == 100) & (snaps["play_id"] == 12)].iloc[0]
    assert pd.isna(row["alignment"])


def test_load_columns_are_na_until_a_threshold_is_frozen(world):
    _run(world)
    na_rate = _summary(world)["na_rate"]
    assert na_rate["absolute_load"] == 1.0
    assert na_rate["relative_load"] == 1.0
    # the sequence columns are populated by the load hook
    assert na_rate["snaps_in_drive"] < 1.0
    assert na_rate["seconds_since_last_snap"] < 1.0


# --------------------------------------------------------------------------- #
# Provenance gate
# --------------------------------------------------------------------------- #
def test_sample_mode_is_stamped_and_not_reportable(world):
    _run(world)
    payload = _summary(world)
    assert payload["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert payload["reportable"] is False
    assert payload["thresholds"]["provenance"] == "missing"


def test_sample_mode_refuses_full_config_provenance(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_world(cfg, _rows(), tmp_path)
    cfg["bdb27_dl"]["mode"]["sample"]["provenance"] = "FULL"
    with pytest.raises(RuntimeError, match="PLACEHOLDER_SAMPLE"):
        stage03.run(cfg, "sample", force=True)


def test_sample_mode_refuses_to_clobber_a_full_snap_table(world):
    out = stage03._out_dir(world, "sample")
    out.mkdir(parents=True, exist_ok=True)
    (out / "snaps_summary.json").write_text(
        json.dumps({"provenance": "FULL", "n_snaps": 1}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="FULL"):
        stage03.run(world, "sample", force=True)
    assert json.loads((out / "snaps_summary.json").read_text())["provenance"] == "FULL"


def test_full_mode_refuses_without_full_thresholds(world):
    with pytest.raises(RuntimeError, match="thresholds.json"):
        stage03.run(world, "full", force=True)

    path = D.resolve_paths(world, "full")["thresholds"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provenance": "PLACEHOLDER_SAMPLE: X",
                                "frozen": True}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not 'FULL'"):
        stage03.run(world, "full", force=True)


def test_full_mode_writes_reportable_table_with_full_thresholds(world):
    path = D.resolve_paths(world, "full")["thresholds"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provenance": "FULL", "frozen": False,
                                "chosen": {"absolute_speed_yds": None,
                                           "relative_fraction": None}}),
                    encoding="utf-8")
    _run(world, "full")
    payload = json.loads(
        (stage03._out_dir(world, "full") / "snaps_summary.json").read_text())
    assert payload["provenance"] == "FULL"
    assert payload["reportable"] is True
    assert payload["thresholds"]["provenance"] == "FULL"
    assert payload["n_snaps"] > 0


def test_full_mode_with_sample_config_provenance_raises(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_world(cfg, _rows(), tmp_path)
    cfg["bdb27_dl"]["mode"]["full"]["provenance"] = "PLACEHOLDER_SAMPLE"
    with pytest.raises(RuntimeError, match="provenance"):
        stage03.run(cfg, "full", force=True)


def test_full_mode_requires_a_frozen_prereg(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_world(cfg, _rows(), tmp_path)
    path = D.resolve_paths(cfg, "full")["thresholds"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provenance": "FULL", "frozen": False}),
                    encoding="utf-8")
    (tmp_path / "notes" / "prereg.md").write_text("# prereg\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="FROZEN"):
        stage03.run(cfg, "full", force=True)


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_empty_snap_table_is_written_with_zero_counts(tmp_path):
    cfg = _make_cfg(tmp_path)
    rows = [
        # a run play: no dropback duration anywhere, no rush assignment
        _row(play_id=1, nfl_id=10, dropback_duration="NA"),
        _row(play_id=1, nfl_id=1, blitzing="NA"),
    ]
    _write_world(cfg, rows, tmp_path)
    _run(cfg)
    out = stage03._out_dir(cfg, "sample")
    header = pd.read_csv(out / "snaps.csv", nrows=0).columns.tolist()
    assert header == SNAP_FIELDS
    payload = json.loads((out / "snaps_summary.json").read_text())
    assert payload["n_snaps"] == 0
    assert payload["n_players"] == 0
    assert payload["event_rate"] is None
    assert payload["counts"]["n_dl_rows"] == 1


def test_stage_is_checkpointed_and_force_rebuilds(world):
    first = _run(world)
    assert first["skipped"] is False
    assert stage03.run(world, "sample", force=False)["skipped"] is True
    assert stage03.run(world, "sample", force=True)["skipped"] is False
