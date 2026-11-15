"""Contract tests for ``notebooks/bdb27_geometric_attribution.ipynb``.

The notebook is Conor's interface: Run All, no CLI, on a Windows laptop. These
tests are static (they read the .ipynb JSON and do not execute it) and pin the
promises the spec makes.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_geometric_attribution.ipynb"
ENCODED = {"to_csv", "read_csv", "read_text", "write_text"}


def notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_cells() -> list[str]:
    return ["".join(c["source"]) for c in notebook()["cells"]
            if c["cell_type"] == "code"]


def all_source() -> str:
    return "\n".join("".join(c["source"]) for c in notebook()["cells"])


def test_committed_unexecuted():
    nb = notebook()
    assert nb["cells"], "notebook is empty"
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            assert cell.get("outputs") == [], "the committed notebook carries outputs"
            assert cell.get("execution_count") is None


def test_config_cell_is_first_code_and_defaults_to_the_full_run():
    src = code_cells()[0]
    assert "TRACKING" in src and "OUT_DIR" in src and "CHUNK_ROWS" in src
    assert "ALLOW_SAMPLE_TRACKING = False" in src, (
        "the sample bypass must be off by default")
    assert "RESUME = True" in src


def test_notebook_uses_the_shared_module_and_the_shared_estimator():
    src = all_source()
    assert "from bdb27 import geometric_match, outcome_test" in src
    assert "outcome_test.run_all(" in src
    assert "outcome_test.verdict(" in src
    assert "geometric_match.match_turns(" in src
    assert "geometric_match.compare_elimination(" in src


def test_it_reports_the_misattribution_rate_before_it_fits_anything():
    src = all_source()
    assert src.index("compare_elimination(") < src.index("outcome_test.run_all("), (
        "the misattribution rate must be computed before the estimator")


def test_final_cell_prints_the_plain_english_verdict():
    last = code_cells()[-1]
    assert "plain_english_verdict" in last
    assert "does not upgrade a null" in last


def test_every_text_io_call_declares_utf8():
    bad = []
    for i, src in enumerate(code_cells()):
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in ENCODED:
                continue
            if not any(k.arg == "encoding" and getattr(k.value, "value", None) == "utf-8"
                       for k in node.keywords):
                bad.append(f"cell {i}:{node.lineno} {name}(...) without encoding='utf-8'")
    assert not bad, bad


def test_no_posix_only_or_platform_specific_code():
    src = all_source()
    for banned in ("import resource", "import psutil", "import fcntl",
                   "os.uname", "sys.platform"):
        assert banned not in src, f"{banned} is not portable to the Windows laptop"


def test_no_cli_instructions_for_the_user():
    src = all_source().lower()
    for banned in ("python scripts/", "pip install", "jupyter nbconvert", "bash "):
        assert banned not in src, f"the notebook tells Conor to use a shell: {banned}"


def test_the_inference_label_is_on_the_surface():
    assert "INFERENCE" in all_source()
    assert "no opponent identifier" in all_source()


def test_it_reports_the_preflight_and_has_no_arbitrary_stop():
    src = all_source()
    assert "1,048,575" in src
    assert "preflight_report(" in src
    assert "pre-flight stop" not in src
    assert "empty extraction" not in src
    assert "TRACKING DOES NOT OBSERVE OPPONENTS ON MOST PLAYS" not in src
    assert "miss['label']" in src, "the misattribution label must come from the module"
    assert "DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE" in src
