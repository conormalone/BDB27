"""Outcome-link feasibility note: numbers, provenance tags and claim hygiene.

The note is a scoping document, so the thing worth testing is that every number
in it is a count the inputs actually produce, that its provenance stamps are
kept apart, and that it makes no finding. Nothing here fits a model.

Two questions are tested separately and never merged:

* **candidate availability** — measured on ``player_play.csv`` alone, over both
  band populations, by ``src/bdb27/outcome_link.py`` (P1-P5);
* **geometric ambiguity** — measured on the local 2023 tracking *sample file* and
  labelled as a property of that file.

Every count is recomputed from the inputs; none is typed in as a fact. Tests skip
when the competition data is not mounted (see tests/test_schema_contract.py).
"""

from __future__ import annotations

import inspect
import json
import math
import re
import statistics
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
CORRECTION_SPEC = ROOT / "notes" / "outcome-link-correction-spec.md"
NOTEBOOK = ROOT / "notebooks" / "bdb27_outcome_link_preflight.ipynb"

DB_ALIGN = ("CB", "SLOT_CB", "HIGH_SAFETY", "BOX_SAFETY")
KEY = ["game_id", "play_id", "nfl_id", "frame_start"]

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
def module():
    from bdb27 import outcome_link

    return outcome_link


@pytest.fixture(scope="module")
def summary():
    path = OUT / "feasibility_summary.json"
    assert path.exists(), "run scripts/outcome_link_feasibility.py first"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def routes():
    return pd.read_csv(OUT / "feasibility_routes.csv")


@pytest.fixture(scope="module")
def ambiguity():
    return pd.read_csv(OUT / "feasibility_ambiguity.csv")


@pytest.fixture(scope="module")
def per_turn():
    return pd.read_csv(OUT / "feasibility_turns.csv")


@pytest.fixture(scope="module")
def candidates():
    path = OUT / "feasibility_candidates.csv"
    assert path.exists(), "run scripts/outcome_link_feasibility.py first"
    return pd.read_csv(path)


@pytest.fixture(scope="module")
def cand_detail():
    return pd.read_csv(OUT / "feasibility_ambiguity_candidates.csv")


@pytest.fixture(scope="module")
def preflight():
    return pd.read_csv(OUT / "feasibility_preflight.csv")


def _one(con, sql):
    return con.execute(sql).fetchone()


def _band_key_sql(parquet: Path) -> str:
    return (f"SELECT game_id, play_id, nfl_id, frame_start FROM "
            f"read_parquet('{parquet.as_posix()}') WHERE valid AND setting='game' "
            f"AND position_group='DB' AND abs_turn_angle >= 30 AND abs_turn_angle < 60")


# ------------------------------------------------------- the artefacts exist, all of them


def test_every_artefact_including_the_new_ones_is_written():
    for name in ("feasibility_routes.csv", "feasibility_turns.csv",
                 "feasibility_ambiguity.csv", "feasibility_summary.json",
                 "feasibility_candidates.csv", "feasibility_preflight.csv",
                 "feasibility_ambiguity_candidates.csv"):
        assert (OUT / name).exists(), f"{name} is missing — run the script"


def test_summary_keeps_every_existing_key(summary):
    for key in ("provenance", "label", "generated_utc", "script", "band", "inputs",
                "sample", "full", "match", "turns", "geometry", "injury"):
        assert key in summary, f"existing summary key {key!r} was dropped"
    for key in ("preflight", "availability", "candidate_status",
                "ambiguity_candidates", "candidate_turn_keys", "geometry_scope_note"):
        assert key in summary, f"new summary key {key!r} is missing"


# ------------------------------------------------------- player_play as the availability source


