"""Turn-context table: the band join, the split, and the provenance trail.

Two layers:

* **synthetic** — a handful of turns and player rows in ``tmp_path``, checked
  against the behaviour the note claims: the band is left-closed, ``play_type``
  comes from ``pass_result`` presence, ``pass_result = 'R'`` is a scramble and
  not a run, the run-wording check only fires one way, the join never fans out,
  and a field that is absent everywhere is reported with its zero denominator
  rather than dropped.
* **full** — skipped unless the competition data is mounted: the manifest's
  digests match the files on disk and the headline counts in ``summary.json``
  are the ones the inputs produce.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
TURNS = ROOT / "data" / "interim" / "full_run_20261111" / "bdb27_full_db_turns.parquet"
OUT = ROOT / "outputs" / "turn_context"
NOTE = ROOT / "notes" / "turn-context.md"

PP_HEADER = (
    "game_id,play_id,team_abbr,nfl_id,lined_up_position,play_description,down,"
    "yards_to_go,possession_team,pass_result,team_coverage_man_zone,"
    "team_coverage_type,in_motion_at_ball_snap,target,route_ran,"
    "separation_at_pass_forward,cushion,tackle,assist,caused_forced_fumble,"
    "tackle_for_loss,coverage_assignment,coverage_assignment_at_snap"
)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@pytest.fixture()
def synthetic(tmp_path):
    """Six turns across the band edges and one play of each type."""
    from bdb27 import turn_context as tc

    turns = pd.DataFrame({
        "setting": ["game"] * 6,
        "position_group": ["DB"] * 6,
        "valid": [True] * 6,
        "game_id": [1, 1, 1, 1, 1, 2],
        "play_id": [10, 10, 10, 11, 11, 20],
        "nfl_id": [100, 100, 100, 100, 100, 100],
        "frame_start": [5, 9, 13, 5, 9, 5],
        "abs_turn_angle": [29.9, 30.0, 60.0, 45.0, 59.99, 40.0],
        "turn_angle": [29.9, 30.0, 60.0, 45.0, 59.99, 40.0],
        "entry_speed": [5.0] * 6,
        "exit_speed": [4.0] * 6,
        "apex_speed": [3.0] * 6,
        "apex_retention": [0.6] * 6,
        "speed_retention": [0.8] * 6,
        "plant": [False] * 6,
        "season": [2023] * 6,
    })
    turns_path = tmp_path / "turns.parquet"
    turns.to_parquet(turns_path, index=False)

    rows = [
        # a completed pass with coverage context
        "1,10,DEF,100,CB,(10:00) A.Pass to B.Receiver for 8 yards.,1,10,OFF,C,"
        "ZONE,COVER_2,FALSE,NA,NA,NA,5.5,NA,NA,NA,NA,ZONE,ZONE",
        # a run to the left tackle: no pass_result, no coverage assignment
        "1,11,DEF,100,CB,(9:00) C.Back left tackle for 3 yards.,2,7,OFF,NA,"
        "MAN,COVER_1,FALSE,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA",
        # a scramble: pass_result R but run wording in the description
        "2,20,DEF,100,CB,(8:00) (Shotgun) D.QB scrambles left end for 6 yards.,"
        "3,4,OFF,R,ZONE,COVER_3,FALSE,NA,NA,NA,7.0,NA,NA,NA,NA,ZONE,ZONE",
    ]
    pp_path = tmp_path / "player_play.csv"
    pp_path.write_text("\n".join([PP_HEADER] + rows) + "\n", encoding="utf-8")

    con = duckdb.connect()
    df = tc.build(con, turns_path, pp_path)
    yield tc, con, df
    con.close()


def test_band_is_left_closed(synthetic):
    _, _, df = synthetic
    assert sorted(df["abs_turn_angle"].tolist()) == [30.0, 40.0, 45.0, 59.99]
    assert (df["valid"]).all() and (df["setting"] == "game").all()


def test_play_type_from_pass_result_presence(synthetic):
    _, _, df = synthetic
    by_play = df.groupby("play_id")["play_type"].first()
    assert by_play[10] == "pass"
    assert by_play[11] == "run"
    assert by_play[20] == "pass"          # a scramble is still a pass
    assert df.loc[df.play_id == 20, "pass_scramble"].all()


def test_run_wording_check_is_one_way(synthetic):
    _, _, df = synthetic
    assert not df.loc[df.play_id == 10, "run_look_description"].any()
    assert df.loc[df.play_id == 11, "run_look_description"].all()
    # a scramble's description reads as run, and is reported as a disagreement
    assert df.loc[df.play_id == 20, "run_look_description"].all()
    assert set(df.loc[df.play_id == 20, "description_check"]) == {"disagree"}


def test_join_never_fans_out(synthetic):
    tc, con, df = synthetic
    n_band = con.execute("SELECT COUNT(*) FROM tc_band_rows").fetchone()[0]
    assert len(df) == n_band == 4
    assert df["game_id"].notna().all()


def test_absent_field_keeps_its_denominator(synthetic):
    tc, con, _ = synthetic
    avail = tc.field_availability(con)
    row = avail.loc[avail.field == "route_ran"].iloc[0]
    assert int(row.present_band) == 0
    assert int(row.n_band) == 4
    assert row.share_band == 0.0
    assert set(avail["field"]) == set(tc.CONTEXT_FIELDS)


def test_na_sentinel_and_integer_tackle(tmp_path):
    from bdb27 import outcome_link as ol

    pp = pd.DataFrame({
        "game_id": [1], "play_id": [1], "nfl_id": [1], "team_abbr": ["DEF"],
        "lined_up_position": ["CB"], "route_ran": ["NA"], "target": ["TRUE"],
        "separation_at_pass_forward": ["NA"], "possession_team": ["OFF"],
        "pass_result": ["NA"], "play_description": ["x"], "tackle": ["2"],
        "assist": ["0"], "tackle_for_loss": ["TRUE"],
        "caused_forced_fumble": ["FALSE"],
    })
    path = tmp_path / "pp_na.csv"
    pp.to_csv(path, index=False)
    con = ol.connect()
    try:
        table = ol.load_player_play(
            con, path, table="t_na",
            extra=(("tackle", "INTEGER"), ("assist", "INTEGER"),
                   ("tackle_for_loss", "BOOLEAN"), ("caused_forced_fumble", "BOOLEAN"),
                   ("pass_result", "VARCHAR")))
        row = con.execute(f"SELECT * FROM {table}").df().iloc[0]
        assert pd.isna(row["route_ran"]) and pd.isna(row["pass_result"])
        assert int(row["tackle"]) == 2 and int(row["assist"]) == 0
        assert bool(row["tackle_for_loss"]) and not bool(row["caused_forced_fumble"])
    finally:
        con.close()
        path.unlink()


def test_module_has_no_gate_constants():
    from bdb27 import turn_context as tc

    src = inspect.getsource(tc)
    for word in ("MIN_", "GATE_", "if n <", "if len(df) <", "if count <"):
        assert word not in src, f"gate-like construct in turn_context.py: {word}"


# ------------------------------------------------------------------ full data
@pytest.mark.parametrize("name", ["manifest.json", "summary.json",
                                  "play_type_split.csv", "field_availability.csv",
                                  "demand_scope.csv", "medians.csv",
                                  "description_check.csv", "turn_context.parquet"])
def test_outputs_exist(name):
    if not OUT.exists():
        pytest.skip("turn-context output not built")
    if name.endswith(".parquet"):
        # the per-turn table is regenerated by the script and never committed
        pytest.skip("per-turn parquet is a local artefact (see .gitignore)")
    assert (OUT / name).exists(), f"run scripts/turn_context.py to produce {name}"


def test_manifest_digests_match_files():
    if not (OUT / "manifest.json").exists():
        pytest.skip("turn-context output not built")
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    for rel, meta in {**manifest["inputs"], **manifest["outputs"]}.items():
        path = ROOT / rel
        assert path.exists(), rel
        assert _sha(path) == meta["sha256"], rel
        assert path.stat().st_size == meta["bytes"], rel


def test_summary_recomputed_from_inputs():
    """The two-way split and band count are recomputed, not trusted."""
    if not (OUT / "summary.json").exists():
        pytest.skip("turn-context output not built")
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    con = duckdb.connect()
    try:
        band = con.execute(f"""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE p.pass_result IS NULL) AS run_turns
        FROM read_parquet('{TURNS.as_posix()}') t
        JOIN read_csv('{(RAW / "player_play.csv").as_posix()}', header=true,
                      all_varchar=true, nullstr=['NA','']) p
          ON CAST(p.game_id AS BIGINT) = t.game_id
         AND CAST(p.play_id AS BIGINT) = t.play_id
         AND CAST(p.nfl_id AS BIGINT) = t.nfl_id
        WHERE t.valid AND t.setting = 'game' AND t.position_group = 'DB'
          AND t.abs_turn_angle >= 30 AND t.abs_turn_angle < 60
        """).fetchone()
    finally:
        con.close()
    assert band[0] == summary["counts"]["band_turns"]
    assert band[1] == summary["play_type"]["run"]
    assert band[0] - band[1] == summary["play_type"]["pass"]


def test_note_numbers_are_in_the_artefacts():
    """Every headline the note quotes appears in summary.json or a csv."""
    if not (OUT / "summary.json").exists() or not NOTE.exists():
        pytest.skip("turn-context output or note not built")
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    text = NOTE.read_text(encoding="utf-8")
    counts = summary["counts"]
    quoted = {
        "7,777": counts["band_turns"],
        "7,774": counts["distinct_turn_keys"],
        "7,404": counts["turn_players"],
        "7,198": counts["band_plays"],
        "113": counts["band_players"],
        "809": counts["band_games"],
        "5,197": summary["play_type"]["pass"],
        "2,580": summary["play_type"]["run"],
        "371": summary["pass_scramble_turns"],
        "4,826": summary["pass_dropback_only_turns"],
        "2,554": summary["description_check"]["run/agree"],
        "4,139": summary["field_availability"]["coverage_assignment"]["present_band"],
    }
    for printed, value in quoted.items():
        assert printed in text, f"{printed} quoted in the note but not traced"
        assert f"{value:,}" == printed, f"note figure {printed} != artefact {value:,}"
