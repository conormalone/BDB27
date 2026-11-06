"""notebook_report — presentation helpers for ``notebooks/bdb27_full_run.ipynb``.

This module contains **no analysis**. It reads artefacts that the stage chain
already wrote and formats them for a human; it never estimates, adjusts,
substitutes or zero-fills a quantity. Every missing file or field is reported as
``NOT RUN — <field> absent`` so a reader can tell "not built" from "built and
empty". The notebook stays a thin interface: config, stage loop, and calls to
these renderers.

Two provenance rules are enforced here, not in prose:

* the headline banner is derived from the artefact's own ``provenance`` key;
  ``PLACEHOLDER_SAMPLE`` is loud and says the numbers are not a result;
* figures are rendered only when the inputs are ``FULL``. In sample mode, or
  when an input is missing, each figure prints why it was skipped.

The functions are pure-ish (files in, strings/figures out) so the contract is
unit-testable without a Jupyter kernel.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

GROUPS = ("pooled", "edge", "interior")
GROUP_TITLES = {"pooled": "POOLED (all DL)",
                "edge": "EDGE",
                "interior": "INTERIOR"}

BANNER_CHAR = "#"
BANNER_WIDTH = 96

MISSING_PREFIX = "NOT RUN — "

#: Human names for the convergence-ladder rungs written by 04_models.
RUNG_NAMES = {
    0: "rung_0_regression_calibration",
    1: "rung_1_no_me_correction",
    2: "rung_2_empirical_bayes",
    3: "rung_3_random_intercept_only",
}


# --------------------------------------------------------------------------- #
# small formatting helpers
# --------------------------------------------------------------------------- #
def read_json(path: str | Path) -> dict | None:
    """Read a JSON artefact. ``None`` means absent or unreadable — never a guess."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def provenance_kind(payload: dict | None) -> str:
    """Classify an artefact's provenance without upgrading it.

    Returns ``"SAMPLE"`` for the placeholder stamp, ``"FULL"`` for a full-run
    stamp, and ``"UNKNOWN"`` for anything else (including a missing stamp).
    """
    raw = str((payload or {}).get("provenance", "") or "")
    if "UNVALIDATED SAMPLE OUTPUT" in raw or raw.startswith("PLACEHOLDER_SAMPLE"):
        return "SAMPLE"
    if raw == "FULL":
        return "FULL"
    return "UNKNOWN"


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _num(value: Any, fmt: str = "{:.3f}") -> str:
    if _is_missing(value):
        return MISSING_PREFIX + "value absent"
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _ci(payload: dict, key: str) -> str:
    """Render a ``[low, high]`` interval, or an explicit absence note."""
    interval = (payload or {}).get(key)
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        return f"(95% CI {MISSING_PREFIX}{key} absent)"
    lo, hi = interval
    if _is_missing(lo) or _is_missing(hi):
        return f"(95% CI {MISSING_PREFIX}{key} absent)"
    return f"95% CI [{float(lo):.3f}, {float(hi):.3f}]"


def _banner_lines(messages: list[str], width: int = BANNER_WIDTH) -> list[str]:
    out = [BANNER_CHAR * width]
    for text in messages:
        pad = max(0, width - 4 - len(text))
        out.append(f"{BANNER_CHAR} {text}{' ' * pad} {BANNER_CHAR}")
    out.append(BANNER_CHAR * width)
    return out


