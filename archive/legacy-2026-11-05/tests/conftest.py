"""Shared pytest fixtures for the new BDB27 suite."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import lc_common as C  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return C.load_config()


@pytest.fixture
def tmp_cfg(tmp_path, cfg):
    """Config clone whose paths all live under ``tmp_path``."""
    clone = copy.deepcopy(cfg)
    block = clone["bdb27_lc"]
    block["paths"].update({
        "raw": str(tmp_path / "raw"),
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
    # tmp_path lives on the root filesystem; keep the guard meaningful but
    # satisfiable for tests that exercise the real pipeline plumbing.
    block["runtime"]["min_free_mb_root"] = 1
    block["runtime"]["min_free_mb_interim"] = 1
    block["runtime"]["temp_directory"] = str(tmp_path / "interim" / "tmp")
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    return clone


def write_config(path: Path, cfg: dict) -> Path:
    """Persist a config clone (comments are not needed by the scripts)."""
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def load_script(name: str):
    """Import a numeric-named script from ``src/`` as a module."""
    spec = importlib.util.spec_from_file_location(f"bdb27_script_{name}",
                                                  SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
