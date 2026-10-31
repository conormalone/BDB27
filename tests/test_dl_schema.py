"""DL schema: the six raw schemas checked via ``bdb27_dl`` paths.

The DL chain must read exactly the same physical files as the LC chain for the
shared inputs — a fork that silently pointed at a different raw directory would
make every later comparison meaningless.
"""

from __future__ import annotations

import duckdb
import pytest

import dl.dl_common as D
import lc_common as C

#: ``EXPECTED_SCHEMAS`` key -> ``bdb27_dl.files`` key that supplies it.
REAL_FILES = {
    "combine_tracking": "combine_tracking",
    "combine_results": "combine_results",
    "players": "players",
    "games": "games",
    "player_play": "player_play",
    "game_tracking": "game_tracking_sample",
}


def _dl_path(cfg, files_key):
    return D.source_paths(cfg, files_key)[0]


@pytest.fixture(scope="session")
def dl_cfg():
    """Real config; ``tests/conftest.py`` is shared with the LC suite and is
    left untouched by this fork."""
    return D.load_config()


def _require(path):
    if not path.exists():
        pytest.skip(f"raw data unavailable on this box: {path} does not exist")


@pytest.mark.parametrize("schema_key,files_key", sorted(REAL_FILES.items()))
def test_real_file_schema_matches_declared(dl_cfg, schema_key, files_key):
    """Columns and canonical dtypes must match ``lc_common.EXPECTED_SCHEMAS``."""
    path = _dl_path(dl_cfg, files_key)
    _require(path)
    con = duckdb.connect()
    try:
        rel = con.sql(
            f"SELECT * FROM read_csv_auto('{path.as_posix()}', "
            f"nullstr=['{D.dl(dl_cfg)['runtime']['csv_null_marker']}']) LIMIT 0")
        D.schema_check(rel, C.EXPECTED_SCHEMAS[schema_key])
    finally:
        con.close()


@pytest.mark.parametrize("schema_key,files_key", sorted(REAL_FILES.items()))
def test_declared_types_accept_every_value(dl_cfg, schema_key, files_key):
    """Reading with the declared dtypes must parse (proves units/nulls fit)."""
    path = _dl_path(dl_cfg, files_key)
    _require(path)
    types = {c: C.DUCKDB_TYPES[d] for c, d in
             C.EXPECTED_SCHEMAS[schema_key]["columns"].items()}
    struct = ", ".join(f"'{c}': '{t}'" for c, t in types.items())
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT count(*) FROM read_csv('{path.as_posix()}', header=true, "
            f"nullstr=['{D.dl(dl_cfg)['runtime']['csv_null_marker']}'], "
            f"types={{{struct}}})").fetchone()[0]
    finally:
        con.close()
    assert rows > 0


def test_dl_paths_point_at_the_same_raw_files_as_lc(dl_cfg):
    """Path parity for every shared input (same resolved file, not a copy)."""
    shared = ("combine_tracking", "combine_results", "players", "games",
              "player_play")
    for key in shared:
        dl_file = D.source_paths(dl_cfg, key)[0]
        lc_file = C.source_paths(dl_cfg, key)[0]
        assert dl_file.resolve() == lc_file.resolve(), key

    dl_sample = D.tracking_sources(dl_cfg, "sample")
    lc_sample = C.tracking_source(dl_cfg, "sample")
    assert [p.resolve() for p in dl_sample] == [p.resolve() for p in lc_sample]

    dl_full = D.tracking_sources(dl_cfg, "full")
    lc_full = C.source_paths(dl_cfg, "game_tracking_full")
    assert [p.resolve() for p in dl_full] == [p.resolve() for p in lc_full]

    dl_raw = D.resolve_paths(dl_cfg, "sample")["raw"].resolve()
    lc_raw = C.resolve_paths(dl_cfg, "sample")["raw"].resolve()
    assert dl_raw == lc_raw


def test_dl_and_lc_blocks_are_separate_objects(dl_cfg):
    """Guard against the fork accidentally aliasing the LC block in YAML."""
    assert D.dl(dl_cfg) is not C.lc(dl_cfg)
    assert D.dl(dl_cfg)["mode"] is not C.lc(dl_cfg)["mode"]
    assert D.dl(dl_cfg)["paths"] is not C.lc(dl_cfg)["paths"]
    assert D.dl(dl_cfg)["paths"]["parquet"] != C.lc(dl_cfg)["paths"]["parquet"]
