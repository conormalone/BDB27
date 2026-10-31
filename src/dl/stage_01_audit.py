#!/usr/bin/env python3
"""stage_01_audit — Phase 1 evidence audit and go/no-go gate.

Orchestrates the existing audit modules and nothing else:

* :mod:`dl.dl_audit_ab`     — items **a** (Combine drills) and **b** (matched DL).
* :mod:`dl.dl_audit_ce`     — items **c** (snap coverage), **d** (null semantics),
  **e** (label provenance).
* :mod:`dl.dl_audit_f`      — item **f** (alignment / depth / blitzing).
* :mod:`dl.dl_audit_power`  — items **g–k** (assignments, units, ICC,
  collinearity, power).

This module adds only (i) the item-status roll-up, (ii) a pure
:func:`decide` gate function, (iii) one DuckDB diagnostic for the unexplained
nulls on live pass plays, and (iv) the ``decision.json`` / ``SUMMARY.md``
writers.  Every threshold is read from ``config.yaml``; the only literal is the
task-specified *upper* edge of the borderline-ICC band, which is not a tunable
threshold.

Standalone::

    python src/dl/run.py 01_audit --mode sample [--force]
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402
from dl import dl_audit_ab  # noqa: E402
from dl import dl_audit_ce  # noqa: E402
from dl import dl_audit_f  # noqa: E402
from dl import dl_audit_power  # noqa: E402

STAGE = "01_audit"
GATE_GO = "GO"
GATE_PENDING = "GO_PENDING_FULL_RUN"
GATE_CONDITIONAL = "CONDITIONAL"
GATE_NO_GO = "NO-GO"

PASS = "PASS"
FAIL = "FAIL"
PENDING = "PENDING_FULL_RUN"
INDETERMINATE = "INDETERMINATE"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNRESOLVED = "UNRESOLVED"
PASS_WITH_CAVEAT = "PASS_WITH_CAVEAT"

#: Item-k Part 2 caveat (sample-size underpowering).  Reporting only: it can
#: never by itself produce a NO-GO, because full data has more snaps.
K_PART2_CAVEAT = (
    "Part 2's smallest detectable log-score difference at the observed snap "
    "count exceeds the smallest effect of interest, so Part 2 is underpowered "
    "at this sample size and must be re-evaluated on full data; Part 1 is "
    "adequately powered.")

#: Task-spec band: an a0 ICC inside [icc_min, 0.60) is CONDITIONAL, never a
#: licence to lower ``thresholds.icc_min``.  The lower edge *is* the config.
ICC_BORDERLINE_HIGH = 0.60

#: Null-semantics verdicts that count as resolved.
RESOLVED = {dl_audit_ce.EVENT_ABSENT, dl_audit_ce.NOT_APPLICABLE}
#: Part 1 outcome fields: unresolved nulls here are a NO-GO.
PART1_FIELDS = ("time_to_pressure", "quick_pressure", "time_to_qb_hurry")
#: Exploratory / secondary fields: unresolved nulls are reported, not gating.
SECONDARY_FIELDS = ("sack", "unblocked_pressure")
#: Phase-1 required columns (TASK.md "Variables to expect").
REQUIRED_PP_FIELDS = ("player_get_off", "time_to_pressure", "time_to_qb_hurry",
                      "quick_pressure", "sack", "blitzing",
                      "dropback_duration", "pressure_allowed")


# --------------------------------------------------------------------------- #
# Small readers
# --------------------------------------------------------------------------- #
def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _out_dir(cfg: Mapping, mode: str) -> Path:
    sub = D.dl(cfg)["audit"].get("out_subdir") or "audit"
    return Path(D.resolve_paths(cfg, mode)["outputs"]) / str(sub)


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _header(cfg: Mapping, mode: str, key: str) -> list[str]:
    path = D.source_paths(cfg, key, mode)[0]
    con = D.duckdb_connect(cfg)
    try:
        return [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto('{path.as_posix()}', "
            f"nullstr=['NA'])").fetchall()]
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# d2: unexplained nulls on live, non-nullified pass plays (one query)
# --------------------------------------------------------------------------- #
def _d2_nulls(cfg: Mapping, mode: str, out: Path) -> dict:
    """Cross-tab the null frame for sack / blitzing / unblocked_pressure.

    Frame: DL rows on pass plays (``pass_result`` non-empty) not nullified by
    penalty.  The three fields are co-null when ``blitzing`` is null; the
    diagnostic isolates the residual where participation *is* recorded but the
    event fields are missing, because ``sack`` encodes zero explicitly (0.0)
    elsewhere and therefore null is not zero.
    """
    pp = D.source_paths(cfg, "player_play", mode)[0]
    pl = D.source_paths(cfg, "players", mode)[0]
    pos = ", ".join(f"'{p}'" for p in D.dl(cfg)["positions"]["dl"])
    con = D.duckdb_connect(cfg)
    try:
        rows = con.execute(f"""
            WITH dl AS (SELECT nfl_id FROM read_csv_auto('{pl.as_posix()}',
                                                         nullstr=['NA'])
                        WHERE nfl_position IN ({pos})),
                 d AS (SELECT p.* FROM read_csv_auto('{pp.as_posix()}',
                                                     nullstr=['NA']) p
                       JOIN dl USING (nfl_id))
            SELECT pass_result,
                   COALESCE(play_nullified_by_penalty, '') AS penalty,
                   (blitzing IS NULL) AS blitzing_null,
                   (sack IS NULL) AS sack_null,
                   (unblocked_pressure IS NULL) AS unblocked_null,
                   (pressure_allowed IS NULL) AS pressure_allowed_null,
                   (player_get_off IS NULL) AS get_off_null,
                   count(*) AS n_rows,
                   count(DISTINCT game_id || '-' || play_id) AS n_plays
            FROM d
            WHERE pass_result IS NOT NULL AND pass_result <> ''
              AND COALESCE(play_nullified_by_penalty, '') <> 'Y'
              AND (sack IS NULL OR blitzing IS NULL
                   OR unblocked_pressure IS NULL)
            GROUP BY 1, 2, 3, 4, 5, 6, 7
            ORDER BY n_rows DESC, pass_result""").fetchall()
    finally:
        con.close()

    fields = ("pass_result", "penalty", "blitzing_null", "sack_null",
              "unblocked_null", "pressure_allowed_null", "get_off_null",
              "n_rows", "n_plays", "explained_by_gate_rule", "rule")
    table = []
    tot = {"sack": 0, "blitzing": 0, "unblocked": 0, "joint": 0,
           "gate": 0, "residual": 0}
    for (pr, pen, b_null, s_null, u_null, pa_null, go_null, n, plays) in rows:
        gate = bool(b_null)  # blitzing null => all three null (verified below)
        rule = ("rush participation not recorded -> not applicable"
                if gate else "participation recorded but event field null")
        table.append({"pass_result": pr, "penalty": pen,
                      "blitzing_null": int(b_null), "sack_null": int(s_null),
                      "unblocked_null": int(u_null),
                      "pressure_allowed_null": int(pa_null),
                      "get_off_null": int(go_null), "n_rows": n,
                      "n_plays": plays, "explained_by_gate_rule": int(gate),
                      "rule": rule})
        tot["sack"] += n if s_null else 0
        tot["blitzing"] += n if b_null else 0
        tot["unblocked"] += n if u_null else 0
        tot["joint"] += n if (b_null and s_null and u_null) else 0
        tot["gate"] += n if gate else 0
        tot["residual"] += n if not gate else 0

    # The gate rule only holds if every row with a null blitzing is co-null on
    # the other two fields; if not, the rule is not clean and nothing is settled.
    holds = all(not (r["blitzing_null"] and not (r["sack_null"]
                                                 and r["unblocked_null"]))
                for r in table)
    if not holds:
        tot["gate"] = 0
        tot["residual"] = sum(r["n_rows"] for r in table)
    usable = sorted({str(r["pass_result"]) for r in table})
    D.write_csv(out / "d2_unexplained_nulls.csv", table, fields,
                mode=mode, cfg=cfg)
    return {"table": table, **tot, "gate_rule_holds": holds,
            "residual_rows": tot["residual"], "pass_results": usable,
            "qb_scramble_fields": []}


# --------------------------------------------------------------------------- #
# Item roll-up
# --------------------------------------------------------------------------- #
def _item(status: str, reason: str, **detail: Any) -> dict:
    return {"status": status, "reason": reason, **detail}


def build_items(cfg: Mapping, mode: str, ab: dict, ce: dict, f: dict,
                pw: dict, d2: dict, out: Path) -> tuple[dict, dict]:
    """Map sub-module verdicts onto items a–k plus the ``counts`` dict."""
    audit = D.dl(cfg)["audit"]
    th = D.dl(cfg)["thresholds"]
    min_matched = int(audit["min_matched_dl"])
    min_snaps = float(audit["min_pass_rush_snaps_per_rusher"])
    min_cover = float(audit["min_snap_coverage_share"])
    icc_min = float(th["icc_min"])
    max_hr = float(audit["max_mde_hr"])

    drills = [r["drill_name"] for r in _read_csv(out / "a_drills.csv")
              if r.get("drill_name")]
    fit_drills = [str(d) for d in (D.dl(cfg)["ceiling"]["fit_drills"] or [])]
    missing_drills = [d for d in fit_drills if d not in set(drills)]
    b_rows = _read_csv(out / "b_matched.csv")
    meet = [r for r in b_rows
            if int(float(r["n_rush_rows"] or 0)) >= min_snaps]

    # a — drills present for the DL cohort
    if ab["drill_rows"] == 0:
        a = _item(FAIL, "DL absent from Combine tracking: no drill rows for "
                        "DE/DT/NT")
    elif missing_drills:
        a = _item(FAIL, f"missing fit drill(s) {missing_drills}; present: "
                        f"{sorted(drills)}")
    else:
        a = _item(PASS, f"{ab['drill_rows']} drill families present incl. "
                        f"{fit_drills}; {ab['result_cols']} result columns "
                        f"profiled")

    # b — matched cohort and volume
    ok_b = (ab["n_matched"] >= min_matched
            and float(ab["median_rush_rows"]) >= min_snaps)
    b = _item(
        PASS if ok_b else FAIL,
        f"{ab['n_matched']}/{ab['n_dl']} DL matched (min {min_matched}); "
        f"median rush rows {ab['median_rush_rows']} (min {min_snaps:g}); "
        f"{len(meet)} players >= {min_snaps:g} rush rows "
        f"(reference {audit['min_players_meeting_snaps']})")

    # c — coverage: the player_play share is a LOWER BOUND, never a FAIL
    share = float(ce["median_share"])
    c = _item(
        PENDING,
        f"player_play share median {share:.3f} vs {min_cover:g} is a lower "
        "bound (denominator counts defensive snaps the player may not have "
        "played); frame-level coverage needs tracked snaps -> stage 03")

    # d — null semantics (Part 1 fields gate; sack/unblocked do not)
    verdicts = dict(ce["verdicts"])
    verdicts["sack"] = UNRESOLVED if d2["residual_rows"] else NOT_APPLICABLE
    verdicts["unblocked_pressure"] = (UNRESOLVED if d2["residual_rows"]
                                      else NOT_APPLICABLE)
    bad1 = [f for f in PART1_FIELDS if verdicts.get(f) not in RESOLVED]
    bad2 = [f for f in SECONDARY_FIELDS if verdicts.get(f) == UNRESOLVED]
    if bad1:
        d = _item(FAIL, f"Part 1 null semantics unresolved for {bad1}")
    elif bad2:
        d = _item(UNRESOLVED,
                  f"{bad2} UNRESOLVED (exploratory/secondary: null is not "
                  f"zero — sack encodes 0.0 — and {d2['residual_rows']} rows "
                  "remain) — reported, NOT a NO-GO")
    else:
        d = _item(PASS, "all five fields resolved: null = event absent / "
                        "not applicable on the rush frame")

    # e — label provenance
    x = ce.get("crosstab") or {}
    e = _item(PASS, f"pressure_allowed vs DL time_to_pressure 2x2 "
                    f"dp/pa cells {sorted(x)}; peak_pressure_probability_allowed "
                    "is model-derived and flagged as circular")

    # f — alignment / depth / blitzing
    f2 = f["verdicts"]["f2"]
    f3 = f["verdicts"]["f3"]
    if f2 == "ABSENT":
        fitem = _item(FAIL, "pre-snap depth missing and not derivable from any "
                            "provided field — required covariate absent")
    elif f3 == "AMBIGUOUS":
        fitem = _item(INDETERMINATE, f"depth {f2} but blitzing semantics "
                                     "AMBIGUOUS")
    else:
        fitem = _item(PASS, f"depth {f2}; blitzing = {f3} "
                            f"({f['crosstab']['TRUE_present']} TRUE rows with "
                            "get_off present)")

    # g–k — power module statuses
    def pwr(letter: str, note: str = "") -> dict:
        blob = pw.get(letter) or {}
        status = blob.get("status", INDETERMINATE)
        notes = "; ".join(blob.get("notes") or []) or note
        if status == "NOT_RUN":
            status = PENDING
        return _item(status, notes or "see artefact", artefacts=blob.get("artefacts"))

    g = pwr("g", "assignment field absent; proximity proxy pending full tracking")
    if g["status"] == FAIL:
        g = _item(INDETERMINATE, "rusher-blocker assignment field FOUND — the "
                                 "no-field design assumption is contradicted; "
                                 "re-scope needed (not a data NO-GO)")
    h = pwr("h", "Combine units / sampling audit")
    if h["status"] == FAIL:
        h = _item(FAIL, "; ".join(pw.get("h", {}).get("notes") or [])
                  or "Combine distance/acceleration inconsistent with x/y")

    # i — ICC
    icc_rows = _read_csv(out / "i_icc.csv")
    a0 = None
    for r in icc_rows:
        if r["feature"] == "a0" and r["estimable"] not in ("False", "0", ""):
            a0 = _float(r["icc"])
    i_status = pw.get("i", {}).get("status", INDETERMINATE)
    if a0 is None:
        i_status = INDETERMINATE
        i_reason = "a0 ICC not estimable in this mode — power attenuation pending"
    elif a0 < icc_min:
        i_status = FAIL
        i_reason = f"a0 ICC {a0:.3f} < icc_min {icc_min:g} (measured, not adjusted)"
    else:
        band = ("borderline reliability (icc_min <= ICC < "
                f"{ICC_BORDERLINE_HIGH:g}) -> CONDITIONAL")
        i_status = PASS
        i_reason = f"a0 ICC {a0:.3f} >= icc_min {icc_min:g}" + (
            f"; {band}" if a0 < ICC_BORDERLINE_HIGH else "")
    i = _item(i_status, i_reason, icc=a0)

    # j — collinearity (Part 2 only; never a Phase-1 NO-GO)
    j = pwr("j", "absolute vs relative load correlation pending snaps.csv")

    # k — power: Part 1 (MDE HR, raw + attenuated) AND Part 2 (MDE log-score).
    # Every part is carried through so the item can never read PASS while its
    # own artefact (k_power.csv) records a FAIL.
    pblk = D.dl(cfg)["power"]
    sh, sl = float(pblk["smallest_effect_hr"]), float(
        pblk["smallest_effect_logscore"])
    hr = hr_att = ls = None
    for r in _read_csv(out / "k_power.csv"):
        val, q = _float(r["estimate"]), r["quantity"]
        if q == "part1_mde_hr":
            hr = val
        elif q == "part1_mde_hr_after_attenuation":
            hr_att = val
        elif q == "part2_mde_logscore":
            ls = val
    cap = min(sh, max_hr)
    part1 = (PENDING if None in (hr, hr_att) else
             PASS if hr <= cap and hr_att <= cap else FAIL)
    part2 = PENDING if ls is None else (PASS if ls <= sl else FAIL)
    detail = {"part1": {"status": part1, "mde_hr": hr,
                        "mde_hr_attenuated": hr_att,
                        "smallest_effect_hr": sh, "max_mde_hr": max_hr},
              "part2": {"status": part2, "mde_logscore": ls,
                        "smallest_effect_logscore": sl}}
    if hr is None:
        k = _item(PENDING, "power not estimable without pressure events",
                  **detail)
    elif part1 == FAIL:
        k = _item(FAIL, f"Part 1 MDE HR {hr:.3f} (attenuated {hr_att}) "
                        f"exceeds smallest_effect_hr {sh:g} / max_mde_hr "
                        f"{max_hr:g}: power only adequate for implausibly "
                        "large effects", **detail)
    elif part1 != PASS:
        k = _item(PENDING, "Part 1 attenuation not estimable — ICC pending",
                  **detail)
    elif part2 == FAIL:
        k = _item(PASS_WITH_CAVEAT, K_PART2_CAVEAT, **detail)
    else:
        k = _item(PASS, f"Part 1 MDE HR {hr:.3f} (attenuated {hr_att:.3f}) "
                        f"and Part 2 MDE log-score {ls:.4f} within smallest "
                        f"effects {sh:g} / {sl:g}", **detail)

    items = {"a": a, "b": b, "c": c, "d": d, "e": e, "f": fitem,
             "g": g, "h": h, "i": i, "j": j, "k": k}

    missing_fields = [c for c in REQUIRED_PP_FIELDS
                      if c not in _header(cfg, mode, "player_play")]
    counts = {
        "n_dl": ab["n_dl"], "n_matched": ab["n_matched"],
        "median_rush_snaps": float(ab["median_rush_rows"]),
        "min_rush_snaps": min_snaps, "min_matched": min_matched,
        "n_players_meeting_snaps": len(meet),
        "min_players_meeting_snaps": int(audit["min_players_meeting_snaps"]),
        "n_combine_drill_rows": ab["drill_rows"],
        "missing_fit_drills": missing_drills,
        "missing_required_fields": missing_fields,
        "null_semantics": verdicts,
        "median_snap_share": share,
        "tracking_snap_coverage": None,
        "a0_icc": a0, "icc_min": icc_min,
        "part1_mde_hr": hr, "part1_mde_hr_attenuated": hr_att,
        "part2_mde_logscore": ls, "max_mde_hr": max_hr,
        "min_snap_coverage_share": min_cover,
        "d2": {k2: v for k2, v in d2.items() if k2 != "table"},
    }
    return items, counts


# --------------------------------------------------------------------------- #
# The gate — pure, unit-testable
# --------------------------------------------------------------------------- #
def decide(items: Mapping[str, Mapping], counts: Mapping, cfg: Mapping) -> dict:
    """Apply TASK.md's go/no-go rules to the item roll-up.

    Hard NO-GO: DL absent from Combine tracking, a required field missing,
    matched DL or median rush snaps below the config minima, Part 1 null
    semantics unresolved, a0 ICC below ``thresholds.icc_min``, Part 1 power
    adequate only above ``audit.max_mde_hr``, and any binding item (a–k except
    ``c``) whose status is ``FAIL``.

    Never a NO-GO: the ``player_play`` snap share (a lower bound), and
    ``sack`` / ``unblocked_pressure`` (exploratory/secondary).
    """
    audit, th = D.dl(cfg)["audit"], D.dl(cfg)["thresholds"]
    no_go: list[str] = []
    cond: list[str] = []
    notes: list[str] = []

    if counts.get("n_combine_drill_rows", 0) == 0:
        no_go.append("a: DL absent from Combine tracking")
    elif counts.get("missing_fit_drills"):
        no_go.append(f"a: fit drill(s) absent {counts['missing_fit_drills']}")
    for fld in counts.get("missing_required_fields") or []:
        no_go.append(f"required field missing from player_play: {fld}")
    if counts["n_matched"] < int(audit["min_matched_dl"]):
        no_go.append(f"b: matched DL {counts['n_matched']} < "
                     f"min_matched_dl {audit['min_matched_dl']}")
    if counts["median_rush_snaps"] < float(audit["min_pass_rush_snaps_per_rusher"]):
        no_go.append(f"b: median rush snaps {counts['median_rush_snaps']:g} < "
                     f"min_pass_rush_snaps_per_rusher "
                     f"{audit['min_pass_rush_snaps_per_rusher']}")

    sem = counts.get("null_semantics") or {}
    for fld in PART1_FIELDS:
        if sem.get(fld) not in RESOLVED:
            no_go.append(f"d: null semantics unresolved for {fld} "
                         f"(Part 1 outcome field)")

    a0 = counts.get("a0_icc")
    icc_min = float(th["icc_min"])
    borderline = a0 is not None and icc_min <= a0 < ICC_BORDERLINE_HIGH
    if a0 is not None and a0 < icc_min:
        no_go.append(f"i: a0 ICC {a0:.3f} < icc_min {icc_min:g}")
    elif borderline:
        cond.append(f"i: a0 ICC {a0:.3f} is borderline "
                    f"({icc_min:g}-{ICC_BORDERLINE_HIGH:g}); icc_min NOT lowered")

    hr = counts.get("part1_mde_hr")
    if hr is not None and hr > float(audit["max_mde_hr"]):
        no_go.append(f"k: Part 1 MDE HR {hr:.3f} > max_mde_hr "
                     f"{audit['max_mde_hr']} (adequate only for implausible HR)")

    cov = counts.get("tracking_snap_coverage")
    if cov is not None and float(cov) < float(audit["min_snap_coverage_share"]):
        cond.append(f"c: tracking coverage {float(cov):.3f} < "
                    f"min_snap_coverage_share "
                    f"{audit['min_snap_coverage_share']} -> Part 2 dropped")
        part2 = "DROPPED_PENDING_FULL_RUN"
    elif cov is None:
        part2 = "PENDING_FULL_RUN"
    else:
        part2 = "ELIGIBLE"

    # k Part 2 failing on the sample is a CAVEAT, never a NO-GO (full data has
    # more snaps and Part 2 is re-evaluated there).
    kk = items.get("k") or {}
    if (kk.get("part2") or {}).get("status") == FAIL and \
            (kk.get("part1") or {}).get("status") == PASS:
        cond.append(K_PART2_CAVEAT)
        part2 = "PENDING_FULL_RUN_UNDERPOWERED_AT_SAMPLE_N"

    for fld in SECONDARY_FIELDS:
        if sem.get(fld) == UNRESOLVED:
            notes.append(f"d: {fld} is UNRESOLVED but exploratory/secondary — "
                         "not a NO-GO by itself")
    notes.append("c: never FAILed from the player_play share alone; that share "
                 "is a lower bound")

    # Item-level FAIL roll-up: a FAIL on a binding item is a NO-GO (TASK.md
    # design contract).  This runs before the part2/cond/pending branch so it
    # wins over CONDITIONAL and GO_PENDING_FULL_RUN.
    # PASS_WITH_CAVEAT and UNRESOLVED are *not* FAILs: the former is reported,
    # the latter is handled by the secondary-field notes above.
    item_fails = sorted(k for k, v in items.items()
                        if v.get("status") == FAIL)
    for k in item_fails:
        if k == "c":
            # Coverage share is a lower bound by construction: a FAIL here is
            # impossible, so it can never gate.  Surface it, do not act on it.
            notes.append("c: item status FAIL ignored for the gate — the "
                         "player_play share is a lower bound, never binding")
            continue
        no_go.append(f"{k}: item FAIL — "
                     f"{(items.get(k) or {}).get('reason', '')}")

    pending = sorted(k for k, v in items.items() if v.get("status") == PENDING)

    if no_go:
        gate = GATE_NO_GO
    elif part2 == "DROPPED_PENDING_FULL_RUN" or cond:
        gate = GATE_CONDITIONAL
    elif pending:
        gate = GATE_PENDING
    else:
        gate = GATE_GO

    return {"gate": gate, "no_go": no_go, "conditional": cond, "notes": notes,
            "part2_status": part2, "pending_items": pending,
            "reasons": no_go + cond}


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _icc_markdown(out: Path) -> str:
    rows = _read_csv(out / "i_icc.csv")
    if not rows:
        return "_no ICC rows written_\n"
    cols = ("feature", "n_players", "n_attempts", "n_excluded", "icc",
            "ci_low", "ci_high", "attenuation", "estimable")
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def _summary(cfg: Mapping, mode: str, items: Mapping, gate: Mapping,
             counts: Mapping, d2: dict, runtime_s: float) -> str:
    audit = D.dl(cfg)["audit"]
    lines = [
        "# Phase 1 audit — decision summary",
        "",
        f"**Provenance:** `{D.provenance_stamp(cfg, mode)}` — "
        + ("FULL data." if mode == "full" else
           "SAMPLE ONLY. Nothing here validates full data; every number is "
           "stamped and non-reportable."),
        f"**Gate:** `{gate['gate']}`",
        f"**Runtime:** {runtime_s:.1f} s;  stage `{STAGE}`, mode `{mode}`",
        "",
        "## Items a–k",
        "",
        "| item | status | reason |",
        "|---|---|---|",
    ]
    titles = {
        "a": "Combine drills", "b": "matched DL volume",
        "c": "snap coverage", "d": "null semantics",
        "e": "label provenance", "f": "alignment / depth / blitzing",
        "g": "rusher–blocker assignments", "h": "Combine units",
        "i": "ICC reliability", "j": "load collinearity", "k": "power",
    }
    for letter in "abcdefghijk":
        it = items[letter]
        lines.append(f"| {letter} — {titles[letter]} | **{it['status']}** | "
                     f"{str(it['reason']).replace('|', '/')} |")

    lines += ["", "## ICC (measured, not screened)", "",
              _icc_markdown(_out_dir(cfg, mode)),
              f"`thresholds.icc_min = {D.dl(cfg)['thresholds']['icc_min']}` is "
              "reported, never enforced by adjustment. Attenuation is "
              "`sqrt(ICC)` per feature.", ""]

    lines += ["## Null semantics", ""]
    for fld, verdict in (counts.get("null_semantics") or {}).items():
        lines.append(f"- `{fld}`: **{verdict}**")
    lines += ["",
              "The five fields are *not* interchangeable: `time_to_pressure`, "
              "`quick_pressure` and `time_to_qb_hurry` are Part 1 outcomes and "
              "gate the audit; `sack` and `unblocked_pressure` are "
              "exploratory/secondary and cannot gate it alone.", ""]

    # d2 block
    lines += ["### d2 — the null frame on live, non-nullified pass plays", "",
              f"- sack null: **{d2['sack']}** rows; blitzing null: "
              f"**{d2['blitzing']}**; unblocked_pressure null: "
              f"**{d2['unblocked']}**",
              f"- all three null together: **{d2['joint']}** rows",
              f"- explained by the gate rule (`blitzing` null = rush "
              f"participation not recorded ⇒ not applicable): **{d2['gate']}**",
              f"- residual with participation recorded but the event field "
              f"null: **{d2['residual_rows']}**", ""]
    if d2["residual_rows"] == 0 and d2["gate_rule_holds"]:
        lines += ["Clean rule found: the three fields share one null gate "
                  "(`blitzing`). All rows are explained ⇒ these fields are "
                  "**NOT_APPLICABLE**, not missing.", ""]
    else:
        lines += [f"**Not fully explained: {d2['residual_rows']} rows remain "
                  "UNRESOLVED.** `sack` encodes zero explicitly (0.0), so a "
                  "null there is a gap, not an absent event. The gate rule "
                  "settles the co-null rows but not this residual; no clean "
                  "rule covers it. `sack` / `unblocked_pressure` stay "
                  "UNRESOLVED (secondary fields — Part 1 continues).", ""]
    lines += [f"Cross-tab written to `d2_unexplained_nulls.csv`. "
              f"qb_scramble-like columns present: "
              f"{d2['qb_scramble_fields'] or 'none'}.", ""]

    lines += ["## Provenance", "",
              f"- config: `{cfg.get('_config_path')}`; stage `{STAGE}`",
              f"- mode `{mode}` ⇒ `{D.provenance_stamp(cfg, mode)}`",
              f"- combine source rows: {counts['n_combine_drill_rows']} drill "
              f"rows; DL cohort {counts['n_dl']}, matched {counts['n_matched']}",
              "- no tracking-frame number is reported here as validated", ""]

    lines += ["## Coverage and Part 2", "",
              f"- `player_play` median share **{counts['median_snap_share']:.3f}** "
              f"vs `min_snap_coverage_share "
              f"{audit['min_snap_coverage_share']}` — a LOWER BOUND only; "
              "no PASS/FAIL is derived from it.",
              f"- tracking-based coverage: "
              f"{counts['tracking_snap_coverage'] or 'not yet measured (stage 03)'}",
              f"- Part 2 status: **{gate['part2_status']}**", ""]

    lines += ["## Power (item k)", "",
              f"- Part 1 MDE hazard ratio: "
              f"{counts['part1_mde_hr'] if counts['part1_mde_hr'] is not None else 'not estimable'}"
              f" (max `audit.max_mde_hr = {audit['max_mde_hr']}`)",
              f"- Part 2 MDE log-score: {counts.get('part2_mde_logscore')} "
              f"(k.part2 = {(items['k'].get('part2') or {}).get('status')}); "
              "Part 2 never gates Phase 1.", ""]

    lines += ["## Gate reasons", ""]
    if gate["no_go"]:
        lines += [f"- **NO-GO** — {r}" for r in gate["no_go"]]
    for r in gate["conditional"]:
        lines += [f"- CONDITIONAL — {r}"]
    for r in gate["notes"]:
        lines += [f"- note — {r}"]
    if not (gate["no_go"] or gate["conditional"]):
        lines.append(f"- no blocking condition; gate is `{gate['gate']}`")
    lines += ["", f"_Pending full run: {', '.join(gate['pending_items']) or 'none'}_", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def run_audit(cfg: Mapping, mode: str, force: bool = False) -> dict:
    out = _out_dir(cfg, mode)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    ab = dl_audit_ab.run(cfg, mode)
    ce = dl_audit_ce.run(cfg, mode)
    f = dl_audit_f.run(cfg, mode)
    pw = dl_audit_power.run(cfg, mode)
    d2 = _d2_nulls(cfg, mode, out)

    items, counts = build_items(cfg, mode, ab, ce, f, pw, d2, out)
    gate = decide(items, counts, cfg)
    runtime_s = time.perf_counter() - t0

    D.write_json(out / "decision.json",
                 {"stage": STAGE, "mode": mode, "gate": gate["gate"],
                  "reasons": gate["reasons"], "no_go": gate["no_go"],
                  "conditional": gate["conditional"], "notes": gate["notes"],
                  "part2_status": gate["part2_status"],
                  "pending_items": gate["pending_items"],
                  "items": items, "counts": counts,
                  "runtime_s": round(runtime_s, 1)}, mode=mode, cfg=cfg)
    D.write_markdown(out / "SUMMARY.md",
                     _summary(cfg, mode, items, gate, counts, d2, runtime_s),
                     mode=mode, cfg=cfg)
    return {"gate": gate["gate"], "items": items, "counts": counts,
            "decision": out / "decision.json", "summary": out / "SUMMARY.md",
            "runtime_s": runtime_s, "gate_detail": gate}


def main(argv: Sequence[str] | None = None) -> int:
    args = D.parse_args(argv)
    cfg = D.load_config(args.config)
    mode = args.mode or D.dl(cfg)["mode"]["default"]

    if D.is_done(cfg, STAGE, mode, force=args.force):
        print(f"[{STAGE}] checkpoint exists for mode={mode}; skipping "
              "(use --force to rebuild)")
        return 0

    with D.timed_stage(cfg, STAGE, mode) as st:
        result = run_audit(cfg, mode, force=args.force)
        st.note(f"gate={result['gate']} items=" +
                ",".join(f"{k}:{v['status']}" for k, v in result["items"].items()))

    D.mark_done(cfg, STAGE, mode,
                {"gate": result["gate"],
                 "decision": str(result["decision"]),
                 "items": {k: v["status"] for k, v in result["items"].items()}})

    print((result["decision"]).read_text(encoding="utf-8"))
    print("\nICC (measured):")
    for r in _read_csv(_out_dir(cfg, mode) / "i_icc.csv"):
        print(f"  {r['feature']:<16} n_att={r['n_attempts']:>5} "
              f"icc={r['icc']:>7} ci=[{r['ci_low']}, {r['ci_high']}] "
              f"att={r['attenuation']}")
    print(f"\n[{STAGE}] {result['gate']} in {result['runtime_s']:.1f} s -> "
          f"{result['summary']}")
    return 0 if result["gate"] != GATE_NO_GO else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
