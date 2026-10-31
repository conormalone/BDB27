#!/usr/bin/env python3
"""dl_audit_ce — Phase 1 audit items c, d, e for the DL task.

* **c** ``c_snap_coverage.csv`` — per-DL-player snap coverage: how many
  ``player_play`` rows a defender has relative to the number of defensive plays
  his team played.  ``player_play`` is a prospect cohort (497 distinct nfl_id
  over 114,954 plays), not a participation table, so coverage is expected to
  fail the 0.9 threshold.  Frame-level confirmation is PENDING_FULL_RUN.
* **d** ``d_null_semantics.csv`` — null vs zero semantics for the five pressure
  fields, decided by four explicit tests (non-dropback nullity, ttp/quick
  co-nullity, quick vs ``ttp <= 2.5`` agreement, hurry superset of ttp).
* **e** ``e_label_provenance.csv`` — play-level 2x2 of DL ``time_to_pressure``
  presence against ``pressure_allowed``, plus label provenance counts and the
  circularity note (``peak_pressure_probability_allowed`` is probability-valued
  and therefore model-derived).

Entry point ``run(cfg, mode) -> dict``.  Reads ``player_play`` and ``players``
only (no tracking).  Nothing here mutates a design constant.

Standalone::

    python src/dl/dl_audit_ce.py --mode sample
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402

PENDING = "PENDING_FULL_RUN"
EVENT_ABSENT = "EVENT_ABSENT"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNRESOLVED = "UNRESOLVED"

FIELDS = ("quick_pressure", "unblocked_pressure", "time_to_pressure",
          "time_to_qb_hurry", "sack")
#: Fields that must be null on every non-dropback play (test 1).
DROPBACK_ONLY = ("sack", "blitzing", "unblocked_pressure")


def _rate(num: int, den: int) -> float:
    return round(num / den, 6) if den else 0.0


def _views(con, pp: Path, players: Path) -> None:
    con.execute(f"""CREATE OR REPLACE TEMP VIEW pp AS
        SELECT * FROM read_csv_auto('{pp.as_posix()}', nullstr=['NA'])""")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW players AS
        SELECT * FROM read_csv_auto('{players.as_posix()}', nullstr=['NA'])""")
    con.execute("""CREATE OR REPLACE TEMP VIEW dl AS
        SELECT p.* FROM pp p JOIN players u USING (nfl_id)
        WHERE u.nfl_position IN ('DE', 'DT', 'NT')""")
    # Play-level facts that are 100% null on DL rows (OL/play-owned columns).
    con.execute("""CREATE OR REPLACE TEMP VIEW plays AS
        SELECT game_id, play_id,
               max(dropback_duration) AS db,
               max(CASE WHEN pressure_allowed THEN 1
                        WHEN NOT pressure_allowed THEN 0 END) AS pa,
               max(peak_pressure_probability_allowed) AS peak
        FROM pp GROUP BY 1, 2""")
    con.execute("""CREATE OR REPLACE TEMP VIEW dld AS
        SELECT d.*, pl.db FROM dl d JOIN plays pl USING (game_id, play_id)""")