def test_player_play_is_the_availability_source(con, summary):
    rows, plays, players, games = _one(con, """
        SELECT COUNT(*), COUNT(DISTINCT (game_id::BIGINT, play_id::BIGINT)),
               COUNT(DISTINCT nfl_id), COUNT(DISTINCT game_id)
        FROM raw_pp""")
    mean, max_rows = _one(con, """
        SELECT AVG(n), MAX(n) FROM (
          SELECT COUNT(*) AS n FROM raw_pp
          GROUP BY game_id::BIGINT, play_id::BIGINT)""")
    both = _one(con, """
        SELECT COUNT(*) FROM (
          SELECT game_id, play_id FROM raw_pp GROUP BY 1, 2
          HAVING COUNT(*) FILTER (WHERE team_abbr = possession_team) > 0
             AND COUNT(*) FILTER (WHERE team_abbr IS NOT NULL
                                  AND team_abbr <> possession_team) > 0)""")[0]

    avail = summary["availability"]
    assert avail["pp_rows"] == rows
    assert avail["pp_plays"] == plays
    assert avail["pp_players"] == players
    assert avail["pp_games"] == games
    assert avail["pp_rows_per_play_max"] == max_rows
    assert avail["pp_rows_per_play_mean"] == pytest.approx(mean, rel=1e-6)
    assert avail["pp_plays_with_both_sides"] == both
    # A rows-per-play figure is a property of the draft-prospect cohort, so this
    # file is never capped at the tracking sample's per-play ceiling.
    assert max_rows > 4, (
        f"max rows per play is {max_rows}: that is the tracking sample's shape, not "
        "player_play's — the cohort file does not cap plays at four players")


def test_both_teams_are_typed_on_a_majority_share(con, summary):
    """The false stop-check said opposing players 'cannot be typed by team'."""
    share = summary["availability"]["pp_plays_with_both_sides_share"]
    plays = summary["availability"]["pp_plays"]
    assert share == round(summary["availability"]["pp_plays_with_both_sides"] / plays, 6)
    assert share > 0.5, (
        f"only {share:.1%} of plays carry rows from both sides — the availability "
        "claim would need re-deriving")


def test_players_file_covers_the_cohort(con, summary):
    avail = summary["availability"]
    if "players_file_players" not in avail:
        pytest.skip("players.csv not next to player_play.csv on this machine")
    n_file = _one(con, f"""SELECT COUNT(DISTINCT nfl_id::BIGINT) FROM read_csv(
        '{(RAW / "players.csv").as_posix()}', header=true, all_varchar=true,
        nullstr=['NA',''])""")[0]
    missing = _one(con, """
        SELECT COUNT(DISTINCT nfl_id::BIGINT) FROM raw_pp
        WHERE nfl_id::BIGINT NOT IN (SELECT nfl_id::BIGINT FROM read_csv(
            '""" + (RAW / "players.csv").as_posix() + """', header=true,
            all_varchar=true, nullstr=['NA','']))""")[0]
    assert avail["players_file_players"] == n_file
    assert avail["pp_players_not_in_players_file"] == missing
    assert avail["pp_players"] <= n_file


# ------------------------------------------------------- the tracking sample is not an availability source


def test_tracking_sample_cannot_characterise_availability(con, summary):
    """The sample file's shape is structural; it is never read for availability."""
    rows, players, plays = _one(con, """
        SELECT COUNT(*), COUNT(DISTINCT CAST(nfl_id AS BIGINT)),
               COUNT(DISTINCT (CAST(game_id AS BIGINT), CAST(play_id AS BIGINT)))
        FROM read_csv('""" + (RAW / "game_tracking_2023_sample.csv").as_posix() + """',
             header=true, all_varchar=true, nullstr=['NA',''])""")
    max_per_play, median_per_play = _one(con, """
        SELECT MAX(n), MEDIAN(n) FROM (
          SELECT COUNT(DISTINCT CAST(nfl_id AS BIGINT)) AS n
          FROM read_csv('""" + (RAW / "game_tracking_2023_sample.csv").as_posix() + """',
               header=true, all_varchar=true, nullstr=['NA',''])
          GROUP BY CAST(game_id AS BIGINT), CAST(play_id AS BIGINT))""")

    # 2^20 - 1 rows: the file is a truncated download, computed not typed.
    assert rows == 2 ** 20 - 1
    scope = summary["geometry_scope_note"]
    assert scope["rows_in_file"] == rows
    assert scope["players_tracked"] == players
    assert scope["plays_covered"] == plays
    assert scope["max_tracked_players_per_play"] == max_per_play
    assert max_per_play <= 4
    assert median_per_play == 1
    assert players < summary["availability"]["pp_players"], (
        "the sample tracks fewer players than the cohort: it cannot characterise it")
    assert "not data availability" in scope["note"]
    assert "structural" in scope["note"]


