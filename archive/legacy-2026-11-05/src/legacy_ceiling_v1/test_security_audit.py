#!/usr/bin/env python3
"""Behaviour tests for the Phase-1 **board/security audit extension** — BDB27 stakes task.

Covers ``src/security_audit.py`` (audit items c/d/e/h/l + the merged go/no-go & headline gate) as wired
additively into ``src/01_audit.py``.

Runnable **without pytest** (``python src/tests/test_security_audit.py``) and collected by pytest.
Behaviours asserted:

  * the extension **runs end-to-end** on the synthetic fixture and writes the ``outputs/audit/security``
    CSVs + ``audit_security.json`` (items c, d, e, h, l), **without touching** the pre-existing a–i results;
  * the extension **degrades gracefully** when the board is unavailable (explicit PENDING, never raises);
  * item **d** counts duplicate player-snapshot cells, blank ``nfl_id`` rows and board↔combine position
    mismatches;
  * the **merged gate** is *not evaluated* in sample mode; in full mode it blocks on any failed check
    (external-data permission, board presence, matched-receiver minima, Combine reliability, Analysis A);
  * board position → competition position-group mapping.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from datetime import date

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402
import security_audit as sa  # noqa: E402
import security_features as sf  # noqa: E402

SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(tmp: Path, synthetic: bool = True, fixtures: Path | None = None, raw: Path | None = None,
         **overrides) -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(tmp / "out")
    if synthetic:
        cfg["paths"]["fixtures"] = str(fixtures or (REPO / "fixtures"))
    if raw is not None:
        cfg["paths"]["raw"] = str(raw)
    for dotted, val in overrides.items():
        node = cfg
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = val
    cfg["_config_path"] = str(tmp / "config.yaml")
    return cfg


def _raw_dir(tmp: Path) -> Path:
    """A tiny fake raw dir so the audit's csv fallbacks resolve without touching the real data drive."""
    raw = tmp / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "combine_results.csv").write_text(
        "nfl_id,combine_position\n800001,WR\n800002,TE\n800003,WR\n800004,LB\n", encoding="utf-8")
    (raw / "combine_tracking.csv").write_text(
        "nfl_id,entity_type,drill_type,time\n"
        "800001,PLAYER,FORTY_YARD_DASH,2023-03-03 10:00:00\n"
        "800002,PLAYER,FORTY_YARD_DASH,2023-03-03 10:05:00\n", encoding="utf-8")
    (raw / "players.csv").write_text(
        "nfl_id,draft_overall_pick\n800001,5\n800002,NA\n800003,40\n", encoding="utf-8")
    (raw / "player_play.csv").write_text(
        "nfl_id,route_ran\n800001,GO\n800001,CROSS\n800002,HITCH\n800003,\n", encoding="utf-8")
    return raw


def _ctx(tmp: Path, mode: str = "sample", synthetic: bool = True, regime: str = "planted",
         board: bool = True) -> dict:
    raw = _raw_dir(tmp)
    cfg = _cfg(tmp, synthetic=synthetic, raw=raw)
    if not board and synthetic:
        # point the fixture path at an empty root so the board is missing
        cfg["paths"]["fixtures"] = str(tmp / "empty_fixtures")
    con = common.connect(1)
    prov = "SYNTHETIC FIXTURE (not a finding)" if synthetic else common.provenance(cfg, mode)
    return dict(cfg=cfg, con=con, mode=mode, prov=prov, logger=common.get_logger(),
                audit_dir=tmp / "out" / "audit", tables={},
                game_side_status=("PENDING FULL RUN" if mode != "full" else "AVAILABLE"),
                synthetic=synthetic, synthetic_regime=regime)


