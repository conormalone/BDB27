"""Part-2 NA self-documentation (reason codes + sentences, and their markdown).

Every exit path of ``run_part2`` must carry ``part2_reason_absolute``,
``part2_reason_relative`` and ``part2_reason_comparison``; each is a short code
plus one sentence.  A FULL-provenance sentence may not defer the reader to
sample mode, a re-run or a larger sample.  Deterministic, synthetic, no network.
"""

from __future__ import annotations

import copy
import math
import re

import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_fixtures as F
from dl import dl_models_core as M
from dl import stage_04_models as stage04

REASON_KEYS = ("part2_reason_absolute", "part2_reason_relative",
               "part2_reason_comparison")


# --------------------------------------------------------------------------- #
# Fixtures (same tiny world used by test_dl_models.py)
# --------------------------------------------------------------------------- #
def _cfg(tmp_path, *, n_players=6, n_games=3, snaps_per_game=4, **models):
    cfg = D.load_config()
    block = copy.deepcopy(cfg["bdb27_dl"])
    block["fixture"]["n_players"] = n_players
    block["fixture"]["n_games"] = n_games
    block["fixture"]["snaps_per_game"] = snaps_per_game
    block["paths"]["raw"] = str(tmp_path / "raw")
    block["paths"]["outputs"] = str(tmp_path / "outputs")
    block["paths"]["interim"] = str(tmp_path / "interim")
    block["paths"]["parquet"] = str(tmp_path / "parquet")
    block["paths"]["checkpoints"] = str(tmp_path / "checkpoints")
    block["paths"]["tmp"] = str(tmp_path / "tmp")
    block["paths"]["thresholds"] = str(tmp_path / "thresholds.json")
    block["models"].update(models)
    cfg["bdb27_dl"] = block
    return cfg


def _frame(tmp_path, **kw):
    cfg = _cfg(tmp_path, **kw)
    world = tmp_path / "world"
    F.write_world(cfg, "planted", world)
    block = D.dl(cfg)
    block["paths"]["raw"] = str(world.resolve())
    block["models"]["inputs"]["snaps"] = str(world.resolve() / "snaps.csv")
    block["models"]["inputs"]["ceiling"] = str(world.resolve() / "player_ceiling.csv")
    frames = {name: pd.read_csv(world / f"{name}.csv")
              for name in ("snaps", "player_ceiling", "players", "combine_results")}
    frame, _ = M.build_frame(frames["snaps"], frames["player_ceiling"],
                             frames["players"], frames["combine_results"])
    frame = pd.concat([frame, M.build_load_panel(frame, block)], axis=1)
    frame = M.ensure_a0_z(M.prepare_frame(frame, block))
    return cfg, block, frame


def _assert_reasons(payload):
    for key in REASON_KEYS:
        record = payload[key]
        assert set(record) == {"code", "message"}
        assert record["code"], f"{key} has no code"
        assert record["message"].strip(), f"{key} has no sentence"
    md = "\n".join(stage04._reason_markdown(payload))
    for key in REASON_KEYS:
        assert payload[key]["code"] in md
        assert payload[key]["message"] in md
    return md


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def test_fmt_score_is_short_and_never_raises():
    assert stage04._fmt_score(None) == "NA"
    assert stage04._fmt_score(float("nan")) == "NA"
    assert stage04._fmt_score(float("inf")) == "NA"
    assert stage04._fmt_score(float("-inf")) == "NA"
    assert stage04._fmt_score("not a number") == "NA"
    assert stage04._fmt_score(object()) == "NA"
    assert stage04._fmt_score(0.123456) == "0.123"
    assert stage04._fmt_score(-0.50001) == "-0.5"
    assert math.isclose(float(stage04._fmt_score(1234.0)), 1230.0)


def test_reason_markdown_tolerates_missing_keys_and_bad_records():
    assert stage04._reason_markdown(None) == []
    assert stage04._reason_markdown({}) == []
    assert stage04._reason_markdown({"status": "NOT_ESTIMABLE"}) == []
    partial = {"part2_reason_absolute": {"code": "X", "message": "why"}}
    lines = stage04._reason_markdown(partial)
    assert len(lines) == 1 and "`X`" in lines[0] and "why" in lines[0]
    malformed = {"part2_reason_comparison": {"code": None, "message": None}}
    assert "UNKNOWN" in stage04._reason_markdown(malformed)[0]