# ------------------------------------------------------- the turn-key trap


def test_candidate_rows_keep_every_turn(con, candidates, summary):
    """Grouping on three columns would silently drop the second turns."""
    assert summary["candidate_turn_keys"], "the turn-key check was not recorded"
    for stamp, parquet in (("SAMPLE", TURNS), ("FULL", FULL)):
        df = candidates[candidates.population == stamp]
        band_rows, band_keys = _one(con, f"""
            SELECT COUNT(*), COUNT(DISTINCT (game_id, play_id, nfl_id, frame_start))
            FROM ({_band_key_sql(parquet)})""")
        # One artefact row per band-turn row, and as many distinct four-column
        # keys as the turn table itself holds (a few rows repeat in the source).
        assert len(df) == band_rows
        assert df[KEY].drop_duplicates().shape[0] == band_keys
        if stamp == "SAMPLE":
            assert not df.duplicated(KEY).any(), (
                "the sample turn table has one row per turn")
        n_rows = len(df)
        n_player_key = df[["game_id", "play_id", "nfl_id"]].drop_duplicates().shape[0]
        assert n_player_key < n_rows, (
            f"{stamp}: the four-column key adds no rows over three columns — the "
            "data or the key has changed")
        assert (summary["candidate_turn_keys"][stamp]["rows_turn_key"] == n_rows)
        assert (summary["candidate_turn_keys"][stamp]["rows_player_key"] == n_player_key)
        assert (summary["candidate_turn_keys"][stamp]
                ["second_turns_dropped_by_player_key"] == n_rows - n_player_key)


def test_a_three_column_grouping_would_fail_this_test(module, candidates, summary):
    """Guard on the module itself: the key must name frame_start."""
    assert module.TURN_KEY == ("game_id", "play_id", "nfl_id", "frame_start")
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "frame_start" in src
    for stamp in ("SAMPLE", "FULL"):
        df = candidates[candidates.population == stamp]
        three_col = df.groupby(["game_id", "play_id", "nfl_id"]).size()
        assert (three_col > 1).any(), (
            f"{stamp}: no play has two turns by one DB, so this test has stopped "
            "protecting the four-column key")
        assert summary["candidate_turn_keys"][stamp]["second_turns_dropped_by_player_key"] > 0


# ------------------------------------------------------- candidate counts (recomputed from raw)


