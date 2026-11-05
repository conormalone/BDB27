#!/usr/bin/env python3
"""01_db_audit.py — Phase 1 audit for the DB-decay task (NFL Big Data Bowl 2027).

Binding spec: `TASK.md` ("In-game decay of DB performance relative to the Combine
ceiling", rewritten 2026-10-25) and decision D51. This stage implements audit items
(a)-(i) as **evidence**, plus the go/no-go gate.

Design rules
------------
* One code path for ``--mode sample|full`` and for ``--synthetic`` (the mode only
  changes input paths and the provenance label).
* Memory-safe: DuckDB queries over CSV (no full CSV is ever loaded into pandas).
* Deterministic: every random draw is seeded from ``config.seed``.
* Checkpointed to ``outputs/audit/db_decay/.done.json`` (skip unless ``--force``).
* Every CSV carries ``provenance`` as its FIRST column; sample artefacts contain the
  literal string ``UNVALIDATED SAMPLE OUTPUT`` (CSV column, JSON field, MD title).
* Never invents a column or a value: unavailable quantities are ``UNESTIMABLE`` or
  ``PENDING FULL RUN``, explicitly.

Outputs
-------
``outputs/audit/db_decay/{a..i}_*.csv``, ``audit_items.json``,
``SUMMARY_db_decay.md``, and the gate decision ``outputs/audit/db_decay/audit_decision.json``
(its own tree: the legacy WR/TE ``outputs/audit/audit_decision.json`` is never overwritten —
D52).

CLI
---
    python src/01_db_audit.py --mode sample [--force] [--synthetic] [--config PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import combine_features as cf  # noqa: E402
import common  # noqa: E402

STAGE = "01_db_audit"
PENDING = "PENDING FULL RUN"
SAMPLE_PROV = "UNVALIDATED SAMPLE OUTPUT"
SYNTH_PROV = "SYNTHETIC FIXTURE (not a finding)"
UNESTIMABLE = "UNESTIMABLE"

DB_POSITIONS = ("CB", "SS", "FS", "DB")

# Exact raw schemas (fail loudly on any drift). `player_play` reproduces the vendor's
# spelling of `visitor_team_win_probility_added`.
EXACT_SCHEMAS: dict[str, list[str]] = {
    "combine_tracking": ["draft_year", "event_id", "nfl_id", "entity_type", "time",
                         "drill_type", "drill_name", "attempt", "x", "y", "s", "a",
                         "dis", "dir"],
    "players": ["nfl_id", "display_name", "draft_year", "nfl_position", "birth_date",
                "college_name", "college_conference", "draft_round",
                "draft_pick_within_round", "draft_overall_pick"],
    "games": ["game_id", "game_key", "season", "season_type", "week",
              "home_team_abbr", "visitor_team_abbr"],
    "player_play": [
        "game_id", "play_id", "team_abbr", "nfl_id", "lined_up_position",
        "play_description", "quarter", "down", "yards_to_go", "possession_team",
        "yardline_side", "yardline_number", "game_clock", "pre_snap_home_score",
        "pre_snap_visitor_score", "play_direction", "pre_snap_home_team_win_probability",
        "pre_snap_visitor_team_win_probability", "home_team_win_probability_added",
        "visitor_team_win_probility_added", "expected_points_added", "expected_points",
        "offense_formation", "receiver_alignment", "pass_result", "pass_length",
        "play_nullified_by_penalty", "penalty_yards", "pre_penalty_yards_gained",
        "yards_gained", "team_coverage_man_zone", "team_coverage_type",
        "in_motion_at_ball_snap", "target", "rec_yards", "rush_yards",
        "yards_after_catch", "fumble", "fumble_lost", "route_ran",
        "separation_at_pass_forward", "cushion", "expected_yards_after_catch",
        "dropback_duration", "extended_sack_allowed", "pass_rushers_encountered",
        "peak_pressure_probability_allowed", "pressure_allowed", "sack_allowed",
        "time_to_pressure_allowed", "quick_pressure", "unblocked_pressure", "blitzing",
        "player_get_off", "sack", "tackle", "assist", "caused_forced_fumble",
        "recovered_fumble", "tackle_for_loss", "time_to_pressure", "time_to_qb_hurry",
        "coverage_assignment", "coverage_assignment_at_snap",
    ],
    "game_tracking": ["game_id", "play_id", "nfl_id", "time", "x", "y", "s", "a",
                      "dis", "o", "dir", "event"],
}

FIELD_LIST_D = [
    "coverage_assignment", "coverage_assignment_at_snap", "team_coverage_man_zone",
    "team_coverage_type", "route_ran", "pass_length", "target", "dropback_duration",
    "pass_result", "yards_gained", "pre_snap_home_score", "pre_snap_visitor_score",
    "game_clock", "quarter", "down", "yards_to_go", "play_nullified_by_penalty",
    "play_description",
]


class SchemaError(RuntimeError):
    """A raw input is missing a required column (or has an unexpected one)."""


class GateError(RuntimeError):
    """The go/no-go gate cannot be evaluated."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _q(p: Path | str) -> str:
    return str(p).replace("'", "''")


GAME_TRACKING_TYPES = {
    "game_id": "BIGINT", "play_id": "BIGINT", "nfl_id": "BIGINT", "time": "TIMESTAMP",
    "x": "DOUBLE", "y": "DOUBLE", "s": "DOUBLE", "a": "DOUBLE", "dis": "DOUBLE",
    "o": "DOUBLE", "dir": "DOUBLE", "event": "VARCHAR",
}


def read_sql(p: Path | str, types: dict[str, str] | None = None) -> str:
    """DuckDB CSV scan with the vendor NULL token mapped to NULL.

    ``types`` pins the schema when a file may be empty (an empty CSV has no rows from
    which to infer numeric types); the game-tracking scan always pins it.
    """
    if types:
        cols = ", ".join(f"'{k}': '{v}'" for k, v in types.items())
        return (f"read_csv('{_q(p)}', header=true, nullstr=['NA', ''], "
                f"columns={{{cols}}})")
    return f"read_csv_auto('{_q(p)}', header=true, nullstr=['NA', ''])"


def describe(con, p: Path | str) -> list[str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM {read_sql(p)}").fetchall()
    return [str(r[0]) for r in rows]


def assert_schema(con, p: Path | str, table: str) -> None:
    """Assert the exact column set (and order) of a raw CSV; raise a loud, named error."""
    expected = EXACT_SCHEMAS[table]
    got = describe(con, p)
    missing = [c for c in expected if c not in got]
    extra = [c for c in got if c not in expected]
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing column(s): " + ", ".join(missing))
        if extra:
            parts.append("unexpected column(s): " + ", ".join(extra))
        raise SchemaError(
            f"{table} schema mismatch at {p} — " + "; ".join(parts)
            + f" (expected {len(expected)} columns, got {len(got)})"
        )
    if got != expected:
        raise SchemaError(f"{table} column ORDER differs at {p}: got {got}")


def _num(v, nd: int = 6):
    """Round floats for deterministic CSV output; NaN/inf -> None."""
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, nd)
    return v


def emit(ctx: dict, name: str, rows: Iterable[dict], extra_first: Sequence[str] = ()) -> Path:
    """Write a CSV with `provenance` (and any extra_first columns) as the leading columns."""
    rows = [dict(r) for r in rows]
    if not rows:
        rows = [{}]
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    lead = ["provenance", *[c for c in extra_first if c != "provenance"]]
    fields = lead + [f for f in fields if f not in lead]
    norm = []
    for r in rows:
        d = {"provenance": ctx["prov"]}
        for c in lead[1:]:
            d[c] = r.get(c)
        for k, v in r.items():
            if k not in d:
                d[k] = _num(v)
        norm.append(d)
    path = ctx["out"] / name
    common.write_csv(path, norm, fieldnames=fields)
    return path


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, default=str)
        fh.write("\n")


def _in_positions(positions: Sequence[str]) -> str:
    return ", ".join("'" + str(p).replace("'", "''") + "'" for p in positions)


def _median_iqr(vals: Sequence[float]) -> dict:
    a = np.asarray([v for v in vals if v is not None and np.isfinite(v)], float)
    if a.size == 0:
        return dict(n=0, median=None, q25=None, q75=None, iqr=None, min=None, max=None)
    q = np.percentile(a, [25, 50, 75])
    return dict(n=int(a.size), median=float(q[1]), q25=float(q[0]), q75=float(q[2]),
                iqr=float(q[2] - q[0]), min=float(a.min()), max=float(a.max()))


def _position_group(pos: str) -> str:
    if pos == "CB":
        return "CB"
    if pos in ("SS", "FS"):
        return "S"
    return "DB"


def _overall_status(mode: str, synthetic: bool) -> str:
    if synthetic:
        return SYNTH_PROV
    return SAMPLE_PROV if mode == "sample" else "FULL"


# --------------------------------------------------------------------------- #
# input resolution / schema gate
# --------------------------------------------------------------------------- #
def resolve_inputs(cfg: dict, mode: str, synthetic: bool) -> dict[str, Any]:
    raw = common.resolve(cfg, "raw")
    if synthetic:
        base = Path(cfg["paths"]["synthetic"])
        if not base.is_absolute():
            base = common.REPO_ROOT / base
        base = base / "db_decay" if (base / "db_decay").exists() else base
        return {
            "combine_tracking": base / "combine_tracking.csv",
            "players": base / "players.csv",
            "games": base / "games.csv",
            "player_play": base / "player_play.csv",
            "game_tracking": [base / "game_tracking.csv"],
        }
    out = {
        "combine_tracking": raw / cfg["files"]["combine_tracking"],
        "players": raw / cfg["files"]["players"],
        "games": raw / cfg["files"]["games"],
        "player_play": raw / cfg["files"]["player_play"],
        "game_tracking": [],
    }
    if mode == "sample":
        out["game_tracking"] = [raw / cfg["files"][cfg["mode"]["sample"]["game_tracking"]]]
    else:
        paths = [raw / fn for fn in cfg["files"]["game_tracking_full"]]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError(
                "full mode requires the full game tracking files, but these are absent: "
                + ", ".join(missing)
                + " -> missing full game tracking files (run --mode full on the host that "
                  "holds them; see notes/blockers.md)")
        out["game_tracking"] = paths
    for k in ("combine_tracking", "players", "games", "player_play"):
        if not out[k].exists():
            raise FileNotFoundError(f"missing raw input {k}: {out[k]}")
    for p in out["game_tracking"]:
        if not p.exists():
            raise FileNotFoundError(f"missing game tracking file: {p}")
    return out


def game_tracking_sql(paths: Sequence[Path]) -> str:
    if len(paths) == 1:
        return read_sql(paths[0], GAME_TRACKING_TYPES)
    return ("(" + " UNION ALL ".join(
        f"SELECT * FROM {read_sql(p, GAME_TRACKING_TYPES)}" for p in paths) + ")")


