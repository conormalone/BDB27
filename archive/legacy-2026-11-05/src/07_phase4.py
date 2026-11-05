#!/usr/bin/env python3
"""07_phase4.py — the remaining TASK.md Phase 4 analyses (D67).

Runs after ``06_validation`` on the ``04_efforts`` table, and reads the
``02_combine_ceiling`` per-player ceiling table plus the players mirror for
position.  Nothing here re-derives depth, leverage, thresholds or the primary
specification; the D65 model and the D66 validation logic are untouched.

What runs:

1. matched high-vs-low design (CEM on route family, depth bin, quarter and load
   bin, within player; player-clustered bootstrap);
2. headroom moderation (``leverage x baseline utilization`` estimated on an
   independent half of each player's low-leverage efforts, plus
   ``leverage x vmax`` and ``leverage x tau``), naive and errors-in-variables
   corrected;
3. ceiling bound check (observed and counterfactual exceedance by leverage bin);
4. dose-response across leverage bins, with an explicit monotonicity statement;
5. per-player leverage slopes, gated on model-based reliability;
6. TE replication, labelled exploratory when the sample is small;
7. the tactical-demand contrast that the available fields allow — and an
   explicit NOT RUN with the missing field name for the one that does not.

Outputs (``outputs/game/lc/phase4/``): ``phase4_result.json``,
``matched_pairs.csv``, ``matched_design.csv``, ``headroom.csv``,
``ceiling_bound.csv``, ``dose_response.csv``, ``per_player_slopes.csv``,
``utilization_baseline.csv``, ``checks_not_run.csv`` and ``SUMMARY.md``.  Every
artefact is stamped with the run's provenance by ``lc_common``.

Run:
    python src/07_phase4.py --mode sample [--force]
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import lc_common as C
import lc_models as M
import lc_phase4 as P

STAGE = "07_phase4"

PAIR_FIELDS = ["nfl_id", "stratum", "route_family", "depth_bin", "quarter",
               "load_bin", "leverage_z_high", "leverage_z_low", "y_high",
               "y_low", "diff"]
DESIGN_FIELDS = ["metric", "value", "note"]
HEADROOM_FIELDS = ["model", "moderator", "status", "beta", "se", "ci_low",
                   "ci_high", "p", "naive_beta", "naive_se", "lambda",
                   "corrected_beta", "corrected_se", "correction",
                   "moderator_scale", "main_leverage_beta", "main_leverage_se",
                   "n", "n_players", "note"]
BOUND_FIELDS = ["leverage_bin", "n", "n_players", "mean_observed_fraction",
                "observed_exceedance_rate", "mean_fitted_fraction",
                "fitted_exceedance_rate", "mean_predicted_fraction_at_high",
                "predicted_exceedance_rate_at_high"]
DOSE_FIELDS = ["term", "bin", "role", "status", "beta", "se", "ci_low",
               "ci_high", "p", "n", "n_players", "note"]
SLOPE_FIELDS = ["nfl_id", "slope", "se", "ci_low", "ci_high", "n_efforts"]
UTIL_FIELDS = ["nfl_id", "baseline_utilization", "utilization_se", "n_half_a",
               "n_low_total"]
NOT_RUN_FIELDS = ["check", "missing_field", "reason"]


# --------------------------------------------------------------------------- #
# Paths and inputs
# --------------------------------------------------------------------------- #
def _out_dir(cfg: dict, mode: str):
    paths = C.resolve_paths(cfg, mode)
    sub = P.block(cfg).get("out_subdir", "game/lc/phase4")
    out = paths["outputs"] / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


def _efforts_path(cfg: dict, mode: str):
    paths = C.resolve_paths(cfg, mode)
    sub = C.lc(cfg)["efforts"].get("out_subdir", "game/lc/efforts")
    return paths["outputs"] / sub / "efforts.csv"


def load_efforts(cfg: dict, mode: str) -> pd.DataFrame:
    path = _efforts_path(cfg, mode)
    if not path.exists():
        raise RuntimeError(
            f"STAGE {STAGE!r}: {path} is absent. Run 04_efforts first.")
    frame = pd.read_csv(path, low_memory=False)
    M.assert_effort_provenance(frame, mode, STAGE)
    return frame


def _ceiling_provenance(cfg: dict, mode: str) -> dict:
    """Provenance of the ceiling table, checked in full mode and reported always."""
    sub = C.lc(cfg)["ceiling"].get("out_subdir", "ceiling/lc")
    name = P.block(cfg).get("ceiling_file", "player_ceiling.csv")
    path = C.resolve_paths(cfg, mode)["outputs"] / sub / name
    if not path.exists():
        return {"present": False, "values": []}
    head = pd.read_csv(path, nrows=2000, low_memory=False)
    values = (sorted({str(v) for v in head["provenance"].dropna().unique()})
              if "provenance" in head.columns else [])
    if mode == "full" and values and values != ["FULL"]:
        raise RuntimeError(
            f"STAGE {STAGE!r}: mode=full but the ceiling table provenance is "
            f"{values}, not ['FULL']. Rebuild 02_combine_ceiling on the full "
            "Combine input before running the headroom analysis.")
    return {"present": True, "path": str(path), "values": values}


# --------------------------------------------------------------------------- #
# Summary document
# --------------------------------------------------------------------------- #
def _fmt(value, digits: int = 4, signed: bool = True) -> str:
    try:
        if value is None or not np.isfinite(float(value)):
            return "NA"
        return (f"{float(value):+.{digits}f}" if signed
                else f"{float(value):.{digits}f}")
    except (TypeError, ValueError):
        return "NA"


def _markdown(result: dict, meta: dict) -> str:
    matched = result["matched_design"]
    boot = matched.get("matched") or {}
    reg = matched.get("regression_on_matched") or {}
    head = result["headroom"]
    dose = result["dose_response"]
    slopes = result["per_player_slopes"]
    repl = result["replication"]
    lines = [
        "# Phase 4 remainder — matched design, headroom, ceiling bound, "
        "dose-response, slopes, TE (D67)",
        "",
        "**This is a result only when the provenance line says FULL.** Sample "
        "output is plumbing and is not interpreted.",
        "",
        f"- Efforts: {result['sample_sizes']['n_efforts']:,}  ·  players: "
        f"{result['sample_sizes']['n_players']}",
        f"- Ceiling coverage: {meta.get('n_efforts_with_ceiling', 0):,} efforts "
        f"({meta.get('n_players_with_ceiling', 0)} players)",
        "",
        "## 1. Matched design (sensitivity)",
        "",
        f"- status: **{matched['status']}**  ·  pairs: {matched['n_pairs']}  ·  "
        f"players contributing: {matched['n_players_contributing']}",
        f"- strata matched: {matched['n_strata_matched']}  ·  one-sided strata "
        f"discarded: {matched['n_strata_one_sided']}  ·  unmatched high/low: "
        f"{matched['n_unmatched_high']}/{matched['n_unmatched_low']}",
        f"- matched estimate: {_fmt(boot.get('estimate'))} log-speed "
        f"(clustered bootstrap SE {_fmt(boot.get('se'), signed=False)}, 95% CI "
        f"[{_fmt(boot.get('ci_low'))}, {_fmt(boot.get('ci_high'))}], "
        f"{boot.get('reps', 0)} reps)",
        f"- regression on the matched rows: {_fmt(reg.get('beta_per_sd'))} per "
        f"within-player SD → {_fmt(reg.get('beta_high_vs_low'))} high-minus-low "
        f"(leverage_z gap {_fmt(reg.get('mean_leverage_z_gap'))})",
        "",
        matched["note"],
        "",
        "## 2. Headroom moderation (secondary)",
        "",
        f"- split: {head.get('split', {}).get('rule', 'n/a')}",
        f"- players with a baseline: {head.get('n_players', 'NA')}  ·  "
        f"{head.get('n_utilisation_rows', 'NA')} low-leverage rows used for the "
        f"baseline  ·  {head.get('n_analysis_rows', 'NA')} comparison rows",
        "",
        "| model | naive beta (SE) | lambda | corrected beta (SE) | n | players |",
        "|---|---|---|---|---|---|",
    ]
    for row in head.get("rows", []):
        lines.append(
            f"| {row['model']} | {_fmt(row.get('naive_beta'))} "
            f"({_fmt(row.get('naive_se'), signed=False)}) | "
            f"{_fmt(row.get('lambda'), 3, signed=False)} | "
            f"{_fmt(row.get('corrected_beta'))} "
            f"({_fmt(row.get('corrected_se'), signed=False)}) | "
            f"{row.get('n', 'NA')} | "
            f"{row.get('n_players', 'NA')} |")
    lines += ["", "Correction: " + P.EIV_CORRECTION + ".", ""]

    bound = result["ceiling_bound"]
    lines += ["## 3. Ceiling bound check", ""]
    if bound.get("status") != "ok":
        lines.append(f"NOT RUN / not estimable: {bound.get('reason') or bound.get('status')}")
    else:
        lines += [
            "| leverage bin | n | observed exceedance | predicted-at-high exceedance |",
            "|---|---|---|---|",
        ]
        for row in bound["rows"]:
            lines.append(f"| {row['leverage_bin']} | {row['n']} | "
                         f"{row['observed_exceedance_rate']:.4f} | "
                         f"{row['predicted_exceedance_rate_at_high']:.4f} |")
        lines.append("")
        lines.append(
            f"Max predicted effort fraction at high leverage: "
            f"{bound['max_predicted_fraction_at_high']:.4f} "
            f"(exceedance defined as > {bound['exceedance_ratio']}).")

    lines += ["", "## 4. Dose-response", ""]
    if dose.get("status") != "ok":
        lines.append(f"NOT RUN: {dose.get('note') or dose.get('status')}")
    else:
        for row in dose["rows"]:
            if row["role"] == "reference":
                lines.append(f"- {row['term']}: reference (n {row.get('n')})")
            else:
                lines.append(f"- {row['term']}: {_fmt(row.get('beta'))} "
                             f"(SE {_fmt(row.get('se'), signed=False)}, "
                             f"n {row.get('n')})")
        lines.append("")
        lines.append(f"Monotone: **{dose['monotone']}** ({dose['direction']}); "
                     f"trend {_fmt(dose['trend'].get('beta'))} per bin "
                     f"(SE {_fmt(dose['trend'].get('se'), signed=False)}, "
                     f"p {_fmt(dose['trend'].get('p'))}).")

    lines += ["", "## 5. Per-player leverage slopes", ""]
    lines.append(f"- status: **{slopes['status']}**  ·  reliability "
                 f"{_fmt(slopes.get('reliability'), 3, signed=False)} vs threshold "
                 f"{slopes.get('reliability_threshold')}")
    lines.append(f"- {slopes['reason']}")
    if slopes["status"] != "reported":
        lines.append("- no per-player table is emitted (the gate is the point)")

    lines += ["", "## 6. TE replication", ""]
    if repl.get("status") != "ok":
        lines.append(f"NOT RUN: {repl.get('reason')}")
    else:
        lines.append(
            f"- {repl['position']}: {_fmt(repl.get('beta'))} "
            f"(SE {_fmt(repl.get('se'), signed=False)}, 95% CI "
            f"[{_fmt(repl.get('ci_low'))}, {_fmt(repl.get('ci_high'))}]), "
            f"n = {repl['n_efforts']:,} efforts / {repl['n_players']} players")
        lines.append(f"- label: **{repl['label']}** "
                     f"(threshold {repl['exploratory_below_players']} players)")

    tactical = result["tactical_demand"]
    lines += ["", "## 7. Tactical demand — what ran and what did not", ""]
    tt = tactical.get("time_to_throw") or {}
    if tt.get("status") == "ok":
        lines.append(f"- time to throw: all {_fmt(tt['all'].get('beta'))} "
                     f"(SE {_fmt(tt['all'].get('se'), signed=False)}, "
                     f"n {tt['all'].get('n')}); "
                     f"restricted ±{tt['window_s']}s around the median "
                     f"{_fmt(tt['median_s'], 2)}s: "
                     f"{_fmt(tt['comparable'].get('beta'))} "
                     f"(SE {_fmt(tt['comparable'].get('se'), signed=False)}, "
                     f"n {tt['comparable'].get('n')})")
    else:
        lines.append(f"- time to throw: NOT RUN — {tt.get('reason')}")
    dd = tactical.get("defender_distance") or {}
    if dd.get("status") == "ok":
        lines.append(f"- defender distance: interaction {_fmt(dd.get('interaction_beta'))} "
                     f"(SE {_fmt(dd.get('se'), signed=False)}, n {dd.get('n')})")
    else:
        lines.append(f"- defender distance: **NOT RUN** — missing field "
                     f"`{dd.get('missing_field')}`. {dd.get('reason')}")

    lines += ["", "## Checks NOT RUN", ""]
    if not result["checks_not_run"]:
        lines.append("- none")
    for row in result["checks_not_run"]:
        lines.append(f"- {row['check']}: missing field `{row.get('missing_field')}` "
                     f"— {row.get('reason')}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Stage entry point
# --------------------------------------------------------------------------- #
def run(cfg: dict, mode: str, bootstrap_reps: int | None = None) -> dict:
    table = load_efforts(cfg, mode)
    ceiling_prov = _ceiling_provenance(cfg, mode)
    enriched, meta = P.enrich(table, cfg, mode)
    meta["ceiling_provenance"] = ceiling_prov.get("values", [])
    result = P.run_phase4(enriched, cfg, mode=mode,
                          bootstrap_reps=bootstrap_reps)
    return {"result": result, "meta": meta, "table": enriched}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sample", "full"), default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    cfg = C.load_config(args.config)
    mode = args.mode or C.lc(cfg)["mode"]["default"]
    paths = C.resolve_paths(cfg, mode)
    C.assert_disk_space(paths, cfg)
    C.require_full_provenance(cfg, mode, STAGE)
    if C.is_done(cfg, STAGE, mode, force=args.force):
        print(f"[{STAGE}] checkpoint present — skipping (use --force to rebuild)")
        return 0
    out = _out_dir(cfg, mode)

    with C.timed_stage(cfg, STAGE, mode,
                       note="Phase 4 remainder: matched, headroom, bound, dose, "
                            "slopes, TE") as stage:
        bundle = run(cfg, mode)
        result, meta = bundle["result"], bundle["meta"]
        stage.note(f"matched_pairs={result['matched_design']['n_pairs']}")
        stage.note(f"headroom={result['headroom'].get('status')}")
        stage.note(f"slopes={result['per_player_slopes']['status']}")
        stage.note(f"te={result['replication'].get('status')}")

    result["inputs"] = {
        "efforts": str(_efforts_path(cfg, mode)),
        "ceiling_provenance": meta.get("ceiling_provenance", []),
        "position_source": meta.get("position_source"),
        "ceiling_source": meta.get("ceiling_source"),
        "n_efforts_with_ceiling": meta.get("n_efforts_with_ceiling", 0),
        "n_players_with_ceiling": meta.get("n_players_with_ceiling", 0),
    }
    pairs = result.pop("_pairs")
    utilization = result.pop("_utilization")

    C.write_json(out / P.block(cfg).get("summary_filename", "phase4_result.json"),
                 result, mode=mode, cfg=cfg)
    C.write_csv(out / "matched_pairs.csv",
                (pairs.to_dict("records") if pairs is not None and len(pairs)
                 else []), PAIR_FIELDS, mode=mode, cfg=cfg)
    matched = result["matched_design"]
    design_rows = [
        {"metric": "status", "value": matched["status"], "note": matched["note"]},
        {"metric": "n_pairs", "value": matched["n_pairs"], "note": ""},
        {"metric": "n_players_contributing",
         "value": matched["n_players_contributing"], "note": ""},
        {"metric": "n_strata_matched", "value": matched["n_strata_matched"],
         "note": ""},
        {"metric": "n_strata_one_sided", "value": matched["n_strata_one_sided"],
         "note": "strata with only one leverage bin present"},
        {"metric": "matched_estimate", "value": (matched.get("matched") or {}).get("estimate"),
         "note": "mean within-pair difference, log max speed"},
        {"metric": "matched_se", "value": (matched.get("matched") or {}).get("se"),
         "note": "player-clustered bootstrap"},
        {"metric": "regression_beta_per_sd",
         "value": (matched.get("regression_on_matched") or {}).get("beta_per_sd"),
         "note": "primary specification on the matched rows"},
        {"metric": "regression_high_vs_low",
         "value": (matched.get("regression_on_matched") or {}).get("beta_high_vs_low"),
         "note": "converted with the matched-sample leverage_z gap"},
    ]
    C.write_csv(out / "matched_design.csv", design_rows, DESIGN_FIELDS,
                mode=mode, cfg=cfg)
    C.write_csv(out / "headroom.csv", result["headroom"].get("rows", []),
                HEADROOM_FIELDS, mode=mode, cfg=cfg)
    bound = result["ceiling_bound"]
    bound_rows = list(bound.get("rows") or [])
    if bound.get("all_rows"):
        bound_rows.append(bound["all_rows"])
    C.write_csv(out / "ceiling_bound.csv", bound_rows, BOUND_FIELDS,
                mode=mode, cfg=cfg)
    C.write_csv(out / "dose_response.csv",
                result["dose_response"].get("rows", []), DOSE_FIELDS,
                mode=mode, cfg=cfg)
    C.write_csv(out / "per_player_slopes.csv",
                result["per_player_slopes"].get("rows", []), SLOPE_FIELDS,
                mode=mode, cfg=cfg)
    C.write_csv(out / "utilization_baseline.csv",
                (utilization.to_dict("records")
                 if utilization is not None and len(utilization) else []),
                UTIL_FIELDS, mode=mode, cfg=cfg)
    C.write_csv(out / "checks_not_run.csv", result["checks_not_run"],
                NOT_RUN_FIELDS, mode=mode, cfg=cfg)
    C.write_markdown(out / "SUMMARY.md", _markdown(result, meta),
                     mode=mode, cfg=cfg)

    C.mark_done(cfg, STAGE, mode, extra={
        "matched_pairs": matched["n_pairs"],
        "matched_status": matched["status"],
        "headroom_status": result["headroom"].get("status"),
        "slopes_status": result["per_player_slopes"]["status"],
        "te_status": result["replication"].get("status"),
    })
    kept = result["headroom"].get("rows") or []
    first = kept[0] if kept else {}
    print(f"[{STAGE}] matched pairs={matched['n_pairs']} "
          f"({matched['n_players_contributing']} players) · "
          f"headroom beta={_fmt(first.get('beta'))} · "
          f"dose monotone={result['dose_response'].get('monotone')} · "
          f"slopes={result['per_player_slopes']['status']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
