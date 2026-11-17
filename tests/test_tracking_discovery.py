"""TRACKING = [] must find the file — and say so when it cannot (Conor, 2026-11-17).

The defect this pins: ``TRACKING = []`` used to mean "four hard-coded names
under data/raw", while the config cell's comment claimed it globbed
``game_tracking*.csv``. A file named anything else — a suffix, a sub-folder, a
per-season export — was invisible, and a run with no tracking looked exactly
like a run with tracking that measured nothing: one ``paths tried`` line among
many, and empty tables that read as results. Conor's laptop run produced the
no-tracking artefacts without ever being told.

What is pinned here:

* discovery finds a differently-named file, case-insensitively, and still
  refuses ``sample`` and ``combine``;
* when discovery finds nothing it lists the directory's actual contents;
* both banner branches are emitted, with the text a reader needs;
* the no-tracking path completes — a missing file is printed, never raised.

Nothing here gates: these are messages, not thresholds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import ambiguity_attribution as aa  # noqa: E402

TRACKING_COLUMNS = ("game_id", "play_id", "nfl_id", "time", "x", "y")


def write_csv(path: Path, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "game_id": [1] * rows, "play_id": [1] * rows,
        "nfl_id": list(range(1, rows + 1)),
        "time": [0.1 * i for i in range(rows)],
        "x": [1.0] * rows, "y": [2.0] * rows,
    }).to_csv(path, index=False, encoding="utf-8")
    return path


# ------------------------------------------------------------------ discovery
def test_discovery_finds_a_differently_named_file(tmp_path):
    """The whole point: not ``game_tracking_2023.csv`` and still found."""
    wanted = write_csv(tmp_path / "Season_2024_TRACKING_export.CSV")
    write_csv(tmp_path / "player_play.csv")
    write_csv(tmp_path / "game_tracking_2023_sample.csv")
    write_csv(tmp_path / "combine_tracking.csv")

    found = aa.discover_tracking(roots=[tmp_path])
    assert [p.name for p in found["files"]] == [wanted.name]


def test_discovery_searches_a_sub_folder(tmp_path):
    wanted = write_csv(tmp_path / "per_season" / "tracking_2025_part1.csv")
    found = aa.discover_tracking(roots=[tmp_path])
    assert wanted in found["files"]


def test_discovery_never_takes_a_sample_or_the_combine_table(tmp_path):
    write_csv(tmp_path / "game_tracking_2023_sample.csv")
    write_csv(tmp_path / "combine_tracking.csv")
    assert aa.discover_tracking(roots=[tmp_path])["files"] == []


def test_an_explicit_file_is_used_as_given_whatever_its_name(tmp_path):
    """Pointing at a file deliberately still works, including the sample."""
    sample = write_csv(tmp_path / "my_export_0001.csv")
    assert aa.discover_tracking([sample])["files"] == [sample]


def test_the_default_search_never_promotes_a_sample():
    """Whatever this machine holds, a sample is never taken as the tracking."""
    assert all("sample" not in p.name.lower()
               for p in aa._tracking_candidates(None))


# ------------------------------------------------------------------ the banners
def test_the_none_found_banner_lists_the_directory_contents(tmp_path):
    write_csv(tmp_path / "player_play.csv")
    write_csv(tmp_path / "game_tracking_2023_sample.csv")
    (tmp_path / "sub").mkdir()

    info = aa.tracking_status(roots=[tmp_path])
    assert info["status"] == "NONE FOUND" and info["files"] == []
    assert info["roots"][0]["entries"]                 # it recorded what was there

    text = aa.tracking_banner(info)
    assert "TRACKING: NONE FOUND" in text
    assert str(tmp_path) in text                       # names the directory
    assert "player_play.csv" in text                   # lists the contents
    assert "game_tracking_2023_sample.csv" in text     # the near miss is visible
    assert "sub/" in text
    assert "TO EVALUATE GEOMETRY" in text              # says what to do about it
    assert "TRACKING = [" in text                      # names the config cell
    assert "NOT EVALUABLE" in text and "adds 0 to N" in text
    assert "nothing here is an error" in text          # it is not a crash


def test_the_found_banner_names_files_sizes_rows_and_seasons(tmp_path):
    one = write_csv(tmp_path / "tracking_2023_export.csv", rows=4)
    two = write_csv(tmp_path / "game_tracking_2024.csv", rows=6)

    info = aa.tracking_status(roots=[tmp_path])
    assert info["status"] == "FOUND" and len(info["files"]) == 2
    assert info["rows"] == 10
    assert info["seasons"] == ["2023", "2024"]

    text = aa.tracking_banner(info)
    assert "TRACKING: FOUND" in text
    assert one.resolve().as_posix() in text            # absolute path
    assert two.resolve().as_posix() in text
    assert "10" in text and "rows read" in text
    assert "2023, 2024" in text
    assert "bytes" in text or "MB" in text or "GB" in text


def test_a_missing_explicit_path_is_named_and_does_not_raise(tmp_path):
    info = aa.tracking_status([tmp_path / "not_here.csv"])
    assert info["status"] == "NONE FOUND"
    text = aa.tracking_banner(info)
    assert "not_here.csv" in text and "NOT FOUND" in text


def test_an_unreadable_found_file_is_reported_not_raised(tmp_path):
    broken = tmp_path / "tracking_2023_broken.csv"
    broken.write_text("this is not a csv\n", encoding="utf-8")
    info = aa.tracking_status(roots=[tmp_path])
    assert info["status"] == "FOUND"
    assert info["read_error"]                          # reported in the banner
    text = aa.tracking_banner(info)
    assert "UNREADABLE" in text


def test_search_summary_names_every_directory_and_its_contents(tmp_path):
    write_csv(tmp_path / "player_play.csv")
    absent = tmp_path / "absent"
    summary = aa.search_summary([tmp_path, absent])
    assert str(tmp_path) in summary and "player_play.csv" in summary
    assert str(absent) in summary and "NOT FOUND" in summary


# ------------------------------------------------------------------ no-tracking path
def test_no_tracking_path_completes_and_says_where_it_looked():
    """The geometry helpers return, and their reason is a place, not a blank."""
    assert isinstance(aa._tracking_candidates(None), list)
    info = aa.tracking_status(roots=[ROOT / "data" / "raw"])
    assert isinstance(aa.tracking_banner(info), str)
    reason = aa._paths_tried([], None)
    assert reason and "data/raw" in reason            # it named the directory


def test_the_not_evaluable_text_no_longer_blames_the_machine():
    """It is a statement about the run, true on the laptop and here alike."""
    assert "not a distance between two players" in aa.GEOMETRY_NOT_EVALUABLE
    assert "this machine" not in aa.GEOMETRY_NOT_EVALUABLE
