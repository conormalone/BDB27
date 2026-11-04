#!/usr/bin/env python3
"""stage_05_validate — Phase 5: validation of the Part-1 / Part-2 effects.

Run order: after ``04_models``; last stage in ``TASK.md``.

    cd src && python -m dl.run 05_validate --mode sample
    python src/dl/stage_05_validate.py --mode sample --force

What this build implements
--------------------------
**The ceiling permutation** (prereg §3 exposure, §7 relative load; TASK.md
Phase 5).  Per replicate:

1. shuffle the shrunken per-player ceilings **across players within the
   player's position group** (interior vs edge) — the whole ceiling travels
   together (``a0``, ``a0_se``, ``a0_icc``, ``vmax``, ``tau`` and their SEs),
   so the donor's ceiling is the one that reaches both the Part-1 exposure and
   the relative-load denominator;
2. rebuild the relative-load panel from the shuffled ceiling (same arithmetic,
   same grids, same recency weighting as stage 04);
3. refit the Part-1 model with the **same estimator, same rung ladder and same
   standardisation rule** as the confirmatory fit, and re-derive the ``a0``
   effect;
4. refit the S1 population model on the rebuilt relative load and re-derive
   the relative-load coefficient.

The observed statistic is recomputed by the identical code path, so the
observed value and the null distribution are commensurable.  Reported per
statistic: point estimate, permutation null summary (mean, SD, 2.5/97.5
quantiles), a two-sided and a one-sided permutation p, the number of valid
permutations, and the seed.

NOT implemented in this build (stubs that raise ``NotImplementedError`` and are
listed as ``NOT_IMPLEMENTED`` in ``validation_result.json`` and ``SUMMARY.md``,
never as a pass and never silently absent):

* within-player snap-order permutation;
* the cross-validated relative-vs-controls-only baseline;
* the measurement-error-corrected ceiling/load estimates.

The remaining Phase-5 checks in design §5.6 (placebos, rotation/survivorship,
context sensitivity, equivalence/TOST) are not built either; their artefact
files are written with ``status: NOT_IMPLEMENTED`` and a reason naming the
check, so an absent check can never read as a passed one.

Honesty rules
-------------
* Sample mode stamps every artefact ``PLACEHOLDER_SAMPLE`` and sets
  ``reportable: false``; the verdict is ``NOT_INTERPRETED_SAMPLE`` and no
  sample number is presented as a finding.
* Full mode requires ``FULL`` provenance and a frozen prereg.
* A statistic that cannot be computed is ``NOT_ESTIMABLE`` with a reason.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402
from dl import dl_models_core as M  # noqa: E402
from dl import stage_00_thresholds as stage00  # noqa: E402
from dl import stage_04_models as stage04  # noqa: E402

STAGE = "05_validate"


def _block(cfg: Mapping) -> Mapping:
    """The ``bdb27_dl`` block, accepting either the full config or the block."""
    return D.dl(cfg) if "bdb27_dl" in cfg else cfg

#: The ceiling columns that travel together in a permutation.  Missing columns
#: are skipped (and reported); ``nfl_id`` is the key and is never moved.
CEILING_COLUMNS = ("a0", "a0_se", "a0_icc", "vmax", "tau", "vmax_se", "tau_se")


# --------------------------------------------------------------------------- #
# Not-implemented checks (stubs — they must fail loudly if called)
# --------------------------------------------------------------------------- #
class CheckNotImplemented(NotImplementedError):
    """Raised by a Phase-5 check this build does not implement."""


def within_player_order_permutation(frame: pd.DataFrame, cfg: Mapping) -> dict:
    """Shuffle snap order within ``(nfl_id, game_id)`` and rebuild the loads."""
    raise CheckNotImplemented(
        "within-player order permutation is NOT IMPLEMENTED in this build: it "
        "must shuffle snap order within (nfl_id, game_id), rebuild both "
        "cumulative loads and show the load effect vanishes (prereg §7, "
        "TASK.md Phase 5).")


def cv_vs_controls_only_baseline(frame: pd.DataFrame, cfg: Mapping) -> dict:
    """Player-grouped CV of each load definition against a controls-only model."""
    raise CheckNotImplemented(
        "CV-vs-controls-only baseline is NOT IMPLEMENTED in this build: it must "
        "score each load definition against a controls-only model on the same "
        "player-grouped folds, so a definition's gain is measured against the "
        "controls it shares (prereg §7 tuning rule, TASK.md Phase 5).")


def measurement_error_correction(frame: pd.DataFrame, cfg: Mapping) -> dict:
    """Ceiling / load measurement-error-corrected estimates alongside naive ones."""
    raise CheckNotImplemented(
        "measurement-error correction is NOT IMPLEMENTED in this build: it must "
        "propagate the ceiling SE into the load and the load x ceiling "
        "interaction and report corrected estimates beside the naive ones "
        "(TASK.md Phase 4 secondary; prereg §7 posterior-draw rule).")


STUBS = {
    "within_player_order_permutation": within_player_order_permutation,
    "cv_vs_controls_only_baseline": cv_vs_controls_only_baseline,
    "measurement_error_correction": measurement_error_correction,
}

#: Phase-5 checks from design §5.6 that are neither built nor stubbed with a
#: function.  They get an artefact row so they cannot read as passed.
UNBUILT_CHECKS = {
    "placebo_pre_snap_features": (
        "load must not predict pre-snap alignment or depth; height and weight "
        "must not mimic the a0 effect after the §8 controls (TASK.md Phase 5)"),
    "rotation_and_survivorship": (
        "restriction to complete-coverage rusher-games and high-snap-share "
        "rushers, snaps by quarter, and the stay-probability-weighted version "
        "(TASK.md Phase 5; prereg §6, §9)"),
    "context_sensitivity": (
        "garbage time, final two minutes of each half, obvious passing "
        "situations and blocker-control variation, run separately "
        "(TASK.md Phase 5; prereg §6, §8)"),
    "mechanical_coupling": (
        "repeat the comparison with ceiling-fraction outcomes only, as a check "
        "that the confirmatory result rests on the ceiling-free outcomes "
        "(TASK.md Phase 5; prereg §4)"),
    "equivalence_tost": (
        "TOST against the prereg §12 margins (HR 1.05 / 0.005 nats per snap) "
        "with the informative-null interval rule"),
}


# --------------------------------------------------------------------------- #
# Paths / provenance
# --------------------------------------------------------------------------- #
def _out_dir(cfg: Mapping, mode: str) -> Path:
    paths = D.resolve_paths(cfg, mode)
    return paths["outputs"] / str(D.dl(cfg)["validate"]["out_subdir"])


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(D.REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def check_provenance(cfg: Mapping, mode: str) -> dict[str, Any]:
    """Same gate as stage 04: full mode needs FULL config *and* thresholds."""
    level = D.require_full_provenance(cfg, mode, STAGE)
    D.require_prereg(cfg, mode, STAGE)
    thresholds = stage00._read_existing(D.resolve_paths(cfg, mode)["thresholds"])
    th_level = stage00._provenance_level(thresholds) or "missing"
    if mode == "full":
        if level != "FULL" or th_level != "FULL" or not stage00.is_full_provenance(thresholds):
            raise RuntimeError(
                f"STAGE {STAGE!r}: full mode requires FULL provenance for the "
                f"config and thresholds.json (got {level!r}, {th_level!r}); a "
                f"validation verdict may not rest on sample-tuned thresholds.")
    return {"config_level": level, "thresholds": th_level,
            "reportable": level == "FULL" and mode == "full"}


def _existing_full(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(payload.get("provenance", "")).startswith("FULL")


# --------------------------------------------------------------------------- #
# JSON-safe conversion (same rule as stage 04)
# --------------------------------------------------------------------------- #
def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()
                if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.DataFrame):
        return _clean(value.to_dict(orient="records"))
    return value


# --------------------------------------------------------------------------- #
# Inputs / position groups
# --------------------------------------------------------------------------- #
def load_frame(cfg: Mapping, mode: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Stage-04 inputs, assembled exactly as stage 04 assembles them."""
    inputs = stage04.load_inputs(cfg, mode)
    frame, report = M.build_frame(inputs["snaps"], inputs["ceiling"],
                                  inputs["players"], inputs["combine"])
    return frame, {**inputs, "_report": report}


