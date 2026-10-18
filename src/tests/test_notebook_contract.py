#!/usr/bin/env python3
"""Static contract test for ``notebooks/bdb27_full_run.ipynb`` (no execution).

The notebook is interpreted as JSON source: these assertions lock in the stale-code guard and the
honest §8 failure wording without running any stage or touching ``outputs/``.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NB_PATH = REPO / "notebooks" / "bdb27_full_run.ipynb"


def _cells() -> list[str]:
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    return ["".join(c.get("source", [])) for c in nb["cells"]]


def test_bootstrap_prints_checkout_header_and_runs_evaluate_checkout():
    boot = _cells()[4]
    assert "import run_diagnostics" in boot, "bootstrap must import run_diagnostics"
    assert "evaluate_checkout(REPO)" in boot, "bootstrap must evaluate the checkout"
    assert 'print(f"checkout : {line}")' in boot, "bootstrap must print the header lines"
    assert "git unavailable" in boot, "bootstrap must say when git is unavailable"
    assert "pipeline will NOT execute until the checkout is updated" in boot, boot


def test_pipeline_loop_is_gated_and_keeps_traceback_tail():
    pipe = _cells()[6]
    assert "CHECKOUT_BLOCKED" in pipe and "if CHECKOUT_BLOCKED" in pipe, \
        "the stage loop must be gated by the checkout guard"
    assert "RUN_RESULTS = []" in pipe, pipe[:400]
    assert "[-10:]" in pipe, "run_stage must keep the last 10 tail lines"
    assert "failure_lines" in pipe, "a failed stage must print its failure lines"
    assert "synthetic demo skipped — checkout guard blocked" in _cells()[8], "cell 8 must be gated"


def test_section_8_prints_checkout_header_and_stop_message():
    sec8 = _cells()[26]
    assert "SECTION 8" in sec8, "§8 marker missing"
    assert 'print(f"checkout : {line}")' in sec8, "§8 must print the checkout header"
    assert 'CHECKOUT["stop_message"]' in sec8, "§8 must surface the stop message"
    assert "NO RESULT" in sec8, sec8[:400]


def test_old_unconditional_data_supply_wording_is_gone():
    joined = "\n".join(_cells())
    assert "07_security_models needs the full game-tracking files" not in joined, \
        "the old unconditional data-supply remedy must be gone"
    assert "diagnose_missing_primary" in joined, "security_chain_issues must use the diagnosis helper"


def test_config_defaults_unchanged():
    cfg = _cells()[2]
    assert "CLEAN              = False" in cfg, "CLEAN default changed"
    assert 'CLEAN_CONFIRM      = ""' in cfg, "CLEAN_CONFIRM default changed"
    assert "FORCE_STAGES       = []" in cfg, "FORCE_STAGES default changed"


def test_notebook_writes_no_outputs_path():
    """No cell writes directly into outputs/ (stage code owns those paths; figures use FIGDIR)."""
    for cell in _cells():
        for line in cell.splitlines():
            if any(tok in line for tok in ("to_csv(", "write_text(", "write_bytes(", "savefig(")):
                assert "OUTPUTS" not in line, f"notebook writes into outputs/ directly: {line!r}"


# --------------------------------------------------------------------------- #
# D45: RUN_ONLY single-stage re-run is a first-class path
# --------------------------------------------------------------------------- #
def test_run_only_knob_exists_defaults_empty_and_is_documented():
    cfg = _cells()[2]
    assert "RUN_ONLY           = []" in cfg, "RUN_ONLY must default to [] (run every stage)"
    assert "assert isinstance(RUN_ONLY, (list, tuple)), RUN_ONLY" in cfg, cfg
    for cell in (1, 5):
        assert "RUN_ONLY" in _cells()[cell], f"§{'1' if cell == 1 else '3'} markdown must document RUN_ONLY"
    assert "RUN_ONLY=[\"07_security_models\"]" in _cells()[1], _cells()[1]


def test_run_only_filters_the_loop_without_editing_stage_order():
    pipe = _cells()[6]
    assert 'STAGE_ORDER = ["01_audit", "02_combine_envelope", "06_security", "00_thresholds",' in pipe, \
        "STAGE_ORDER must stay fixed; RUN_ONLY is the knob"
    assert "STAGES_TO_RUN = [s for s in STAGE_ORDER if (not RUN_ONLY_NORM or s in RUN_ONLY_NORM)]" in pipe, pipe
    assert "SKIPPED_STAGES = [s for s in STAGE_ORDER if s not in STAGES_TO_RUN]" in pipe, pipe
    assert "for name in STAGES_TO_RUN:" in pipe, "the loop must iterate the RUN_ONLY subset"
    assert "stage_status_on_disk" in pipe, "skipped stages must be reported from their own checkpoint"


def test_skipped_stage_status_comes_from_disk_not_from_a_failure_label():
    boot, pipe = _cells()[4], _cells()[6]
    assert "run_diagnostics.stage_status_on_disk(name, OUTPUTS)" in boot, \
        "stage_status_text must fall back to the on-disk checkpoint"
    assert "not run in this session (§3 must be executed first)" not in boot, \
        "a skipped stage must not read as 'not run' / failure"
    assert "SKIPPED - checkpoint" in pipe, pipe
    assert "never marked failed" in _cells()[2].lower(), _cells()[2]


def test_section_8_full_gate_is_provenance_driven():
    sec8 = _cells()[26]
    assert "run_diagnostics.headline_is_full(" in sec8, sec8[:600]
    gate = sec8.split("FULL = run_diagnostics.headline_is_full(", 1)[1].split("PREVIEW", 1)[0]
    assert "RUN_RESULTS" not in gate, "§8's FULL gate must not depend on which stages ran this session"
    assert "thr_prov_h" in gate and "sec_prov_h" in gate, gate


def test_section_8_header_prints_data_dir_and_covariate_quality():
    sec8 = _cells()[26]
    assert 'print(f"  data dir                 : {DATA}")' in sec8, sec8[:800]
    assert "covariate quality        :" in sec8, sec8[:2000]
    assert "n_effort_rows_excluded" in sec8, sec8[:2000]


if __name__ == "__main__":
    import sys
    sys.exit(0 if all(f() is None for f in (
        test_bootstrap_prints_checkout_header_and_runs_evaluate_checkout,
        test_pipeline_loop_is_gated_and_keeps_traceback_tail,
        test_section_8_prints_checkout_header_and_stop_message,
        test_old_unconditional_data_supply_wording_is_gone,
        test_config_defaults_unchanged,
        test_notebook_writes_no_outputs_path,
        test_run_only_knob_exists_defaults_empty_and_is_documented,
        test_run_only_filters_the_loop_without_editing_stage_order,
        test_skipped_stage_status_comes_from_disk_not_from_a_failure_label,
        test_section_8_full_gate_is_provenance_driven,
        test_section_8_header_prints_data_dir_and_covariate_quality,
    )) else 1)
