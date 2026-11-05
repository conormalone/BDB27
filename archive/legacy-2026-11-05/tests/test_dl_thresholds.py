"""DL stage 00: thresholds artefact contract, guards and freeze gate.

The tests build a throwaway ``bdb27_dl`` config rooted in ``tmp_path`` — they
never read the raw data and never touch ``outputs/``.
"""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from dl import dl_common as D
from dl import stage_00_thresholds as stage00

CHOSEN_KEYS = ("absolute_speed_yds", "relative_fraction", "tuned_on")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _make_cfg(tmp_path, *, full_provenance: str = "FULL") -> dict:
    """Real config cloned with every path (and the disk guard) sent to tmp."""
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
    block["mode"]["full"]["provenance"] = full_provenance
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    return cfg


def _write_cfg(tmp_path, cfg: dict):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def cfg(tmp_path):
    return _make_cfg(tmp_path)


@pytest.fixture
def out_path(tmp_path):
    return tmp_path / "outputs" / "thresholds.json"


# --------------------------------------------------------------------------- #
# Sample mode
# --------------------------------------------------------------------------- #
def test_sample_writes_nulls_and_sample_provenance(cfg, out_path):
    outcome = stage00.run(cfg, "sample")
    assert outcome["skipped"] is False

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    # Provenance is the first key so it cannot be missed by a reader.
    assert list(payload)[0] == "provenance"
    assert payload["provenance"].startswith("PLACEHOLDER_SAMPLE")

    assert payload["mode"] == "sample"
    assert payload["frozen"] is False
    assert payload["chosen"] == {
        "absolute_speed_yds": None, "relative_fraction": None, "tuned_on": None}
    assert set(payload["chosen"]) == set(CHOSEN_KEYS)


def test_sample_refuses_to_clobber_a_full_file(cfg, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frozen = {
        "provenance": "FULL",
        "mode": "full",
        "frozen": True,
        "chosen": {"absolute_speed_yds": 2.5, "relative_fraction": 0.7,
                   "tuned_on": "train_folds"},
    }
    out_path.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    before = out_path.read_bytes()

    with pytest.raises(RuntimeError, match="FULL"):
        stage00.run(cfg, "sample", force=True)

    assert out_path.read_bytes() == before  # untouched, not partially written


# --------------------------------------------------------------------------- #
# Full mode
# --------------------------------------------------------------------------- #
def test_full_mode_without_full_provenance_raises(tmp_path):
    cfg = _make_cfg(tmp_path, full_provenance="PLACEHOLDER_SAMPLE")
    with pytest.raises(RuntimeError, match="provenance"):
        stage00.run(cfg, "full", force=True)


def test_full_mode_first_run_writes_nulls(cfg, out_path):
    stage00.run(cfg, "full", force=True)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["provenance"] == "FULL"
    assert payload["frozen"] is False
    assert payload["chosen"] == {
        "absolute_speed_yds": None, "relative_fraction": None, "tuned_on": None}


def test_full_rerun_carries_existing_chosen_forward(cfg, out_path):
    """04_models rewrites ``chosen``; re-running 00 must not erase it."""
    stage00.run(cfg, "full", force=True)
    tuned = json.loads(out_path.read_text(encoding="utf-8"))
    tuned["chosen"] = {"absolute_speed_yds": 2.5, "relative_fraction": 0.7,
                       "tuned_on": "train_folds"}
    tuned["frozen"] = True
    out_path.write_text(json.dumps(tuned, indent=2), encoding="utf-8")

    stage00.run(cfg, "full", force=True)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["provenance"] == "FULL"
    assert payload["chosen"] == tuned["chosen"]
    assert payload["frozen"] is True


# --------------------------------------------------------------------------- #
# Freeze gate
# --------------------------------------------------------------------------- #
def test_assert_thresholds_frozen_raises_then_passes(tmp_path, cfg, out_path):
    cfg_path = _write_cfg(tmp_path, cfg)
    stage00.run(cfg, "full", force=True)

    # Nulls under FULL provenance: still not frozen.
    with pytest.raises(RuntimeError, match="NOT FROZEN"):
        stage00.assert_thresholds_frozen(str(cfg_path))

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    payload["chosen"] = {"absolute_speed_yds": 2.5, "relative_fraction": 0.7,
                         "tuned_on": "train_folds"}
    payload["frozen"] = True
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    assert stage00.assert_thresholds_frozen(str(cfg_path))["frozen"] is True


def test_assert_thresholds_frozen_rejects_sample_provenance(tmp_path, cfg,
                                                            out_path):
    cfg_path = _write_cfg(tmp_path, cfg)
    stage00.run(cfg, "sample", force=True)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    # Even with values filled in, sample provenance is not usable.
    payload["chosen"] = {"absolute_speed_yds": 2.5, "relative_fraction": 0.7,
                         "tuned_on": "sample"}
    payload["frozen"] = True
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="NOT FROZEN"):
        stage00.assert_thresholds_frozen(str(cfg_path))


def test_assert_thresholds_frozen_raises_when_file_missing(tmp_path, cfg):
    cfg_path = _write_cfg(tmp_path, cfg)
    with pytest.raises(RuntimeError, match="NOT FROZEN"):
        stage00.assert_thresholds_frozen(str(cfg_path))


def test_is_frozen_requires_both_values():
    assert stage00.is_frozen({"absolute_speed_yds": 2.5,
                              "relative_fraction": 0.7}) is True
    assert stage00.is_frozen({"absolute_speed_yds": 2.5,
                              "relative_fraction": None}) is False
    assert stage00.is_frozen({"absolute_speed_yds": None,
                              "relative_fraction": 0.7}) is False
    assert stage00.is_frozen(None) is False


# --------------------------------------------------------------------------- #
# Grid fidelity / determinism
# --------------------------------------------------------------------------- #
def test_grids_round_trip_exactly_as_configured(cfg, out_path):
    stage00.run(cfg, "sample", force=True)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    load = cfg["bdb27_dl"]["load"]
    assert payload["absolute_speed_grid_yds"] == load["absolute_speed_grid_yds"]
    assert payload["relative_fraction_grid"] == load["relative_fraction_grid"]
    assert [type(v) for v in payload["absolute_speed_grid_yds"]] == \
        [type(v) for v in load["absolute_speed_grid_yds"]]
    assert payload["recency_half_life_s"] == load["recency_half_life_s"]
    assert payload["min_snaps_per_player"] == \
        cfg["bdb27_dl"]["game"]["min_snaps_per_player"]
    assert payload["garbage_time"] == cfg["bdb27_dl"]["game"]["garbage_time"]
    assert payload["primary_feature"] == \
        cfg["bdb27_dl"]["ceiling"]["primary_feature"]
    assert payload["fallback_feature"] == \
        cfg["bdb27_dl"]["ceiling"]["fallback_feature"]
    assert "prereg_frozen" in payload and "prereg_path" in payload


def test_idempotent_rerun_is_byte_identical(cfg, out_path):
    stage00.run(cfg, "sample", force=True)
    first = out_path.read_bytes()
    stage00.run(cfg, "sample", force=True)
    assert out_path.read_bytes() == first


def test_payload_keys_are_complete(cfg):
    payload = stage00.build_thresholds(cfg, "sample")
    assert list(payload) == [
        "mode", "frozen", "absolute_speed_grid_yds", "relative_fraction_grid",
        "recency_half_life_s", "min_snaps_per_player", "garbage_time",
        "primary_feature", "fallback_feature", "prereg_path", "prereg_frozen",
        "chosen"]
