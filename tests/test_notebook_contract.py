"""The deliverable notebook must run the current task's chain (D63).

Conor's working rule is "notebooks only, no CLI". These tests pin the properties that make one
Run All reproduce the documented command sequence, so a future module move cannot silently
orphan the notebook again (D59 did exactly that for two days).

They read the committed notebook as text/JSON — they do not execute it (too slow for the suite;
the executed check is recorded in D63 §8).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import lc_common as C

ROOT = Path(__file__).resolve().parents[1]
NB_PATH = ROOT / "notebooks" / "bdb27_full_run.ipynb"

LC_CHAIN = ["01_audit", "02_combine_ceiling", "00_thresholds", "03_route_depth",
            "04_efforts", "05_models", "06_validation"]
LEGACY_CHAIN = ["02_combine_envelope", "06_security", "03_game_demand", "07_security_models",
                "10_dose_response", "00b_external_proxies", "04_models", "05_validate"]


@pytest.fixture(scope="module")
def notebook():
    return json.loads(NB_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def code_text(notebook):
    parts = []
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        src = cell["source"]
        parts.append("".join(src) if isinstance(src, list) else src)
    return "\n".join(parts)


def _stage_list(code_text: str, name: str) -> list[str]:
    """Resolve a stage list from the notebook, following ``A + B`` composition."""
    match = re.search(rf"^{name} = (.*)$", code_text, re.M)
    assert match, f"{name} not found in the notebook"
    expr = match.group(1).strip()
    if expr.startswith("["):
        end = code_text.index("]", match.start(1))
        return re.findall(r'"([^"]+)"', code_text[match.start(1):end])
    parts = [p.strip() for p in expr.split("+")]
    assert parts, f"{name} is neither a list literal nor a '+' composition: {expr!r}"
    out: list[str] = []
    for part in parts:
        out.extend(_stage_list(code_text, part))
    return out


def test_lc_stage_order_is_the_documented_chain(code_text):
    assert _stage_list(code_text, "LC_STAGE_ORDER") == LC_CHAIN


def test_stage_order_runs_the_lc_chain_first(code_text):
    stage_order = _stage_list(code_text, "STAGE_ORDER")
    assert stage_order[: len(LC_CHAIN)] == LC_CHAIN


def test_legacy_stages_are_preserved_and_still_scheduled(code_text):
    stage_order = _stage_list(code_text, "STAGE_ORDER")
    assert _stage_list(code_text, "LEGACY_STAGE_ORDER") == LEGACY_CHAIN
    for name in LEGACY_CHAIN:
        assert name in stage_order


def test_notebook_starts_on_the_current_engine(code_text):
    # D59 moved the engine to lc_common.py; a bootstrap that still requires src/common.py cannot
    # start (that was the D63 defect).
    assert "src/lc_common.py" in code_text.replace('" / "', "/")
    assert "import lc_common as LC" in code_text
    assert 'not in sys.path' in code_text          # both trees are put on the path explicitly


def test_every_stage_receives_the_run_config(code_text):
    # The scratch run-config carries the notebook's data path; without --config a stage would read
    # config.yaml defaults (wrong data dir on Kaggle).
    assert '"--config"' in code_text
    assert "RUN_CFG_PATH" in code_text


def test_section_13_reads_the_lc_outputs_and_types_no_threshold(code_text):
    assert "route_depth_summary.json" in code_text
    assert "route_depth.csv" in code_text
    # the D63 constants must come from config, never from a literal in the notebook
    assert "decision_rule" in code_text
    for key in ("min_pre_throw_breaks", "min_families_meeting_bar",
                "min_pre_throw_breaks_per_family"):
        assert key in code_text
        assert not re.search(rf"^\s*{key}\s*=", code_text, re.M), f"{key} is typed into the notebook"


def test_section_15_reads_the_validation_outputs_and_types_no_number(code_text):
    """D66: §15 renders 06_validation from disk; the verdict is not asserted in code."""
    assert "validation_result.json" in code_text
    assert "permutation_null.csv" in code_text
    assert "sensitivity_curve.csv" in code_text
    for token in ("p_value_rule", "attenuation_fraction", "equivalent_at_margin"):
        assert token in code_text, token
    # the verdict must come from the artefact, never from a literal in the notebook
    assert "verdict" in code_text
    assert not re.search(r"^\s*(p_player_game|attenuation|equivalence_margin)\s*=",
                         code_text, re.M)


def test_d66_validation_constants_live_in_config(cfg):
    block = C.lc(cfg)["validation"]
    assert block["permutations"]["reps"] == 1000
    assert block["permutations"]["block_columns"]["player_game"] == ["nfl_id", "game_id"]
    assert block["permutations"]["block_columns"]["route_family"] == ["nfl_id", "route_family"]
    assert block["equivalence"]["margin"] == C.lc(cfg)["power"]["equivalence_margin"]
    assert block["equivalence"]["smallest_effect_of_interest"] == \
        C.lc(cfg)["power"]["smallest_effect_of_interest"]
    # the garbage-time margins stay in exactly one place
    assert "q4_margin" not in block["garbage_time"]
    assert "any_quarter_margin" not in block["garbage_time"]


def test_committed_notebook_has_no_executed_outputs(notebook):
    for i, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs") == [], f"cell {i} has outputs committed"
        assert cell.get("execution_count") is None, f"cell {i} has an execution count"


def test_d63_rule_is_in_config_and_evaluated_on_full_only(cfg):
    rule = C.lc(cfg)["route_depth"]["decision_rule"]
    assert rule["min_pre_throw_breaks"] == 500
    assert rule["min_families_meeting_bar"] == 6
    assert rule["min_pre_throw_breaks_per_family"] == 25
    assert rule["evaluated_on"] == "FULL"
    # precision arithmetic recorded in D63 must still reproduce the constant
    sigma = C.lc(cfg)["power"]["assumed_within_player_sd_log_speed"]
    sei = C.lc(cfg)["power"]["smallest_effect_of_interest"]
    n_min = (1.96 * sigma / (sei / 3.0)) ** 2 * 1.3
    assert round(n_min / 100.0) * 100 == rule["min_pre_throw_breaks"]
