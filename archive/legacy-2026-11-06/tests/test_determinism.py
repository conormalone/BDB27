"""Determinism: same seed, same bytes.

Two levels are tested. First the pure pieces (fixtures, writers, measurement).
Then the real Phase 1 stage end to end, when the data is present, because a stage
that reorders a dict or iterates a set can drift between runs without any RNG.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import common as C
import physical as P
import synthetic


def _sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_fixture_is_byte_identical_across_builds(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    synthetic.build_fixture(a, seed=42, n_players=4, snaps_per=5)
    synthetic.build_fixture(b, seed=42, n_players=4, snaps_per=5)
    for name in ("game_tracking.csv", "player_play.csv", "games.csv",
                 "combine_tracking.csv"):
        assert _sha(a / name) == _sha(b / name), name


def test_fixture_changes_with_the_seed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    synthetic.build_fixture(a, seed=42, n_players=4, snaps_per=5)
    synthetic.build_fixture(b, seed=43, n_players=4, snaps_per=5)
    assert _sha(a / "game_tracking.csv") != _sha(b / "game_tracking.csv")


def test_writers_are_deterministic(tmp_path, cfg):
    p1 = C.write_json(tmp_path / "a.json", {"z": 1, "a": 2}, mode="sample", cfg=cfg)
    p2 = C.write_json(tmp_path / "b.json", {"z": 1, "a": 2}, mode="sample", cfg=cfg)
    assert _sha(p1) == _sha(p2)


def test_measurement_is_deterministic(cfg):
    rows, _ = synthetic.make_combine_frames(seed=5, n_players=2, attempts_per=2)
    sub = [r for r in rows if r["nfl_id"] == rows[0]["nfl_id"]]
    times = synthetic.relative_seconds([r["time"] for r in sub])
    args = (times, [r["x"] for r in sub], [r["y"] for r in sub],
            [r["s"] for r in sub], cfg, [1.0, 2.0])
    a = P.measure_attempt(*args)
    b = P.measure_attempt(*args)
    assert a == b


def test_seeding_is_reproducible(cfg):
    C.seed_everything(123)
    import random
    a = [random.random() for _ in range(3)]
    C.seed_everything(123)
    b = [random.random() for _ in range(3)]
    assert a == b


def test_audit_stage_is_byte_identical_on_rerun(cfg, real_data):
    """Run 01_audit twice with --force and compare the audit table."""
    mod = __import__("conftest").load_stage("01_audit")
    out = C.paths(cfg, "sample")["outputs"] / "audit" / "audit_items.csv"
    assert mod.main(["--mode", "sample", "--force"]) == 0
    first = _sha(out)
    assert mod.main(["--mode", "sample", "--force"]) == 0
    second = _sha(out)
    assert first == second
