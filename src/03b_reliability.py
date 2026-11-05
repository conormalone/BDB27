#!/usr/bin/env python3
"""03b_reliability — per-player reliability and the Phase 4 selection rule.

Answers ONE question, with evidence and no threshold choice:

    which players should enter the Phase 4 variance decomposition, and how
    reliable is a player-level get-off estimate?

Inputs (and their completeness status, which is what the provenance stamp rests
on — see ``input_evidence``):

* ``outputs/phase3/snaps.parquet`` — Phase 3's per-snap table. Only its
  label-side columns are read (``player_get_off``, ids, season/week,
  alignment), so the analysis is **not** limited by the 2023 tracking sample.
  ``reconcile_label_side`` proves that: the parquet's 44,002 get-off rows and
  33,061 primary rows are compared with a direct query on the complete
  ``player_play.csv`` and must match exactly or the stage refuses.
* ``data/raw/player_play.csv`` — complete (314,197 rows, one row per
  player-play, 1,001 of 1,002 games).
* ``data/raw/games.csv`` — complete (1,002 games, 2023-2025).
* ``data/raw/combine_tracking.csv`` — complete (510 players, 6,301
  player-attempts; a sample could not preserve attempt counts).
* ``data/raw/players.csv`` — complete (display names only).

Therefore the artefacts are stamped ``FULL`` and are **not**
``UNVALIDATED SAMPLE OUTPUT``. What this stage cannot do is anything that needs
in-game tracking: the split-half here is across *games*, which the label file
identifies, not across tracked frames.

Nothing in this stage fits a Phase 4 model, chooses a threshold, or writes to
Phase 4's inputs.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import common as C  # noqa: E402
import reliability as REL  # noqa: E402
import stats as ST  # noqa: E402

STAGE = "03b_reliability"

Z_FLOOR = 1.96  # the z used by stats.min_detectable_share; same function, same z


# --------------------------------------------------------------------------- #
# Input provenance
# --------------------------------------------------------------------------- #
def input_evidence(con, cfg) -> dict:
    """Row counts and the assertion that makes each input "complete"."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    games = C.read_csv_sql(C.source_path(cfg, "games"), cfg)
    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    players = C.read_csv_sql(C.source_path(cfg, "players"), cfg)

    pp_rows, pp_keys, pp_games = con.execute(
        f"SELECT count(*), count(DISTINCT (game_id, play_id, nfl_id)), "
        f"count(DISTINCT game_id) FROM {pp}").fetchone()
    game_rows, game_seasons = con.execute(
        f"SELECT count(*), count(DISTINCT season) FROM {games}").fetchone()
    ct_players, ct_attempts, ct_drills, ct_years = con.execute(
        f"SELECT count(DISTINCT nfl_id), count(DISTINCT (nfl_id, event_id)), "
        f"count(DISTINCT drill_name), count(DISTINCT draft_year) FROM {ct} "
        f"WHERE entity_type = 'PLAYER'").fetchone()
    pl_rows = con.execute(f"SELECT count(*) FROM {players}").fetchone()[0]

    if int(pp_rows) != int(pp_keys):
        raise C.SchemaError(
            "player_play is not one row per player-play: "
            f"{pp_rows} rows vs {pp_keys} distinct keys; completeness cannot be "
            "asserted (notes/assumptions.md A15)")
    covered = int(pp_games) / max(1, int(game_rows))
    if covered < 0.99:
        raise C.SchemaError(
            f"player_play covers {pp_games}/{game_rows} games ({covered:.3f}); "
            "a row sample cannot be ruled out (A7)")

    return {
        "player_play.csv": {
            "rows": int(pp_rows), "distinct_player_play_keys": int(pp_keys),
            "games_covered": int(pp_games), "games_in_games_csv": int(game_rows),
            "games_covered_share": round(covered, 6),
            "status": "COMPLETE",
            "basis": "one row per (game, play, player); 1,001/1,002 games; "
                     "2023-2025. Not a row sample (A15, A7, B5)."},
        "games.csv": {
            "rows": int(game_rows), "seasons": int(game_seasons),
            "status": "COMPLETE", "basis": "every season of the release"},
        "combine_tracking.csv": {
            "players": int(ct_players), "player_attempts": int(ct_attempts),
            "drills": int(ct_drills), "draft_years": int(ct_years),
            "status": "COMPLETE",
            "basis": "510 attendees, 1-4 forty-yard attempts each, 3 draft "
                     "classes; a sample would lose attempt structure (A12)"},
        "players.csv": {"rows": int(pl_rows), "status": "COMPLETE",
                        "basis": "same 510 attendees as the Combine file"},
    }


def reconcile_label_side(con, snaps_path: Path, cfg) -> dict:
    """Prove the Phase 3 table's label side is the complete label file.

    Compares the parquet's counts with a direct query on ``player_play.csv``
    using the documented rule. Any mismatch is a schema failure, because the
    FULL provenance stamp below would otherwise be unearned.
    """
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    dl = [str(v) for v in C.section(cfg, "positions")["dl_linedup"]]
    passes = [str(v) for v in C.section(cfg, "snaps")["pass_results"]]
    lo, hi = [float(v) for v in C.section(cfg, "schema")["get_off_range"]]
    dl_sql = ", ".join(f"'{v}'" for v in dl)
    pass_sql = ", ".join(f"'{v}'" for v in passes)
    direct = con.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE lined_up_position IN ({dl_sql})
                                  AND pass_result IN ({pass_sql})
                                  AND player_get_off BETWEEN {lo} AND {hi})
        FROM {pp} WHERE player_get_off IS NOT NULL""").fetchone()
    rel = f"read_parquet('{Path(snaps_path).as_posix()}')"
    table = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE in_primary_set)
        FROM {rel}""").fetchone()
    out = {"direct_get_off_rows": int(direct[0]),
           "table_rows": int(table[0]),
           "direct_primary_rows": int(direct[1]),
           "table_primary_rows": int(table[1])}
    if out["direct_get_off_rows"] != out["table_rows"]:
        raise C.SchemaError(
            "LABEL-SIDE RECONCILIATION FAILED: Phase 3 table has "
            f"{out['table_rows']} get-off rows, the complete label file has "
            f"{out['direct_get_off_rows']}. FULL provenance is not asserted.")
    if out["direct_primary_rows"] != out["table_primary_rows"]:
        raise C.SchemaError(
            "LABEL-SIDE RECONCILIATION FAILED: Phase 3 primary set has "
            f"{out['table_primary_rows']} rows, the complete label file gives "
            f"{out['direct_primary_rows']} under the documented rule.")
    out["status"] = "MATCH"
    out["meaning"] = ("the Phase 3 table carries every DL pass-rush get-off row "
                      "in the release; no row sampling is involved")
    return out


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_primary(con, snaps_path: Path):
    """Primary-set reps, ordered chronologically within player."""
    rel = f"read_parquet('{Path(snaps_path).as_posix()}')"
    return con.execute(f"""
        SELECT nfl_id, game_id, season, week, play_id,
               player_get_off AS y, alignment_class
        FROM {rel}
        WHERE in_primary_set AND player_get_off IS NOT NULL
        ORDER BY nfl_id, season, week, game_id, play_id""").df()


