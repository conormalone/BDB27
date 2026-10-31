"""dl_common: config isolation, provenance writers, checkpoints, parquet, CLI.

Every test here is mechanical: it pins the build contract in
``notes/dl-design.md`` §3 rather than any modelling choice.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

import dl.dl_common as D
import lc_common as C

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SAMPLE_STAMP = "UNVALIDATED SAMPLE OUTPUT"

#: Frozen snapshot of the ``bdb27_lc`` surface the DL engine must not disturb.
#: (Taken 2026-10-30, before the ``bdb27_dl`` block was appended.)
LC_SNAPSHOT = {
    "top_level_keys": [
        "audit", "ceiling", "efforts", "files", "fixture", "gate", "leverage",
        "mode", "models", "paths", "phase4", "power", "provenance_note",
        "route_depth", "runtime", "seeds", "thresholds", "validation",
    ],
    "sub_block_keys": {
        "paths": ["checkpoints", "figures", "fixtures", "interim", "outputs",
                  "parquet", "raw", "run_log", "synthetic", "thresholds", "tmp"],
        "files": ["combine_results", "combine_tracking", "game_tracking_full",
                  "game_tracking_sample", "games", "player_play", "players"],
        "seeds": ["base", "bootstrap", "fixture", "model", "permutation"],
        "mode": ["default", "full", "sample"],
        "runtime": ["chunk_rows", "csv_null_marker", "duckdb_memory_limit",
                    "duckdb_threads", "empty_string_is_category",
                    "min_free_mb_interim", "min_free_mb_root",
                    "parquet_compression", "parquet_row_group_size",
                    "rss_sample_interval_s", "temp_directory"],
        "thresholds": ["combine_dt_tolerance_s", "combine_sampling_hz",
                       "despike", "equivalence_margin", "gap", "icc_min",
                       "max_features", "min_attempts_per_player",
                       "min_efforts_per_bin", "min_efforts_per_player",
                       "min_receivers_both_extremes", "peak_window",
                       "smallest_effect_of_interest", "smoothing",
                       "speed_cap_yds", "vif_max"],
    },
    "values": {
        "paths": {
            "raw": "data/raw", "interim": "data/interim",
            "parquet": "data/interim/parquet",
            "checkpoints": "data/interim/checkpoints",
            "tmp": "data/interim/tmp", "outputs": "outputs",
            "run_log": "outputs/run_log.csv",
            "thresholds": "outputs/thresholds.json", "fixtures": "fixtures",
            "synthetic": "fixtures/synthetic/lc",
        },
        "files": {
            "combine_tracking": "combine_tracking.csv",
            "combine_results": "combine_results.csv",
            "players": "players.csv", "games": "games.csv",
            "player_play": "player_play.csv",
            "game_tracking_sample": "game_tracking_2023_sample.csv",
            "game_tracking_full": ["game_tracking_2023.csv",
                                   "game_tracking_2024.csv",
                                   "game_tracking_2025.csv"],
        },
        "seeds": {"base": 20270106, "fixture": 20270107, "bootstrap": 20270108,
                  "permutation": 20270109, "model": 20270110},
        "mode": {
            "default": "sample",
            "sample": {"tracking_key": "game_tracking_sample",
                       "provenance": "PLACEHOLDER_SAMPLE",
                       "sample_row_cap": 1048575},
            "full": {"tracking_key": "game_tracking_full",
                     "provenance": "FULL"},
        },
        "runtime": {
            "duckdb_threads": 1, "duckdb_memory_limit": "1200MB",
            "temp_directory": "data/interim/tmp", "csv_null_marker": "NA",
            "parquet_compression": "zstd", "parquet_row_group_size": 122880,
            "chunk_rows": 250000, "min_free_mb_root": 40,
            "min_free_mb_interim": 2048, "rss_sample_interval_s": 0.2,
        },
        "thresholds": {
            "speed_cap_yds": 12.0, "combine_sampling_hz": 10,
            "combine_dt_tolerance_s": 0.02, "icc_min": 0.5, "vif_max": 5.0,
            "max_features": 3,
        },
    },
}

TINY_CSV = "a,b,c\n1,2.5,x\n2,NA,y\n3,4.0,z\n"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def dl_cfg():
    return D.load_config()


@pytest.fixture
def tmp_cfg(tmp_path, dl_cfg):
    """DL config clone whose paths all live under ``tmp_path``."""
    clone = copy.deepcopy(dl_cfg)
    block = clone["bdb27_dl"]
    block["paths"].update({
        "raw": str(tmp_path / "raw"),
        "interim": str(tmp_path / "interim"),
        "parquet": str(tmp_path / "interim" / "dl_parquet"),
        "checkpoints": str(tmp_path / "interim" / "dl_checkpoints"),
        "tmp": str(tmp_path / "interim" / "tmp"),
        "outputs": str(tmp_path / "outputs" / "dl"),
        "figures": str(tmp_path / "outputs" / "dl" / "figures"),
        "fixtures": str(tmp_path / "fixtures"),
        "thresholds": str(tmp_path / "outputs" / "dl" / "thresholds.json"),
        "run_log": str(tmp_path / "outputs" / "run_log.csv"),
        "prereg": str(tmp_path / "notes" / "prereg.md"),
    })
    block["runtime"]["min_free_mb_root"] = 1
    block["runtime"]["min_free_mb_interim"] = 1
    block["runtime"]["temp_directory"] = str(tmp_path / "interim" / "tmp")
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    return clone


@pytest.fixture
def tiny_csv(tmp_cfg):
    path = D.resolve_paths(tmp_cfg, "sample")["raw"] / "tiny.csv"
    path.write_text(TINY_CSV, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_dl_block_loads(dl_cfg):
    block = D.dl(dl_cfg)
    assert block["mode"]["default"] == "sample"
    assert D.provenance(dl_cfg, "sample") == "PLACEHOLDER_SAMPLE"
    assert D.provenance(dl_cfg, "full") == "FULL"
    assert block["paths"]["prereg"] == "notes/prereg.md"
    assert block["paths"]["checkpoints"] == "data/interim/dl_checkpoints"
    assert "bdb27_lc" in dl_cfg          # both chains coexist in one file


def test_load_config_requires_dl_block(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump({"project": "BDB27", "seed": 1}), encoding="utf-8")
    with pytest.raises(KeyError, match="bdb27_dl"):
        D.load_config(path)
    with pytest.raises(KeyError):
        D.dl({"bdb27_lc": {}})


def test_bdb27_lc_block_is_untouched(dl_cfg):
    """The DL config must not have edited, moved or renamed any LC key."""
    block = dl_cfg["bdb27_lc"]
    assert sorted(block) == LC_SNAPSHOT["top_level_keys"]
    for name, keys in LC_SNAPSHOT["sub_block_keys"].items():
        assert sorted(block[name]) == keys, name
    for name, expected in LC_SNAPSHOT["values"].items():
        for key, value in expected.items():
            assert block[name][key] == value, f"bdb27_lc.{name}.{key}"
    # The two chains must not share a parquet/checkpoint directory.
    dl_block = D.dl(dl_cfg)
    for key in ("parquet", "checkpoints"):
        assert dl_block["paths"][key] != block["paths"][key]
    assert dl_cfg["pipeline_version"] == 1


# --------------------------------------------------------------------------- #
# Provenance + writers
# --------------------------------------------------------------------------- #
def test_provenance_stamps(dl_cfg):
    assert D.provenance_stamp(dl_cfg, "sample") == f"PLACEHOLDER_SAMPLE: {SAMPLE_STAMP}"
    assert D.provenance_stamp(dl_cfg, "full") == "FULL"


def test_require_full_provenance(dl_cfg):
    assert D.require_full_provenance(dl_cfg, "sample", "01_audit") == "PLACEHOLDER_SAMPLE"
    assert D.require_full_provenance(dl_cfg, "full", "01_audit") == "FULL"
    tampered = copy.deepcopy(dl_cfg)
    tampered["bdb27_dl"]["mode"]["full"]["provenance"] = "PLACEHOLDER_SAMPLE"
    with pytest.raises(RuntimeError, match="refusing to run"):
        D.require_full_provenance(tampered, "full", "01_audit")


@pytest.mark.parametrize("mode,stamp", [
    ("sample", f"PLACEHOLDER_SAMPLE: {SAMPLE_STAMP}"),
    ("full", "FULL"),
])
def test_write_csv_stamps_every_row(tmp_cfg, tmp_path, mode, stamp):
    out = tmp_path / "artefacts" / "rows.csv"
    D.write_csv(out, [{"a": 1, "b": 2}, {"a": 3}], ["a", "b"], mode=mode,
                cfg=tmp_cfg)
    with open(out, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == ["provenance", "a", "b"]
    assert {r["provenance"] for r in rows} == {stamp}
    assert rows[1]["b"] == ""


@pytest.mark.parametrize("mode,stamp", [
    ("sample", f"PLACEHOLDER_SAMPLE: {SAMPLE_STAMP}"),
    ("full", "FULL"),
])
def test_write_json_stamps_and_ignores_caller_override(tmp_cfg, tmp_path, mode, stamp):
    out = tmp_path / "artefacts" / "doc.json"
    D.write_json(out, {"b": 1, "a": 2, "provenance": "hand-written"}, mode=mode,
                 cfg=tmp_cfg)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert list(payload)[0] == "provenance"
    assert payload["provenance"] == stamp
    assert payload["b"] == 1 and payload["a"] == 2
    with pytest.raises(TypeError):
        D.write_json(out, ["not", "a", "mapping"], mode=mode, cfg=tmp_cfg)


def test_write_markdown_stamps_first_line(tmp_cfg, tmp_path):
    out = tmp_path / "artefacts" / "SUMMARY.md"
    D.write_markdown(out, "# heading", mode="sample", cfg=tmp_cfg)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"<!-- provenance: PLACEHOLDER_SAMPLE: {SAMPLE_STAMP} -->"
    assert lines[1] == "# heading"


# --------------------------------------------------------------------------- #
# Fingerprint / checkpoints
# --------------------------------------------------------------------------- #
def test_checkpoint_roundtrip(tmp_cfg):
    stage = "unit_stage"
    assert not D.is_done(tmp_cfg, stage, "sample")
    path = D.mark_done(tmp_cfg, stage, "sample", extra={"rows": 3})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["done"] is True
    assert list(payload) == sorted(payload)          # deterministic sorted keys
    assert payload["provenance"] == "PLACEHOLDER_SAMPLE"
    assert payload["extra"] == {"rows": 3}
    assert path.parent == D.resolve_paths(tmp_cfg, "sample")["checkpoints"]
    assert D.is_done(tmp_cfg, stage, "sample")
    assert not D.is_done(tmp_cfg, stage, "sample", force=True)
    assert not D.is_done(tmp_cfg, stage, "full")


def test_fingerprint_ignores_lc_and_pipeline_version(dl_cfg):
    base = D.config_fingerprint(dl_cfg, "01_audit")
    assert len(base) == 64
    assert base == D.config_fingerprint(dl_cfg, "01_audit")

    lc_changed = copy.deepcopy(dl_cfg)
    lc_changed["bdb27_lc"]["runtime"]["min_free_mb_interim"] = 1
    lc_changed["bdb27_lc"]["thresholds"]["speed_cap_yds"] = 99.0
    lc_changed["bdb27_lc"]["mode"]["full"]["provenance"] = "PLACEHOLDER_SAMPLE"
    assert D.config_fingerprint(lc_changed, "01_audit") == base

    version_changed = copy.deepcopy(dl_cfg)
    version_changed["pipeline_version"] = 2
    assert D.config_fingerprint(version_changed, "01_audit") == base

    assert D.config_fingerprint(dl_cfg, "02_combine_ceiling") != base


def test_fingerprint_tracks_dl_values(dl_cfg):
    base = D.config_fingerprint(dl_cfg, "01_audit")
    for mutate in (
        lambda c: c["bdb27_dl"]["thresholds"].__setitem__("icc_min", 0.99),
        lambda c: c["bdb27_dl"]["runtime"].__setitem__("parquet_row_group_size", 1),
        lambda c: c["bdb27_dl"]["mode"]["full"].__setitem__("provenance", "OTHER"),
    ):
        changed = copy.deepcopy(dl_cfg)
        mutate(changed)
        assert D.config_fingerprint(changed, "01_audit") != base
    seed_changed = copy.deepcopy(dl_cfg)
    seed_changed["seed"] = 1
    assert D.config_fingerprint(seed_changed, "01_audit") != base


def test_checkpoint_name_carries_stage_mode_and_fingerprint(tmp_cfg):
    path = D.checkpoint_path(tmp_cfg, "03_game_snaps", "full")
    fp = D.config_fingerprint(tmp_cfg, "03_game_snaps")[:16]
    assert path.name == f"03_game_snaps__full__{fp}.json"
    assert "dl_checkpoints" in path.as_posix()


# --------------------------------------------------------------------------- #
# Prereg gate
# --------------------------------------------------------------------------- #
def test_require_prereg_sample_mode_never_raises(tmp_cfg):
    path = D.require_prereg(tmp_cfg, "sample", "01_audit")
    assert path == Path(tmp_cfg["bdb27_dl"]["paths"]["prereg"])
    assert not path.exists()


def test_require_prereg_full_mode_gates_on_frozen(tmp_cfg):
    path = Path(tmp_cfg["bdb27_dl"]["paths"]["prereg"])
    with pytest.raises(RuntimeError, match="pre-registration"):
        D.require_prereg(tmp_cfg, "full", "01_audit")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Pratt placeholder: not frozen yet\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="FROZEN"):
        D.require_prereg(tmp_cfg, "full", "01_audit")

    path.write_text("# PREREG — FROZEN 2026-11-01\nvalues...\n", encoding="utf-8")
    assert D.require_prereg(tmp_cfg, "full", "01_audit") == path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_parse_args_contract(dl_cfg):
    ns = D.parse_args(["--mode", "full", "--force"])
    assert set(vars(ns)) == {"mode", "force", "config"}
    assert ns.mode == "full" and ns.force is True and ns.config is None

    ns = D.parse_args([])
    assert ns.mode == D.dl(dl_cfg)["mode"]["default"]
    assert ns.force is False and ns.config is None

    ns = D.parse_args(["--config", str(D.DEFAULT_CONFIG), "--mode", "sample"])
    assert ns.config == str(D.DEFAULT_CONFIG) and ns.mode == "sample"

    with pytest.raises(SystemExit):
        D.parse_args(["--mode", "bogus"])


def test_parse_args_accepts_stage_for_dispatcher():
    ns = D.parse_args(["01_audit", "--mode", "sample"], with_stage=True)
    assert ns.stage == "01_audit" and ns.mode == "sample"
    assert set(vars(ns)) == {"stage", "mode", "force", "config"}
    assert D.parse_args(["--mode", "sample"], with_stage=True).stage is None


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def test_resolve_paths_creates_only_interim_dirs(tmp_cfg, tmp_path):
    paths = D.resolve_paths(tmp_cfg, "sample")
    for key in ("interim", "parquet", "checkpoints", "tmp"):
        assert paths[key].is_dir(), key
    assert not (tmp_path / "outputs").exists()
    with pytest.raises(ValueError):
        D.resolve_paths(tmp_cfg, "nope")


def test_runtime_temp_directory_matches_paths_tmp(tmp_cfg):
    configured = Path(tmp_cfg["bdb27_dl"]["runtime"]["temp_directory"])
    assert configured == D.resolve_paths(tmp_cfg, "sample")["tmp"]


def test_source_and_tracking_paths(dl_cfg):
    assert D.source_paths(dl_cfg, "players")[0].name == "players.csv"
    assert len(D.source_paths(dl_cfg, "game_tracking_full")) == 3
    sample = D.tracking_sources(dl_cfg, "sample")
    full = D.tracking_sources(dl_cfg, "full")
    assert [p.name for p in sample] == ["game_tracking_2023_sample.csv"]
    assert [p.name for p in full] == [
        "game_tracking_2023.csv", "game_tracking_2024.csv",
        "game_tracking_2025.csv"]
    assert sample[0].parent == D.resolve_paths(dl_cfg, "sample")["raw"]
    with pytest.raises(KeyError):
        D.source_paths(dl_cfg, "not_a_file")


# --------------------------------------------------------------------------- #
# Parquet conversion
# --------------------------------------------------------------------------- #
def test_to_parquet_selects_only_requested_columns(tmp_cfg, tiny_csv):
    out = D.to_parquet(tmp_cfg, "tiny", "tiny", columns=["a", "c"],
                       source=tiny_csv)
    assert out.parent == D.resolve_paths(tmp_cfg, "sample")["parquet"]
    assert out.suffix == ".parquet" and out.exists()
    con = duckdb.connect()
    try:
        rel = con.sql(f"SELECT * FROM read_parquet('{out.as_posix()}')")
        assert rel.columns == ["a", "c"]
        assert con.execute(
            f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0] == 3
        nulls = con.execute(
            f"SELECT count(*) FROM read_parquet('{out.as_posix()}') "
            f"WHERE c = 'y'").fetchone()[0]
        assert nulls == 1
        rows = con.execute(
            f"SELECT a, c FROM read_parquet('{out.as_posix()}') ORDER BY a"
        ).fetchall()
        assert rows == [(1, "x"), (2, "y"), (3, "z")]
    finally:
        con.close()


def test_to_parquet_uses_configured_compression_and_row_group(tmp_cfg, tiny_csv):
    out = D.to_parquet(tmp_cfg, "tiny", "tiny_meta", columns=["a"], source=tiny_csv)
    con = duckdb.connect()
    try:
        meta = con.execute(
            f"SELECT DISTINCT compression, row_group_num_rows "
            f"FROM parquet_metadata('{out.as_posix()}')").fetchall()
    finally:
        con.close()
    assert {m[0].upper() for m in meta} == {
        str(tmp_cfg["bdb27_dl"]["runtime"]["parquet_compression"]).upper()}
    cap = int(tmp_cfg["bdb27_dl"]["runtime"]["parquet_row_group_size"])
    assert all(m[1] <= cap for m in meta)


def test_to_parquet_supports_expression_columns(tmp_cfg, tiny_csv):
    out = D.to_parquet(tmp_cfg, "tiny", "tiny_expr",
                       columns={"doubled": "a * 2", "c": "c"}, source=tiny_csv)
    con = duckdb.connect()
    try:
        rel = con.sql(f"SELECT * FROM read_parquet('{out.as_posix()}')")
        assert rel.columns == ["doubled", "c"]
        assert con.execute(
            f"SELECT max(doubled) FROM read_parquet('{out.as_posix()}')").fetchone()[0] == 6
    finally:
        con.close()


def test_to_parquet_falls_back_to_declared_schema(tmp_cfg):
    """``columns=None`` is allowed only when lc_common declares the schema."""
    declared = list(C.EXPECTED_SCHEMAS["players"]["columns"])
    raw = D.resolve_paths(tmp_cfg, "sample")["raw"] / "players.csv"
    values = {
        "nfl_id": "1", "display_name": "A Player", "draft_year": "2020",
        "nfl_position": "DE", "birth_date": "1998-01-01",
        "college_name": "College", "college_conference": "Conf",
        "draft_round": "1", "draft_pick_within_round": "1",
        "draft_overall_pick": "1",
    }
    raw.write_text(",".join(declared) + "\n" +
                   ",".join(values[c] for c in declared) + "\n", encoding="utf-8")
    out = D.to_parquet(tmp_cfg, "players", "players")
    con = duckdb.connect()
    try:
        assert con.sql(f"SELECT * FROM read_parquet('{out.as_posix()}')").columns == declared
    finally:
        con.close()

    with pytest.raises(ValueError, match="explicit column list"):
        D.to_parquet(tmp_cfg, "no_such_schema_key", "nope")


def test_to_parquet_is_idempotent_and_force_rebuilds(tmp_cfg, tiny_csv):
    out = D.to_parquet(tmp_cfg, "tiny", "tiny_idem", columns=["a"], source=tiny_csv)
    first = out.stat().st_mtime_ns
    again = D.to_parquet(tmp_cfg, "tiny", "tiny_idem", columns=["a"], source=tiny_csv)
    assert again == out and out.stat().st_mtime_ns == first

    future = tiny_csv.stat().st_mtime + 60
    os.utime(tiny_csv, (future, future))
    D.to_parquet(tmp_cfg, "tiny", "tiny_idem", columns=["a"], source=tiny_csv)
    assert out.stat().st_mtime_ns != first

    rebuilt = out.stat().st_mtime_ns
    D.to_parquet(tmp_cfg, "tiny", "tiny_idem", columns=["a"], source=tiny_csv,
                 force=True)
    assert out.stat().st_mtime_ns >= rebuilt


def test_to_parquet_reports_missing_source(tmp_cfg):
    with pytest.raises(FileNotFoundError):
        D.to_parquet(tmp_cfg, "tiny", "tiny_missing", columns=["a"],
                     source=Path(tmp_cfg["bdb27_dl"]["paths"]["raw"]) / "gone.csv")


def test_duckdb_connect_is_single_threaded_with_interim_tempdir(tmp_cfg):
    con = D.duckdb_connect(tmp_cfg)
    try:
        threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
        tmpdir = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        limit = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    finally:
        con.close()
    assert int(threads) == int(tmp_cfg["bdb27_dl"]["runtime"]["duckdb_threads"]) == 1
    assert Path(tmpdir).resolve() == D.resolve_paths(tmp_cfg, "sample")["tmp"].resolve()
    # DuckDB renders "1200MB" as "1.1 GiB"; only the presence matters here.
    assert "unlimited" not in str(limit).lower()
    assert any(unit in str(limit).upper() for unit in ("MB", "GIB", "GB"))


# --------------------------------------------------------------------------- #
# Disk guard
# --------------------------------------------------------------------------- #
def test_disk_guard_passes_with_real_thresholds(dl_cfg):
    D.resolve_paths(dl_cfg, "sample")
    measured = D.assert_disk_space(dl_cfg)
    assert set(measured) == {"root_free_mb", "interim_free_mb"}
    assert measured["root_free_mb"] > 0 and measured["interim_free_mb"] > 0


def test_disk_guard_fails_loudly_with_actual_numbers(tmp_cfg):
    bad = copy.deepcopy(tmp_cfg)
    bad["bdb27_dl"]["runtime"]["min_free_mb_root"] = 10 ** 9
    bad["bdb27_dl"]["runtime"]["min_free_mb_interim"] = 10 ** 9
    with pytest.raises(RuntimeError) as err:
        D.assert_disk_space(bad)
    message = str(err.value)
    assert "DISK GUARD FAILED" in message
    assert "root filesystem" in message and "interim volume" in message
    assert "1000000000 MB" in message
    assert "MB free" in message


# --------------------------------------------------------------------------- #
# Full-run evidence gate
# --------------------------------------------------------------------------- #
def test_full_tracking_gate_refuses_sample_sized_input(tmp_cfg):
    """A full run must refuse input no larger than the provided sample cap."""
    raw = D.resolve_paths(tmp_cfg, "sample")["raw"]
    names = ("game_tracking_2023.csv", "game_tracking_2024.csv",
             "game_tracking_2025.csv")
    for name in names:
        (raw / name).write_text("game_id,play_id,nfl_id\n1,1,10\n1,2,11\n",
                                encoding="utf-8")
    assert D.tracking_row_count(tmp_cfg, "full") == 6
    with pytest.raises(RuntimeError, match="does not exceed"):
        D.require_full_tracking(tmp_cfg, "full")

    (raw / names[1]).unlink()
    with pytest.raises(RuntimeError, match="missing"):
        D.require_full_tracking(tmp_cfg, "full")


# --------------------------------------------------------------------------- #
# Run log
# --------------------------------------------------------------------------- #
def test_timed_stage_appends_run_log(tmp_cfg):
    with D.timed_stage(tmp_cfg, "unit_stage", "sample", note="start") as state:
        state.note("did a thing")
    log = Path(tmp_cfg["bdb27_dl"]["paths"]["run_log"])
    assert log.exists()
    with open(log, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == C.RUN_LOG_FIELDS
    assert rows[-1]["stage"] == "unit_stage"
    assert rows[-1]["mode"] == "sample"
    assert rows[-1]["provenance"] == "PLACEHOLDER_SAMPLE"
    assert "start" in rows[-1]["notes"] and "did a thing" in rows[-1]["notes"]

    with pytest.raises(RuntimeError):
        with D.timed_stage(tmp_cfg, "unit_fail", "sample"):
            raise RuntimeError("boom")
    with open(log, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[-1]["stage"] == "unit_fail"
    assert "FAILED: RuntimeError: boom" in rows[-1]["notes"]


# --------------------------------------------------------------------------- #
# Re-exported pure helpers
# --------------------------------------------------------------------------- #
def test_pure_helpers_are_reused_not_duplicated():
    assert D.SAMPLE_STAMP == SAMPLE_STAMP
    assert D.EXPECTED_SCHEMAS is C.EXPECTED_SCHEMAS
    assert D.schema_check is C.schema_check
    assert D.SchemaError is C.SchemaError
    assert D.seed_everything is C.seed_everything
    assert D.stable_hash is C.stable_hash
    assert len(D.EXPECTED_SCHEMAS) == 6

    import pandas as pd

    with pytest.raises(D.SchemaError):
        D.schema_check(pd.DataFrame({"a": [1]}), {"a": "float"})

    D.seed_everything(4242)
    first = (random.random(), random.random())
    D.seed_everything(4242)
    assert first == (random.random(), random.random())
    assert D.stable_hash({"a": 1, "b": 2}) == D.stable_hash({"b": 2, "a": 1})


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
def _run(args, cwd):
    return subprocess.run([sys.executable, *args], cwd=str(cwd),
                          capture_output=True, text=True)


@pytest.mark.parametrize("cwd,args", [
    (SRC, ["-m", "dl.run"]),
    (ROOT, ["src/dl/run.py"]),
])
def test_dispatcher_rejects_unknown_stage_with_valid_stages(cwd, args):
    """Contract: a name outside the registry is refused, and the user is told
    both what was wrong and which stages do exist.

    This deliberately does *not* assert on whether a given stage happens to be
    implemented today; that is transient state, not a contract.
    """
    result = _run([*args, "99_not_a_stage", "--mode", "sample"], cwd)
    assert result.returncode == 2, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "unknown stage '99_not_a_stage'" in output
    for stage in ("01_audit", "02_combine_ceiling", "00_thresholds",
                  "03_game_snaps", "04_models", "05_validate"):
        assert stage in output
    assert "Traceback" not in result.stderr


def test_dispatcher_says_stage_not_implemented_for_missing_module(monkeypatch, capsys):
    """Registered stage whose module is absent must fail with the clear
    "not implemented" message, whichever stages are implemented today.

    The registry is extended with a synthetic entry in-process, so the path is
    pinned without depending on the current state of ``src/dl/stage_*.py``.
    """
    import dl.run as R

    missing_module = "dl.stage_zz_missing_unit_test"
    patched = dict(R.STAGES)          # keep the real stages for the hint text
    patched["zz_missing"] = missing_module
    monkeypatch.setattr(R, "STAGES", patched)

    assert R.main(["zz_missing", "--mode", "sample"]) == 2
    err = capsys.readouterr().err
    assert "STAGE NOT IMPLEMENTED" in err
    assert "zz_missing" in err and missing_module in err
    assert "01_audit" in err          # still lists valid stages
    assert "Traceback" not in err


def test_dispatcher_requires_a_stage():
    result = _run(["src/dl/run.py"], ROOT)
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert "valid stages:" in result.stderr
