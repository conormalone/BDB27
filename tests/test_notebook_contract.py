"""Notebook contract: the notebook is the interface, and it may not lie.

Four things must stay true whatever the pipeline does:

  * the stage order is the spec's order (and matches ``src/run.py``);
  * the committed notebook carries no baked outputs;
  * a missing gate artefact renders ``NOT RUN``, never a silent blank;
  * a stub is never scored as a pass.

Plus the full-mode preflight: a missing full tracking file is named and the run is
blocked, with no silent fallback to the sample.
"""

from __future__ import annotations

import ast
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_full_run.ipynb"

#: TASK.md "Layout and run order".
SPEC_ORDER = ["00_thresholds", "01_audit", "02_turns", "03_envelope",
              "04_demand_coverage", "05_validate", "06_figures"]

SEASON_FILES = ("game_tracking_2023.csv", "game_tracking_2024.csv", "game_tracking_2025.csv")


@pytest.fixture(scope="module")
def notebook():
    assert NOTEBOOK.exists(), f"notebook missing: {NOTEBOOK}"
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def source_of(notebook, marker):
    """The source of the first code cell containing ``marker``."""
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        if marker in source:
            return source
    raise AssertionError(f"no code cell contains {marker!r}")


def exec_cell(source, **globals_in):
    namespace = dict(globals_in)
    exec(compile(source, "<notebook cell>", "exec"), namespace)  # noqa: S102 - our own notebook
    return namespace


def test_stage_order_matches_the_spec(notebook):
    source = source_of(notebook, "STAGE_ORDER = [")
    order = None
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "STAGE_ORDER" for t in node.targets):
            order = ast.literal_eval(node.value)
    assert order == SPEC_ORDER, f"notebook stage order is {order}"

    sys.path.insert(0, str(ROOT / "src"))
    import run as runner
    assert order == runner.STAGES, "notebook order drifted from src/run.py"


def test_committed_notebook_has_no_baked_outputs(notebook):
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs", []) == [], f"code cell {index} carries baked outputs"
        assert cell.get("execution_count") is None, f"code cell {index} carries an execution count"


def test_missing_gate_artefact_renders_not_run(notebook, tmp_path):
    source = source_of(notebook, "def render_headline")
    out = io.StringIO()
    with redirect_stdout(out):
        exec_cell(source,
                  Path=Path,
                  GATE_JSON=tmp_path / "gate.json",
                  AUDIT_CSV=tmp_path / "audit_items.csv")
    text = out.getvalue()

    assert "NOT RUN — gate.json absent" in text
    for field in ("provenance", "verdict", "because", "primary_group",
                  "recommended_smoother"):
        assert f"NOT RUN — {field} absent" in text, f"{field} did not render NOT RUN"
    for number in range(1, 10):
        assert f"G{number}" in text
        assert "NOT RUN — condition absent" in text
    for letter in "abcdefghijklm":
        assert f"{letter} : NOT RUN — item absent" in text
    # An absent artefact must never be dressed up as a result banner.
    assert "NOT A RESULT" not in text


def test_a_stub_is_never_a_pass(notebook):
    namespace = exec_cell(source_of(notebook, "def classify_outcome"))
    classify = namespace["classify_outcome"]

    stub = classify(2, "[02_turns] STAGE NOT IMPLEMENTED — Phase 2 is not built yet.", False)
    assert stub["passed"] is False
    assert stub["stub"] is True
    assert stub["status"] == "NOT IMPLEMENTED (stub, exit 2)"

    reused = classify(0, "00_thresholds: /tmp/x/thresholds.json exists (use --force)", False)
    assert reused["passed"] is True
    assert reused["reused"] is True
    assert "checkpoint reused" in reused["status"]

    wrote = classify(0, "01_audit: 13 items", True)
    assert wrote["passed"] is True and wrote["reused"] is False

    refused = classify(2, "REFUSED: required inputs are absent", False)
    assert refused["passed"] is False and refused["stub"] is False
    assert "REFUSED" in refused["status"]

    failed = classify(1, "Traceback (most recent call last): ...", True)
    assert failed["passed"] is False and failed["stub"] is False
    assert "FAILED" in failed["status"]


def test_full_mode_preflight_names_the_missing_tracking(notebook, tmp_path):
    namespace = exec_cell(source_of(notebook, "def full_tracking_preflight"))
    preflight = namespace["full_tracking_preflight"]

    blocked, message = preflight(tmp_path, "game_tracking.csv")
    assert blocked is True
    assert "PENDING FULL RUN" in message
    for name in SEASON_FILES:
        assert name in message, f"{name} was not named"
    assert "game_tracking.csv" in message
    assert "sample data is NOT substituted" in message

    (tmp_path / "game_tracking.csv").write_text("", encoding="utf-8")
    blocked, message = preflight(tmp_path, "game_tracking.csv")
    assert blocked is False
    assert message == ""
