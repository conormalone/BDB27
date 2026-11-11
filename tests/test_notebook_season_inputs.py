"""The notebook reads the shipped per-season tracking layout (D184 extension).

Conor's folder holds ``game_tracking_2023.csv``, ``game_tracking_2024.csv`` and
``game_tracking_2025.csv``. These tests lift the notebook's own resolver and
converter out of the committed notebook and exercise them on a temporary folder:

  * three per-season files are consumed exactly as named — no renaming, no
    concatenation;
  * a single concatenated ``game_tracking.csv`` is still accepted;
  * a folder that matches neither is refused, and the error names every file
    that was looked for plus every ``game_tracking*.csv`` it did find;
  * conversion tags each Parquet with the season in the filename.
"""

from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_full_game_turns.ipynb"
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["turn_coverage"]

WANTED = ("tracking_inputs", "season_of_tracking", "convert", "sha256", "cached_sha256")


def _cfg_get(dotted):
    node = CONFIG
    for part in dotted.split("."):
        node = node[part]
    return node


class Cfg:
    """The tiny slice of ``common.Config`` the lifted functions use."""

    def get(self, dotted, default=None):
        return _cfg_get(dotted)


def _notebook_functions(tmp_path):
    """Exec the named functions from the notebook's code cells, with their globals."""
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from bdb27 import common  # noqa: PLC0415 - deliberately late, repo-local

    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    wanted = set(WANTED)
    sources = []
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                sources.append(ast.get_source_segment(source, node))
    missing = wanted - {ast.parse(src).body[0].name for src in sources}
    assert not missing, f"the notebook no longer defines {sorted(missing)}"

    run = tmp_path / "bdb27_run"
    (run / "tmp").mkdir(parents=True)
    namespace = {
        "Path": Path, "re": re, "json": json, "time": time,
        "common": common, "cfg": Cfg(), "RUN": run, "pq": pq,
    }
    for source in sources:
        exec(compile(source, "<notebook>", "exec"), namespace)  # noqa: S102 - our notebook
    return namespace


def _write_tracking(path: Path, game_id: int) -> None:
    rows = ["game_id,play_id,nfl_id,time,x,y,o,dir"]
    for i in range(4):
        rows.append(f"{game_id},1,1001,{i * 100},{i * 0.5:.1f},0.0,90.0,0.0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _data_dir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return data


def test_season_comes_from_the_filename():
    # `season_of_tracking` is pure; lift it without a RUN directory.
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = next(
        "".join(cell["source"]) for cell in nb["cells"]
        if cell["cell_type"] == "code" and "def season_of_tracking" in "".join(cell["source"]))
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "season_of_tracking")
    namespace: dict = {"Path": Path, "re": re}
    exec(compile(ast.get_source_segment(source, node), "<season_of_tracking>", "exec"),
         namespace)  # noqa: S102
    season = namespace["season_of_tracking"]
    assert season("game_tracking_2023.csv") == 2023
    assert season("game_tracking_2023_sample.csv") == 2023
    assert season("game_tracking.csv") is None


def test_three_per_season_files_are_consumed_without_renaming(tmp_path):
    data = _data_dir(tmp_path)
    for year in (2023, 2024, 2025):
        _write_tracking(data / f"game_tracking_{year}.csv", 2023000000 + year)

    ns = _notebook_functions(tmp_path)
    found = ns["tracking_inputs"](data, "full")

    assert [p.name for p in found] == [
        "game_tracking_2023.csv", "game_tracking_2024.csv", "game_tracking_2025.csv"]
    assert all(p.exists() for p in found)


def test_conversion_tags_each_file_with_its_season(tmp_path):
    data = _data_dir(tmp_path)
    for year in (2023, 2024, 2025):
        _write_tracking(data / f"game_tracking_{year}.csv", 2023000000 + year)

    ns = _notebook_functions(tmp_path)
    columns = ["game_id", "play_id", "nfl_id", "time", "x", "y", "o", "dir"]
    for csv_path in ns["tracking_inputs"](data, "full"):
        season = ns["season_of_tracking"](csv_path)
        out = ns["RUN"] / f"{csv_path.stem}.parquet"
        ns["convert"](csv_path, columns, out, "game_id, play_id, nfl_id, time",
                      season=season)
        frame = pq.read_table(out).to_pandas()
        assert frame["season"].nunique() == 1
        assert int(frame["season"].iloc[0]) == season
        assert len(frame) == 4


def test_a_single_concatenated_file_is_still_accepted(tmp_path):
    data = _data_dir(tmp_path)
    _write_tracking(data / "game_tracking.csv", 2023090700)

    ns = _notebook_functions(tmp_path)
    found = ns["tracking_inputs"](data, "full")

    assert [p.name for p in found] == ["game_tracking.csv"]


def test_an_unexpected_layout_names_what_was_looked_for(tmp_path):
    data = _data_dir(tmp_path)
    _write_tracking(data / "game_tracking_23.csv", 2023090700)

    ns = _notebook_functions(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        ns["tracking_inputs"](data, "full")
    message = str(exc.value)

    for name in ("game_tracking_2023.csv", "game_tracking_2024.csv",
                 "game_tracking_2025.csv", "game_tracking.csv"):
        assert name in message, f"{name} was not named"
    assert "game_tracking_23.csv" in message, "the lookalike file was not listed"
    assert "nothing was renamed" in message.lower()
    assert "will use     : nothing" in message
    assert "sample is NOT substituted" in message


def test_a_single_file_without_a_season_gets_a_null_season_column(tmp_path):
    """The single-file layout has no season in its name; the column must still exist."""
    data = _data_dir(tmp_path)
    _write_tracking(data / "game_tracking.csv", 2023090700)
    ns = _notebook_functions(tmp_path)

    csv_path = data / "game_tracking.csv"
    out = ns["RUN"] / "game_tracking.parquet"
    assert ns["season_of_tracking"](csv_path) is None
    ns["convert"](csv_path, ["game_id", "play_id", "nfl_id", "time", "x", "y", "o", "dir"],
                  out, "game_id, play_id, nfl_id, time", with_season=True)

    frame = pq.read_table(out).to_pandas()
    assert "season" in frame.columns
    assert frame["season"].isna().all()
    assert len(frame) == 4