def player_groups(frame: pd.DataFrame, cfg: Mapping) -> tuple[pd.Series, dict]:
    """Per-player position group: ``edge`` or ``interior``.

    The labelled per-snap field is ``alignment``; a player's group is the
    **modal** alignment over his own snaps (ties broken by the alphabetically
    first label, so the result is deterministic).  Players with no usable
    alignment fall back to the roster ``position_group`` mapping in
    ``validate.position_group.fallback_map``.  Players in neither are
    ``unknown`` and are never mixed into a real group.

    The prereg fixes *that* the shuffle is within position group but not how
    the group is derived from this product; this is the most conservative
    reading (the labelled edge/interior field, not a guess from size).
    """
    vcfg = _block(cfg)["validate"]["position_group"]
    source = str(vcfg["source"])
    values = [str(v) for v in vcfg["values"]]

    groups: dict[Any, str] = {}
    sources: dict[Any, str] = {}
    if source in frame.columns:
        for pid, part in frame.groupby("nfl_id", sort=True):
            series = part[source].dropna().astype(str)
            series = series[series.isin(values)]
            if len(series):
                counts = series.value_counts()
                top = int(counts.max())
                tied = sorted(str(v) for v in counts[counts == top].index)
                groups[pid] = tied[0]
                sources[pid] = f"modal_{source}"

    fallback = str(vcfg.get("fallback_column", ""))
    fmap = {str(k): {str(x) for x in v} for k, v in
            dict(vcfg.get("fallback_map") or {}).items()}
    if fallback and fallback in frame.columns:
        for pid, part in frame.groupby("nfl_id", sort=True):
            if pid in groups:
                continue
            labels = {str(v) for v in part[fallback].dropna().unique()}
            hit = sorted(g for g, members in fmap.items() if labels & members)
            groups[pid] = hit[0] if hit else "unknown"
            sources[pid] = f"fallback_{fallback}"

    for pid in frame["nfl_id"].unique():
        groups.setdefault(pid, "unknown")
        sources.setdefault(pid, "unknown")

    series = pd.Series(groups, name="position_group_for_permutation").sort_index()
    counts = series.value_counts().to_dict()
    report = {
        "source": source, "values": values,
        "n_players": int(len(series)),
        "group_counts": {str(k): int(v) for k, v in sorted(counts.items())},
        "source_counts": {str(k): int(v) for k, v in
                          sorted(pd.Series(sources).value_counts().to_dict().items())},
        "n_unknown": int((series == "unknown").sum()),
        "singleton_groups": int(sum(1 for g, c in counts.items()
                                    if str(g) != "unknown" and c == 1)),
    }
    return series, report


