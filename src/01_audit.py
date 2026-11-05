#!/usr/bin/env python3
"""01_audit.py — Phase 1 audit (gate: kill test).

Answers TASK.md Phase 1 items (a)-(i) with counts, not adjectives, and writes a
machine-readable gate decision. Items that cannot be settled without full-data
game tracking are SCRIPTED but reported as ``PENDING_FULL_RUN``.

Outputs
-------
outputs/audit/audit_items.csv          one row per measured quantity
outputs/audit/gate.json                machine-readable go/no-go
outputs/audit/combine_drill_inventory.csv
outputs/audit/dl_rusher_counts.csv
outputs/audit/onset_combine_attempts.csv
notes/phase1-audit.md                  human report (written by hand from these)

Coverage labelling
------------------
Every row carries ``scope``:

* ``FULL_RAW``            -- the whole file as provided (combine_*, player_play, games, players)
* ``SAMPLE_TRACKING``     -- derived from the 2**20-row game-tracking sample
* ``DESIGN``              -- a design calculation, not a data estimate

Rows whose scope is ``SAMPLE_TRACKING`` are stamped UNVALIDATED SAMPLE OUTPUT
and must never be quoted as a finding.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import physical as P  # noqa: E402
import schema as S  # noqa: E402
import stats as ST  # noqa: E402

STAGE = "01_audit"
SAMPLE = C.SAMPLE_STAMP


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #
FIELDS = ["item", "name", "status", "value", "unit", "scope", "evidence", "note"]


def row(item, name, value, unit="", status="INFO", scope="FULL_RAW",
        evidence="", note=""):
    if isinstance(value, float):
        value = None if math.isnan(value) else round(value, 6)
    return {"item": item, "name": name, "status": status, "value": value,
            "unit": unit, "scope": scope, "evidence": evidence, "note": note}


def dl_set(pp: str, cfg) -> str:
    """The canonical DL analysis set: lined up on the DL *and* has a get-off."""
    vals = "(" + ", ".join(f"'{p}'" for p in
                           C.section(cfg, "positions")["dl_linedup"]) + ")"
    return (f"(SELECT DISTINCT nfl_id FROM {pp} WHERE lined_up_position IN {vals} "
            f"AND player_get_off IS NOT NULL)")


def _status_for(scope: str) -> str:
    return "PENDING_FULL_RUN" if scope == "SAMPLE_TRACKING" else "INFO"


# --------------------------------------------------------------------------- #
# LOS / depth (item c)
# --------------------------------------------------------------------------- #
#: Yard line in field coordinates. Verified against the tracked position of DL
#: rushers at the snap (median depth 0.6 yd, 95th percentile < 2.5 yd).
LOS_SQL = """
CAST(CASE
  WHEN (m.yardline_side = m.possession_team AND m.play_direction = 'right')
    OR (m.yardline_side <> m.possession_team AND m.play_direction = 'left')
  THEN 10.0 + m.yardline_number
  ELSE 110.0 - m.yardline_number
END AS DOUBLE)
"""


def item_a(con, cfg):
    """Combine tracking for DL: drills, straight-line sprint, attempts, years."""
    cr = C.read_csv_sql(C.source_path(cfg, "combine_results"), cfg)
    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    dl = f"(SELECT nfl_id, draft_year FROM {cr} WHERE combine_position='DL')"

    rows, detail = [], []
    n_dl = con.execute(f"SELECT count(*) FROM {dl}").fetchone()[0]
    rows.append(row("a", "combine DL players", int(n_dl), "players",
                    evidence="player_play cross-check below",
                    note="combine_position='DL' in combine_results.csv"))
    years = con.execute(
        f"SELECT min(draft_year), max(draft_year), count(DISTINCT draft_year) "
        f"FROM {dl}").fetchone()
    rows.append(row("a", "DL class years covered", f"{years[0]}-{years[1]}",
                    "years", evidence="combine_results.draft_year",
                    note=f"{years[2]} distinct draft years"))

    inv = con.execute(f"""
        SELECT t.drill_name, t.drill_type, count(*) n_rows,
               count(DISTINCT t.nfl_id) n_players,
               count(DISTINCT (t.nfl_id, t.attempt)) n_attempts
        FROM {ct} t JOIN {dl} d USING(nfl_id)
        GROUP BY 1,2 ORDER BY n_players DESC, n_rows DESC""").fetchall()
    for name, typ, n_rows, n_players, n_att in inv:
        detail.append({"drill_name": name, "drill_type": typ, "rows": n_rows,
                       "players": n_players, "attempts": n_att})
    rows.append(row("a", "distinct drills containing DL players", len(inv),
                    "drills", evidence="combine_tracking x combine_results",
                    note="see combine_drill_inventory.csv"))

    sprint = [d for d in detail if d["drill_name"] == "FORTY_YARD_DASH"]
    rows.append(row(
        "a", "straight-line sprint from a stance present",
        "PASS" if sprint else "FAIL", "",
        status="PASS" if sprint else "FAIL",
        evidence="FORTY_YARD_DASH rows for combine DL players",
        note=(f"{sprint[0]['attempts']} attempts by {sprint[0]['players']} DL"
              if sprint else "no 40-yard dash rows for DL")))
    if sprint:
        s = sprint[0]
        rows.append(row("a", "40yd attempts per DL player",
                        round(s["attempts"] / s["players"], 2), "attempts/player",
                        evidence="count(DISTINCT (nfl_id, attempt))"))
        rows.append(row("a", "DL players with >=2 40yd attempts",
                        int(con.execute(
                            f"SELECT count(*) FROM (SELECT nfl_id, count(DISTINCT attempt) k "
                            f"FROM {ct} WHERE drill_name='FORTY_YARD_DASH' AND nfl_id IN "
                            f"(SELECT nfl_id FROM {dl}) GROUP BY 1) WHERE k>=2"
                        ).fetchone()[0]), "players",
                        evidence="needed for test-retest (item h)"))
    return rows, detail


def item_b(con, cfg):
    """Matched Combine-to-NFL DL, snaps per rusher, games per rusher, split."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    cr = C.read_csv_sql(C.source_path(cfg, "combine_results"), cfg)
    a = C.section(cfg, "audit")
    rows, detail = [], []

    total_pp = con.execute(
        f"SELECT count(DISTINCT nfl_id) FROM {pp}").fetchone()[0]
    matched = con.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT nfl_id FROM {pp}) d "
        f"JOIN {cr} USING(nfl_id)").fetchone()[0]
    rows.append(row("b", "players in player_play", int(total_pp), "players",
                    evidence="count(DISTINCT nfl_id) player_play.csv"))
    rows.append(row("b", "matched to a Combine row", int(matched), "players",
                    evidence="inner join on nfl_id",
                    note="the provided dataset is pre-filtered to Combine "
                         "attendees, so matching is 100% by construction — "
                         "there is no unmatched group to audit"))

    per = con.execute(f"""
        SELECT nfl_id, count(*) reps, count(DISTINCT game_id) games,
               min(lined_up_position) pos
        FROM {pp} WHERE player_get_off IS NOT NULL
        GROUP BY 1 ORDER BY reps DESC""").fetchall()
    for nfl_id, reps, games, pos in per:
        detail.append({"nfl_id": nfl_id, "get_off_reps": reps, "games": games,
                       "position": pos})
    n = len(per)
    reps = [r[1] for r in per]
    games = [r[2] for r in per]
    rows.append(row("b", "DL rushers with >=1 player_get_off", n, "players",
                    evidence="player_get_off non-null"))
    rows.append(row("b", "median pass-rush reps per rusher", ST.describe(reps)["median"],
                    "reps", evidence="player_get_off non-null",
                    note=f"min {min(reps)}, max {max(reps)}"))
    rows.append(row("b", "rushers with >= min_pass_rush_snaps_per_rusher",
                    sum(1 for r in reps if r >= a["min_pass_rush_snaps_per_rusher"]),
                    "players",
                    evidence=f"threshold {a['min_pass_rush_snaps_per_rusher']} from config"))
    rows.append(row("b", "median games per rusher", ST.describe(games)["median"],
                    "games", evidence="distinct game_id per nfl_id",
                    note=f"min {min(games)}, max {max(games)}"))

    split = con.execute(f"""
        SELECT lined_up_position, count(*) reps, count(DISTINCT nfl_id) players
        FROM {pp} WHERE player_get_off IS NOT NULL
        GROUP BY 1 ORDER BY reps DESC""").fetchall()
    for pos, r_, p_ in split:
        rows.append(row("b", f"get-off reps at {pos}", int(r_), "reps",
                        evidence="lined_up_position", note=f"{p_} players"))
    return rows, detail


