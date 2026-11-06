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

The four functions are pure-ish (files in, strings/figures out) so the contract
is unit-testable without a Jupyter kernel.
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
# figures
# --------------------------------------------------------------------------- #
def _fig_skip(name: str, why: str) -> str:
    return f"{name}: SKIPPED — {why}"


def _load_snaps(path: Path, columns: list[str]):
    import pandas as pd

    if not path.exists():
        return None
    try:
        return pd.read_parquet(path, columns=columns)
    except Exception:
        return None


def render_figures(outputs_dir: str | Path, figures_dir: str | Path,
                   mode: str = "sample", dpi: int = 150) -> dict[str, Any]:
    """Render the four deliverable figures from FULL-provenance artefacts only.

    Returns ``{"messages": [...], "paths": [...]}``. Every skip is named, and no
    figure is ever built from placeholder data. The caller displays the saved
    PNGs; this function does not assume an interactive backend.
    """
    outputs = Path(outputs_dir)
    figures = Path(figures_dir)
    messages: list[str] = []
    paths: list[str] = []

    art = read_json(outputs / "phase4" / "decomposition.json")
    if art is None:
        return {"messages": [_fig_skip("all figures",
                                       "outputs/phase4/decomposition.json absent")],
                "paths": []}
    if mode != "full" or provenance_kind(art) != "FULL":
        why = (f"MODE={mode!r} and provenance "
               f"{str(art.get('provenance', 'absent'))!r}; a plot of sample output "
               "would not be a finding")
        return {"messages": [f"figure {i}: SKIPPED — {why}" for i in (1, 2, 3, 4)],
                "paths": []}

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
    except Exception as exc:                                   # pragma: no cover
        return {"messages": [_fig_skip("all figures",
                                       f"matplotlib/pandas unavailable "
                                       f"({type(exc).__name__}: {exc})")],
                "paths": []}

    figures.mkdir(parents=True, exist_ok=True)

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
                grp = art.get(label) or {}
                boot = grp.get("bootstrap") or {}
                val = grp.get(field)
                ci = boot.get(field + "_ci95") or [None, None]
                vals.append(float(val) if not _is_missing(val) else np.nan)
                los.append(float(ci[0]) if not _is_missing(ci[0]) else np.nan)
                his.append(float(ci[1]) if not _is_missing(ci[1]) else np.nan)
            vals = np.asarray(vals)
            err = np.vstack([vals - np.asarray(los), np.asarray(his) - vals])
            ax.bar(x + offset, vals, width, label=name, color=colour,
                   yerr=np.nan_to_num(err), capsize=4)
        ax.set_xticks(x, [l.title() for l in labels])
        ax.set_ylabel("share of between-player variance")
        ax.set_ylim(0, 1.05)
        ax.set_title("Between-player get-off variance: phys vs jump, 95% intervals")
        ax.legend(frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        out = figures / "fig1_variance_split.png"
        fig.savefig(out, dpi=dpi)
        plt.close(fig)
        paths.append(str(out))
        messages.append(f"figure 1: wrote {out}")
    except Exception as exc:
        messages.append(_fig_skip("figure 1", f"{type(exc).__name__}: {exc}"))

    # ---- shared inputs for figures 2 and 4 ---------------------------------
    snaps = _load_snaps(outputs / "phase3" / "snaps.parquet",
                        ["nfl_id", "depth_yd", "cross_time_s", "analysis_ready",
                         "in_primary_set", "provenance"])
    phys = None
    phys_path = outputs / "phase2" / "physical_time_by_player.csv"
    if phys_path.exists():
        try:
            phys = pd.read_csv(phys_path)
        except Exception:
            phys = None
    phys_full = (phys is not None and "provenance" in phys.columns
                 and set(phys["provenance"].astype(str).unique()) == {"FULL"})
    snaps_full = (snaps is not None and "provenance" in snaps.columns
                  and set(snaps["provenance"].astype(str).unique()) == {"FULL"})

    # ---- Figure 2: phys time vs observed get-off, named off-diagonal --------
    try:
        if snaps is None or phys is None:
            raise RuntimeError("snaps.parquet or physical_time_by_player.csv absent")
        if not (snaps_full and phys_full):
            raise RuntimeError("inputs are not FULL provenance")
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
        pred = pred.merge(names, on="nfl_id", how="left")
        if len(pred) < 3:
            raise RuntimeError(f"only {len(pred)} players with both quantities")
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
        fig.tight_layout()
        out = figures / "fig2_phys_vs_observed.png"
        fig.savefig(out, dpi=dpi)
        plt.close(fig)
        paths.append(str(out))
        messages.append(f"figure 2: wrote {out}")
    except Exception as exc:
        messages.append(_fig_skip("figure 2", f"{type(exc).__name__}: {exc}"))

    # ---- Figure 3: reliability and agreement of jump -----------------------
    try:
        labels = ["pooled", "edge", "interior"]
        rel_sb, rel_mb, agr_u, agr_p = [], [], [], []
        for label in labels:
            grp = art.get(label) or {}
            rel = grp.get("reliability") or {}
            agr = grp.get("agreement") or {}
            rel_sb.append(float((rel.get("split_half") or {}).get("spearman_brown")
                                if not _is_missing((rel.get("split_half") or {}).get("spearman_brown"))
                                else np.nan))
            rel_mb.append(float((rel.get("model_based") or {}).get("reliability")
                                if not _is_missing((rel.get("model_based") or {}).get("reliability"))
                                else np.nan))
            agr_u.append(float((agr.get("u_vs_onset_latency") or {}).get("r")
                               if not _is_missing((agr.get("u_vs_onset_latency") or {}).get("r"))
                               else np.nan))
            agr_p.append(float((agr.get("phys_vs_onset_to_cross") or {}).get("r")
                               if not _is_missing((agr.get("phys_vs_onset_to_cross") or {}).get("r"))
                               else np.nan))
        if all(np.all(np.isnan(v)) for v in (rel_sb, rel_mb, agr_u, agr_p)):
            raise RuntimeError("reliability and agreement blocks absent")
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
        fig.tight_layout()
        out = figures / "fig3_reliability_agreement.png"
        fig.savefig(out, dpi=dpi)
        plt.close(fig)
        paths.append(str(out))
        messages.append(f"figure 3: wrote {out}")
    except Exception as exc:
        messages.append(_fig_skip("figure 3", f"{type(exc).__name__}: {exc}"))

    # ---- Figure 4: one rusher's rep-level story ----------------------------
    try:
        if snaps is None or phys is None:
            raise RuntimeError("snaps.parquet or physical_time_by_player.csv absent")
        if not (snaps_full and phys_full):
            raise RuntimeError("inputs are not FULL provenance")
        work = snaps[(snaps["in_primary_set"] == True) &               # noqa: E712
                     (snaps["analysis_ready"] == True) &               # noqa: E712
                     snaps["cross_time_s"].notna() & snaps["depth_yd"].notna()]
        counts = work.groupby("nfl_id").size().sort_values(ascending=False)
        if counts.empty:
            raise RuntimeError("no analysis-ready reps")
        player = counts.index[0]
        reps = work[work["nfl_id"] == player]
        curve = phys[phys["nfl_id"] == player].sort_values("distance_yd")
        name = (str(curve["display_name"].iloc[0]) if not curve.empty
                else f"nfl_id {player}")
        effects = outputs / "phase4" / "player_effects.csv"
        u_text = ""
        if effects.exists():
            eff = pd.read_csv(effects)
            row = eff[eff["player_id"] == player]
            if len(row) and not _is_missing(row.iloc[0].get("u_s")):
                u_text = f" · u = {float(row.iloc[0]['u_s']):+.3f} s"
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
        fig.tight_layout()
        out = figures / "fig4_one_rusher_reps.png"
        fig.savefig(out, dpi=dpi)
        plt.close(fig)
        paths.append(str(out))
        messages.append(f"figure 4: wrote {out}")
    except Exception as exc:
        messages.append(_fig_skip("figure 4", f"{type(exc).__name__}: {exc}"))

    return {"messages": messages, "paths": paths}
