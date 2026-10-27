"""Same seed / same inputs => identical outputs for this pass's components."""

from __future__ import annotations

import hashlib

import lc_common as C
import synthetic_lc_fixture as F
from conftest import load_script

thr = load_script("00_thresholds")


def test_threshold_payload_deterministic_except_timestamp(tmp_cfg):
    a = thr.build_payload(tmp_cfg, "sample")
    b = thr.build_payload(tmp_cfg, "sample")
    a.pop("created_utc")
    b.pop("created_utc")
    assert a == b


def test_fixture_generation_identical(tmp_path):
    F.generate(tmp_path / "one", seed=99, n_games=2, plays_per_game=5)
    F.generate(tmp_path / "two", seed=99, n_games=2, plays_per_game=5)
    for rel in ("combine/combine_tracking.csv", "game/player_play.csv",
                "game/game_tracking.csv", "game/ground_truth.json"):
        one = hashlib.sha256((tmp_path / "one" / rel).read_bytes()).hexdigest()
        two = hashlib.sha256((tmp_path / "two" / rel).read_bytes()).hexdigest()
        assert one == two, rel


def test_parquet_conversion_is_byte_deterministic(tmp_cfg, tmp_path):
    (tmp_path / "raw" / "players.csv").write_text(
        "nfl_id,draft_year\n900001,2023\n900002,2024\n")
    out = C.to_parquet(tmp_cfg, "players", "det", columns=["nfl_id", "draft_year"],
                       force=True)
    first = hashlib.sha256(out.read_bytes()).hexdigest()
    C.to_parquet(tmp_cfg, "players", "det", columns=["nfl_id", "draft_year"],
                 force=True)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == first


def test_checkpoint_payload_deterministic_apart_from_timestamp(tmp_cfg):
    a = C.mark_done(tmp_cfg, "det_stage", "sample", extra={"x": 1})
    b = C.mark_done(tmp_cfg, "det_stage", "sample", extra={"x": 1})
    import json
    pa, pb = json.loads(a.read_text()), json.loads(b.read_text())
    pa.pop("when_utc"), pb.pop("when_utc")
    assert pa == pb
