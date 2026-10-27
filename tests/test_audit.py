"""Audit items a-j, gate logic, provenance and path isolation."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

import lc_common as C
from conftest import load_script, write_config

audit = load_script("01_audit")

FILES = {
    "combine_tracking.csv": "combine",
    "combine_results.csv": "combine",
    "players.csv": "combine",
    "player_play.csv": "game",
    "games.csv": "game",
    "game_tracking_2023_sample.csv": "game",
}


def _fixture_cfg(tmp_path, cfg, fixtures_root):
    clone = copy.deepcopy(cfg)
    block = clone["bdb27_lc"]
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    block["paths"].update({
        "raw": str(raw),
        "interim": str(tmp_path / "interim"),
        "parquet": str(tmp_path / "interim" / "parquet"),
        "checkpoints": str(tmp_path / "interim" / "checkpoints"),
        "tmp": str(tmp_path / "interim" / "tmp"),
        "outputs": str(tmp_path / "outputs"),
        "figures": str(tmp_path / "outputs" / "figures"),
        "fixtures": str(tmp_path / "fixtures"),
        "synthetic": str(tmp_path / "fixtures" / "synthetic" / "lc"),
        "thresholds": str(tmp_path / "outputs" / "thresholds.json"),
        "run_log": str(tmp_path / "outputs" / "run_log.csv"),
    })
    block["runtime"]["min_free_mb_root"] = 1
    block["runtime"]["min_free_mb_interim"] = 1
    block["runtime"]["temp_directory"] = str(tmp_path / "interim" / "tmp")
    for name, sub in FILES.items():
        source = "game_tracking.csv" if name.startswith("game_tracking") else name
        (raw / name).symlink_to(fixtures_root / sub / source)
    return clone


@pytest.fixture(scope="module")
def fixtures_root(tmp_path_factory):
    import synthetic_lc_fixture as F
    root = tmp_path_factory.mktemp("audit_fixtures") / "lc"
    F.generate(root, seed=20270107)
    return root


def _run(cfg_path, extra=()):
    return audit.main(["--mode", "sample", "--config", str(cfg_path),
                       "--force", *extra])


def _check(verdict="PASS", status="EVALUATED", cid="a"):
    return {"id": cid, "name": "x", "status": status, "verdict": verdict,
            "value": 1, "threshold": 1, "basis": "b"}


def test_gate_logic_go_pending_and_no_go():
    assert audit.decide([_check()]) == "GO"
    assert audit.decide([_check(), _check("PENDING", "PENDING FULL RUN", "g")]) \
        == "GO_PENDING_FULL_RUN"
    assert audit.decide([_check(), _check("FAIL", "EVALUATED", "c")]) == "NO-GO"
    assert audit.decide([_check("PASS", "BLOCKED", "d")]) == "NO-GO"


def test_audit_fixture_end_to_end(tmp_path, cfg, fixtures_root):
    clone = _fixture_cfg(tmp_path, cfg, fixtures_root)
    cfg_path = write_config(tmp_path / "config.yaml", clone)
    # A legacy path must not be touched by the new pipeline.
    legacy = tmp_path / "outputs" / "audit" / "db_decay"
    legacy.mkdir(parents=True)
    sentinel = legacy / "sentinel.txt"
    sentinel.write_text("legacy evidence\n")

    assert _run(cfg_path) == 0
    out = tmp_path / "outputs" / "audit" / "lc"
    for name in audit.CSV_FIELDS:
        assert (out / name).exists(), name
    decision = json.loads((out / "decision.json").read_text())
    assert decision["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert decision["verdict"] in {"GO", "GO_PENDING_FULL_RUN", "NO-GO"}
    assert [c["id"] for c in decision["checks"]] == list("abcdefghij")
    assert "g" in decision["pending_full_run"]
    assert (out / "SUMMARY.md").read_text().find(C.SAMPLE_STAMP) >= 0
    assert sentinel.read_text() == "legacy evidence\n"


def test_every_audit_csv_is_stamped(tmp_path, cfg, fixtures_root):
    clone = _fixture_cfg(tmp_path, cfg, fixtures_root)
    cfg_path = write_config(tmp_path / "config.yaml", clone)
    assert _run(cfg_path) == 0
    out = tmp_path / "outputs" / "audit" / "lc"
    for name in audit.CSV_FIELDS:
        lines = (out / name).read_text().splitlines()
        assert lines[0].split(",")[0] == "provenance", name
        assert lines[0].split(",")[1:] == audit.CSV_FIELDS[name], name
        assert all(C.SAMPLE_STAMP in line for line in lines[1:]), name


def test_pending_items_are_not_guessed(tmp_path, cfg, fixtures_root):
    clone = _fixture_cfg(tmp_path, cfg, fixtures_root)
    cfg_path = write_config(tmp_path / "config.yaml", clone)
    assert _run(cfg_path) == 0
    out = tmp_path / "outputs" / "audit" / "lc"
    rows = (out / "g_effort_counts.csv").read_text().splitlines()
    assert "PENDING FULL RUN" in rows[1]
    decision = json.loads((out / "decision.json").read_text())
    g = next(c for c in decision["checks"] if c["id"] == "g")
    assert g["verdict"] == "PENDING"
    j = next(c for c in decision["checks"] if c["id"] == "j")
    assert j["verdict"] == "PENDING"


def test_audit_is_deterministic(tmp_path, cfg, fixtures_root):
    clone = _fixture_cfg(tmp_path, cfg, fixtures_root)
    cfg_path = write_config(tmp_path / "config.yaml", clone)
    assert _run(cfg_path) == 0
    first = {name: (tmp_path / "outputs" / "audit" / "lc" / name).read_bytes()
             for name in audit.CSV_FIELDS}
    first_decision = json.loads(
        (tmp_path / "outputs" / "audit" / "lc" / "decision.json").read_text())
    assert _run(cfg_path) == 0
    for name in audit.CSV_FIELDS:
        assert (tmp_path / "outputs" / "audit" / "lc" / name).read_bytes() == first[name]
    second_decision = json.loads(
        (tmp_path / "outputs" / "audit" / "lc" / "decision.json").read_text())
    first_decision.pop("gate_evaluated_utc")
    second_decision.pop("gate_evaluated_utc")
    assert first_decision == second_decision


def test_new_pipeline_does_not_import_parked_code():
    src = Path(__file__).resolve().parents[1] / "src"
    for name in ("01_audit.py", "02_combine_ceiling.py", "00_thresholds.py",
                 "lc_common.py", "ceiling_core.py", "lc_efforts.py",
                 "lc_models.py", "synthetic_lc_fixture.py"):
        tree = ast.parse((src / name).read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not [m for m in imports if "legacy" in m], (name, imports)