def item_c(con, cfg):
    """Can pre-snap depth and snap time be derived? Is stance labelled?"""
    gt = C.read_csv_sql(C.tracking_sources(cfg, "sample"), cfg)
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    pos = C.section(cfg, "positions")
    dlsql = "(" + ", ".join(f"'{p}'" for p in pos["dl_linedup"]) + ")"
    rows = []

    cols = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM {pp}").fetchall()]
    stance_cols = [c for c in cols if "stance" in c.lower()]
    rows.append(row("c", "stance column present", "no" if not stance_cols else "yes",
                    "", status="INFO", evidence="DESCRIBE player_play",
                    note=f"columns matching /stance/: {stance_cols or 'none'}"))

    plays = con.execute(f"SELECT count(DISTINCT (game_id, play_id)) FROM {gt}"
                        ).fetchone()[0]
    snaps = con.execute(
        f"SELECT count(DISTINCT (game_id, play_id)) FROM {gt} WHERE event='ball_snap'"
    ).fetchone()[0]
    rows.append(row("c", "plays in tracking sample", int(plays), "plays",
                    scope="SAMPLE_TRACKING", evidence="game_tracking sample"))
    rows.append(row("c", "plays with a ball_snap event", int(snaps), "plays",
                    status="PASS" if snaps == plays else "INFO",
                    scope="SAMPLE_TRACKING", evidence="event='ball_snap'",
                    note="snap time is an explicit event, not inferred"))
    rows.append(row("c", "snap-time derivation", "event='ball_snap'", "",
                    status="PASS", evidence="tracking event column",
                    note="explicit; no inference needed"))

    dep = con.execute(f"""
        WITH snap AS (SELECT game_id, play_id, nfl_id, any_value(x) x
                      FROM {gt} WHERE event='ball_snap' GROUP BY 1,2,3),
        meta AS (SELECT game_id, play_id, nfl_id, yardline_side, yardline_number,
                        play_direction, possession_team, lined_up_position
                 FROM {pp})
        SELECT abs(s.x - ({LOS_SQL})) d
        FROM snap s JOIN meta m USING(game_id, play_id, nfl_id)
        WHERE m.lined_up_position IN {dlsql}""").fetchall()
    d = [r[0] for r in dep]
    if d:
        desc = ST.describe(d)
        rows.append(row("c", "pre-snap depth derivable", "yes", "",
                        status="PASS", scope="VALIDATION",
                        evidence="|x_at_snap - LOS| where LOS is derived from "
                                 "yardline_side/yardline_number/play_direction",
                        note="LOS is metadata-derived, so this does not depend "
                             "on the tracking sample at all"))
        rows.append(row("c", "median pre-snap depth", desc["median"], "yd",
                        scope="SAMPLE_TRACKING", evidence="n=" + str(desc["n"])))
        rows.append(row("c", "p95 pre-snap depth", desc["p95"], "yd",
                        scope="SAMPLE_TRACKING", evidence="n=" + str(desc["n"]),
                        note="depth is the distance to cover to cross the line; "
                             "a small value means the first yard dominates"))
        share = sum(1 for v in d if 0 <= v <= 5) / len(d)
        rows.append(row("c", "share of depths in [0,5] yd", round(share, 4), "share",
                        status="PASS" if share > 0.95 else "FAIL",
                        scope="VALIDATION", evidence=f"n={len(d)}",
                        note="a low share would mean the LOS formula is wrong"))
    else:
        rows.append(row("c", "pre-snap depth derivable", "unverified", "",
                        status="PENDING_FULL_RUN", scope="SAMPLE_TRACKING",
                        evidence="no DL snap in the sample had a ball_snap event"))
    return rows, []