def test_candidate_counts_recompute_both_populations(con, candidates, summary):
    """Every per-turn count is re-derived here from player_play and the turn tables."""
    out = {}
    for stamp, parquet in (("SAMPLE", TURNS), ("FULL", FULL)):
        # DISTINCT: a few turn rows repeat in the full table, and a duplicated key
        # would fan the candidate join out (the module dedupes them the same way).
        con.execute(f"CREATE OR REPLACE TABLE k_{stamp.lower()} AS "
                    f"SELECT DISTINCT * FROM ({_band_key_sql(parquet)})")
        out[stamp] = con.execute(f"""
        WITH own AS (
          SELECT CAST(game_id AS BIGINT) game_id, CAST(play_id AS BIGINT) play_id,
                 CAST(nfl_id AS BIGINT) nfl_id,
                 MIN(team_abbr) FILTER (WHERE team_abbr IS NOT NULL) own_team
          FROM raw_pp GROUP BY 1, 2, 3
        ), p AS (
          SELECT CAST(game_id AS BIGINT) game_id, CAST(play_id AS BIGINT) play_id,
                 CAST(nfl_id AS BIGINT) nfl_id, team_abbr, route_ran,
                 TRY_CAST(separation_at_pass_forward AS DOUBLE) sep
          FROM raw_pp
        )
        SELECT t.game_id, t.play_id, t.nfl_id, t.frame_start,
          o.own_team,
          COUNT(p.nfl_id) FILTER (WHERE p.team_abbr IS NOT NULL
                                   AND p.team_abbr <> o.own_team) AS n_opp,
          COUNT(p.nfl_id) FILTER (WHERE p.team_abbr IS NOT NULL
                                   AND p.team_abbr <> o.own_team
                                   AND p.route_ran IS NOT NULL) AS n_route,
          COUNT(p.nfl_id) FILTER (WHERE p.team_abbr IS NOT NULL
                                   AND p.team_abbr <> o.own_team
                                   AND p.route_ran IS NOT NULL
                                   AND p.sep IS NOT NULL) AS n_sep
        FROM k_{stamp.lower()} t
        LEFT JOIN own o
          ON t.game_id = o.game_id AND t.play_id = o.play_id AND t.nfl_id = o.nfl_id
        LEFT JOIN p
          ON p.game_id = t.game_id AND p.play_id = t.play_id AND p.nfl_id <> t.nfl_id
        GROUP BY 1, 2, 3, 4, 5
        """).df()

        got = candidates[candidates.population == stamp].drop_duplicates(KEY)
        rec = out[stamp].drop_duplicates(KEY)
        assert len(got) == len(rec)
        merged = got.merge(rec, on=KEY, how="outer", indicator=True,
                           suffixes=("", "_rec"))
        assert (merged["_merge"] == "both").all(), (
            f"{stamp}: the artefact and the recomputation disagree on which turns exist")
        assert (merged["pp_opp_rows"] == merged["n_opp"]).all()
        assert (merged["pp_opp_route_rows"] == merged["n_route"]).all()
        assert (merged["pp_opp_route_sep_rows"] == merged["n_sep"]).all()
        assert (merged["own_team"].fillna("") == merged["own_team_rec"].fillna("")).all()

    # The two headline acceptance quantities, recomputed and confronted with the
    # module's own measurement rather than with a literal.
    for stamp in ("SAMPLE", "FULL"):
        df = candidates[candidates.population == stamp]
        assert len(df) == summary["preflight"][stamp]["band_turns"]
        assert int((df.pp_opp_route_rows >= 1).sum()) == (
            summary["preflight"][stamp]["p2_turns_with_candidate"])


def test_candidate_status_partitions_every_turn(candidates, summary):
    for stamp in ("SAMPLE", "FULL"):
        df = candidates[candidates.population == stamp]
        assert set(df.pp_candidate_status) <= {"none_on_play", "single", "multiple"}
        counts = df.pp_candidate_status.value_counts().to_dict()
        assert sum(counts.values()) == len(df)
        assert counts.get("none_on_play", 0) == int((df.pp_opp_route_rows == 0).sum())
        assert counts.get("single", 0) == int((df.pp_opp_route_rows == 1).sum())
        assert counts.get("multiple", 0) == int((df.pp_opp_route_rows >= 2).sum())
        totals = {r["pp_candidate_status"]: r["n_turns"]
                  for r in summary["candidate_status"][stamp]}
        assert totals == {k: int(v) for k, v in counts.items()}


# ------------------------------------------------------- the pre-flight


