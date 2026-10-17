#!/usr/bin/env python3
"""security_audit.py — Phase-1 **board/security** audit extension (BDB27 stakes task).

Additive to ``01_audit.py`` (the existing items a–i are untouched): this module implements the
board/security evidence items from the binding spec, and the **merged go/no-go + headline gate**:

  (c) board snapshot timing — ``snapshot_note`` values, snapshots per player, each snapshot's date vs
      the actual Combine testing window and vs **each player's own testing day** (a pre-Combine
      snapshot counts only when strictly before the player's own testing);
  (d) ``nfl_id`` match rates boards ↔ Combine ↔ game, duplicates and position mismatches;
  (e) prospect pool — board prospects with Combine / game tracking, and whether undrafted appear;
  (h) matched receivers per security tier and per participation pattern; route snaps per player;
  (l) power calculation — smallest detectable security effect at the matched sample sizes.

Game-side frame-level items are **PENDING FULL RUN** (the tracking here is a non-representative
prefix). Everything degrades gracefully: if the board is unavailable the board items emit an
explicit PENDING note and never raise, so the audit still exits cleanly.

This module writes CSVs (``provenance`` first) under ``outputs/audit/security/`` and a machine summary
``outputs/audit/security/audit_security.json``. No magic numbers (config-driven).
"""
from __future__ import annotations

import csv
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

import duckdb

import common
import security_features as sf

PENDING = "PENDING FULL RUN"


