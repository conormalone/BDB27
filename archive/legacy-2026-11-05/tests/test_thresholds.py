"""Provenance logic for src/00_thresholds.py — both modes, and refusals."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

import lc_common as C
from conftest import load_script, write_config

thr = load_script("00_thresholds")
REAL_RAW = C.REPO_ROOT / "data" / "raw"


def test_sample_payload_and_stamp(tmp_cfg, tmp_path):
    payload = thr.build_payload(tmp_cfg, "sample")
    out = C.write_json(tmp_path / "thresholds.json", payload, mode="sample",
                       cfg=tmp_cfg)
    data = json.loads(out.read_text())
    assert data["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert C.SAMPLE_STAMP in data["provenance"]
    assert data["mode"] == "sample"
    for section in ("thresholds", "ceiling", "audit", "leverage", "efforts",
                    "power", "gate"):
        assert section in data
    assert data["thresholds"] == C.lc(tmp_cfg)["thresholds"]
    datetime.strptime(data["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
    assert data["seed"] == C.lc(tmp_cfg)["seeds"]["base"]
    assert len(data["config_fingerprint"]) == 64


def test_require_full_provenance_sample_is_allowed(tmp_cfg):
    assert C.require_full_provenance(tmp_cfg, "sample", "03") == "PLACEHOLDER_SAMPLE"


def test_require_full_provenance_full_raises(tmp_cfg):
    tmp_cfg["bdb27_lc"]["mode"]["full"]["provenance"] = "PLACEHOLDER_SAMPLE"
    with pytest.raises(RuntimeError) as err:
        C.require_full_provenance(tmp_cfg, "full", "04")
    assert "refusing to run" in str(err.value)
    assert "'FULL'" in str(err.value)


def test_require_full_provenance_full_passes_when_configured(tmp_cfg):
    assert C.provenance(tmp_cfg, "full") == "FULL"
    assert C.require_full_provenance(tmp_cfg, "full", "05") == "FULL"


def test_verify_full_input_missing_files(tmp_cfg):
    with pytest.raises(RuntimeError) as err:
        thr.verify_full_input(tmp_cfg, "full")
    assert "missing" in str(err.value)
    assert "game_tracking_2023.csv" in str(err.value)


def test_verify_full_input_rejects_sample_sized_input(tmp_cfg, tmp_path):
    """The real sample file is exactly the cap, so FULL must be refused."""
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    tmp_cfg["bdb27_lc"]["files"]["game_tracking_full"] = [
        "game_tracking_2023_sample.csv"]
    link = tmp_path / "raw" / "game_tracking_2023_sample.csv"
    link.symlink_to(REAL_RAW / "game_tracking_2023_sample.csv")
    with pytest.raises(RuntimeError) as err:
        thr.verify_full_input(tmp_cfg, "full")
    message = str(err.value)
    assert "1,048,575" in message
    assert "sample row cap" in message


def test_verify_full_input_accepts_larger_than_cap(tmp_cfg, tmp_path):
    tmp_cfg["bdb27_lc"]["files"]["game_tracking_full"] = ["fake_full.csv"]
    tmp_cfg["bdb27_lc"]["mode"]["sample"]["sample_row_cap"] = 1
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "fake_full.csv").write_text(
        "game_id,play_id,nfl_id,time,x,y,s,a,dis,o,dir,event\n"
        "1,1,1,2023-09-07T17:00:00.000,0,0,0,0,0,0,0,ball_snap\n"
        "1,1,1,2023-09-07T17:00:00.100,1,0,1,1,0,1,90,\n")
    result = thr.verify_full_input(tmp_cfg, "full")
    assert result["rows"] == 2
    assert result["sample_row_cap"] == 1


def test_main_sample_mode_writes_stamped_file(tmp_cfg, tmp_path):
    cfg_path = write_config(tmp_path / "config.yaml", tmp_cfg)
    code = thr.main(["--mode", "sample", "--config", str(cfg_path)])
    assert code == 0
    data = json.loads((tmp_path / "outputs" / "thresholds.json").read_text())
    assert data["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert C.SAMPLE_STAMP in data["provenance"]


def test_main_full_mode_refuses_sample_input(tmp_cfg, tmp_path):
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    tmp_cfg["bdb27_lc"]["files"]["game_tracking_full"] = [
        "game_tracking_2023_sample.csv"]
    (tmp_path / "raw" / "game_tracking_2023_sample.csv").symlink_to(
        REAL_RAW / "game_tracking_2023_sample.csv")
    cfg_path = write_config(tmp_path / "config.yaml", tmp_cfg)
    code = thr.main(["--mode", "full", "--config", str(cfg_path)])
    assert code == 2
    assert not (tmp_path / "outputs" / "thresholds.json").exists()