def _rows(rows: list[dict]) -> list[dict]:
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# end-to-end extension
# --------------------------------------------------------------------------- #
def test_extension_runs_and_is_additive():
    tmp = Path(tempfile.mkdtemp())
    ctx = _ctx(tmp)
    audit_results = {"a": [{"x": 1}], "b": [{"y": 2}], "c": [], "d": [], "e": [], "f": [], "g": [], "i": []}
    before = json.dumps(audit_results, sort_keys=True)
    sec = sa.run(ctx, audit_results)
    # additive: the pre-existing a-i results are untouched
    assert json.dumps(audit_results, sort_keys=True) == before
    assert sec["provenance"] == "SYNTHETIC FIXTURE (not a finding)"
    assert sec["items"].get("c", 0) > 0 and sec["items"].get("d", 0) > 0
    assert sec["items"].get("e", 0) > 0 and sec["items"].get("h", 0) > 0 and sec["items"].get("l", 0) > 0
    assert sec["gate"]["evaluated"] is False and sec["gate"]["blocking"] is False
    d = tmp / "out" / "audit" / "security"
    for f in ("c_snapshot_timing.csv", "c_player_testing_days.csv", "d_match_rates.csv",
              "e_prospect_pool.csv", "h_security_groups.csv", "l_power.csv", "audit_security.json"):
        assert (d / f).exists(), f
    # every emitted CSV carries provenance first
    for f in ("c_snapshot_timing.csv", "d_match_rates.csv", "e_prospect_pool.csv"):
        header = (d / f).read_text().splitlines()[0]
        assert header.split(",")[0] == "provenance", header


def test_extension_degrades_gracefully_without_board():
    tmp = Path(tempfile.mkdtemp())
    ctx = _ctx(tmp, board=False)
    sec = sa.run(ctx, {})                     # must not raise
    src = str(sec.get("board_source", ""))
    assert ("SYNTHETIC MISSING" in src) or ("UNAVAILABLE" in src), src
    assert sec["gate"]["evaluated"] is False
    # an explicit PENDING note is written for the board items
    text = (tmp / "out" / "audit" / "security" / "d_match_rates.csv").read_text()
    assert "PENDING FULL RUN" in text


def test_item_d_counts_duplicates_blanks_and_mismatches():
    tmp = Path(tempfile.mkdtemp())
    ctx = _ctx(tmp)
    ctx["sec_dir"] = ctx["audit_dir"] / "security"      # normally set by sa.run()
    inp = sa.resolve_inputs(ctx)
    board = _rows(inp["board_rows"])
    nid = board[0]["nfl_id"]
    note = board[0]["snapshot_note"]
    dup = dict(board[0]); dup["snapshot_date"] = board[0]["snapshot_date"]
    blank_before = sf.board_blank_id_count(board)
    board.append(dup)                                     # duplicate (nfl_id, snapshot_note) cell
    blank = dict(board[0]); blank["nfl_id"] = None
    board.append(blank)                                   # blank join key
    inp = dict(inp, board_rows=board)
    out = sa.item_d(ctx, inp)
    row = [r for r in out if r.get("check") == "duplicates"][0]
    assert row["n_blank_nfl_id_rows"] == blank_before + 1, row
    assert row["n_duplicated_player_snapshot_cells"] >= 1, row
    assert "n_position_mismatch_board_vs_combine" in row


# --------------------------------------------------------------------------- #
# merged headline gate
# --------------------------------------------------------------------------- #
def _gate_ctx(tmp: Path, mode: str = "full", **overrides) -> dict:
    cfg = _cfg(tmp, **overrides)
    con = common.connect(1)
    return dict(cfg=cfg, con=con, mode=mode, prov=common.provenance(cfg, mode),
                logger=common.get_logger(), audit_dir=tmp / "out" / "audit", tables={},
                game_side_status="AVAILABLE", synthetic=False, synthetic_regime="binding")


def _write_analysis_a(tmp: Path, gate_pass: bool) -> None:
    d = tmp / "out" / "security"
    d.mkdir(parents=True, exist_ok=True)
    (d / "analysis_a.json").write_text(json.dumps({
        "gate_pass": gate_pass, "n": 280,
        "speed_coef": {"coef": -9.0, "se": 3.0, "pvalue": 0.01},
        "interaction_coef": {"coef": -2.0, "se": 1.0, "pvalue": 0.05}, "note": "x"}))


def _inp(board_present: bool = True):
    b = [{"nfl_id": 1, "draft_year": 2023, "position": "WR", "school": "X", "type": "All",
          "n_total": 100, "n": 50, "edp": 20.0, "edp_rank": 1, "pos_rank": "WR1",
          "snapshot_date": date(2023, 2, 20), "snapshot_note": "1 Week Pre-Combine"}]
    return {"board_rows": (b if board_present else []), "testing_days": {}, "drills_by_player": {},
            "source": "x", "board_meta": {}}


