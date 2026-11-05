"""Schema assertions for the real Kaggle files and negative controls."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

import lc_common as C

REAL_FILES = {
    "combine_tracking": "combine_tracking.csv",
    "combine_results": "combine_results.csv",
    "players": "players.csv",
    "games": "games.csv",
    "player_play": "player_play.csv",
    "game_tracking": "game_tracking_2023_sample.csv",
}


def _raw(cfg, filename):
    return C.resolve_paths(cfg, "sample")["raw"] / filename


@pytest.mark.parametrize("key,filename", sorted(REAL_FILES.items()))
def test_real_file_schema_matches_declared(cfg, key, filename):
    """Column set and canonical dtypes must match ``lc_common.EXPECTED_SCHEMAS``."""
    con = duckdb.connect()
    try:
        path = _raw(cfg, filename)
        rel = con.sql(
            f"SELECT * FROM read_csv_auto('{path.as_posix()}', nullstr=['NA']) LIMIT 0")
        C.schema_check(rel, C.EXPECTED_SCHEMAS[key])
    finally:
        con.close()


@pytest.mark.parametrize("key,filename", sorted(REAL_FILES.items()))
def test_declared_types_accept_every_value(cfg, key, filename):
    """Reading the whole file with the declared dtypes must not fail.

    This is stronger than a header sniff: it proves every value (including the
    ``NA`` null marker) parses as the declared type.
    """
    con = duckdb.connect()
    try:
        types = {c: C.DUCKDB_TYPES[d] for c, d in
                 C.EXPECTED_SCHEMAS[key]["columns"].items()}
        type_struct = ", ".join(f"'{c}': '{t}'" for c, t in types.items())
        path = _raw(cfg, filename).as_posix()
        n = con.execute(
            f"SELECT count(*) FROM read_csv('{path}', header=true, "
            f"nullstr=['NA'], types={{{type_struct}}})").fetchone()[0]
        assert n > 0
    finally:
        con.close()


def test_units_forty_dash_is_yards(cfg):
    """A 40-yard dash must span ~40 yd in x; this anchors the x/y unit claim."""
    con = duckdb.connect()
    try:
        path = _raw(cfg, "combine_tracking.csv").as_posix()
        row = con.execute(f"""
            WITH t AS (
              SELECT nfl_id, attempt, max(x) - min(x) AS span
              FROM read_csv_auto('{path}', nullstr=['NA'])
              WHERE entity_type = 'PLAYER' AND drill_name = 'FORTY_YARD_DASH'
              GROUP BY 1, 2)
            SELECT median(span), count(*) FROM t""").fetchone()
        assert 38.0 <= row[0] <= 42.0, f"median x-span {row[0]:.2f} yd is not a 40-yd dash"
        assert row[1] == 793
    finally:
        con.close()


def test_units_dis_is_not_yards(cfg):
    """Numeric evidence for the documented `dis` anomaly: do not use the column."""
    con = duckdb.connect()
    try:
        path = _raw(cfg, "combine_tracking.csv").as_posix()
        row = con.execute(f"""
            WITH t AS (
              SELECT nfl_id, attempt,
                     max(dis) - min(dis) AS dis_span,
                     max(x) - min(x) AS x_span
              FROM read_csv_auto('{path}', nullstr=['NA'])
              WHERE entity_type = 'PLAYER' AND drill_name = 'FORTY_YARD_DASH'
              GROUP BY 1, 2)
            SELECT median(dis_span), median(x_span) FROM t""").fetchone()
        assert row[0] < 2.0, f"dis span unexpectedly large: {row[0]}"
        assert row[1] > 38.0, f"x span unexpectedly small: {row[1]}"
    finally:
        con.close()


def test_entity_filter_matters_for_speed_cap(cfg):
    """BALL rows carry the >12 yd/s values; PLAYER rows do not."""
    con = duckdb.connect()
    try:
        path = _raw(cfg, "combine_tracking.csv").as_posix()
        rows = con.execute(f"""
            SELECT entity_type, max(s)
            FROM read_csv_auto('{path}', nullstr=['NA'])
            GROUP BY 1 ORDER BY 1""").fetchall()
        stats = dict(rows)
        assert stats["PLAYER"] < 12.0
        assert stats["BALL"] > 20.0
    finally:
        con.close()


def test_schema_check_rejects_missing_column():
    df = pd.DataFrame({"a": [1, 2], "b": [0.1, 0.2]})
    with pytest.raises(C.SchemaError) as err:
        C.schema_check(df, {"a": "int", "b": "float", "c": "float"})
    message = str(err.value)
    assert "missing columns" in message
    assert "'c'" in message


def test_schema_check_rejects_wrong_dtype():
    df = pd.DataFrame({"a": ["1", "2"], "b": [0.1, 0.2]})
    with pytest.raises(C.SchemaError) as err:
        C.schema_check(df, {"a": "int", "b": "float"})
    message = str(err.value)
    assert "dtype" in message
    assert "a: expected int" in message


def test_schema_check_rejects_extra_column():
    df = pd.DataFrame({"a": [1], "surprise": [2]})
    with pytest.raises(C.SchemaError) as err:
        C.schema_check(df, {"a": "int"})
    assert "unexpected columns" in str(err.value)


def test_schema_check_accepts_duckdb_relation():
    con = duckdb.connect()
    try:
        rel = con.sql("SELECT 1::BIGINT AS a, 'x'::VARCHAR AS b")
        C.schema_check(rel, {"a": "int", "b": "str"})
    finally:
        con.close()