# --------------------------------------------------------------------------- #
# emit (house convention: provenance first)
# --------------------------------------------------------------------------- #
def _clean(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    return v


def emit(out_dir: Path, name: str, rows: list[dict], prov: str, log=None) -> Path:
    rows = [dict(r) for r in (rows or [])] or [{}]
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    fields = ["provenance"] + [f for f in fields if f != "provenance"]
    norm = []
    for r in rows:
        d = {"provenance": prov}
        for k, v in r.items():
            if k != "provenance":
                d[k] = _clean(v)
        norm.append(d)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    common.write_csv(path, norm, fieldnames=fields)
    if log:
        log.info("wrote %s (%d rows)", path.name, len(norm))
    return path


# --------------------------------------------------------------------------- #
# input resolution (real board / synthetic fixture)
# --------------------------------------------------------------------------- #
def resolve_inputs(ctx: dict) -> dict:
    """Resolve board rows, per-player testing days and speed-drill sets for the audit.

    Real mode → the config board file + the (complete) Combine tracking in ``ctx['tables']``.
    Synthetic mode → the committed ``fixtures/synthetic/security/<regime>/`` fixture (never a finding).
    Returns ``{board_rows, testing_days, drills_by_player, source, board_meta}``.
    """
    cfg, con = ctx["cfg"], ctx["con"]
    synthetic = bool(ctx.get("synthetic"))
    regime = ctx.get("synthetic_regime", "binding")
    sec_regime = "null" if regime == "null" else "planted"
    info: dict[str, Any] = {"source": None, "board_meta": {}}

    if synthetic:
        root = common.resolve(cfg, "fixtures", "synthetic/security") / sec_regime
        board = root / "board.csv"
        if not board.exists():
            info["source"] = f"SYNTHETIC MISSING ({board})"
            return {**info, "board_rows": [], "testing_days": {}, "drills_by_player": {}}
        info["source"] = f"synthetic fixture ({sec_regime})"
        board_rows = sf.load_board_rows(con, cfg, board)
        testing_days = _read_testing_csv(root / "combine_testing.csv")
        drills = _read_drills_csv(root / "combine_drills.csv")
        return {**info, "board_rows": board_rows, "testing_days": testing_days, "drills_by_player": drills}

    board = sf.board_path(cfg)
    if not board.exists():
        info["source"] = f"UNAVAILABLE ({board})"
        return {**info, "board_rows": [], "testing_days": {}, "drills_by_player": {}}
    info["source"] = str(board)
    info["board_meta"] = sf.verify_board(cfg, board, ctx.get("logger"))
    board_rows = sf.load_board_rows(con, cfg, board)
    ct = ctx["tables"].get("combine_tracking")
    testing_days = sf.combine_testing_days(con, ct) if ct else {}
    drills = sf.combine_drills_by_player(con, ct, cfg) if ct else {}
    return {**info, "board_rows": board_rows, "testing_days": testing_days, "drills_by_player": drills}


def _read_testing_csv(path: Path) -> dict[int, date]:
    if not path.exists():
        return {}
    out: dict[int, date] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d, m, y = str(r["testing_day"]).split("/")
            out[int(r["nfl_id"])] = date(int(y), int(m), int(d))
    return out


def _read_drills_csv(path: Path) -> dict[int, set[str]]:
    if not path.exists():
        return {}
    out: dict[int, set[str]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(int(r["nfl_id"]), set()).add(str(r["drill_type"]))
    return out


# --------------------------------------------------------------------------- #
# (c) snapshot timing vs Combine dates / each player's testing day
# --------------------------------------------------------------------------- #
def item_c(ctx: dict, inp: dict) -> list[dict]:
    cfg = ctx["cfg"]
    b = cfg["external"]["board"]
    rows = inp["board_rows"]
    testing_days = inp["testing_days"]
    out_dir = ctx["sec_dir"]

    # testing window per class (from Combine tracking / fixture)
    if inp["source"].startswith("UNAVAILABLE") and not testing_days:
        emit(out_dir, "c_snapshot_timing.csv",
             [dict(item="c", check="snapshot_timing", status=PENDING,
                   note="board unavailable -> snapshot-vs-Combine timing not computable")], ctx["prov"], ctx["logger"])
        return []

    # per (draft_year, snapshot_note): date, n_rows, n strictly-before-player-testing
    acc: dict[tuple[int, str], dict[str, Any]] = {}
    for r in rows:
        key = (int(r["draft_year"]), str(r["snapshot_note"]))
        a = acc.setdefault(key, {"n": 0, "dates": set(), "before": 0, "after": 0, "unknown": 0})
        a["n"] += 1
        a["dates"].add(r["snapshot_date"])
        nid = r.get("nfl_id")
        td = testing_days.get(int(nid)) if nid is not None else None
        if td is None:
            a["unknown"] += 1
        elif r["snapshot_date"] < td:
            a["before"] += 1
        else:
            a["after"] += 1
    out = []
    for (year, note), a in sorted(acc.items()):
        ds = sorted(a["dates"])
        out.append(dict(item="c", draft_year=year, snapshot_note=note, n_rows=a["n"],
                        snapshot_dates="|".join(str(d) for d in ds),
                        n_strictly_before_player_testing=a["before"],
                        n_on_or_after_player_testing=a["after"], n_no_testing_day=a["unknown"],
                        is_pre_label=(note in b["snapshot_pre"]),
                        used_as_exposure=(note in b["snapshot_pre"])))
    # testing windows
    emit(out_dir, "c_snapshot_timing.csv", out, ctx["prov"], ctx["logger"])
    # player-level testing day table (Combine tracking; sample of all board players)
    prows = []
    for p in sf.board_players(rows):
        td = testing_days.get(p["nfl_id"])
        prows.append(dict(item="c", nfl_id=p["nfl_id"], draft_year=p["draft_year"], position=p["position"],
                          testing_day=(str(td) if td else ""), has_testing_day=(td is not None)))
    emit(out_dir, "c_player_testing_days.csv", prows, ctx["prov"], ctx["logger"])
    return out


def item_c_windows(ctx: dict, inp: dict) -> list[dict]:
    """Per-class Combine testing window + the pre-Combine snapshot dates (evidence item c)."""
    cfg = ctx["cfg"]
    b = cfg["external"]["board"]
    ct = ctx["tables"].get("combine_tracking")
    out = []
    if ct:
        for y, (a, d) in sorted(sf.combine_testing_window(ctx["con"], ct).items()):
            out.append(dict(item="c", draft_year=y, testing_window_start=str(a), testing_window_end=str(d),
                            source="combine_tracking"))
    prec = {}
    for r in inp["board_rows"]:
        if str(r["snapshot_note"]) in b["snapshot_pre"]:
            prec[int(r["draft_year"])] = min(prec.get(int(r["draft_year"]), r["snapshot_date"]), r["snapshot_date"])
    for y, d in sorted(prec.items()):
        out.append(dict(item="c", draft_year=y, earliest_pre_snapshot_date=str(d),
                        snapshot_primary_date=str(_primary_date(inp["board_rows"], y, b["snapshot_primary"]))))
    emit(ctx["sec_dir"], "c_testing_windows.csv", out, ctx["prov"], ctx["logger"])
    return out


def _primary_date(rows: list[dict], year: int, note: str):
    ds = [r["snapshot_date"] for r in rows if int(r["draft_year"]) == year and str(r["snapshot_note"]) == note]
    return min(ds) if ds else ""


# --------------------------------------------------------------------------- #
# (d) nfl_id match rates, duplicates, position mismatches
# --------------------------------------------------------------------------- #
def item_d(ctx: dict, inp: dict) -> list[dict]:
    cfg, con = ctx["cfg"], ctx["con"]
    rows = inp["board_rows"]
    out_dir = ctx["sec_dir"]
    if not rows:
        emit(out_dir, "d_match_rates.csv",
             [dict(item="d", check="match_rates", status=PENDING, note="board unavailable")], ctx["prov"], ctx["logger"])
        return []

    board_ids = {int(r["nfl_id"]) for r in rows if r.get("nfl_id") is not None}
    pos_of = {}
    for p in sf.board_players(rows):
        pos_of[p["nfl_id"]] = p["position"]
    wrte_ids = {i for i in board_ids if sf.position_group(cfg, pos_of.get(i)) in ("WR", "TE")}

    def _ids(table, col="nfl_id"):
        try:
            return {int(x[0]) for x in con.execute(f"SELECT DISTINCT {col} FROM {_pq(ctx, table)}").fetchall()}
        except Exception:  # noqa: BLE001 - a missing table degrades to empty, never raises
            return set()

    cr = _ids("combine_results")
    pp = _ids("player_play")
    gt = _ids("game_tracking") if ctx.get("tables", {}).get("game_tracking") is not None else set()

    def _rate(ids, universe):
        hit = len(ids & universe)
        return hit, len(ids), (round(hit / len(ids), 4) if ids else None)

    rows_out = []
    for label, universe in (("all_board_ids", board_ids), ("board_WR_TE", wrte_ids)):
        h1, n1, r1 = _rate(universe, cr)
        h2, n2, r2 = _rate(universe, pp)
        h3, n3, r3 = _rate(universe, gt)
        rows_out.append(dict(item="d", universe=label, n_board_ids=n1,
                             matched_combine=h1, match_rate_combine=r1,
                             matched_player_play=h2, match_rate_player_play=r2,
                             matched_game_tracking_sample=h3, match_rate_game_tracking=r3,
                             game_tracking_status=(PENDING if ctx["game_side_status"] != "AVAILABLE" else "AVAILABLE")))
    # duplicates + blank ids
    blank = sf.board_blank_id_count(rows)
    dup_cells = {}
    for r in rows:
        if r.get("nfl_id") is None:
            continue
        dup_cells[(int(r["nfl_id"]), str(r["snapshot_note"]))] = dup_cells.get((int(r["nfl_id"]), str(r["snapshot_note"])), 0) + 1
    n_dup = sum(1 for v in dup_cells.values() if v > 1)
    # players with conflicting board positions
    pos_sets: dict[int, set[str]] = {}
    for r in rows:
        if r.get("nfl_id") is not None:
            pos_sets.setdefault(int(r["nfl_id"]), set()).add(str(r["position"]))
    n_pos_conflict_board = sum(1 for v in pos_sets.values() if len(v) > 1)
    # position mismatches board vs combine (present in both)
    mism = []
    if cr:
        comb = {int(n): str(p) for n, p in con.execute(
            f"SELECT DISTINCT nfl_id, combine_position FROM {_pq(ctx, 'combine_results')}").fetchall()}
        for i in sorted(board_ids & cr):
            bg = sf.position_group(cfg, pos_of.get(i))
            cg = str(comb.get(i))
            if bg != cg:
                mism.append(dict(item="d", check="position_mismatch", nfl_id=i,
                                 board_position=pos_of.get(i), board_group=bg, combine_position=cg))
    rows_out.append(dict(item="d", check="duplicates", n_blank_nfl_id_rows=blank,
                         n_duplicated_player_snapshot_cells=n_dup,
                         n_players_conflicting_board_positions=n_pos_conflict_board,
                         n_position_mismatch_board_vs_combine=len(mism)))
    emit(out_dir, "d_match_rates.csv", rows_out, ctx["prov"], ctx["logger"])
    if mism:
        emit(out_dir, "d_position_mismatches.csv", mism, ctx["prov"], ctx["logger"])
    return rows_out + mism


def _pq(ctx: dict, table: str):
    t = ctx.get("tables", {})
    if table in t:
        p = t[table]
        if isinstance(p, (list, tuple)):
            import game_features as gf
            return gf.pq_list(p)
        return common.pq(p)
    # Table not in the audit's materialised set (e.g. players.csv): read the raw CSV lazily.
    csvp = common.csv_path(ctx["cfg"], table)
    return f"read_csv_auto('{csvp}', header=true, nullstr=['NA', ''])"


# --------------------------------------------------------------------------- #
# (e) prospect pool
# --------------------------------------------------------------------------- #
def item_e(ctx: dict, inp: dict) -> list[dict]:
    cfg, con = ctx["cfg"], ctx["con"]
    rows = inp["board_rows"]
    out_dir = ctx["sec_dir"]
    if not rows:
        emit(out_dir, "e_prospect_pool.csv",
             [dict(item="e", check="prospect_pool", status=PENDING, note="board unavailable")], ctx["prov"], ctx["logger"])
        return []
    board_ids = {int(r["nfl_id"]) for r in rows if r.get("nfl_id") is not None}

    def _ids(table):
        try:
            return {int(x[0]) for x in con.execute(f"SELECT DISTINCT nfl_id FROM {_pq(ctx, table)}").fetchall()}
        except Exception:  # noqa: BLE001
            return set()

    cr = _ids("combine_results")
    ct = _ids("combine_tracking")
    pp = _ids("player_play")
    # undrafted = board players present in the game pool but NOT drafted... use players.csv draft slot
    drafted = set()
    all_players = set()
    try:
        for n, pick in con.execute(f"SELECT nfl_id, draft_overall_pick FROM {_pq(ctx, 'players')}").fetchall():
            all_players.add(int(n))
            if pick not in (None, "", "NA"):
                drafted.add(int(n))
    except Exception:  # noqa: BLE001
        pass
    undrafted_board = {i for i in board_ids if i not in drafted}
    out = [dict(item="e", metric="board_prospects_total", value=len(board_ids)),
           dict(item="e", metric="board_with_combine_tracking", value=len(board_ids & ct)),
           dict(item="e", metric="board_with_combine_results", value=len(board_ids & cr)),
           dict(item="e", metric="board_with_game_player_play", value=len(board_ids & pp),
                status=(PENDING if ctx["game_side_status"] != "AVAILABLE" else "AVAILABLE")),
           dict(item="e", metric="board_undrafted_count", value=len(undrafted_board)),
           dict(item="e", metric="board_undrafted_with_combine", value=len(undrafted_board & ct)),
           dict(item="e", metric="board_undrafted_with_game", value=len(undrafted_board & pp)),
           dict(item="e", metric="board_with_players_csv", value=len(board_ids & all_players))]
    emit(out_dir, "e_prospect_pool.csv", out, ctx["prov"], ctx["logger"])
    return out


# --------------------------------------------------------------------------- #
# (h) matched receivers per security tier & participation pattern
# --------------------------------------------------------------------------- #
def _tiers(log_edp: list[float], cfg: dict) -> list[tuple[float, float]]:
    qs = [float(q) for q in cfg["security"]["tier_quantiles"]]
    cuts = [float(np.quantile(log_edp, q)) for q in qs]
    return [(-math.inf, cuts[0]), (cuts[0], cuts[-1]), (cuts[-1], math.inf)]


def _tier_label(log_edp: float, tiers: list[tuple[float, float]]) -> str:
    labels = ["low_edp_secure", "mid_edp", "high_edp_at_risk"]
    for lab, (lo, hi) in zip(labels, tiers):
        if lo <= log_edp <= hi:
            return lab
    return labels[-1]


def item_h(ctx: dict, inp: dict) -> tuple[list[dict], dict]:
    cfg, con = ctx["cfg"], ctx["con"]
    rows = inp["board_rows"]
    out_dir = ctx["sec_dir"]
    if not rows:
        emit(out_dir, "h_security_groups.csv",
             [dict(item="h", check="security_groups", status=PENDING, note="board unavailable")], ctx["prov"], ctx["logger"])
        return [], {"evaluated": False}
    exp = sf.build_exposures(rows, inp["testing_days"], inp["drills_by_player"], cfg)
    # route snaps per player from the (complete) player_play route_ran field
    snaps: dict[int, int] = {}
    try:
        for n, c in con.execute(f"SELECT nfl_id, count(*) FROM {_pq(ctx, 'player_play')} "
                                f"WHERE route_ran IS NOT NULL GROUP BY 1").fetchall():
            snaps[int(n)] = int(c)
    except Exception:  # noqa: BLE001
        snaps = {}
    has_primary = [e for e in exp if e["has_primary"]]
    pos_groups = sorted({e["position_group"] for e in has_primary if e["position_group"] in ("WR", "TE")})
    out = []
    gate_counts: dict[str, int] = {}
    for pg in pos_groups:
        pg_rows = [e for e in has_primary if e["position_group"] == pg]
        log_edps = [e["log_edp_pre"] for e in pg_rows]
        tiers = _tiers(log_edps, cfg) if len(log_edps) >= 3 else []
        for e in pg_rows:
            e["_tier"] = _tier_label(e["log_edp_pre"], tiers) if tiers else "all"
            e["_snaps"] = snaps.get(e["nfl_id"], 0)
        for tier in (["low_edp_secure", "mid_edp", "high_edp_at_risk"] if tiers else ["all"]):
            grp = [e for e in pg_rows if e["_tier"] == tier]
            matched_game = [e for e in grp if e["_snaps"] > 0]
            gate_counts[f"{pg}:{tier}"] = len(matched_game)
            out.append(dict(item="h", dimension="tier", position_group=pg, tier=tier,
                            n=len(grp), n_with_combine=sum(1 for e in grp if e["n_drills"] > 0),
                            n_matched_game=len(matched_game),
                            median_route_snaps=(float(np.median([e["_snaps"] for e in matched_game]))
                                                if matched_game else None),
                            status=(PENDING if ctx["game_side_status"] != "AVAILABLE" else "AVAILABLE")))
        pats = sorted({e["participation_pattern"] for e in pg_rows})
        for pat in pats:
            grp = [e for e in pg_rows if e["participation_pattern"] == pat]
            matched_game = [e for e in grp if e["_snaps"] > 0]
            out.append(dict(item="h", dimension="participation", position_group=pg,
                            participation_pattern=pat, n=len(grp),
                            n_matched_game=len(matched_game),
                            median_route_snaps=(float(np.median([e["_snaps"] for e in matched_game]))
                                                if matched_game else None),
                            status=(PENDING if ctx["game_side_status"] != "AVAILABLE" else "AVAILABLE")))
    emit(out_dir, "h_security_groups.csv", out, ctx["prov"], ctx["logger"])
    return out, {"evaluated": True, "gate_counts": gate_counts, "n_exposure": len(has_primary)}


# --------------------------------------------------------------------------- #
# (l) power calculation
# --------------------------------------------------------------------------- #
def _z(p: float) -> float:
    # inverse normal CDF via erf inverse (no scipy dependency needed here)
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


def item_l(ctx: dict, inp: dict, h_info: dict) -> list[dict]:
    cfg = ctx["cfg"]
    p = cfg["security"]["power"]
    alpha, power = float(p["alpha"]), float(p["power"])
    z = _z(1 - alpha / 2) + _z(power)
    # matched-receiver counts (player_play preview) per position, when available
    counts = {}
    if h_info.get("evaluated"):
        for key, c in h_info["gate_counts"].items():
            pg = key.split(":")[0]
            counts[pg] = counts.get(pg, 0) + c
    out = []
    for pg in (["WR", "TE"] if counts else ["WR", "TE"]):
        n = counts.get(pg, 0)
        se_std = (z / math.sqrt(n)) if n > 0 else None  # MDE in SD units (std predictor)
        mde_log = (z * float(p["residual_sd_log_speed"]) / math.sqrt(n)) if n > 0 else None
        out.append(dict(item="l", position_group=pg, n_matched=n, alpha=alpha, power=power,
                        min_detectable_effect_std=se_std,
                        min_detectable_effect_log_speed=mde_log,
                        min_detectable_speed_ratio=((math.exp(mde_log) - 1) if mde_log else None),
                        exposure_sd_log_edp=p["exposure_sd_log_edp"],
                        note=("power from the player_play matched preview; frame-level counts = "
                              + PENDING if ctx["game_side_status"] != "AVAILABLE" else "FULL")))
    emit(ctx["sec_dir"], "l_power.csv", out, ctx["prov"], ctx["logger"])
    return out


# --------------------------------------------------------------------------- #
# merged gate (full-mode only)
# --------------------------------------------------------------------------- #
def merged_gate(ctx: dict, inp: dict, results: dict, h_info: dict, l_rows: list[dict]) -> dict:
    """The merged Phase-1 go/no-go + headline gate. **Evaluated only in ``--mode full``.**

    Checks (spec "Headline gate"): external data permitted; each security group meets the prereg
    minimum matched-receiver count; Combine ceiling reliability passes (ICC from audit g); and
    Analysis A shows the market responded to speed (read from ``outputs/security/analysis_a.json``).
    In sample mode the gate is *not evaluated* and never blocks (the pipeline must run end-to-end).

    **Semantics.** ``headline_pass`` (== ``passed``) reports whether the *security* question may lead
    the paper; a ``headline_pass`` failure is **not** a pipeline stop — per TASK.md the analysis falls
    back to the ceiling/tail framing with security reported as exploratory. ``blocking`` is reserved
    for the one hard stop the spec lists here (external board data not permitted).
    """
    cfg = ctx["cfg"]
    mode = ctx["mode"]
    b = cfg["external"]["board"]
    gate: dict[str, Any] = {"evaluated": False, "blocking": False, "checks": {}, "mode": mode}
    if mode != "full":
        gate["note"] = "sample mode: merged headline gate not evaluated (pipeline runs end-to-end)"
        return gate
    gate["evaluated"] = True
    checks: dict[str, Any] = {}
    checks["external_permitted"] = bool(b.get("permitted"))
    checks["board_present"] = bool(inp["board_rows"])
    # per-tier matched counts vs minimum
    min_grp = int(cfg["security"]["min_matched_receivers_per_tier"])
    counts = h_info.get("gate_counts", {}) if h_info.get("evaluated") else {}
    tier_ok = {}
    seen_pg = set()
    for key, c in counts.items():
        pg = key.split(":")[0]
        seen_pg.add(pg)
        tier_ok[key] = (c >= min_grp)
    checks["min_matched_receivers_per_tier"] = {"minimum": min_grp, "groups": tier_ok,
                                                "ok": (all(tier_ok.values()) if tier_ok else None)}
    # ceiling reliability: any retained/estimated ICC above the threshold
    g = results.get("g") or []
    est = [r for r in g if str(r.get("status", "")).startswith("ESTIMATED")
           and float(r.get("icc") or 0) >= float(cfg["audit"]["min_icc"])]
    checks["combine_reliability_ok"] = bool(est)
    # analysis A (read the security stage's machine output, if it has run)
    a_path = common.resolve(cfg, "outputs", cfg["security"]["out_subdir"]) / cfg["security"]["analysis_a_file"]
    if a_path.exists():
        try:
            a = json.loads(a_path.read_text(encoding="utf-8"))
            checks["analysis_a_ok"] = bool(a.get("gate_pass"))
            checks["analysis_a"] = {k: a.get(k) for k in ("speed_coef", "interaction_coef", "n", "note")}
        except (ValueError, OSError):
            checks["analysis_a_ok"] = None
    else:
        checks["analysis_a_ok"] = None
        checks["analysis_a"] = {"note": "analysis_a.json not found; run src/06_security.py"}
    required = ["external_permitted", "board_present", "combine_reliability_ok"]
    failed = [k for k in required if checks.get(k) is False]
    if checks["min_matched_receivers_per_tier"]["ok"] is False:
        failed.append("min_matched_receivers_per_tier")
    if checks.get("analysis_a_ok") is False:
        failed.append("analysis_a_ok")
    gate["checks"] = checks
    gate["failed"] = failed
    # Headline-gate outcome (TASK.md "Headline gate"): whether the SECURITY question may lead the
    # paper. A failure does NOT stop the pipeline — the spec says "fall back to the ceiling and tail
    # design ... and report security as exploratory". So `headline_pass` is *informational*.
    gate["headline_pass"] = not failed
    gate["passed"] = gate["headline_pass"]
    # The ONLY hard stop is external board data that is not permitted (TASK.md "Stop and write to
    # blockers.md if ... external board data is not permitted"); earlier audit logic is unchanged.
    gate["blocking"] = bool(checks.get("external_permitted") is False)
    if gate["blocking"]:
        gate["block_reason"] = "external board data not permitted"
    return gate


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(ctx: dict, audit_results: dict) -> dict:
    """Run every board/security audit item (never raises) and write the summary JSON."""
    log = ctx["logger"]
    ctx["sec_dir"] = ctx["audit_dir"] / "security"
    ctx["sec_dir"].mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    gate: dict[str, Any] = {"evaluated": False, "note": "board/security audit not run"}
    try:
        inp = resolve_inputs(ctx)
        ctx["board_source"] = inp["source"]
        results["c"] = item_c(ctx, inp)
        item_c_windows(ctx, inp)
        results["d"] = item_d(ctx, inp)
        results["e"] = item_e(ctx, inp)
        h_rows, h_info = item_h(ctx, inp)
        results["h"] = h_rows
        results["l"] = item_l(ctx, inp, h_info)
        gate = merged_gate(ctx, inp, audit_results, h_info, results["l"])
        summary = dict(provenance=ctx["prov"], mode=ctx["mode"], board_source=inp["source"],
                       n_board_rows=len(inp["board_rows"]),
                       n_board_players=len(sf.board_players(inp["board_rows"])),
                       game_side_status=ctx["game_side_status"],
                       gate=gate,
                       items={k: len(v) for k, v in results.items()})
    except Exception as exc:  # noqa: BLE001 - the audit must never die on the extension
        log.error("security audit extension failed: %s: %s", type(exc).__name__, exc)
        summary = dict(provenance=ctx["prov"], mode=ctx["mode"], error=f"{type(exc).__name__}: {exc}",
                       gate=gate)
    (ctx["sec_dir"] / "audit_security.json").write_text(json.dumps(summary, indent=2, default=str),
                                                        encoding="utf-8")
    return summary