def test_preflight_verdicts_and_gate_arithmetic(preflight, summary):
    pf = preflight.set_index("population")
    assert set(pf.index) == {"SAMPLE", "FULL"}

    for stamp in ("SAMPLE", "FULL"):
        row = pf.loc[stamp]
        total = int(row.band_turns)
        ceiling = int(row.p2_turns_with_candidate)
        assert total == summary["preflight"][stamp]["band_turns"]
        # P3 / P4 recomputed from the gate constants, not compared to literals.
        assert bool(row.p3_cleared) == (ceiling >= int(row.p3_gate_min_turns))
        required = math.ceil(float(row.p4_gate_min_share) * total)
        assert int(row.p4_required_turns) == required
        assert bool(row.p4_cleared) == (ceiling >= required)
        assert row.p2_ceiling_share == pytest.approx(ceiling / total, abs=1e-6)
        # P1: the artefact's own turn table must reconcile.
        assert int(row.p1_unsided_turns) == (
            int(row.p1_turns_without_player_play_row)
            + int(row.p1_turns_without_team_label)
            + int(row.p1_turns_with_conflicting_team_label))

    full = pf.loc["FULL"]
    sample = pf.loc["SAMPLE"]
    assert full.verdict == "GATE LIVE"
    assert sample.verdict.startswith("GATE UNREACHABLE")
    assert bool(full.stop) is False
    assert bool(sample.stop) is False
    assert bool(full.p3_cleared) and bool(full.p4_cleared)
    assert bool(sample.p4_cleared) is False


def test_preflight_p1_is_clean_on_the_real_data(preflight):
    """Every band turn is sided: no turn lacks a player_play row or a team label."""
    for stamp in ("SAMPLE", "FULL"):
        row = preflight.set_index("population").loc[stamp]
        assert int(row.p1_unsided_turns) == 0
        assert row.p1_unsided_turn_keys == "" or pd.isna(row.p1_unsided_turn_keys)


def test_preflight_takes_no_tracking_argument(module):
    """The pre-flight must never be able to read frame-level data."""
    sig = inspect.signature(module.preflight)
    names = list(sig.parameters)
    assert names == ["con", "candidates_table", "population"], names
    assert not any(t in n.lower() for n in names for t in ("track", "path", "file", "csv"))
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "game_tracking" not in src
    assert "read_parquet" not in src.split("def preflight")[1].split("def ")[0]


def test_preflight_p5_states_the_cohort_ceiling(preflight):
    for stamp in ("SAMPLE", "FULL"):
        row = preflight.set_index("population").loc[stamp]
        text = row.p5_wording
        assert "Cohort ceiling" in text
        assert "lower bound" in text
        # The counts in the sentence are the artefact's own, not typed.
        assert f"{int(row.p5_turns_no_opposing_row):,}" in text
        assert f"{int(row.band_turns):,}" in text
        assert row.p5_share_no_opposing_row == pytest.approx(
            int(row.p5_turns_no_opposing_row) / int(row.band_turns), abs=1e-6)


# ------------------------------------------------------- ties, counted and never resolved


def test_ties_are_counted_from_the_candidate_rows(cand_detail, summary):
    sample = cand_detail[cand_detail.population == "SAMPLE"]
    ties = summary["ambiguity_candidates"]["SAMPLE"]

    two = sample[sample.n_candidates_on_turn == 2]
    n_two = two[KEY].drop_duplicates().shape[0]
    assert ties["turns_with_two_candidates"] == n_two
    assert n_two > 0

    both_sep = two[two.n_candidates_with_separation == 2][
        KEY + ["turn_separation_gap_yd"]].drop_duplicates(KEY)
    assert ties["two_candidate_turns_with_both_separations"] == len(both_sep)

    gaps = sorted(both_sep.groupby(KEY)["turn_separation_gap_yd"].first().tolist())
    assert ties["two_candidate_separation_gap_min_yd"] == round(gaps[0], 3)
    assert ties["two_candidate_separation_gap_max_yd"] == round(gaps[-1], 3)
    assert ties["two_candidate_separation_gap_median_yd"] == round(
        statistics.median(gaps), 3)
    assert gaps[0] > 0, "two candidates carrying the same separation would be a duplicate row"


def test_no_tie_is_resolved_anywhere(cand_detail, per_turn):
    banned = ("matched", "chosen", "resolved", "winner", "selected", "attributed")
    assert [c for c in cand_detail.columns
            if any(b in c.lower() for b in banned)] == []
    # The geometric match never keeps an id on an ambiguous turn.
    assert per_turn.loc[per_turn.match_status != "unique",
                        "matched_receiver_nfl_id"].isna().all()
    two = cand_detail[cand_detail.n_candidates_on_turn == 2]
    # Every two-candidate turn holds two distinct players: no duplicate row.
    for _, grp in two.groupby(KEY):
        assert grp.candidate_nfl_id.nunique() == 2