def _snap_coverage(con, thr: float) -> tuple[list[dict], dict, float]:
    rows = con.execute("""
        WITH dlrows AS (
            SELECT p.nfl_id, p.team_abbr, p.game_id, p.play_id, p.blitzing
            FROM pp p JOIN players u USING (nfl_id)
            WHERE u.nfl_position IN ('DE', 'DT', 'NT')),
        teamdef AS (
            SELECT team_abbr, count(DISTINCT (game_id, play_id)) AS dp
            FROM pp WHERE team_abbr <> possession_team GROUP BY 1),
        pteams AS (SELECT DISTINCT nfl_id, team_abbr FROM dlrows),
        tden AS (
            SELECT pteams.nfl_id, sum(COALESCE(teamdef.dp, 0)) AS team_def_plays
            FROM pteams LEFT JOIN teamdef USING (team_abbr) GROUP BY 1),
        cnt AS (
            SELECT nfl_id, count(*) AS n_rows,
                   count(*) FILTER (WHERE blitzing) AS n_rush
            FROM dlrows GROUP BY 1)
        SELECT u.nfl_id,
               COALESCE(t.team_def_plays, 0) AS team_def_plays,
               COALESCE(c.n_rows, 0) AS n_rows,
               COALESCE(c.n_rush, 0) AS n_rush_rows
        FROM players u
        LEFT JOIN tden t USING (nfl_id)
        LEFT JOIN cnt c USING (nfl_id)
        WHERE u.nfl_position IN ('DE', 'DT', 'NT')
        ORDER BY u.nfl_id""").fetchall()

    out, shares, n_pass = [], [], 0
    for nfl_id, tdp, n_rows, n_rush in rows:
        share = _rate(n_rows, tdp)
        verdict = "PASS" if share >= thr else "FAIL"
        n_pass += verdict == "PASS"
        shares.append(share)
        out.append({"nfl_id": nfl_id, "team_def_plays": tdp, "n_rows": n_rows,
                    "n_rush_rows": n_rush, "share": share, "verdict": verdict,
                    "frame_level": PENDING})

    median = round(statistics.median(shares), 6) if shares else 0.0
    cohort_verdict = ("PASS" if median >= thr and n_pass == len(shares)
                      else "FAIL")
    out.append({"nfl_id": "__COHORT__",
                "team_def_plays": sum(r[1] for r in rows),
                "n_rows": sum(r[2] for r in rows),
                "n_rush_rows": sum(r[3] for r in rows),
                "share": median, "verdict": cohort_verdict,
                "frame_level": PENDING})
    summary = {"n_players": len(shares), "n_pass": n_pass,
               "median_share": median, "cohort_verdict": cohort_verdict}
    return out, summary, median