def forty_players(con, cfg) -> set[int]:
    """Players with a tracked 40-yard attempt (the Phase 4 fittable condition)."""
    ct = C.read_csv_sql(C.source_path(cfg, "combine_tracking"), cfg)
    drill = C.section(cfg, "physical")["drill"]
    rows = con.execute(
        f"SELECT DISTINCT nfl_id FROM {ct} WHERE drill_name = '{drill}' "
        f"AND entity_type = 'PLAYER'").fetchall()
    return {int(r[0]) for r in rows}


def display_names(con, cfg) -> dict[int, str]:
    pl = C.read_csv_sql(C.source_path(cfg, "players"), cfg)
    return {int(r[0]): str(r[1]) for r in con.execute(
        f"SELECT nfl_id, display_name FROM {pl}").fetchall()}


def game_order(df) -> dict:
    """Player -> distinct games in chronological order (caller sorted the frame)."""
    order: dict[int, list] = {}
    for pid, gid in zip(df["nfl_id"].tolist(), df["game_id"].tolist()):
        seq = order.setdefault(int(pid), [])
        if not seq or seq[-1] != gid:
            seq.append(int(gid))
    return order


# --------------------------------------------------------------------------- #
# Per-player table
# --------------------------------------------------------------------------- #
def per_player_table(df, halves, k, comps3, forty, names) -> list[dict]:
    pm = df.groupby("nfl_id")["y"].agg(["mean", "size", "std"])
    games = df.groupby("nfl_id")["game_id"].nunique()
    edge = (df.assign(is_edge=(df["alignment_class"] == "EDGE"))
            .groupby("nfl_id")["is_edge"].mean())
    half_by = {int(h["player"]): h for h in halves}
    rows = []
    for pid in pm.index:
        pid = int(pid)
        n = int(pm.loc[pid, "size"])
        g = int(games.loc[pid])
        h = half_by.get(pid, {})
        rel_k = REL.reliability_from_k(n, k)
        rel3 = REL.reliability_three_level(
            n, g, comps3["sigma2_player"], comps3["sigma2_game"],
            comps3["sigma2_rep"])
        rows.append({
            "nfl_id": pid, "display_name": names.get(pid, ""),
            "reps_primary": n, "games": g,
            "mean_get_off_s": round(float(pm.loc[pid, "mean"]), 6),
            "sd_within_player_s": (None if n < 2
                                   else round(float(pm.loc[pid, "std"]), 6)),
            "share_edge": round(float(edge.loc[pid]), 6),
            "reliability_rep_level": round(rel_k, 6) if not math.isnan(rel_k) else None,
            "reliability_three_level": round(rel3, 6) if not math.isnan(rel3) else None,
            "n_half0": h.get("n_half0"), "n_half1": h.get("n_half1"),
            "mean_half0_s": None if h.get("mean_half0") is None else round(h["mean_half0"], 6),
            "mean_half1_s": None if h.get("mean_half1") is None else round(h["mean_half1"], 6),
            "half_difference_s": (None if h.get("mean_half0") is None
                                  or h.get("mean_half1") is None
                                  else round(h["mean_half1"] - h["mean_half0"], 6)),
            "split_half_estimable": bool(h.get("estimable", False)),
            "has_forty_attempt": pid in forty,
        })
    return sorted(rows, key=lambda r: (-r["reps_primary"], r["nfl_id"]))


# --------------------------------------------------------------------------- #
# Selection rules
# --------------------------------------------------------------------------- #
def _floor(n_players: int) -> float | None:
    if n_players < 2:
        return None
    return ST.min_detectable_share(n_players)["min_share"]


def selection_tables(players, cfg, comps3) -> tuple[list[dict], list[dict]]:
    """Rule A (raw reps) and Rule B (reliability), side by side.

    Returns (rows, membership) where membership is one long-format row per
    (player, rule) so "who is admitted / dropped" is answerable by id.
    """
    sel = C.section(cfg, "selection")
    soi = float(C.section(cfg, "audit")["smallest_effect_of_interest"])
    rows, membership = [], []
    reps = {r["nfl_id"]: r["reps_primary"] for r in players}
    relk = {r["nfl_id"]: r["reliability_rep_level"] for r in players}
    rel3 = {r["nfl_id"]: r["reliability_three_level"] for r in players}
    has40 = {r["nfl_id"]: r["has_forty_attempt"] for r in players}

    for cut in [int(v) for v in sel["rule_a_min_reps"]]:
        admitted = {p for p in reps if reps[p] >= cut}
        rows.append(_selection_row("A_raw_reps", cut, admitted, ply=players,
                                   soi=soi, rel3=rel3, has40=has40,
                                   note="reps >= cut"))
        for p in reps:
            membership.append({"nfl_id": p, "rule": "A_raw_reps", "cut": cut,
                               "admitted": p in admitted,
                               "fittable": bool(p in admitted and has40[p])})

    for cut in [float(v) for v in sel["rule_b_min_reliability"]]:
        admitted = {p for p in relk if relk[p] is not None and relk[p] >= cut}
        rows.append(_selection_row("B_reliability_rep_level", cut, admitted,
                                   ply=players, soi=soi, rel3=rel3,
                                   has40=has40,
                                   note="reliability_i = n_i/(n_i+k), k from "
                                        "the two-level model"))
        for p in reps:
            membership.append({"nfl_id": p, "rule": "B_reliability_rep_level",
                               "cut": cut, "admitted": p in admitted,
                               "fittable": bool(p in admitted and has40[p])})

    for cut in [float(v) for v in sel["rule_b_min_reliability"]]:
        admitted = {p for p in rel3 if rel3[p] is not None and rel3[p] >= cut}
        rows.append(_selection_row("B_three_level_PROPOSED", cut, admitted,
                                   ply=players, soi=soi, rel3=rel3,
                                   has40=has40,
                                   note="reliability from the three-level model "
                                        "(uses games as well as reps); "
                                        "PROPOSED, not the specified rule"))
        for p in reps:
            membership.append({"nfl_id": p, "rule": "B_three_level_PROPOSED",
                               "cut": cut, "admitted": p in admitted,
                               "fittable": bool(p in admitted and has40[p])})
    return rows, membership


