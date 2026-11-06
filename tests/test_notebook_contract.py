"""The deliverable notebook's contract.

These tests read the committed ``.ipynb`` as JSON (no Jupyter dependency) and
exercise the headline renderer directly. They exist because on 2026-11-05 the
repo's notebook folder described notebooks that had been archived: the interface
lied about itself (D118). The contract is:

* the notebook exists, is valid JSON, and has **no baked outputs**;
* the stage order in the code is exactly ``TASK.md``'s order, and the
  ``05_validate`` stub is reported as not built rather than run as a pass;
* the configuration cell exposes exactly the documented defaults;
* stages are reloaded from disk before each call (a ``git pull`` cannot leave
  stale code in the kernel);
* a missing ``decomposition.json`` prints ``NOT RUN`` — never a fabricated zero;
* sample-provenance numbers are printed only under the loud ``NOT A RESULT``
  banner, and a zero share is reported as zero, not replaced by the naive rung.
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
                        "03b_reliability", "04_models"]
STUB = "05_validate"

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


def test_stub_is_reported_not_run(notebook):
    source = all_source(notebook)
    assert "05_validate" in source
    assert "STUB_STAGE = \"05_validate\"" in source
    assert "NOT BUILT (stub, exits" in source
    # The stub must not be smuggled into the executable chain.
    match = re.search(r"STAGE_ORDER\s*=\s*\[(.*?)\]", source, re.S)
    assert "05_validate" not in match.group(1)


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
# figures
# --------------------------------------------------------------------------- #
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