# --------------------------------------------------------------------------- #
# Every NA exit carries all three reasons
# --------------------------------------------------------------------------- #
def test_below_min_snaps_carries_all_three_reasons(tmp_path):
    _, block, frame = _frame(tmp_path, n_players=4, n_games=2, snaps_per_game=4)
    payload = M.run_part2(frame, block)
    assert payload["status"] == "NOT_ESTIMABLE"
    for key in REASON_KEYS:
        assert payload[key]["code"] == "BELOW_MIN_SNAPS"
    _assert_reasons(payload)


def test_no_complete_cases_carries_all_three_reasons(tmp_path):
    _, block, frame = _frame(tmp_path, n_players=4, n_games=2, snaps_per_game=4)
    censored = frame.copy()
    censored["censor_time"] = 0.0
    payload = M.run_part2(censored, block)
    assert payload["status"] == "NOT_ESTIMABLE"
    for key in REASON_KEYS:
        assert payload[key]["code"] == "NO_COMPLETE_CASES"
    _assert_reasons(payload)


def test_missing_design_columns_carries_all_three_reasons(tmp_path):
    _, block, frame = _frame(tmp_path, n_players=4, n_games=2, snaps_per_game=4)
    broken = frame.iloc[0:0].drop(columns=["a0"])
    payload = M.run_part2(broken, block)
    assert payload["status"] == "NOT_ESTIMABLE"
    for key in REASON_KEYS:
        assert payload[key]["code"] == "MISSING_DESIGN_COLUMNS"
        assert "a0" in payload[key]["message"]
    _assert_reasons(payload)


def test_missing_load_columns_name_the_build_failure(tmp_path):
    _, block, frame = _frame(tmp_path, n_players=6, n_games=3, snaps_per_game=10)
    block["game"]["min_snaps_per_player"] = 2
    drop = [c for c in frame.columns if c.startswith("absolute_load_")]
    assert drop
    payload = M.run_part2(frame.drop(columns=drop), block)
    assert payload["status"] == "NOT_ESTIMABLE"
    assert payload["part2_reason_absolute"]["code"] == "MISSING_LOAD_COLUMN"
    assert "absolute-load" in payload["part2_reason_absolute"]["message"]
    _assert_reasons(payload)


def test_all_na_load_columns_name_the_input_shortage(tmp_path):
    _, block, frame = _frame(tmp_path, n_players=6, n_games=3, snaps_per_game=10)
    block["game"]["min_snaps_per_player"] = 2
    load_columns = [c for c in frame.columns if c.startswith("relative_load_")]
    assert load_columns
    blank = frame.copy()
    blank[load_columns] = float("nan")
    payload = M.run_part2(blank, block)
    assert payload["status"] == "NOT_ESTIMABLE"
    assert payload["part2_reason_relative"]["code"] == "NO_LOAD_VALUES"
    assert "vmax/tau" in payload["part2_reason_relative"]["message"]
    _assert_reasons(payload)


# --------------------------------------------------------------------------- #
# Provenance rule, enforced in code
# --------------------------------------------------------------------------- #
_FORBIDDEN = re.compile(r"\bsample\b|\bre-?run\b|\bfull[- ]data\b|"
                        r"\b(?:larger|bigger)[- ]sample\b|\bmore data\b",
                        re.IGNORECASE)


def test_full_provenance_never_defers_to_a_bigger_sample(tmp_path):
    _, block, frame = _frame(tmp_path, n_players=4, n_games=2, snaps_per_game=4)
    payload = M.run_part2(frame, block, provenance="FULL")
    for key in REASON_KEYS:
        assert not _FORBIDDEN.search(payload[key]["message"]), payload[key]
    _assert_reasons(payload)


def test_full_provenance_guard_raises_on_deferring_wording():
    bad = {"code": "X", "message": "re-run on full data once the sample grows"}
    with pytest.raises(ValueError, match="defers"):
        M._assert_reason_is_full_safe(bad, "FULL")
    # the same wording is allowed outside a full run
    M._assert_reason_is_full_safe(bad, "PLACEHOLDER_SAMPLE")


def test_sample_mode_appends_the_diagnostic_stamp():
    record = M._reason("X", "why", provenance="PLACEHOLDER_SAMPLE")
    assert "Sample-mode diagnostic only" in record["message"]
    full = M._reason("X", "why", provenance="FULL")
    assert "Sample-mode" not in full["message"]