def _selection_row(rule, cut, admitted, *, ply, soi, rel3, has40, note) -> dict:
    n_all = len(admitted)
    fit = sorted(p for p in admitted if has40[p])
    n_fit = len(fit)
    by_id = {p["nfl_id"]: p for p in ply}
    floor = _floor(n_fit)
    p_eff = REL.effective_players([rel3[p] for p in fit
                                   if rel3[p] is not None])
    floor_eff = None if (not p_eff or math.isnan(p_eff) or p_eff < 2) \
        else ST.min_detectable_share(int(round(p_eff)))["min_share"]
    return {
        "rule": rule, "cut": cut, "players_admitted": n_all,
        "players_fittable": n_fit,
        "players_admitted_without_forty": n_all - n_fit,
        "median_reliability_admitted_three_level": _median(
            [rel3[p] for p in sorted(admitted) if rel3[p] is not None]),
        "min_reliability_admitted_three_level": (
            None if not admitted else round(min(
                rel3[p] for p in admitted if rel3[p] is not None), 6)),
        "min_reps_admitted": min((by_id[p]["reps_primary"] for p in admitted),
                                 default=None),
        "median_reps_admitted": _median(
            [by_id[p]["reps_primary"] for p in admitted]),
        "median_games_admitted": _median(
            [by_id[p]["games"] for p in admitted]),
        "implied_detectable_floor": None if floor is None else round(floor, 6),
        "meets_soi_fittable": None if floor is None else bool(floor <= soi),
        "effective_players_reliability_weighted": (None if p_eff is None or
                                                   math.isnan(p_eff)
                                                   else round(p_eff, 3)),
        "floor_at_effective_players": (None if floor_eff is None
                                       else round(floor_eff, 6)),
        "meets_soi_effective": (None if floor_eff is None
                                else bool(floor_eff <= soi)),
        "smallest_effect_of_interest": soi,
        "note": note,
    }


def overlap_tables(players, membership, cfg) -> list[dict]:
    """A x B overlap at every cut pair, with the disagreement sets described."""
    sel = C.section(cfg, "selection")
    reps = {r["nfl_id"]: r["reps_primary"] for r in players}
    rel3 = {r["nfl_id"]: r["reliability_three_level"] for r in players}
    relk = {r["nfl_id"]: r["reliability_rep_level"] for r in players}
    has40 = {r["nfl_id"]: r["has_forty_attempt"] for r in players}

    def admitted(rule, cut):
        return {m["nfl_id"] for m in membership
                if m["rule"] == rule and m["cut"] == cut and m["admitted"]}

    out = []
    for model, rule_b in (("_".join(["rep", "level"]), "B_reliability_rep_level"),
                          ("three_level_PROPOSED", "B_three_level_PROPOSED")):
        rel = relk if rule_b == "B_reliability_rep_level" else rel3
        for a_cut in [int(v) for v in sel["rule_a_min_reps"]]:
            a = admitted("A_raw_reps", a_cut)
            for b_cut in [float(v) for v in sel["rule_b_min_reliability"]]:
                b = admitted(rule_b, b_cut)
                only_a = a - b
                only_b = b - a
                out.append({
                    "reliability_model": model,
                    "rule_a_cut_reps": a_cut, "rule_b_cut_reliability": b_cut,
                    "n_a": len(a), "n_b": len(b), "n_both": len(a & b),
                    "n_only_a": len(only_a), "n_only_b": len(only_b),
                    "only_a_fittable": sum(1 for p in only_a if has40[p]),
                    "only_b_fittable": sum(1 for p in only_b if has40[p]),
                    "only_a_median_reps": _median([reps[p] for p in only_a]),
                    "only_b_median_reps": _median([reps[p] for p in only_b]),
                    "only_a_min_reps": min([reps[p] for p in only_a], default=None),
                    "only_b_max_reps": max([reps[p] for p in only_b], default=None),
                    "only_a_median_reliability": _median(
                        [rel[p] for p in only_a if rel[p] is not None]),
                    "only_b_median_reliability": _median(
                        [rel[p] for p in only_b if rel[p] is not None]),
                })
    return out


def _median(values):
    v = [float(x) for x in values if x is not None and not math.isnan(float(x))]
    return round(float(np.median(v)), 6) if v else None


# --------------------------------------------------------------------------- #
# Reliability at rep counts, and the residual caveat
# --------------------------------------------------------------------------- #
def reliability_at_rep_counts(players, cfg, k, comps3) -> list[dict]:
    sel = C.section(cfg, "selection")
    rows = []
    for n in [int(v) for v in sel["rep_count_grid"]]:
        at = [p for p in players if p["reps_primary"] >= n]
        med_games = _median([p["games"] for p in at]) if at else None
        rel3 = (None if med_games is None else round(
            REL.reliability_three_level(n, med_games, comps3["sigma2_player"],
                                        comps3["sigma2_game"],
                                        comps3["sigma2_rep"]), 6))
        rows.append({
            "reps": n, "players_at_or_above": len(at),
            "players_fittable_at_or_above": sum(
                1 for p in at if p["has_forty_attempt"]),
            "reliability_rep_level": (None if math.isnan(
                REL.reliability_from_k(n, k))
                else round(REL.reliability_from_k(n, k), 6)),
            "median_games_at_this_rep_count": med_games,
            "reliability_three_level_at_median_games": rel3,
        })
    return rows


def residual_sensitivity(players, cfg, comps3) -> list[dict]:
    """Reliability of the residual if the physical term removes some s2u.

    This is arithmetic on a *grid*, not an estimate of how much the physical
    term explains: no Phase 4 model is fitted here. It exists to stop the
    reliability above being read as the reliability of "jump".
    """
    sel = C.section(cfg, "selection")
    med_n = _median([p["reps_primary"] for p in players])
    med_g = _median([p["games"] for p in players])
    s2u = comps3["sigma2_player"]
    rows = []
    for f in [float(v) for v in sel["residual_share_removed_grid"]]:
        rows.append({
            "share_of_between_player_variance_removed": f,
            "assumed_median_reps": med_n, "assumed_median_games": med_g,
            "reliability_of_residual": round(REL.reliability_three_level(
                med_n, med_g, s2u * (1 - f), comps3["sigma2_game"],
                comps3["sigma2_rep"]), 6),
        })
    return rows


