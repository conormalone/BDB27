"""Shared test setup: repo paths and a config that points at the synthetic fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "fixtures" / "synthetic"
AUDIT = ROOT / "outputs" / "audit"


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture()
def fixture_config(tmp_path) -> Path:
    """The real config, with raw/ parquet/ outputs/ redirected at a temp tree.

    Keeps one code path: the tests exercise the same loaders and the same
    config schema as a real run, only the roots differ.
    """
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    block = cfg["turn_coverage"]
    block["paths"]["raw"] = str(FIXTURE)
    block["paths"]["parquet"] = str(tmp_path / "parquet")
    block["paths"]["checkpoints"] = str(tmp_path / "checkpoints")
    block["paths"]["outputs"] = str(tmp_path / "outputs")
    block["paths"]["audit"] = str(tmp_path / "outputs" / "audit")
    block["paths"]["thresholds"] = str(tmp_path / "outputs" / "thresholds.json")
    block["paths"]["run_log"] = str(tmp_path / "outputs" / "run_log.csv")
    # the fixture is the "raw" tree for both settings
    block["sample_files"] = {k: v for k, v in block["files"].items()}
    out = tmp_path / "config.yaml"
    out.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return out


@pytest.fixture(scope="session")
def combine_csv() -> pd.DataFrame:
    return pd.read_csv(FIXTURE / "combine_tracking.csv", parse_dates=["time"])


def times_s(df: pd.DataFrame) -> "pd.Series":
    return (df.time - df.time.iloc[0]).dt.total_seconds()
