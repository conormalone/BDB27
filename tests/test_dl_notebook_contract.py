"""Contract tests for the current-task DL notebook (no execution).

The notebook is Conor's only interface; these tests pin the properties that make one Run All
reproduce the documented DL chain and keep sample output out of the headline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NB_PATH = ROOT / "notebooks" / "bdb27_dl_full_run.ipynb"

DL_CHAIN = ["00_thresholds", "01_audit", "02_combine_ceiling", "03_game_snaps", "04_models"]


@pytest.fixture(scope="module")
def notebook():
    assert NB_PATH.exists(), "the DL runnable notebook is missing"
    return json.loads(NB_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def code_text(notebook):
    parts = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            src = cell["source"]
            parts.append("".join(src) if isinstance(src, list) else src)
    return "\n".join(parts)


def test_committed_notebook_has_no_executed_outputs(notebook):
    for i, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs") == [], f"cell {i} has committed outputs"
        assert cell.get("execution_count") is None, f"cell {i} has an execution count"


def test_dl_stage_order_is_the_task_chain(code_text):
    match = re.search(r"^DL_STAGE_ORDER\s*=\s*\[(.*?)\]", code_text, re.M | re.S)
    assert match, "DL_STAGE_ORDER not found"
    assert re.findall(r'"([^"]+)"', match.group(1)) == DL_CHAIN


def test_validate_is_not_in_the_order_and_is_reported_not_run(code_text):
    match = re.search(r"^DL_STAGE_ORDER\s*=\s*\[(.*?)\]", code_text, re.M | re.S)
    assert "05_validate" not in re.findall(r'"([^"]+)"', match.group(1))
    assert "stage_05_validate.py is not built" in code_text


def test_config_cell_has_every_required_knob_and_assertions(code_text):
    for knob in ("MODE", "DATA_DIR", "FORCE", "FORCE_STAGES", "RUN_ONLY",
                 "CLEAN", "CLEAN_CONFIRM", "SHOW_FIGURES", "FIG_DPI"):
        assert re.search(rf"^{knob}\s*=", code_text, re.M), knob
    assert 'CLEAN_CONFIRM == "DELETE"' in code_text
    assert 'assert MODE in ("sample", "full")' in code_text
    assert "assert isinstance(FORCE, bool)" in code_text


def test_run_uses_stage_runner_and_the_scratch_config(code_text):
    assert "stage_runner.run_stage" in code_text
    assert "helpers=DL_HELPERS" in code_text
    assert '"--config", str(RUN_CFG_PATH)' in code_text
    assert "outputs_root=OUTPUTS" in code_text


def test_missing_full_tracking_is_pending_not_failed(code_text):
    assert "FULL_TRACKING_MISSING" in code_text
    assert "FULL_DATA_STAGES" in code_text
    assert "PENDING FULL RUN" in code_text
    # the full-data stages must be guarded before they are invoked
    assert "not FULL_TRACKING_READY" in code_text


def test_headline_withholds_sample_mode_and_never_guesses(code_text):
    assert "WITHHELD — MODE='sample'" in code_text
    assert "NOT RUN — " in code_text
    assert "never filled with a guess or a zero" in code_text


def test_diagnostic_figures_require_full_provenance(code_text):
    assert "figure 1: SKIPPED — snaps provenance is" in code_text
    assert "figure 2: SKIPPED — ceiling/snaps provenance is not FULL" in code_text