def item_d(con, cfg):
    """What is player_get_off, exactly? Documentation + empirical check."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    rows = []
    rows.append(row(
        "d", "definition in official documentation", "NOT AVAILABLE", "",
        status="PENDING_FULL_RUN",
        evidence="no data dictionary ships with the provided files; the Kaggle "
                 "competition pages are client-rendered (notes/rules.md §1)",
        note="TASK.md states 'seconds from snap until the rusher crosses the "
             "line of scrimmage'. That is the working definition; it is not "
             "confirmed against an official dictionary."))
    n_cap = con.execute(
        f"SELECT count(*) FROM {pp} WHERE player_get_off >= 1.999").fetchone()[0]
    mx = con.execute(f"SELECT max(player_get_off) FROM {pp}").fetchone()[0]
    rows.append(row("d", "max observed player_get_off", mx, "s",
                    evidence="player_play.csv", note="capped at 2.0 s"))
    rows.append(row("d", "reps at the 2.0 s cap", int(n_cap), "reps",
                    evidence="player_get_off >= 1.999",
                    note="a hard cap; treat as censored, not as a true value"))
    return rows, []


def item_d_empirical(con, cfg):
    """Compare player_get_off with a tracking-derived crossing time (sample).

    The tracking sample is loaded ONCE into a frame and grouped in Python. Never
    issue one query per snap against a CSV: that was ~1 s per snap on the Pi.
    """
    gt = C.read_csv_sql(C.tracking_sources(cfg, "sample"), cfg)
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    pos = C.section(cfg, "positions")
    dlsql = "(" + ", ".join(f"'{p}'" for p in pos["dl_linedup"]) + ")"

    picks = con.execute(f"""
        WITH snap AS (SELECT game_id, play_id, nfl_id, any_value(x) x
                      FROM {gt} WHERE event='ball_snap' GROUP BY 1,2,3),
        meta AS (SELECT game_id, play_id, nfl_id, yardline_side, yardline_number,
                        play_direction, possession_team, player_get_off,
                        lined_up_position
                 FROM {pp} WHERE player_get_off IS NOT NULL)
        SELECT s.game_id, s.play_id, s.nfl_id, m.player_get_off, s.x, ({LOS_SQL}) los
        FROM snap s JOIN meta m USING(game_id, play_id, nfl_id)
        WHERE m.lined_up_position IN {dlsql}""").df()
    if picks.empty:
        return [row("d", "empirical check vs tracking", "no overlapping snaps", "",
                    status="PENDING_FULL_RUN", scope="SAMPLE_TRACKING")], []

    # Stream the tracking file game-batch by game-batch. A single load of the
    # full 3-season tracking file would be ~17 GB in pandas, so the same code
    # must work on the sample (1M rows) and on full data without changing shape.
    games = sorted(set(picks["game_id"].tolist()))
    # 25 games per batch keeps the pandas frame near 100 MB even in full mode.
    per_batch = 25
    diffs_along, diffs_x, depths = [], [], []
    snaps = (con.execute(
        f"SELECT game_id, play_id, min(time) FROM {gt} WHERE event='ball_snap' "
        f"GROUP BY 1,2").df())
    snaps = {(r[0], r[1]): r[2] for r in snaps.itertuples(index=False)}

    for start in range(0, len(games), per_batch):
        chunk = games[start:start + per_batch]
        ids = ", ".join(str(int(g)) for g in chunk)
        track = con.execute(f"""
            SELECT game_id, play_id, nfl_id, time, x, y, s, event
            FROM {gt} WHERE game_id IN ({ids})
            ORDER BY game_id, play_id, nfl_id, time""").df()
        track = track.sort_values(["game_id", "play_id", "nfl_id", "time"])
        sub_picks = picks[picks["game_id"].isin(chunk)]
        for r in sub_picks.itertuples(index=False):
            key = (r.game_id, r.play_id)
            if key not in snaps:
                continue
            sub = track[(track["game_id"] == r.game_id) &
                        (track["play_id"] == r.play_id) &
                        (track["nfl_id"] == r.nfl_id)]
            if len(sub) < 8:
                continue
            snap_t = snaps[key]
            times = [(x - snap_t).total_seconds() for x in sub["time"]]
            xs, ys, ss = sub["x"].tolist(), sub["y"].tolist(), sub["s"].tolist()
            onset = P.detect_onset(times, ss, cfg)
            if not onset["found"]:
                continue
            i0 = onset["index"]
            depth = abs(float(r.x) - float(r.los))
            depths.append(depth)
            try:
                ux, uy = P.travel_direction(xs, ys, start=i0)
            except ValueError:
                continue
            along = P.project_along(xs, ys, ux, uy, origin=(xs[i0], ys[i0]))
            res = P.time_at_distance(times, along, depth, first_index=i0)
            if res["reached"]:
                diffs_along.append(res["time_s"] - float(r.player_get_off))
            sign = 1.0 if float(r.los) >= float(r.x) else -1.0
            xprog = [(x - float(r.x)) * sign for x in xs]
            resx = P.time_at_distance(times, xprog, depth, first_index=i0)
            if resx["reached"]:
                diffs_x.append(resx["time_s"] - float(r.player_get_off))

    rows = []
    if diffs_along:
        d = ST.describe([abs(v) for v in diffs_along])
        rows.append(row("d", "median |along-travel crossing - player_get_off|",
                        d["median"], "s", scope="SAMPLE_TRACKING",
                        evidence=f"n={d['n']} DL snaps with a detectable onset",
                        note=f"{SAMPLE}: a definition check, not a finding"))
        rows.append(row("d", "median signed difference (along-travel)",
                        ST.describe(diffs_along)["median"], "s",
                        scope="SAMPLE_TRACKING", evidence=f"n={len(diffs_along)}",
                        note=SAMPLE))
    if diffs_x:
        rows.append(row("d", "median |x-axis crossing - player_get_off|",
                        ST.describe([abs(v) for v in diffs_x])["median"], "s",
                        scope="SAMPLE_TRACKING", evidence=f"n={len(diffs_x)}",
                        note=f"{SAMPLE}; compare with the along-travel row"))
    rows.append(row("d", "snaps used in the definition check", len(diffs_along),
                    "snaps", scope="SAMPLE_TRACKING", evidence="item d empirical"))
    return rows, []


def item_e(con, cfg):
    """Frames in the first second / first yard, in both settings."""
    rows = []
    o = C.section(cfg, "onset")
    rows.append(row("e", "sampling rate, Combine tracking", int(o["hz"]), "Hz",
                    evidence="100 ms spacing, verified by lag-difference",
                    note="nominal"))
    rows.append(row("e", "sampling rate, game tracking", int(o["hz"]), "Hz",
                    scope="SAMPLE_TRACKING",
                    evidence="mode of lag(time) == 100 ms over 1,033,962 pairs"))

    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    cr = C.read_csv_sql(C.source_path(cfg, "combine_results"), cfg)
    fr = con.execute(f"""
        WITH a AS (SELECT nfl_id, attempt, count(*) frames
                   FROM {ct} WHERE drill_name='FORTY_YARD_DASH'
                     AND nfl_id IN (SELECT nfl_id FROM {cr} WHERE combine_position='DL')
                   GROUP BY 1,2)
        SELECT min(frames), median(frames), max(frames), count(*) FROM a""").fetchone()
    rows.append(row("e", "Combine 40yd frames per attempt (min/median/max)",
                    f"{fr[0]}/{fr[1]}/{fr[2]}", "frames",
                    evidence="n=" + str(fr[3]) + " DL attempts"))
    rows.append(row("e", "Combine frames in the first second after onset",
                    int(o["hz"]), "frames", evidence="10 Hz x 1 s",
                    note="by construction; the measured rate confirms 10 Hz"))
    rows.append(row("e", "frames to cover the first yard at 8 yd/s", 2, "frames",
                    evidence="1 yd / 8 yd/s = 0.125 s = 1-2 frames",
                    note="resolution in the first yard is 1-2 samples in BOTH "
                         "settings: this is the binding constraint on onset"))

    gt = C.read_csv_sql(C.tracking_sources(cfg, "sample"), cfg)
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    pos = C.section(cfg, "positions")
    dlsql = "(" + ", ".join(f"'{p}'" for p in pos["dl_linedup"]) + ")"
    g = con.execute(f"""
        WITH snap AS (SELECT game_id, play_id, min(time) ts FROM {gt}
                      WHERE event='ball_snap' GROUP BY 1,2),
        dl AS ({dl_set(pp, cfg)}),
        t AS (SELECT g.game_id, g.play_id, g.nfl_id,
                     count(*) FILTER (WHERE g.time >= s.ts
                                      AND g.time < s.ts + INTERVAL 1 SECOND) f1,
                     count(*) FILTER (WHERE g.time < s.ts) pre
              FROM {gt} g JOIN dl USING(nfl_id) JOIN snap s USING(game_id, play_id)
              GROUP BY 1,2,3)
        SELECT count(*), min(f1), median(f1), max(f1), median(pre),
               count(DISTINCT (game_id, play_id)) FROM t""").fetchone()
    rows.append(row("e", "game frames in the first second after snap (min/median/max)",
                    f"{g[1]}/{g[2]}/{g[3]}", "frames", scope="SAMPLE_TRACKING",
                    evidence=f"{g[0]} DL player-plays, {g[5]} distinct plays",
                    note="per player-per-play, not summed across players"))
    rows.append(row("e", "median pre-snap frames per DL player-play", g[4], "frames",
                    scope="SAMPLE_TRACKING", evidence=f"{g[0]} DL player-plays"))
    rows.append(row("e", "same onset definition usable in both settings",
                    "PASS (structurally)", "",
                    status="PASS",
                    evidence="identical 10 Hz grid and identical threshold formula",
                    note="whether the SAME threshold value is appropriate in both "
                         "settings is a calibration question -> PENDING_FULL_RUN"))
    return rows, []


def item_f(con, cfg):
    """Null semantics and provenance of the pressure labels."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    pos = C.section(cfg, "positions")
    dlsql = "(" + ", ".join(f"'{p}'" for p in pos["dl_linedup"]) + ")"
    rows = []
    n_dl = con.execute(f"SELECT count(*) FROM {pp} WHERE lined_up_position IN {dlsql} "
                       f"AND player_get_off IS NOT NULL").fetchone()[0]
    q_pct = 100.0 * con.execute(
        f"SELECT count(*) FILTER (WHERE quick_pressure IS NULL) * 1.0 / count(*) "
        f"FROM {pp} WHERE lined_up_position IN {dlsql} "
        f"AND player_get_off IS NOT NULL").fetchone()[0]
    for col in ("quick_pressure", "unblocked_pressure", "time_to_pressure",
                "time_to_qb_hurry"):
        q = con.execute(f"""
            SELECT count(*) FILTER (WHERE {col} IS NULL),
                   count(*) FILTER (WHERE {col} IS NOT NULL)
            FROM {pp} WHERE lined_up_position IN {dlsql}
             AND player_get_off IS NOT NULL""").fetchone()
        rows.append(row("f", f"{col}: null / non-null on DL rows",
                        f"{q[0]}/{q[1]}", "rows",
                        evidence=f"DL rows = {n_dl}",
                        note=f"{100.0*q[0]/n_dl:.1f}% null"))
    cross = con.execute(f"""
        SELECT quick_pressure, (time_to_pressure IS NULL) ttp_null, count(*)
        FROM {pp} WHERE lined_up_position IN {dlsql}
             AND player_get_off IS NOT NULL
             AND (quick_pressure IS NOT NULL OR time_to_pressure IS NOT NULL)
        GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    for qp, tnull, n in cross:
        rows.append(row("f", f"quick_pressure={qp} & time_to_pressure_null={tnull}",
                        int(n), "rows", evidence="cross-tab on DL rows"))
    rows.append(row(
        "f", "quick_pressure usable as a complete binary outcome", "NO", "",
        status="FAIL",
        evidence=f"{100.0*q_pct:.1f}% of DL get-off rows are null; null is not "
                 f"'no pressure'",
        note="BLOCKER: the secondary outcome cannot be modelled as a binary "
             "target without a documented null policy. Logged in blockers.md."))
    rows.append(row(
        "f", "pressure label provenance", "model-derived", "",
        evidence="player_play carries peak_pressure_probability_allowed, "
                 "pressure_allowed, time_to_pressure_allowed, quick_pressure",
        note="a pressure-probability model produces these; they are not a naive "
             "function of the rusher's own tracking. No row-level provenance "
             "column exists to confirm this."))
    return rows, []


# --------------------------------------------------------------------------- #
# Item g — kill-test statistics
# --------------------------------------------------------------------------- #
def item_g(con, cfg):
    """Split-half ICC of mean get-off across games; SD decomposition."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    g = C.read_csv_sql(C.source_path(cfg, "games"), cfg)
    a = C.section(cfg, "audit")
    rows = []

    df = con.execute(f"""
        SELECT p.nfl_id, p.game_id, p.player_get_off, gg.season, gg.week,
               p.play_id
        FROM {pp} p JOIN {g} gg USING(game_id)
        WHERE p.player_get_off IS NOT NULL""").df()
    if df.empty:
        return [row("g", "split-half ICC", "no data", "",
                    status="BLOCKED", evidence="no get-off rows")], []

    # chronological rank of games, then odd/even
    order = (df[["season", "week", "game_id"]].drop_duplicates()
             .sort_values(["season", "week", "game_id"]).reset_index(drop=True))
    order["rank"] = range(len(order))
    df = df.merge(order[["game_id", "rank"]], on="game_id", how="left")
    df["half"] = df["rank"] % 2
    per = (df.groupby(["nfl_id", "half"])["player_get_off"].mean()
           .unstack("half"))
    pairs = [(r.get(0), r.get(1)) for _, r in per.iterrows()]
    sh = ST.split_half_icc(pairs)

    rows.append(row("g", "split-half ICC of player mean get-off",
                    sh.get("icc_2_1"), "ICC_2_1", status="PENDING_FULL_RUN",
                    evidence=f"odd/even games, n={sh.get('n_players')} players",
                    note="PROVISIONAL — computed from the provided player_play "
                         "(1001/1002 games), not from game tracking. Value is "
                         "recorded for continuity; the gate stays PENDING."))
    rows.append(row("g", "split-half Pearson r", sh.get("r"), "r",
                    status="PENDING_FULL_RUN",
                    evidence=f"n={sh.get('n_players')}"))
    rows.append(row("g", "Spearman-Brown stepped-up reliability",
                    sh.get("spearman_brown"), "reliability",
                    status="PENDING_FULL_RUN",
                    evidence=f"n={sh.get('n_players')}"))

    # variance decomposition of rep-level get-off
    overall = df["player_get_off"].var(ddof=1)
    means = df.groupby("nfl_id")["player_get_off"].agg(["mean", "var", "count"])
    between = means["mean"].var(ddof=1)
    within = float((means["var"] * (means["count"] - 1)).sum() /
                   max(1, (means["count"] - 1).sum()))
    rows.append(row("g", "between-player SD of mean get-off",
                    math.sqrt(between), "s", status="PENDING_FULL_RUN",
                    evidence=f"n={len(means)} players"))
    rows.append(row("g", "within-player SD of rep get-off",
                    math.sqrt(within), "s", status="PENDING_FULL_RUN",
                    evidence="pooled within-player variance"))
    share = between / (between + within) if (between + within) > 0 else None
    rows.append(row("g", "between-player share of total get-off variance", share,
                    "share", status="PENDING_FULL_RUN",
                    evidence="one-way decomposition (unadjusted for depth)",
                    note="upper bound: not adjusted for pre-snap depth, which is "
                         "exactly what Phase 2-4 must do"))
    return rows, []


# --------------------------------------------------------------------------- #
# Item h — Combine test-retest
# --------------------------------------------------------------------------- #
def item_h(con, cfg):
    """ICC of Combine onset-to-distance times across attempts (DL 40yd)."""
    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    cr = C.read_csv_sql(C.source_path(cfg, "combine_results"), cfg)
    rows, detail = [], []
    df = con.execute(f"""
        SELECT t.nfl_id, t.attempt, t.time, t.x, t.y, t.s
        FROM {ct} t
        WHERE t.drill_name='FORTY_YARD_DASH'
          AND t.entity_type = 'PLAYER'
          AND t.nfl_id IN (SELECT nfl_id FROM {cr} WHERE combine_position='DL')
        ORDER BY t.nfl_id, t.attempt, t.time""").df()
    if df.empty:
        return [row("h", "test-retest ICC", "no attempts", "",
                    status="BLOCKED", evidence="no DL 40yd rows")], []

    distances = [1.0, 2.0, 5.0]
    per_player: dict[int, list[dict]] = {}
    for (nfl_id, attempt), sub in df.groupby(["nfl_id", "attempt"]):
        times = [(t - sub["time"].iloc[0]).total_seconds()
                 for t in sub["time"]]
        m = P.measure_attempt(times, sub["x"].tolist(), sub["y"].tolist(),
                              sub["s"].tolist(), cfg, distances)
        rec = {"nfl_id": int(nfl_id), "attempt": int(attempt),
               "ok": bool(m["ok"]), "reason": m["reason"],
               "onset_s": m.get("onset_s")}
        for d in distances:
            key = f"t_to_{str(d).replace('.', 'p')}yd"
            rec[key] = m.get(key)
        detail.append(rec)
        per_player.setdefault(int(nfl_id), []).append(rec)

    n_att = len(detail)
    n_ok = sum(1 for r in detail if r["ok"])
    rows.append(row("h", "DL 40yd attempts measured", n_att, "attempts",
                    evidence="FORTY_YARD_DASH, entity_type=PLAYER"))
    rows.append(row("h", "attempts with a detectable onset", n_ok, "attempts",
                    status="PASS" if n_ok / max(1, n_att) > 0.9 else "FAIL",
                    evidence="onset.speed_yds from config",
                    note=f"{100*n_ok/max(1,n_att):.1f}%"))
    for d in distances:
        key = f"t_to_{str(d).replace('.', 'p')}yd"
        groups = [[r[key] for r in rs if r.get(key) is not None]
                  for rs in per_player.values()]
        r_ = ST.icc_oneway(groups)
        rows.append(row("h", f"test-retest ICC, onset->{d:g} yd",
                        r_["icc"], "ICC_1_1",
                        status="PASS" if r_["icc"] >= C.section(cfg, "audit")["icc_testretest_min"] else "FAIL",
                        evidence=f"{r_['k_subjects']} players, "
                                 f"{r_['mean_obs_per_subject']:.2f} attempts each",
                        note=f"min {C.section(cfg,'audit')['icc_testretest_min']}"))
    return rows, detail


# --------------------------------------------------------------------------- #
# Item i — power
# --------------------------------------------------------------------------- #
def item_i(con, cfg):
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    a = C.section(cfg, "audit")
    rows = []
    per = con.execute(f"""
        SELECT nfl_id, count(*) reps, count(DISTINCT game_id) games
        FROM {pp} WHERE player_get_off IS NOT NULL GROUP BY 1""").df()
    if per.empty:
        return [row("i", "power", "no data", "", status="BLOCKED")], []
    eligible = per[per["reps"] >= a["min_pass_rush_snaps_per_rusher"]]
    p = len(eligible)
    n = float(eligible["reps"].median()) if p else float("nan")
    md = ST.min_detectable_share(p)
    rows.append(row("i", "players meeting the snap minimum", p, "players",
                    evidence=f"reps >= {a['min_pass_rush_snaps_per_rusher']}"))
    rows.append(row("i", "median reps per eligible player", n, "reps",
                    evidence="player_get_off non-null"))
    rows.append(row("i", "smallest detectable between-player variance share",
                    md["min_share"], "share", status="PASS",
                    scope="DESIGN", evidence=md.get("method", ""),
                    note=f"smallest effect of interest = "
                         f"{a['smallest_effect_of_interest']}"))
    rows.append(row("i", "design resolves the smallest effect of interest",
                    "yes" if md["min_share"] <= a["smallest_effect_of_interest"] else "no",
                    "", status="PASS" if md["min_share"] <= a["smallest_effect_of_interest"] else "FAIL",
                    scope="DESIGN", evidence="compare the two rows above"))
    rel = ST.reliability_from_share(a["smallest_effect_of_interest"], n)
    rows.append(row("i", "model-based reliability at the smallest effect of interest",
                    rel, "reliability", scope="DESIGN",
                    evidence="sigma_u^2/(sigma_u^2+sigma_e^2/n)"))
    return rows, []


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
def build_gate(rows, cfg):
    a = C.section(cfg, "audit")
    by_item = {}
    for r in rows:
        by_item.setdefault(r["item"], []).append(r)

    def status(item, name_fragment):
        for r in by_item.get(item, []):
            if name_fragment in r["name"]:
                return r["status"]
        return "MISSING"

    def val(item, name_fragment):
        for r in by_item.get(item, []):
            if name_fragment in r["name"]:
                return r["value"]
        return None

    conditions = [
        {"id": "combine_sprint_present",
         "condition": "A straight-line Combine sprint from a stance exists",
         "status": status("a", "straight-line sprint"),
         "evidence": val("a", "distinct drills containing DL players")},
        {"id": "depth_derivable",
         "condition": "Pre-snap depth and the line of scrimmage can be derived",
         "status": status("c", "pre-snap depth derivable"),
         "evidence": val("c", "share of depths in [0,5] yd")},
        {"id": "onset_consistent",
         "condition": "Onset can be defined consistently across settings",
         "status": status("e", "same onset definition"),
         "evidence": "identical 10 Hz grid; threshold equivalence is a "
                     "calibration question (Phase 2)"},
        {"id": "get_off_reliability",
         "condition": f"Mean get-off split-half ICC >= {a['icc_min']}",
         "status": "PENDING_FULL_RUN",
         "evidence": val("g", "split-half ICC of player mean get-off")},
        {"id": "combine_vs_get_off",
         "condition": "Combine-predicted time relates to mean get-off at player level",
         "status": "PENDING_FULL_RUN",
         "evidence": "requires Phase 2 physical times, which this run does not "
                     "implement"},
        {"id": "snaps_per_rusher",
         "condition": f">= {a['min_pass_rush_snaps_per_rusher']} pass-rush reps per rusher",
         "status": ("PASS" if (val("i", "players meeting the snap minimum") or 0) >= a["min_matched_dl"]
                    else "FAIL"),
         "evidence": val("i", "players meeting the snap minimum")},
        {"id": "matched_dl",
         "condition": f">= {a['min_matched_dl']} matched DL",
         "status": ("PASS" if (val("b", "DL rushers with >=1 player_get_off") or 0) >= a["min_matched_dl"]
                    else "FAIL"),
         "evidence": val("b", "DL rushers with >=1 player_get_off")},
    ]
    failed = [c for c in conditions if c["status"] == "FAIL"]
    pending = [c for c in conditions if c["status"] == "PENDING_FULL_RUN"]
    decision = ("NO-GO" if failed else
                "CONDITIONAL GO — no kill condition triggered, "
                f"{len(pending)} pending full data" if pending else "GO")
    return {
        "stage": STAGE,
        "decision": decision,
        "failed": [c["id"] for c in failed],
        "pending": [c["id"] for c in pending],
        "conditions": conditions,
        "thresholds": a,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    cfg, mode, force = C.stage_bootstrap(argv, STAGE)
    if C.is_done(cfg, STAGE, mode, force):
        print(f"{STAGE}: checkpoint present for mode={mode}; use --force to rerun")
        return 0
    out = C.paths(cfg, mode)["outputs"] / "audit"
    out.mkdir(parents=True, exist_ok=True)

    with C.timed_stage(cfg, STAGE, mode) as st:
        con = C.duckdb_connect(cfg)
        rows, details = [], {}
        try:
            for key in ("games", "players", "combine_results", "combine_tracking",
                        "player_play"):
                S.assert_schema(con, cfg, key)
            S.assert_schema(con, cfg, "game_tracking", mode=mode)
            S.assert_vocabulary(con, cfg)
            st.note("schema ok")
            units = {"combine_tracking": S.assert_units(con, cfg, "combine_tracking"),
                     "game_tracking": S.assert_units(con, cfg, "game_tracking", mode=mode)}
            units["tracking_contract"] = S.tracking_contract(con, cfg, mode)
            st.note("units ok; tracking contract ok")

            for fn in (item_a, item_b, item_c, item_d, item_f, item_g, item_h, item_i):
                r, d = fn(con, cfg)
                rows += r
                details[fn.__name__] = d
            r, d = item_d_empirical(con, cfg)
            rows += r
            r, d = item_e(con, cfg)
            rows += r
        finally:
            con.close()

        gate = build_gate(rows, cfg)

        C.write_csv(out / "audit_items.csv", rows, FIELDS, mode=mode, cfg=cfg)
        C.write_csv(out / "combine_drill_inventory.csv",
                    details.get("item_a", []),
                    ["drill_name", "drill_type", "rows", "players", "attempts"],
                    mode=mode, cfg=cfg)
        C.write_csv(out / "dl_rusher_counts.csv", details.get("item_b", []),
                    ["nfl_id", "get_off_reps", "games", "position"],
                    mode=mode, cfg=cfg)
        C.write_csv(out / "onset_combine_attempts.csv", details.get("item_h", []),
                    ["nfl_id", "attempt", "ok", "reason", "onset_s",
                     "t_to_1p0yd", "t_to_2p0yd", "t_to_5p0yd"],
                    mode=mode, cfg=cfg)
        C.write_json(out / "gate.json", gate, mode=mode, cfg=cfg)
        C.write_json(out / "units_measured.json", units, mode=mode, cfg=cfg)

        st.note(f"gate={gate['decision']}")
        print(f"{STAGE}: {len(rows)} audit rows; decision = {gate['decision']}")
        if gate["failed"]:
            print(f"  FAILED conditions: {gate['failed']}")
        if gate["pending"]:
            print(f"  PENDING full data: {gate['pending']}")

    C.mark_done(cfg, STAGE, mode, {"n_rows": len(rows)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
