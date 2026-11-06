"""Layout and run order: the dispatcher exists, stubs fail loudly, nothing
silently succeeds.
"""

from __future__ import annotations

import inspect
import json

import pytest

from conftest import SRC, load_stage
import common as C
import run as RUN


EXPECTED_ORDER = ["01_audit", "02_combine_physical_time", "03_game_snaps",
                  "03b_reliability", "04_models", "05_validate"]

# Stages that exist only as layout stubs. 03b is an added evidence stage that
# sits between Phase 3 and Phase 4 (decisions.md D107); 04_models was
# implemented in the 2026-11-05 Phase 4 cycle; 05_validate was implemented in
# the 2026-11-06 Phase 5 cycle (D119). Nothing is a stub today, but the
# parametrised test below stays so that a future stub still trips it.
STUBS: list[str] = []


def test_run_order_matches_the_spec():
    assert RUN.STAGES == EXPECTED_ORDER


def test_every_stage_file_exists():
    for s in EXPECTED_ORDER:
        assert (SRC / f"{s}.py").exists(), s


def test_list_runs():
    assert RUN.main(["--list"]) == 0


def test_unknown_stage_exits_2(capsys):
    assert RUN.main(["99_nope", "--mode", "sample"]) == 2
    assert "unknown stage" in capsys.readouterr().err


def test_missing_stage_argument_exits_2(capsys):
    assert RUN.main([]) == 2


@pytest.mark.parametrize("stage", STUBS)
def test_stubs_fail_loudly_and_write_nothing(stage, capsys, cfg):
    mod = load_stage(stage)
    before = C.checkpoint_path(cfg, stage, "sample")
    code = mod.main(["--mode", "sample"])
    assert code == 3
    err = capsys.readouterr().err
    assert "STAGE NOT IMPLEMENTED" in err
    assert not before.exists(), "a stub must not create a checkpoint"


def test_phase3_is_no_longer_a_stub():
    mod = load_stage("03_game_snaps")
    assert mod.STAGE == "03_game_snaps"
    assert callable(mod.main)
    assert hasattr(mod, "measure_rep")
    assert hasattr(mod, "assert_output_schema")
    assert len(mod.SNAP_FIELDS) == len(mod.SNAP_COLUMNS)


def test_phase2_is_no_longer_a_stub():
    mod = load_stage("02_combine_physical_time")
    assert mod.STAGE == "02_combine_physical_time"
    assert callable(mod.main)
    assert hasattr(mod, "measure_attempt")
    assert hasattr(mod, "pool_players")


def test_phase4_is_no_longer_a_stub():
    mod = load_stage("04_models")
    assert mod.STAGE == "04_models"
    assert callable(mod.main)
    assert hasattr(mod, "attach_physical")
    assert hasattr(mod, "variance_rows")
    assert hasattr(mod, "SNAP_COLUMNS")


def test_phase5_is_no_longer_a_stub():
    mod = load_stage("05_validate")
    assert mod.STAGE == "05_validate"
    assert callable(mod.main)
    assert hasattr(mod, "run_checks")
    assert hasattr(mod, "load_frames")
    # It must not be routed through the not-implemented helper.
    assert "fail_not_implemented" not in inspect.getsource(mod.main)


@pytest.mark.parametrize("stage", EXPECTED_ORDER[1:])
def test_stub_stage_names_are_the_filenames(stage):
    mod = load_stage(stage)
    assert mod.STAGE == stage


def test_audit_stage_exposes_main():
    mod = load_stage("01_audit")
    assert callable(mod.main)
    assert mod.STAGE == "01_audit"


def test_sample_mode_never_touches_full_tracking(cfg):
    """FULL inputs do not exist here; sample mode must not need them."""
    assert not all(p.exists() for p in C.tracking_sources(cfg, "full"))


def test_full_mode_is_refused_cleanly_by_the_cli(capsys):
    """No traceback: the dispatcher turns a gate refusal into exit 4."""
    code = RUN.main(["01_audit", "--mode", "full"])
    err = capsys.readouterr().err
    assert code == 4
    assert err.startswith("REFUSED:")
    assert "Traceback" not in err


def test_gate_refusal_is_a_runtime_error_subclass():
    assert issubclass(C.GateRefused, RuntimeError)
