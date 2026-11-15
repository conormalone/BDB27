"""Path resolution in ``notebooks/bdb27_geometric_attribution.ipynb`` (D201).

The notebook is Run-All on Conor's Windows laptop, where the full turns parquet
sits in a folder he chose, not in the repo. ``TURNS = None`` must therefore
*search* rather than assume ``data/interim/full_run_20261111`` (which only exists
on the Linux box, as a symlink to the USB drive).

These tests are static/exec-in-memory: the search helper cell is lifted out of the
.ipynb, given a temp repo and temp home, and exercised directly. The truncation
stop is the module's own ``preflight_verdict`` — unchanged.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_geometric_attribution.ipynb"

TURNS_PATTERNS = ("bdb27_full_db_turns.parquet", "bdb27_full_game_turns*.parquet",
                  "*full_db_turns*.parquet")
TURNS_HINT = ("set TURNS in the config cell to the full path of "
              "bdb27_full_db_turns.parquet, e.g. "
              'TURNS = r"C:\\Users\\<you>\\Downloads\\bdb27_full_db_turns.parquet"')


def notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_cells() -> list[str]:
    return ["".join(c["source"]) for c in notebook()["cells"] if c["cell_type"] == "code"]


def cell_with(marker: str) -> str:
    for src in code_cells():
        if marker in src:
            return src
    raise AssertionError(f"no code cell contains {marker!r}")


def work(tmp_path: Path) -> Path:
    """An empty working directory: the real cwd must never leak into a test."""
    path = tmp_path / "work"
    path.mkdir(exist_ok=True)
    return path


def roots_for(ns, repo: Path, tmp_path: Path):
    return ns["search_roots"](repo, tmp_path / "home", work(tmp_path))


def helper(repo: Path) -> dict:
    """Exec the search-helper cell with ``REPO`` bound to a temp repo."""
    src = cell_with("def find_candidates(")
    ns: dict = {"REPO": repo, "Path": Path}
    exec(compile(src, "<paths cell>", "exec"), ns)  # noqa: S102 - our own notebook
    return ns


def quiet(fn, *args, **kwargs):
    out = io.StringIO()
    with redirect_stdout(out):
        value = fn(*args, **kwargs)
    return value, out.getvalue()


def turns_file(path: Path, size: int, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    os.utime(path, (mtime, mtime))
    return path


# --------------------------------------------------------------- 1. single hit

def test_one_candidate_is_found_and_used(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    want = turns_file(repo / "data" / "bdb27_full_db_turns.parquet", 4096,
                      1_700_000_000)

    ns = helper(repo)
    roots = roots_for(ns, repo, tmp_path)
    got, text = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS, roots, TURNS_HINT)

    assert got == want
    assert "found exactly one" in text
    assert str(want) in text
    assert "4.0 KB" in text, text
    assert "modified 2023-11-14" in text, text


def test_a_full_run_folder_under_home_is_searched(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    want = turns_file(home / "Downloads" / "bdb27_interim"
                      / "full_run_20260930" / "bdb27_full_db_turns.parquet",
                      1024, 1_700_000_100)

    ns = helper(repo)
    roots = ns["search_roots"](repo, home, work(tmp_path))
    got, text = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS, roots, TURNS_HINT)

    assert got == want
    assert str(want) in text


def test_a_symlinked_data_folder_is_followed(tmp_path):
    """On this machine ``data/interim`` is a symlink; searching must follow it."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    outside = tmp_path / "usb" / "bdb27_interim" / "full_run_20261111"
    want = turns_file(outside / "bdb27_full_db_turns.parquet", 2048, 1_700_000_000)
    try:
        (repo / "data" / "interim").symlink_to(outside.parent.parent,
                                               target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unavailable on this host")

    ns = helper(repo)
    roots = roots_for(ns, repo, tmp_path)
    got, _ = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS, roots, TURNS_HINT)
    assert got is not None and got.resolve() == want.resolve()


# -------------------------------------------------- 2. several: newest is used

def test_several_candidates_pick_the_newest_and_print_all(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    old = turns_file(repo / "data" / "old" / "bdb27_full_db_turns.parquet",
                     1024, 1_690_000_000)
    new = turns_file(repo / "data" / "bdb27_full_db_turns.parquet", 2048,
                     1_710_000_000)
    alt = turns_file(repo / "data" / "bdb27_full_game_turns_2025.parquet", 512,
                     1_700_000_000)

    ns = helper(repo)
    roots = roots_for(ns, repo, tmp_path)
    got, text = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS, roots, TURNS_HINT)

    assert got == new, "the newest candidate must be chosen"
    for path in (old, new, alt):
        assert str(path) in text, f"{path} was not printed"
    assert "3 TURNS candidates found" in text
    assert "using the newest" in text
    first = text.index("USING")
    assert first < text.index(str(new)) < text.index(str(old)), (
        "the chosen file must be printed first, then the others")
    # deterministic: the same inputs give the same choice
    again, _ = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS, roots, TURNS_HINT)
    assert again == got


def test_a_same_mtime_tie_breaks_on_path_not_on_scan_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    a = turns_file(repo / "data" / "a" / "bdb27_full_db_turns.parquet", 10, 1_700_000_000)
    b = turns_file(repo / "data" / "b" / "bdb27_full_db_turns.parquet", 10, 1_700_000_000)
    ns = helper(repo)
    got, _ = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS,
                   roots_for(ns, repo, tmp_path), TURNS_HINT)
    assert got == min(a, b, key=str)


# ------------------------------------------------------- 3. nothing found stops