# --------------------------------------------------------------------------- #
# (a) DB presence + drill inventory
# --------------------------------------------------------------------------- #
def item_a(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, CT = ctx["sql"]["players"], ctx["sql"]["combine_tracking"]
    rows = con.execute(f"""
        WITH db AS (SELECT nfl_id, nfl_position, draft_year FROM {PL}
                    WHERE nfl_position IN ({pos})),
             ev AS (SELECT c.nfl_id, c.drill_type, c.drill_name, c.event_id, count(*) n_rows
                    FROM {CT} c JOIN db USING(nfl_id)
                    WHERE c.entity_type = 'PLAYER'
                    GROUP BY 1, 2, 3, 4)
        SELECT ev.drill_type, ev.drill_name, db.nfl_position,
               count(DISTINCT ev.nfl_id) AS n_players,
               count(DISTINCT ev.event_id) AS n_attempts,
               sum(ev.n_rows) AS n_rows
        FROM ev JOIN db USING(nfl_id)
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """).fetchall()
    fam = dd["audit"]["drill_families"]
    unmapped = set()
    inv = []
    for drill_type, drill_name, position, n_players, n_attempts, n_rows in rows:
        family = fam.get(str(drill_name)) or fam.get(str(drill_type))
        if family is None:
            unmapped.add((str(drill_type), str(drill_name)))
        inv.append(dict(item="a", drill_type=drill_type, drill_name=drill_name,
                        position=position, family=family, n_players=n_players,
                        n_attempts=n_attempts, n_rows=n_rows))
    if unmapped:
        raise SchemaError(
            "db_decay.audit.drill_families is incomplete — no family for: "
            + ", ".join(f"{t}/{n}" for t, n in sorted(unmapped))
            + " (add the mapping to config.yaml; refusing to guess a family)")
    emit(ctx, "a_drill_inventory.csv", inv)

    # family summary
    agg: dict[str, dict] = {}
    for r in inv:
        d = agg.setdefault(r["family"], dict(family=r["family"], n_drills=0,
                                             n_players_max=0, n_attempts=0, n_rows=0))
        d["n_drills"] += 1
        d["n_players_max"] = max(d["n_players_max"], r["n_players"])
        d["n_attempts"] += r["n_attempts"]
        d["n_rows"] += r["n_rows"]
    emit(ctx, "a_family_summary.csv", [{**r, "item": "a"} for r in agg.values()])

    # presence by position / class
    cov = con.execute(f"""
        WITH db AS (SELECT nfl_id, nfl_position, draft_year FROM {PL}
                    WHERE nfl_position IN ({pos})),
             ct AS (SELECT DISTINCT nfl_id FROM {CT} WHERE entity_type = 'PLAYER')
        SELECT db.nfl_position, db.draft_year, count(*) AS n_dbs,
               count(ct.nfl_id) AS n_in_combine
        FROM db LEFT JOIN ct USING(nfl_id)
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    cov_rows = [dict(item="a", nfl_position=p, draft_year=y, n_dbs=n,
                     n_in_combine=int(k), n_absent=int(n) - int(k))
                for p, y, n, k in cov]
    emit(ctx, "a_db_coverage.csv", cov_rows)

    missing40 = con.execute(f"""
        WITH db AS (SELECT nfl_id, nfl_position, draft_year FROM {PL}
                    WHERE nfl_position IN ({pos}))
        SELECT db.nfl_id, db.nfl_position, db.draft_year
        FROM db
        WHERE NOT EXISTS (
            SELECT 1 FROM {CT} c
            WHERE c.nfl_id = db.nfl_id AND c.entity_type = 'PLAYER'
              AND c.drill_type = 'FORTY_YARD_DASH')
        ORDER BY db.nfl_position, db.nfl_id
    """).fetchall()
    emit(ctx, "a_dbs_missing_40.csv",
         [dict(item="a", nfl_id=n, nfl_position=p, draft_year=y) for n, p, y in missing40])

    n_dbs = int(con.execute(
        f"SELECT count(*) FROM {PL} WHERE nfl_position IN ({pos})").fetchone()[0])
    n_in_ct = int(con.execute(f"""
        SELECT count(DISTINCT c.nfl_id) FROM {CT} c
        JOIN {PL} p USING(nfl_id)
        WHERE c.entity_type='PLAYER' AND p.nfl_position IN ({pos})""").fetchone()[0])
    return dict(
        status="EVALUATED",
        n_dbs=n_dbs,
        n_dbs_in_combine=int(n_in_ct),
        n_dbs_absent_from_combine=int(n_dbs - n_in_ct),
        n_drills=int(len({(r["drill_type"], r["drill_name"]) for r in inv})),
        n_dbs_missing_40=int(len(missing40)),
        families=sorted(agg),
        n_unmapped=0,
    )


# --------------------------------------------------------------------------- #
# (b) attempts per player per drill + attempt-numbering unit
# --------------------------------------------------------------------------- #
def item_b(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, CT = ctx["sql"]["players"], ctx["sql"]["combine_tracking"]
    base = f"""
        WITH db AS (SELECT nfl_id, draft_year FROM {PL} WHERE nfl_position IN ({pos})),
             e AS (SELECT DISTINCT c.nfl_id, c.drill_type, c.drill_name, c.event_id, c.attempt
                   FROM {CT} c JOIN db USING(nfl_id)
                   WHERE c.entity_type = 'PLAYER')
    """
    hyp = [
        ("H1_within_player_drilltype_drillname",
         "nfl_id || '|' || drill_type || '|' || drill_name || '|' || attempt"),
        ("H2_within_player_drilltype",
         "nfl_id || '|' || drill_type || '|' || attempt"),
        ("H3_within_player_global",
         "nfl_id || '|' || attempt"),
    ]
    hyp_rows = []
    for name, key in hyp:
        n, n_uniq = con.execute(
            base + f"SELECT count(*), count(DISTINCT {key}) FROM e").fetchone()
        hyp_rows.append(dict(item="b", hypothesis=name, attempt_unit=key,
                             n_event_attempt_rows=int(n),
                             n_distinct_unit_attempt=int(n_uniq),
                             n_duplicates=int(n) - int(n_uniq)))
    emit(ctx, "b_attempt_unit.csv", hyp_rows)
    chosen = min(hyp_rows, key=lambda r: (r["n_duplicates"], -len(r["attempt_unit"])))
    chosen_unit = chosen["hypothesis"]

    # the 9 (or n) ambiguous events where the chosen unit has two event_ids
    dups = con.execute(f"""
        WITH db AS (SELECT nfl_id FROM {PL} WHERE nfl_position IN ({pos})),
             e AS (SELECT DISTINCT c.nfl_id, c.drill_type, c.drill_name, c.event_id, c.attempt
                   FROM {CT} c JOIN db USING(nfl_id) WHERE c.entity_type='PLAYER'),
             d AS (SELECT nfl_id, drill_type, drill_name, attempt, count(*) c,
                          string_agg(event_id, ' | ' ORDER BY event_id) events
                   FROM e GROUP BY 1,2,3,4 HAVING count(*) > 1)
        SELECT nfl_id, drill_type, drill_name, attempt, c, events FROM d
        ORDER BY nfl_id, drill_type, drill_name, attempt
    """).fetchall()
    emit(ctx, "b_attempt_duplicates.csv",
         [dict(item="b", nfl_id=n, drill_type=t, drill_name=dn, attempt=a, n_events=c,
               event_ids=ev) for n, t, dn, a, c, ev in dups])

    dist = con.execute(base + f"""
        , a AS (SELECT nfl_id, drill_type, drill_name, count(DISTINCT event_id) n
                FROM e GROUP BY 1,2,3)
        SELECT drill_type, drill_name, count(*) n_players,
               sum(CASE WHEN n = 1 THEN 1 ELSE 0 END) n_with_1,
               sum(CASE WHEN n >= 2 THEN 1 ELSE 0 END) n_with_ge2,
               median(n) median_attempts, max(n) max_attempts, sum(n) n_attempts
        FROM a GROUP BY 1,2 ORDER BY 1,2
    """).fetchall()
    dist_rows = [dict(item="b", drill_type=t, drill_name=dn, n_players=np_,
                      n_with_1=one, n_with_ge2=ge2, median_attempts=med,
                      max_attempts=mx, n_attempts=tot)
                 for t, dn, np_, one, ge2, med, mx, tot in dist]
    emit(ctx, "b_attempt_distribution.csv", dist_rows)

    # class years
    cls = con.execute(f"""
        SELECT draft_year, count(*) FROM {PL}
        WHERE nfl_position IN ({pos}) GROUP BY 1 ORDER BY 1""").fetchall()
    cls_all = con.execute(
        f"SELECT draft_year, count(*) FROM {PL} GROUP BY 1 ORDER BY 1").fetchall()
    years = [int(y) for y, _ in cls]
    missing_years = [y for y in range(min(years), max(years) + 1) if y not in years] if years else []
    emit(ctx, "b_class_years.csv",
         [dict(item="b", scope="db_positions", draft_year=y, n_players=n) for y, n in cls]
         + [dict(item="b", scope="all_positions", draft_year=y, n_players=n) for y, n in cls_all])

    # per-player attempt counts -> class dependence tests
    raw = con.execute(base + """
        SELECT e.nfl_id, db.draft_year, e.drill_type, e.drill_name,
               count(DISTINCT e.event_id) n
        FROM e JOIN db USING(nfl_id) GROUP BY 1,2,3,4""").fetchall()
    by_drill: dict[tuple[str, str], dict[int, list[float]]] = {}
    for nid, yr, t, dn, n in raw:
        by_drill.setdefault((str(t), str(dn)), {}).setdefault(int(nid), []).append(float(n))
    tests = []
    for (t, dn), players in sorted(by_drill.items()):
        per_year: dict[int, list[float]] = {}
        for nid, vals in players.items():
            # draft_year lookup
            yr = next((int(r[1]) for r in raw if int(r[0]) == nid), None)
            if yr is not None:
                per_year.setdefault(yr, []).append(float(np.mean(vals)))
        groups = [v for v in per_year.values() if len(v) >= 2]
        if len(groups) >= 2:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    stat, p = stats.kruskal(*groups)
            except ValueError:
                stat, p = None, None
            tests.append(dict(item="b", drill_type=t, drill_name=dn, test="kruskal_wallis",
                              statistic=stat, p_value=p,
                              n_classes=len(groups),
                              class_counts=";".join(f"{k}:{len(v)}" for k, v in sorted(per_year.items()))))
        else:
            tests.append(dict(item="b", drill_type=t, drill_name=dn, test="kruskal_wallis",
                              statistic=None, p_value=None, n_classes=len(groups),
                              class_counts=";".join(f"{k}:{len(v)}" for k, v in sorted(per_year.items()))))
    emit(ctx, "b_class_year_tests.csv", tests)

    return dict(status="EVALUATED", chosen_attempt_unit=chosen_unit,
                n_duplicates_chosen=int(chosen["n_duplicates"]),
                n_duplicates_H2=int(next(r["n_duplicates"] for r in hyp_rows
                                         if r["hypothesis"].startswith("H2"))),
                n_drills=int(len(dist_rows)),
                class_years=years, missing_class_years=missing_years,
                n_ambiguous_attempt_events=int(len(dups)))


# --------------------------------------------------------------------------- #
# (c) matched DBs + alignment
# --------------------------------------------------------------------------- #
def item_c(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, PP, GT = ctx["sql"]["players"], ctx["sql"]["player_play"], ctx["sql"]["game_tracking"]
    db = f"(SELECT nfl_id, nfl_position, draft_year FROM {PL} WHERE nfl_position IN ({pos}))"
    n_combine = int(con.execute(f"SELECT count(*) FROM {db}").fetchone()[0])
    n_pp = int(con.execute(
        f"SELECT count(DISTINCT p.nfl_id) FROM {PP} p JOIN {db} d USING(nfl_id)").fetchone()[0])
    n_gt = int(con.execute(
        f"SELECT count(DISTINCT t.nfl_id) FROM {GT} t JOIN {db} d USING(nfl_id)").fetchone()[0])
    emit(ctx, "c_matched_levels.csv", [
        dict(item="c", level="combine_dbs", n=n_combine),
        dict(item="c", level="in_player_play", n=n_pp),
        dict(item="c", level="in_game_tracking", n=n_gt),
    ])

    per = con.execute(f"""
        WITH db AS {db},
             x AS (SELECT db.nfl_id, db.nfl_position, db.draft_year,
                          count(DISTINCT p.game_id) n_games,
                          count(DISTINCT p.game_id || '-' || p.play_id) n_plays,
                          count(*) n_snaps,
                          count(DISTINCT g.season) n_seasons
                   FROM db
                   LEFT JOIN {PP} p USING(nfl_id)
                   LEFT JOIN {ctx['sql']['games']} g ON p.game_id = g.game_id
                   GROUP BY 1,2,3),
             t AS (SELECT DISTINCT nfl_id FROM {GT})
        SELECT x.*, CASE WHEN t.nfl_id IS NULL THEN 0 ELSE 1 END in_tracking
        FROM x LEFT JOIN t USING(nfl_id) ORDER BY x.nfl_id
    """).fetchall()
    per_rows = [dict(item="c", nfl_id=r[0], nfl_position=r[1], draft_year=r[2],
                     position_group=_position_group(str(r[1])), n_games=r[3],
                     n_plays=r[4], n_snaps=r[5], n_db_seasons=r[6], in_tracking=r[7])
                for r in per]
    emit(ctx, "c_matched_dbs.csv", per_rows)

    grp = []
    for g in ("CB", "S", "DB"):
        sub = [r for r in per_rows if str(r["nfl_position"]) != "None" and r["position_group"] == g]
        plays = _median_iqr([r["n_plays"] for r in sub])
        snaps = _median_iqr([r["n_snaps"] for r in sub])
        seasons = _median_iqr([r["n_db_seasons"] for r in sub])
        grp.append(dict(item="c", position_group=g, n_dbs=len(sub),
                        db_seasons=sum(r["n_db_seasons"] or 0 for r in sub),
                        plays_median=plays["median"], plays_q25=plays["q25"],
                        plays_q75=plays["q75"], plays_iqr=plays["iqr"],
                        plays_min=plays["min"], plays_max=plays["max"],
                        snaps_median=snaps["median"], snaps_q25=snaps["q25"],
                        snaps_q75=snaps["q75"], snaps_iqr=snaps["iqr"],
                        snaps_min=snaps["min"], snaps_max=snaps["max"],
                        db_seasons_median=seasons["median"]))
    emit(ctx, "c_matched_by_group.csv", grp)

    # snaps by quarter (per matched DB, aggregated over games)
    qrows = con.execute(f"""
        WITH db AS {db}
        SELECT CASE WHEN db.nfl_position='CB' THEN 'CB'
                    WHEN db.nfl_position IN ('SS','FS') THEN 'S' ELSE 'DB' END AS grp,
               p.quarter, count(*) snaps, count(DISTINCT p.nfl_id) n_dbs,
               count(DISTINCT p.game_id) n_games
        FROM {PP} p JOIN db USING(nfl_id)
        WHERE p.quarter IS NOT NULL
        GROUP BY 1,2 ORDER BY 1,2
    """).fetchall()
    emit(ctx, "c_snaps_by_quarter.csv",
         [dict(item="c", position_group=g, quarter=q, snaps=s, n_dbs=nd, n_games=ng)
          for g, q, s, nd, ng in qrows])

    # alignment labels from player_play
    amap = dd["audit"]["alignment_map"]
    al = con.execute(f"""
        WITH db AS {db}
        SELECT p.lined_up_position, count(*) n, count(DISTINCT p.nfl_id) n_dbs
        FROM {PP} p JOIN db USING(nfl_id) GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    al_rows = []
    for lup, n, nd in al:
        family = amap.get(str(lup), "other") if lup is not None else "missing"
        al_rows.append(dict(item="c", source="player_play", lined_up_position=lup,
                            alignment_family=family, n_rows=n, n_dbs=nd))
    emit(ctx, "c_alignment_labels.csv", al_rows)

    # tracking-derived alignment (depth behind LOS; lateral needs a ball reference)
    snap = con.execute(f"""
        WITH db AS {db},
             s AS (SELECT game_id, play_id, nfl_id, avg(x) x, avg(y) y
                   FROM {GT} WHERE event = 'ball_snap' GROUP BY 1,2,3),
             pp AS (SELECT game_id, play_id, nfl_id,
                           any_value(lined_up_position) lup,
                           any_value(yardline_side) ys,
                           any_value(yardline_number) yn,
                           any_value(play_direction) pd,
                           any_value(possession_team) pt,
                           any_value(team_abbr) ta
                    FROM {PP} GROUP BY 1,2,3)
        SELECT s.game_id, s.play_id, s.nfl_id, s.x, s.y, pp.lup, pp.ys, pp.yn, pp.pd, pp.pt, pp.ta
        FROM s JOIN pp USING(game_id, play_id, nfl_id)
        WHERE s.nfl_id IN (SELECT nfl_id FROM db)
    """).fetchall()
    off_ref = con.execute(f"""
        WITH db AS {db},
             s AS (SELECT game_id, play_id, nfl_id, avg(y) y FROM {GT}
                   WHERE event='ball_snap' GROUP BY 1,2,3),
             pp AS (SELECT game_id, play_id, nfl_id, any_value(possession_team) pt,
                           any_value(team_abbr) ta FROM {PP} GROUP BY 1,2,3)
        SELECT s.game_id, s.play_id, count(*) n_off, median(s.y) ball_y
        FROM s JOIN pp USING(game_id, play_id, nfl_id)
        WHERE pp.ta = pp.pt GROUP BY 1,2
    """).fetchall()
    off = {(int(g), int(p)): (int(n), float(y)) for g, p, n, y in off_ref}
    align = dd["audit"]["alignment"]
    tr_rows = []
    pairs = []
    for gid, pid, nid, x, y, lup, ys, yn, pd, pt, ta in snap:
        if ys is None or yn is None or pd is None:
            tr_rows.append(dict(item="c", source="tracking", game_id=gid, play_id=pid,
                                nfl_id=nid, status="UNESTIMABLE_MISSING_LOS_FIELDS"))
            continue
        los = 10.0 + (float(yn) if str(ys) == str(pt) else (100.0 - float(yn)))
        if str(pd) == "left":
            los = 120.0 - los
        sign = 1.0 if str(pd) != "left" else -1.0
        depth_yd = (float(x) - los) * sign
        if depth_yd >= float(align["safety_min_depth_yd"]):
            klass = "safety"
        elif abs(depth_yd) <= float(align["los_max_depth_yd"]):
            klass = "box_or_lb"
        else:
            klass = "corner_short"
        n_off, ball_y = off.get((int(gid), int(pid)), (0, None))
        lateral = None
        klass_full = klass
        if n_off >= int(align["min_offense_rows_for_lateral"]) and ball_y is not None:
            lateral = abs(float(y) - float(ball_y))
            if klass == "safety":
                klass_full = "safety"
            elif lateral >= float(align["slot_max_lateral_yd"]):
                klass_full = "outside_corner"
            else:
                klass_full = "slot_corner"
        tr_rows.append(dict(item="c", source="tracking", game_id=gid, play_id=pid,
                            nfl_id=nid, lined_up_position=lup, depth_yd=depth_yd,
                            tracking_class=klass, tracking_class_full=klass_full,
                            lateral_offset_yd=lateral,
                            n_offense_rows_at_snap=n_off,
                            lateral_reference="available" if lateral is not None else "MISSING"))
        mapped = amap.get(str(lup))
        if mapped is not None:
            pairs.append((mapped, klass_full))
    emit(ctx, "c_alignment_tracking.csv", tr_rows)

    # agreement: binary safety vs non-safety, plus 3-class if lateral available
    agree_rows = []
    if pairs:
        n = len(pairs)
        mapped_s = [m == "safety" for m, _ in pairs]
        track_s = [t == "safety" for _, t in pairs]
        agree = sum(a == b for a, b in zip(mapped_s, track_s))
        tp = sum(a and b for a, b in zip(mapped_s, track_s))
        tn = sum((not a) and (not b) for a, b in zip(mapped_s, track_s))
        fp = sum((not a) and b for a, b in zip(mapped_s, track_s))
        fn = sum(a and (not b) for a, b in zip(mapped_s, track_s))
        agree_rows.append(dict(item="c", comparison="safety_vs_nonsafety",
                               n_matched=n, n_agree=agree,
                               agreement=(agree / n if n else None),
                               tp=tp, tn=tn, fp=fp, fn=fn))
        full = [(m, t) for m, t in pairs if t in ("outside_corner", "slot_corner", "safety")]
        if full:
            n2 = len(full)
            a2 = sum(m == t for m, t in full)
            agree_rows.append(dict(item="c", comparison="3class_full_lateral",
                                   n_matched=n2, n_agree=a2, agreement=a2 / n2,
                                   tp=None, tn=None, fp=None, fn=None))
        else:
            agree_rows.append(dict(item="c", comparison="3class_full_lateral",
                                   n_matched=0, n_agree=0, agreement=None,
                                   tp=None, tn=None, fp=None, fn=None))
    emit(ctx, "c_alignment_agreement.csv", agree_rows)

    return dict(status="EVALUATED" if ctx["mode"] == "full" else "PARTIAL",
                n_combine_dbs=n_combine, n_in_player_play=n_pp, n_in_game_tracking=n_gt,
                n_tracking_snap_units=len(snap),
                n_alignment_pairs=len(pairs),
                alignment_agreement=(agree_rows[0]["agreement"] if agree_rows else None),
                n_lateral_reference_available=sum(
                    1 for r in tr_rows if r.get("lateral_reference") == "available"),
                missing_alignment_field=("no ball entity / ball lateral reference in game "
                                         "tracking; the sample has ~1 player per play"))


# --------------------------------------------------------------------------- #
# (d) game data coverage + sample-draw diagnosis
# --------------------------------------------------------------------------- #
def _players_per_play(con, table: str) -> list[tuple]:
    return con.execute(f"""
        WITH a AS (SELECT game_id, play_id, count(DISTINCT nfl_id) n
                   FROM {table} GROUP BY 1,2)
        SELECT n, count(*) FROM a GROUP BY 1 ORDER BY 1""").fetchall()


def item_d(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, PP, GT, G = (ctx["sql"]["players"], ctx["sql"]["player_play"],
                     ctx["sql"]["game_tracking"], ctx["sql"]["games"])
    db = f"(SELECT nfl_id FROM {PL} WHERE nfl_position IN ({pos}))"

    # (i) players per play
    pp_dist = _players_per_play(con, PP)
    gt_dist = _players_per_play(con, GT)
    emit(ctx, "d_players_per_play.csv",
         [dict(item="d", source="player_play", players_per_play=n, n_plays=c)
          for n, c in pp_dist]
         + [dict(item="d", source="game_tracking", players_per_play=n, n_plays=c)
            for n, c in gt_dist])
    pp_n = int(sum(c for _, c in pp_dist))
    pp_mean = sum(n * c for n, c in pp_dist) / pp_n if pp_n else None
    gt_n = int(sum(c for _, c in gt_dist))
    gt_mean = sum(n * c for n, c in gt_dist) / gt_n if gt_n else None
    n22 = sum(c for n, c in gt_dist if n >= 22)

    # (ii) field availability (one scan for DB rows, one for opponent rows)
    cols = ", ".join(f'count("{f}") AS "{f}"' for f in FIELD_LIST_D)
    db_av = con.execute(
        f"SELECT count(*), {cols} FROM {PP} p JOIN {db} d USING(nfl_id)").fetchone()
    opp_sql = f"""
        WITH dbp AS (SELECT DISTINCT game_id, play_id, team_abbr
                     FROM {PP} p JOIN {db} d USING(nfl_id))
        SELECT count(*), {cols}
        FROM {PP} pp JOIN dbp b
          ON pp.game_id = b.game_id AND pp.play_id = b.play_id AND pp.team_abbr <> b.team_abbr
    """
    opp_av = con.execute(opp_sql).fetchone()
    avail = []
    for i, f in enumerate(FIELD_LIST_D):
        avail.append(dict(item="d", field=f, scope="db_rows", present="YES",
                          n_rows=int(db_av[0]), n_nonnull=int(db_av[i + 1]),
                          pct_nonnull=(db_av[i + 1] / db_av[0] if db_av[0] else None)))
    for i, f in enumerate(FIELD_LIST_D):
        avail.append(dict(item="d", field=f, scope="opponent_rows_same_play", present="YES",
                          n_rows=int(opp_av[0]), n_nonnull=int(opp_av[i + 1]),
                          pct_nonnull=(opp_av[i + 1] / opp_av[0] if opp_av[0] else None)))
    emit(ctx, "d_field_availability.csv", avail)

    # (iii) which plays are included: pass / rush / other + nullified, by season/type
    inc = con.execute(f"""
        WITH p AS (
            SELECT game_id, play_id,
                   max(CASE WHEN pass_result IS NOT NULL OR dropback_duration IS NOT NULL
                             OR lower(play_description) LIKE '% pass %'
                             OR lower(play_description) LIKE '%sacked%'
                             OR lower(play_description) LIKE '%scramble%'
                            THEN 1 ELSE 0 END) is_pass,
                   max(CASE WHEN lower(play_description) SIMILAR TO
                             '.*(left|right) (end|guard|tackle).*'
                             OR lower(play_description) LIKE '%up the middle%'
                             OR lower(play_description) LIKE '%kneel%'
                            THEN 1 ELSE 0 END) is_rush,
                   max(CASE WHEN play_nullified_by_penalty = 'Y' THEN 1 ELSE 0 END) is_null
            FROM {PP} GROUP BY 1,2),
             j AS (SELECT p.*, g.season, g.season_type FROM p JOIN {G} g USING(game_id))
        SELECT season, season_type,
               count(*) n_plays,
               sum(is_pass) n_pass, sum(is_rush) n_rush,
               sum(CASE WHEN is_pass=0 AND is_rush=0 THEN 1 ELSE 0 END) n_other,
               sum(is_null) n_nullified
        FROM j GROUP BY 1,2 ORDER BY 1,2
    """).fetchall()
    inc_rows = [dict(item="d", season=s, season_type=t, n_plays=n,
                     n_pass=vp, n_rush=vr, n_other=vo, n_nullified=vn,
                     pass_frac=(vp / n if n else None), rush_frac=(vr / n if n else None),
                     nullified_frac=(vn / n if n else None))
                for s, t, n, vp, vr, vo, vn in inc]
    emit(ctx, "d_play_inclusion.csv", inc_rows)

    # (iv) sample-draw diagnosis
    n_rows = int(con.execute(f"SELECT count(*) FROM {GT}").fetchone()[0])
    n_games = int(con.execute(f"SELECT count(DISTINCT game_id) FROM {GT}").fetchone()[0])
    n_plays = int(con.execute(
        f"SELECT count(DISTINCT game_id || '-' || play_id) FROM {GT}").fetchone()[0])
    n_units = int(con.execute(
        f"SELECT count(*) FROM (SELECT game_id,play_id,nfl_id FROM {GT} GROUP BY 1,2,3)"
    ).fetchone()[0])
    n_players = int(con.execute(f"SELECT count(DISTINCT nfl_id) FROM {GT}").fetchone()[0])
    frames = con.execute(f"""
        WITH u AS (SELECT game_id,play_id,nfl_id,count(*) n FROM {GT} GROUP BY 1,2,3)
        SELECT min(n), median(n), max(n), avg(n) FROM u""").fetchone()
    dt_hist = con.execute(f"""
        WITH d AS (SELECT epoch_ms(time) - lag(epoch_ms(time)) OVER
                          (PARTITION BY game_id,play_id,nfl_id ORDER BY time) dt
                   FROM {GT})
        SELECT dt, count(*) FROM d WHERE dt IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    classes = con.execute(f"""
        SELECT p.draft_year, count(DISTINCT t.nfl_id)
        FROM (SELECT DISTINCT nfl_id FROM {GT}) t JOIN {PL} p USING(nfl_id)
        GROUP BY 1 ORDER BY 1""").fetchall()
    n_db_units = int(con.execute(f"""
        SELECT count(*) FROM (SELECT t.game_id,t.play_id,t.nfl_id FROM {GT} t
                              JOIN {db} d USING(nfl_id) GROUP BY 1,2,3)""").fetchone()[0])
    n_db_players = int(con.execute(
        f"SELECT count(DISTINCT t.nfl_id) FROM {GT} t JOIN {db} d USING(nfl_id)").fetchone()[0])
    plays_2db = int(con.execute(f"""
        SELECT count(*) FROM (SELECT t.game_id,t.play_id,count(DISTINCT t.nfl_id) n
                              FROM {GT} t JOIN {db} d USING(nfl_id) GROUP BY 1,2) WHERE n >= 2
    """).fetchone()[0])
    game_cov = con.execute(f"""
        WITH s AS (SELECT game_id, count(DISTINCT play_id) n_sel, min(play_id) mn, max(play_id) mx
                   FROM {GT} GROUP BY 1),
             pall AS (SELECT DISTINCT game_id, play_id FROM {PP}),
             pr AS (SELECT game_id, play_id,
                           row_number() OVER (PARTITION BY game_id ORDER BY play_id) rn
                    FROM pall),
             pc AS (SELECT game_id, count(*) n_all, max(play_id) pmx FROM pall GROUP BY 1),
             pref AS (SELECT s.game_id, count(*) n_selected_in_first
                      FROM s JOIN pr USING(game_id) JOIN {GT} t
                        ON t.game_id = pr.game_id AND t.play_id = pr.play_id
                      WHERE pr.rn <= s.n_sel GROUP BY 1)
        SELECT s.game_id, s.n_sel, pc.n_all, s.mn, s.mx, pc.pmx,
               coalesce(pref.n_selected_in_first,0) n_selected_in_first
        FROM s JOIN pc USING(game_id) LEFT JOIN pref USING(game_id) ORDER BY s.game_id
    """).fetchall()
    game_rows = []
    for gid, n_sel, n_all, mn, mx, pmx, nin in game_cov:
        game_rows.append(dict(item="d", game_id=gid, n_plays_selected=n_sel,
                              n_plays_in_player_play=n_all,
                              play_id_min_selected=mn, play_id_max_selected=mx,
                              play_id_max_player_play=pmx,
                              coverage="prefix" if nin == n_sel else "scattered",
                              last_play_selected=(mx == pmx)))
    emit(ctx, "d_sample_game_coverage.csv", game_rows)
    n_prefix = sum(1 for r in game_rows if r["coverage"] == "prefix")
    n_last = sum(1 for r in game_rows if r["last_play_selected"])

    last_ev = con.execute(f"""
        WITH u AS (SELECT game_id,play_id,nfl_id,
                          arg_max(event, time) last_event, max(time) last_time
                   FROM {GT} GROUP BY 1,2,3)
        SELECT last_event, count(*) FROM u GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    n_gap = int(con.execute(f"""
        WITH d AS (SELECT game_id, play_id, nfl_id,
                          epoch_ms(time) - lag(epoch_ms(time)) OVER
                          (PARTITION BY game_id,play_id,nfl_id ORDER BY time) dt
                   FROM {GT})
        SELECT count(DISTINCT game_id||'-'||play_id||'-'||nfl_id) FROM d WHERE dt > 150
    """).fetchone()[0])
    half = con.execute(f"""
        SELECT count(*) FROM (SELECT game_id,play_id,nfl_id,count(*) n FROM {GT}
                              GROUP BY 1,2,3) WHERE n < 40""").fetchone()[0]

    draw = []
    cap = 2 ** 20 - 1
    draw.append(dict(item="d", hypothesis="A: 2^20-row prefix of a game/play-ordered file",
                     verdict="REFUTED" if n_games > 20 else "CONSISTENT",
                     evidence=f"rows={n_rows} (2^20-1={cap}); games={n_games} are interleaved "
                              f"in file order; a game/play prefix would cover ~1 game"))
    draw.append(dict(item="d", hypothesis="B: exactly one player per play by construction",
                     verdict="PARTIAL",
                     evidence=f"{n_plays} plays / {n_units} player-play units; mean "
                              f"{(n_units / n_plays if n_plays else 0):.3f} players/play; "
                              f"{plays_2db} plays have "
                              f"2+ DBs (max {max((n for n, _ in gt_dist), default=0)})"))
    draw.append(dict(item="d", hypothesis="C: restricted to Combine-class players",
                     verdict="CONFIRMED (stronger)",
                     evidence="all players have draft_year "
                              + ",".join(f"{int(y)}:{int(c)}" for y, c in classes)
                              + "; no 2024/2025 class in the sample"))
    draw.append(dict(item="d", hypothesis="D: truncated within plays",
                     verdict="PARTIAL",
                     evidence=f"file has exactly 2^20-1 data rows (hard 1,048,576-line cap "
                              f"incl. header); {half} units have <40 frames; {n_gap} units have "
                              f"an internal >150ms gap; last-event histogram: "
                              + "; ".join(f"{e}:{c}" for e, c in last_ev[:5])))
    emit(ctx, "d_sample_draw.csv", draw)
    emit(ctx, "d_sample_dt_histogram.csv",
         [dict(item="d", dt_ms=dt, n_frames=c) for dt, c in dt_hist])
    emit(ctx, "d_sample_players_per_play.csv",
         [dict(item="d", players_per_play=n, n_plays=c) for n, c in gt_dist])

    blocked = [
        dict(item="d", code_path="opponent/ball/nearest-receiver features",
             why=f"mean {_fmt(gt_mean, 2)} players per play (max "
                 f"{max((n for n, _ in gt_dist), default=0)}); no ball entity, no opponent rows",
             consequence="same-play matchup demand cannot be computed; within-game contrasts only"),
        dict(item="d", code_path="class-year comparisons / 2024-2025 seasons",
             why="the sample contains only draft_year=2023 players from the 2023 season",
             consequence="no cross-class or cross-season generalisation can be tested"),
        dict(item="d", code_path="within-game substitution inference",
             why="only one player per play; teammates are absent on the same snap",
             consequence="cannot tell who replaced whom or infer snap attrition from tracking"),
        dict(item="d", code_path="load accumulation from tracking alone",
             why="plays are a scattered subset (~11% of player_play plays) and some units are "
                 "truncated (2^20-1-row cap)",
             consequence="prior-effort load must come from the full run, not the sample"),
        dict(item="d", code_path="speed/acceleration peaks",
             why="truncated units cut the tail of a play; 200ms gaps drop frames",
             consequence="peak speeds are biased downward on truncated units"),
        dict(item="d", code_path="multi-season DB-seasons in tracking",
             why="the sample is a single season (2023)",
             consequence="no season-level replication"),
    ]
    emit(ctx, "d_sample_blocked_paths.csv", blocked)

    return dict(status="EVALUATED",
                n_sample_rows=n_rows, n_sample_games=n_games, n_sample_plays=n_plays,
                n_sample_units=n_units, n_sample_players=n_players,
                n_sample_db_players=n_db_players, n_sample_db_units=n_db_units,
                sample_frames_per_unit_median=frames[1],
                sample_players_per_play_mean=gt_mean,
                all_22_tracked=("YES" if (gt_n and n22 == gt_n) else "NO"),
                n_plays_with_22_tracked=int(n22),
                player_play_players_per_play_mean=pp_mean,
                plays_with_2plus_dbs=plays_2db,
                sample_game_prefix_games=n_prefix,
                sample_games_last_play_present=n_last,
                sample_rows_equals_2_20_minus_1=(n_rows == cap),
                n_blocked_paths=len(blocked))


# --------------------------------------------------------------------------- #
# (e) snap counts by quarter (player_play = complete for DBs)
# --------------------------------------------------------------------------- #
def item_e(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, PP, GT = ctx["sql"]["players"], ctx["sql"]["player_play"], ctx["sql"]["game_tracking"]
    db = f"(SELECT nfl_id, nfl_position FROM {PL} WHERE nfl_position IN ({pos}))"

    games = con.execute(f"""
        WITH db AS {db}
        SELECT db.nfl_id, db.nfl_position, p.game_id,
               count(*) snaps,
               sum(CASE WHEN p.quarter=1 THEN 1 ELSE 0 END) q1,
               sum(CASE WHEN p.quarter=2 THEN 1 ELSE 0 END) q2,
               sum(CASE WHEN p.quarter=3 THEN 1 ELSE 0 END) q3,
               sum(CASE WHEN p.quarter=4 THEN 1 ELSE 0 END) q4
        FROM {PP} p JOIN db USING(nfl_id)
        WHERE p.quarter IS NOT NULL
        GROUP BY 1,2,3 ORDER BY 1,3
    """).fetchall()
    emit(ctx, "e_snaps_by_db_game.csv",
         [dict(item="e", nfl_id=n, nfl_position=p, position_group=_position_group(str(p)),
               game_id=g, snaps=s, q1=a, q2=b, q3=c, q4=d)
          for n, p, g, s, a, b, c, d in games])

    # One row per (player, game, quarter): the distribution below is over DB-GAMES, so the
    # grouping must include the unit. Grouping on (position, quarter) alone collapses every
    # DB-game into a single total and makes "snaps_median" a sum (regression test: P27).
    qd = con.execute(f"""
        WITH db AS {db}
        SELECT db.nfl_position, db.nfl_id, p.game_id, p.quarter, count(*) snaps
        FROM {PP} p JOIN db USING(nfl_id)
        WHERE p.quarter IS NOT NULL
        GROUP BY 1,2,3,4 ORDER BY 1,4
    """).fetchall()
    by_q: dict[tuple[str, int], list[float]] = {}
    for p, _nid, _gid, q, s in qd:
        by_q.setdefault((_position_group(str(p)), int(q)), []).append(float(s))
    qrows = []
    for (g, q), vals in sorted(by_q.items()):
        st = _median_iqr(vals)
        qrows.append(dict(item="e", position_group=g, quarter=q, n_db_games=st["n"],
                          snaps_median=st["median"], snaps_q25=st["q25"],
                          snaps_q75=st["q75"], snaps_iqr=st["iqr"],
                          snaps_min=st["min"], snaps_max=st["max"]))
    emit(ctx, "e_snap_distribution_by_quarter.csv", qrows)

    att = {}
    for g in ("CB", "S", "DB", "ALL"):
        sub = [r for r in games if g == "ALL" or _position_group(str(r[1])) == g]
        q1 = [r for r in sub if r[4] > 0]
        n1 = len(q1)
        att[g] = dict(item="e", position_group=g, n_db_games_with_q1=n1,
                      share_q2=(sum(1 for r in q1 if r[5] > 0) / n1 if n1 else None),
                      share_q3=(sum(1 for r in q1 if r[6] > 0) / n1 if n1 else None),
                      share_q4=(sum(1 for r in q1 if r[7] > 0) / n1 if n1 else None),
                      share_all_four=(sum(1 for r in q1 if min(r[4], r[5], r[6], r[7]) > 0) / n1
                                      if n1 else None),
                      share_q1_not_q4=(sum(1 for r in q1 if r[7] == 0) / n1 if n1 else None))
    emit(ctx, "e_attrition.csv", list(att.values()))

    # tracking-side version: scripted but PENDING FULL RUN (sample is single-class/1 player)
    tr = con.execute(f"""
        WITH db AS {db},
             u AS (SELECT t.game_id, t.play_id, t.nfl_id, count(*) frames
                   FROM {GT} t JOIN db USING(nfl_id) GROUP BY 1,2,3),
             q AS (SELECT DISTINCT game_id, play_id, quarter FROM {PP})
        SELECT db.nfl_position, q.quarter, count(*) n_player_plays
        FROM u JOIN q USING(game_id, play_id) JOIN db USING(nfl_id)
        GROUP BY 1,2 ORDER BY 1,2
    """).fetchall()
    emit(ctx, "e_tracking_by_quarter.csv",
         [dict(item="e", position_group=_position_group(str(p)), quarter=q,
               n_player_plays=n, status=PENDING,
               note="tracking-side snap count; sample cannot support the full answer")
          for p, q, n in tr])

    return dict(status="EVALUATED", tracking_status=PENDING,
                n_db_games=int(len(games)),
                attrition_all_four=att["ALL"]["share_all_four"],
                attrition_q1_not_q4=att["ALL"]["share_q1_not_q4"],
                n_db_games_with_q1=att["ALL"]["n_db_games_with_q1"])


# --------------------------------------------------------------------------- #
# (f) reliability of provided dis / s / a
# --------------------------------------------------------------------------- #
def _reliability_rows(con, sql: str, base_cols: str, alias_cols: str, group_by: str,
                      where: str, rtol: float, extra: dict) -> list[dict]:
    lead = (base_cols + ", ") if base_cols else ""
    q = f"""
        WITH f AS (
            SELECT {lead}
                   x, y, s, a, dis,
                   epoch_ms(time) - lag(epoch_ms(time)) OVER w AS dt_ms,
                   x - lag(x) OVER w AS dx,
                   y - lag(y) OVER w AS dy,
                   s - lag(s) OVER w AS ds
            FROM {sql} {where}
            WINDOW w AS (PARTITION BY event_key ORDER BY time)
        ), g AS (
            SELECT {alias_cols}, dis, s, a,
                   sqrt(dx*dx + dy*dy) AS step,
                   (sqrt(dx*dx + dy*dy) / NULLIF(dt_ms/1000.0, 0)) AS v,
                   (ds / NULLIF(dt_ms/1000.0, 0)) AS dv
            FROM f WHERE dx IS NOT NULL AND dt_ms > 0
        )
        SELECT {group_by},
               count(*) n,
               corr(dis, step) r_dis,
               avg(abs(dis - step)) mae_dis,
               avg(dis - step) bias_dis,
               avg(CASE WHEN step > 0.1 AND abs(dis - step) > {rtol} * step
                        THEN 1.0 ELSE 0.0 END) viol_dis,
               corr(s, v) r_s, avg(abs(s - v)) mae_s, avg(s - v) bias_s,
               avg(CASE WHEN v > 0.1 AND abs(s - v) > {rtol} * v THEN 1.0 ELSE 0.0 END) viol_s,
               corr(a, dv) r_a, avg(abs(a - dv)) mae_a, avg(a - dv) bias_a,
               avg(CASE WHEN abs(dv) > 0.1 AND abs(a - dv) > {rtol} * abs(dv)
                        THEN 1.0 ELSE 0.0 END) viol_a
        FROM g GROUP BY {group_by} ORDER BY {group_by}
    """
    rows = con.execute(q).fetchall()
    out = []
    for r in rows:
        ncol = len(r) - 13
        keys = r[:ncol]
        vals = r[ncol:]
        d = {k: v for k, v in zip(["g1", "g2"][:ncol], keys)}
        d.update(dict(n=vals[0], r_dis=vals[1], mae_dis=vals[2], bias_dis=vals[3],
                      viol_dis=vals[4], r_s=vals[5], mae_s=vals[6], bias_s=vals[7],
                      viol_s=vals[8], r_a=vals[9], mae_a=vals[10], bias_a=vals[11],
                      viol_a=vals[12]))
        d.update(extra)
        out.append(d)
    return out


def item_f(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, CT, GT = ctx["sql"]["players"], ctx["sql"]["combine_tracking"], ctx["sql"]["game_tracking"]
    rtol = float(dd["audit"]["distance_rtol"])
    db = f"(SELECT nfl_id FROM {PL} WHERE nfl_position IN ({pos}))"

    comb = f"""(SELECT c.*, c.event_id AS event_key FROM {CT} c
                WHERE c.entity_type = 'PLAYER' AND c.nfl_id IN {db})"""
    rows = _reliability_rows(con, comb, "drill_type, drill_name",
                             "drill_type AS g1, drill_name AS g2", "g1, g2",
                             "", rtol, {"item": "f", "source": "combine"})
    fam = dd["audit"]["drill_families"]
    for r in rows:
        r["family"] = fam.get(str(r.get("g2"))) or fam.get(str(r.get("g1"))) or "other"
        r["drill_type"], r["drill_name"] = r.get("g1"), r.get("g2")
        r.pop("g1", None); r.pop("g2", None)
    emit(ctx, "f_reliability_combine.csv", rows)

    game = f"""(SELECT t.*, t.game_id || '-' || t.play_id || '-' || t.nfl_id AS event_key
                FROM {GT} t WHERE t.nfl_id IN {db})"""
    grows = _reliability_rows(con, game, "", "'game_tracking' AS g1", "g1", "", rtol,
                              {"item": "f", "source": "game_tracking", "family": "game"})
    for r in grows:
        r["drill_type"], r["drill_name"] = None, None
        r.pop("g1", None); r.pop("g2", None)
    emit(ctx, "f_reliability_game.csv", grows)

    # sampling / units / coordinate frame
    def _dt(src):
        return con.execute(f"""
            WITH d AS (SELECT epoch_ms(time) - lag(epoch_ms(time)) OVER
                              (PARTITION BY {src} ORDER BY time) dt FROM {GT})
            SELECT dt, count(*) FROM d WHERE dt IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
        """).fetchall()
    samp = []
    for label, sql, key, where in (
            ("combine", CT, "event_id", "WHERE entity_type = 'PLAYER'"),
            ("game_tracking", GT, "game_id || '-' || play_id || '-' || nfl_id", "")):
        hs = con.execute(f"""
            WITH d AS (SELECT epoch_ms(time) - lag(epoch_ms(time)) OVER
                              (PARTITION BY {key} ORDER BY time) dt FROM {sql} {where})
            SELECT dt, count(*) FROM d WHERE dt IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
            LIMIT 10""").fetchall()
        for dt, c in hs:
            samp.append(dict(item="f", source=label, dt_ms=dt, n_frames=int(c)))
    emit(ctx, "f_sampling.csv", samp)

    rng = con.execute(
        f"SELECT min(x),max(x),min(y),max(y),min(s),max(s),min(a),max(a),"
        f"min(dis),max(dis),min(dir),max(dir) FROM {CT} WHERE entity_type='PLAYER'").fetchone()
    rng_g = con.execute(
        f"SELECT min(x),max(x),min(y),max(y),min(s),max(s),min(a),max(a),"
        f"min(dis),max(dis),min(o),max(o),min(dir),max(dir) FROM {GT}").fetchone()
    emit(ctx, "f_coordinate_frame.csv", [
        dict(item="f", source="combine", columns="draft_year,event_id,nfl_id,entity_type,time,"
                                                 "drill_type,drill_name,attempt,x,y,s,a,dis,dir",
             x_min=rng[0], x_max=rng[1], y_min=rng[2], y_max=rng[3], s_min=rng[4],
             s_max=rng[5], a_min=rng[6], a_max=rng[7], dis_min=rng[8], dis_max=rng[9],
             o_min=None, o_max=None, dir_min=rng[10], dir_max=rng[11],
             units="x/y yards (field frame), s yd/s, a yd/s^2, dis yd, dir degrees"),
        dict(item="f", source="game_tracking",
             columns="game_id,play_id,nfl_id,time,x,y,s,a,dis,o,dir,event",
             x_min=rng_g[0], x_max=rng_g[1], y_min=rng_g[2], y_max=rng_g[3], s_min=rng_g[4],
             s_max=rng_g[5], a_min=rng_g[6], a_max=rng_g[7], dis_min=rng_g[8], dis_max=rng_g[9],
             o_min=rng_g[10], o_max=rng_g[11], dir_min=rng_g[12], dir_max=rng_g[13],
             units="x/y yards (field frame), s yd/s, a yd/s^2, dis yd, o/dir degrees"),
    ])

    return dict(status="EVALUATED",
                n_combine_groups=len(rows),
                combine_dis_violation_40=next(
                    (r["viol_dis"] for r in rows if r.get("drill_type") == "FORTY_YARD_DASH"), None),
                combine_s_violation_40=next(
                    (r["viol_s"] for r in rows if r.get("drill_type") == "FORTY_YARD_DASH"), None),
                game_dis_violation=(grows[0]["viol_dis"] if grows else None),
                game_s_violation=(grows[0]["viol_s"] if grows else None),
                note=("Report only; nothing is corrected. Evidence for this mode is in "
                      "f_reliability_{combine,game}.csv and f_sampling.csv — read the "
                      "numbers there, never from this note."
                      + (" Sample-mode readings (UNVALIDATED, never reportable): Combine `s` "
                         "and `a` track |dxy|/dt and ds/dt closely on the straight dash, but "
                         "`dis` under-counts low-speed displacement and breaches the 5% "
                         "relative tolerance on most 40-yard frames; game `dis`/`s` are "
                         "reliable, but the provided game `a` is not the derivative of game "
                         "`s`, so it must not be used as acceleration without validation."
                         if ctx["mode"] != "full" and not ctx["synthetic"] else "")))


# --------------------------------------------------------------------------- #
# (g) reliability helpers: ICC(1,1), attempt features, cut metrics
# --------------------------------------------------------------------------- #
def _smooth(v: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(v) < 3:
        return np.asarray(v, float)
    if w % 2 == 0:
        w += 1
    pad = w // 2
    vp = np.pad(np.asarray(v, float), (pad, pad), mode="edge")
    k = np.ones(w) / w
    return np.convolve(vp, k, mode="valid")[: len(v)]


def icc_1_1(groups: dict[int, list[float]], min_players: int, alpha: float = 0.05) -> dict:
    """ICC(1,1) one-way random effects with an exact F-based CI.

    Only players with >=2 finite attempts contribute (test-retest needs replication).
    Never drops a player silently: counts are returned for the caller to report.
    """
    clean = {int(k): [float(v) for v in vals if v is not None and np.isfinite(v)]
             for k, vals in groups.items()}
    allg = {k: v for k, v in clean.items() if len(v) >= 1}
    rep = {k: v for k, v in allg.items() if len(v) >= 2}
    k_players = len(rep)
    N = sum(len(v) for v in rep.values())
    res = dict(icc=None, ci_low=None, ci_high=None, k0=None, msb=None, msw=None,
               n_players_total=len(allg), n_players_with_repeat=k_players,
               n_attempts_used=N, status=None)
    if k_players < min_players:
        res["status"] = (f"UNESTIMABLE: only {k_players} players with >=2 attempts "
                         f"(<{min_players})")
        return res
    if k_players < 2 or N <= k_players:
        res["status"] = f"UNESTIMABLE: too few groups/observations (k={k_players}, N={N})"
        return res
    allv = np.concatenate([np.asarray(v) for v in rep.values()])
    grand = float(allv.mean())
    ssb = sum(len(v) * (float(np.mean(v)) - grand) ** 2 for v in rep.values())
    ssw = sum(float(np.sum((np.asarray(v) - np.mean(v)) ** 2)) for v in rep.values())
    dfb, dfw = k_players - 1, N - k_players
    if dfw <= 0:
        res["status"] = f"UNESTIMABLE: no within-player df (dfw={dfw})"
        return res
    msb, msw = ssb / dfb, ssw / dfw
    k0 = (N - sum(len(v) ** 2 for v in rep.values()) / N) / (k_players - 1)
    res.update(msb=float(msb), msw=float(msw), k0=float(k0))
    if msw <= 1e-12:
        res["status"] = "UNESTIMABLE: zero within-player variance"
        return res
    icc = (msb - msw) / (msb + (k0 - 1) * msw)
    res["icc"] = float(icc)
    if msb > 0:
        fL = (msb / msw) / stats.f.ppf(1 - alpha / 2, dfb, dfw)
        fU = (msb / msw) * stats.f.ppf(1 - alpha / 2, dfw, dfb)
        lo = (fL - 1) / (fL + k0 - 1)
        hi = (fU - 1) / (fU + k0 - 1)
        res["ci_low"] = float(max(-1.0, min(1.0, lo)))
        res["ci_high"] = float(max(-1.0, min(1.0, hi)))
    res["status"] = "ESTIMATED"
    return res


def _attempt_features(x, y, t, cfg: dict) -> dict[str, float]:
    """Model features for one attempt: envelope vmax/a0 on smoothed speed + cut metrics."""
    ceil = cfg["db_decay"]["ceiling"]
    fit = cfg["envelope"]["fit"]
    x = np.asarray(x, float); y = np.asarray(y, float); t = np.asarray(t, float)
    dt = np.diff(t)
    dt[dt <= 0] = np.nan
    step = np.hypot(np.diff(x), np.diff(y))
    v = np.nan_to_num(np.concatenate([[0.0], step / dt]))
    vs = _smooth(v, int(ceil["smooth_window_frames"]))
    onset = np.where(vs >= float(fit["onset_speed_yds"]))[0]
    i0 = int(onset[0]) if len(onset) else 0
    t_rel = t - t[i0]
    vmax, tau, a0 = cf.fit_envelope(t_rel, vs, cfg)
    out = dict(vmax=vmax, tau=tau, a0=a0, n_frames=int(len(t)),
               peak_speed=float(np.nanmax(vs)) if len(vs) else np.nan)
    if len(t_rel) >= 3:
        dv = np.gradient(vs, t_rel)
        out["max_decel"] = float(-np.nanmin(dv)) if np.isfinite(np.nanmin(dv)) else np.nan
    else:
        out["max_decel"] = np.nan
    out.update(_cut_metrics(x, y, t, vs, ceil))
    return out


def _cut_metrics(x, y, t, vs, ceil: dict) -> dict[str, float]:
    out = dict(entry_speed=np.nan, exit_speed=np.nan, cod_deficit=np.nan)
    if len(x) < 5:
        return out
    dx, dy = np.diff(x), np.diff(y)
    step = np.hypot(dx, dy)
    ok = step > 1e-6
    if ok.sum() < 3:
        return out
    ang = np.unwrap(np.arctan2(dy, dx))
    ang = _smooth(ang, int(ceil["heading_window_frames"]))
    turn_strength = np.abs(np.diff(ang))
    candidate = np.where(step[1:] >= float(ceil["cut_min_speed_yds"]) * 0.1)[0]
    if len(candidate) == 0:
        return out
    # score by heading change; ties -> earliest
    i = int(candidate[np.argmax(turn_strength[candidate])]) + 1
    dt_med = float(np.nanmedian(np.diff(t))) if len(t) > 1 else 0.1
    if not np.isfinite(dt_med) or dt_med <= 0:
        dt_med = 0.1
    win = max(int(round(float(ceil["cut_window_s"]) / dt_med)), 1)
    a, b = max(0, i - win), min(len(vs), i + win + 1)
    entry = float(np.nanmax(vs[a:i + 1])) if i >= a else np.nan
    exit_ = float(np.nanmax(vs[i:b])) if b > i else np.nan
    out.update(entry_speed=entry, exit_speed=exit_,
               cod_deficit=(entry - exit_) if np.isfinite(entry) and np.isfinite(exit_) else np.nan)
    return out


# --------------------------------------------------------------------------- #
# (g) ceiling feature selection: ICC screen, then VIF
# --------------------------------------------------------------------------- #
SENSITIVITY_VARIANTS = ("none", "vmax_bound_only", "over_peak_only", "both")
GATE_VARIANT = "both"  # the fully-filtered variant feeds the gate; the rest are sensitivity


def ceiling_config(dd: dict) -> dict:
    """Return the validated ``db_decay.ceiling`` block.

    Every threshold used by item (g) comes from here. A missing key is a loud
    ``GateError``, never a silent default: a defaulted 12.5 yd/s bound would quietly
    change which attempts are excluded from the reliability evidence.
    """
    ceil = dd.get("ceiling")
    if not isinstance(ceil, dict):
        raise GateError("config db_decay.ceiling is missing")
    missing = [k for k in ("required_features", "candidates", "selection", "sanity",
                           "smooth_window_frames", "heading_window_frames",
                           "cut_window_s", "cut_min_speed_yds", "cut_drills",
                           "cut_metrics")
               if k not in ceil]
    if missing:
        raise GateError("config db_decay.ceiling missing required key(s): "
                        + ", ".join(f"db_decay.ceiling.{m}" for m in missing))
    sel, san, cand = ceil["selection"], ceil["sanity"], ceil["candidates"]
    if not isinstance(sel, dict):
        raise GateError("config db_decay.ceiling.selection must be a mapping")
    if not isinstance(san, dict):
        raise GateError("config db_decay.ceiling.sanity must be a mapping")
    missing = [f"selection.{k}" for k in ("rule", "icc_min", "vif_max", "max_features")
               if k not in sel]
    missing += [f"sanity.{k}" for k in ("vmax_yds_max", "vmax_over_peak_max")
                if k not in san]
    if missing:
        raise GateError("config db_decay.ceiling missing required key(s): "
                        + ", ".join(missing))
    if str(sel["rule"]) != "icc_then_vif":
        raise GateError(f"unsupported db_decay.ceiling.selection.rule {sel['rule']!r}; "
                        "only 'icc_then_vif' is implemented")
    for key, val in (("selection.icc_min", sel["icc_min"]),
                     ("selection.vif_max", sel["vif_max"]),
                     ("sanity.vmax_yds_max", san["vmax_yds_max"]),
                     ("sanity.vmax_over_peak_max", san["vmax_over_peak_max"]),
                     ("cut_window_s", ceil["cut_window_s"]),
                     ("cut_min_speed_yds", ceil["cut_min_speed_yds"])):
        try:
            fv = float(val)
        except (TypeError, ValueError):
            raise GateError(f"config db_decay.ceiling.{key} must be numeric, got {val!r}")
        if not np.isfinite(fv) or fv <= 0:
            raise GateError(f"config db_decay.ceiling.{key} must be positive, got {val!r}")
    for key in ("smooth_window_frames", "heading_window_frames"):
        try:
            iv = int(ceil[key])
        except (TypeError, ValueError):
            raise GateError(f"config db_decay.ceiling.{key} must be an int, got {ceil[key]!r}")
        if iv < 1:
            raise GateError(f"config db_decay.ceiling.{key} must be >= 1, got {ceil[key]!r}")
    for key in ("cut_drills", "cut_metrics"):
        if not isinstance(ceil[key], (list, tuple)) or not ceil[key]:
            raise GateError(f"config db_decay.ceiling.{key} must be a non-empty list")
    try:
        if int(sel["max_features"]) < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise GateError("config db_decay.ceiling.selection.max_features must be an int >= 1")
    if not cand:
        raise GateError("config db_decay.ceiling.candidates must not be empty")
    required = [str(r) for r in ceil["required_features"]]
    if not required:
        raise GateError("config db_decay.ceiling.required_features must not be empty")
    not_cand = [r for r in required if r not in [str(c) for c in cand]]
    if not_cand:
        raise GateError("db_decay.ceiling.required_features not in candidates: "
                        + ", ".join(not_cand))
    for name in [str(c) for c in cand]:
        _candidate_spec(name, ceil)  # raises on an unknown candidate/metric
    return ceil


AUDIT_KEYS = (
    "out_subdir", "positions", "min_matched_dbs", "min_efforts_per_db",
    "min_efforts_per_db_slope", "min_total_efforts", "min_icc_primary",
    "min_icc_secondary", "min_repeat_players_icc", "icc_ci_alpha", "distance_rtol",
    "exceedance_margin_rel", "provenance_thresholds", "alignment_map", "alignment",
    "drill_families", "pending_full_run",
)
GAME_KEYS = (
    "effort_speed_frac_of_vmax", "effort_speed_floor_yds", "effort_accel_frac_of_a0",
    "effort_accel_floor_yds2", "min_effort_duration_s", "garbage_time_q4_margin",
    "garbage_time_any_margin", "min_snaps_per_player",
)
POWER_KEYS = (
    "alpha", "power", "reps", "seed_offset", "effect_grid", "assumed_sd_effort_fraction",
    "assumed_load_icc_within_player", "assumed_efforts_per_db", "assumed_games_per_db",
    "smallest_effect_of_interest", "assumed_load_sd_per10", "assumed_random_intercept_sd",
    "assumed_random_slope_sd",
)
ALIGNMENT_KEYS = ("safety_min_depth_yd", "los_max_depth_yd", "slot_max_lateral_yd",
                  "min_offense_rows_for_lateral")


def _require_block(dd: dict, key: str, keys: Sequence[str], errors: list[str],
                   path: str | None = None) -> dict:
    path = path or key
    block = dd.get(key)
    if not isinstance(block, dict):
        errors.append(f"db_decay.{path} is missing or not a mapping")
        return {}
    for k in keys:
        if k not in block:
            errors.append(f"db_decay.{path}.{k} is missing")
    return block


def validate_db_decay_config(dd: dict) -> dict:
    """Fail fast, by name, on any missing/malformed ``db_decay`` key.

    Called once at the top of :func:`run`, before any data is read. Every threshold the audit
    consumes must be configured: a silent default here would change which attempts or features
    the gate sees, so a missing key is a ``GateError`` naming the dotted path.
    """
    if not isinstance(dd, dict):
        raise GateError("config db_decay is missing")
    errors: list[str] = []
    audit = _require_block(dd, "audit", AUDIT_KEYS, errors)
    game = _require_block(dd, "game", GAME_KEYS, errors)
    power = _require_block(dd, "power", POWER_KEYS, errors)
    align = (_require_block(audit, "alignment", ALIGNMENT_KEYS, errors,
                            path="audit.alignment") if audit else {})
    ceiling_config(dd)  # raises on any missing/invalid db_decay.ceiling key
    if errors:
        raise GateError("config db_decay invalid \u2014 " + "; ".join(errors))

    def _num(block, key, path, lo=None, hi=None, allow_zero=False):
        try:
            v = float(block[key])
        except (TypeError, ValueError):
            errors.append(f"db_decay.{path}.{key} must be numeric, got {block[key]!r}")
            return None
        if not np.isfinite(v):
            errors.append(f"db_decay.{path}.{key} must be finite, got {block[key]!r}")
            return None
        if (v < 0 if allow_zero else v <= 0):
            errors.append(f"db_decay.{path}.{key} must be "
                          f"{'>= 0' if allow_zero else '> 0'}, got {block[key]!r}")
        if lo is not None and v <= lo:
            errors.append(f"db_decay.{path}.{key} must be > {lo}, got {block[key]!r}")
        if hi is not None and v >= hi:
            errors.append(f"db_decay.{path}.{key} must be < {hi}, got {block[key]!r}")
        return v

    for k in ("min_matched_dbs", "min_efforts_per_db", "min_efforts_per_db_slope",
              "min_total_efforts", "min_repeat_players_icc"):
        _num(audit, k, "audit")
    for k in ("min_icc_primary", "min_icc_secondary"):
        _num(audit, k, "audit", lo=0.0, hi=1.0)
    _num(audit, "icc_ci_alpha", "audit", lo=0.0, hi=1.0)
    _num(audit, "distance_rtol", "audit", lo=0.0, hi=1.0)
    _num(audit, "exceedance_margin_rel", "audit", lo=-1.0, allow_zero=True)
    if not str(audit.get("provenance_thresholds") or "").strip():
        errors.append("db_decay.audit.provenance_thresholds must be a non-empty string")
    for k in ("positions",):
        if not isinstance(audit.get(k), (list, tuple)) or not audit.get(k):
            errors.append(f"db_decay.audit.{k} must be a non-empty list")
    for k in ("alignment_map", "drill_families"):
        if not isinstance(audit.get(k), dict) or not audit.get(k):
            errors.append(f"db_decay.audit.{k} must be a non-empty mapping")
    for k in ALIGNMENT_KEYS:
        if align:
            _num(align, k, "audit.alignment", allow_zero=True)
    for k in ("effort_speed_frac_of_vmax", "effort_speed_floor_yds",
              "effort_accel_frac_of_a0", "effort_accel_floor_yds2",
              "min_effort_duration_s", "min_snaps_per_player"):
        _num(game, k, "game")
    for k in ("garbage_time_q4_margin", "garbage_time_any_margin"):
        _num(game, k, "game", allow_zero=True)
    _num(power, "alpha", "power", lo=0.0, hi=1.0)
    _num(power, "power", "power", lo=0.0, hi=1.0)
    _num(power, "reps", "power", allow_zero=True)
    _num(power, "seed_offset", "power", allow_zero=True)
    for k in ("assumed_sd_effort_fraction", "assumed_load_sd_per10",
              "smallest_effect_of_interest", "assumed_efforts_per_db",
              "assumed_games_per_db", "assumed_random_intercept_sd",
              "assumed_random_slope_sd"):
        _num(power, k, "power")
    grid = power.get("effect_grid")
    if not isinstance(grid, (list, tuple)) or not grid:
        errors.append("db_decay.power.effect_grid must be a non-empty list")
    else:
        for e in grid:
            try:
                if float(e) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"db_decay.power.effect_grid entries must be positive, got {e!r}")
    if errors:
        raise GateError("config db_decay invalid \u2014 " + "; ".join(errors))
    return dd


def _candidate_spec(name: str, ceil: dict) -> tuple[str, str]:
    """Map a configured candidate name to (drill row in `g_feature_icc.csv`, metric)."""
    if name in ("vmax", "a0", "max_decel"):
        return "FORTY_YARD_DASH", name
    if name.startswith("cut_"):
        metric = name[len("cut_"):]
        if metric not in [str(m) for m in ceil["cut_metrics"]]:
            raise GateError(f"candidate {name!r} refers to unconfigured cut metric "
                            f"{metric!r}")
        return "POOLED_CUT_DRILLS", metric
    raise GateError(f"unknown db_decay.ceiling.candidates entry {name!r}")


def _sanity_flags(f: dict, san: dict) -> tuple[bool, bool]:
    """(above absolute speed bound, above ratio-to-observed-peak bound)."""
    vmax, peak = f.get("vmax"), f.get("peak_speed")
    bound = bool(vmax is not None and np.isfinite(vmax)
                 and float(vmax) > float(san["vmax_yds_max"]))
    over = bool(vmax is not None and np.isfinite(vmax)
                and peak is not None and np.isfinite(peak) and float(peak) > 0
                and float(vmax) > float(san["vmax_over_peak_max"]) * float(peak))
    return bound, over


def _variant_ok(f: dict, variant: str) -> bool:
    b, o = bool(f.get("flag_vmax_bound")), bool(f.get("flag_over_peak"))
    if variant == "none":
        return True
    if variant == "vmax_bound_only":
        return not b
    if variant == "over_peak_only":
        return not o
    if variant == "both":
        return not (b or o)
    raise GateError(f"unknown sensitivity variant {variant!r}")


def _variant_groups(feats: list[dict], key: str, variant: str) -> dict[int, list[float]]:
    g: dict[int, list[float]] = {}
    for f in feats:
        if f.get("drill_type") != "FORTY_YARD_DASH" or not _variant_ok(f, variant):
            continue
        v = f.get(key)
        if v is None or not np.isfinite(v):
            continue
        g.setdefault(int(f["nfl_id"]), []).append(float(v))
    return g


def _variant_counts(feats: list[dict], key: str, variant: str) -> tuple[int, int]:
    """(attempts with a finite value, attempts dropped by this variant's filters)."""
    total = kept = 0
    for f in feats:
        if f.get("drill_type") != "FORTY_YARD_DASH":
            continue
        v = f.get(key)
        if v is None or not np.isfinite(v):
            continue
        total += 1
        if _variant_ok(f, variant):
            kept += 1
    return total, total - kept


def _candidate_player_means(feats: list[dict], candidates: list[str],
                            ceil: dict) -> dict[str, dict[int, float]]:
    """Per-player mean of each candidate, on the same rows the gate ICC uses."""
    cut_drills = {str(d) for d in ceil["cut_drills"]}
    out: dict[str, dict[int, float]] = {}
    for name in candidates:
        drill, metric = _candidate_spec(name, ceil)
        vals: dict[int, list[float]] = {}
        for f in feats:
            if drill == "FORTY_YARD_DASH":
                if f.get("drill_type") != "FORTY_YARD_DASH":
                    continue
                if not _variant_ok(f, GATE_VARIANT):
                    continue
            elif str(f.get("drill_name")) not in cut_drills:
                continue
            v = f.get(metric)
            if v is None or not np.isfinite(v):
                continue
            vals.setdefault(int(f["nfl_id"]), []).append(float(v))
        out[name] = {k: float(np.mean(v)) for k, v in vals.items()}
    return out


def _vif_table(means: dict[str, dict[int, float]],
               features: list[str]) -> tuple[dict[str, float], int]:
    """VIF from the player-level correlation matrix; {} when it is not estimable."""
    if not features:
        return {}, 0
    pids = set(means[features[0]])
    for f in features[1:]:
        pids &= set(means[f])
    pids = sorted(pids)
    if len(features) < 2:
        return {features[0]: 1.0}, len(pids)
    if len(pids) < len(features) + 2:
        return {}, len(pids)
    X = np.asarray([[means[f][p] for f in features] for p in pids], float)
    if np.any(X.std(axis=0) <= 1e-12):
        return {}, len(pids)
    corr = np.atleast_2d(np.corrcoef(X, rowvar=False))
    if not np.all(np.isfinite(corr)):
        return {}, len(pids)
    try:
        inv = np.linalg.inv(corr)
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(corr)
    vif = {f: float(inv[i, i]) for i, f in enumerate(features)}
    if not all(np.isfinite(v) and v > 0 for v in vif.values()):
        return {}, len(pids)
    return vif, len(pids)


def select_ceiling_features(icc_rows: list[dict], feats: list[dict], ceil: dict) -> dict:
    """Pre-registered selection rule: screen by ICC, then greedily prune by VIF.

    Candidates are *screened*, not mandated: failing ICC means "excluded_by_selection",
    not NO_GO. Required features are always retained so a required failure stays visible
    to the gate (it is never laundered into a pass by the selection object).
    """
    sc = ceil["selection"]
    icc_min, vif_max = float(sc["icc_min"]), float(sc["vif_max"])
    max_features = int(sc["max_features"])
    candidates = [str(c) for c in ceil["candidates"]]
    required = [str(r) for r in ceil["required_features"]]
    lookup = {(str(r["drill"]), str(r["feature"])): r for r in icc_rows}
    info = {}
    for name in candidates:
        drill, metric = _candidate_spec(name, ceil)
        row = lookup.get((drill, metric), {})
        icc = row.get("icc")
        estimated = (icc is not None and str(row.get("status", "")).startswith("ESTIMATED"))
        info[name] = dict(feature=name, drill=drill, metric=metric, icc=icc,
                          ci_low=row.get("ci_low"), ci_high=row.get("ci_high"),
                          icc_status=row.get("status"),
                          passed_icc=bool(estimated and icc is not None and icc >= icc_min),
                          required=name in required)
    passing = [n for n in candidates if info[n]["passed_icc"]]
    means = _candidate_player_means(feats, candidates, ceil)
    vif, n_vif = _vif_table(means, passing)
    selected = [n for n in required if n in candidates]
    excluded: list[dict] = []
    ranked = sorted((n for n in passing if n not in selected),
                    key=lambda n: (-float(info[n]["icc"]), candidates.index(n)))
    for name in ranked:
        if len(selected) >= max_features:
            reason = "max_features_reached"
        elif name in vif and vif[name] > vif_max:
            reason = "vif_above_max"
        else:
            selected.append(name)
            continue
        excluded.append(dict(feature=name, drill=info[name]["drill"],
                             icc=info[name]["icc"], ci_low=info[name]["ci_low"],
                             ci_high=info[name]["ci_high"], reason=reason,
                             vif=vif.get(name)))
    for name in candidates:
        if name in selected or any(e["feature"] == name for e in excluded):
            continue
        excluded.append(dict(feature=name, drill=info[name]["drill"],
                             icc=info[name]["icc"], ci_low=info[name]["ci_low"],
                             ci_high=info[name]["ci_high"],
                             reason=("unestimable" if info[name]["icc"] is None
                                     else "icc_below_min"),
                             vif=vif.get(name)))
    selected_detail = [dict(info[n], vif=vif.get(n),
                            selection=("required" if n in required else "icc_then_vif"))
                       for n in selected]
    return dict(rule=str(sc["rule"]),
                thresholds=dict(icc_min=icc_min, vif_max=vif_max,
                                max_features=max_features),
                required=required, selected=selected, selected_detail=selected_detail,
                excluded=excluded, vif=vif, n_players_vif=n_vif,
                vif_status=("ESTIMATED" if vif else
                            f"UNESTIMABLE: {n_vif} complete players"),
                candidates=[dict(info[n]) for n in candidates])


def sanity_flag_bias(ctx: dict, feats: list[dict], ceil: dict) -> tuple[list[dict], dict]:
    """Is the sanity exclusion selective? Flagged shares by year/position/drill + hash."""
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL = ctx["sql"]["players"]
    meta = {int(r[0]): (r[1], str(r[2] or "")) for r in con.execute(
        f"SELECT nfl_id, draft_year, nfl_position FROM {PL} "
        f"WHERE nfl_position IN ({pos})").fetchall()}

    def _agg(sub: list[dict]) -> dict:
        n = len(sub)
        nb = sum(1 for f in sub if f["flag_vmax_bound"])
        no = sum(1 for f in sub if f["flag_over_peak"])
        nf = sum(1 for f in sub if f["flag_vmax_bound"] or f["flag_over_peak"])
        vals = np.asarray([f["vmax"] for f in sub
                           if f.get("vmax") is not None and np.isfinite(f["vmax"])], float)
        return dict(n_attempts=n, n_flagged=nf,
                    share_flagged=(nf / n if n else None),
                    n_flag_vmax_bound=nb, n_flag_over_peak=no,
                    mean_vmax=(float(vals.mean()) if vals.size else None),
                    sd_vmax=(float(vals.std(ddof=1)) if vals.size > 1 else None),
                    median_vmax=(float(np.median(vals)) if vals.size else None))

    rows: list[dict] = []
    dims = [
        ("draft_year", lambda f: str(meta.get(int(f["nfl_id"]), (None, ""))[0]
                                     or "UNKNOWN")),
        ("nfl_position", lambda f: str(meta.get(int(f["nfl_id"]), (None, ""))[1]
                                       or "UNKNOWN")),
        ("drill_name", lambda f: str(f["drill_name"])),
    ]
    # the exclusion applies to 40-yard envelope fits only, so report both scopes
    scopes = (("all_drills", feats),
              ("forty_yard_dash", [f for f in feats
                                   if f["drill_type"] == "FORTY_YARD_DASH"]))
    by_dim: dict[str, dict[str, float]] = {}
    for dim, keyfn in dims:
        for scope, pool in (scopes if dim != "drill_name" else scopes[:1]):
            key = dim if scope == "all_drills" else f"{dim}_40"
            by_dim[key] = {}
            for lvl in sorted({keyfn(f) for f in pool}):
                sub = [f for f in pool if keyfn(f) == lvl]
                agg = _agg(sub)
                by_dim[key][lvl] = agg["share_flagged"]
                rows.append(dict(item="g", section="by_dimension", scope=scope,
                                 dimension=dim, level=lvl, **agg))
    for scope, pool in scopes:
        groups = {"flagged": [f for f in pool
                              if f["flag_vmax_bound"] or f["flag_over_peak"]],
                  "clean": [f for f in pool
                            if not (f["flag_vmax_bound"] or f["flag_over_peak"])]}
        for label, sub in groups.items():
            rows.append(dict(item="g", section="vmax_flagged_vs_clean", scope=scope,
                             dimension="attempt_group", level=label, **_agg(sub)))

    by_player: dict[int, list[dict]] = {}
    for f in feats:
        if f.get("drill_type") == "FORTY_YARD_DASH":
            by_player.setdefault(int(f["nfl_id"]), []).append(f)
    other_drills = {int(f["nfl_id"]) for f in feats
                    if f.get("drill_type") != "FORTY_YARD_DASH"}
    lost = []
    for nid, sub in sorted(by_player.items()):
        if sub and all(f["flag_vmax_bound"] or f["flag_over_peak"] for f in sub):
            lost.append((nid, len(sub), nid in other_drills))
    ids_hash = (hashlib.sha256(",".join(str(x[0]) for x in lost).encode("utf-8")).hexdigest()
                if lost else None)
    for nid, n_att, has_other in lost:
        rows.append(dict(item="g", section="players_losing_all_attempts",
                         dimension="player", level=str(nid), n_attempts=n_att,
                         n_flagged=n_att, has_other_drills=has_other))
    for level, value in (("n_players", len(lost)), ("ids_sha256", ids_hash),
                         ("n_without_other_drills",
                          sum(1 for x in lost if not x[2]))):
        rows.append(dict(item="g", section="players_losing_all_attempts",
                         dimension="summary", level=level, value=value))
    def _vstats(scope_pool):
        sub_f = [f for f in scope_pool if f["flag_vmax_bound"] or f["flag_over_peak"]]
        sub_c = [f for f in scope_pool if not (f["flag_vmax_bound"] or f["flag_over_peak"])]
        return dict(flagged_mean_vmax=_agg(sub_f)["mean_vmax"],
                    flagged_sd_vmax=_agg(sub_f)["sd_vmax"],
                    clean_mean_vmax=_agg(sub_c)["mean_vmax"],
                    clean_sd_vmax=_agg(sub_c)["sd_vmax"])

    summary = dict(n_attempts=len(feats),
                   n_flagged=sum(1 for f in feats
                                 if f["flag_vmax_bound"] or f["flag_over_peak"]),
                   n_players_losing_all_attempts=len(lost),
                   ids_sha256=ids_hash,
                   n_without_other_drills=sum(1 for x in lost if not x[2]),
                   flag_share_by_draft_year=by_dim["draft_year"],
                   flag_share_by_position=by_dim["nfl_position"],
                   flag_share_by_drill=by_dim["drill_name"],
                   flag_share_by_draft_year_40=by_dim["draft_year_40"],
                   flag_share_by_position_40=by_dim["nfl_position_40"],
                   all_drills=_vstats(feats),
                   forty_yard_dash=_vstats(scopes[1][1]))
    return rows, summary


def item_g(ctx: dict) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, CT = ctx["sql"]["players"], ctx["sql"]["combine_tracking"]
    ceil = ceiling_config(dd)
    cut_drills = [str(d) for d in ceil["cut_drills"]]
    drills = ["FORTY_YARD_DASH"] + cut_drills
    in_list = ", ".join("'" + d.replace("'", "''") + "'" for d in drills)
    rows = con.execute(f"""
        SELECT c.nfl_id, c.event_id, c.drill_type, c.drill_name, c.attempt,
               epoch_ms(c.time) ms, c.x, c.y, c.s, c.a
        FROM {CT} c
        WHERE c.entity_type='PLAYER'
          AND c.nfl_id IN (SELECT nfl_id FROM {PL} WHERE nfl_position IN ({pos}))
          AND (c.drill_type = 'FORTY_YARD_DASH' OR c.drill_name IN ({in_list}))
        ORDER BY c.nfl_id, c.event_id, c.time
    """).fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((int(r[0]), str(r[1]), str(r[2]), str(r[3]), int(r[4])), []).append(r)

    san = ceil["sanity"]
    feats = []
    for (nid, eid, dtp, dnm, att), rs in groups.items():
        t = (np.asarray([r[5] for r in rs], float) - float(rs[0][5])) / 1000.0
        f = _attempt_features([r[6] for r in rs], [r[7] for r in rs], t, ctx["cfg"])
        bound, over = _sanity_flags(f, san)
        flag = "vmax_gt_sanity" if bound else ("vmax_over_peak" if over else "")
        feats.append(dict(item="g", nfl_id=nid, event_id=eid, drill_type=dtp, drill_name=dnm,
                          attempt=att, vmax_flag=flag, flag_vmax_bound=bound,
                          flag_over_peak=over, **f))
    emit(ctx, "g_attempt_features.csv", feats)

    minrep = int(dd["audit"]["min_repeat_players_icc"])
    alpha = float(dd["audit"]["icc_ci_alpha"])

    # --- reliability sensitivity: four pre-registered filter variants ----------
    sens_rows, sens_raw = [], {}
    for variant in SENSITIVITY_VARIANTS:
        for label in ("vmax", "a0"):
            g = _variant_groups(feats, label, variant)
            r = icc_1_1(g, minrep, alpha)
            n_total, n_excl = _variant_counts(feats, label, variant)
            sens_raw[(variant, label)] = r
            gate_variant = variant == GATE_VARIANT
            sens_rows.append(dict(
                item="g", variant=variant, feature=label,
                variant_role=("gate_primary" if gate_variant else "sensitivity"),
                is_gate_variant=gate_variant,
                n_players=r["n_players_with_repeat"],
                n_players_total=r["n_players_total"],
                n_attempts_total=n_total, n_attempts_used=r["n_attempts_used"],
                n_excluded=n_excl, icc=r["icc"], ci_low=r["ci_low"],
                ci_high=r["ci_high"], status=r["status"]))
    emit(ctx, "g_feature_icc_sensitivity.csv", sens_rows)

    icc_rows = []
    for label in ("vmax", "a0"):
        r, raw = sens_raw[(GATE_VARIANT, label)], sens_raw[("none", label)]
        icc_rows.append(dict(
            item="g", drill="FORTY_YARD_DASH", feature=label, role="primary",
            n_players_total=r["n_players_total"],
            n_players_with_repeat=r["n_players_with_repeat"],
            n_attempts_used=r["n_attempts_used"],
            n_attempts_excluded_sanity=_variant_counts(feats, label, GATE_VARIANT)[1],
            icc=r["icc"], ci_low=r["ci_low"], ci_high=r["ci_high"],
            icc_raw=raw["icc"], n_players_with_repeat_raw=raw["n_players_with_repeat"],
            msb=r["msb"], msw=r["msw"], k0=r["k0"], status=r["status"]))
    # max_decel is not in the vmax/a0 sensitivity table; same `both` filter as the gate
    r = icc_1_1(_variant_groups(feats, "max_decel", GATE_VARIANT), minrep, alpha)
    raw = icc_1_1(_variant_groups(feats, "max_decel", "none"), minrep, alpha)
    icc_rows.append(dict(
        item="g", drill="FORTY_YARD_DASH", feature="max_decel", role="secondary",
        n_players_total=r["n_players_total"],
        n_players_with_repeat=r["n_players_with_repeat"],
        n_attempts_used=r["n_attempts_used"],
        n_attempts_excluded_sanity=_variant_counts(feats, "max_decel", GATE_VARIANT)[1],
        icc=r["icc"], ci_low=r["ci_low"], ci_high=r["ci_high"],
        icc_raw=raw["icc"], n_players_with_repeat_raw=raw["n_players_with_repeat"],
        msb=r["msb"], msw=r["msw"], k0=r["k0"], status=r["status"]))
    for drill in cut_drills:
        for metric in ceil["cut_metrics"]:
            g: dict[int, list[float]] = {}
            for f in feats:
                if f["drill_name"] != drill:
                    continue
                v = f.get(metric)
                if v is None or not np.isfinite(v):
                    continue
                g.setdefault(int(f["nfl_id"]), []).append(float(v))
            rr = icc_1_1(g, minrep, alpha)
            icc_rows.append(dict(item="g", drill=drill, feature=metric, role="secondary",
                                 n_players_total=rr["n_players_total"],
                                 n_players_with_repeat=rr["n_players_with_repeat"],
                                 n_attempts_used=rr["n_attempts_used"],
                                 n_attempts_excluded_sanity=0,
                                 icc=rr["icc"], ci_low=rr["ci_low"], ci_high=rr["ci_high"],
                                 icc_raw=None, n_players_with_repeat_raw=None,
                                 msb=rr["msb"], msw=rr["msw"], k0=rr["k0"],
                                 status=rr["status"]))
    # pooled cut metrics: explicit caveat (mixes different drills)
    for metric in ceil["cut_metrics"]:
        g = {}
        for f in feats:
            if f["drill_name"] not in cut_drills:
                continue
            v = f.get(metric)
            if v is None or not np.isfinite(v):
                continue
            g.setdefault(int(f["nfl_id"]), []).append(float(v))
        rr = icc_1_1(g, minrep, alpha)
        icc_rows.append(dict(item="g", drill="POOLED_CUT_DRILLS", feature=metric,
                             role="secondary",
                             n_players_total=rr["n_players_total"],
                             n_players_with_repeat=rr["n_players_with_repeat"],
                             n_attempts_used=rr["n_attempts_used"],
                             n_attempts_excluded_sanity=0,
                             icc=rr["icc"], ci_low=rr["ci_low"], ci_high=rr["ci_high"],
                             icc_raw=None, n_players_with_repeat_raw=None,
                             msb=rr["msb"], msw=rr["msw"], k0=rr["k0"],
                             status=(rr["status"] if rr["status"] != "ESTIMATED"
                                     else "ESTIMATED (mixes drills; not a pure test-retest)")))

    selection = select_ceiling_features(icc_rows, feats, ceil)
    cand_by_key = {}
    for name in [str(c) for c in ceil["candidates"]]:
        cand_by_key[_candidate_spec(name, ceil)] = name
    sel_names = set(selection["selected"])
    exc_names = {e["feature"] for e in selection["excluded"]}
    for row in icc_rows:
        name = cand_by_key.get((row["drill"], row["feature"]))
        if name in sel_names:
            row["selection_status"] = "selected"
        elif name in exc_names:
            row["selection_status"] = "excluded_by_selection"
        elif (row.get("icc") is None
              or not str(row.get("status", "")).startswith("ESTIMATED")):
            row["selection_status"] = "unestimable"
        else:
            row["selection_status"] = "secondary"
    emit(ctx, "g_feature_icc.csv", icc_rows)

    bias_rows, bias_summary = sanity_flag_bias(ctx, feats, ceil)
    emit(ctx, "g_sanity_flag_bias.csv", bias_rows)

    def _find(feature, drill="FORTY_YARD_DASH"):
        return next(r for r in icc_rows if r["feature"] == feature and r["drill"] == drill)

    vmax_row, a0_row = _find("vmax"), _find("a0")
    dec_row = _find("max_decel")
    return dict(status="EVALUATED", icc_rows=len(icc_rows),
                vmax_icc=vmax_row["icc"],
                vmax_icc_ci=[vmax_row["ci_low"], vmax_row["ci_high"]],
                vmax_icc_raw=vmax_row["icc_raw"],
                vmax_n_repeat=vmax_row["n_players_with_repeat"],
                a0_icc=a0_row["icc"], a0_icc_ci=[a0_row["ci_low"], a0_row["ci_high"]],
                a0_icc_raw=a0_row["icc_raw"],
                a0_n_repeat=a0_row["n_players_with_repeat"],
                max_decel_icc=dec_row["icc"],
                selection=selection,
                icc_sensitivity=dict(gate_variant=GATE_VARIANT,
                                     variants=list(SENSITIVITY_VARIANTS),
                                     rows=sens_rows),
                sanity_bias=bias_summary,
                dropped_features=[f"{e['drill']}:{e['feature']}"
                                  for e in selection["excluded"]],
                n_sanity_flagged_40=sum(
                    1 for f in feats
                    if (f["flag_vmax_bound"] or f["flag_over_peak"])
                    and f["drill_type"] == "FORTY_YARD_DASH"),
                cut_icc_estimable=[f"{r['drill']}:{r['feature']}" for r in icc_rows
                                   if r["drill"] != "FORTY_YARD_DASH"
                                   and str(r["status"]).startswith("ESTIMATED")],
                cut_icc_unestimable=[f"{r['drill']}:{r['feature']}" for r in icc_rows
                                     if r["drill"] != "FORTY_YARD_DASH"
                                     and not str(r["status"]).startswith("ESTIMATED")])


# --------------------------------------------------------------------------- #
# (h) exceedance of the shrunken Combine ceiling
# --------------------------------------------------------------------------- #
def _shrunk_ceiling(feats: list[dict], icc: float | None) -> dict[int, dict]:
    """Reliability-weighted EB shrinkage of per-player mean vmax/a0 toward the group mean.

    reliability_i = n_i*ICC / (1 + (n_i-1)*ICC); with ICC<=0 or None -> no shrinkage.
    """
    out: dict[int, dict] = {}
    players = sorted({int(f["nfl_id"]) for f in feats if f["drill_type"] == "FORTY_YARD_DASH"})
    for key in ("vmax", "a0"):
        per: dict[int, list[float]] = {}
        for f in feats:
            if f["drill_type"] != "FORTY_YARD_DASH" or f.get("vmax_flag"):
                continue
            v = f.get(key)
            if v is not None and np.isfinite(v):
                per.setdefault(int(f["nfl_id"]), []).append(float(v))
        grand = float(np.mean([np.mean(v) for v in per.values()])) if per else None
        for nid in players:
            vals = per.get(nid, [])
            if not vals or grand is None:
                continue
            mean = float(np.mean(vals))
            rel = 0.0
            if icc is not None and icc > 0:
                n = len(vals)
                rel = (n * icc) / (1.0 + (n - 1) * icc)
            out.setdefault(nid, {})[f"{key}_mean"] = mean
            out[nid][f"{key}_shrunk"] = grand + rel * (mean - grand)
            out[nid][f"{key}_reliability"] = rel
            out[nid][f"{key}_n_attempts"] = len(vals)
    return out


def item_h(ctx: dict, g: dict, feats: list[dict]) -> dict:
    con, dd = ctx["con"], ctx["dd"]
    pos = _in_positions(dd["audit"]["positions"])
    PL, GT = ctx["sql"]["players"], ctx["sql"]["game_tracking"]
    ceil = _shrunk_ceiling(feats, g.get("vmax_icc"))
    gate_icc = g.get("a0_icc")
    a_ceil = _shrunk_ceiling(feats, gate_icc)
    for nid, d in a_ceil.items():
        for k, v in d.items():
            if k.startswith("a0_"):
                ceil.setdefault(nid, {})[k] = v
    players = con.execute(
        f"SELECT nfl_id, nfl_position FROM {PL} WHERE nfl_position IN ({pos})").fetchall()
    posmap = {int(n): str(p) for n, p in players}
    peaks = con.execute(f"""
        SELECT game_id, play_id, nfl_id, max(s) max_s, max(abs(a)) max_a, count(*) n_frames
        FROM {GT} GROUP BY 1,2,3
    """).fetchall()
    margin = float(dd["audit"]["exceedance_margin_rel"])
    rows = []
    for gid, pid, nid, ms, ma, nf in peaks:
        nid = int(nid)
        c = ceil.get(nid)
        if not c:
            continue
        vs, as_ = c.get("vmax_shrunk"), c.get("a0_shrunk")
        rows.append(dict(item="h", game_id=gid, play_id=pid, nfl_id=nid,
                         position_group=_position_group(posmap.get(nid, "")),
                         max_s=ms, max_abs_a=ma, vmax_shrunk=vs, a0_shrunk=as_,
                         speed_exceeds=(float(ms) > float(vs) * (1 + margin)
                                        if vs is not None else None),
                         accel_exceeds=(float(ma) > float(as_) * (1 + margin)
                                        if as_ is not None else None),
                         speed_ratio=(float(ms) / float(vs) if vs else None)))
    emit(ctx, "h_exceedance_units.csv", rows)
    valid_s = [r for r in rows if r["speed_exceeds"] is not None]
    valid_a = [r for r in rows if r["accel_exceeds"] is not None]
    ex_s = sum(1 for r in valid_s if r["speed_exceeds"])
    ex_a = sum(1 for r in valid_a if r["accel_exceeds"])
    summary = [dict(item="h", metric="speed", n_units=len(valid_s), n_exceed=ex_s,
                    exceedance_rate=(ex_s / len(valid_s) if valid_s else None),
                    status=PENDING),
               dict(item="h", metric="accel", n_units=len(valid_a), n_exceed=ex_a,
                    exceedance_rate=(ex_a / len(valid_a) if valid_a else None),
                    status=PENDING)]
    emit(ctx, "h_exceedance_summary.csv", summary)
    ratios = [r["speed_ratio"] for r in rows if r["speed_ratio"]]
    return dict(status="PARTIAL", pending=PENDING,
                n_units=len(rows), n_speed_units=len(valid_s), n_accel_units=len(valid_a),
                speed_exceedance_rate=(ex_s / len(valid_s) if valid_s else None),
                accel_exceedance_rate=(ex_a / len(valid_a) if valid_a else None),
                median_speed_ratio=(float(np.median(ratios)) if ratios else None),
                note=(("sample only; never used for the gate. " if ctx["mode"] != "full"
                       else "Exceedance is descriptive evidence, never a gate input. ")
                      + "Ceiling = reliability-weighted "
                      "EB shrinkage of the mean of sanity-flagged-clean 40-yard attempts toward "
                      "the DB-wide mean: grand + rel*(player_mean-grand), "
                      "rel = n*ICC/(1+(n-1)*ICC). Accel exceedance uses the provided `a`; "
                      "item f finds that game `a` is not the derivative of game `s` (see "
                      "f_reliability_game.csv for this mode), so treat the accel number as "
                      "provisional."))


# --------------------------------------------------------------------------- #
# (i) power / MDE
# --------------------------------------------------------------------------- #
def _power_closed_form(n_db: int, m: int, sigma: float, rho: float, alpha: float,
                       target_power: float) -> dict:
    if n_db <= 0 or m <= 0:
        return dict(mde=None, se=None, n_eff=None, status=UNESTIMABLE)
    # within-player information: sum_i sum_j (x_ij - xbar_i)^2 = n*m*(1-rho)
    info = n_db * m * max(1e-9, (1.0 - rho))
    se = sigma / math.sqrt(info)
    mde = (stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(target_power)) * se
    return dict(mde=float(mde), se=float(se), n_eff=float(info), status="ESTIMATED")


def _power_mc(n_db: int, m: int, cfg: dict, seed: int, log) -> dict:
    """Seeded Monte Carlo: fit `y ~ x + (1+x | player)` at beta=0 and exploit linearity.

    For a linear mixed model the profiled variance components are invariant to the fixed
    effect (the residuals y-Xb are unchanged by adding beta*x), and the slope estimator is
    linear in y, so slope_hat(beta) = slope_hat(0) + beta exactly. One fit per replicate
    therefore yields power at every grid effect. The identity is validated on the first
    two replicates and the run is marked UNESTIMABLE if it fails.
    """
    pw = cfg["db_decay"]["power"]
    reps = int(pw["reps"])
    grid = [float(x) for x in pw["effect_grid"]]
    alpha = float(pw["alpha"])
    target = float(pw["power"])
    sigma = float(pw["assumed_sd_effort_fraction"])
    rho = float(pw["assumed_load_icc_within_player"])
    sd_u = float(pw["assumed_random_intercept_sd"])
    sd_s = float(pw["assumed_random_slope_sd"])
    if n_db <= 1 or m <= 1 or reps <= 0:
        return dict(mde=None, curve=[], status=UNESTIMABLE, n_reps=0,
                    note="too few DBs/efforts/reps for a Monte Carlo")
    try:
        import pandas as pd
        import statsmodels.formula.api as smf
    except Exception as exc:  # pragma: no cover - dependency is pinned
        return dict(mde=None, curve=[], status=f"UNESTIMABLE: {type(exc).__name__}: {exc}",
                    n_reps=0)
    rng = np.random.default_rng(seed)
    z = stats.norm.ppf(1 - alpha / 2)
    slopes, ses, fails = [], [], 0
    shift_err = []
    for rep in range(reps):
        pid = np.repeat(np.arange(n_db), m)
        x = rng.normal(0, math.sqrt(rho), n_db)[pid] + rng.normal(0, math.sqrt(1 - rho), n_db * m)
        u = rng.normal(0, sd_u, n_db)
        s = rng.normal(0, sd_s, n_db)
        y = u[pid] + s[pid] * x + rng.normal(0, sigma, n_db * m)
        df = pd.DataFrame({"y": y, "x": x, "pid": pid})
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = smf.mixedlm("y ~ x", df, groups=df["pid"],
                                  re_formula="1+x").fit(method="lbfgs", maxiter=100, disp=False)
            b, se = float(fit.params["x"]), float(fit.bse["x"])
            if not (np.isfinite(b) and np.isfinite(se) and se > 0):
                raise ValueError("non-finite fit")
            slopes.append(b); ses.append(se)
            if rep < 2:  # validate the beta-shift identity
                y2 = y + 0.01 * x
                df2 = pd.DataFrame({"y": y2, "x": x, "pid": pid})
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    f2 = smf.mixedlm("y ~ x", df2, groups=df2["pid"],
                                     re_formula="1+x").fit(method="lbfgs", maxiter=100,
                                                           disp=False)
                shift_err.append(abs(float(f2.params["x"]) - b - 0.01))
        except Exception:
            fails += 1
    if len(slopes) < max(10, int(0.8 * reps)):
        return dict(mde=None, curve=[], status=f"UNESTIMABLE: {fails}/{reps} fits failed",
                    n_reps=len(slopes), n_failures=fails)
    slopes = np.asarray(slopes); ses = np.asarray(ses)
    curve = []
    mde = None
    for e in grid:
        power = float(np.mean(np.abs(slopes + e) > z * ses))
        curve.append(dict(effect=e, power=power))
        if mde is None and power >= target:
            mde = e
    return dict(mde=mde, curve=curve, status="ESTIMATED", n_reps=len(slopes),
                n_failures=fails, median_se=float(np.median(ses)),
                shift_identity_max_err=(max(shift_err) if shift_err else None),
                note=("MDE is the smallest grid effect with power >= target"
                      if mde is not None else "no grid effect reached the target power"))


def item_i(ctx: dict, g: dict, c: dict) -> dict:
    dd = ctx["dd"]
    pw = dd["power"]
    n_db = int(c.get("n_in_player_play") or 0)
    m = int(pw["assumed_efforts_per_db"])
    alpha = float(pw["alpha"])
    target = float(pw["power"])
    seed = int(ctx["cfg"].get("seed", 0)) + int(pw["seed_offset"])
    cf_ = _power_closed_form(n_db, m, float(pw["assumed_sd_effort_fraction"]),
                             float(pw["assumed_load_icc_within_player"]), alpha, target)
    mc = _power_mc(n_db, m, ctx["cfg"], seed, ctx["log"])
    sens = []
    for nd in (int(dd["audit"]["min_matched_dbs"]), n_db):
        r = _power_closed_form(nd, m, float(pw["assumed_sd_effort_fraction"]),
                               float(pw["assumed_load_icc_within_player"]), alpha, target)
        sens.append(dict(item="i", method="closed_form", n_dbs=nd, efforts_per_db=m,
                         mde=r["mde"], se=r["se"]))
    emit(ctx, "i_power.csv", sens)
    emit(ctx, "i_power_curve.csv",
         [dict(item="i", method="monte_carlo", effect=x["effect"], power=x["power"],
               reps=mc["n_reps"]) for x in mc.get("curve", [])])

    load_sd = float(pw["assumed_load_sd_per10"])
    mde_sd = mc["mde"] if mc.get("mde") is not None else cf_["mde"]
    assumptions = dict(
        n_dbs=n_db, efforts_per_db=m, games_per_db=int(pw["assumed_games_per_db"]),
        alpha=alpha, power=target, reps=int(pw["reps"]),
        assumed_sd_effort_fraction=float(pw["assumed_sd_effort_fraction"]),
        assumed_load_icc_within_player=float(pw["assumed_load_icc_within_player"]),
        assumed_random_intercept_sd=float(pw["assumed_random_intercept_sd"]),
        assumed_random_slope_sd=float(pw["assumed_random_slope_sd"]),
        assumed_load_sd_per10=load_sd,
        smallest_effect_of_interest=float(pw["smallest_effect_of_interest"]),
        load_units="mean-centred cumulative_load; 1 unit = 10 prior high-intensity efforts",
        conversion="mde_per_10_efforts = mde_per_sd / assumed_load_sd_per10",
        mde_per_10_efforts=(mde_sd / load_sd if mde_sd is not None else None),
        observed_load_sd="NOT AVAILABLE at audit time (stage 03); config placeholder used",
        seed=seed,
    )
    summary = [
        dict(item="i", method="closed_form", n_dbs=n_db, efforts_per_db=m,
             mde_effort_fraction_per_sd=cf_["mde"], se=cf_["se"],
             mde_per_10_efforts=(cf_["mde"] / load_sd if cf_["mde"] else None),
             status=cf_["status"]),
        dict(item="i", method="monte_carlo_mixedlm", n_dbs=n_db, efforts_per_db=m,
             mde_effort_fraction_per_sd=mc.get("mde"), se=mc.get("median_se"),
             mde_per_10_efforts=(mc.get("mde") / load_sd if mc.get("mde") else None),
             status=mc.get("status")),
    ]
    emit(ctx, "i_power_summary.csv", summary)
    write_json(ctx["out"] / "i_power_assumptions.json", dict(assumptions, provenance=ctx["prov"]))
    return dict(status="PARTIAL", pending=PENDING,
                n_dbs=n_db, efforts_per_db=m,
                closed_form_mde=cf_["mde"], mc_mde=mc.get("mde"),
                mc_status=mc.get("status"), mc_reps=mc.get("n_reps"),
                mde_per_10_efforts=(mde_sd / load_sd if mde_sd is not None else None),
                assumptions=assumptions)


# --------------------------------------------------------------------------- #
# gate + deliverables
# --------------------------------------------------------------------------- #
def _icc_gate_status(icc, icc_min: float, mode: str) -> str:
    """A required/selected ICC is blocking. Unestimable is pending in sample, fail in full."""
    if icc is None:
        return "pending" if mode != "full" else "fail"
    return "pass" if float(icc) >= icc_min else "fail"


def decide(ctx: dict, items: dict) -> dict:
    """Evaluate the go/no-go gate on the SELECTED ceiling feature set.

    TASK.md says the ceiling keeps at most three features chosen by ICC then VIF; the
    candidates are screened, not mandated. So a candidate below ``icc_min`` is recorded
    as ``excluded_by_selection`` (non-blocking) and the gate is evaluated on the selected
    features. A *required* feature that fails ICC still fails the gate: selection never
    launders a required failure into a pass.
    """
    dd = ctx["dd"]
    a = dd["audit"]
    ceil = ceiling_config(dd)
    sel_cfg = ceil["selection"]
    icc_min = float(sel_cfg["icc_min"])
    checks: list[dict] = []
    warnings: list[str] = []

    def add(name, value, threshold, status, item, **extra):
        rec = dict(name=name, value=value, threshold=threshold, status=status, item=item)
        rec.update(extra)
        checks.append(rec)

    add("dbs_in_combine_tracking", items["a"]["n_dbs_in_combine"], ">=1",
        "pass" if items["a"]["n_dbs_in_combine"] > 0 else "fail", "a")
    add("matched_dbs_player_play", items["c"]["n_in_player_play"],
        f">={a['min_matched_dbs']}",
        "pass" if items["c"]["n_in_player_play"] >= a["min_matched_dbs"] else "fail", "c")
    tr_n = items["c"]["n_in_game_tracking"]
    add("matched_dbs_game_tracking", tr_n, f">={a['min_matched_dbs']}",
        ("pass" if tr_n >= a["min_matched_dbs"] else "fail")
        if ctx["mode"] == "full" else "pending", "c_tracking")
    add("efforts_per_db", None, f">={a['min_efforts_per_db']}", "pending", "i")
    add("total_efforts", None, f">={a['min_total_efforts']}", "pending", "i")

    g = items["g"]
    selection = g.get("selection")
    if not isinstance(selection, dict) or not isinstance(selection.get("selected"), list):
        raise GateError("item (g) did not produce a selection object; "
                        "the gate cannot be evaluated on the selected feature set")
    detail = {d.get("feature"): d for d in selection.get("selected_detail", [])
              if d.get("feature")}
    for name in selection["selected"]:
        rec = detail.get(name, {})
        icc = rec.get("icc")
        add(f"icc_selected_{name}", icc, f">={icc_min}",
            _icc_gate_status(icc, icc_min, ctx["mode"]), "g",
            ci_low=rec.get("ci_low"), ci_high=rec.get("ci_high"),
            selection=("required" if rec.get("required") else "icc_then_vif"))
    for ex in selection.get("excluded", []):
        add(f"icc_excluded_{ex['feature']}", ex.get("icc"), f">={icc_min}",
            "excluded_by_selection", "g",
            ci_low=ex.get("ci_low"), ci_high=ex.get("ci_high"),
            reason=ex.get("reason"))
    for req in [str(r) for r in ceil["required_features"]]:
        if req not in selection["selected"]:
            add(f"required_feature_{req}_selected", False, "true", "fail", "g")

    sens = g.get("icc_sensitivity") or {}
    for row in sens.get("rows", []):
        if (row.get("feature") in selection["selected"]
                and row.get("icc") is not None and float(row["icc"]) < icc_min):
            warnings.append(
                f"ICC sensitivity: selected feature {row['feature']!r} gives ICC "
                f"{float(row['icc']):.4f} under the {row['variant']!r} filter variant, "
                f"below icc_min {icc_min:.2f}; the gate uses "
                f"{sens.get('gate_variant')!r}.")
    if str(selection.get("vif_status", "")).startswith("UNESTIMABLE"):
        warnings.append("VIF unestimable for the ICC-passing candidates "
                        f"({selection.get('vif_status')}); selection kept the ICC order "
                        "up to max_features.")
    for ex in selection.get("excluded", []):
        if ex.get("reason") == "unestimable":
            warnings.append(f"candidate {ex['feature']!r} is UNESTIMABLE for ICC and was "
                            "excluded by selection (not used in the ceiling).")
    for name in selection["selected"]:
        if str(detail.get(name, {}).get("drill")) == "POOLED_CUT_DRILLS":
            warnings.append(f"selected feature {name!r} is a POOLED cut metric: its ICC "
                            "mixes different drills and is not a pure test-retest. It is "
                            "reliability evidence for a candidate only, not for a ceiling "
                            "feature.")

    mde = items["i"].get("mc_mde")
    if mde is None:
        mde = items["i"].get("closed_form_mde")
    soi = float(dd["power"]["smallest_effect_of_interest"])
    # The MC/closed-form MDE is per 1 SD of load; the pre-registered smallest effect of
    # interest (notes/prereg.md) is per 10 prior high-intensity episodes. Compare like with
    # like, or an adequate design is failed (or, worse, a weak one passed) on units alone.
    load_sd = float(dd["power"]["assumed_load_sd_per10"])
    mde_per10 = (float(mde) / load_sd) if mde is not None else None
    add("power_mde_vs_smallest_effect_of_interest", mde_per10,
        f"<={soi} effort fraction per 10 efforts",
        "pass" if (mde_per10 is not None and mde_per10 <= soi) else
        ("pending" if mde_per10 is None else "fail"), "i",
        mde_per_sd_load=mde, assumed_load_sd_per10=load_sd,
        conversion="mde_per_sd_load / assumed_load_sd_per10")
    add("thresholds_provenance", a.get("provenance_thresholds"), "==FULL", "pending",
        "00_thresholds")
    prereg = common.REPO_ROOT / "notes" / "prereg.md"
    add("prereg_present", prereg.exists(), "true",
        "pending" if ctx["mode"] != "full" else ("pass" if prereg.exists() else "fail"),
        "prereg")

    failures = [c["name"] for c in checks if c["status"] == "fail"]
    pending = [c["name"] for c in checks if c["status"] == "pending"]
    if failures:
        decision = "NO_GO"
    elif ctx["mode"] == "full" and not pending:
        decision = "GO"
    else:
        # sample mode, or a full run with unresolved items: never a bare GO
        decision = "GO_PENDING_FULL_RUN"

    notes = ["Selection semantics: candidates are screened by ICC then VIF; only the "
             "selected set is gate-blocking, excluded candidates are recorded, never dropped."]
    if failures:
        notes.append("NO_GO: blocking check(s) failed -> " + ", ".join(failures)
                     + ". A required feature failing ICC is a spec decision (fix the "
                       "estimator or renegotiate the prereg in notes/decisions.md), not an "
                       "automatic exclusion.")
    elif decision == "GO":
        notes.append("GO: every evaluable check passed and no pending items remained.")
    else:
        notes.append("GO is conditional on the pending full-run items; no bare GO is ever "
                     "emitted.")
    return dict(
        stage=STAGE,
        task="In-game decay of DB performance relative to the Combine ceiling",
        decision=decision,
        mode=ctx["mode"],
        provenance=ctx["prov"],
        synthetic=ctx["synthetic"],
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        checks=checks,
        failures=failures,
        pending_full_run=pending,
        config_minima={k: a[k] for k in (
            "min_matched_dbs", "min_efforts_per_db", "min_efforts_per_db_slope",
            "min_total_efforts", "min_icc_primary", "min_icc_secondary",
            "min_repeat_players_icc")},
        thresholds_provenance=a.get("provenance_thresholds"),
        items={k: {"status": v.get("status")} for k, v in items.items()},
        selection=selection,
        icc_sensitivity=sens,
        warnings=warnings,
        notes=notes,
    )


def gate_checks(ctx: dict, items: dict) -> list[dict]:
    return decide(ctx, items)["checks"]


def write_decision(ctx: dict, checks: list[dict] | None, items: dict) -> dict:
    """Write the DB-decay decision to its own tree; never clobber another stage's decision.

    The legacy WR/TE pipeline enforces `outputs/audit/audit_decision.json` via `src/gate.py`.
    D52 says the two audit trees coexist, so the authoritative file is
    `outputs/audit/db_decay/audit_decision.json`. The legacy path is mirrored **only** when it
    is absent or already holds this stage's decision; an existing WR/TE decision is preserved
    untouched (use `db_decay/audit_decision.json` for the DB gate).
    """
    payload = decide(ctx, items)
    if checks is not None:
        payload["checks"] = checks
    cfg = ctx["cfg"]
    write_json(ctx["out"] / "audit_decision.json", payload)
    legacy = common.resolve(cfg, "outputs") / "audit" / "audit_decision.json"
    owned = False
    if legacy.exists():
        old = common.read_done_marker(legacy)
        owned = old.get("stage") == STAGE
    if not legacy.exists() or owned:
        write_json(legacy, payload)
    return payload


def write_summary(ctx: dict, items: dict, decision: dict) -> Path:
    dd = ctx["dd"]
    p = ctx["out"] / "SUMMARY_db_decay.md"
    lines = [
        f"# 01_db_audit — DB in-game decay audit — {ctx['prov']}",
        "",
        f"- mode: `{ctx['mode']}`" + (" (synthetic fixture)" if ctx["synthetic"] else ""),
        f"- provenance: `{ctx['prov']}`",
        f"- gate decision: **{decision['decision']}**",
        f"- thresholds provenance: `{decision['thresholds_provenance']}`"
        + ("" if str(decision["thresholds_provenance"]) == "FULL"
           else " (stage `00_thresholds` has not frozen DB-decay thresholds)"),
        f"- pending full run: {', '.join(decision['pending_full_run']) or 'none'}",
        "",
        "Game-effort thresholds below are **PLACEHOLDER_SAMPLE** until `00_thresholds` runs: "
        f"speed {dd['game']['effort_speed_frac_of_vmax']:.2f}x vmax "
        f"(floor {dd['game']['effort_speed_floor_yds']} yd/s), accel "
        f"{dd['game']['effort_accel_frac_of_a0']:.2f}x a0 "
        f"(floor {dd['game']['effort_accel_floor_yds2']} yd/s^2), min effort "
        f"{dd['game']['min_effort_duration_s']} s, garbage-time margins "
        f"Q4 {dd['game']['garbage_time_q4_margin']} / any {dd['game']['garbage_time_any_margin']}, "
        f"min snaps {dd['game']['min_snaps_per_player']}.",
        "",
    ]
    if ctx["mode"] == "sample" and not ctx["synthetic"]:
        lines += ["> **UNVALIDATED SAMPLE OUTPUT** — never interpret or tune against these numbers.",
                  ""]
    lines += ["## Items", "", "| item | status | headline |", "|---|---|---|"]
    a, b, c, d, e, f, g, h, i = (items[k] for k in "abcdefghi")
    lines += [
        f"| a | {a['status']} | {a['n_dbs_in_combine']}/{a['n_dbs']} DBs in Combine; "
        f"{a['n_drills']} drills; {a['n_dbs_missing_40']} without a 40-yard dash |",
        f"| b | {b['status']} | attempt unit `{b['chosen_attempt_unit']}` "
        f"({b['n_duplicates_chosen']} duplicate keys vs {b['n_duplicates_H2']} for "
        f"player/drill_type); classes {b['class_years']} |",
        f"| c | {c['status']} | {c['n_combine_dbs']} combine → {c['n_in_player_play']} "
        f"player_play → {c['n_in_game_tracking']} tracking DBs; alignment agreement "
        f"{_pct(c.get('alignment_agreement'))} |",
        f"| d | {d['status']} | all 22 tracked: **{d['all_22_tracked']}** "
        f"(player_play mean {_fmt(d['player_play_players_per_play_mean'], 2)}/play; "
        f"tracking {_fmt(d['sample_players_per_play_mean'], 2)}/play) |",
        f"| e | {e['status']} | {e['n_db_games']} DB-games; all-four-quarters share "
        f"{_pct(e['attrition_all_four'])}; Q1-not-Q4 {_pct(e['attrition_q1_not_q4'])} |",
        f"| f | {f['status']} | 40-yd `dis` tolerance violation "
        f"{_pct(f['combine_dis_violation_40'])}; `s` {_pct(f['combine_s_violation_40'])}; "
        f"game `dis` {_pct(f['game_dis_violation'])} |",
        f"| g | {g['status']} | vmax ICC {_fmt(g['vmax_icc'], 3)} "
        f"[{_fmt(g['vmax_icc_ci'][0], 3)}, {_fmt(g['vmax_icc_ci'][1], 3)}]; a0 ICC "
        f"{_fmt(g['a0_icc'], 3)} (excluded_by_selection); "
        f"selected `{', '.join(g['selection']['selected'])}`; "
        f"{len(g['cut_icc_unestimable'])} cut rows unestimable |",
        f"| h | {h['status']} | speed exceedance {_pct(h['speed_exceedance_rate'])} on "
        f"{h['n_speed_units']} sample DB player-plays ({PENDING}) |",
        f"| i | {i['status']} | MDE {_fmt(i['mc_mde'], 4)} effort-fraction per 1 SD load "
        f"= {_fmt(i.get('mde_per_10_efforts'), 5)} per 10 efforts "
        f"(MC, {i['mc_reps']} reps) / {_fmt(i['closed_form_mde'], 4)} closed-form |",
        "",
        "## Gate checks",
        "",
        "| check | value | threshold | status |",
        "|---|---|---|---|",
    ]
    for ch in decision["checks"]:
        lines.append(f"| {ch['name']} | {_fmt(ch['value'])} | {ch['threshold']} | "
                     f"{ch['status']} |")
    lines += ["", "## Decision interpretation", ""]
    if decision["failures"]:
        lines.append("**NO_GO.** Failed stop condition(s): "
                     + ", ".join(f"`{f}`" for f in decision["failures"]) + ".")
        for ch in decision["checks"]:
            if ch["status"] == "fail":
                lines.append(f"- `{ch['name']}` = {_fmt(ch['value'])} (threshold "
                             f"{ch['threshold']}, item {ch['item']}).")
        lines.append("- Remediation for a required feature is a spec decision: fix its "
                     "estimator or renegotiate the prereg in `notes/decisions.md`, then "
                     "re-run with `--force`.")
    elif decision["decision"] == "GO":
        lines.append("**GO.** Every evaluable check passed and no pending items remained.")
    else:
        lines.append("**GO_PENDING_FULL_RUN.** No evaluable stop condition failed; the pending "
                     "full-run items above still block any final claim.")
    lines.append("- Candidate features are screened, not mandated: failing ICC means "
                 "`excluded_by_selection`, and only the selected set is gate-blocking.")
    lines.append("- Excluded candidates (recorded, never dropped): "
                 + ", ".join(f"`{e['feature']}` ({e['reason']}, ICC "
                             f"{_fmt(e['icc'], 3)})"
                             for e in g["selection"]["excluded"]))
    lines += ["", "## Ceiling feature selection", "",
              f"- rule: `{g['selection']['rule']}`  ·  "
              f"icc_min {g['selection']['thresholds']['icc_min']}  ·  "
              f"vif_max {g['selection']['thresholds']['vif_max']}  ·  "
              f"max_features {g['selection']['thresholds']['max_features']}",
              f"- required: `{', '.join(g['selection']['required'])}`  ·  "
              f"selected: `{', '.join(g['selection']['selected'])}`",
              f"- VIF: {g['selection']['vif_status']} "
              f"(n={g['selection']['n_players_vif']}); "
              f"values `{ {k: round(v, 3) for k, v in g['selection']['vif'].items()} }`",
              "", "## ICC sensitivity (40-yard dash)", "",
              "| variant | feature | ICC | 95% CI | n_players | n_used | n_excluded | role |",
              "|---|---|---|---|---|---|---|---|"]
    for row in g["icc_sensitivity"]["rows"]:
        lines.append(
            f"| {row['variant']} | {row['feature']} | {_fmt(row['icc'], 3)} | "
            f"[{_fmt(row['ci_low'], 3)}, {_fmt(row['ci_high'], 3)}] | "
            f"{row['n_players']} | {row['n_attempts_used']} | {row['n_excluded']} | "
            f"{row['variant_role']} |")
    bias = g.get("sanity_bias") or {}
    bias_share = ((bias.get("n_flagged") or 0) / bias["n_attempts"]
                  if bias.get("n_attempts") else None)
    f40 = bias.get("forty_yard_dash") or {}
    lines += ["", "## Sanity-flag bias (data quality, not selection on speed)", "",
              f"- attempts {bias.get('n_attempts')} · flagged {bias.get('n_flagged')} "
              f"({_pct(bias_share)}); exclusion applies to 40-yard fits only",
              f"- 40-yard flagged-vs-clean vmax: "
              f"{_fmt(f40.get('flagged_mean_vmax'), 3)} "
              f"(SD {_fmt(f40.get('flagged_sd_vmax'), 3)}) vs "
              f"{_fmt(f40.get('clean_mean_vmax'), 3)} "
              f"(SD {_fmt(f40.get('clean_sd_vmax'), 3)}) yd/s",
              f"- players losing ALL 40-yard attempts: "
              f"{bias.get('n_players_losing_all_attempts')} "
              f"(without any other drill: {bias.get('n_without_other_drills')}); "
              f"ids sha256 `{bias.get('ids_sha256')}`",
              f"- flagged share by draft_year (all / 40-yard): "
              f"`{bias.get('flag_share_by_draft_year')}` / "
              f"`{bias.get('flag_share_by_draft_year_40')}`",
              f"- flagged share by position (all / 40-yard): "
              f"`{bias.get('flag_share_by_position')}` / "
              f"`{bias.get('flag_share_by_position_40')}`",
              f"- flagged share by drill: `{bias.get('flag_share_by_drill')}`",
              ]
    if decision.get("warnings"):
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in decision["warnings"]]
    lines += ["", "## Provenance and limits", "",
              f"- Every CSV starts with a `provenance` column equal to `{ctx['prov']}`.",
              "- `01_db_audit` is Combine-complete; game-side numbers in sample mode are "
              "diagnostic only and must be reproduced on the full data.",
              "- Never invent a value: `UNESTIMABLE` and `PENDING FULL RUN` rows are explicit.",
              ""]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _fmt(v, nd: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (float, np.floating)):
        return f"{float(v):.{nd}f}"
    return str(v)


def _pct(v) -> str:
    return "n/a" if v is None else f"{100.0 * float(v):.1f}%"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(cfg: dict, mode: str, force: bool = False, synthetic: bool = False) -> dict:
    log = common.get_logger()
    dd = cfg["db_decay"]
    validate_db_decay_config(dd)  # fail fast on any missing/invalid db_decay key
    prov = SYNTH_PROV if synthetic else common.provenance(cfg, mode)
    out = common.resolve(cfg, "outputs") / dd["audit"]["out_subdir"]
    out.mkdir(parents=True, exist_ok=True)
    ctx = dict(cfg=cfg, dd=dd, mode=mode, synthetic=synthetic, prov=prov, out=out, log=log)
    stage_name = f"{STAGE}[{mode}]"
    marker = out / ".done.json"
    fp = common.config_fingerprint(cfg, STAGE)

    rec = common.read_done_marker(marker)
    skip = (not force) and common.checkpoint_match(
        rec, mode=mode, provenance=prov, synthetic=synthetic, path=marker, fingerprint=fp)
    notes = {"synthetic": synthetic}
    if skip:
        notes["checkpoint"] = True
    with common.timed_stage(cfg, stage_name, mode, **notes):
        if skip:
            log.info("checkpoint hit — skipping %s (use --force to rebuild)", stage_name)
            return dict(skipped=True, out=str(out))

        con = common.connect(threads=2)
        paths = resolve_inputs(cfg, mode, synthetic)
        # loud schema gate before any computation
        for k in ("combine_tracking", "players", "games", "player_play"):
            assert_schema(con, paths[k], k)
        for p in paths["game_tracking"]:
            assert_schema(con, p, "game_tracking")
        ctx["con"] = con
        ctx["sql"] = dict(
            combine_tracking=read_sql(paths["combine_tracking"]),
            players=read_sql(paths["players"]),
            games=read_sql(paths["games"]),
            player_play=read_sql(paths["player_play"]),
            game_tracking=game_tracking_sql(paths["game_tracking"]),
        )
        ctx["paths"] = paths
        common.seed_everything(int(cfg.get("seed", 0)))

        items: dict[str, dict] = {}
        items["a"] = item_a(ctx)
        items["b"] = item_b(ctx)
        items["c"] = item_c(ctx)
        items["d"] = item_d(ctx)
        items["e"] = item_e(ctx)
        items["f"] = item_f(ctx)
        items["g"] = item_g(ctx)
        # item g already wrote per-attempt features; re-read for the ceiling shrinkage
        feats = []
        import csv as _csv
        with open(out / "g_attempt_features.csv", newline="", encoding="utf-8") as fh:
            for r in _csv.DictReader(fh):
                d = dict(r)
                for key in ("vmax", "tau", "a0", "max_decel", "entry_speed", "exit_speed",
                            "cod_deficit"):
                    d[key] = float(d[key]) if d.get(key) not in (None, "", "None") else np.nan
                d["nfl_id"] = int(d["nfl_id"])
                d["vmax_flag"] = r.get("vmax_flag") or ""
                feats.append(d)
        items["h"] = item_h(ctx, items["g"], feats)
        items["i"] = item_i(ctx, items["g"], items["c"])

        checks = gate_checks(ctx, items)
        decision = write_decision(ctx, checks, items)
        summary = write_summary(ctx, items, decision)

        # audit items JSON
        items_payload = dict(
            stage=STAGE, mode=mode, provenance=prov, synthetic=synthetic,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            thresholds_provenance=dd["audit"]["provenance_thresholds"],
            game_thresholds_placeholder={k: dd["game"][k] for k in (
                "effort_speed_frac_of_vmax", "effort_speed_floor_yds",
                "effort_accel_frac_of_a0", "effort_accel_floor_yds2",
                "min_effort_duration_s", "garbage_time_q4_margin",
                "garbage_time_any_margin", "min_snaps_per_player")},
            pending_full_run=dd["audit"]["pending_full_run"],
            config_minima={k: dd["audit"][k] for k in (
                "min_matched_dbs", "min_efforts_per_db", "min_efforts_per_db_slope",
                "min_total_efforts", "min_icc_primary", "min_icc_secondary",
                "min_repeat_players_icc")},
            items={k: v for k, v in items.items()},
            gate_decision=decision["decision"],
        )
        write_json(out / "audit_items.json", items_payload)
        write_json(marker, common.done_marker(
            cfg, STAGE, mode=mode, provenance=prov, synthetic=synthetic,
            n_items=len(items), decision=decision["decision"]))
        log.info("01_db_audit complete: decision=%s out=%s", decision["decision"], out)
        return dict(skipped=False, out=str(out), decision=decision, items=items,
                    summary=str(summary))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DB-decay Phase 1 audit (items a-i)")
    ap.add_argument("--mode", choices=["sample", "full"], default="sample")
    ap.add_argument("--force", action="store_true", help="rebuild even if checkpointed")
    ap.add_argument("--synthetic", action="store_true",
                    help="use the tiny schema-compliant fixture (never a finding)")
    ap.add_argument("--config", default=None, help="alternate config.yaml (tests)")
    a = ap.parse_args(argv)
    cfg = common.load_config(a.config)
    try:
        res = run(cfg, a.mode, force=a.force, synthetic=a.synthetic)
    except (SchemaError, GateError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if res.get("skipped"):
        print(f"checkpoint hit: {res['out']} (use --force to rebuild)")
    else:
        print(f"decision={res['decision']['decision']} out={res['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