def test_each_two_candidate_turn_is_an_ordinary_pair(cand_detail):
    """12 of the 13 sit on distinct plays; one play carries two different DBs."""
    two = cand_detail[cand_detail.n_candidates_on_turn == 2]
    per_turn = two[KEY].drop_duplicates()
    plays = per_turn[["game_id", "play_id"]].drop_duplicates()
    assert len(plays) < len(per_turn), (
        "expected at least one play with two turning DBs — the four-column key's point")
    assert len(per_turn) - len(plays) >= 1


# ------------------------------------------------------- the geometric sweep is still the sample's


def test_route_table_covers_every_route(routes, summary, preflight):
    assert set(routes.route_id) == {"R1", "R2", "R3", "R4", "R5"}
    idx = routes.set_index("route_id")
    # The two geometric routes are inferences and must say so.
    assert idx.loc[["R1", "R2"], "attribution"].str.contains("INFERENCE").all()
    r1 = idx.loc["R1"]
    assert r1.sample_turns_available == summary["turns"]["sample_band_turns"]
    assert r1.sample_turns_usable == summary["match"]["unique"]
    assert r1.sample_attributed_fraction == summary["match"]["attributed_fraction"]
    r5 = idx.loc["R5"]
    assert r5.sample_turns_usable == summary["injury"]["turns_where_turning_db_named"]

    # R1/R2 carry the candidate ceiling beside the geometric count.
    pf = preflight.set_index("population")
    assert r1.sample_candidate_ceiling_turns == pf.loc["SAMPLE"].p2_turns_with_candidate
    assert r1.full_candidate_ceiling_turns == pf.loc["FULL"].p2_turns_with_candidate
    assert r1.sample_candidate_ceiling_share == pytest.approx(
        pf.loc["SAMPLE"].p2_ceiling_share, abs=1e-6)
    assert r1.full_candidate_ceiling_share == pytest.approx(
        pf.loc["FULL"].p2_ceiling_share, abs=1e-6)
    assert "sample file" in r1.sample_turns_usable_source
    for col in ("sample_candidate_ceiling_turns", "sample_candidate_ceiling_share",
                "full_candidate_ceiling_turns", "full_candidate_ceiling_share"):
        assert col in routes.columns
    # R2's ceiling is the MAN-restricted candidate count, never above R1's.
    assert idx.loc["R2"].sample_candidate_ceiling_turns <= r1.sample_candidate_ceiling_turns


def test_geometric_ceiling_is_a_ceiling(per_turn, summary):
    """The geometric unique count can never exceed the candidate ceiling."""
    for stamp in ("SAMPLE",):
        ceiling = summary["preflight"][stamp]["p2_turns_with_candidate"]
        assert summary["match"]["unique"] <= ceiling
        assert summary["match"]["unique"] + summary["match"]["multiple"] <= ceiling


# ------------------------------------------------------- the note


def test_note_carries_the_correction_block(summary, con):
    text = NOTE.read_text(encoding="utf-8")
    assert CORRECTION_SPEC.exists(), "the correction spec is the binding record of this cycle"
    assert "CORRECTION" in text
    # It must be impossible to read the note without seeing it.
    assert text.index("CORRECTION") < text.index("## 1"), (
        "the correction block must sit above §1")
    # The false stop-check is quoted, and marked false — not repeated as instruction.
    assert "well under ~15 rows per play" in text
    block = text[:text.index("## 1")]
    assert "false" in block.lower()
    corrected = block.lower()
    assert "unmeasured" in corrected
    assert "not zero" in corrected or "unmeasured, not" in corrected
    assert "candidate" in corrected


