#!/usr/bin/env python
"""Multi-candidate attribution end to end — the identified set, the variants, the frame.

Specification: ``notes/multi-candidate-spec.md`` (committed 8726120, before any
code existed).

    python scripts/ambiguity_attribution.py --mode full \
        --turns data/interim/full_run_20261111/bdb27_full_db_turns.parquet \
        --player-play data/raw/player_play.csv \
        --out-dir outputs/ambiguity_attribution

The frozen rule's own numbers are produced and printed first, on every run, and
every new number sits beside them. Nothing here replaces D197 or D199, and
nothing here is written into ``TASK.md``, the band table or the report draft.

Exit codes: 0 = ran to completion (including "NOT FITTED"); 1 = an unexpected
error. There is no gate, so there is no third code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bdb27 import ambiguity_attribution as aa  # noqa: E402
from bdb27 import common, outcome_test  # noqa: E402

DEFAULT_TURNS = ROOT / "data/interim/full_run_20261111/bdb27_full_db_turns.parquet"
DEFAULT_PLAYER_PLAY = ROOT / "data/raw/player_play.csv"
RUN_LOG = ROOT / "outputs" / "run_log.csv"


def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _stamp(mode: str) -> str:
    return common.PROVENANCE_FULL if mode == "full" else common.PROVENANCE_SAMPLE


def append_run_log(stage: str, mode: str, wall_s: float, rows: int, status: str,
                   detail: str = "") -> None:
    """One row, same columns as ``common.log_run`` (utc,stage,mode,provenance,
    status,wall_s,peak_rss_mb,rows,detail). Written here rather than through
    ``common.log_run`` so the run does not depend on ``config.yaml`` loading."""
    peak = common.peak_rss_mb()
    row = {"utc": common.utc_now(), "stage": stage, "mode": mode,
           "provenance": _stamp(mode), "status": status,
           "wall_s": round(float(wall_s), 3),
           "peak_rss_mb": None if peak is None else round(float(peak), 1),
           "rows": int(rows), "detail": detail}
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    header = not RUN_LOG.exists()
    pd.DataFrame([row]).to_csv(RUN_LOG, mode="a", header=header, index=False)


def _fmt(value, digits: int = 3, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(v):
        return "n/a"
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def _share(n, d) -> str:
    try:
        return f"{int(n) / int(d):.1%}" if int(d) else "n/a"
    except (TypeError, ValueError, ZeroDivisionError):
        return "n/a"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("full", "sample"), default="full")
    ap.add_argument("--turns", default=None)
    ap.add_argument("--player-play", default=None)
    ap.add_argument("--out-dir", default="outputs/ambiguity_attribution")
    ap.add_argument("--reps", type=int, default=aa.RANDOM_REPS)
    ap.add_argument("--seed", type=int, default=aa.RANDOM_SEED)
    ap.add_argument("--tracking", nargs="*", default=None,
                    help="full game tracking, for the candidate-geometry table; "
                         "absent means the geometry is reported NOT EVALUABLE")
    args = ap.parse_args(argv)

    t0 = time.time()
    turns_path = Path(args.turns).expanduser().resolve() if args.turns else DEFAULT_TURNS
    pp_path = (Path(args.player_play).expanduser().resolve()
               if args.player_play else DEFAULT_PLAYER_PLAY)
    out = Path(args.out_dir).expanduser()
    out = out if out.is_absolute() else ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(args.mode)

    _log(f"turns      : {turns_path}")
    _log(f"player_play: {pp_path}")
    _log(f"out        : {out}  [{stamp}]")
    _log(f"spec       : notes/multi-candidate-spec.md (8726120)")
    _log("")

    # ---- O1: the funnel, re-derived --------------------------------------
    con = aa.connect()
    try:
        funnel = aa.funnel(turns_path, pp_path, con=con)
        frames = {}
        for status in ("single", "multiple"):
            frames[status] = aa.build_frame(turns_path, pp_path, status=status,
                                            con=con)
    finally:
        con.close()

    _log("FROZEN RULE (D197) — numbers produced by this run, not quoted")
    for _, r in funnel.iterrows():
        mark = "" if r["matches"] else f"  <- committed artefact says {r['expected_2026_11_14']:,}"
        _log(f"  {r['step']:<48s} {int(r['n']):>6,}  of {int(r['denominator']):>6,} "
             f"({_share(r['n'], r['denominator']):>7s}){mark}")
    single, multi = frames["single"], frames["multiple"]
    _log(f"  frame(single) rows {len(single):,} · frame(multiple) candidate rows "
         f"{len(multi):,} over {multi[list(outcome_test.TURN_KEY)].drop_duplicates().shape[0]:,} turns")
    _log("")

    frame = aa.analysis_frame(single, multi)

    # ---- O2: the identified set, per specification -----------------------
    bounds = [aa.identified_set(frame, spec) for spec in aa.SPECS]
    frozen_primary = bounds[0]
    _log("O2 — identified set *beside* the frozen-only estimate (spec §3, §4)")
    _log(f"  {'spec':<11s} {'frozen beta':>12s} {'95% CI':>20s} {'n':>6s} "
         f"{'clusters':>8s} | {'identified set':>22s} {'width':>7s} {'CI width':>8s}  label")
    for b in bounds:
        ci = f"[{_fmt(b['ci_lo'])}, {_fmt(b['ci_hi'])}]"
        _log(f"  {b['spec']:<11s} {_fmt(b['beta_frozen']):>12s} {ci:>20s} "
             f"{b['n_frozen']:>6,} {b['frozen_clusters']:>8,} | "
             f"[{_fmt(b['beta_min'])}, {_fmt(b['beta_max'])}] {_fmt(b['width']):>7s} "
             f"{_fmt(b['ci_width']):>8s}  {b['label']}")
    _log(f"  primary branch: {frozen_primary['branch']}  "
         f"(contains zero: {frozen_primary['contains_zero']}, "
         f"inside CI: {frozen_primary['inside_ci']})")
    _log(f"  population: {frozen_primary['n_with_separation']:,} turns "
         f"({frozen_primary['n_frozen']:,} single-candidate + "
         f"{frozen_primary['n_ambiguous']:,} with two or more)")
    _log("")

    # ---- O3: the three variants -----------------------------------------
    variants = []
    for spec in aa.SPECS:
        for rule in aa.PAIRING_RULES:
            variants.append(aa.variant_fit(frame, rule, spec))
    _log("O3 — resolution variants (each an INFERENCE, each with its selection)")
    for v in variants:
        if v["spec"] != "primary":
            continue
        inside = ""
        if rule_is_bounded(v["rule"]):
            lo, hi = frozen_primary["beta_min"], frozen_primary["beta_max"]
            inside = (f" · inside bound: {lo <= v['beta'] <= hi}"
                      if np.isfinite(v["beta"]) else "")
        _log(f"  {v['variant']:<38s} beta {_fmt(v['beta'])} "
             f"[{_fmt(v['lo'])}, {_fmt(v['hi'])}] n={v['n']:,}{inside}")
        _log(f"      selects: {v['population']}")
    _log("")

    # ---- AC3 invariant, on the real frame -------------------------------
    randoms = pd.concat([aa.random_assignments(frame, spec, reps=args.reps,
                                              seed=args.seed)
                         for spec in aa.SPECS], ignore_index=True)
    _log(f"AC3 — {args.reps} fixed-seed random pairings (seed {args.seed}) per "
         f"specification, plus V2/V3, must land inside the identified set")
    for spec in aa.SPECS:
        sub = randoms[randoms["spec"] == spec]
        _log(f"  {spec:<11s} inside {int(sub['inside_bound'].sum()):,} of {len(sub):,}"
             f" · max |closed form - fit| = {sub['abs_difference'].max():.2e}")
    _log("")

    # ---- O4: composition ------------------------------------------------
    comp = aa.composition(single, multi)
    _log("O4 — composition of the ambiguous turns against the single-candidate ones")
    cat = comp[(comp["metric"] == "share")].copy()
    cat["abs_diff"] = cat["difference"].abs()
    for _, r in cat.sort_values("abs_diff", ascending=False).head(12).iterrows():
        _log(f"  {r['variable']:<22s} {str(r['level'])[:18]:<18s} "
             f"ambiguous {_share(r['ambiguous'], r['ambiguous_total']):>7s} "
             f"({int(r['ambiguous']):,}/{int(r['ambiguous_total']):,}) vs single "
             f"{_share(r['single'], r['single_total']):>7s} "
             f"({int(r['single']):,}/{int(r['single_total']):,})  "
             f"diff {r['difference']:+.3f}")
    for var in ("entry_speed", "apex_retention", "abs_turn_angle"):
        row = comp[(comp["variable"] == var) & (comp["metric"] == "mean")]
        if len(row):
            r = row.iloc[0]
            _log(f"  {var:<22s} mean         ambiguous {float(r['ambiguous']):.3f} "
                 f"(n={int(r['ambiguous_total']):,}) vs single {float(r['single']):.3f} "
                 f"(n={int(r['single_total']):,})  diff {r['difference']:+.3f}")
    gap = comp[(comp["variable"] == "separation_gap_yd")]
    if len(gap):
        r = gap.iloc[0]
        _log(f"  separation gap MAX(sep)-MIN(sep): median "
             f"{float(r['ambiguous']):.3f} yd over {int(r['ambiguous_total']):,} "
             f"ambiguous turns — not a distance between two players")
    _log("")

    # ---- O5: candidate geometry ----------------------------------------
    geom = aa.candidate_distance_table(frame, turns_path, pp_path,
                                       tracking=args.tracking)
    _log("O5 — candidate geometry (needs frame-level tracking)")
    _log(f"  {geom['geometry_status'].iloc[0] if len(geom) else 'NO AMBIGUOUS TURNS'}")
    if "paths_tried" in geom.columns and len(geom):
        _log(f"  paths tried: {geom['paths_tried'].iloc[0]}")
    _log("")

    # ---- Route A (§3) and the geometric slots (§0) ----------------------
    frame_all = aa.all_candidates_frame(turns_path, pp_path)
    frozen_keys = list(map(tuple, single[list(outcome_test.TURN_KEY)]
                           .drop_duplicates().to_numpy().tolist()))
    route_turns, route_audit = aa.route_a_tables(frame_all, pp_path,
                                                 exclude=frozen_keys)
    route_eligible = (frame_all[list(outcome_test.TURN_KEY)].drop_duplicates()
                      .shape[0] - len(frozen_keys))
    _log(f"Route A — no-tracking recovery on the {route_eligible:,} turns the "
         f"frozen rule did not take")
    for _, r in route_audit.iterrows():
        _log(f"  {r['route_a_rule']:<12s} {r['outcome']:<11s} "
             f"{r['reason'] or '-':<32s} {int(r['n']):>5,} of {int(r['denominator']):,}")
    _log(f"  attributed by Route A: {len(route_turns):,} of {route_eligible:,} "
         f"({_share(len(route_turns), route_eligible)})")
    _log("")

    geo_slots = aa.geometry_slots(turns_path, pp_path, frame_all,
                                  tracking=args.tracking, exclude=frozen_keys)
    _log("Geometric slots (5 then 8 yd, on what neither the frozen rule nor the "
         "radius before it took)")
    _log(f"  {geo_slots['status']} · turns considered {geo_slots['n_turns_considered']:,} · "
         f"N_geo(5) {geo_slots['n_geo_5']:,} · N_geo(8) {geo_slots['n_geo_8']:,}")
    _log(f"  paths tried: {geo_slots['paths_tried']}")
    if len(geo_slots["rows"]):
        st = geo_slots["rows"]["geometry_status"].value_counts()
        _log("  per-turn status: " + " · ".join(f"{k} {v:,}" for k, v in st.items()))
    singles_geo = aa.single_candidate_geometry(turns_path, single,
                                               tracking=args.tracking)
    _log("Frozen 3-yd check on the single-candidate set — a check on N_frozen, "
         "not a recovery route")
    _log(f"  {singles_geo['status']}")
    _log(f"  tracked {singles_geo['n_tracked']:,} of {singles_geo['n_turns']:,} · "
         f"within {singles_geo['radius_yd']:g} yd "
         f"{singles_geo['n_within_radius']:,} of {singles_geo['n_tracked']:,} "
         f"tracked turns · D204 reported {singles_geo['d204_reported']:,} on the "
         f"full file")
    if len(singles_geo["rows"]):
        st = singles_geo["rows"]["geometry_status"].value_counts()
        _log("  per-turn status: " + " · ".join(f"{k} {v:,}" for k, v in st.items()))
    _log("  these turns are already inside N_frozen; nothing here is added to N")
    _log("")

    agree = aa.agreement(route_turns, geo_slots["rows"])
    if len(agree) and int(agree.loc[agree["scope"] == "overall", "n_both"].sum()):
        r = agree[agree["scope"] == "overall"].iloc[0]
        _log(f"  cross-check on turns both routes fired: {int(r['agree']):,} agree / "
             f"{int(r['disagree']):,} disagree ({_share(r['agree'], r['n_both'])})")
    else:
        _log("  cross-check: NOT EVALUABLE — no turn where both routes fired")
    _log("")

    by_rule = (route_turns["route_a_rule"].value_counts().to_dict()
               if len(route_turns) else {})
    coverage = (route_turns["coverage_family"].fillna("MISSING").value_counts()
                .to_dict() if len(route_turns) else {})
    assembled = aa.assemble_attribution({
        "n_frozen": int(frozen_primary["n_frozen"]),
        "n_geo_5": int(geo_slots["n_geo_5"]),
        "n_geo_8": int(geo_slots["n_geo_8"]),
        "n_route_a": int(len(route_turns)),
        "band_turns": int(funnel.loc[funnel["step"] == "band_turns", "n"].iloc[0]),
        "status": {"geo_5": geo_slots["status"], "geo_8": geo_slots["status"]},
        "route_a_by_rule": by_rule,
        "coverage": coverage,
    })
    _log(f"N = {assembled['N']:,} of {assembled['band_turns']:,} band turns "
         f"({_share(assembled['N'], assembled['band_turns'])})")
    for c in assembled["composition"]:
        _log(f"  {c['slot']:<8s} {c['label']:<28s} {c['n']:>6,}  "
             f"of N {_share(c['n'], assembled['N']):>7s}"
             + (f"  [{c['status'][:40]}]" if c["status"] else ""))
    _log(f"  RULE share of N {_share(assembled['slots']['n_frozen'], assembled['N'])} · "
         f"INFERENCE share {_share(assembled['N'] - assembled['slots']['n_frozen'], assembled['N'])}")
    if by_rule:
        _log("  Route A by rule: " + " · ".join(f"{k} {v:,}" for k, v in by_rule.items()))
    if coverage:
        _log("  coverage label of the attributed turns: "
             + " · ".join(f"{k} {v:,}" for k, v in coverage.items()))
    _log("")

    align = aa.alignment_counts(pp_path)
    _log("AC10 — plays with exactly two receivers, recomputed from player_play.csv")
    for _, r in align.iterrows():
        mark = "" if r["matches"] in (True, None) else f"  <- spec says {int(r['spec_anchor']):,}"
        _log(f"  {r['pair']:<26s} {int(r['n_plays']):>6,} of {int(r['all_plays']):,} plays"
             f" ({_share(r['n_plays'], r['all_plays'])}){mark}")
    _log("")

    # ---- artefacts ------------------------------------------------------
    funnel.to_csv(out / "funnel.csv", index=False, encoding="utf-8")
    multi_turns = _multi_turn_table(multi)
    multi_turns.to_csv(out / "multi_turns.csv", index=False, encoding="utf-8")
    bounds_df = pd.DataFrame(bounds)
    bounds_df.to_csv(out / "bounds.csv", index=False, encoding="utf-8")
    variants_df = pd.DataFrame(variants)
    variants_df.to_csv(out / "variants.csv", index=False, encoding="utf-8")
    comp.to_csv(out / "composition.csv", index=False, encoding="utf-8")
    randoms.to_csv(out / "random_pairings.csv", index=False, encoding="utf-8")
    geom.to_csv(out / "geometry.csv", index=False, encoding="utf-8")
    route_turns.to_csv(out / "route_a_turns.csv", index=False, encoding="utf-8")
    route_audit.to_csv(out / "route_a_audit.csv", index=False, encoding="utf-8")
    pd.DataFrame(assembled["composition"]).to_csv(out / "assembly.csv", index=False,
                                                  encoding="utf-8")
    singles_geo["rows"].to_csv(out / "single_turn_geometry.csv", index=False,
                               encoding="utf-8")

    summary = {
        "provenance": stamp,
        "spec": "notes/multi-candidate-spec.md",
        "spec_commit": "8726120",
        "attribution": aa.ATTRIBUTION,
        "rule_frozen": ("single opposing route-runner within 3 yd at the break frame; "
                        "two or more candidates dropped, never tie-broken"),
        "funnel": {r["step"]: int(r["n"]) for _, r in funnel.iterrows()},
        "funnel_matches_committed": bool(funnel["matches"].all()),
        "frames": {
            "single_rows": int(len(single)),
            "single_turns": int(single[list(outcome_test.TURN_KEY)]
                               .drop_duplicates().shape[0]),
            "multiple_candidate_rows": int(len(multi)),
            "multiple_turns": int(multi[list(outcome_test.TURN_KEY)]
                                  .drop_duplicates().shape[0]),
            "analysis_population": int(frozen_primary["n_population"]),
        },
        "bounds": bounds,
        "label_set_by": "O2 only (spec §4)",
        "primary_branch": frozen_primary["branch"],
        "primary_label": frozen_primary["label"],
        "variants": [{k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                      for k, v in row.items()} for row in variants],
        "random_pairings": {
            "reps": int(args.reps), "seed": int(args.seed),
            "all_inside_bound": bool(randoms["inside_bound"].all()),
            "max_abs_closed_form_minus_fit": float(randoms["abs_difference"].max()),
        },
        "assembly": {
            "N": assembled["N"],
            "slots": assembled["slots"],
            "composition": assembled["composition"],
            "band_turns": assembled["band_turns"],
            "share_of_band": assembled["share_of_band"],
            "rule_share_of_N": assembled["rule_share_of_N"],
            "inference_share_of_N": assembled["inference_share_of_N"],
            "route_a_by_rule": by_rule,
            "coverage": coverage,
            "status": assembled["status"],
        },
        "route_a": {
            "eligible_turns": int(route_eligible),
            "attributed_turns": int(len(route_turns)),
            "by_rule": by_rule,
            "audit": route_audit.to_dict("records"),
        },
        "geometry_slots": {
            "n_geo_5": geo_slots["n_geo_5"],
            "n_geo_8": geo_slots["n_geo_8"],
            "status": geo_slots["status"],
            "n_turns_considered": geo_slots["n_turns_considered"],
            "paths_tried": geo_slots["paths_tried"],
            "per_turn_status": (geo_slots["rows"]["geometry_status"]
                                .value_counts().to_dict() if len(geo_slots["rows"]) else {}),
        },
        "frozen_3yd_check": {
            "n_turns": singles_geo["n_turns"],
            "n_tracked": singles_geo["n_tracked"],
            "n_within_radius": singles_geo["n_within_radius"],
            "radius_yd": singles_geo["radius_yd"],
            "status": singles_geo["status"],
            "d204_reported": singles_geo["d204_reported"],
            "paths_tried": singles_geo["paths_tried"],
            "note": ("a check on the frozen single-candidate set: these turns are "
                     "already inside N_frozen, so nothing here is added to N"),
        },
        "agreement": agree.to_dict("records") if len(agree) else [],
        "alignment_counts": align.to_dict("records"),
        "geometry": {
            "status": (geom["geometry_status"].iloc[0] if len(geom) else "NO AMBIGUOUS TURNS"),
            "rows": int(len(geom)),
            "note": ("candidate distance, nearest-candidate margin and the gap between "
                     "the two nearest candidates need frame-level tracking; without it "
                     "the columns are null and this status is printed"),
        },
        "limits": [
            "The identified set is conditional on the true receiver being among the "
            "visible candidates; the cohort hides candidates, so the true set can only "
            "be wider. The bound is optimistic in the direction of precision.",
            "The bound is not a confidence interval: it carries no sampling error and "
            "is reported beside the CI, never combined with it.",
            "Every pairing is an INFERENCE. The dataset records no opponent identifier, "
            "so no output here says which man was covered.",
            "two_candidate_separation_gap_yd is MAX(sep) - MIN(sep) of the candidates' "
            "separation values. It is not a distance between two players.",
            "Nothing here enters TASK.md, the band table, the four figures or the "
            "report draft. TASK.md forbids outcome tests; this is a diagnostic on an "
            "estimator TASK.md already excludes.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                      encoding="utf-8")

    lines = aa.plain_english_verdict(summary)
    (out / "verdict.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log("VERDICT")
    for line in lines:
        _log("  " + line)
    _log("")

    manifest = aa.write_manifest(
        out, [turns_path, pp_path],
        [out / name for name in ("funnel.csv", "multi_turns.csv", "bounds.csv",
                                 "variants.csv", "composition.csv",
                                 "random_pairings.csv", "geometry.csv",
                                 "route_a_turns.csv", "route_a_audit.csv",
                                 "single_turn_geometry.csv", "assembly.csv",
                                 "summary.json", "verdict.txt")],
        config={"mode": args.mode, "reps": args.reps, "seed": args.seed,
                "specs": list(aa.SPECS), "pairing_rules": list(aa.PAIRING_RULES),
                "tracking": args.tracking},
        extra={"stage": "complete", "provenance": stamp})
    _log(f"manifest: {len(manifest['inputs'])} inputs, {len(manifest['outputs'])} "
         f"outputs sha256'd, git HEAD {manifest['git_head']}")

    wall = time.time() - t0
    append_run_log("ambiguity_attribution", args.mode, wall, len(frame),
                   "OK", f"single {len(single):,} · ambiguous "
                         f"{frozen_primary['n_ambiguous']:,} · N {assembled['N']:,} "
                         f"(route A {len(route_turns):,}) · label "
                         f"{frozen_primary['branch']}")
    _log(f"run_log: one row appended ({wall:,.0f}s, {len(frame):,} analysis rows)")
    return 0


def rule_is_bounded(rule: str) -> bool:
    """V2/V3 are the realisable extremes; V1 changes the population."""
    return rule in ("min_sep", "max_sep")


def _multi_turn_table(multi: pd.DataFrame) -> pd.DataFrame:
    """One row per ambiguous turn: its candidate count and separation span."""
    key = list(outcome_test.TURN_KEY)
    if not len(multi):
        return pd.DataFrame(columns=[*key, "n_candidates", "n_candidates_with_separation",
                                     "separation_min_yd", "separation_max_yd",
                                     "separation_gap_yd", "n_target",
                                     "attribution"])
    df = multi.drop_duplicates(subset=[*key, "receiver_nfl_id"])
    out = df.groupby(key, sort=True).agg(
        n_candidates=("receiver_nfl_id", "nunique"),
        n_candidates_with_separation=("separation_yd", lambda s: int(s.notna().sum())),
        separation_min_yd=("separation_yd", "min"),
        separation_max_yd=("separation_yd", "max"),
        n_target=("receiver_target", lambda s: int(s.fillna(False).sum())),
    ).reset_index()
    out["separation_gap_yd"] = out["separation_max_yd"] - out["separation_min_yd"]
    out["attribution"] = aa.ATTRIBUTION
    return out


if __name__ == "__main__":
    raise SystemExit(main())