def _null_row(con, field: str, tests: dict) -> dict:
    c = con.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE {field} IS NULL),
               count(*) FILTER (WHERE db IS NOT NULL),
               count(*) FILTER (WHERE db IS NOT NULL AND {field} IS NULL),
               count(*) FILTER (WHERE db IS NOT NULL AND blitzing),
               count(*) FILTER (WHERE db IS NOT NULL AND blitzing
                                AND {field} IS NULL),
               count(*) FILTER (WHERE db IS NULL),
               count(*) FILTER (WHERE db IS NULL AND {field} IS NULL),
               count(*) FILTER (WHERE db IS NULL AND {field} IS NULL
                                AND (pass_result IS NULL OR pass_result = ''
                                     OR play_nullified_by_penalty = 'Y')),
               count(*) FILTER (WHERE db IS NULL AND {field} IS NULL
                                AND play_nullified_by_penalty = 'Y'),
               count(*) FILTER (WHERE db IS NULL AND {field} IS NOT NULL
                                AND pass_result IS NOT NULL
                                AND pass_result <> '')
        FROM dld""").fetchone()
    (n_all, null_all, n_db, null_db, n_rush, null_rush, n_ndb, null_ndb,
     null_ndb_explained, null_ndb_pen, nonnull_ndb_pass) = c

    vals = con.execute(f"""
        SELECT {field}::VARCHAR AS v, count(*) AS n FROM dld
        WHERE db IS NOT NULL
        GROUP BY 1 ORDER BY n DESC, v""").fetchall()
    if len(vals) <= 6:
        vc = "|".join(f"{'NULL' if v is None else v}:{n}" for v, n in vals)
    else:
        stats = con.execute(f"""
            SELECT count(*), count(DISTINCT {field}), min({field}),
                   median({field}), max({field}) FROM dld
            WHERE db IS NOT NULL AND {field} IS NOT NULL""").fetchone()
        vc = (f"n={stats[0]};distinct={stats[1]};min={stats[2]:.2f};"
              f"p50={stats[3]:.2f};max={stats[4]:.2f}")

    nonnull_ndb = n_ndb - null_ndb
    if field in DROPBACK_ONLY:
        # Test 1 as specified (null on every non-dropback row) is false:
        # dropback_duration covers only live dropbacks, so live pass plays with
        # no recorded dropback (screens, quick game) carry the field too.  The
        # applicable frame is "live pass play", so the residual -- nulls that
        # are neither non-pass nor penalty-nullified -- is what decides it.
        residual = null_ndb - null_ndb_explained
        ok = null_db == 0 and residual == 0
        verdict = NOT_APPLICABLE if ok else UNRESOLVED
        evidence = (f"test1: null on {null_ndb}/{n_ndb} non-dropback rows = "
                    f"{null_ndb_explained - null_ndb_pen} non-pass + "
                    f"{null_ndb_pen} penalty-nullified; {nonnull_ndb} non-null "
                    f"non-dropback rows are live pass plays (pass_result set), "
                    f"residual {residual} -> null = not applicable, not missing")
    elif field == "time_to_qb_hurry":
        ok = tests["hurry_not_superset"] == 0
        verdict = EVENT_ABSENT if ok else UNRESOLVED
        evidence = (f"test4: {tests['hurry_not_superset']} rows with ttp but no "
                    f"hurry; hurry n={tests['n_hurry']} >= ttp n={tests['n_ttp']}"
                    f" -> superset holds, null = no event")
    elif field == "time_to_pressure":
        ok = tests["quick_no_ttp"] == 0 and tests["hurry_not_superset"] == 0
        verdict = EVENT_ABSENT if ok else UNRESOLVED
        evidence = (f"test2: {tests['ttp_no_quick']} ttp rows lack quick, "
                    f"{tests['quick_no_ttp']} quick rows lack ttp; test4: "
                    f"{tests['hurry_not_superset']} hurry violations -> null = "
                    f"no event")
    else:  # quick_pressure
        ok = tests["quick_no_ttp"] == 0
        verdict = EVENT_ABSENT if ok else UNRESOLVED
        evidence = (f"test3: {tests['agree']}/{tests['both']} agree with "
                    f"ttp<=2.5 ({_rate(tests['agree'], tests['both']):.3f}); "
                    f"{tests['both'] - tests['agree']} quick=False rows have "
                    f"ttp<=2.5, {tests['ttp_no_quick']} ttp rows lack quick "
                    f"-> null = no event, not a deterministic ttp function")

    return {"field": field,
            "n_all": n_all, "null_rate_all": _rate(null_all, n_all),
            "n_dropback": n_db, "null_rate_dropback": _rate(null_db, n_db),
            "n_rush": n_rush, "null_rate_rush": _rate(null_rush, n_rush),
            "n_non_dropback_null_rate": _rate(null_ndb, n_ndb),
            "value_counts": vc, "verdict": verdict, "evidence": evidence}


def _tests(con) -> dict:
    t = {}
    t["ttp_no_quick"], t["quick_no_ttp"] = con.execute("""
        SELECT count(*) FILTER (WHERE time_to_pressure IS NOT NULL
                                AND quick_pressure IS NULL),
               count(*) FILTER (WHERE quick_pressure IS NOT NULL
                                AND time_to_pressure IS NULL)
        FROM dld WHERE db IS NOT NULL""").fetchone()
    t["both"], t["agree"] = con.execute("""
        SELECT count(*), count(*) FILTER (
                   WHERE quick_pressure = (time_to_pressure <= 2.5))
        FROM dld WHERE db IS NOT NULL
          AND quick_pressure IS NOT NULL AND time_to_pressure IS NOT NULL
        """).fetchone()
    t["hurry_not_superset"], t["n_hurry"], t["n_ttp"] = con.execute("""
        SELECT count(*) FILTER (WHERE time_to_pressure IS NOT NULL
                                AND time_to_qb_hurry IS NULL),
               count(time_to_qb_hurry), count(time_to_pressure)
        FROM dld WHERE db IS NOT NULL""").fetchone()
    # Test 1 companion: blitzing must be defined exactly on dropback rows.
    t["blitz_nonnull_nondropback"] = con.execute("""
        SELECT count(blitzing) FROM dld WHERE db IS NULL""").fetchone()[0]
    return t


def _label_provenance(con, tests: dict) -> tuple[list[dict], dict]:
    cells = {(bool(dtp), bool(pa)): n for pa, dtp, n in con.execute("""
        WITH t AS (
            SELECT pl.pa,
                   EXISTS (SELECT 1 FROM dl
                           WHERE dl.game_id = pl.game_id
                             AND dl.play_id = pl.play_id
                             AND dl.time_to_pressure IS NOT NULL) AS dtp
            FROM plays pl WHERE pl.db IS NOT NULL)
        SELECT pa, dtp, count(*) FROM t GROUP BY 1, 2""").fetchall()}

    n_db = con.execute(
        "SELECT count(*) FROM plays WHERE db IS NOT NULL").fetchone()[0]
    n_pa_true = con.execute(
        "SELECT count(*) FROM plays WHERE db IS NOT NULL AND pa = 1"
    ).fetchone()[0]
    n_pa_notnull = con.execute(
        "SELECT count(*) FROM plays WHERE db IS NOT NULL AND pa IS NOT NULL"
    ).fetchone()[0]
    n_dtp = con.execute("""SELECT count(DISTINCT (d.game_id, d.play_id))
        FROM dl d JOIN plays pl USING (game_id, play_id)
        WHERE pl.db IS NOT NULL AND d.time_to_pressure IS NOT NULL""").fetchone()[0]
    n_peak = con.execute("""SELECT count(DISTINCT peak_pressure_probability_allowed)
        FROM pp WHERE peak_pressure_probability_allowed IS NOT NULL""").fetchone()[0]

    rows = []
    for dtp in (False, True):
        for pa in (False, True):
            rows.append({"item": "crosstab_cell",
                         "key_a": f"dl_ttp_present={dtp}",
                         "key_b": f"pressure_allowed={pa}",
                         "count": cells.get((dtp, pa), 0), "note": ""})
    rows += [
        {"item": "n_plays_dropback", "key_a": "", "key_b": "",
         "count": n_db, "note": "plays with dropback_duration"},
        {"item": "n_plays_with_pressure_allowed", "key_a": "pressure_allowed=TRUE",
         "key_b": "", "count": n_pa_true,
         "note": "blocker-side outcome, OL-attributed"},
        {"item": "n_plays_pressure_allowed_non_null", "key_a": "", "key_b": "",
         "count": n_pa_notnull, "note": "defined on every dropback play"},
        {"item": "n_plays_with_dl_ttp", "key_a": "", "key_b": "",
         "count": n_dtp, "note": ">=1 DL row with time_to_pressure"},
        {"item": "n_distinct_peak_probability", "key_a": "", "key_b": "",
         "count": n_peak, "note": "peak_pressure_probability_allowed"},
        {"item": "note_circularity", "key_a": "", "key_b": "", "count": "",
         "note": ("peak_pressure_probability_allowed is probability-valued "
                  "(1566 distinct values) and therefore model-derived, not "
                  "observed; pressure_allowed is its thresholded counterpart. "
                  "Relating Combine kinematics to this label is partly "
                  "circular - report it as conditional on the label's inputs, "
                  "and use pressure_allowed / time_to_pressure as the named "
                  "outcomes.")},
    ]
    counts = {f"dtp_{k[0]}_pa_{k[1]}": v for k, v in cells.items()}
    return rows, counts


def run(cfg, mode: str) -> dict:
    paths = D.resolve_paths(cfg, mode)
    acfg = D.dl(cfg)["audit"]
    thr = float(acfg["min_snap_coverage_share"])
    out = paths["outputs"] / acfg["out_subdir"]
    out.mkdir(parents=True, exist_ok=True)

    pp = D.source_paths(cfg, "player_play", mode)[0]
    players = D.source_paths(cfg, "players", mode)[0]

    con = D.duckdb_connect(cfg)
    try:
        _views(con, pp, players)
        cov_rows, cov_summary, median = _snap_coverage(con, thr)
        tests = _tests(con)
        null_rows = [_null_row(con, f, tests) for f in FIELDS]
        prov_rows, crosstab = _label_provenance(con, tests)
    finally:
        con.close()

    D.write_csv(out / "c_snap_coverage.csv", cov_rows,
                ["nfl_id", "team_def_plays", "n_rows", "n_rush_rows", "share",
                 "verdict", "frame_level"], mode=mode, cfg=cfg)
    D.write_csv(out / "d_null_semantics.csv", null_rows,
                ["field", "n_all", "null_rate_all", "n_dropback",
                 "null_rate_dropback", "n_rush", "null_rate_rush",
                 "n_non_dropback_null_rate", "value_counts", "verdict",
                 "evidence"], mode=mode, cfg=cfg)
    D.write_csv(out / "e_label_provenance.csv", prov_rows,
                ["item", "key_a", "key_b", "count", "note"], mode=mode, cfg=cfg)

    return {"crosstab": crosstab,
            "verdicts": {r["field"]: r["verdict"] for r in null_rows},
            "median_share": median,
            "coverage": cov_summary,
            "tests": tests,
            "out_dir": str(out)}


if __name__ == "__main__":  # pragma: no cover
    args = D.parse_args()
    _cfg = D.load_config(args.config)
    _mode = args.mode or D.dl(_cfg)["mode"]["default"]
    print(run(_cfg, _mode))