def _ceiling_table(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    columns = [c for c in CEILING_COLUMNS if c in frame.columns]
    table = (frame.drop_duplicates("nfl_id").set_index("nfl_id")[columns].copy())
    return table, columns


def permute_ceilings(frame: pd.DataFrame, groups: pd.Series,
                     table: pd.DataFrame, rng) -> tuple[pd.DataFrame, dict]:
    """One permutation: each player receives a ceiling from his own group.

    A group of size one keeps its own ceiling (there is nothing to swap with);
    that is counted, not hidden.  A random permutation may be the identity —
    standard permutation-test practice — and that too is counted.
    """
    donor: dict[Any, Any] = {}
    identity = 0
    for group, members in groups.groupby(groups).groups.items():
        ids = sorted(members)
        if str(group) == "unknown" or len(ids) <= 1:
            donor.update({pid: pid for pid in ids})
            continue
        order = rng.permutation(len(ids))
        for target, pos in zip(ids, order):
            donor[target] = ids[int(pos)]
        identity += int(all(donor[pid] == pid for pid in ids))

    out = frame.copy()
    out = out.drop(columns=["a0_z"], errors="ignore")
    for column in table.columns:
        mapping = {pid: table.at[donor.get(pid, pid), column] for pid in donor}
        out[column] = out["nfl_id"].map(mapping)
    return out, {"identity_groups": int(identity), "n_players": int(len(donor))}


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def _a0_statistic(frame: pd.DataFrame, cfg: Mapping, vcfg: Mapping) -> dict:
    """The P1 estimand from the same estimator / rung ladder as stage 04."""
    p1 = M.fit_p1(frame, _block(cfg), include_secondary=False,
                  starts=int(vcfg["permutations"]["starts"]))
    if p1.get("status") != "OK" or not p1.get("estimate"):
        return {"status": "NOT_ESTIMABLE",
                "reason": f"Part-1 refit status {p1.get('status')!r}: "
                          f"{p1.get('reason', 'no estimate')}"}
    est = p1["estimate"]
    log_hr = est.get("log_estimate")
    if log_hr is None and est.get("estimate") is not None:
        log_hr = math.log(float(est["estimate"]))
    return {"status": "OK", "value": float(log_hr), "hr": est.get("estimate"),
            "se": est.get("se"), "rung": p1.get("rung_used"),
            "n_obs": p1.get("n_obs"), "n_events": p1.get("n_events")}


def _selected_relative_column(cfg: Mapping, mode: str) -> tuple[str | None, str]:
    """The relative-load column stage 04 actually selected, and where from.

    The plurality of stage-04's held-out fold selections is used unchanged: a
    validation stage may not tune a value of its own.  If no selection exists
    the middle grid value is used and the source says so.
    """
    vcfg = _block(cfg)["validate"]
    configured = vcfg.get("relative_load_column")
    if configured:
        return str(configured), "config"
    path = D.resolve_paths(cfg, mode)["outputs"] / "models" / "part2_result.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    values = [row.get("selected_grid_value")
              for row in (payload.get("folds") or [])
              if row.get("definition") == "relative"
              and row.get("selected_grid_value") is not None]
    if values:
        chosen = max(sorted(set(values)), key=values.count)
        label = f"{float(chosen):.1f}".replace(".", "p")
        return f"relative_load_{label}", "stage_04_fold_selection"
    grid = [float(g) for g in _block(cfg)["load"]["relative_fraction_grid"]]
    if not grid:
        return None, "no_grid"
    middle = grid[len(grid) // 2]
    return f"relative_load_{middle:.1f}".replace(".", "p"), "grid_midpoint_default"


def _load_statistic(frame: pd.DataFrame, cfg: Mapping, column: str | None) -> dict:
    """The S1 population relative-load coefficient, naive (the permutation target)."""
    if not column or column not in frame.columns:
        return {"status": "NOT_ESTIMABLE",
                "reason": f"relative-load column {column!r} is absent from the frame"}
    usable = int(pd.to_numeric(frame[column], errors="coerce").notna().sum())
    if usable < int(_block(cfg)["validate"]["permutations"]["min_rows"]):
        return {"status": "NOT_ESTIMABLE",
                "reason": (f"relative load is present on {usable} snaps, below the "
                           f"configured minimum; the sample's per-game tracking "
                           f"sequences are incomplete, so the load cannot be rebuilt "
                           f"for a permutation refit")}
    spread = float(pd.to_numeric(frame[column], errors="coerce").std(ddof=0))
    if not np.isfinite(spread) or spread <= 1e-9:
        # A constant column standardises to zeros: the refit would return a
        # coefficient of exactly 0 and read as "no effect" when in truth the
        # load has no variance on the rows that carry it.
        return {"status": "NOT_ESTIMABLE",
                "reason": (f"relative load is constant on the {usable} snaps that "
                           f"carry it, so there is no load effect to re-derive")}
    # ``fit_s1`` takes the prepared frame (stage 04 prepares it once for P2/S1).
    fit = M.fit_s1(M.prepare_frame(frame, _block(cfg)), _block(cfg),
                   load_column=column, draws=0,
                   include_reliability=False)
    if fit.get("status") != "OK":
        return {"status": "NOT_ESTIMABLE",
                "reason": f"S1 refit status {fit.get('status')!r}"}
    return {"status": "OK", "value": float(fit["naive"]["load_coef"]),
            "se": fit["naive"].get("load_se"),
            "interaction": fit["naive"].get("interaction_coef"),
            "n_obs": fit.get("n_obs"), "n_events": fit.get("n_events")}


def _null_summary(observed: float, null: list[float], alpha: float) -> dict:
    """Permutation inference for one statistic (two-sided and one-sided p)."""
    arr = np.asarray([v for v in null if v is not None and np.isfinite(v)], float)
    n = int(arr.size)
    if n == 0:
        return {"status": "NOT_ESTIMABLE", "reason": "no finite permutation statistic"}
    observed = float(observed)
    extreme = int(np.sum(np.abs(arr) >= abs(observed) - 1e-15))
    greater = int(np.sum(arr >= observed - 1e-15))
    p_two = (1.0 + extreme) / (1.0 + n)
    p_one = (1.0 + greater) / (1.0 + n)
    return {
        "status": "OK",
        "observed": observed,
        "observed_abs": abs(observed),
        "null_mean": float(arr.mean()),
        "null_sd": float(arr.std(ddof=1)) if n > 1 else None,
        "null_q025": float(np.quantile(arr, 0.025)),
        "null_q975": float(np.quantile(arr, 0.975)),
        "null_min": float(arr.min()),
        "null_max": float(arr.max()),
        "n_permutations": n,
        "n_extreme": extreme,
        "p_two_sided": float(p_two),
        "p_one_sided": float(p_one),
        "alpha": float(alpha),
        "reject_null": bool(p_two <= alpha),
        "null_centred_near_zero": bool(abs(float(arr.mean())) <= 0.25 * max(abs(observed), 1e-12)),
    }


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def ceiling_permutation(frame: pd.DataFrame, cfg: Mapping, *,
                        reps: int, seed: int,
                        relative_column: str | None) -> dict:
    """Shuffle ceilings within position group and re-derive both effects."""
    block = _block(cfg)
    vcfg = block["validate"]
    alpha = float(vcfg["equivalence"]["alpha"])
    groups, group_report = player_groups(frame, cfg)
    table, columns = _ceiling_table(frame)
    if not columns:
        return {"status": "NOT_ESTIMABLE",
                "reason": "the frame carries no ceiling columns to permute",
                "design": {"position_groups": group_report}}

    if len(frame) < int(vcfg["permutations"]["min_rows"]):
        return {"status": "NOT_ESTIMABLE",
                "reason": (f"{len(frame)} snaps, below the configured minimum of "
                           f"{vcfg['permutations']['min_rows']}; no refit attempted"),
                "design": {"position_groups": group_report}}

    # The observed statistic goes through the identical code path.
    panel = M.build_load_panel(frame, block)
    base = pd.concat([frame, panel], axis=1)
    observed = {
        "a0": _a0_statistic(base, cfg, vcfg),
        "relative_load": _load_statistic(base, cfg, relative_column),
    }

    rng = np.random.default_rng(int(seed))
    rows: list[dict] = []
    a0_null: list[float] = []
    load_null: list[float] = []
    swaps: list[int] = []
    n_identity = 0
    for rep in range(int(reps)):
        permuted, info = permute_ceilings(frame, groups, table, rng)
        n_identity += int(info["identity_groups"])
        panel_p = M.build_load_panel(permuted, block)
        permuted = pd.concat([permuted, panel_p], axis=1)
        swaps.append(int(info["n_players"]))

        a0 = _a0_statistic(permuted, cfg, vcfg)
        load = _load_statistic(permuted, cfg, relative_column)
        a0_null.append(float(a0["value"]) if a0.get("status") == "OK" else np.nan)
        load_null.append(float(load["value"]) if load.get("status") == "OK" else np.nan)
        rows.append({"check": "ceiling_permutation", "rep": rep + 1,
                     "statistic": "a0_log_hr",
                     "value": a0.get("value"), "status": a0.get("status"),
                     "rung": a0.get("rung")})
        rows.append({"check": "ceiling_permutation", "rep": rep + 1,
                     "statistic": "relative_load_coef",
                     "value": load.get("value"), "status": load.get("status"),
                     "rung": ""})

    a0_block = dict(observed["a0"])
    a0_block["inference"] = (_null_summary(observed["a0"]["value"], a0_null, alpha)
                             if observed["a0"].get("status") == "OK"
                             else {"status": "NOT_ESTIMABLE",
                                   "reason": observed["a0"].get("reason")})
    load_block = dict(observed["relative_load"])
    load_block["inference"] = (
        _null_summary(observed["relative_load"]["value"], load_null, alpha)
        if observed["relative_load"].get("status") == "OK"
        else {"status": "NOT_ESTIMABLE",
              "reason": observed["relative_load"].get("reason")})

    statuses = [b.get("inference", {}).get("status") for b in (a0_block, load_block)]
    status = "OK" if "OK" in statuses else "NOT_ESTIMABLE"
    return {
        "status": status,
        "design": {
            "scheme": str(vcfg["permutations"]["ceiling_scheme"]),
            "reps_requested": int(reps),
            "seed": int(seed),
            "columns_permuted": list(columns),
            "relative_load_column": relative_column,
            "position_groups": group_report,
            "identity_group_draws": int(n_identity),
        },
        "a0": a0_block,
        "relative_load": load_block,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def _fmt(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "NA"
    return f"{number:.{digits}f}" if math.isfinite(number) else "NA"


def _stat_markdown(name: str, block: Mapping, unit: str) -> list[str]:
    lines = [f"### {name}\n"]
    if block.get("status") != "OK":
        lines.append(f"* **NOT ESTIMABLE** — {block.get('reason')}")
        return lines + [""]
    inf = block.get("inference") or {}
    lines.append(f"* observed: **{_fmt(block.get('value'))}** {unit} "
                 f"(refit on the observed ceilings)")
    if inf.get("status") != "OK":
        lines.append(f"* permutation inference: **NOT ESTIMABLE** — {inf.get('reason')}")
        return lines + [""]
    lines.append(f"* permutation null: mean {_fmt(inf['null_mean'])}, "
                 f"SD {_fmt(inf['null_sd'])}, "
                 f"95% [{_fmt(inf['null_q025'])}, {_fmt(inf['null_q975'])}], "
                 f"{inf['n_permutations']} permutations")
    lines.append(f"* point estimate vs null: {inf['n_extreme']} of "
                 f"{inf['n_permutations']} replicates at least as extreme")
    lines.append(f"* permutation p (two-sided): **{_fmt(inf['p_two_sided'])}**; "
                 f"one-sided {_fmt(inf['p_one_sided'])} at alpha "
                 f"{_fmt(inf['alpha'], 2)}")
    direction = ("extreme against the null — the effect does **not** vanish when the "
                 "ceiling is shuffled within position group, so it is not carried by "
                 "the arrangement of ceilings"
                 if inf["reject_null"] else
                 "inside the permutation null — the effect is consistent with a "
                 "chance arrangement of the ceilings within position group")
    lines.append(f"* reading: {direction}")
    return lines + [""]


def _summary_markdown(cfg: Mapping, mode: str, prov: Mapping, result: Mapping) -> str:
    lines: list[str] = ["# DL stage 05 — validation\n"]
    if prov["reportable"]:
        lines.append("**Provenance: FULL.** These numbers come from the full-data "
                     "run and may be reported under the prereg's rules.\n")
    else:
        lines.append("**PROVENANCE: PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT.**\n")
        lines.append("> **No number in this directory may be quoted as a result.** "
                     "The permutation machinery is exercised here; the verdict is "
                     "suppressed and no sample statistic is a finding. Reportable "
                     "numbers require `provenance == FULL`.\n")

    lines.append("## Verdict\n")
    lines.append(f"**{result['verdict']}** — {result['verdict_statement']}\n")

    checks = result["checks"]
    perm = checks.get("ceiling_permutation") or {}
    lines.append("## Ceiling permutation (implemented)\n")
    lines.append("Scheme: shuffle each player's shrunken ceiling to a donor player "
                 "**within the same position group** (interior vs edge), rebuild the "
                 "relative-load panel and the Part-1 exposure from the shuffled "
                 "ceiling, refit with the same estimator and rung ladder, and compare "
                 "the observed statistic with the permutation null.\n")
    design = perm.get("design") or {}
    groups = design.get("position_groups") or {}
    lines.append(f"* permutations requested: **{design.get('reps_requested')}**, "
                 f"seed **{design.get('seed')}**")
    lines.append(f"* ceiling columns carried with the donor: "
                 f"`{'`, `'.join(design.get('columns_permuted') or [])}`")
    lines.append(f"* relative-load column: `{design.get('relative_load_column')}`")
    lines.append(f"* players by group: {groups.get('group_counts')} "
                 f"(group source `{groups.get('source')}`, "
                 f"{groups.get('singleton_groups')} singleton groups keep their own "
                 f"ceiling)")
    lines.append("")
    if perm.get("status") != "OK":
        lines.append(f"**NOT ESTIMABLE** — {perm.get('reason')}\n")
    else:
        lines.extend(_stat_markdown("a0 effect on the pressure hazard",
                                    perm.get("a0") or {}, "log HR per 1 SD"))
        lines.extend(_stat_markdown("relative-load effect",
                                    perm.get("relative_load") or {},
                                    "log hazard per 1 SD load"))

    lines.append("## NOT IMPLEMENTED (never a pass)\n")
    for name, item in checks.items():
        if name == "ceiling_permutation":
            continue
        lines.append(f"* **{item.get('status')}** — `{name}`: {item.get('reason')}")
    lines.append("")

    lines.append("## Claim hierarchy\n")
    lines.append("* A permutation p is a statement about the observed effect under a "
                 "shuffled ceiling, not a confirmatory test; P1 and P2 remain the two "
                 "confirmatory tests.")
    lines.append("* Nothing here re-tunes the exposure, the load grid, the controls, "
                 "the metric, the source of interest or any threshold.\n")
    lines.append("## What this stage did not do\n")
    lines.append("* It did not run the within-player order permutation, the "
                 "CV-vs-controls-only baseline, or the measurement-error correction "
                 "(stubs; listed above as NOT IMPLEMENTED).")
    lines.append("* It did not run the placebos, rotation/survivorship, context "
                 "sensitivity, mechanical-coupling or equivalence checks.")
    lines.append("* It did not quote a sample number as a finding.\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def _check_rows(result: Mapping) -> list[dict]:
    """One row per (check, statistic) for the status table."""
    rows: list[dict] = []
    for name, item in result["checks"].items():
        if name == "ceiling_permutation":
            for statistic, key in (("a0_log_hr", "a0"),
                                   ("relative_load_coef", "relative_load")):
                block = item.get(key) or {}
                inf = block.get("inference") or {}
                rows.append({
                    "check": name,
                    "status": block.get("status", item.get("status")),
                    "statistic": statistic,
                    "observed": block.get("value"),
                    "null_mean": inf.get("null_mean"),
                    "p_two_sided": inf.get("p_two_sided"),
                    "reason": block.get("reason") or inf.get("reason") or "",
                })
    for name, item in result["checks"].items():
        if name == "ceiling_permutation":
            continue
        rows.append({"check": name, "status": item.get("status"),
                     "statistic": "", "observed": "", "null_mean": "",
                     "p_two_sided": "", "reason": item.get("reason") or ""})
    return rows


#: Which not-implemented checks belong in each design-§5.6 artefact.
_ARTEFACT_CHECKS = {
    "placebo_checks.csv": ("placebo_pre_snap_features",),
    "rotation.csv": ("rotation_and_survivorship",),
    "context_sensitivity.csv": ("context_sensitivity",),
}


def run(cfg: Mapping, mode: str, force: bool = False) -> dict[str, Any]:
    out_dir = _out_dir(cfg, mode)
    result_path = out_dir / "validation_result.json"
    if mode == "sample" and _existing_full(result_path):
        raise RuntimeError(
            f"STAGE {STAGE!r}: refusing to run — {result_path} already carries FULL "
            f"provenance. A sample run must never overwrite a full-run verdict.")
    prov = check_provenance(cfg, mode)
    if D.is_done(cfg, STAGE, mode, force=force):
        return {"skipped": True, "path": out_dir}

    D.assert_disk_space(cfg, mode)
    block = D.dl(cfg)
    vcfg = block["validate"]
    frame, inputs = load_frame(cfg, mode)
    seed = int(block["seeds"][str(vcfg["permutations"]["seed_key"])])
    reps = int(vcfg["permutations"]["sample_reps"] if mode == "sample"
               else vcfg["permutations"]["reps"])
    relative_column, column_source = _selected_relative_column(cfg, mode)

    perm = ceiling_permutation(frame, cfg, reps=reps, seed=seed,
                               relative_column=relative_column)
    perm["relative_load_column_source"] = column_source

    # Stubs: call them, record the refusal, never crash the stage.
    checks: dict[str, dict] = {"ceiling_permutation": {
        k: v for k, v in perm.items() if k != "rows"}}
    for name, fn in STUBS.items():
        try:
            fn(frame, cfg)
            checks[name] = {"status": "UNEXPECTEDLY_RAN",
                            "reason": "the stub returned instead of refusing"}
        except NotImplementedError as exc:
            checks[name] = {"status": "NOT_IMPLEMENTED", "reason": str(exc)}
    for name, reason in UNBUILT_CHECKS.items():
        checks[name] = {"status": "NOT_IMPLEMENTED", "reason": reason}

    if perm.get("status") == "OK":
        a0_inf = (perm.get("a0") or {}).get("inference") or {}
        load_inf = (perm.get("relative_load") or {}).get("inference") or {}
        if not prov["reportable"]:
            verdict = "NOT_INTERPRETED_SAMPLE"
            statement = ("Sample mode: the permutation ran as a machinery check and "
                         "the verdict is suppressed. Nothing here is a finding.")
        elif a0_inf.get("status") != "OK" and load_inf.get("status") != "OK":
            verdict = "NOT_ESTIMABLE"
            statement = "Neither statistic could be re-derived."
        elif a0_inf.get("reject_null") or load_inf.get("reject_null"):
            verdict = "PERMUTATION_REJECTS"
            statement = ("At least one effect is more extreme than the shuffled-ceiling "
                         "null: it is carried by the ceiling assignment, not by chance.")
        else:
            verdict = "CONSISTENT_WITH_NULL"
            statement = ("Every re-derived effect sits inside the permutation null: "
                         "the effects are consistent with a chance arrangement of the "
                         "ceilings within position group.")
    else:
        verdict = "NOT_ESTIMABLE" if prov["reportable"] else "NOT_INTERPRETED_SAMPLE"
        statement = str(perm.get("reason") or "the permutation could not be computed")

    result = {
        "stage": STAGE, "mode": mode,
        "reportable": bool(prov["reportable"]),
        "config_provenance": prov["config_level"],
        "thresholds_provenance": prov["thresholds"],
        "no_number_may_be_quoted": not prov["reportable"],
        "statement": ("Sample mode: every artefact is PLACEHOLDER_SAMPLE and no "
                      "number here may be quoted as a result."
                      if not prov["reportable"] else
                      "Full run: numbers are reportable under the prereg."),
        "verdict": verdict,
        "verdict_statement": statement,
        "checks": checks,
        "not_implemented": [name for name, item in checks.items()
                            if item["status"] == "NOT_IMPLEMENTED"],
        "ceiling_permutation": {k: v for k, v in perm.items() if k != "rows"},
        "design": {
            "reps_requested": reps,
            "reps_configured_full": int(vcfg["permutations"]["reps"]),
            "seed": seed,
            "seed_key": str(vcfg["permutations"]["seed_key"]),
            "relative_load_column": relative_column,
            "relative_load_column_source": column_source,
            "starts_per_refit": int(vcfg["permutations"]["starts"]),
        },
        "counts": {
            "n_snaps": int(len(frame)),
            "n_players": int(frame["nfl_id"].nunique()),
        },
    }

    payload_rows = perm.get("rows") or []
    if payload_rows:
        payload_rows = [{**row,
                         "relative_load_column": relative_column}
                        for row in payload_rows]
    csv_tables: dict[str, tuple[list[dict], list[str]]] = {
        "permutation_null.csv": ([
            {k: row.get(k) for k in
             ("check", "rep", "statistic", "value", "status", "rung",
              "relative_load_column")} for row in payload_rows],
            ["check", "rep", "statistic", "value", "status", "rung",
             "relative_load_column"]),
        "check_status.csv": (_check_rows(result),
                             ["check", "status", "statistic", "observed",
                              "null_mean", "p_two_sided", "reason"]),
    }
    for filename, names in _ARTEFACT_CHECKS.items():
        csv_tables[filename] = (
            [{"check": name, "status": checks[name]["status"],
              "reason": checks[name]["reason"]} for name in names],
            ["check", "status", "reason"])
    payload = {
        "json": {
            "validation_result.json": result,
            "equivalence.json": {
                "status": "NOT_IMPLEMENTED",
                "reason": UNBUILT_CHECKS["equivalence_tost"],
                "margins": vcfg["equivalence"],
            },
        },
        "csv": csv_tables,
        "summary": _summary_markdown(cfg, mode, prov, result),
    }
    for name, obj in payload["json"].items():
        D.write_json(out_dir / name, _clean(obj), mode=mode, cfg=cfg)
    for name, (rows, fields) in payload["csv"].items():
        D.write_csv(out_dir / name, rows, fields, mode=mode, cfg=cfg)
    D.write_markdown(out_dir / "SUMMARY.md", payload["summary"], mode=mode, cfg=cfg)

    D.mark_done(cfg, STAGE, mode, extra={
        "verdict": verdict,
        "ceiling_permutation": perm.get("status"),
        "not_implemented": len(result["not_implemented"]),
        "reportable": prov["reportable"]})
    return {"skipped": False, "path": out_dir, "result": result, "perm": perm}


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
            result = outcome["result"]
            perm = outcome["perm"]
            a0 = (perm.get("a0") or {})
            a0_inf = a0.get("inference") or {}
            st.note(f"verdict={result['verdict']} perm={perm.get('status')} "
                    f"reps={result['design']['reps_requested']} "
                    f"not_implemented={len(result['not_implemented'])}")
            print(f"{STAGE}: wrote {outcome['path']}")
            print(f"  verdict: {result['verdict']}")
            print(f"  ceiling permutation: {perm.get('status')} · "
                  f"a0 {a0.get('status')} · p_two={a0_inf.get('p_two_sided')}")
            print(f"  NOT IMPLEMENTED: {', '.join(result['not_implemented'])}")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
