#!/usr/bin/env python3
"""Tests for D49 — true season labels, the season-scope filter, and config fingerprints.

Runnable without pytest (``python src/tests/test_season_scope.py``) and collected by pytest.

Covered:
  * the date-based fallback maps a January game to the PREVIOUS season (the 2024-01 -> 2023 case);
  * games.csv is the single source of truth; a game_id absent from a PRESENT games.csv is a
    hard error (never a silent guess);
  * the synthetic game fixture spans PRE/REG/POST, contains a Jan game whose calendar year differs
    from its season, and contains a player whose route snaps are majority preseason;
  * ``build_effort_rows`` applies the active scope to rows AND to snap counts / snap_share /
    ``min_snaps`` — including a hostile row labelled ``season_scope=primary`` with a PRE
    ``season_type`` (the season_type filter must still drop it);
  * ``season_scope.csv`` reports both scopes with the right snapshot/eligibility counts;
  * 09's inherited panel rows never leak a season type outside the active scope;
  * checkpoint fingerprints: same config matches, changed ``game.scope`` misses, and a legacy
    marker with no fingerprint misses.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SM = _load("_sm07_season_scope", REPO / "src" / "07_security_models.py")
_RR = None


def _rr():
    global _RR
    if _RR is None:
        _RR = _load("_rr08_season_scope", REPO / "src" / "08_route_robustness.py")
    return _RR


def _cfg() -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    return cfg


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# 1. season label correctness (incl. the January case)
# --------------------------------------------------------------------------- #
def test_january_game_maps_to_previous_season():
    assert common.fallback_season_from_game_id(2024010700) == 2023
    assert common.fallback_season_from_game_id(2024021100) == 2023
    assert common.fallback_season_from_game_id(2024090800) == 2024
    assert common.fallback_season_from_game_id(2025081000) == 2025
    # synthetic/unparseable month -> leading four digits, not a crash
    assert common.fallback_season_from_game_id(2025000000) == 2025


def test_games_csv_is_the_source_of_truth():
    games = {2024010700: dict(season=2023, season_type="REG")}
    assert common.season_and_type(2024010700, games) == (2023, "REG")
    # without games.csv the fallback still maps the same game correctly
    assert common.season_and_type(2024010700, None)[0] == 2023


def test_missing_game_id_is_a_hard_error():
    games = {2024010700: dict(season=2023, season_type="REG")}
    try:
        common.season_and_type(2024010701, games)
    except common.SeasonLookupError as exc:
        assert "2024010701" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a missing game_id was silently accepted")


def test_attach_seasons_reports_count_and_ids():
    rows = [dict(game_id=1), dict(game_id=2), dict(game_id=3)]
    try:
        common.attach_seasons(rows, {1: dict(season=2024, season_type="REG")})
    except common.SeasonLookupError as exc:
        text = str(exc)
        assert "2 game_id" in text and "2" in text and "3" in text
    else:  # pragma: no cover
        raise AssertionError("missing game_ids were silently accepted")


# --------------------------------------------------------------------------- #
# 2. fixture spans the scopes + the mislabel case + a majority-preseason player
# --------------------------------------------------------------------------- #
def test_synthetic_fixture_spans_scopes_and_jan_mislabel():
    fix = REPO / "fixtures" / "synthetic" / "binding"
    games = _rows(fix / "games.csv")
    types = {r["season_type"] for r in games}
    assert {"PRE", "REG", "POST"} <= types, types
    jan = [r for r in games
           if r["game_id"].startswith(("202401", "202402", "202501", "202502"))]
    assert jan, "fixture must contain a Jan/Feb game (the season-mislabel case)"
    assert any(int(r["season"]) == int(r["game_id"][:4]) - 1 for r in jan), jan

    pp = _rows(fix / "player_play.csv")
    by_game = {r["game_id"]: r for r in games}
    per: dict[str, dict[str, int]] = {}
    for r in pp:
        g = by_game[r["game_id"]]
        d = per.setdefault(r["nfl_id"], {})
        d[g["season_type"]] = d.get(g["season_type"], 0) + 1
    assert any(v.get("PRE", 0) > sum(c for t, c in v.items() if t != "PRE")
               for v in per.values()), per


# --------------------------------------------------------------------------- #
# 3. scope filter: rows AND snap counts / snap_share / min_snaps
# --------------------------------------------------------------------------- #
def _scope_fixture():
    cfg = _cfg()
    assign = {"assignments": [
        dict(nfl_id=1, group="moved_down", stratum="trend", log_edp_pre=3.0, edp_pre=20.0,
             position_group="WR"),
        dict(nfl_id=2, group="high_secure", stratum="trend", log_edp_pre=3.1, edp_pre=22.0,
             position_group="WR"),
    ]}
    envelope = {1: dict(env_vmax=9.0, peak_speed=9.0, speed_at_20yd=8.5),
                2: dict(env_vmax=9.0, peak_speed=9.0, speed_at_20yd=8.5)}
    cov = {1: dict(position="WR", height=73.0, weight=200.0, draft_year=2023),
           2: dict(position="WR", height=73.0, weight=200.0, draft_year=2023)}
    psum = {1: dict(n_route_snaps=120, topk_mean_speed=8.0),
            2: dict(n_route_snaps=40, topk_mean_speed=8.0)}
    topk = [
        # player 1: honest REG + POST rows under the primary label
        dict(nfl_id=1, game_id=2024090801, play_id=1, season=2024, season_type="REG",
             season_scope="primary", route_ran="GO", pass_length=20, target=True, peak_speed=8.0),
        dict(nfl_id=1, game_id=2025011201, play_id=2, season=2024, season_type="POST",
             season_scope="primary", route_ran="CROSS", pass_length=8, target=True, peak_speed=8.2),
        # hostile row: PRE season_type deliberately mislabelled as primary -> must still drop
        dict(nfl_id=1, game_id=2025081001, play_id=3, season=2025, season_type="PRE",
             season_scope="primary", route_ran="GO", pass_length=25, target=True, peak_speed=9.9),
        dict(nfl_id=1, game_id=2025081001, play_id=3, season=2025, season_type="PRE",
             season_scope="sensitivity", route_ran="GO", pass_length=25, target=True,
             peak_speed=9.9),
        # sensitivity-only label -> must not enter a primary run
        dict(nfl_id=1, game_id=2024090802, play_id=4, season=2024, season_type="REG",
             season_scope="sensitivity", route_ran="OUT", pass_length=10, target=True,
             peak_speed=8.1),
        # player 2 is below min_snaps in the primary scope
        dict(nfl_id=2, game_id=2024090803, play_id=5, season=2024, season_type="REG",
             season_scope="primary", route_ran="GO", pass_length=20, target=True, peak_speed=8.0),
        dict(nfl_id=2, game_id=2024090803, play_id=5, season=2024, season_type="REG",
             season_scope="sensitivity", route_ran="GO", pass_length=20, target=True,
             peak_speed=8.0),
    ]
    return cfg, assign, envelope, cov, psum, topk


def test_scope_filters_rows_and_uses_scoped_snap_counts():
    cfg, assign, envelope, cov, psum, topk = _scope_fixture()
    rows, dropped = SM.build_effort_rows(
        topk, assign, envelope, cov, psum, cfg,
        snap_counts={1: 110, 2: 40}, game_index=None,
        season_types=["REG", "POST"], season_scope="primary", min_snaps=100)
    assert {r["season_type"] for r in rows} == {"REG", "POST"}
    assert {r["nfl_id"] for r in rows} == {1}, rows
    assert dropped.get("season_type_not_in_scope") == 1
    assert dropped.get("other_season_scope") == 3
    assert dropped.get("below_min_snaps") == 1
    # the denominator is the SCOPED count (110), not the player_summary's 120
    assert rows[0]["n_route_snaps"] == 110
    assert abs(rows[0]["snap_share"] - 1.0) < 1e-12   # clipped at 1.0


def test_sensitivity_scope_keeps_preseason_and_lifts_the_gate():
    cfg, assign, envelope, cov, psum, topk = _scope_fixture()
    rows, _ = SM.build_effort_rows(
        topk, assign, envelope, cov, psum, cfg,
        snap_counts={1: 130, 2: 120}, game_index=None,
        season_types=["REG", "POST", "PRE"], season_scope="sensitivity", min_snaps=100)
    assert any(r["season_type"] == "PRE" for r in rows)
    assert {r["nfl_id"] for r in rows} == {1, 2}   # both pass min_snaps in this scope


def test_season_scope_table_reports_both_scopes():
    cfg = _cfg()
    snap_rows = [
        dict(nfl_id=1, season=2024, season_type="REG", n_route_snaps=110),
        dict(nfl_id=1, season=2025, season_type="PRE", n_route_snaps=30),
        dict(nfl_id=2, season=2024, season_type="REG", n_route_snaps=80),
        dict(nfl_id=2, season=2025, season_type="PRE", n_route_snaps=40),
    ]
    table = SM.season_scope_table(cfg, snap_rows, {1: {}, 2: {}}, "primary")
    assert [r["scope"] for r in table] == ["primary", "sensitivity"]
    p, s = table
    assert p["n_route_snaps"] == 190 and s["n_route_snaps"] == 260
    assert p["n_players_passing_min_snaps"] == 1 and s["n_players_passing_min_snaps"] == 2
    assert p["active"] is True and s["active"] is False


# --------------------------------------------------------------------------- #
# 4. 09 inherits the filter through 08 -> 07
# --------------------------------------------------------------------------- #
def test_panels_do_not_leak_out_of_scope_rows():
    cfg = _cfg()
    cfg["security_models"]["primary"]["attenuation_reps"] = 5
    panels = _rr().build_panels(cfg, True, "planted", SM)
    allowed = set(panels["season_types"])
    for rows in panels["rows"].values():
        assert {r.get("season_type") for r in rows} <= allowed, allowed
    assert panels["season_scope"] == cfg["game"]["scope"]["active"]


# --------------------------------------------------------------------------- #
# 5. checkpoint fingerprints
# --------------------------------------------------------------------------- #
def _marker(cfg, stage="07_security_models"):
    return common.done_marker(cfg, stage, mode="full", provenance="FULL",
                              synthetic=False, regime="planted")


def test_fingerprint_matches_unchanged_config():
    cfg = _cfg()
    fp = common.config_fingerprint(cfg, "07_security_models")
    rec = _marker(cfg)
    assert common.checkpoint_match(rec, mode="full", provenance="FULL", synthetic=False,
                                   regime="planted", fingerprint=fp)


def test_fingerprint_mismatch_on_changed_scope():
    cfg = _cfg()
    rec = _marker(cfg)
    fp_before = common.config_fingerprint(cfg, "07_security_models")
    cfg["game"]["scope"]["primary"] = ["REG"]
    fp_after = common.config_fingerprint(cfg, "07_security_models")
    assert fp_before != fp_after
    assert not common.checkpoint_match(rec, mode="full", provenance="FULL", synthetic=False,
                                       regime="planted", fingerprint=fp_after)
    # and the same config still matches (a rebuild is only caused by the change)
    assert common.checkpoint_match(_marker(cfg), mode="full", provenance="FULL", synthetic=False,
                                   regime="planted", fingerprint=fp_after)


def test_legacy_marker_without_fingerprint_is_a_mismatch():
    cfg = _cfg()
    fp = common.config_fingerprint(cfg, "07_security_models")
    legacy = dict(mode="full", provenance="FULL", synthetic=False, regime="planted")
    assert not common.checkpoint_match(legacy, mode="full", provenance="FULL", synthetic=False,
                                       regime="planted", fingerprint=fp)
    # without a fingerprint request the legacy rules are unchanged
    assert common.checkpoint_match(legacy, mode="full", provenance="FULL", synthetic=False,
                                   regime="planted")


def test_pipeline_version_changes_the_fingerprint():
    cfg = _cfg()
    fp1 = common.config_fingerprint(cfg, "03_game_demand")
    cfg["pipeline_version"] = int(cfg["pipeline_version"]) + 1
    assert common.config_fingerprint(cfg, "03_game_demand") != fp1


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_january_game_maps_to_previous_season, test_games_csv_is_the_source_of_truth,
             test_missing_game_id_is_a_hard_error, test_attach_seasons_reports_count_and_ids,
             test_synthetic_fixture_spans_scopes_and_jan_mislabel,
             test_scope_filters_rows_and_uses_scoped_snap_counts,
             test_sensitivity_scope_keeps_preseason_and_lifts_the_gate,
             test_season_scope_table_reports_both_scopes,
             test_panels_do_not_leak_out_of_scope_rows,
             test_fingerprint_matches_unchanged_config,
             test_fingerprint_mismatch_on_changed_scope,
             test_legacy_marker_without_fingerprint_is_a_mismatch,
             test_pipeline_version_changes_the_fingerprint]
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
