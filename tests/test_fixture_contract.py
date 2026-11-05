"""The committed synthetic fixture must match what the generator produces.

`fixtures/synthetic/demo/` is committed so that the repo has a runnable dataset
without the USB volume. If someone edits the generator, this test fails rather
than letting the fixture silently drift into an unreproducible artefact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import synthetic

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "demo"
FILES = ("game_tracking.csv", "player_play.csv", "games.csv",
         "combine_tracking.csv")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", FILES)
def test_committed_fixture_reproduces(name, tmp_path):
    synthetic.build_fixture(tmp_path, seed=20270106, n_players=8, snaps_per=25,
                            jump_sd=0.08, burst_sd=0.10)
    assert _sha(tmp_path / name) == _sha(DEMO / name), (
        f"{name} has drifted from src/synthetic.py; regenerate "
        f"fixtures/synthetic/demo or revert the generator")


def test_committed_truth_records_the_planted_parameters():
    truth = json.loads((DEMO / "truth.json").read_text())
    assert truth["params"]["seed"] == 20270106
    assert truth["params"]["n_players"] == 8
    assert truth["snaps"] == 200
    assert len(truth["players"]) == 8
    for rec in truth["players"].values():
        assert "jump" in rec and "vmax" in rec


def test_fixture_has_enough_structure_to_be_useful():
    rows = (DEMO / "player_play.csv").read_text().splitlines()
    assert len(rows) > 100          # header + 200 snaps
    assert "NA" in (DEMO / "player_play.csv").read_text()