# --------------------------------------------------------------------------- #
# headline
# --------------------------------------------------------------------------- #
def _group_block(label: str, group: dict, qualifier: str) -> list[str]:
    title = GROUP_TITLES.get(label, label.upper())
    lines = [f"{title}{qualifier}"]

    if not isinstance(group, dict):
        lines.append(f"  {MISSING_PREFIX}{label} absent")
        return lines

    status = group.get("status")
    if status and status != "OK":
        lines.append(f"  {MISSING_PREFIX}{label} status {status}")
        return lines

    lines.append("  coverage               : "
                 f"{_num(group.get('n_reps'), '{:.0f}')} reps · "
                 f"{_num(group.get('n_players'), '{:.0f}')} players · "
                 f"{_num(group.get('n_team_games'), '{:.0f}')} team-games · "
                 f"min {_num(group.get('min_reps_per_player'), '{:.0f}')} reps/player")

    if _is_missing(group.get("b")):
        lines.append(f"  b (phys coefficient)   : {MISSING_PREFIX}b absent")
    else:
        lines.append(f"  b (phys coefficient)   : {_num(group.get('b'))}  "
                     f"{_ci(group.get('bootstrap') or {}, 'b_ci95')}")

    boot = group.get("bootstrap") or {}
    lines.append("  between-player variance split")
    for field, label_text in (("share_phys", "phys"),
                              ("share_u", "jump (u)")):
        if _is_missing(group.get(field)):
            lines.append(f"    {label_text:<20} : {MISSING_PREFIX}{field} absent")
        else:
            lines.append(f"    {label_text:<20} : {_num(group.get(field))}  "
                         f"{_ci(boot, field + '_ci95')}")

    lines.append("  total variance split")
    for field, label_text in (("share_phys_total", "phys"),
                              ("share_u_total", "jump (u)")):
        if _is_missing(group.get(field)):
            lines.append(f"    {label_text:<20} : {MISSING_PREFIX}{field} absent")
        else:
            lines.append(f"    {label_text:<20} : {_num(group.get(field))}")
    if _is_missing(group.get("within_player_remainder_total")):
        lines.append("    within-player remainder: "
                     f"{MISSING_PREFIX}within_player_remainder_total absent")
    else:
        lines.append("    within-player remainder: "
                     f"{_num(group.get('within_player_remainder_total'))}  "
                     f"{_ci(boot, 'within_player_remainder_total_ci95')}")

    if _is_missing(group.get("jump_sd_ms")):
        lines.append(f"  jump SD                : {MISSING_PREFIX}jump_sd_ms absent")
    else:
        lines.append(f"  jump SD                : {_num(group.get('jump_sd_ms'), '{:.1f}')} ms")

    rel = group.get("reliability") or {}
    if not isinstance(rel, dict) or not rel:
        lines.append(f"  reliability of u       : {MISSING_PREFIX}reliability absent")
    else:
        sh = rel.get("split_half") or {}
        mb = rel.get("model_based") or {}
        lines.append("  reliability of u       : "
                     f"split-half r={_num(sh.get('r_half'), '{:.2f}')} "
                     f"(Spearman-Brown {_num(sh.get('spearman_brown'), '{:.2f}')}, "
                     f"n={_num(sh.get('n_players'), '{:.0f}')}); "
                     f"model-based {_num(mb.get('reliability'), '{:.2f}')}")

    agr = group.get("agreement") or {}
    if not isinstance(agr, dict) or not agr:
        lines.append(f"  agreement with latency : {MISSING_PREFIX}agreement absent")
    else:
        u = agr.get("u_vs_onset_latency") or {}
        p = agr.get("phys_vs_onset_to_cross") or {}
        lines.append("  agreement with latency : "
                     f"u vs onset latency r={_num(u.get('r'), '{:.2f}')} "
                     f"(n={_num(u.get('n_players'), '{:.0f}')}); "
                     f"phys vs onset-to-cross r={_num(p.get('r'), '{:.2f}')} "
                     f"(n={_num(p.get('n_players'), '{:.0f}')})")

    if _is_missing(group.get("rung_used")):
        lines.append(f"  rung used              : {MISSING_PREFIX}rung_used absent")
    else:
        rung = int(group["rung_used"])
        lines.append(f"  rung used              : {rung} "
                     f"({RUNG_NAMES.get(rung, 'unknown rung')})")

    boundary = group.get("boundary")
    if not isinstance(boundary, dict):
        lines.append(f"  boundary estimates     : {MISSING_PREFIX}boundary absent")
    else:
        flags = []
        if "sigma_u_zero" in boundary:
            flags.append(f"sigma_u at zero={boundary['sigma_u_zero']}")
        if "between_phys_var_zero" in boundary:
            flags.append("between-phys variance at zero="
                         f"{boundary['between_phys_var_zero']}")
        lines.append("  boundary estimates     : "
                     + ("; ".join(flags) if flags else "flags absent")
                     + " — boundary estimates, reported as they are; a zero share "
                       "stays zero and is not replaced by the naive rung")

    y2y = group.get("year_to_year")
    if not isinstance(y2y, dict):
        lines.append(f"  year to year           : {MISSING_PREFIX}year_to_year absent")
    else:
        lines.append("  year to year           : "
                     f"status {y2y.get('status', 'absent')} · "
                     f"{_num(y2y.get('n_pairs'), '{:.0f}')} pairs")

    lines.append("")
    return lines


