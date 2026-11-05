"""Shared helpers: config, paths, checkpoints, run log, TMPDIR, writers."""

from __future__ import annotations

import csv
import json
import os
import random
import tempfile
import time

import pytest

import lc_common as C


def test_config_access(tmp_cfg):
    block = C.lc(tmp_cfg)
    assert block["mode"]["default"] == "sample"
    assert C.provenance(tmp_cfg, "sample") == "PLACEHOLDER_SAMPLE"
    with pytest.raises(KeyError):
        C.lc({"no_block": 1})


def test_resolve_paths_creates_only_interim_dirs(tmp_cfg, tmp_path):
    paths = C.resolve_paths(tmp_cfg, "sample")
    for key in ("interim", "parquet", "checkpoints", "tmp"):
        assert paths[key].is_dir(), key
    assert not (tmp_path / "outputs").exists()
    with pytest.raises(ValueError):
        C.resolve_paths(tmp_cfg, "nope")


def test_runtime_temp_directory_matches_paths_tmp(tmp_cfg):
    """Two config keys name the temp dir; they must not drift apart."""
    configured = C._abs(tmp_cfg, C.lc(tmp_cfg)["runtime"]["temp_directory"])
    assert configured == C.resolve_paths(tmp_cfg, "sample")["tmp"]


def test_source_paths_expands_full_list(tmp_cfg):
    paths = C.source_paths(tmp_cfg, "game_tracking_full")
    assert len(paths) == 3
    assert [p.name for p in paths] == [
        "game_tracking_2023.csv", "game_tracking_2024.csv", "game_tracking_2025.csv"]
    assert C.source_paths(tmp_cfg, "players")[0].name == "players.csv"
    with pytest.raises(KeyError):
        C.source_paths(tmp_cfg, "not_a_file")


def test_seed_everything_is_deterministic():
    C.seed_everything(123)
    a = (random.random(), random.random())
    C.seed_everything(123)
    b = (random.random(), random.random())
    assert a == b


def test_stable_hash_is_key_order_insensitive():
    assert C.stable_hash({"a": 1, "b": 2}) == C.stable_hash({"b": 2, "a": 1})
    assert C.stable_hash({"a": 1}) != C.stable_hash({"a": 2})


def test_config_fingerprint_stability_and_sensitivity(tmp_cfg):
    a = C.config_fingerprint(tmp_cfg, "01_audit")
    b = C.config_fingerprint(tmp_cfg, "01_audit")
    assert a == b and len(a) == 64
    assert C.config_fingerprint(tmp_cfg, "02_combine_ceiling") != a
    tmp_cfg["bdb27_lc"]["thresholds"]["speed_cap_yds"] = 99.0
    assert C.config_fingerprint(tmp_cfg, "01_audit") != a


def test_checkpoint_skip_and_force(tmp_cfg):
    stage = "unit_stage"
    assert not C.is_done(tmp_cfg, stage, "sample")
    path = C.mark_done(tmp_cfg, stage, "sample", extra={"rows": 3})
    payload = json.loads(path.read_text())
    assert payload["done"] is True
    assert list(payload) == sorted(payload)          # deterministic sorted keys
    assert payload["provenance"] == "PLACEHOLDER_SAMPLE"
    assert C.is_done(tmp_cfg, stage, "sample")
    assert not C.is_done(tmp_cfg, stage, "sample", force=True)
    assert not C.is_done(tmp_cfg, stage, "full")
    # changing the config invalidates the checkpoint
    tmp_cfg["bdb27_lc"]["thresholds"]["speed_cap_yds"] = 11.5
    assert not C.is_done(tmp_cfg, stage, "sample")


def test_checkpoint_corrupt_file_is_not_done(tmp_cfg):
    path = C.checkpoint_path(tmp_cfg, "corrupt", "sample")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert not C.is_done(tmp_cfg, "corrupt", "sample")


