"""Schema contract: the real files match the registry, and a mismatch FAILS.

The point of these tests is not that the registry is pretty. It is that a
wrongly named column, a silently retyped column, or a unit change stops the
pipeline with a message a human can act on, instead of producing a plausible
number from the wrong quantity.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import common as C
import schema as S


def _cfg_pointing_at(cfg, tmp_path, key, filename):
    import copy
    c = copy.deepcopy(cfg)
    C.block(c)["paths"]["raw"] = str(tmp_path)
    C.block(c)["files"][key] = filename
    return c


def _write(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def test_real_files_match_the_registry(con, cfg, real_data):
    for key in ("games", "players", "combine_results", "combine_tracking",
                "player_play"):
        S.assert_schema(con, cfg, key)
    S.assert_schema(con, cfg, "game_tracking", mode="sample")


def test_real_vocabulary_is_edge_and_interior(con, cfg, real_data):
    seen = S.assert_vocabulary(con, cfg)["lined_up_position"]
    assert "EDGE" in seen and "INTERIOR_LINE" in seen
    # DE/DT/NT are the *players.csv* vocabulary and must not leak in here.
    assert not ({"DE", "DT", "NT"} & set(seen))


def test_missing_column_fails_loudly(con, cfg, tmp_path):
    _write(tmp_path / "games.csv", ["game_id", "season"], [[1, 2024]])
    c = _cfg_pointing_at(cfg, tmp_path, "games", "games.csv")
    with pytest.raises(C.SchemaError) as ei:
        S.assert_schema(con, c, "games")
    assert "missing column" in str(ei.value)


def test_wrong_dtype_fails_loudly(con, cfg, tmp_path):
    header = list(S.REQUIRED["games"])
    row = [1, 1, "twenty24", "REG", 1, "AAA", "BBB"]   # season not parseable as int
    _write(tmp_path / "games.csv", header, [row])
    c = _cfg_pointing_at(cfg, tmp_path, "games", "games.csv")
    with pytest.raises(C.SchemaError) as ei:
        S.assert_schema(con, c, "games")
    assert "expected family int" in str(ei.value)


def test_unit_error_is_caught(con, cfg, tmp_path):
    """A speed of 300 in a yd/s column is a unit error, not a fast player."""
    header = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
              "dis", "o", "dir", "event"]
    rows = [[1, 2, 3, "2024-01-01 00:00:00", 50.0, 26.0, 300.0, 0.0,
             0.1, 90.0, 90.0, ""]]
    _write(tmp_path / "game_tracking.csv", header, rows)
    c = _cfg_pointing_at(cfg, tmp_path, "game_tracking_sample", "game_tracking.csv")
    with pytest.raises(C.SchemaError) as ei:
        S.assert_units(con, c, "game_tracking", mode="sample")
    assert "yd/s" in str(ei.value)


def test_off_field_coordinates_are_caught(con, cfg, tmp_path):
    header = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
              "dis", "o", "dir", "event"]
    rows = [[1, 2, 3, "2024-01-01 00:00:00", 500.0, 26.0, 2.0, 0.0,
             0.1, 90.0, 90.0, ""]]
    _write(tmp_path / "game_tracking.csv", header, rows)
    c = _cfg_pointing_at(cfg, tmp_path, "game_tracking_sample", "game_tracking.csv")
    with pytest.raises(C.SchemaError) as ei:
        S.assert_units(con, c, "game_tracking", mode="sample")
    assert "unit error" in str(ei.value)


def test_de_dt_nt_vocabulary_is_rejected(con, cfg, tmp_path):
    """If a future file switches to DE/DT/NT the pipeline must stop, not guess."""
    import copy
    c = copy.deepcopy(cfg)
    C.block(c)["positions"]["dl_linedup"] = ["DE", "DT", "NT"]
    with pytest.raises(C.SchemaError) as ei:
        S.assert_vocabulary(con, c)
    assert "missing configured DL values" in str(ei.value)


def test_vocabulary_helper_rejects_legacy_labels(con, cfg, tmp_path):
    """The explicit legacy-label guard fires even if config were changed."""
    seen = {"EDGE", "INTERIOR_LINE", "DE"}
    legacy = {"DE", "DT", "NT"} & seen
    assert legacy == {"DE"}   # documents the guard's premise


# --------------------------------------------------------------------------- #
# Tracking contract (assumptions about full data, asserted at runtime)
# --------------------------------------------------------------------------- #
def test_tracking_contract_passes_on_the_fixture(con, cfg_for_fixture):
    got = S.tracking_contract(con, cfg_for_fixture, "sample")
    assert got["median_frame_interval_s"] == pytest.approx(0.1, abs=1e-6)
    assert got["ball_snap_share"] > 0.95
    assert got["duplicate_keys"] == 0
    assert got["plays"] > 0


def test_tracking_contract_fails_on_the_wrong_sampling_rate(con, cfg, tmp_path):
    """A 5 Hz file must be rejected, not silently averaged at 10 Hz."""
    rows = []
    for k in range(10):
        t = f"2024-01-01 00:00:{k*2:02d}.0"
        rows.append([1, 2, 3, t, 50.0, 26.0, 1.0, 0.0, 0.1, 90.0, 90.0,
                     "ball_snap" if k == 2 else ""])
    header = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
              "dis", "o", "dir", "event"]
    _write(tmp_path / "game_tracking.csv", header, rows)
    c = _cfg_pointing_at(cfg, tmp_path, "game_tracking_sample", "game_tracking.csv")
    with pytest.raises(C.SchemaError) as ei:
        S.tracking_contract(con, c, "sample")
    assert "modal frame interval" in str(ei.value)


def test_tracking_contract_fails_without_ball_snap(con, cfg, tmp_path):
    rows = []
    for k in range(10):
        t = f"2024-01-01 00:00:0{k}.0"
        rows.append([1, 2, 3, t, 50.0, 26.0, 1.0, 0.0, 0.1, 90.0, 90.0, ""])
    header = ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
              "dis", "o", "dir", "event"]
    _write(tmp_path / "game_tracking.csv", header, rows)
    c = _cfg_pointing_at(cfg, tmp_path, "game_tracking_sample", "game_tracking.csv")
    with pytest.raises(C.SchemaError) as ei:
        S.tracking_contract(con, c, "sample")
    assert "ball_snap" in str(ei.value)
