"""Shared pytest fixtures for the BDB27 burst-vs-jump suite.

The suite is designed to run on a machine with the real data OR without it:
tests that need the real files skip with an explicit reason, never silently pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import common as C  # noqa: E402
import synthetic  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return C.load_config(ROOT / "config.yaml")


@pytest.fixture(scope="session")
def con(cfg):
    c = C.duckdb_connect(cfg)
    yield c
    c.close()


@pytest.fixture(scope="session")
def real_data(cfg):
    """Skip unless every raw file the audit reads is actually present."""
    missing = []
    for key in ("games", "players", "combine_results", "combine_tracking",
                "player_play", "game_tracking_sample"):
        if not all(p.exists() for p in C.source_path(cfg, key)):
            missing.append(key)
    if missing:
        pytest.skip(f"raw data not present: {missing}")
    return True


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    """A complete synthetic dataset, built once per session."""
    d = tmp_path_factory.mktemp("synthetic")
    synthetic.build_fixture(d, seed=11, n_players=10, snaps_per=20)
    return d


@pytest.fixture()
def cfg_for_fixture(cfg, fixture_dir):
    """A config clone whose raw path points at the synthetic fixture."""
    import copy
    c = copy.deepcopy(cfg)
    b = C.block(c)
    b["paths"]["raw"] = str(fixture_dir)
    b["paths"]["interim"] = str(fixture_dir / "interim")
    b["paths"]["parquet"] = str(fixture_dir / "interim" / "parquet")
    b["paths"]["checkpoints"] = str(fixture_dir / "interim" / "checkpoints")
    b["paths"]["tmp"] = str(fixture_dir / "interim" / "tmp")
    b["paths"]["outputs"] = str(fixture_dir / "outputs")
    b["paths"]["phase2"] = str(fixture_dir / "outputs" / "phase2")
    b["paths"]["calibration"] = str(fixture_dir / "outputs" / "calibration.json")
    b["paths"]["run_log"] = str(fixture_dir / "outputs" / "run_log.csv")
    b["files"]["game_tracking_sample"] = "game_tracking.csv"
    return c


def load_stage(name: str):
    """Import src/<name>.py (filenames start with digits, so importlib)."""
    import importlib.util
    path = SRC / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"stage_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def audit_mod():
    return load_stage("01_audit")


@pytest.fixture(scope="session")
def audit_run(cfg, real_data):
    """Run the real Phase 1 stage once; yield its output directory."""
    mod = load_stage("01_audit")
    assert mod.main(["--mode", "sample", "--force"]) == 0
    return C.paths(cfg, "sample")["outputs"] / "audit"
