"""Fixture contract: schema-identical, planted truth present, deterministic."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import duckdb
import pytest

import lc_common as C
import synthetic_lc_fixture as F


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixtures") / "lc"
    F.generate(root, seed=20270107)
    return root


class _Rel:
    def __init__(self, columns, types):
        self.columns, self.types = columns, types


def _describe(path: Path):
    con = duckdb.connect()
    try:
        rel = con.sql(
            f"SELECT * FROM read_csv_auto('{path.as_posix()}', nullstr=['NA']) LIMIT 0")
        return _Rel(list(rel.columns), list(rel.types))
    finally:
        con.close()


@pytest.mark.parametrize("rel,key", [
    ("combine/combine_tracking.csv", "combine_tracking"),
    ("combine/combine_results.csv", "combine_results"),
    ("combine/players.csv", "players"),
    ("game/player_play.csv", "player_play"),
    ("game/games.csv", "games"),
    ("game/game_tracking.csv", "game_tracking"),
    ("game/null/player_play.csv", "player_play"),
    ("game/confounded/game_tracking.csv", "game_tracking"),
])
def test_fixture_schema_matches_declared(fixtures, rel, key):
    C.schema_check(_describe(fixtures / rel), C.EXPECTED_SCHEMAS[key])


def test_ground_truth_files_present(fixtures):
    combine_truth = fixtures / "combine" / "ground_truth.csv"
    assert combine_truth.exists()
    header = combine_truth.read_text().splitlines()[0]
    assert header == ("nfl_id,draft_year,position,attempt,true_vmax_yds,"
                      "true_tau_s,n_frames,flags")
    planted = json.loads((fixtures / "game" / "ground_truth.json").read_text())
    null = json.loads((fixtures / "game" / "null" / "ground_truth.json").read_text())
    conf = json.loads((fixtures / "game" / "confounded" / "ground_truth.json").read_text())
    assert planted["beta_leverage_log_speed_per_sd"] == 0.05
    assert planted["n_efforts"] > 0
    assert null["beta_leverage_log_speed_per_sd"] == 0.0
    assert conf["beta_leverage_log_speed_per_sd"] == 0.0
    assert conf["realised_corr_leverage_time"] > 0.80
    assert abs(planted["realised_corr_leverage_time"]) < 0.30
    assert "not_data" in planted


def test_planted_leverage_effect_exists_and_null_does_not(fixtures):
    def within_player_corr(path):
        data = json.loads((fixtures / "game" / path / "ground_truth.json").read_text())
        xs, ys = [], []
        by_player = {}
        for e in data["efforts"]:
            by_player.setdefault(e["nfl_id"], []).append(e)
        for rows in by_player.values():
            mean_x = sum(r["leverage"] for r in rows) / len(rows)
            mean_y = sum(r["true_log_speed"] for r in rows) / len(rows)
            for r in rows:
                xs.append(r["leverage"] - mean_x)
                ys.append(r["true_log_speed"] - mean_y)
        cov = sum(x * y for x, y in zip(xs, ys))
        vx = sum(x * x for x in xs)
        vy = sum(y * y for y in ys)
        return cov / math.sqrt(vx * vy)

    assert within_player_corr("") > 0.50, "planted leverage effect not visible"
    assert abs(within_player_corr("null")) < 0.02, "null shows a spurious effect"
    # The confounded world has no leverage effect but a real time/load decline,
    # and leverage is correlated with time.  A large raw correlation is the
    # *point*: it is what the orthogonalised model in test_leverage_recovery
    # must remove.
    assert within_player_corr("confounded") < -0.50


def test_combine_fixture_contains_planted_edge_cases(fixtures):
    con = duckdb.connect()
    try:
        p = (fixtures / "combine" / "combine_tracking.csv").as_posix()
        ball = con.execute(
            f"SELECT count(*) FROM read_csv_auto('{p}', nullstr=['NA']) "
            "WHERE entity_type = 'BALL'").fetchone()[0]
        assert ball > 0
        assert con.execute(
            f"SELECT max(s) FROM read_csv_auto('{p}', nullstr=['NA']) "
            "WHERE entity_type = 'PLAYER'").fetchone()[0] > 12.0
        # gap > 0.5 s inside an attempt
        gap = con.execute(f"""
            WITH t AS (
              SELECT nfl_id, attempt, time,
                     epoch_ms(time) - epoch_ms(lag(time) OVER (
                       PARTITION BY nfl_id, attempt ORDER BY time)) AS dt_ms
              FROM read_csv_auto('{p}', nullstr=['NA'])
              WHERE entity_type = 'PLAYER')
            SELECT max(dt_ms) / 1000.0 FROM t""").fetchone()[0]
        assert gap > 0.5
        # duplicate attempt: same (nfl_id, attempt) spans > 30 s
        span = con.execute(f"""
            WITH t AS (
              SELECT nfl_id, attempt,
                     max(epoch_ms(time)) - min(epoch_ms(time)) AS span_ms
              FROM read_csv_auto('{p}', nullstr=['NA'])
              WHERE entity_type = 'PLAYER' GROUP BY 1, 2)
            SELECT max(span_ms) / 1000.0 FROM t""").fetchone()[0]
        assert span > 30.0
        counts = dict(con.execute(f"""
            SELECT n, count(*) FROM (
              SELECT nfl_id, count(DISTINCT attempt) AS n
              FROM read_csv_auto('{p}', nullstr=['NA'])
              WHERE entity_type = 'PLAYER' GROUP BY 1)
            GROUP BY 1""").fetchall())
        assert counts.get(1, 0) > 0 and counts.get(2, 0) > 0
    finally:
        con.close()


def test_generation_is_byte_deterministic(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    kwargs = dict(seed=424242, n_games=2, plays_per_game=6)
    F.generate(a, **kwargs)
    F.generate(b, **kwargs)
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files, "generator produced no files"
    for rel in files:
        assert hashlib.sha256((a / rel).read_bytes()).hexdigest() == \
            hashlib.sha256((b / rel).read_bytes()).hexdigest(), rel


def test_planted_and_null_share_schedule_but_not_outcomes(fixtures):
    planted_pp = (fixtures / "game" / "player_play.csv").read_bytes()
    null_pp = (fixtures / "game" / "null" / "player_play.csv").read_bytes()
    assert planted_pp == null_pp
    assert (fixtures / "game" / "game_tracking.csv").read_bytes() != \
        (fixtures / "game" / "null" / "game_tracking.csv").read_bytes()
    p = json.loads((fixtures / "game" / "ground_truth.json").read_text())
    n = json.loads((fixtures / "game" / "null" / "ground_truth.json").read_text())
    assert [e["leverage"] for e in p["efforts"]] == \
        [e["leverage"] for e in n["efforts"]]


def test_readme_marks_fixtures_as_not_data(fixtures):
    text = (fixtures / "README.md").read_text().lower()
    assert "not data" in text
    assert "beta" in text
