"""Stage contracts: loud stubs, honest full-mode refusal, dispatcher order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run(stage: str, *args, cwd=ROOT):
    import subprocess
    import sys
    return subprocess.run([sys.executable, str(SRC / f"{stage}.py"), *args],
                          capture_output=True, text=True, cwd=str(cwd))


# 03_envelope, 04_demand_coverage and 06_figures are implemented; they are
# covered by tests/test_envelope_stage.py, tests/test_demand_coverage_stage.py
# and tests/test_figures_stage.py.
@pytest.mark.parametrize("stage", ["05_validate"])
def test_stub_is_loud_and_writes_nothing(stage, tmp_path):
    before = set((ROOT / "outputs").rglob("*"))
    ckpt = ROOT / "data" / "interim" / "checkpoints"
    ckpt_before = set(ckpt.glob("*")) if ckpt.exists() else set()
    res = run(stage, "--mode", "sample")
    assert res.returncode != 0
    assert "STAGE NOT IMPLEMENTED" in res.stderr
    after = set((ROOT / "outputs").rglob("*"))
    assert before == after, f"{stage} wrote {after - before}"
    ckpt_after = set(ckpt.glob("*")) if ckpt.exists() else set()
    assert ckpt_before == ckpt_after, f"{stage} created a checkpoint"


def test_full_mode_refuses_and_names_missing_inputs(tmp_path):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["turn_coverage"]["paths"]["outputs"] = str(tmp_path / "outputs")
    cfg["turn_coverage"]["paths"]["thresholds"] = str(tmp_path / "outputs" / "thresholds.json")
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text(yaml.safe_dump(cfg, sort_keys=False))
    res = run("00_thresholds", "--mode", "full", "--config", str(cfgp))
    assert res.returncode != 0
    assert "required inputs are absent" in res.stderr + res.stdout
    assert "game_tracking.csv" in res.stderr + res.stdout
    assert not (tmp_path / "outputs" / "thresholds.json").exists()


def test_dispatcher_stage_list_is_exactly_the_spec_order():
    import sys
    sys.path.insert(0, str(SRC))
    import run as runner
    assert runner.STAGES == ["00_thresholds", "01_audit", "02_turns", "03_envelope",
                             "04_demand_coverage", "05_validate", "06_figures"]


def test_dispatcher_rejects_unknown_stage():
    res = run("run", "99_nope", "--mode", "sample")
    assert res.returncode == 2
    assert "unknown stage" in res.stderr


def test_thresholds_sample_provenance(tmp_path):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["turn_coverage"]["paths"]["outputs"] = str(tmp_path / "outputs")
    cfg["turn_coverage"]["paths"]["thresholds"] = str(tmp_path / "outputs" / "thresholds.json")
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text(yaml.safe_dump(cfg, sort_keys=False))
    res = run("00_thresholds", "--mode", "sample", "--config", str(cfgp))
    assert res.returncode == 0, res.stderr
    payload = json.loads((tmp_path / "outputs" / "thresholds.json").read_text())
    assert payload["provenance"] == "PLACEHOLDER_SAMPLE"
    assert payload["smoothing"]["primary"] in [c["name"] for c in cfg["turn_coverage"]["smoothing"]["candidates"]]
    assert payload["bins"]["entry_speed"] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0]


def test_thresholds_exist_and_are_not_marked_frozen():
    p = ROOT / "outputs" / "thresholds.json"
    assert p.exists()
    d = json.loads(p.read_text())
    assert d["provenance"] == "PLACEHOLDER_SAMPLE"
    assert d["frozen"] is False