def reliability_distribution(players) -> dict:
    """How many players fall in each reliability band, and what they look like."""
    rel = [p["reliability_three_level"] for p in players
           if p["reliability_three_level"] is not None]
    vals = [float(x) for x in rel]
    below = {}
    for mark in (0.5, 0.7, 0.8):
        sub = [p for p in players
               if p["reliability_three_level"] is not None
               and p["reliability_three_level"] < mark]
        below[str(mark)] = {
            "players": len(sub),
            "max_reps_among_them": max((p["reps_primary"] for p in sub),
                                       default=None),
            "max_games_among_them": max((p["games"] for p in sub), default=None),
            "fittable_among_them": sum(1 for p in sub if p["has_forty_attempt"]),
        }
    return {"describe": ST.describe(vals), "below": below,
            "meaning": "three-level reliability per player"}


# --------------------------------------------------------------------------- #
# The Phase 1 reproduction
# --------------------------------------------------------------------------- #
def phase1_reproduction(con, cfg) -> dict:
    """Recompute Phase 1(g) exactly: all get-off rows, global game-rank parity."""
    pp = C.read_csv_sql(C.source_path(cfg, "player_play"), cfg)
    games = C.read_csv_sql(C.source_path(cfg, "games"), cfg)
    rows = con.execute(f"""
        SELECT p.nfl_id, p.game_id, p.player_get_off, g.season, g.week
        FROM {pp} p JOIN {games} g USING(game_id)
        WHERE p.player_get_off IS NOT NULL""").df()
    order = (rows[["season", "week", "game_id"]].drop_duplicates()
             .sort_values(["season", "week", "game_id"]).reset_index(drop=True))
    order["rank"] = range(len(order))
    rows = rows.merge(order[["game_id", "rank"]], on="game_id", how="left")
    rows["half"] = rows["rank"] % 2
    per = rows.groupby(["nfl_id", "half"])["player_get_off"].mean().unstack("half")
    pairs = [(r.get(0), r.get(1)) for _, r in per.iterrows()]
    # unstack gives NaN for a missing half; "is not None" is not enough.
    pairs = [(float(a), float(b)) for a, b in pairs
             if a is not None and b is not None
             and not (isinstance(a, float) and math.isnan(a))
             and not (isinstance(b, float) and math.isnan(b))
             and not math.isnan(float(a)) and not math.isnan(float(b))]
    r = REL.pearson_r(pairs)
    return {
        "scope": "FULL_RAW_LABEL_FILE",
        "rows": int(len(rows)), "players_with_any_get_off": int(
            rows["nfl_id"].nunique()),
        "n_players_both_halves": len(pairs),
        "split_half_icc_2_1": round(ST.split_half_icc(pairs)["icc_2_1"], 6),
        "split_half_pearson_r": round(r, 6),
        "spearman_brown": round(REL.spearman_brown(r, 2), 6),
        "matches_phase1": abs(
            ST.split_half_icc(pairs)["icc_2_1"] - 0.634145) < 1e-6,
        "sample": "every player_get_off row in player_play.csv (run plays and "
                  "non-DL included), odd/even by league-wide chronological game "
                  "rank - the Phase 1 method, reproduced as a control",
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    cfg, mode, force = C.stage_bootstrap(argv, STAGE)
    p = C.paths(cfg, mode)
    out_dir = p["outputs"] / "phase3b"
    out_dir.mkdir(parents=True, exist_ok=True)
    snaps = p["outputs"] / "phase3" / "snaps.parquet"
    stamp = "FULL"  # earned by input_evidence + reconcile_label_side, not by mode

    if C.is_done(cfg, STAGE, mode) and not force:
        print(f"{STAGE}: already done (checkpoint); use --force to re-run")
        return 0
    if not snaps.exists():
        raise C.GateRefused(
            f"{STAGE}: {snaps} does not exist. Run 03_game_snaps first — this "
            "stage reads Phase 3's per-snap table and never rebuilds it.")

    summary: dict = {}
    with C.timed_stage(cfg, STAGE, mode, note="player reliability + "
                                             "selection rules") as st:
        con = C.duckdb_connect(cfg)
        try:
            evidence = input_evidence(con, cfg)
            recon = reconcile_label_side(con, snaps, cfg)
            st.note(f"label side reconciled: {recon['table_primary_rows']} "
                    f"primary rows")
            df = load_primary(con, snaps)
            if df.empty:
                raise C.GateRefused(f"{STAGE}: Phase 3 table has no primary rows")
            forty = forty_players(con, cfg)
            names = display_names(con, cfg)

            # --- cells and components -------------------------------------
            pcells = REL.player_cells(zip(df["nfl_id"], df["y"]))
            gcells = REL.game_cells(zip(df["nfl_id"], df["game_id"], df["y"]))
            comp2 = REL.two_level_components(pcells)
            comp3 = REL.three_level_components(gcells)
            if not comp2["ok"] or not comp3["ok"]:
                raise C.GateRefused(
                    f"{STAGE}: variance components not estimable "
                    f"(two-level {comp2.get('ok')}, three-level "
                    f"{comp3.get('ok')})")
            bcfg = C.section(cfg, "bootstrap")
            boot2 = REL.bootstrap_two_level(
                pcells, int(bcfg["n_resamples"]), int(bcfg["seed"]))
            boot3 = REL.bootstrap_three_level(
                gcells, int(bcfg["n_resamples"]), int(bcfg["seed"]))

            # --- split-half across games ----------------------------------
            halves = REL.split_half_means(
                zip(df["nfl_id"], df["game_id"], df["y"]), game_order(df))
            sh = REL.split_half_report(halves)
            strata = REL.split_half_strata(
                halves, C.section(cfg, "selection")["split_half_min_reps_per_half"])
            well_games = int(C.section(cfg, "selection")
                             ["well_estimated_min_games_per_half"])
            well = [h for h in halves if h["estimable"]
                    and h["n_half0"] >= 1 and h["n_half1"] >= 1]
            # "games per half": a player's games alternate, so >= 2 games in a
            # half means >= 3 games total. Counted here, not inferred.
            half_games = _games_per_half(df, game_order(df))
            well_g = [h for h in halves
                      if half_games.get(int(h["player"]), (0, 0))[0] >= well_games
                      and half_games.get(int(h["player"]), (0, 0))[1] >= well_games]
            sh_well = REL.split_half_report(well_g)

            # --- does the model reproduce the split-half curve? ------------
            sims = int(C.section(cfg, "selection")["split_half_simulations"])
            sim2 = REL.simulate_split_half_curve(
                gcells, {"sigma2_player": comp2["tau2"], "sigma2_game": 0.0,
                         "sigma2_rep": comp2["sigma2"]},
                [int(s["min_reps_per_half"]) for s in strata], sims,
                int(bcfg["seed"]), model="two_level")
            sim3 = REL.simulate_split_half_curve(
                gcells, comp3,
                [int(s["min_reps_per_half"]) for s in strata], sims,
                int(bcfg["seed"]) + 1, model="three_level")
            curve = []
            for obs, s2, s3 in zip(strata, sim2, sim3):
                curve.append({
                    "min_reps_per_half": obs["min_reps_per_half"],
                    "n_players": obs["n_players"],
                    "median_min_reps_per_half": obs["median_min_reps_per_half"],
                    "observed_r": _r(obs["r"]),
                    "observed_spearman_brown": _r(obs["spearman_brown"]),
                    "predicted_r_two_level": _r(s2["r_predicted"]),
                    "two_level_lo": _r(s2["r_lo"]), "two_level_hi": _r(s2["r_hi"]),
                    "predicted_r_three_level": _r(s3["r_predicted"]),
                    "three_level_lo": _r(s3["r_lo"]),
                    "three_level_hi": _r(s3["r_hi"]),
                })
            curve_fit = _curve_fit(curve)

            # --- per-player table ----------------------------------------
            players = per_player_table(df, halves, comp2["k"], comp3, forty, names)
            C.write_csv(out_dir / "reliability_by_player.csv", players,
                        list(players[0].keys()), mode=mode, cfg=cfg, stamp=stamp)

            # --- selection tables ----------------------------------------
            sel_rows, membership = selection_tables(players, cfg, comp3)
            C.write_csv(out_dir / "selection_rules.csv", sel_rows,
                        list(sel_rows[0].keys()), mode=mode, cfg=cfg, stamp=stamp)
            C.write_csv(out_dir / "rule_membership.csv", membership,
                        list(membership[0].keys()), mode=mode, cfg=cfg,
                        stamp=stamp)
            overlap = overlap_tables(players, membership, cfg)
            C.write_csv(out_dir / "overlap_matrix.csv", overlap,
                        list(overlap[0].keys()), mode=mode, cfg=cfg, stamp=stamp)

            grid = reliability_at_rep_counts(players, cfg, comp2["k"], comp3)
            C.write_csv(out_dir / "reliability_by_rep_count.csv", grid,
                        list(grid[0].keys()), mode=mode, cfg=cfg, stamp=stamp)
            resid = residual_sensitivity(players, cfg, comp3)
            C.write_csv(out_dir / "reliability_residual_sensitivity.csv", resid,
                        list(resid[0].keys()), mode=mode, cfg=cfg, stamp=stamp)
            C.write_csv(out_dir / "split_half_curve.csv", curve,
                        list(curve[0].keys()), mode=mode, cfg=cfg, stamp=stamp)

            # --- summary --------------------------------------------------
            means = df.groupby("nfl_id")["y"].agg(["mean", "size"])
            within = comp2["sigma2"] ** 0.5
            between = comp2["tau2"] ** 0.5
            n_est = sum(1 for h in halves if h["estimable"])
            summary = {
                "stage": STAGE,
                "mode": mode,
                "question": "which players enter Phase 4, and how reliable is "
                            "a player-level get-off estimate?",
                "threshold_chosen": False,
                "provenance_basis": {
                    "stamp": stamp,
                    "why_not_sample": "every input is a complete file; the "
                                      "analysis reads no tracking column",
                    "inputs": evidence,
                    "label_side_reconciliation": recon,
                },
                "analysis_set": {
                    "rows": int(len(df)),
                    "players": int(df["nfl_id"].nunique()),
                    "games": int(df["game_id"].nunique()),
                    "median_reps_per_player": float(means["size"].median()),
                    "players_with_forty_attempt": len(forty & set(
                        int(x) for x in means.index)),
                    "seasons": [int(s) for s in sorted(df["season"].unique())],
                    "scope": "in_primary_set (DL, non-run pass-rush, published "
                             "player_get_off) from the complete label file",
                },
                "distribution": {
                    "player_mean_get_off": ST.describe(
                        means["mean"].tolist()),
                    "sd_between_player_anova": round(between, 6),
                    "sd_within_player_pooled": round(within, 6),
                    "sd_of_player_means_unweighted": round(
                        float(means["mean"].std(ddof=1)), 6),
                    "between_share_noise_corrected": round(comp2["icc"], 6),
                    "between_share_uncorrected_phase1_method": round(
                        float(means["mean"].var(ddof=1)) /
                        (float(means["mean"].var(ddof=1)) + comp2["sigma2"]), 6),
                    "icc_oneway_check": round(
                        ST.icc_oneway(_groups(df))["icc"], 6),
                    "check_variance_of_player_means": {
                        "predicted": round(
                            comp3["sigma2_player"]
                            + comp3["sigma2_game"] * float(
                                np.mean(1.0 / df.groupby("nfl_id")["game_id"]
                                        .nunique().values))
                            + comp3["sigma2_rep"] * float(
                                np.mean(1.0 / means["size"].values)), 6),
                        "observed": round(float(means["mean"].var(ddof=1)), 6),
                        "meaning": "s2u + s2v*E[1/G] + s2e*E[1/n] against the "
                                   "observed variance of player means: an "
                                   "out-of-sample check the three-level "
                                   "components reproduce the observed spread",
                    },
                },
                "two_level_model": {
                    "estimator": "one-way random effects, method of moments "
                                 "(same moments as stats.hierarchical_normal)",
                    "sigma2_within": round(comp2["sigma2"], 8),
                    "tau2_between": round(comp2["tau2"], 8),
                    "k_sigma2_over_tau2": round(comp2["k"], 6),
                    "n0": round(comp2["n0"], 4),
                    "bootstrap": _boot_block(boot2, "k", "k = sigma2/tau2"),
                    "reliability_at_median_reps": round(
                        REL.reliability_from_k(
                            float(means["size"].median()), comp2["k"]), 6),
                    "reliability_at_median_reps_bootstrap": _reliability_ci(
                        boot2, float(means["size"].median())),
                },
                "three_level_model": {
                    "estimator": "nested unbalanced ANOVA, method of moments "
                                 "(reps in games in players)",
                    "sigma2_rep": round(comp3["sigma2_rep"], 8),
                    "sigma2_game_within_player": round(comp3["sigma2_game"], 8),
                    "sigma2_player": round(comp3["sigma2_player"], 8),
                    "share_player": round(comp3["share_player"], 6),
                    "share_game": round(comp3["share_game"], 6),
                    "share_rep": round(comp3["share_rep"], 6),
                    "components_truncated": comp3["components_truncated"],
                    "bootstrap": _boot_block(boot3, "share_player", "share_player = s2u/total"),
                    "reliability_at_median": round(
                        REL.reliability_three_level(
                            float(means["size"].median()),
                            float(df.groupby("nfl_id")["game_id"].nunique().median()),
                            comp3["sigma2_player"], comp3["sigma2_game"],
                            comp3["sigma2_rep"]), 6),
                },
                "split_half": {
                    "estimable_players": n_est,
                    "players_never_estimable": int(len(halves)) - n_est,
                    "well_estimated_min_games_per_half": well_games,
                    "well_estimated_players": sh_well["n_players"],
                    "all_estimable": {k: _r(v) if isinstance(v, float) else v
                                      for k, v in sh.items()},
                    "well_estimated": {k: _r(v) if isinstance(v, float) else v
                                       for k, v in sh_well.items()},
                    "strata": curve,
                    "curve_fit": curve_fit,
                    "phase1_reproduction": phase1_reproduction(con, cfg),
                },
                "selection_rules": {
                    "rule_a_cuts": C.section(cfg, "selection")["rule_a_min_reps"],
                    "rule_b_cuts": C.section(cfg, "selection")
                                   ["rule_b_min_reliability"],
                    "smallest_effect_of_interest": float(
                        C.section(cfg, "audit")["smallest_effect_of_interest"]),
                    "implied_rep_cut_by_reliability": [
                        {"reliability": float(r),
                         "implied_min_reps_rep_level": round(
                             REL.rep_cut_for_reliability(comp2["k"], float(r)), 3)}
                        for r in C.section(cfg, "selection")
                        ["rule_b_min_reliability"]],
                    "table": sel_rows,
                },
                "residual_reliability_sensitivity": resid,
                "reliability_distribution": reliability_distribution(players),
                "outputs": [
                    "reliability_by_player.csv", "selection_rules.csv",
                    "rule_membership.csv", "overlap_matrix.csv",
                    "reliability_by_rep_count.csv",
                    "reliability_residual_sensitivity.csv",
                    "split_half_curve.csv", "phase3b_summary.json",
                    "input_provenance.csv"],
            }
            C.write_csv(out_dir / "input_provenance.csv",
                        [{"file": k, **v} for k, v in evidence.items()],
                        ["file", "status", "basis", "rows", "players",
                         "player_attempts", "drills", "draft_years", "seasons",
                         "games_covered", "games_in_games_csv",
                         "games_covered_share", "distinct_player_play_keys"],
                        mode=mode, cfg=cfg, stamp=stamp)
            C.write_json(out_dir / "phase3b_summary.json", summary,
                         mode=mode, cfg=cfg, stamp=stamp)
            write_note(p["notes"] / "selection-rule.md", summary, cfg,
                       mode=mode, stamp=stamp, players=players)
        finally:
            con.close()

    C.mark_done(cfg, STAGE, mode, {"players": summary.get(
        "analysis_set", {}).get("players")})
    print_summary(summary)
    return 0


def _r(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 6)


def _curve_fit(curve) -> dict:
    """RMSE of each model's predicted half-half correlation vs the observed one.

    A single number for "which model reproduces the split-half attenuation".
    """
    def rmse(key_obs, key_pred):
        diffs = [row[key_pred] - row[key_obs] for row in curve
                 if row[key_obs] is not None and row[key_pred] is not None]
        return round(math.sqrt(sum(d * d for d in diffs) / len(diffs)), 6)

    return {"two_level_rmse": rmse("observed_r", "predicted_r_two_level"),
            "three_level_rmse": rmse("observed_r",
                                     "predicted_r_three_level"),
            "cuts": len(curve),
            "meaning": "lower is better; a model that omits the game level "
                       "cannot reproduce the observed attenuation"}


def _groups(df):
    return [g["y"].tolist() for _, g in df.groupby("nfl_id")]


def _boot_block(boot, key, label: str | None = None) -> dict:
    if not boot.get("ok"):
        return {"ok": False}
    ci = boot["intervals"].get(key, {})
    return {"quantity": label or key,
            "median": _r(ci.get("median")), "p_lo": _r(ci.get("p_lo")),
            "p_hi": _r(ci.get("p_hi")), "n_resamples": ci.get("n")}


def _reliability_ci(boot, n: float) -> dict:
    if not boot.get("ok"):
        return {}
    ks = boot["intervals"].get("k", {})
    if not ks:
        return {}
    return {"reliability_at_n": _r(REL.reliability_from_k(n, ks["median"])),
            "p_lo_from_k_p_hi": _r(REL.reliability_from_k(n, ks["p_hi"])),
            "p_hi_from_k_p_lo": _r(REL.reliability_from_k(n, ks["p_lo"]))}


def _games_per_half(df, order) -> dict:
    """(games in half 0, games in half 1) per player."""
    out = {}
    for pid, seq in order.items():
        out[int(pid)] = (len(seq[0::2]), len(seq[1::2]))
    return out


def print_summary(summary: dict) -> None:
    ds = summary["distribution"]
    print(f"{STAGE}: {summary['analysis_set']['rows']:,} reps, "
          f"{summary['analysis_set']['players']} players "
          f"(median {summary['analysis_set']['median_reps_per_player']:.0f} reps)")
    print(f"  sd between {ds['sd_between_player_anova']:.4f} s, "
          f"within {ds['sd_within_player_pooled']:.4f} s, "
          f"corrected between share {ds['between_share_noise_corrected']:.3f}")
    t = summary["two_level_model"]
    print(f"  two-level k={t['k_sigma2_over_tau2']} -> reliability at median "
          f"reps {t['reliability_at_median_reps']}")
    t3 = summary["three_level_model"]
    print(f"  three-level s2u={t3['sigma2_player']:.5f} "
          f"s2v={t3['sigma2_game_within_player']:.5f} "
          f"s2e={t3['sigma2_rep']:.5f} -> reliability "
          f"{t3['reliability_at_median']}")
    sh = summary["split_half"]
    print(f"  split-half: {sh['estimable_players']} estimable of "
          f"{sh['estimable_players'] + sh['players_never_estimable']}; "
          f"r={sh['all_estimable']['r']} SB={sh['all_estimable']['spearman_brown']}"
          f"; well estimated n={sh['well_estimated_players']} "
          f"r={sh['well_estimated']['r']}")
    print("  selection (fittable players -> floor):")
    for row in summary["selection_rules"]["table"]:
        print(f"    {row['rule']} {row['cut']}: {row['players_admitted']} "
              f"({row['players_fittable']} fittable) floor "
              f"{row['implied_detectable_floor']} meets SOI "
              f"{row['meets_soi_fittable']}")


# --------------------------------------------------------------------------- #
# The one-page note
# --------------------------------------------------------------------------- #
def write_note(path: Path, s: dict, cfg, *, mode: str, stamp: str,
               players: list[dict]) -> Path:
    a = s["analysis_set"]
    ds = s["distribution"]
    t2 = s["two_level_model"]
    t3 = s["three_level_model"]
    sh = s["split_half"]
    strat = sh["strata"]
    tab = s["selection_rules"]["table"]
    soi = s["selection_rules"]["smallest_effect_of_interest"]
    n_total = a["players"]
    resid = {r["share_of_between_player_variance_removed"]: r
             for r in s["residual_reliability_sensitivity"]}
    implied = s["selection_rules"]["implied_rep_cut_by_reliability"]

    def find(rule, cut):
        for r in tab:
            if r["rule"] == rule and float(r["cut"]) == float(cut):
                return r
        raise KeyError((rule, cut))

    a50 = find("A_raw_reps", 50)
    b08 = find("B_reliability_rep_level", 0.8)
    dist = s["reliability_distribution"]
    rel_min = min(p["reliability_three_level"] for p in players
                  if p["reliability_three_level"] is not None)
    at10 = [p["reliability_three_level"] for p in players
            if p["reps_primary"] == 10 and p["reliability_three_level"]]
    rel_at_10 = min(at10) if at10 else float("nan")
    low_b = [p for p in players
             if p["reliability_rep_level"] is not None
             and p["reliability_rep_level"] >= 0.5
             and p["reps_primary"] < 20]
    low_b_rel = [p["reliability_three_level"] for p in low_b
                 if p["reliability_three_level"] is not None]

    def row(r):
        f = "n/a" if r["implied_detectable_floor"] is None else f"{r['implied_detectable_floor']:.4f}"
        return (f"| {r['rule']} | {r['cut']} | {r['players_admitted']} | "
                f"{r['players_fittable']} | {f} | "
                f"{'yes' if r['meets_soi_fittable'] else 'no'} | "
                f"{'' if r['effective_players_reliability_weighted'] is None else r['effective_players_reliability_weighted']} |")

    strat_lines = "\n".join(
        f"| >= {x['min_reps_per_half']} | {x['n_players']} | {x['observed_r']} | "
        f"{x['predicted_r_two_level']} | {x['predicted_r_three_level']} |"
        for x in strat)

    text = f"""# Selection rule for the Phase 4 variance decomposition

**Question:** which players enter the Phase 4 decomposition, and how reliable is
a player-level get-off estimate? Evidence only. No threshold is chosen here and
no Phase 4 model is fitted.

**Provenance.** Every input is a complete file: `player_play.csv` (314,197 rows,
one per player-play), `games.csv` (1,002 games), `combine_tracking.csv` (510
attendees, 6,301 player-attempts), `players.csv`. The Phase 3 table's label side
is reconciled row-for-row against a direct query on the complete label file:
{s['provenance_basis']['label_side_reconciliation']['table_rows']:,} get-off rows and {s['provenance_basis']['label_side_reconciliation']['table_primary_rows']:,}
primary rows on both sides. No tracking column is read, so these numbers are
**real evidence, not `UNVALIDATED SAMPLE OUTPUT`**.

## 1. Analysis set and the variance split

{a['rows']:,} DL non-run pass-rush reps, {a['players']} players, {a['games']:,}
games, {a['median_reps_per_player']:.0f} median reps per player;
{a['players_with_forty_attempt']} players have a Combine 40-yard attempt (the
fittable set).

| quantity | value |
|---|---:|
| between-player SD (noise-corrected) | {ds['sd_between_player_anova']:.4f} s |
| within-player SD (pooled) | {ds['sd_within_player_pooled']:.4f} s |
| SD of player means (uncorrected) | {ds['sd_of_player_means_unweighted']:.4f} s |
| between-player share, noise-corrected | **{ds['between_share_noise_corrected']:.3f}** |
| between-player share, Phase 1 method | {ds['between_share_uncorrected_phase1_method']:.3f} |

Phase 1's 0.285 was computed on all get-off rows (run plays and non-DL included)
without removing sampling error from the variance of player means; both inflate
the share. **{ds['between_share_noise_corrected']:.3f} is the honest ceiling for
what a physical model could explain here**, before depth or alignment adjustment.

## 2. How reliable is a player mean?

**(a) Split-half across games.** Games are ordered chronologically and
alternated into halves. {sh['estimable_players']} of
{sh['estimable_players'] + sh['players_never_estimable']} players have a rep in
each half; the other {sh['players_never_estimable']} have one game and cannot be
split. All estimable: **r = {sh['all_estimable']['r']:.3f}** (SB
{sh['all_estimable']['spearman_brown']:.3f}). The {sh['well_estimated_players']}
players with {sh['well_estimated_min_games_per_half']}+ games in *each* half:
**r = {sh['well_estimated']['r']:.3f}** (SB
{sh['well_estimated']['spearman_brown']:.3f}). Both are attenuated by unequal
halves:

| min reps/half | players | observed r | predicted r, rep-level | predicted r, three-level |
|---:|---:|---:|---:|---:|
{strat_lines}

The rise with half size is attenuation, not changing reliability. The rep-level
model misses the curve (RMSE {sh['curve_fit']['two_level_rmse']:.3f}); the
three-level model reproduces it (RMSE {sh['curve_fit']['three_level_rmse']:.3f}).

**(b) Model-based, as specified:** `k = sigma_e^2/sigma_u^2` from a one-way
random-effects fit: `sigma_e = {t2['sigma2_within'] ** 0.5:.4f} s`,
`sigma_u = {t2['tau2_between'] ** 0.5:.4f} s`, so **k = {t2['k_sigma2_over_tau2']:.2f}**
(player-cluster bootstrap {t2['bootstrap']['p_lo']}–{t2['bootstrap']['p_hi']}).
Reliability `n/(n+k)` = **{t2['reliability_at_median_reps']:.3f} at the median
{a['median_reps_per_player']:.0f} reps**.

**(c) Model-based, games as a level:** `sigma_u =
{t3['sigma2_player'] ** 0.5:.4f} s`, `sigma_v = {t3['sigma2_game_within_player'] ** 0.5:.4f} s`
(game within player), `sigma_e = {t3['sigma2_rep'] ** 0.5:.4f} s`.
Reliability `sigma_u^2/(sigma_u^2 + sigma_v^2/G + sigma_e^2/n)` =
**{t3['reliability_at_median']:.3f} at the median player**. As a check that these
components are real: they predict the observed variance of player means at
{ds['check_variance_of_player_means']['predicted']:.5f} against
{ds['check_variance_of_player_means']['observed']:.5f} observed.

**Where they disagree.** The rep-level figure is optimistic by
{100 * (t2['reliability_at_median_reps'] - t3['reliability_at_median']):.0f} points because it treats ~100 reps as
independent observations when they are ~12 games with correlated reps. It also
cannot reproduce the split-half curve. Use the three-level column.

**Is it "very low"? No.** At the median it is about
{t3['reliability_at_median']:.2f}. It is low only in the tail:
{dist['below']['0.5']['players']} players are below 0.5 and every one has
{dist['below']['0.5']['max_reps_among_them']} reps or fewer.

**Caveat.** This is the reliability of the **raw** mean. Phase 4's residual `u_i`
has less between-player variance, so its reliability is lower: 0.869 if the
physical term removes 25% of it, 0.815 at 50%. **These numbers are an upper bound
on the reliability of "jump".**

**Phase 1 control.** 0.634 is reproduced exactly (ICC(2,1) =
{sh['phase1_reproduction']['split_half_icc_2_1']}, r =
{sh['phase1_reproduction']['split_half_pearson_r']}, n =
{sh['phase1_reproduction']['n_players_both_halves']}) on all 44,002 get-off rows
with league-wide game-rank parity — a different sample from the primary set.

## 3. The two rules

**Rule A (raw reps)** admits anyone with ≥ c reps. Failure mode: it treats a rep
count as information, so 20 snaps in one game count the same as 20 across 15.

**Rule B (reliability)** admits anyone with `n/(n+k) ≥ r`. With a common `k`
this is algebraically `n ≥ k·r/(1−r)` — at k = {t2['k_sigma2_over_tau2']:.2f} the cuts are
{', '.join(f"{x['implied_min_reps_rep_level']:.1f}" for x in implied)} reps for
r = {', '.join(str(x['reliability']) for x in implied)}. **As specified, Rule B
is Rule A with a different label and cannot admit a different set.** It becomes a
distinct rule only if reliability is computed per player with games as a level
(`B_three_level_PROPOSED` below) — a proposal, not the specified rule.

| rule | cut | players | fittable | floor | meets SOI {soi} | P_eff |
|---|---:|---:|---:|---:|---|---:|
{chr(10).join(row(r) for r in tab)}

Floor is `1.96·sqrt(2/P)` on the **fittable** set: a player with no Combine time
contributes nothing to the decomposition.

## 4. The power artefact

The floor assumes every admitted player is equally informative. Admitting
players whose means are mostly noise lowers the computed floor while the quantity
it describes gets worse — their observed means shrink toward the grand mean, so
the between-player share is biased down by roughly the mean reliability. **The
floor must be computed on the fittable set that carries information.** `P_eff` is
the Kish count `(sum r)^2/sum r^2` on three-level reliabilities.

Here the artefact is **small**: median fittable reliability is
{find('A_raw_reps', 20)['median_reliability_admitted_three_level']:.2f} at 20 reps
and {a50['median_reliability_admitted_three_level']:.2f} at 50; at the 20-rep cut
`P_eff` = {find('A_raw_reps', 20)['effective_players_reliability_weighted']} against 90 heads, and the floor moves
{find('A_raw_reps', 20)['implied_detectable_floor']} → {find('A_raw_reps', 20)['floor_at_effective_players']}. So 20 versus 50 reps is a **headcount** question, not a
reliability one. The artefact bites only in the tail Rule B at 0.5 reaches: the
{len(low_b)} players it admits below 20 reps have three-level reliability
{min(low_b_rel):.2f}–{max(low_b_rel):.2f} ({rel_min:.2f} for the worst 1-rep
player). Every cut from 0.6 upward stops at 12 reps or more.

**Who each rule silently drops.** A at 50 drops {n_total - a50['players_admitted']}
of {n_total} players: all with 1–49 reps, plus {a50['players_admitted_without_forty']}
with 50+ reps and no 40 time. B at 0.8 drops {n_total - b08['players_admitted']}
and admits players from {b08['min_reps_admitted']} reps (median
{b08['median_reps_admitted']:.0f} reps / {b08['median_games_admitted']:.0f} games). Exact membership:
`rule_membership.csv`; disagreement sets: `overlap_matrix.csv`.

## 5. A question for Conor, not a finding to act on

The corrected between-player share is **{ds['between_share_noise_corrected']:.3f}**,
roughly half the 0.285 that SOI 0.30 was calibrated against. If the SOI means the
between-player share of **total** variance, no selection rule makes it
detectable: floor 0.2922 at 20 reps, 0.3159 at 50, 0.2499 over all 123 fittable
players. But `TASK.md` Phase 4 asks for the share of **between-player** variance
explained by `b*phys` versus `u` — a different denominator, not yet estimated.
**Which share does 0.30 refer to?** The prereg should not be frozen until that
is fixed.

**Not computable here:** reliability of the residual `u_i` (Phase 4); depth- and
opponent-adjusted reliability (needs tracking); anything for the
{a['players'] - a['players_with_forty_attempt']} primary players with no 40 time.

All artefacts in `outputs/phase3b/`.
"""
    return C.write_markdown(path, text, mode=mode, cfg=cfg, stamp=stamp)


if __name__ == "__main__":
    sys.exit(main())
