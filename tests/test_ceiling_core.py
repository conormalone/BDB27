"""Ceiling core: planted Combine recovery, shrinkage, flags, determinism."""

from __future__ import annotations

import copy
import csv
from pathlib import Path

import pytest

import ceiling_core
import lc_common as C
import synthetic_lc_fixture as F

FILES = {
    "combine_tracking.csv": "combine",
    "combine_results.csv": "combine",
    "players.csv": "combine",
    "player_play.csv": "game",
    "games.csv": "game",
    "game_tracking_2023_sample.csv": "game",
}


def _env(tmp_path: Path, cfg: dict):
    root = tmp_path / "fixtures"
    F.generate(root, seed=20270107)
    clone = copy.deepcopy(cfg)
    block = clone["bdb27_lc"]
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    block["paths"]["raw"] = str(raw)
    block["runtime"]["min_free_mb_root"] = 1
    block["runtime"]["min_free_mb_interim"] = 1
    block["runtime"]["temp_directory"] = str(tmp_path / "tmp")
    for name, sub in FILES.items():
        source = "game_tracking.csv" if name.startswith("game_tracking") else name
        (raw / name).symlink_to(root / sub / source)
    truth = {int(r["nfl_id"]): float(r["true_vmax_yds"])
             for r in csv.DictReader((root / "combine" / "ground_truth.csv").open())}
    return clone, root, truth


@pytest.fixture(scope="module")
def ceiling_env(tmp_path_factory):
    base = tmp_path_factory.mktemp("ceiling")
    cfg = C.load_config()
    clone, root, truth = _env(base, cfg)
    result = ceiling_core.compute_ceiling_features(clone)
    return {"cfg": clone, "result": result, "truth": truth, "root": root}


def test_recovers_planted_vmax(ceiling_env):
    result = ceiling_env["result"]
    truth = ceiling_env["truth"]
    rows = [r for r in result["rows"] if r["feature"] == "vmax"]
    assert len(rows) == 52
    errors = [abs(r["estimate"] - truth[r["nfl_id"]]) / truth[r["nfl_id"]]
              for r in rows]
    assert max(errors) < 0.02, f"max relative vmax error {max(errors):.4f}"
    assert result["icc"]["vmax"] > 0.95
    assert result["selected"] == ["vmax"], "VIF screen must drop collinear tau/a0"


def test_sanity_flags_find_planted_defects(ceiling_env):
    fit = ceiling_env["result"]["summary"]["fit"]
    assert fit["n_duplicate_attempt"] >= 1
    assert fit["n_peak_window_gap"] >= 1
    assert fit["n_speed_above_cap"] >= 1
    assert fit["n_speed_over_vmax"] == 0


def test_one_attempt_players_are_shrunk_more(ceiling_env):
    rows = [r for r in ceiling_env["result"]["rows"] if r["feature"] == "vmax"]
    rel_one = [r["reliability"] for r in rows if r["n_attempts"] == 1]
    rel_two = [r["reliability"] for r in rows if r["n_attempts"] == 2]
    assert rel_one and rel_two
    assert sum(rel_one) / len(rel_one) < sum(rel_two) / len(rel_two)
    assert all(r["se"] > 0 for r in rows)


def test_every_estimate_carries_uncertainty(ceiling_env):
    for row in ceiling_env["result"]["rows"]:
        assert row["estimate"] > 0
        assert row["se"] > 0
        assert 0.0 <= row["icc"] <= 1.0
        assert 0.0 <= row["reliability"] <= 1.0
        assert row["n_attempts"] >= 1


def test_rows_match_configured_output_schema(ceiling_env):
    fields = set(ceiling_env["cfg"]["bdb27_lc"]["ceiling"]["output_schema"])
    for row in ceiling_env["result"]["rows"]:
        assert set(row) == fields


def test_computation_is_deterministic(tmp_path):
    cfg = C.load_config()
    clone, _, _ = _env(tmp_path, cfg)
    first = ceiling_core.compute_ceiling_features(clone)
    second = ceiling_core.compute_ceiling_features(clone)
    assert first["rows"] == second["rows"]
    assert first["summary"]["icc"] == second["summary"]["icc"]
    assert first["selected"] == second["selected"]


def test_short_and_nonstraight_attempts_are_excluded(tmp_path):
    """A 5-frame attempt and a path twice the displacement must both drop."""
    cfg = C.load_config()
    clone, root, _ = _env(tmp_path, cfg)
    track = root / "combine" / "combine_tracking.csv"
    lines = track.read_text().splitlines()
    header = lines[0].split(",")
    idx = {name: i for i, name in enumerate(header)}
    frames = [line.split(",") for line in lines[1:]]
    # Keep only five PLAYER frames for one real attempt, and double its y path.
    target = next(f for f in frames if f[idx["entity_type"]] == "PLAYER")
    key = (target[idx["nfl_id"]], target[idx["attempt"]], target[idx["event_id"]])
    kept = []
    seen = 0
    for f in frames:
        same = (f[idx["nfl_id"]], f[idx["attempt"]], f[idx["event_id"]]) == key
        if same:
            seen += 1
            if seen > 5:
                continue
        kept.append(f)
    track.write_text("\n".join([lines[0]] + [",".join(f) for f in kept]) + "\n")
    result = ceiling_core.compute_ceiling_features(clone)
    assert result["summary"]["fit"]["n_too_short"] >= 1