def test_run_log_appends_header_once(tmp_cfg, tmp_path):
    with C.timed_stage(tmp_cfg, "stage_a", "sample", note="first") as st:
        st.note("extra")
    with C.timed_stage(tmp_cfg, "stage_b", "sample"):
        pass
    log = tmp_path / "outputs" / "run_log.csv"
    rows = list(csv.DictReader(log.open()))
    assert len(rows) == 2
    assert rows[0]["stage"] == "stage_a"
    assert rows[1]["stage"] == "stage_b"
    assert rows[0]["provenance"] == "PLACEHOLDER_SAMPLE"
    assert "first" in rows[0]["notes"] and "extra" in rows[0]["notes"]
    assert float(rows[0]["wall_s"]) >= 0.0
    assert float(rows[0]["peak_rss_mb"]) > 0.0
    header = log.read_text().splitlines()[0]
    assert header == ",".join(C.RUN_LOG_FIELDS)


def test_duckdb_redirects_tmpdir(tmp_cfg, tmp_path):
    old = {k: os.environ.get(k) for k in ("TMPDIR", "TMP", "TEMP")}
    old_tempdir = tempfile.tempdir
    con = None
    try:
        con = C.duckdb_connect(tmp_cfg)
        expected = str(tmp_path / "interim" / "tmp")
        assert os.environ["TMPDIR"] == expected
        assert tempfile.gettempdir() == expected
        got = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        assert os.path.realpath(got) == os.path.realpath(expected)
    finally:
        if con is not None:
            con.close()
        tempfile.tempdir = old_tempdir
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_write_csv_stamps_sample_and_full(tmp_cfg, tmp_path):
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    sample = C.write_csv(tmp_path / "s.csv", rows, ["a", "b"], mode="sample",
                         cfg=tmp_cfg)
    lines = sample.read_text().splitlines()
    assert lines[0].startswith("provenance,")
    assert C.SAMPLE_STAMP in lines[1]
    assert "PLACEHOLDER_SAMPLE" in lines[1]

    full = C.write_csv(tmp_path / "f.csv", rows, ["a", "b"], mode="full",
                       cfg=tmp_cfg)
    assert full.read_text().splitlines()[1].startswith("FULL,")

    seq = C.write_csv(tmp_path / "seq.csv", [[3, "z"]], ["a", "b"],
                      mode="sample", cfg=tmp_cfg)
    assert C.SAMPLE_STAMP in seq.read_text()


def test_write_json_requires_mapping_and_stamps(tmp_cfg, tmp_path):
    with pytest.raises(TypeError):
        C.write_json(tmp_path / "x.json", [1, 2], mode="sample", cfg=tmp_cfg)
    out = C.write_json(tmp_path / "x.json", {"k": 1}, mode="sample", cfg=tmp_cfg)
    data = json.loads(out.read_text())
    assert list(data)[0] == "provenance"
    assert C.SAMPLE_STAMP in data["provenance"]
    assert data["k"] == 1


def test_write_markdown_stamps_and_is_atomic(tmp_cfg, tmp_path):
    out = C.write_markdown(tmp_path / "s.md", "# Title\n", mode="sample",
                           cfg=tmp_cfg)
    text = out.read_text()
    assert text.startswith(f"<!-- provenance: PLACEHOLDER_SAMPLE: {C.SAMPLE_STAMP} -->")
    assert "# Title" in text
    full = C.write_markdown(tmp_path / "f.md", "ok", mode="full", cfg=tmp_cfg)
    assert full.read_text().startswith("<!-- provenance: FULL -->")


def test_to_parquet_selection_idempotence_and_force(tmp_cfg, tmp_path):
    raw = tmp_path / "raw"
    (raw / "players.csv").write_text(
        "nfl_id,draft_year\n900001,2023\n900002,2024\n900003,2025\n")
    out = C.to_parquet(tmp_cfg, "players", "mini", columns=["nfl_id", "draft_year"])
    assert out.exists()
    con = C.duckdb_connect(tmp_cfg)
    try:
        assert con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0] == 3
    finally:
        con.close()

    first = out.stat().st_mtime_ns
    time.sleep(0.01)
    C.to_parquet(tmp_cfg, "players", "mini", columns=["nfl_id", "draft_year"])
    assert out.stat().st_mtime_ns == first          # skipped: parquet is newer

    time.sleep(0.01)
    C.to_parquet(tmp_cfg, "players", "mini", columns=["nfl_id", "draft_year"],
                 force=True)
    assert out.stat().st_mtime_ns != first          # force rebuilds