def test_note_quotes_the_artefacts(summary, con):
    text = NOTE.read_text(encoding="utf-8")
    # Turn counts, as the artefacts hold them.
    assert f"{summary['turns']['sample_band_turns']:,}" in text
    assert f"{summary['full']['full_band_turns']:,}" in text
    assert f"{summary['full']['full_band_plays']:,}" in text
    # The DB-alignment separation count is computed, never typed.
    db_rows = summary["sample"]["db_alignment_rows"]
    assert f"0 of {db_rows:,}" in text
    assert "**INFERENCE**" in text
    # The candidate ceiling sits beside the geometric count; 1.2% is no longer the
    # headline — the ceiling is.
    ceiling = summary["preflight"]["FULL"]["p2_turns_with_candidate"]
    share = summary["preflight"]["FULL"]["p2_ceiling_share"]
    assert f"{ceiling:,}" in text
    assert f"{share:.1%}" in text
    assert "unobservable" in text
    # Provenance stamps are carried, not merged.
    assert "[SAMPLE]" in text and "[FULL]" in text
    assert "no result" in text.lower()


def test_note_no_longer_asserts_a_rows_per_play_stop_condition():
    text = NOTE.read_text(encoding="utf-8")
    body = text[text.index("## 1"):]
    live = [line for line in body.splitlines()
            if "rows per play" in line.lower() and "~15" in line]
    assert live == [], (
        "a live rows-per-play stop condition survives below the correction block: "
        f"{live}")
    # §6.1 must be the corrected pre-flight, and must name P1-P5.
    assert "P1" in body and "P5" in body


def test_note_makes_no_finding():
    text = NOTE.read_text(encoding="utf-8").lower()
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
    assert "Non-goals" in SPEC.read_text(encoding="utf-8")


# ------------------------------------------------------- the pre-flight notebook


def _notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells(nb: dict) -> list[str]:
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def test_preflight_notebook_is_committed_unexecuted():
    nb = _notebook()
    assert nb["nbformat"] == 4
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [], "the notebook is committed with baked outputs"
            assert cell["execution_count"] is None


def test_preflight_notebook_is_portable():
    src = "\n".join(_code_cells(_notebook()))
    assert "import resource" not in src
    assert "sys.platform" not in src and "os.name" not in src
    assert "os.sep" not in src and "os.path.join" not in src
    for line in src.splitlines():
        if ".csv" in line or ".parquet" in line:
            assert "\\\\" not in line, f"a Windows path separator: {line}"
    # The notebook performs no file writes: artefacts are the script's job.
    assert "write_text(" not in src and "to_csv(" not in src
    for line in src.splitlines():
        if "read_csv(" in line or "read_parquet(" in line:
            assert ".as_posix()" in line, f"SQL path without .as_posix(): {line}"


def test_preflight_notebook_does_not_read_the_tracking_sample():
    src = "\n".join(_code_cells(_notebook()))
    assert "game_tracking" not in src, (
        "the pre-flight notebook must not take the tracking sample as an input")


def test_preflight_notebook_cell_executes_and_prints_the_verdict(summary):
    """§4 runs against the real player_play and prints P1-P5 and the verdict."""
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError:  # pragma: no cover - nbclient is optional locally
        pytest.skip("nbclient is not installed")

    node = nbformat.reads(NOTEBOOK.read_text(encoding="utf-8"), as_version=4)
    # Only §1-§4: exactly the cell under test, so the test stays quick.
    cut = next(i for i, c in enumerate(node.cells)
               if c["cell_type"] == "markdown" and "## 5." in c["source"])
    node.cells = node.cells[:cut]
    NotebookClient(node, timeout=900, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT)}}).execute()

    text = "".join(o.get("text", "")
                   for c in node.cells if c.cell_type == "code"
                   for o in c.outputs if o.output_type == "stream")
    for marker in ("P1 siding", "P2 pool", "P3 count", "P4 share",
                   "P5 cohort ceiling", "verdict"):
        assert marker in text, f"the pre-flight cell does not print {marker!r}"
    for stamp in ("SAMPLE", "FULL"):
        expected = summary["preflight"][stamp]["verdict"]
        assert expected in text, f"{stamp} verdict {expected!r} not printed"
    assert "no real stop condition fired" in text