def render_headline(outputs_dir: str | Path, mode: str = "sample") -> str:
    """Render the headline block from ``outputs/phase4/decomposition.json``.

    Never raises for a missing or malformed artefact: the absence is the report.
    In sample mode every number is printed under a loud ``NOT A RESULT`` banner
    and each group title repeats the qualifier, so a diagnostic cannot be read as
    a finding.
    """
    outputs = Path(outputs_dir)
    path = outputs / "phase4" / "decomposition.json"
    art = read_json(path)

    lines: list[str] = []
    lines.append("HEADLINE — burst vs. jump: the get-off variance decomposition")
    lines.append("=" * BANNER_WIDTH)

    if art is None:
        lines.append(f"{MISSING_PREFIX}outputs/phase4/decomposition.json absent")
        lines.append("  The decomposition has not been produced on this data. No "
                     "stage number is")
        lines.append("  substituted, and no sample value stands in for it.")
        lines.append("")
        lines.append("  To produce it: run 02_combine_physical_time, then "
                     "03_game_snaps, then 04_models")
        lines.append("  (the stage table in §4 of this notebook reports each one).")
        return "\n".join(lines)

    kind = provenance_kind(art)
    raw_prov = str(art.get("provenance", "") or "")
    lines.append("PROVENANCE — read from the artefact itself")
    lines.append(f"  provenance : {raw_prov or MISSING_PREFIX + 'provenance absent'}")
    lines.append("")

    sample = kind != "FULL"
    if mode == "full" and kind != "FULL":
        lines.append("NOTE: MODE='full' but this artefact is not a FULL artefact; it was "
                     "not rebuilt in this run.")
        lines.append("")
    if kind == "SAMPLE":
        lines.extend(_banner_lines([
            "UNVALIDATED SAMPLE OUTPUT — NOT A RESULT",
            "",
            "Every number below is a sample diagnostic on truncated data.",
            "It may not be quoted, interpreted, or compared with the prereg.",
            "Set MODE=\"full\" and run on the full tracking files for a result.",
        ]))
        qualifier = " — SAMPLE DIAGNOSTIC, NOT A RESULT"
    elif kind == "FULL":
        lines.append("PROVENANCE: FULL — final-run artefact")
        qualifier = ""
    else:
        lines.extend(_banner_lines([
            "PROVENANCE NOT RECOGNISED — NOT A RESULT",
            "The artefact carries no FULL stamp, so no number is a finding.",
        ]))
        qualifier = " — PROVENANCE NOT RECOGNISED, NOT A RESULT"
    lines.append("")

    est = art.get("estimator")
    lines.append("MODEL (from the artefact)")
    if not isinstance(est, dict) or not est:
        lines.append(f"  {MISSING_PREFIX}estimator absent")
    else:
        for key in ("name", "measurement_error", "intervals", "bayesian_fit"):
            if _is_missing(est.get(key)):
                lines.append(f"  {key:<17}: {MISSING_PREFIX}{key} absent")
            else:
                lines.append(f"  {key:<17}: {est[key]}")
    me = art.get("measurement_error")
    if isinstance(me, dict):
        lines.append(f"  shared error     : {me.get('shared_error_variance', MISSING_PREFIX + 'shared_error_variance absent')}")
    lines.append("")

    for label in GROUPS:
        lines.extend(_group_block(label, art.get(label), qualifier))

    res = art.get("resolution")
    lines.append("RESOLUTION LIMIT (from the artefact's `resolution` block)")
    if not isinstance(res, dict) or not res:
        lines.append(f"  {MISSING_PREFIX}resolution absent")
    else:
        lines.append("  provenance               : "
                     f"{res.get('provenance', MISSING_PREFIX + 'resolution.provenance absent')}")
        lines.append("  smallest detectable jump SD: "
                     f"{_num(res.get('smallest_detectable_jump_sd_ms_at_20_reps'), '{:.1f}')} ms "
                     "at the 20-rep cut; "
                     f"{_num(res.get('smallest_detectable_jump_sd_ms_best_case'), '{:.1f}')} ms "
                     "best case")
        statement = res.get("statement")
        if _is_missing(statement):
            lines.append(f"  statement                : {MISSING_PREFIX}resolution.statement absent")
        else:
            lines.append(f"  statement                : {statement}")
        lines.append("  This is a resolution limit, not a pass/fail threshold. No "
                     "smallest-effect-of-")
        lines.append("  interest test is applied (Conor 2026-11-05 22:16; frozen prereg).")
    lines.append("")

    interp = art.get("phys_interpolation_status")
    lines.append("PHYS INTERPOLATION (coverage counter — not a result)")
    if not isinstance(interp, dict) or not interp:
        lines.append(f"  {MISSING_PREFIX}phys_interpolation_status absent")
    else:
        lines.append("  " + " · ".join(f"{k} {v}" for k, v in interp.items()))
    lines.append("")

    if sample:
        lines.extend(_banner_lines(["END OF SAMPLE DIAGNOSTICS — NOT A RESULT"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# validation (Phase 5)
# --------------------------------------------------------------------------- #
#: Detector names written by 05_validate, in display order, with the block in
#: ``synthetic_can_fail`` that holds the expected detector text.
DETECTORS = (
    ("permutation", "permutation"),
    ("placebo", "placebo"),
    ("mechanical_depth_alignment", "mechanical", "depth_alignment_world"),
    ("mechanical_onset", "mechanical", "onset_world"),
    ("snap_quality", "snap_quality"),
    ("selection", "selection"),
)


def _share_text(block: Any) -> str:
    """``b`` / ``share_phys`` on one line, naming anything absent."""
    if not isinstance(block, dict):
        return MISSING_PREFIX + "value absent"
    parts = []
    for key, fmt in (("b", "{:.3f}"), ("share_phys", "{:.3f}")):
        parts.append(f"{key}={_num(block.get(key), fmt)}")
    for key in ("n_reps", "n_players"):
        if key in block:
            parts.append(f"{key}={_num(block.get(key), '{:.0f}')}")
    return " ".join(parts)


def _block_title(index: int, text: str) -> list[str]:
    return [f"{index}. {text}", "-" * BANNER_WIDTH]


def _render_permutation(perm: Any) -> list[str]:
    lines = _block_title(1, "PERMUTATION — shuffle physical time across players within position")
    if not isinstance(perm, dict) or not perm:
        lines.append(f"  {MISSING_PREFIX}checks.permutation absent")
        lines.append("")
        return lines
    if perm.get("status") not in (None, "OK"):
        lines.append(f"  {MISSING_PREFIX}checks.permutation status {perm.get('status')}")
    observed = perm.get("observed") or {}
    lines.append(f"  observed physical share  : {_num(observed.get('share_phys'))}")
    dist = perm.get("permutation_share_phys") or {}
    if not isinstance(dist, dict) or not dist:
        lines.append(f"  permutation distribution : {MISSING_PREFIX}permutation_share_phys absent")
    else:
        lines.append(
            "  permutation distribution : "
            f"n={_num(dist.get('n'), '{:.0f}')} "
            f"mean={_num(dist.get('mean'))} sd={_num(dist.get('sd'))} "
            f"[{_num(dist.get('p2_5'))}, {_num(dist.get('p97_5'))}] "
            f"(min {_num(dist.get('min'))}, max {_num(dist.get('max'))})")
    p = perm.get("one_sided_monte_carlo_p")
    if _is_missing(p):
        lines.append(f"  one-sided Monte-Carlo p  : {MISSING_PREFIX}one_sided_monte_carlo_p absent")
    else:
        lines.append(f"  one-sided Monte-Carlo p  : {_num(p, '{:.3f}')}  "
                     f"(n_resamples={_num(perm.get('n_requested'), '{:.0f}')}, "
                     f"n_ok={_num(perm.get('n_ok'), '{:.0f}')})")
    lines.append(f"  observed percentile      : "
                 f"{_num(perm.get('observed_share_phys_percentile'), '{:.1f}')}")
    boundary = (observed.get("boundary") or {}) if isinstance(observed, dict) else {}
    if boundary.get("between_phys_var_zero") is True:
        lines.append("  UNINFORMATIVE ON THIS DATA: the observed physical share is at the")
        lines.append("    zero boundary, so it cannot fall further and the permutation")
        lines.append("    cannot move it. Reported as uninformative — NOT as a pass.")
    reading = perm.get("reading")
    if _is_missing(reading):
        lines.append(f"  reading                  : {MISSING_PREFIX}reading absent")
    else:
        lines.append(f"  reading                  : {reading}")
    lines.append("")
    return lines


def _render_placebo(plac: Any) -> list[str]:
    lines = _block_title(2, "PLACEBO — height and weight alone should not reproduce the effect")
    if not isinstance(plac, dict) or not plac:
        lines.append(f"  {MISSING_PREFIX}checks.placebo absent")
        lines.append("")
        return lines
    if plac.get("status") not in (None, "OK"):
        lines.append(f"  {MISSING_PREFIX}checks.placebo status {plac.get('status')}")
    real = plac.get("real") or {}
    fake = plac.get("placebo") or {}
    lines.append(f"  real physical share      : {_share_text(real)}")
    lines.append(f"  height/weight share      : {_share_text(fake)}")
    lines.append("  placebo minus real       : "
                 f"{_num(plac.get('placebo_minus_real_share_phys'))}")
    reg = plac.get("height_weight_regression") or {}
    if isinstance(reg, dict) and reg:
        lines.append(f"  height/weight R²         : {_num(reg.get('r2'), '{:.3f}')}")
    reading = plac.get("reading")
    if _is_missing(reading):
        lines.append(f"  reading                  : {MISSING_PREFIX}reading absent")
    else:
        lines.append(f"  reading                  : {reading}")
    lines.append("")
    return lines


def _render_mechanical(mech: Any) -> list[str]:
    lines = _block_title(3, "MECHANICAL — depth, alignment, onset threshold, stance")
    if not isinstance(mech, dict) or not mech:
        lines.append(f"  {MISSING_PREFIX}checks.mechanical absent")
        lines.append("")
        return lines
    for key, label in (("base", "base"),
                       ("depth_removed", "depth removed"),
                       ("alignment_removed", "alignment removed"),
                       ("both_removed", "both removed")):
        lines.append(f"  {label:<24} : {_share_text(mech.get(key))}")
    onset = mech.get("onset") or {}
    if not isinstance(onset, dict) or not onset:
        lines.append(f"  onset sweep              : {MISSING_PREFIX}mechanical.onset absent")
    else:
        grid = onset.get("grid") or []
        lines.append("  onset sweep              :")
        lines.append(f"    {'onset_yds':>9} {'reps':>6} {'b':>8} {'share_phys':>11} {'jump_sd_ms':>11}  status")
        if not grid:
            lines.append(f"    {MISSING_PREFIX}grid absent")
        for row in grid:
            if not isinstance(row, dict):
                lines.append(f"    {MISSING_PREFIX}grid row malformed")
                continue
            lines.append(
                f"    {_num(row.get('onset_speed_yds'), '{:.2f}'):>9} "
                f"{_num(row.get('n_reps_refit'), '{:.0f}'):>6} "
                f"{_num(row.get('b'), '{:.3f}'):>8} "
                f"{_num(row.get('share_phys')):>11} "
                f"{_num(row.get('jump_sd_ms'), '{:.1f}'):>11}  "
                f"{row.get('status', 'absent')}")
        lines.append("    span across the sweep  : "
                     f"share_phys {_num(onset.get('span_share_phys'))}, "
                     f"b {_num(onset.get('span_b'), '{:.3f}')}")
    stance = mech.get("stance") or {}
    if not isinstance(stance, dict) or not stance:
        lines.append(f"  stance subsets           : {MISSING_PREFIX}mechanical.stance absent")
    else:
        lines.append(f"  stance subsets           : {stance.get('status', MISSING_PREFIX + 'status absent')}"
                     f" — {stance.get('reason', MISSING_PREFIX + 'reason absent')}")
    reading = mech.get("reading")
    if _is_missing(reading):
        lines.append(f"  reading                  : {MISSING_PREFIX}reading absent")
    else:
        lines.append(f"  reading                  : {reading}")
    lines.append("")
    return lines


def _render_snap_quality(qc: Any) -> list[str]:
    lines = _block_title(4, "SNAP QUALITY — restrict to snaps whose onset was detected cleanly")
    if not isinstance(qc, dict) or not qc:
        lines.append(f"  {MISSING_PREFIX}checks.snap_quality absent")
        lines.append("")
        return lines
    if qc.get("status") not in (None, "OK"):
        lines.append(f"  {MISSING_PREFIX}checks.snap_quality status {qc.get('status')}")
    for key, label in (("fitted_set", "fitted set"),
                       ("tracked_primary_set", "tracked primary set")):
        block = qc.get(key) or {}
        if not isinstance(block, dict) or not block:
            lines.append(f"  {label:<24} : {MISSING_PREFIX}{key} absent")
            continue
        lines.append(f"  {label:<24} : {_num(block.get('n_reps'), '{:.0f}')} reps → "
                     f"{_num(block.get('n_clean'), '{:.0f}')} clean, "
                     f"dropped {_num(block.get('n_dropped'), '{:.0f}')} "
                     f"({_num(block.get('share_dropped'))})")
        dropped_by = block.get("dropped_by_status")
        if isinstance(dropped_by, dict) and dropped_by:
            lines.append("    " + " · ".join(f"{k} {v}" for k, v in dropped_by.items()))
    lines.append(f"  shares, all → clean     : "
                 f"share_phys {_num((qc.get('base') or {}).get('share_phys'))} → "
                 f"{_num((qc.get('clean') or {}).get('share_phys'))} · "
                 f"share_u {_num((qc.get('base') or {}).get('share_u'))} → "
                 f"{_num((qc.get('clean') or {}).get('share_u'))} · "
                 f"b {_num((qc.get('base') or {}).get('b'), '{:.3f}')} → "
                 f"{_num((qc.get('clean') or {}).get('b'), '{:.3f}')}")
    reading = qc.get("reading")
    if _is_missing(reading):
        lines.append(f"  reading                  : {MISSING_PREFIX}reading absent")
    else:
        lines.append(f"  reading                  : {reading}")
    lines.append("")
    return lines


def _render_selection(sel: Any) -> list[str]:
    lines = _block_title(5, "SELECTION — do the shares depend on how many snaps a player played?")
    if not isinstance(sel, dict) or not sel:
        lines.append(f"  {MISSING_PREFIX}checks.selection absent")
        lines.append("")
        return lines
    if sel.get("status") not in (None, "OK"):
        lines.append(f"  {MISSING_PREFIX}checks.selection status {sel.get('status')}")
    lines.append(f"  snap counts              : {_num(sel.get('n_players'), '{:.0f}')} players, "
                 f"{_num(sel.get('n_reps'), '{:.0f}')} reps; max/min reps = "
                 f"{_num(sel.get('max_over_min_reps'), '{:.2f}')}; top player "
                 f"{_num(sel.get('share_of_reps_top_player'))} of reps")
    un_w = sel.get("unweighted") or {}
    eq_w = sel.get("equal_snap_weighted") or {}
    lines.append(f"  unweighted               : {_share_text(un_w)}")
    boot = (eq_w.get("bootstrap") or {}) if isinstance(eq_w, dict) else {}
    lines.append(f"  equal-snap weighted      : {_share_text(eq_w)}  "
                 f"{_ci(boot, 'b_ci95')}")
    lines.append("  difference (equal minus unweighted) : "
                 f"{_num(sel.get('equal_snap_minus_unweighted_share_phys'))}")
    draftee = sel.get("draftee_selection") or {}
    if not isinstance(draftee, dict) or not draftee:
        lines.append(f"  draftee selection        : {MISSING_PREFIX}draftee_selection absent")
    else:
        lines.append(f"  draftee selection        : {draftee.get('status', MISSING_PREFIX + 'status absent')}"
                     f" — {draftee.get('reason', MISSING_PREFIX + 'reason absent')}")
    lines.append("")
    return lines


def _render_resolution(res: Any) -> list[str]:
    lines = _block_title(6, "RESOLUTION — a statement, not a pass/fail threshold")
    if not isinstance(res, dict) or not res:
        lines.append(f"  {MISSING_PREFIX}resolution absent")
        lines.append("")
        return lines
    statement = res.get("statement")
    if _is_missing(statement):
        lines.append(f"  statement                : {MISSING_PREFIX}resolution.statement absent")
    else:
        lines.append(f"  statement                : {statement}")
    lines.append("  smallest detectable jump SD, realised sample : "
                 f"{_num(res.get('smallest_detectable_jump_sd_ms_realised'), '{:.1f}')} ms")
    ref = res.get("prereg_reference") or {}
    lines.append("  prereg reference         : "
                 f"{_num(ref.get('smallest_detectable_jump_sd_ms_at_20_reps'), '{:.1f}')} ms "
                 "at the 20-rep cut; "
                 f"{_num(ref.get('smallest_detectable_jump_sd_ms_best_case'), '{:.1f}')} ms best case")
    lines.append("")
    return lines


def _render_detectors(can_fail: Any) -> list[str]:
    lines = _block_title(7, "CAN THIS CHECK FAIL? — synthetic worlds in which each check MUST fire")
    if not isinstance(can_fail, dict) or not can_fail:
        lines.append(f"  {MISSING_PREFIX}synthetic_can_fail absent")
        lines.append("")
        return lines
    purpose = can_fail.get("purpose")
    if not _is_missing(purpose):
        lines.append(f"  purpose                  : {purpose}")
    lines.append(f"  all detectors fired      : {can_fail.get('all_detectors_fired', 'absent')}")
    lines.append(f"  {'detector':<30} {'detector_fired':>14}  expected detector")
    detectors = can_fail.get("detectors") or {}
    for entry in DETECTORS:
        name = entry[0]
        block: Any = can_fail
        for key in entry[1:]:
            block = (block or {}).get(key) if isinstance(block, dict) else None
        if not isinstance(detectors, dict):
            lines.append(f"  {MISSING_PREFIX}synthetic_can_fail.detectors absent")
            break
        fired = detectors.get(name)
        if name not in detectors:
            lines.append(f"  {name:<30} {MISSING_PREFIX}detector_fired absent")
            continue
        expected = (block or {}).get("expected_detector") if isinstance(block, dict) else None
        lines.append(f"  {name:<30} {str(fired):>14}  "
                     f"{expected if not _is_missing(expected) else MISSING_PREFIX + 'expected_detector absent'}")
    if isinstance(detectors, dict):
        for name, fired in detectors.items():
            if name not in {e[0] for e in DETECTORS}:
                lines.append(f"  {name:<30} {str(fired):>14}  (unmapped detector)")
    lines.append("  A check that cannot fail manufactures confidence; every row above is the same ")
    lines.append("  code detecting an effect it was given. False is reported, not hidden.")
    lines.append("")
    return lines


def render_validation(outputs_dir: str | Path, mode: str = "sample") -> str:
    """Render the Phase 5 validation block from ``outputs/validation/validation.json``.

    Presentation only: every number, banner and reading string comes from the
    artefact. A missing file or field is reported as ``NOT RUN — <field>
    absent``; no value is invented. In sample mode the whole block sits under
    the loud sample banner, so a diagnostic cannot be read as validation of a
    real result.
    """
    outputs = Path(outputs_dir)
    art = read_json(outputs / "validation" / "validation.json")

    lines = ["VALIDATION — Phase 5 checks on the decomposition", "=" * BANNER_WIDTH]
    if art is None:
        lines.append(f"{MISSING_PREFIX}outputs/validation/validation.json absent")
        lines.append("  Phase 5 has not run on this data. No check is reported as "
                     "passed, and no")
        lines.append("  sample value stands in for it.")
        return "\n".join(lines)

    kind = provenance_kind(art)
    raw_prov = str(art.get("provenance", "") or "")
    lines.append("PROVENANCE — read from the artefact itself")
    lines.append(f"  provenance : {raw_prov or MISSING_PREFIX + 'provenance absent'}")
    lines.append(f"  stage      : {art.get('stage', MISSING_PREFIX + 'stage absent')}")
    lines.append("")
    if mode == "full" and kind != "FULL":
        lines.append("NOTE: MODE='full' but this artefact is not a FULL artefact; it was "
                     "not rebuilt in this run.")
        lines.append("")
    if kind == "SAMPLE":
        lines.extend(_banner_lines([
            "UNVALIDATED SAMPLE OUTPUT — NOT A RESULT",
            "",
            "Every number below is a sample diagnostic on truncated data.",
            "It may not be quoted, interpreted, or compared with the prereg.",
        ]))
        qualifier = "SAMPLE DIAGNOSTIC, NOT A RESULT"
    elif kind == "FULL":
        lines.append("PROVENANCE: FULL — final-run artefact")
        qualifier = ""
    else:
        lines.extend(_banner_lines([
            "PROVENANCE NOT RECOGNISED — NOT A RESULT",
            "The artefact carries no FULL stamp, so no number is a finding.",
        ]))
        qualifier = "PROVENANCE NOT RECOGNISED, NOT A RESULT"
    lines.append("")
    if qualifier:
        lines.append(f"  Every row below is a {qualifier}.")
        lines.append("")

    if art.get("all_detectors_fired") is not True:
        lines.append("NOTE: not every synthetic detector fired — the corresponding check ")
        lines.append("      above may not be able to fail on this data.")
        lines.append("")

    checks = art.get("checks")
    if not isinstance(checks, dict) or not checks:
        checks = {}
        lines.append(f"{MISSING_PREFIX}checks absent")
        lines.append("")

    for renderer, key in ((_render_permutation, "permutation"),
                          (_render_placebo, "placebo"),
                          (_render_mechanical, "mechanical"),
                          (_render_snap_quality, "snap_quality"),
                          (_render_selection, "selection")):
        lines.extend(renderer(checks.get(key)))

    lines.extend(_render_resolution(art.get("resolution")))
    lines.extend(_render_detectors(art.get("synthetic_can_fail")))

    det = art.get("determinism") or {}
    if isinstance(det, dict) and det:
        lines.append("DETERMINISM")
        lines.append("-" * BANNER_WIDTH)
        lines.append(f"  permutation seed         : {det.get('permutation_seed', MISSING_PREFIX + 'permutation_seed absent')}")
        lines.append(f"  synthetic seed           : {det.get('synthetic_seed', MISSING_PREFIX + 'synthetic_seed absent')}")
        lines.append("")

    if kind == "SAMPLE":
        lines.extend(_banner_lines(["END OF SAMPLE DIAGNOSTICS — NOT A RESULT"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def _fig_skip(name: str, why: str) -> str:
    return f"{name}: SKIPPED — {why}"



class FigureInputMissing(RuntimeError):
    """A named input is absent, so the figure is skipped — never plotted blank."""


def _need(block: Any, key: str, where: str) -> Any:
    """Fetch a required field or raise with its full name."""
    if not isinstance(block, dict):
        raise FigureInputMissing(f"{where} block absent")
    if key not in block or block[key] is None:
        raise FigureInputMissing(f"{where}.{key} absent")
    value = block[key]
    if isinstance(value, float) and not math.isfinite(value):
        raise FigureInputMissing(f"{where}.{key} is non-finite")
    return value


def _need_num(block: Any, key: str, where: str) -> float:
    value = _need(block, key, where)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FigureInputMissing(f"{where}.{key} is not a number ({value!r})") from None
    if not math.isfinite(number):
        raise FigureInputMissing(f"{where}.{key} is non-finite")
    return number


def _need_ci(block: Any, key: str, where: str) -> tuple[float, float]:
    value = _need(block, key, where)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise FigureInputMissing(f"{where}.{key} is not a two-number interval")
    try:
        lo, hi = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        raise FigureInputMissing(f"{where}.{key} is not a two-number interval") from None
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise FigureInputMissing(f"{where}.{key} is non-finite")
    return lo, hi


def _need_columns(frame, columns: list[str], where: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise FigureInputMissing(f"{where} column(s) absent: {', '.join(missing)}")


def _load_snaps(path: Path, columns: list[str], where: str):
    import pandas as pd

    if not path.exists():
        raise FigureInputMissing(f"{where} absent")
    try:
        return pd.read_parquet(path, columns=columns)
    except Exception as exc:
        raise FigureInputMissing(
            f"{where} unreadable ({type(exc).__name__}: {exc})") from None


def _render_figures_impl(outputs: Path, figures: Path,
                         dpi: int) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    messages: list[str] = []
    paths: list[str] = []
    records: list[dict[str, Any]] = []

    figures.mkdir(parents=True, exist_ok=True)
    art = read_json(outputs / "phase4" / "decomposition.json")
    if art is None:
        raise FigureInputMissing("outputs/phase4/decomposition.json absent")

    def save(name: str, fig, ax) -> Path:
        """Save, record finite limits and pixel dimensions, close."""
        xlim = tuple(float(v) for v in ax.get_xlim())
        ylim = tuple(float(v) for v in ax.get_ylim())
        if not all(math.isfinite(v) for v in xlim + ylim):
            raise FigureInputMissing(f"{name} produced non-finite axis limits {xlim} {ylim}")
        width_px, height_px = (int(round(v * dpi)) for v in fig.get_size_inches())
        out = figures / f"{name}.png"
        fig.tight_layout()
        fig.savefig(out, dpi=dpi)
        plt.close(fig)
        records.append({"name": name, "path": str(out), "dpi": dpi,
                        "width_px": width_px, "height_px": height_px,
                        "xlim": list(xlim), "ylim": list(ylim)})
        paths.append(str(out))
        messages.append(f"figure {len(records)}: wrote {out}")
        return out

    # ---- Figure 1: variance split with intervals, edge vs interior ----------
    try:
        labels = ["edge", "interior"]
        comps = [("share_phys", "phys", "#4c72b0"),
                 ("share_u", "jump (u)", "#dd8452")]
        fig, ax = plt.subplots(figsize=(8, 4.2))
        x = np.arange(len(labels))
        width = 0.36
        for offset, (field, name, colour) in zip((-width / 2, width / 2), comps):
            vals, los, his = [], [], []
            for label in labels:
                grp = art.get(label)
                if not isinstance(grp, dict):
                    raise FigureInputMissing(f"decomposition[{label!r}] block absent")
                boot = grp.get("bootstrap")
                vals.append(_need_num(grp, field, label))
                lo, hi = _need_ci(boot, field + "_ci95", f"{label}.bootstrap")
                los.append(lo)
                his.append(hi)
            vals = np.asarray(vals)
            err = np.vstack([vals - np.asarray(los), np.asarray(his) - vals])
            ax.bar(x + offset, vals, width, label=name, color=colour,
                   yerr=err, capsize=4)
        ax.set_xticks(x, [l.title() for l in labels])
        ax.set_ylabel("share of between-player variance")
        ax.set_ylim(0, 1.05)
        ax.set_title("Between-player get-off variance: phys vs jump, 95% intervals")
        ax.legend(frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
        save("fig1_variance_split", fig, ax)
    except Exception as exc:
        messages.append(_fig_skip("figure 1", f"{type(exc).__name__}: {exc}"))

    # ---- player-level inputs, loaded once and named on failure -------------
    cache: dict[str, Any] = {}

    def player_inputs():
        if "value" in cache:
            value = cache["value"]
            if isinstance(value, Exception):
                raise value
            return value
        try:
            snaps = _load_snaps(outputs / "phase3" / "snaps.parquet",
                                ["nfl_id", "depth_yd", "cross_time_s",
                                 "analysis_ready", "in_primary_set", "provenance"],
                                "phase3/snaps.parquet")
            _need_columns(snaps, ["nfl_id", "depth_yd", "cross_time_s",
                                  "analysis_ready", "in_primary_set", "provenance"],
                          "snaps.parquet")
            phys_path = outputs / "phase2" / "physical_time_by_player.csv"
            if not phys_path.exists():
                raise FigureInputMissing("phase2/physical_time_by_player.csv absent")
            phys = pd.read_csv(phys_path)
            _need_columns(phys, ["nfl_id", "display_name", "distance_yd",
                                 "posterior_mean_s", "provenance"],
                          "physical_time_by_player.csv")
            for frame, where in ((snaps, "snaps.parquet"),
                                 (phys, "physical_time_by_player.csv")):
                stamps = sorted(set(frame["provenance"].astype(str).unique()))
                if stamps != ["FULL"]:
                    raise FigureInputMissing(
                        f"{where} is not FULL provenance (found {stamps})")
        except Exception as exc:
            cache["value"] = exc
            raise
        cache["value"] = (snaps, phys)
        return cache["value"]

    # ---- Figure 2: phys time vs observed get-off, named off-diagonal --------
    try:
        snaps, phys = player_inputs()
        work = snaps[(snaps["in_primary_set"] == True) &               # noqa: E712
                     (snaps["analysis_ready"] == True) &               # noqa: E712
                     snaps["cross_time_s"].notna() &
                     snaps["depth_yd"].notna()]
        obs = (work.groupby("nfl_id")
                    .agg(observed_s=("cross_time_s", "mean"),
                         depth_yd=("depth_yd", "mean"),
                         n=("cross_time_s", "size"))
                    .reset_index())
        obs = obs[obs["n"] >= 20]
        names = (phys.drop_duplicates("nfl_id")[["nfl_id", "display_name"]])
        preds = []
        for _, row in obs.iterrows():
            curve = phys[(phys["nfl_id"] == row["nfl_id"])].sort_values("distance_yd")
            if curve.empty:
                continue
            grid = curve["distance_yd"].to_numpy(dtype=float)
            vals = curve["posterior_mean_s"].to_numpy(dtype=float)
            if not (grid.min() <= row["depth_yd"] <= grid.max()):
                continue
            preds.append((row["nfl_id"], float(np.interp(row["depth_yd"], grid, vals)),
                          float(row["observed_s"]), int(row["n"])))
        pred = pd.DataFrame(preds, columns=["nfl_id", "predicted_s", "observed_s", "n"])
        if pred.empty:
            raise FigureInputMissing("no player has both a curve covering his mean "
                                     "depth and >=20 analysis-ready reps")
        pred = pred.merge(names, on="nfl_id", how="left")
        if len(pred) < 3:
            raise FigureInputMissing(f"only {len(pred)} players with both quantities")
        pred["resid"] = pred["observed_s"] - pred["predicted_s"]
        fig, ax = plt.subplots(figsize=(8, 4.6))
        ax.scatter(pred["predicted_s"], pred["observed_s"], s=22, alpha=0.75,
                   color="#4c72b0", edgecolor="white", linewidth=0.5)
        lo = float(min(pred["predicted_s"].min(), pred["observed_s"].min()))
        hi = float(max(pred["predicted_s"].max(), pred["observed_s"].max()))
        ax.plot([lo, hi], [lo, hi], color="0.35", lw=1, ls="--",
                label="observed = predicted")
        for _, row in pred.reindex(pred["resid"].abs().sort_values(ascending=False).index).head(6).iterrows():
            label = (str(row["display_name"]) if not _is_missing(row.get("display_name"))
                     else f"nfl_id {int(row['nfl_id'])}")
            ax.annotate(label, (row["predicted_s"], row["observed_s"]),
                        textcoords="offset points", xytext=(4, 3), fontsize=7)
        ax.set_xlabel("Combine-predicted physical time at mean depth (s)")
        ax.set_ylabel("observed mean tracked get-off (s)")
        ax.set_title("Physical prediction vs observed get-off — named players off the diagonal")
        ax.legend(frameon=False, loc="upper left")
        ax.spines[["top", "right"]].set_visible(False)
        save("fig2_phys_vs_observed", fig, ax)
    except Exception as exc:
        messages.append(_fig_skip("figure 2", f"{type(exc).__name__}: {exc}"))

    # ---- Figure 3: reliability and agreement of jump -----------------------
    try:
        labels = ["pooled", "edge", "interior"]
        rel_sb, rel_mb, agr_u, agr_p = [], [], [], []
        for label in labels:
            grp = art.get(label)
            if not isinstance(grp, dict):
                raise FigureInputMissing(f"decomposition[{label!r}] block absent")
            rel = grp.get("reliability")
            agr = grp.get("agreement")
            rel_sb.append(_need_num(rel.get("split_half") if isinstance(rel, dict) else None,
                                    "spearman_brown", f"{label}.reliability.split_half"))
            rel_mb.append(_need_num(rel.get("model_based") if isinstance(rel, dict) else None,
                                    "reliability", f"{label}.reliability.model_based"))
            agr_u.append(_need_num(agr.get("u_vs_onset_latency") if isinstance(agr, dict) else None,
                                   "r", f"{label}.agreement.u_vs_onset_latency"))
            agr_p.append(_need_num(agr.get("phys_vs_onset_to_cross") if isinstance(agr, dict) else None,
                                   "r", f"{label}.agreement.phys_vs_onset_to_cross"))
        x = np.arange(len(labels))
        w = 0.2
        fig, ax = plt.subplots(figsize=(8, 4.2))
        ax.bar(x - 1.5 * w, rel_sb, w, label="split-half reliability (SB)", color="#4c72b0")
        ax.bar(x - 0.5 * w, rel_mb, w, label="model-based reliability", color="#55a868")
        ax.bar(x + 0.5 * w, agr_u, w, label="r: u vs onset latency", color="#dd8452")
        ax.bar(x + 1.5 * w, agr_p, w, label="r: phys vs onset-to-cross", color="#c44e52")
        ax.set_xticks(x, [l.title() for l in labels])
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("correlation / reliability")
        ax.set_title("Reliability and latency agreement of the jump")
        ax.legend(frameon=False, fontsize=8, ncol=2)
        ax.spines[["top", "right"]].set_visible(False)
        save("fig3_reliability_agreement", fig, ax)
    except Exception as exc:
        messages.append(_fig_skip("figure 3", f"{type(exc).__name__}: {exc}"))

    # ---- Figure 4: one rusher's rep-level story ----------------------------
    try:
        snaps, phys = player_inputs()
        work = snaps[(snaps["in_primary_set"] == True) &               # noqa: E712
                     (snaps["analysis_ready"] == True) &               # noqa: E712
                     snaps["cross_time_s"].notna() & snaps["depth_yd"].notna()]
        counts = work.groupby("nfl_id").size().sort_values(ascending=False)
        if counts.empty:
            raise FigureInputMissing("no analysis-ready reps")
        player = counts.index[0]
        reps = work[work["nfl_id"] == player]
        curve = phys[phys["nfl_id"] == player].sort_values("distance_yd")
        name = (str(curve["display_name"].iloc[0]) if not curve.empty
                else f"nfl_id {player}")
        u_text = ""
        effects = outputs / "phase4" / "player_effects.csv"
        if effects.exists():
            try:
                eff = pd.read_csv(effects)
                row = eff[eff["player_id"] == player]
                if len(row) and "u_s" in eff.columns and not _is_missing(row.iloc[0].get("u_s")):
                    u_text = f" · u = {float(row.iloc[0]['u_s']):+.3f} s"
            except Exception:
                u_text = ""
        fig, ax = plt.subplots(figsize=(8, 4.6))
        ax.scatter(reps["depth_yd"], reps["cross_time_s"], s=12, alpha=0.45,
                   color="#4c72b0", label="reps (tracked LOS crossing)")
        if not curve.empty and curve["distance_yd"].notna().any():
            ax.plot(curve["distance_yd"], curve["posterior_mean_s"], color="#c44e52",
                    lw=1.8, label="this player's Combine physical curve")
        ax.set_xlabel("pre-snap depth to the line (yd)")
        ax.set_ylabel("snap to LOS crossing (s)")
        ax.set_title(f"{name}: {len(reps)} reps against his own physical curve{u_text}")
        ax.legend(frameon=False, fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
        save("fig4_one_rusher_reps", fig, ax)
    except Exception as exc:
        messages.append(_fig_skip("figure 4", f"{type(exc).__name__}: {exc}"))

    return {"messages": messages, "paths": paths, "figures": records}


def render_figures(outputs_dir: str | Path, figures_dir: str | Path,
                   mode: str = "sample", dpi: int = 150) -> dict[str, Any]:
    """Render the four deliverable figures from FULL-provenance artefacts only.

    Returns ``{"messages": [...], "paths": [...], "figures": [...]}``. Every skip
    is named — a missing field names itself rather than being plotted as an empty
    axis — and no figure is ever built from placeholder data. ``figures`` carries
    each PNG's pixel dimensions and axis limits so a caller can prove the axes
    are finite without opening the image.
    """
    outputs = Path(outputs_dir)
    figures = Path(figures_dir)

    art = read_json(outputs / "phase4" / "decomposition.json")
    if art is None:
        return {"messages": [_fig_skip("all figures",
                                       "outputs/phase4/decomposition.json absent")],
                "paths": [], "figures": []}
    if mode != "full" or provenance_kind(art) != "FULL":
        why = (f"MODE={mode!r} and provenance "
               f"{str(art.get('provenance', 'absent'))!r}; a plot of sample output "
               "would not be a finding")
        return {"messages": [f"figure {i}: SKIPPED — {why}" for i in (1, 2, 3, 4)],
                "paths": [], "figures": []}

    try:
        return _render_figures_impl(outputs, figures, dpi)
    except Exception as exc:                                       # pragma: no cover
        return {"messages": [_fig_skip("all figures",
                                       f"{type(exc).__name__}: {exc}")],
                "paths": [], "figures": []}
