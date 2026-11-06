"""The deliverable notebook's contract.

These tests read the committed ``.ipynb`` as JSON (no Jupyter dependency) and
exercise the headline renderer directly. They exist because on 2026-11-05 the
repo's notebook folder described notebooks that had been archived: the interface
lied about itself (D118). The contract is:

* the notebook exists, is valid JSON, and has **no baked outputs**;
* the stage order in the code is exactly ``TASK.md``'s order, every stage
  including ``05_validate`` is inside the executed chain, and an unimplemented
  stage would still be reported as NOT BUILT rather than counted as a pass;
* the configuration cell exposes exactly the documented defaults;
* stages are reloaded from disk before each call (a ``git pull`` cannot leave
  stale code in the kernel);
* a missing ``decomposition.json`` prints ``NOT RUN`` — never a fabricated zero;
* sample-provenance numbers are printed only under the loud ``NOT A RESULT``
  banner, and a zero share is reported as zero, not replaced by the naive rung;
* the Phase 5 validation block reads ``validation.json``, prints every
  synthetic ``detector_fired`` row, and says a boundary-bound permutation is
  uninformative rather than a pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import notebook_report as R

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_full_run.ipynb"

EXPECTED_STAGE_ORDER = ["01_audit", "02_combine_physical_time", "03_game_snaps",
                        "03b_reliability", "04_models", "05_validate"]

#: Phase 5 was implemented on 2026-11-06 (D119); there is no stub stage today.
#: The contract that an unimplemented stage is *reported*, never counted as a
#: pass, is kept by asserting the rc == 3 reporting path still exists.
STUB_STATUS_TEXT = "NOT BUILT"

SAMPLE_STAMP = "PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT"
SAMPLE_BANNER = "UNVALIDATED SAMPLE OUTPUT — NOT A RESULT"


@pytest.fixture(scope="module")
def notebook() -> dict:
    assert NOTEBOOK.exists(), f"deliverable notebook missing: {NOTEBOOK}"
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_cells(notebook: dict) -> list[dict]:
    return [c for c in notebook["cells"] if c.get("cell_type") == "code"]


def all_source(notebook: dict) -> str:
    return "\n\n".join("".join(c.get("source", [])) for c in notebook["cells"])


# --------------------------------------------------------------------------- #
# the file and its committed state
# --------------------------------------------------------------------------- #
def test_notebook_is_valid_json_with_cells(notebook):
    assert notebook.get("cells"), "notebook has no cells"
    assert notebook["metadata"].get("kernelspec", {}).get("name") == "python3"


def test_no_baked_outputs(notebook):
    """The committed notebook must be code only: zero outputs, zero execution counts."""
    for index, cell in enumerate(code_cells(notebook)):
        assert cell.get("outputs") == [], f"code cell {index} carries baked output"
        assert cell.get("execution_count") is None, f"code cell {index} carries an execution count"


def test_every_code_cell_parses(notebook):
    """A syntax error must fail here, not in the kernel at Run All."""
    import ast

    for index, cell in enumerate(code_cells(notebook)):
        ast.parse("".join(cell["source"]), filename=f"cell-{index}")


# --------------------------------------------------------------------------- #
# configuration and stage order
# --------------------------------------------------------------------------- #
def test_config_cell_has_the_documented_defaults(notebook):
    source = "".join(code_cells(notebook)[0]["source"])
    assert re.search(r'^MODE\s*=\s*"sample"', source, re.M)
    assert re.search(r"^DATA_DIR\s*=\s*None", source, re.M)
    assert re.search(r"^FORCE_STAGES\s*=\s*\[\]", source, re.M)
    assert re.search(r"^CLEAN\s*=\s*False", source, re.M)
    assert re.search(r'^CLEAN_CONFIRM\s*=\s*""', source, re.M)


def test_stage_order_matches_the_spec(notebook):
    source = all_source(notebook)
    match = re.search(r"STAGE_ORDER\s*=\s*\[(.*?)\]", source, re.S)
    assert match, "STAGE_ORDER is not defined in the notebook"
    names = re.findall(r'"([^"]+)"', match.group(1))
    assert names == EXPECTED_STAGE_ORDER


def test_unimplemented_stage_is_reported_not_run(notebook):
    """A stage that exits 3 must be reported as NOT BUILT, never as a pass."""
    source = all_source(notebook)
    assert STUB_STATUS_TEXT in source
    assert re.search(r'\["rc"\]\s*==\s*3', source)
    # 05_validate is real now, so it must be inside the executable chain.
    match = re.search(r"STAGE_ORDER\s*=\s*\[(.*?)\]", source, re.S)
    assert match, "STAGE_ORDER is not defined in the notebook"
    assert "05_validate" in match.group(1)
    # ...and the old stub machinery must be gone.
    assert "STUB_STAGE" not in source
    assert "NOT BUILT (stub, exits" not in source


def test_stage_modules_are_reloaded_from_disk(notebook):
    """The D46 trap: import_module alone would reuse a pre-pull module object."""
    source = all_source(notebook)
    assert "spec_from_file_location" in source
    assert "importlib.reload" in source
    assert "_purge_bytecode" in source
    assert "reload_helpers()" in source


def test_full_mode_names_missing_tracking_and_continues(notebook):
    source = all_source(notebook)
    assert "FULL_TRACKING_MISSING" in source
    assert "PENDING FULL RUN" in source
    assert "FULL_TRACKING_STAGES" in source


# --------------------------------------------------------------------------- #
# the headline renderer
# --------------------------------------------------------------------------- #
def _artifact(provenance=SAMPLE_STAMP, **overrides) -> dict:
    group = {
        "status": "OK",
        "n_reps": 100, "n_players": 12, "n_team_games": 40,
        "b": 1.25,
        "share_phys": 0.0, "share_u": 1.0,
        "share_phys_total": 0.0, "share_u_total": 0.20,
        "within_player_remainder_total": 0.80,
        "jump_sd_ms": 55.0,
        "rung_used": 0,
        "bootstrap": {
            "b_ci95": [0.70, 1.90],
            "share_phys_ci95": [0.0, 0.0],
            "share_u_ci95": [1.0, 1.0],
            "within_player_remainder_total_ci95": [0.70, 0.90],
        },
        "reliability": {"split_half": {"r_half": 0.80, "spearman_brown": 0.89, "n_players": 12},
                        "model_based": {"reliability": 0.75}},
        "agreement": {"u_vs_onset_latency": {"r": 0.60, "n_players": 12},
                      "phys_vs_onset_to_cross": {"r": 0.55, "n_players": 12}},
        "year_to_year": {"status": "INSUFFICIENT_SEASONS", "n_pairs": 0},
        "boundary": {"sigma_u_zero": False, "between_phys_var_zero": True},
        "naive_rung1": {"share_phys": 0.03, "share_u": 0.97},
    }
    payload = {"provenance": provenance,
               "estimator": {"name": "EM", "measurement_error": "regression calibration",
                             "intervals": "player-cluster bootstrap"},
               "resolution": {"provenance": "FULL",
                              "smallest_detectable_jump_sd_ms_at_20_reps": 54.0,
                              "smallest_detectable_jump_sd_ms_best_case": 52.0,
                              "statement": "Resolution statement, not a threshold."},
               "phys_interpolation_status": {"OK": 90, "NO_CURVE": 10}}
    payload.update({"pooled": dict(group), "edge": dict(group), "interior": dict(group)})
    payload.update(overrides)
    return payload


def test_missing_decomposition_is_not_run(tmp_path):
    text = R.render_headline(tmp_path, "sample")
    assert "NOT RUN — outputs/phase4/decomposition.json absent" in text
    assert "0.000" not in text                      # no invented zero
    assert "UNVALIDATED SAMPLE OUTPUT — NOT A RESULT" not in text


def test_malformed_decomposition_is_not_run(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    (path / "decomposition.json").write_text("{not json", encoding="utf-8")
    assert "NOT RUN — outputs/phase4/decomposition.json absent" in R.render_headline(tmp_path, "sample")


def test_sample_numbers_sit_under_the_banner(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    (path / "decomposition.json").write_text(json.dumps(_artifact()), encoding="utf-8")
    text = R.render_headline(tmp_path, "sample")
    assert text.index(SAMPLE_BANNER) < text.index("POOLED")
    assert "SAMPLE DIAGNOSTIC, NOT A RESULT" in text
    assert "b (phys coefficient)   : 1.250" in text


def test_zero_share_is_reported_as_zero_not_replaced(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    (path / "decomposition.json").write_text(json.dumps(_artifact()), encoding="utf-8")
    text = R.render_headline(tmp_path, "sample")
    assert "phys                 : 0.000" in text
    assert "a zero share stays zero and is not replaced by the naive rung" in text
    # The naive rung's 0.03 must never be printed as the phys share.
    assert "0.030" not in text


def test_full_provenance_has_no_sample_banner(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    (path / "decomposition.json").write_text(
        json.dumps(_artifact(provenance="FULL")), encoding="utf-8")
    text = R.render_headline(tmp_path, "full")
    assert "PROVENANCE: FULL" in text
    assert SAMPLE_BANNER not in text
    assert "SAMPLE DIAGNOSTIC" not in text


def test_missing_group_is_named_not_guessed(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    payload = _artifact()
    payload.pop("interior")
    (path / "decomposition.json").write_text(json.dumps(payload), encoding="utf-8")
    text = R.render_headline(tmp_path, "sample")
    assert "NOT RUN — interior absent" in text


def test_missing_field_is_named(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    payload = _artifact()
    payload["pooled"].pop("jump_sd_ms")
    (path / "decomposition.json").write_text(json.dumps(payload), encoding="utf-8")
    assert "NOT RUN — jump_sd_ms absent" in R.render_headline(tmp_path, "sample")


def test_resolution_is_not_a_pass_fail_threshold(tmp_path):
    path = tmp_path / "phase4"
    path.mkdir()
    (path / "decomposition.json").write_text(json.dumps(_artifact()), encoding="utf-8")
    text = R.render_headline(tmp_path, "sample")
    assert "resolution limit, not a pass/fail threshold" in text
    assert "54.0 ms" in text


# --------------------------------------------------------------------------- #
# integrity block: a refused upstream stage is reported, never silent
# --------------------------------------------------------------------------- #
def _write_decomposition(tmp_path, payload) -> Path:
    path = tmp_path / "phase4"
    path.mkdir(parents=True, exist_ok=True)
    (path / "decomposition.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def _write_calibration(tmp_path, *, status, provenance="FULL", **extra) -> Path:
    payload = {"status": status, "provenance": provenance, "estimated": False,
               "reason": ("Stance (three-point vs stand-up) and Combine start type are "
                          "not labelled in any provided file."),
               "blocker": "B8"}
    payload.update(extra)
    (tmp_path / "calibration.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def _stage_results(overrides=None) -> list[dict]:
    rows = [{"stage": s, "status": "OK", "rc": 0}
            for s in EXPECTED_STAGE_ORDER]
    for stage, status, rc in (overrides or []):
        for row in rows:
            if row["stage"] == stage:
                row["status"], row["rc"] = status, rc
    return rows


def test_full_artifact_with_unestimated_calibration_prints_the_taint(tmp_path):
    _write_decomposition(tmp_path, _artifact(provenance="FULL"))
    _write_calibration(tmp_path, status="NOT_ESTIMABLE")
    text = R.render_headline(
        tmp_path, "full",
        _stage_results([("02_combine_physical_time", "REFUSED (gate)", 4)]))
    assert R.TAINT_NO_CALIBRATION in text
    assert "the decomposition is produced without one" in text
    assert "02_combine_physical_time (REFUSED (gate), exit 4)" in text
    assert "NOT a sign-off" in text
    assert "B8" in text


def test_estimated_full_calibration_does_not_print_the_taint(tmp_path):
    _write_decomposition(tmp_path, _artifact(provenance="FULL"))
    _write_calibration(tmp_path, status="ESTIMATED", estimated=True)
    text = R.render_headline(tmp_path, "full", _stage_results())
    assert R.TAINT_NO_CALIBRATION not in text
    assert "no upstream stage failed or refused" in text
    assert "Calibration was ESTIMATED on full data" in text


def test_missing_calibration_file_names_itself(tmp_path):
    _write_decomposition(tmp_path, _artifact(provenance="FULL"))
    text = R.render_headline(tmp_path, "full", _stage_results())
    assert "NOT RUN — outputs/calibration.json absent" in text


def test_sample_calibration_is_reported_not_refused(tmp_path):
    """Sample mode still renders: reporting must not become a gate."""
    _write_decomposition(tmp_path, _artifact())
    _write_calibration(tmp_path, status="PLACEHOLDER", provenance=SAMPLE_STAMP)
    text = R.render_headline(tmp_path, "sample", _stage_results())
    assert R.TAINT_NO_CALIBRATION in text
    assert "POOLED" in text                      # the numbers still print


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def test_notebook_calls_the_validation_renderer(notebook):
    """The validation block is printed from the artefact, not typed in."""
    source = all_source(notebook)
    assert "render_validation(OUTPUTS, MODE)" in source


def _validation_artifact(provenance=SAMPLE_STAMP, **overrides) -> dict:
    boundary = {"sigma_u_zero": False, "between_phys_var_zero": False}
    payload = {
        "provenance": provenance,
        "stage": "05_validate",
        "mode": "sample",
        "status": "OK",
        "all_detectors_fired": True,
        "checks": {
            "permutation": {
                "status": "OK",
                "observed": {"share_phys": 0.31, "b": 1.2, "n_reps": 900,
                             "n_players": 18, "boundary": boundary},
                "permutation_share_phys": {"n": 200, "mean": 0.01, "sd": 0.02,
                                           "min": 0.0, "p2_5": 0.0, "p50": 0.01,
                                           "p97_5": 0.06, "max": 0.08},
                "n_requested": 200, "n_ok": 200,
                "one_sided_monte_carlo_p": 0.005,
                "observed_share_phys_percentile": 100.0,
                "reading": "Observed share sits above the whole permutation distribution.",
            },
            "placebo": {
                "status": "OK",
                "real": {"share_phys": 0.31, "b": 1.2, "n_reps": 900, "n_players": 18},
                "placebo": {"share_phys": 0.02, "b": 1.3, "n_reps": 900, "n_players": 18},
                "placebo_minus_real_share_phys": -0.29,
                "height_weight_regression": {"r2": 0.04},
                "reading": "Reported as it is; no threshold is applied.",
            },
            "mechanical": {
                "status": "OK",
                "base": {"b": 1.2, "share_phys": 0.31},
                "depth_removed": {"b": 1.9, "share_phys": 0.20, "n_reps": 900, "n_players": 18},
                "alignment_removed": {"b": 1.25, "share_phys": 0.30, "n_reps": 900, "n_players": 18},
                "both_removed": {"b": 1.95, "share_phys": 0.19, "n_reps": 900, "n_players": 18},
                "onset": {"status": "OK", "span_share_phys": 0.11, "span_b": 0.8,
                          "grid": [{"onset_speed_yds": 0.75, "n_reps_refit": 900, "b": 1.1,
                                    "share_phys": 0.30, "jump_sd_ms": 55.0, "status": "OK"},
                                   {"onset_speed_yds": 1.0, "n_reps_refit": 900, "b": 1.4,
                                    "share_phys": 0.25, "jump_sd_ms": 61.0, "status": "OK"}]},
                "stance": {"status": "NOT POSSIBLE", "reason": "no stance label exists (B8)"},
                "reading": "No threshold is applied.",
            },
            "snap_quality": {
                "status": "OK",
                "fitted_set": {"n_reps": 900, "n_clean": 820, "n_dropped": 80,
                               "share_dropped": 0.089},
                "tracked_primary_set": {"n_reps": 1200, "n_clean": 1100, "n_dropped": 100,
                                        "share_dropped": 0.083,
                                        "dropped_by_status": {"cross_status_not_reached": 100}},
                "base": {"share_phys": 0.31, "share_u": 0.69, "b": 1.2},
                "clean": {"share_phys": 0.28, "share_u": 0.72, "b": 1.1, "status": "OK"},
                "reading": "Reported at both levels.",
            },
            "selection": {
                "status": "OK",
                "n_players": 18, "n_reps": 900, "max_over_min_reps": 6.0,
                "share_of_reps_top_player": 0.12,
                "unweighted": {"share_phys": 0.31, "b": 1.2, "n_reps": 900},
                "equal_snap_weighted": {"share_phys": 0.27, "b": 1.5, "n_reps": 900,
                                        "status": "OK",
                                        "bootstrap": {"b_ci95": [1.1, 2.0]}},
                "equal_snap_minus_unweighted_share_phys": -0.04,
                "draftee_selection": {"status": "CANNOT BE TESTED", "reason": "pre-filtered (B5)"},
            },
        },
        "resolution": {
            "status": "OK",
            "smallest_detectable_jump_sd_ms_realised": 42.5,
            "prereg_reference": {"smallest_detectable_jump_sd_ms_at_20_reps": 54.4,
                                 "smallest_detectable_jump_sd_ms_best_case": 51.8},
            "statement": "Reported as a resolution statement, not a threshold.",
        },
        "synthetic_can_fail": {
            "purpose": "each world is built so the check SHOULD fire",
            "all_detectors_fired": True,
            "detectors": {"permutation": True, "placebo": True,
                          "mechanical_depth_alignment": True, "mechanical_onset": True,
                          "snap_quality": True, "selection": False},
            "permutation": {"expected_detector": "observed share above the distribution"},
            "placebo": {"expected_detector": "high placebo share when signal is present"},
            "mechanical": {"depth_alignment_world": {"expected_detector": "a material b change"},
                           "onset_world": {"expected_detector": "a material share span"}},
            "snap_quality": {"expected_detector": "a material share change"},
            "selection": {"expected_detector": "a material share change under weighting"},
        },
        "determinism": {"permutation_seed": 20270109, "synthetic_seed": 20270111},
    }
    payload.update(overrides)
    return payload


def _write_validation(tmp_path, payload) -> Path:
    path = tmp_path / "validation"
    path.mkdir(parents=True, exist_ok=True)
    (path / "validation.json").write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_missing_validation_is_not_run(tmp_path):
    text = R.render_validation(tmp_path, "sample")
    assert "NOT RUN — outputs/validation/validation.json absent" in text
    assert "0.000" not in text                       # no invented share
    assert "UNVALIDATED SAMPLE OUTPUT — NOT A RESULT" not in text


def test_validation_is_printed_under_the_sample_banner(tmp_path):
    _write_validation(tmp_path, _validation_artifact())
    text = R.render_validation(tmp_path, "sample")
    assert text.index(SAMPLE_BANNER) < text.index("1. PERMUTATION")
    assert "Every row below is a SAMPLE DIAGNOSTIC, NOT A RESULT." in text
    assert text.rstrip().endswith("#")


def test_validation_prints_every_detector_row(tmp_path):
    _write_validation(tmp_path, _validation_artifact())
    text = R.render_validation(tmp_path, "sample")
    for detector in ("permutation", "placebo", "mechanical_depth_alignment",
                     "mechanical_onset", "snap_quality", "selection"):
        assert detector in text, detector
    assert "detector_fired" in text
    # A detector that did not fire is printed as False, never dropped.
    assert re.search(r"^\s+selection\s+False\b", text, re.M)


def test_validation_boundary_check_is_uninformative_not_a_pass(tmp_path):
    payload = _validation_artifact()
    payload["checks"]["permutation"]["observed"]["boundary"]["between_phys_var_zero"] = True
    payload["checks"]["permutation"]["one_sided_monte_carlo_p"] = 1.0
    _write_validation(tmp_path, payload)
    text = R.render_validation(tmp_path, "sample")
    assert "UNINFORMATIVE ON THIS DATA" in text
    assert "NOT as a pass" in text


def test_validation_names_an_absent_field(tmp_path):
    payload = _validation_artifact()
    payload["resolution"].pop("statement")
    _write_validation(tmp_path, payload)
    text = R.render_validation(tmp_path, "sample")
    assert "NOT RUN — resolution.statement absent" in text


def test_validation_stance_is_reported_not_possible(tmp_path):
    _write_validation(tmp_path, _validation_artifact())
    text = R.render_validation(tmp_path, "sample")
    assert "NOT POSSIBLE" in text and "B8" in text


def test_validation_full_provenance_has_no_sample_banner(tmp_path):
    _write_validation(tmp_path, _validation_artifact(provenance="FULL"))
    text = R.render_validation(tmp_path, "full")
    assert "PROVENANCE: FULL" in text
    assert SAMPLE_BANNER not in text


def test_figures_are_skipped_without_full_provenance(tmp_path):
    out = tmp_path / "outputs"
    (out / "phase4").mkdir(parents=True)
    (out / "phase4" / "decomposition.json").write_text(json.dumps(_artifact()), encoding="utf-8")
    result = R.render_figures(out, tmp_path / "figures", mode="sample")
    assert result["paths"] == []
    assert len(result["messages"]) == 4
    assert all("SKIPPED" in m for m in result["messages"])


def test_figures_are_skipped_when_artefact_absent(tmp_path):
    result = R.render_figures(tmp_path, tmp_path / "figures", mode="full")
    assert result["paths"] == []
    assert "decomposition.json absent" in result["messages"][0]