def test_test_fixtures_and_sample_copies_are_never_picked(tmp_path):
    """``fixtures/`` is committed, so a fresh checkout has a fake turns file."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    real = turns_file(repo / "data" / "bdb27_full_db_turns.parquet", 2048,
                      1_700_000_000)
    turns_file(repo / "fixtures" / "synthetic" / "bdb27_full_db_turns.parquet",
               16, 1_800_000_000)
    turns_file(repo / "data" / "bdb27_full_db_turns_sample.parquet", 16,
               1_800_000_000)

    ns = helper(repo)
    got, text = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS,
                      roots_for(ns, repo, tmp_path), TURNS_HINT)

    assert got == real
    assert "found exactly one" in text
    assert "synthetic" not in text and "_sample.parquet" not in text


def test_a_repo_copy_is_used_ahead_of_the_search(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data" / "raw").mkdir(parents=True)
    canonical = turns_file(repo / "data" / "raw" / "player_play.csv", 128,
                           1_600_000_000)
    turns_file(repo / "Downloads" / "player_play.csv", 128, 1_800_000_000)

    ns = helper(repo)
    got, text = quiet(ns["locate_input"], "PLAYER_PLAY", ("player_play.csv",),
                      roots_for(ns, repo, tmp_path), "hint",
                      preferred=[canonical])

    assert got == canonical
    assert "using the repo copy" in text


def test_tracking_missing_message_names_every_file_it_looked_for(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data" / "raw").mkdir(parents=True)
    ns = helper(repo)
    looked_for = ["game_tracking.csv", "game_tracking_2023.csv",
                  "game_tracking_2024.csv", "game_tracking_2025.csv"]
    message = ns["missing_message"]("TRACKING", tuple(looked_for),
                                    [str(repo / "data" / "raw")], "set TRACKING")
    for name in looked_for:
        assert name in message
    assert str(repo / "data" / "raw") in message
    assert "set TRACKING" in message

def test_nothing_found_prints_the_directories_the_patterns_and_the_exact_hint(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)

    ns = helper(repo)
    roots = ns["search_roots"](repo, home, work(tmp_path))
    got, text = quiet(ns["locate_input"], "TURNS", TURNS_PATTERNS, roots, TURNS_HINT)

    assert got is None
    assert "STOP — no TURNS file was found." in text
    for pattern in TURNS_PATTERNS:
        assert pattern in text
    # every directory searched is named
    for searched in (repo, repo / "data", home / "Downloads"):
        assert str(searched) in text, f"{searched} was not named"
    assert TURNS_HINT in text
    assert ('set TURNS in the config cell to the full path of '
            'bdb27_full_db_turns.parquet') in text
    assert ('TURNS = r"C:\\Users\\<you>\\Downloads\\'
            'bdb27_full_db_turns.parquet"') in text


def test_nothing_found_for_player_play_names_its_own_hint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hint = ('set PLAYER_PLAY in the config cell to the full path of player_play.csv, '
            'e.g. PLAYER_PLAY = r"C:\\Users\\<you>\\Downloads\\player_play.csv"')
    ns = helper(repo)
    got, text = quiet(ns["locate_input"], "PLAYER_PLAY", ("player_play.csv",),
                      roots_for(ns, repo, tmp_path), hint)
    assert got is None
    assert "no PLAYER_PLAY file was found" in text
    assert "player_play.csv" in text and hint in text


# ------------------------------------------- 4. relative paths resolve on repo

def test_relative_paths_resolve_against_the_repo_and_absolute_paths_do_not(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    ns = helper(repo)
    resolve = ns["resolve_path"]

    rel = resolve("data/interim/full_run_20261111/bdb27_full_db_turns.parquet")
    assert rel == (repo / "data" / "interim" / "full_run_20261111"
                   / "bdb27_full_db_turns.parquet").resolve()
    assert rel.is_absolute()

    absolute = tmp_path / "elsewhere" / "bdb27_full_db_turns.parquet"
    assert resolve(absolute) == absolute.resolve()
    assert resolve(str(absolute)) == absolute.resolve()

    assert resolve("~/somewhere/x.parquet") == (Path.home() / "somewhere"
                                                / "x.parquet").resolve()


# ------------------------------------ 5. sample / truncation still stops loudly

def test_a_truncated_or_sample_file_still_trips_the_preflight_stop():
    from bdb27.geometric_match import (MIN_FULL_PLAYERS, TRUNCATION_ROWS,
                                       preflight_verdict)

    truncated = preflight_verdict({"path": "game_tracking.csv",
                                   "rows": TRUNCATION_ROWS, "players": 5000,
                                   "plays": 900, "median_players_on_play": 22.0})
    assert truncated["ok"] is False
    assert any("1,048,575-row (2^20-1) truncation" in s for s in truncated["stops"])

    sample = preflight_verdict({"path": "game_tracking_sample.csv", "rows": 1000,
                                "players": 4, "plays": 250,
                                "median_players_on_play": 4.0})
    assert sample["ok"] is False
    assert str(MIN_FULL_PLAYERS) in " ".join(sample["stops"]) or "300" in " ".join(
        sample["stops"])
    assert any("truncation" in s for s in sample["stops"])


def test_the_notebook_keeps_the_preflight_stop_and_the_bypass_flag():
    src = cell_with("def find_candidates(") + cell_with("preflight_verdict(")
    inputs = cell_with("preflight_verdict(")
    assert "ALLOW_SAMPLE_TRACKING" in inputs
    assert 'raise SystemExit("pre-flight stop")' in inputs
    assert "preflight_verdict(c, band_plays=len(band_plays))" in inputs
    assert src.count("is_dir()") >= 1
    # the search is depth-bounded and refuses to walk the whole disk
    helper_src = cell_with("def find_candidates(")
    assert "SEARCH_DEPTH" in helper_src and "SEARCH_MAX_DIRS" in helper_src
    assert "SKIP_DIRS" in helper_src
