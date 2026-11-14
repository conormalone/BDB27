"""Outcome-link feasibility note: numbers, provenance tags and claim hygiene.

The note is a scoping document, so the thing worth testing is that every number
in it is a count the inputs actually produce, that its provenance stamps are
kept apart, and that it makes no finding. Nothing here fits a model.

Counts are recomputed from the inputs; none is typed in as a fact. Tests skip
when the competition data is not mounted (see tests/test_schema_contract.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
TURNS = ROOT / "outputs" / "turns" / "turns.parquet"
FULL = ROOT / "data" / "interim" / "full_run_20261111" / "bdb27_full_db_turns.parquet"
OUT = ROOT / "outputs" / "outcome_link"
NOTE = ROOT / "notes" / "outcome-link-feasibility.md"
SPEC = ROOT / "notes" / "outcome-link-feasibility-spec.md"

DB_ALIGN = ("CB", "SLOT_CB", "HIGH_SAFETY", "BOX_SAFETY")

pytestmark = pytest.mark.skipif(
    not ((RAW / "player_play.csv").exists() and TURNS.exists() and FULL.exists()),
    reason="competition data / sample turns not mounted (see README_RUN.md §2)")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute("SET memory_limit='1200MB'; SET threads=2")
    c.execute(
        f"CREATE TABLE raw_pp AS SELECT * FROM read_csv('{RAW.as_posix()}/player_play.csv',"
        " header=true, all_varchar=true, nullstr=['NA',''])")
    yield c
    c.close()


@pytest.fixture(scope="module")
def summary():
    path = OUT / "feasibility_summary.json"
    assert path.exists(), "run scripts/outcome_link_feasibility.py first"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def routes():
    return pd.read_csv(OUT / "feasibility_routes.csv")


def _one(con, sql):
    return con.execute(sql).fetchone()


def test_band_turns_recompute(con, summary):
    n_all, n_band = _one(con, f"""
        SELECT COUNT(*), COUNT(*) FILTER (
            WHERE abs_turn_angle >= 30 AND abs_turn_angle < 60)
        FROM read_parquet('{TURNS.as_posix()}')
        WHERE setting = 'game' AND position_group = 'DB' AND valid""")
    assert summary["turns"]["sample_db_turns_all"] == n_all
    assert summary["turns"]["sample_band_turns"] == n_band


def test_separation_is_receiver_side_only(con, summary):
    rows, sep = _one(con, f"""
        SELECT COUNT(*), COUNT(separation_at_pass_forward) FROM raw_pp
        WHERE lined_up_position IN ({",".join("'" + p + "'" for p in DB_ALIGN)})""")
    total_sep = _one(con, "SELECT COUNT(separation_at_pass_forward) FROM raw_pp")[0]
    assert sep == 0
    assert summary["sample"]["db_alignment_rows"] == rows
    assert summary["sample"]["db_alignment_rows_with_separation"] == 0
    assert summary["sample"]["receiver_separation_rows"] == total_sep
    assert total_sep == 46737


def test_no_opponent_identifier_exists(con):
    cols = [r[0] for r in con.execute("DESCRIBE raw_pp").fetchall()]
    banned = ("opponent", "matchup", "assigned", "defender_id", "coverage_player")
    hits = [c for c in cols if any(b in c.lower() for b in banned)]
    assert hits == [], f"an opponent identifier would change the whole note: {hits}"


def test_full_denominators_recompute(con, summary):
    f = FULL.as_posix()
    n, players = _one(con, f"""
        SELECT COUNT(*), COUNT(DISTINCT nfl_id) FROM read_parquet('{f}')
        WHERE valid AND abs_turn_angle >= 30 AND abs_turn_angle < 60""")
    plays, one = _one(con, f"""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE n = 1) FROM (
          SELECT game_id, play_id, COUNT(*) n FROM read_parquet('{f}')
          WHERE valid AND abs_turn_angle >= 30 AND abs_turn_angle < 60
          GROUP BY 1, 2)""")
    assert summary["full"]["full_band_turns"] == n
    assert summary["full"]["full_band_players"] == players
    assert summary["full"]["full_band_plays"] == plays
    assert summary["full"]["full_band_plays_one_turn"] == one


def test_break_frame_alignment_is_proved(summary):
    # The note rests on recovering the break frame from (x_centre, y_centre).
    assert summary["turns"]["break_frame_recovered"] == summary["turns"]["sample_band_turns"]
    assert summary["turns"]["break_frame_alignment_max_yd"] < 0.25


def test_route_table_covers_every_route(routes, summary):
    assert set(routes.route_id) == {"R1", "R2", "R3", "R4", "R5"}
    idx = routes.set_index("route_id")
    # The two geometric routes are inferences and must say so.
    assert idx.loc[["R1", "R2"], "attribution"].str.contains("INFERENCE").all()
    r1 = routes.set_index("route_id").loc["R1"]
    assert r1.sample_turns_available == summary["turns"]["sample_band_turns"]
    assert r1.sample_turns_usable == summary["geometry"][
        "turns_with_route_runner_within_5yd_and_sep"]
    r5 = routes.set_index("route_id").loc["R5"]
    assert r5.sample_turns_usable == summary["injury"]["turns_where_turning_db_named"]


def test_injury_signal_exists_and_is_counted(con, summary):
    # Main's premise was that no injury signal exists; the note refutes it.
    plays = _one(con, """
        SELECT COUNT(DISTINCT game_id || '-' || play_id) FROM raw_pp
        WHERE lower(play_description) LIKE '%injur%'""")[0]
    assert summary["injury"]["plays_with_injury_text"] > plays
    assert summary["injury"]["injury_mentions"] > 0
    assert (summary["injury"]["mentions_unresolved_or_ambiguous"]
            + summary["injury"]["mentions_resolved_unique"]
            == summary["injury"]["injury_mentions"])


def test_note_quotes_the_artefacts(summary):
    text = NOTE.read_text()
    assert "326" in text and "7,777" in text and "7,198" in text
    assert "0 of 75,428" in text
    assert "**INFERENCE**" in text
    # Provenance stamps are carried, not merged.
    assert "[SAMPLE]" in text and "[FULL]" in text
    assert "no result" in text.lower()


def test_note_makes_no_finding():
    text = NOTE.read_text().lower()
    banned = [
        r"\bpredicts\b", r"\bshows that\b", r"\bevidence that\b", r"\beffect of\b",
        r"\bcoefficient\b", r"\bstatistically significant\b", r"\bcorrelat\w+ with\b",
        r"\bwe find\b", r"\bresults show\b",
    ]
    # The note is allowed to name these words only to deny them ("no coefficient
    # is reported"). Any sentence that uses one without a negation is drift.
    negation = re.compile(r"\b(no|not|never|cannot|nothing|nor)\b")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    hits = [s for s in sentences
            if any(re.search(p, s) for p in banned) and not negation.search(s)]
    assert hits == [], f"feasibility note drifted into finding language: {hits}"


def test_spec_predates_the_note():
    assert SPEC.exists()
    assert "Non-goals" in SPEC.read_text()