def _g_ok():
    return [{"status": "ESTIMATED", "icc": 0.97}]


def _h_ok(cfg: dict):
    m = int(cfg["security"]["min_matched_receivers_per_tier"]) + 5
    return {"evaluated": True, "gate_counts": {f"WR:low_edp_secure": m, "WR:mid_edp": m,
                                               "WR:high_edp_at_risk": m, "TE:low_edp_secure": m,
                                               "TE:mid_edp": m, "TE:high_edp_at_risk": m}}


def test_merged_gate_not_evaluated_in_sample_mode():
    tmp = Path(tempfile.mkdtemp())
    ctx = _gate_ctx(tmp, mode="sample")
    g = sa.merged_gate(ctx, _inp(), {}, _h_ok(ctx["cfg"]), [])
    assert g["evaluated"] is False and g["blocking"] is False and "sample" in g["note"]


def test_merged_gate_full_passes_when_all_checks_ok():
    tmp = Path(tempfile.mkdtemp())
    _write_analysis_a(tmp, True)
    ctx = _gate_ctx(tmp)
    g = sa.merged_gate(ctx, _inp(), {"g": _g_ok()}, _h_ok(ctx["cfg"]), [])
    assert g["evaluated"] is True and g["headline_pass"] is True and g["passed"] is True, g
    assert g["blocking"] is False
    assert g["checks"]["analysis_a_ok"] is True


def test_merged_gate_headline_failure_is_not_blocking():
    """A failed security HEADLINE gate demotes safety to exploratory; it must NOT stop the pipeline.

    (TASK.md "Headline gate": on failure, fall back to the ceiling/tail framing; only external data
    that is not permitted is a hard stop.)
    """
    tmp = Path(tempfile.mkdtemp())
    _write_analysis_a(tmp, False)
    ctx = _gate_ctx(tmp)
    g = sa.merged_gate(ctx, _inp(), {"g": _g_ok()}, _h_ok(ctx["cfg"]), [])
    assert g["headline_pass"] is False and "analysis_a_ok" in g["failed"], g
    assert g["blocking"] is False, "headline-gate failure must not block the full pipeline"


def test_merged_gate_blocks_only_when_not_permitted():
    tmp = Path(tempfile.mkdtemp())
    _write_analysis_a(tmp, True)
    # external data not permitted -> the one hard stop
    ctx = _gate_ctx(tmp, **{"external.board.permitted": False})
    g = sa.merged_gate(ctx, _inp(), {"g": _g_ok()}, _h_ok(ctx["cfg"]), [])
    assert g["blocking"] is True and "external_permitted" in g["failed"]
    # below the matched-receiver minimum -> headline fails but does NOT block
    ctx2 = _gate_ctx(tmp)
    h = {"evaluated": True, "gate_counts": {"WR:high_edp_at_risk": 1}}
    g2 = sa.merged_gate(ctx2, _inp(), {"g": _g_ok()}, h, [])
    assert g2["headline_pass"] is False and "min_matched_receivers_per_tier" in g2["failed"]
    assert g2["blocking"] is False
    # no reliable ICC -> headline fails, not blocking
    g3 = sa.merged_gate(ctx2, _inp(), {"g": []}, _h_ok(ctx2["cfg"]), [])
    assert g3["headline_pass"] is False and "combine_reliability_ok" in g3["failed"]
    assert g3["blocking"] is False


# --------------------------------------------------------------------------- #
# position mapping
# --------------------------------------------------------------------------- #
def test_position_group_mapping():
    cfg = common.load_config()
    assert sf.position_group(cfg, "WR") == "WR"
    assert sf.position_group(cfg, "CB") == "DB"
    assert sf.position_group(cfg, "EDGE") == "DL"
    assert sf.position_group(cfg, "OT") == "OL"
    assert sf.position_group(cfg, "XXX") == "OTHER"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_extension_runs_and_is_additive, test_extension_degrades_gracefully_without_board,
             test_item_d_counts_duplicates_blanks_and_mismatches,
             test_merged_gate_not_evaluated_in_sample_mode,
             test_merged_gate_full_passes_when_all_checks_ok,
             test_merged_gate_headline_failure_is_not_blocking,
             test_merged_gate_blocks_only_when_not_permitted,
             test_position_group_mapping]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
