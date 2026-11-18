"""Pull-then-Run-All in the same kernel: the notebook reloads what is on disk (D209).

Conor's 2026-11-18 failure was not a bad file. It was a cached module object:
the kernel had imported ``bdb27.ambiguity_attribution`` before the pull, so
``RUN_STATUS_NAME`` (added to the file by the pull) did not exist on the object
the notebook was handed. These tests seed exactly that state and require the
boot cell to replace it from the file on disk, to say which file and checkout it
loaded, and to fail in plain English if the files are older than the notebook.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
NOTEBOOK = ROOT / "notebooks" / "bdb27_multi_candidate.ipynb"
sys.path.insert(0, str(SRC))

from bdb27 import notebook_boot as boot  # noqa: E402

MODULES = ("outcome_test", "geometric_match", "ambiguity_attribution")
AA_FILE = (SRC / "bdb27" / "ambiguity_attribution.py").resolve()

#: The notebook's own dependency list. Kept here as a literal on purpose: a
#: test that imports the list from the notebook cannot catch a list that was
#: silently emptied.
REQUIRED = {
    "ambiguity_attribution": [
        "RUN_STATUS_NAME", "write_run_status", "run_status_header",
        "note_run_status", "tracking_status", "tracking_banner",
        "ARTEFACT_TABLES", "RANDOM_REPS", "SPECS", "PAIRING_RULES",
        "connect", "funnel", "build_frame", "analysis_frame", "identified_set",
        "all_candidates_frame", "attribution_pipeline",
        "single_candidate_geometry", "candidate_distance_table",
        "within_radius_distribution", "margin_distribution", "variant_fit",
        "nearest_variant", "multi_turn_table", "composition",
        "random_assignments", "alignment_counts", "agreement",
        "write_artefacts", "write_manifest",
    ],
    "outcome_test": ["TURN_KEY"],
    "geometric_match": ["candidate_frame", "recover_break_frames"],
}


@pytest.fixture()
def restored_import_state():
    """Snapshot and restore everything the reload touches, so tests cannot leak."""
    saved_modules = {k: v for k, v in sys.modules.items()
                     if k == "bdb27" or k.startswith("bdb27.")}
    saved_path = list(sys.path)
    try:
        yield
    finally:
        for key in [k for k in sys.modules
                    if k == "bdb27" or k.startswith("bdb27.")]:
            del sys.modules[key]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def code_cells() -> list[str]:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


# ---------------------------------------------------------------- the defect
def test_a_stale_module_in_sys_modules_is_replaced_by_the_file_on_disk(
        restored_import_state):
    """The 07:17 module object, mutated to lack the symbol the pull added."""
    stale = types.ModuleType("bdb27.ambiguity_attribution")
    stale.__file__ = "/nowhere/stale/ambiguity_attribution.py"
    stale.OLD_MARKER = True
    sys.modules["bdb27.ambiguity_attribution"] = stale
    assert not hasattr(stale, "RUN_STATUS_NAME")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        modules = boot.fresh_import(SRC, MODULES, root=ROOT)

    aa = modules["ambiguity_attribution"]
    assert aa is not stale, "the cached module object was reused"
    assert hasattr(aa, "RUN_STATUS_NAME"), "the file on disk was not read"
    assert Path(aa.__file__).resolve() == AA_FILE
    assert not hasattr(aa, "OLD_MARKER")

    out = buf.getvalue()
    assert "loaded" in out
    assert str(AA_FILE) in out
    assert "checkout" in out
    assert "nothing was cached" not in out          # it did drop the stale entry


def test_a_missing_symbol_fails_with_the_file_and_an_older_checkout_message():
    """A truly old checkout gets a sentence, not a bare AttributeError."""
    fake = types.ModuleType("bdb27.ambiguity_attribution")
    fake.__file__ = str(AA_FILE)
    with pytest.raises(RuntimeError) as excinfo:
        boot.require_symbols({"ambiguity_attribution": fake},
                             {"ambiguity_attribution": ["RUN_STATUS_NAME"]},
                             SRC, root=ROOT)
    assert not isinstance(excinfo.value, AttributeError)
    message = str(excinfo.value)
    assert "IMPORT CHECK FAILED" in message
    assert str(AA_FILE) in message
    assert "RUN_STATUS_NAME" in message
    assert "OLDER THAN THE NOTEBOOK" in message
    assert "press Run All again" in message


def test_the_real_files_pass_the_check(restored_import_state):
    modules = boot.fresh_import(SRC, MODULES, root=ROOT, verbose=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        boot.require_symbols(modules, REQUIRED, SRC, root=ROOT)
    assert "every symbol the notebook uses is present" in buf.getvalue()


def test_the_checkout_label_is_read_from_git_without_a_subprocess():
    label = boot.checkout_label(ROOT)
    assert "unknown" not in label
    assert "@" in label


# ------------------------------------------------------- the notebook itself
def test_the_boot_cell_reports_file_and_checkout_and_binds_a_fresh_module(
        tmp_path, restored_import_state):
    """Run the config cell and the boot cell with a poisoned kernel, as Jupyter would."""
    cells = code_cells()
    config, boot_cell = cells[0], cells[1]
    assert "# ---- CONFIG" in config
    assert "BOOT_FILE" in boot_cell

    stale = types.ModuleType("bdb27.ambiguity_attribution")
    stale.__file__ = "/nowhere/stale/ambiguity_attribution.py"
    sys.modules["bdb27.ambiguity_attribution"] = stale

    config = re.sub(r"^OUT_DIR\s*=.*$", f"OUT_DIR = {str(tmp_path)!r}",
                    config, flags=re.M)
    ns: dict = {"__name__": "__main__"}
    buf = io.StringIO()
    previous = Path.cwd()
    os.chdir(ROOT)
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(config, "<config>", "exec"), ns)   # noqa: S102
            exec(compile(boot_cell, "<boot>", "exec"), ns)  # noqa: S102
    finally:
        os.chdir(previous)

    assert ns["aa"] is not stale
    assert hasattr(ns["aa"], "RUN_STATUS_NAME")
    assert Path(ns["aa"].__file__).resolve() == AA_FILE

    out = buf.getvalue()
    for token in ("loaded", "checkout", str(AA_FILE),
                  "bdb27.outcome_test", "bdb27.geometric_match",
                  "import check"):
        assert token in out, token


def test_the_boot_cell_does_not_import_the_package_the_old_way():
    """A `from bdb27 import ...` is the stale-object path; it must not come back."""
    cells = code_cells()
    joined = "\n".join(cells)
    assert "from bdb27 import ambiguity_attribution as aa" not in joined
    assert "from bdb27 import outcome_test as ot" not in joined

    boot_cell = cells[1]
    call = boot_cell.index("boot.fresh_import(")
    assert call < boot_cell.index("aa.")
    import_block = boot_cell[call:call + 300]
    for name in MODULES:
        assert f'"{name}"' in import_block, name
    assert "boot.require_symbols(" in boot_cell
    assert boot_cell.index("boot.require_symbols(") > call
