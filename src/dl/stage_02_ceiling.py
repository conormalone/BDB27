#!/usr/bin/env python3
"""stage_02_ceiling — Phase 2: Combine acceleration ceiling for DL.

Run order: after ``01_audit``, before ``00_thresholds``.

    cd src && python -m dl.run 02_combine_ceiling --mode sample
    python src/dl/stage_02_ceiling.py --mode sample --force

What it does (``notes/dl-design.md`` §5.2):

* fits ``v(t) = vmax*(1-exp(-t/tau))`` per straight-line Combine attempt and
  derives ``a0 = vmax/tau`` with a delta-method SE;
* pools attempts per player with empirical-Bayes shrinkage (log scale, never a
  best attempt) and carries an SE with every estimate;
* reports test-retest ICC, the ICC/VIF screen that ranks alternate
  operationalisations while keeping ``a0`` (the estimand) admitted, the
  between-player correlation of the shrunken estimates (a within-player
  correlation is not estimable at two attempts per player), and exploratory
  shuttle/cone mechanics;
* writes ``player_ceiling.csv``, ``attempt_fits.csv``, ``summary.json`` and
  ``SUMMARY.md`` under ``outputs/dl/ceiling/`` through the provenance writers.

Every artefact carries the mode's provenance stamp; sample output is not a
result and is never interpreted here.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402  (needs the sys.path fix above)
from dl import dl_ceiling_core as core  # noqa: E402

STAGE = "02_combine_ceiling"

#: ``player_ceiling.csv`` columns, fixed by ``notes/dl-design.md`` §5.2.
PLAYER_FIELDS = ("nfl_id", "position_group", "feature", "estimate", "se",
                 "n_attempts", "icc", "reliability", "selected", "vif")

#: ``attempt_fits.csv`` columns: one row per fitted attempt, plus one row per
#: lateral (shuttle/cone) attempt with only its mechanics filled in.
ATTEMPT_FIELDS = ("nfl_id", "drill_name", "attempt", "event_id", "kind",
                  "n_frames", "duration_s", "peak_speed", "vmax", "tau", "a0",
                  "r2", "se_log_vmax", "se_log_tau", "se_log_a0",
                  "speed_at_1_0", "speed_at_2_0", "entry_speed", "exit_speed",
                  "total_time_s", "braking_distance_yds", "ten_yd_split",
                  "peak_accel", "flags")


def _out_dir(cfg: dict, mode: str) -> Path:
    """``outputs/dl/ceiling`` (subdir overridable from the config block)."""
    paths = D.resolve_paths(cfg, mode)
    subdir = D.dl(cfg)["ceiling"].get("out_subdir") or "ceiling"
    return paths["outputs"] / str(subdir)


def _check_fields(rows: list[dict], fields: tuple[str, ...], label: str) -> None:
    """Mechanical schema check: every row carries exactly ``fields``."""
    for i, row in enumerate(rows):
        extra = set(row) - set(fields)
        missing = set(fields) - set(row)
        if extra or missing:
            raise RuntimeError(f"{label} row {i} schema mismatch: "
                               f"extra={sorted(extra)} missing={sorted(missing)}")


def _csv_ready(rows: list[dict]) -> list[dict]:
    """Map non-finite floats to ``''`` so the CSV stays readable and stable."""
    out: list[dict] = []
    for row in rows:
        clean: dict = {
            key: ("" if isinstance(value, float) and not math.isfinite(value)
                  else value)
            for key, value in row.items()}
        out.append(clean)
    return out


def _fmt(value, digits: int = 3) -> str:
    if isinstance(value, float):
        if not (value == value):
            return "NA"
        return f"{value:.{digits}f}"
    return str(value)


def _markdown(summary: dict, mode: str) -> str:
    icc = summary["icc"]
    lines = [
        "# Phase 2 — DL Combine acceleration ceiling",
        "",
        f"Mode: `{mode}`. Fits `v(t) = vmax * (1 - exp(-t / tau))` per "
        "FORTY_YARD_DASH attempt, pools attempts per player with "
        "empirical-Bayes shrinkage, and screens candidate features by ICC and "
        "VIF.",
        "",
        "## Counts",
        "",
        f"- DL players in `players.csv`: {summary['n_players_total']} "
        f"(edge {summary['position_groups'].get('edge', 0)}, "
        f"interior {summary['position_groups'].get('interior', 0)})",
        f"- players with a usable fit: {summary['n_players_with_fits']}",
        f"- attempts used: {summary['n_attempts_used']} "
        f"(of {summary['fit']['n_attempt_series']} series; "
        f"{summary['fit']['n_too_short']} under min_frames, "
        f"{summary['fit']['n_low_r2']} below min_r2)",
        f"- lateral attempts described: {summary['n_lateral_attempts']}",
        "",
        "## Feature screen",
        "",
        f"ICC floor {summary['icc_min']}, VIF ceiling {summary['vif_max']}, "
        f"at most {summary['max_features']} features.",
        "",
        "| feature | ICC (jackknife SE) | players | attempts | excluded | VIF | selected |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    icc_se = summary.get("icc_se", {})
    for feature in summary["eligible_features"] + [
            f for f in summary["ineligible_features"] if f in icc]:
        value = icc.get(feature, float("nan"))
        se_value = icc_se.get(feature, float("nan"))
        counts = summary["feature_counts"].get(feature, {})
        vif = summary["vif"].get(feature)
        if vif is None:
            vif = summary["vif_at_admission"].get(feature)
        icc_cell = (_fmt(value) if not isinstance(se_value, float)
                    or not math.isfinite(se_value)
                    else f"{_fmt(value)} ± {_fmt(se_value)}")
        lines.append(
            f"| {feature} | {icc_cell} | "
            f"{counts.get('n_players', 0)} | "
            f"{counts.get('n_attempts', 0)} | "
            f"{summary['icc_excluded_players'].get(feature, 0)} | "
            f"{_fmt(vif) if vif is not None else 'NA'} | "
            f"{'yes' if feature in summary['selected_features'] else 'no'} |")
    lines += [
        "",
        "VIF is the joint variance inflation of the selected set; for a feature "
        "that was not selected it is the value measured when it was offered "
        "(`inf` = collinear with the set it would have joined).",
        "",
        "Per-feature ICC is computed on attempt-level values from players with "
        "at least `thresholds.min_attempts_for_icc` attempts; excluded players "
        "still receive a shrunken estimate.  The SE is a delete-one-player "
        "jackknife and understates the interval; a player-clustered bootstrap "
        "CI is still pending (D69(5)).",
        "",
        f"Selected: **{', '.join(summary['selected_features']) or 'none'}**.",
    ]
    reasons = {f: r for f, r in summary.get("feature_drop_reasons", {}).items()
               if f not in summary["selected_features"]}
    if reasons:
        lines += ["", "Dropped: " + ", ".join(
            f"`{f}` ({r})" for f, r in sorted(reasons.items())) + "."]
    gate_reason_text = (", ".join(f"`{r}`" for r in summary.get("gate_reasons", []))
                        or "none")
    lines += [
        f"Primary feature `{summary['primary_feature']}`: "
        f"{'selected' if summary['primary_selected'] else 'NOT selected'} "
        f"(feature gate `{summary['feature_gate']}`, reason: {gate_reason_text}). "
        f"The primary is admitted first and is never displaced by a "
        f"higher-ICC alternate. Fallback `{summary['fallback_feature']}`: "
        f"{'appears among the selected alternates' if summary['fallback_selected'] else 'not selected'} "
        "— the fallback is never adopted in place of the primary; that is a "
        "decision for Conor, and `icc_min` is never lowered (D69(5)).",
        "",
        "The screen ranks *alternative operationalisations* of the same fitted "
        "curve (`vmax`, `tau`, `speed_at_1_0`, `speed_at_2_0`, `peak_accel`, "
        "`ten_yd_split`) for the Part 1 specification curve; these are one-per-"
        "model alternatives, never joint regressors (D69(3)).",
        "",
    ]
    for feature, reason in summary["icc_not_estimable"].items():
        lines.append(f"- `{feature}`: ICC not estimable — {reason}.")
    lines += [
        "",
        "## Collinearity: between players, not within",
        "",
        "Between-player correlation of the shrunken estimates (natural scale):",
        "",
        "| pair | r | players |",
        "| --- | --- | --- |",
    ]
    for pair, block in summary["between_player_corr"].items():
        lines.append(f"| {pair.replace('_vs_', ' vs ')} | "
                     f"{_fmt(block.get('r'))} | {block.get('n_players', 0)} |")
    within_reason = next(
        (block["reason"] for block in
         (summary["corr_vmax_a0"], summary["corr_vmax_tau"])
         if not block.get("estimable", True)), "")
    lines += [
        "",
        f"**Within-player correlation is not estimable** for this cohort: "
        f"{within_reason}. With two attempts per player a within-player "
        "correlation is identically ±1 by construction, so the statistic would "
        "report the attempt count, not the data — an artefact, not evidence "
        "(D69(4)). It is therefore not reported as a correlation at all.",
        "",
        "Because `a0 = vmax / tau`, the collinearity among `vmax`, `tau` and "
        "`a0` is arithmetic rather than empirical; the screen reports it but "
        "does not use VIF to arbitrate among them (D69(3)).",
    ]
    lines += [
        "",
        "## Lateral mechanics (exploratory)",
        "",
        "Entry speed (mean over the first "
        f"`ceiling.lateral.entry_window_s`), peak speed, exit speed (mean over "
        "the last `exit_window_s`), total time, and braking distance (path "
        "length from the first speed maximum (the first local maximum) to the "
        "next local minimum once the drop clears `braking_min_drop_yds`).",
        "",
        "| feature | attempts | players | missing |",
        "| --- | --- | --- | --- |",
    ]
    for feature in sorted(summary["lateral_coverage"]):
        cov = summary["lateral_coverage"][feature]
        lines.append(f"| {feature} | {cov.get('n_attempts', 0)} | "
                     f"{cov.get('n_players', 0)} | {cov.get('n_missing', 0)} |")
    lines += [
        "",
        "## Method notes",
        "",
        "- Speed is smoothed with a centred rolling median over "
        "`thresholds.smoothing.window_frames`; speeds above "
        "`thresholds.speed_cap_yds` are replaced by interpolation and counted. "
        "Raw positions are never differentiated.",
        "- Elapsed time is measured from the first recorded frame of a series; "
        "gaps under `thresholds.gap.interp_max_s` are interpolated only "
        "outside the peak-speed window.",
        "- Attempts failing `min_r2` or `min_frames` are excluded and counted, "
        "never repaired.",
        f"- Reported `ten_yd_split` comes from `combine_results`, not from the "
        f"curve. Its cohort-level instrument-noise proxy is "
        f"{_fmt(summary['split_noise_s'])} s.",
        "- Pooling is on the log scale with method-of-moments `tau2`; the SE is "
        "the posterior SD plus the uncertainty in the population mean. The "
        "`vmax`/`a0`/`tau` errors are treated as independent (conservative).",
        "- Lateral mechanics are exploratory and never enter the feature "
        "screen.",
        "",
    ]
    if mode == "sample":
        lines.append("_Sample-mode artefacts are unvalidated sample output and "
                     "are not interpreted as findings._")
    return "\n".join(lines) + "\n"


def run(cfg: dict, mode: str, force: bool = False) -> dict:
    """Build the ceiling table; returns a small outcome mapping."""
    if D.is_done(cfg, STAGE, mode, force=force):
        return {"skipped": True, "stage": STAGE}

    D.assert_disk_space(cfg, mode)
    out_dir = _out_dir(cfg, mode)
    result = core.compute_ceiling_features(cfg)
    rows = result["rows"]
    attempts = result["attempt_rows"]
    summary = result["summary"]

    _check_fields(rows, PLAYER_FIELDS, "player_ceiling")
    _check_fields(attempts, ATTEMPT_FIELDS, "attempt_fits")

    D.write_csv(out_dir / "player_ceiling.csv", _csv_ready(rows), PLAYER_FIELDS,
                mode=mode, cfg=cfg)
    D.write_csv(out_dir / "attempt_fits.csv", _csv_ready(attempts),
                ATTEMPT_FIELDS, mode=mode, cfg=cfg)
    D.write_json(out_dir / "summary.json", {
        "stage": STAGE,
        "mode": mode,
        "n_rows": len(rows),
        "n_attempt_rows": len(attempts),
        **summary,
    }, mode=mode, cfg=cfg)
    D.write_markdown(out_dir / "SUMMARY.md", _markdown(summary, mode),
                     mode=mode, cfg=cfg)
    D.mark_done(cfg, STAGE, mode, extra={
        "n_rows": len(rows),
        "n_players_with_fits": summary["n_players_with_fits"],
        "selected_features": summary["selected_features"],
        "feature_gate": summary["feature_gate"],
    })
    return {"skipped": False, "stage": STAGE, "result": result}


def main(argv=None) -> int:
    args = D.parse_args(argv)
    cfg = D.load_config(args.config)
    D.seed_everything(int(D.dl(cfg)["seeds"]["base"]))
    try:
        with D.timed_stage(cfg, STAGE, args.mode) as st:
            outcome = run(cfg, args.mode, force=args.force)
            if outcome.get("skipped"):
                st.note("skipped: checkpoint current")
                print(f"{STAGE}: up to date (use --force to rebuild)")
                return 0
            summary = outcome["result"]["summary"]
            st.note(f"players={summary['n_players_with_fits']} "
                    f"attempts={summary['n_attempts_used']} "
                    f"selected={','.join(summary['selected_features']) or 'NONE'}")
            print(f"{STAGE}: {summary['n_players_with_fits']} players, "
                  f"{summary['n_attempts_used']} attempts, "
                  f"{summary['n_lateral_attempts']} lateral")
            for feature, value in summary["icc"].items():
                print(f"  ICC {feature}: {_fmt(value, 4)}")
            print(f"  selected: {summary['selected_features']}")
            if not summary["primary_selected"]:
                # Reported, not fatal: the decision is Conor's (dl-design §8).
                print(f"WARNING: feature gate {summary['feature_gate']}: "
                      f"primary feature {summary['primary_feature']!r} did not "
                      f"clear the ICC floor "
                      f"({', '.join(summary['gate_reasons']) or 'no reason'}). "
                      f"Fallback {summary['fallback_feature']!r} is NOT "
                      f"adopted automatically.", file=sys.stderr)
            else:
                print(f"  feature gate: {summary['feature_gate']} "
                      f"({summary['primary_feature']} admitted first)")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
