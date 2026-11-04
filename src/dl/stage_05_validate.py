#!/usr/bin/env python3
"""stage_05_validate — Phase 5 under the binding IDR spec (TASK.md, 2026-11-04).

Run order: after ``04_models``; last stage in ``TASK.md``.

    cd src && python -m dl.run 05_validate --mode sample
    python src/dl/stage_05_validate.py --mode sample --force

What this build implements (the two items TASK.md Phase 5 names)
-----------------------------------------------------------------

1. **Within-player rest permutation** — ``dl.dl_idr_validate.rest_permutation``.
   ``inter_snap_rest_s`` is shuffled within every ``(rusher, game)`` block, the
   rest multiset of each block is preserved and no value crosses a player or
   game boundary (both asserted), the Part-1 mixed model is refitted through
   the identical production ladder, and the observed load x rest interaction
   is compared with the permutation null (mean, SD, quantiles, two-sided and
   one-sided p, reps, seed, valid refits, unpermutable snaps).
2. **Context controls** — ``dl.dl_idr_validate.context_controls``.  The
   interaction is re-estimated excluding (a) garbage time, (b) the final two
   minutes of each half, and (c) obvious passing situations.  The garbage-time
   rule and the two-minute clock are read from ``config.yaml``; **no rule for
   "obvious passing situations" exists anywhere in this repository**, so that
   check is reported ``DEFINITION_UNDEFINED`` and the gap is recorded in
   ``notes/blockers.md`` — a threshold is never invented here.

The retired spec's **ceiling permutation** is kept for provenance only.  Its
code is intact below, it is **not in the default run path**
(``validate.ceiling_permutation.enabled: false``), and every artefact labels it
``SUPERSEDED``.  The remaining checks of the old design (placebos,
rotation/survivorship, mechanical coupling, equivalence/TOST, the snap-order
permutation) are still listed as ``NOT_IMPLEMENTED`` — never as a pass and
never silently absent.  They are **not** part of the IDR spec and are not to be
built without a task line that asks for them.

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
from dl import dl_idr_validate as IV  # noqa: E402
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
    """Retired spec: shuffle snap ORDER within ``(nfl_id, game_id)``.

    This is **not** the TASK.md (2026-11-04) Phase-5 item and is deliberately
    not built: the binding item is the within-player **rest** permutation
    (:func:`dl.dl_idr_validate.rest_permutation`), which is implemented.
    """
    raise CheckNotImplemented(
        "within-player snap-ORDER permutation is NOT IMPLEMENTED in this build "
        "and is not asked for by TASK.md (2026-11-04): the binding Phase-5 "
        "item shuffles inter-snap REST within (rusher, game), not snap order")


def cv_vs_controls_only_baseline(frame: pd.DataFrame, cfg: Mapping) -> dict:
    """Retired spec: player-grouped CV of each load definition vs controls."""
    raise CheckNotImplemented(
        "CV-vs-controls-only baseline is NOT IMPLEMENTED in this build and is "
        "not asked for by TASK.md (2026-11-04) Phase 5")


def measurement_error_correction(frame: pd.DataFrame, cfg: Mapping) -> dict:
    """Retired spec: ceiling / load measurement-error-corrected estimates."""
    raise CheckNotImplemented(
        "measurement-error correction is NOT IMPLEMENTED in this build and is "
        "not asked for by TASK.md (2026-11-04) Phase 5")


STUBS = {
    "within_player_order_permutation": within_player_order_permutation,
    "cv_vs_controls_only_baseline": cv_vs_controls_only_baseline,
    "measurement_error_correction": measurement_error_correction,
}

#: Checks from the **retired** 2026-10-30 design §5.6 that are neither built nor
#: stubbed with a function.  They are not in the binding IDR spec; they get an
#: artefact row so an absent check cannot read as passed.
UNBUILT_CHECKS = {
    "placebo_pre_snap_features": (
        "load must not predict pre-snap alignment or depth; height and weight "
        "must not mimic the a0 effect after the §8 controls (retired spec; "
        "not in the 2026-11-04 IDR Phase 5)"),
    "rotation_and_survivorship": (
        "restriction to complete-coverage rusher-games and high-snap-share "
        "rushers, snaps by quarter, and the stay-probability-weighted version "
        "(retired spec; not in the 2026-11-04 IDR Phase 5)"),
    "mechanical_coupling": (
        "repeat the comparison with ceiling-fraction outcomes only, as a check "
        "that the confirmatory result rests on the ceiling-free outcomes "
        "(retired spec; not in the 2026-11-04 IDR Phase 5)"),
    "equivalence_tost": (
        "TOST against the prereg §12 margins (HR 1.05 / 0.005 nats per snap) "
        "with the informative-null interval rule (retired spec; not in the "
        "2026-11-04 IDR Phase 5)"),
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
    """**SUPERSEDED** (2026-10-30 spec): shuffle ceilings within position group.

    Kept in the tree for provenance.  It is not called by the default run path
    (``validate.ceiling_permutation.enabled: false``) because the binding
    TASK.md replaced the a0 -> pressure hypothesis with the IDR spec: a0 is now
    a baseline, not a predictor.  Re-enable only to reproduce the historical
    check deliberately.
    """
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
    lines: list[str] = ["# DL stage 05 — validation (IDR spec)\n"]
    if prov["reportable"]:
        lines.append("**Provenance: FULL.** These numbers come from the full-data "
                     "run and may be reported under the prereg's rules.\n")
    else:
        lines.append("**PROVENANCE: PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT.**\n")
        lines.append("> **No number in this directory may be quoted as a result.** "
                     "The validation machinery is exercised here; the verdict is "
                     "suppressed and no sample statistic is a finding. Reportable "
                     "numbers require `provenance == FULL`.\n")

    lines.append("## Verdict\n")
    lines.append(f"**{result['verdict']}** — {result['verdict_statement']}\n")

    checks = result["checks"]

    # ---- 1. within-player rest permutation ---------------------------------
    perm = checks.get("rest_permutation") or {}
    design = perm.get("design") or {}
    lines.append("## 1. Within-player rest permutation (implemented)\n")
    lines.append("`TASK.md` Phase 5: *\"Shuffle inter-snap rest times within a "
                 "player-game; the deficit effect should vanish.\"* Rest is "
                 "shuffled **within each (rusher, game) block only**, every other "
                 "column is left in place, and the Part-1 mixed model is refitted "
                 "through the identical ladder with the same player-clustered "
                 "inference.\n")
    lines.append(f"* scheme: **{design.get('scheme')}**, blocks "
                 f"`{'`, `'.join(design.get('block_columns') or [])}`, "
                 f"load window **{design.get('window')}** prior snaps")
    lines.append(f"* permutations: requested **{design.get('reps_requested')}**, "
                 f"valid **{design.get('reps_valid')}**, "
                 f"failed **{design.get('reps_failed')}**, seed "
                 f"**{design.get('seed')}** (`{design.get('seed_key')}`)")
    lines.append(f"* blocks: {design.get('n_blocks')} · permuted "
                 f"{design.get('n_blocks_permuted')} · singleton "
                 f"{design.get('n_blocks_singleton')} "
                 f"({design.get('n_snaps_in_singleton_blocks')} snaps cannot be "
                 f"permuted — counted, never dropped) · "
                 f"{design.get('n_values_permuted_per_rep')} rest values moved "
                 f"per replicate")
    if design.get("block_multiset_preserved") is None:
        lines.append("* integrity assertions: not evaluated — no replicate ran")
    else:
        lines.append(f"* integrity assertions: block multiset preserved "
                     f"**{design.get('block_multiset_preserved')}**, missingness "
                     f"preserved **{design.get('missingness_preserved')}**")
    if design.get("rung_counts"):
        lines.append(f"* rungs used across replicates: {design.get('rung_counts')}")
    lines.append("")
    obs = perm.get("observed") or {}
    inf = perm.get("inference") or {}
    if perm.get("status") != "OK":
        lines.append(f"**NOT ESTIMABLE** — {perm.get('reason')}")
        frame_info = obs.get("frame") or {}
        if frame_info:
            lines.append("")
            lines.append(f"* Part-1 frame on this run: {frame_info.get('n_complete')} "
                         f"complete rows of {frame_info.get('n_rows_in')}, "
                         f"{frame_info.get('n_players')} rushers, "
                         f"{frame_info.get('n_games')} games")
        lines.append("")
    else:
        lines.append(f"* observed interaction (standardised): "
                     f"**{_fmt(obs.get('coef_std'))}** "
                     f"(player-clustered SE {_fmt(obs.get('se_std'))}, "
                     f"p {_fmt(obs.get('p'))}, rung `{obs.get('rung_used')}`, "
                     f"n = {obs.get('n_snaps')} snaps / {obs.get('n_players')} rushers)")
        lines.append(f"* permutation null: mean {_fmt(inf.get('null_mean'))}, "
                     f"SD {_fmt(inf.get('null_sd'))}, "
                     f"95 % [{_fmt(inf.get('null_q025'))}, {_fmt(inf.get('null_q975'))}] "
                     f"over {inf.get('n_permutations')} refits")
        lines.append(f"* point estimate vs null: {inf.get('n_extreme')} of "
                     f"{inf.get('n_permutations')} replicates at least as extreme")
        lines.append(f"* permutation p (two-sided): **{_fmt(inf.get('p_two_sided'))}**; "
                     f"one-sided {_fmt(inf.get('p_one_sided'))} at alpha "
                     f"{_fmt(inf.get('alpha'), 2)}")
        reading = ("the observed interaction is **outside** the rest-shuffle "
                   "null, so the effect is carried by the real rest alignment — "
                   "shuffling rest does drive it to the null centre"
                   if inf.get("reject_null") else
                   "the observed interaction sits **inside** the rest-shuffle "
                   "null, so it is consistent with a chance arrangement of rest "
                   "within a player-game")
        lines.append(f"* reading: {reading}")
        lines.append("")

    # ---- 2. context controls ------------------------------------------------
    ctx = checks.get("context_controls") or {}
    lines.append("## 2. Context controls (implemented)\n")
    lines.append("`TASK.md` Phase 5: exclude garbage time, the final two minutes "
                 "of each half, and obvious passing situations. Each row refits "
                 "the same Part-1 ladder on the restricted sample; the windowed "
                 "load and the rest keep the values Phase 3 measured on the full "
                 "sequence (an exclusion changes the estimation sample, not the "
                 "measurement).\n")
    if ctx.get("status") != "OK" and not ctx.get("rows"):
        lines.append(f"**NOT ESTIMABLE** — {ctx.get('reason')}\n")
    else:
        lines.append("| exclusion | status | n rows | n excluded | interaction "
                     "(std) | clustered SE | p | rule |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in ctx.get("rows") or []:
            est = row.get("estimate") or {}
            rule = row.get("rule") or {}
            rule_text = _rule_text(rule)
            lines.append(
                f"| `{row.get('check')}` | {row.get('status')} | "
                f"{row.get('n_rows') if row.get('n_rows') is not None else 'NA'} | "
                f"{row.get('n_excluded') if row.get('n_excluded') is not None else 'NA'} | "
                f"{_fmt(est.get('coef_std'))} | {_fmt(est.get('se_std'))} | "
                f"{_fmt(est.get('p'))} | {rule_text} |")
        lines.append("")
        for row in ctx.get("rows") or []:
            if row.get("status") != "OK" and row.get("reason"):
                lines.append(f"* `{row.get('check')}` — **{row.get('status')}**: "
                             f"{row.get('reason')}")
        lines.append("")

    # ---- 3. superseded ceiling permutation ---------------------------------
    ceil = checks.get("ceiling_permutation") or {}
    lines.append("## 3. Ceiling permutation — **SUPERSEDED** (kept for provenance)\n")
    lines.append(f"* status: **{ceil.get('status')}** — {ceil.get('reason')}")
    lines.append("")
    if ceil.get("status") not in ("SUPERSEDED",):
        lines.extend(_stat_markdown("a0 effect on the pressure hazard",
                                    ceil.get("a0") or {}, "log HR per 1 SD"))
        lines.extend(_stat_markdown("relative-load effect",
                                    ceil.get("relative_load") or {},
                                    "log hazard per 1 SD load"))

    # ---- not implemented ----------------------------------------------------
    lines.append("## NOT IMPLEMENTED (never a pass)\n")
    for name, item in checks.items():
        if name in ("rest_permutation", "context_controls", "ceiling_permutation"):
            continue
        lines.append(f"* **{item.get('status')}** — `{name}`: {item.get('reason')}")
    lines.append("")

    lines.append("## Claim hierarchy\n")
    lines.append("* A permutation p is a statement about the observed interaction "
                 "under shuffled rest, not a confirmatory test; the Part-1 and "
                 "Part-2 fits remain the two confirmatory tests.")
    lines.append("* Nothing here re-tunes the exposure, the load window, the "
                 "controls, the rung ladder, the metric or any threshold.\n")
    lines.append("## What this stage did not do\n")
    lines.append("* It did not run the placebos, rotation/survivorship, "
                 "mechanical-coupling or equivalence checks: they belong to the "
                 "retired 2026-10-30 spec and are not in this Phase 5.")
    lines.append("* It did not define an \"obvious passing situation\": the repo "
                 "contains no such rule, so the check is reported "
                 "DEFINITION_UNDEFINED rather than invented.")
    lines.append("* It did not quote a sample number as a finding.\n")
    return "\n".join(lines)


def _rule_text(rule: Mapping) -> str:
    if not rule:
        return ""
    name = rule.get("rule")
    if not name:
        return str(rule.get("status") or "")
    parts = [f"`{name}`"]
    for key in ("q4", "q4_margin", "any_quarter_margin", "quarters", "seconds"):
        if rule.get(key) is not None:
            parts.append(f"{key}={rule[key]}")
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def _check_rows(result: Mapping) -> list[dict]:
    """One row per (check, statistic) for the status table."""
    rows: list[dict] = []
    perm = result["checks"].get("rest_permutation") or {}
    inf = perm.get("inference") or {}
    obs = perm.get("observed") or {}
    rows.append({
        "check": "rest_permutation", "status": perm.get("status"),
        "statistic": inf.get("observed", obs.get("coef_std")),
        "observed": obs.get("coef_std"),
        "null_mean": inf.get("null_mean"),
        "p_two_sided": inf.get("p_two_sided"),
        "reason": perm.get("reason") or inf.get("reason") or "",
    })
    ctx = result["checks"].get("context_controls") or {}
    for row in ctx.get("rows") or []:
        est = row.get("estimate") or {}
        rows.append({
            "check": f"context:{row.get('check')}", "status": row.get("status"),
            "statistic": est.get("coef_std"), "observed": est.get("coef_std"),
            "null_mean": "", "p_two_sided": est.get("p"),
            "reason": row.get("reason") or "",
        })
    ceil = result["checks"].get("ceiling_permutation") or {}
    if ceil.get("status") == "SUPERSEDED":
        rows.append({"check": "ceiling_permutation", "status": "SUPERSEDED",
                     "statistic": "", "observed": "", "null_mean": "",
                     "p_two_sided": "", "reason": ceil.get("reason") or ""})
    else:
        for statistic, key in (("a0_log_hr", "a0"),
                               ("relative_load_coef", "relative_load")):
            block = ceil.get(key) or {}
            block_inf = block.get("inference") or {}
            rows.append({
                "check": "ceiling_permutation",
                "status": block.get("status", ceil.get("status")),
                "statistic": statistic,
                "observed": block.get("value"),
                "null_mean": block_inf.get("null_mean"),
                "p_two_sided": block_inf.get("p_two_sided"),
                "reason": block.get("reason") or block_inf.get("reason") or "",
            })
    for name, item in result["checks"].items():
        if name in ("rest_permutation", "context_controls", "ceiling_permutation"):
            continue
        rows.append({"check": name, "status": item.get("status"),
                     "statistic": "", "observed": "", "null_mean": "",
                     "p_two_sided": "", "reason": item.get("reason") or ""})
    return rows


#: Which not-implemented retired checks belong in each artefact.
_ARTEFACT_CHECKS = {
    "placebo_checks.csv": ("placebo_pre_snap_features",),
    "rotation.csv": ("rotation_and_survivorship",),
}


def _context_rows(ctx: Mapping) -> list[dict]:
    rows: list[dict] = []
    for row in (ctx or {}).get("rows") or []:
        est = row.get("estimate") or {}
        rule = row.get("rule") or {}
        rows.append({
            "check": row.get("check"), "status": row.get("status"),
            "n_rows": row.get("n_rows"), "n_excluded": row.get("n_excluded"),
            "interaction_coef_std": est.get("coef_std"),
            "interaction_se_std": est.get("se_std"),
            "interaction_p": est.get("p"),
            "rung_used": est.get("rung_used"),
            "rule": rule.get("rule"), "rule_source": rule.get("config_source"),
            "reason": row.get("reason") or "",
        })
    return rows


_CONTEXT_FIELDS = ["check", "status", "n_rows", "n_excluded",
                   "interaction_coef_std", "interaction_se_std", "interaction_p",
                   "rung_used", "rule", "rule_source", "reason"]


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
    restr_cfg = dict(vcfg.get("rest_permutation") or {})
    frame, inputs = load_frame(cfg, mode)
    seed = int(block["seeds"][str(restr_cfg["seed_key"])])
    reps = int(restr_cfg["sample_reps"] if mode == "sample" else restr_cfg["reps"])

    # --- the two TASK.md Phase-5 items --------------------------------------
    rest = IV.rest_permutation(frame, block, reps=reps, seed=seed, mode=mode)
    ctx = IV.context_controls(frame, block, baseline=rest.get("observed"))
    null_values = list(rest.get("null_values") or [])
    rest_public = {k: v for k, v in rest.items() if k != "null_values"}

    # --- the retired ceiling permutation: NOT in the default run path --------
    ceil_cfg = dict(vcfg.get("ceiling_permutation") or {})
    if bool(ceil_cfg.get("enabled", False)):
        relative_column, column_source = _selected_relative_column(cfg, mode)
        ceil = ceiling_permutation(
            frame, cfg,
            reps=reps,
            seed=int(block["seeds"][str(vcfg["permutations"]["seed_key"])]),
            relative_column=relative_column)
        ceil["relative_load_column_source"] = column_source
        ceil["superseded"] = True
    else:
        ceil = {
            "status": "SUPERSEDED",
            "superseded": True,
            "reason": ("the ceiling permutation belongs to the retired 2026-10-30 "
                       "spec; TASK.md (2026-11-04) replaced it with the "
                       "within-player rest permutation. The code is kept, the "
                       "check is not run, and no result is carried forward."),
        }

    # Stubs: call them, record the refusal, never crash the stage.
    checks: dict[str, dict] = {
        "rest_permutation": rest_public,
        "context_controls": ctx,
        "ceiling_permutation": {k: v for k, v in ceil.items() if k != "rows"},
    }
    for name, fn in STUBS.items():
        try:
            fn(frame, cfg)
            checks[name] = {"status": "UNEXPECTEDLY_RAN",
                            "reason": "the stub returned instead of refusing"}
        except NotImplementedError as exc:
            checks[name] = {"status": "NOT_IMPLEMENTED", "reason": str(exc)}
    for name, reason in UNBUILT_CHECKS.items():
        checks[name] = {"status": "NOT_IMPLEMENTED", "reason": reason}

    if not prov["reportable"]:
        verdict = "NOT_INTERPRETED_SAMPLE"
        statement = ("Sample mode: the rest permutation and the context controls "
                     "ran as machinery checks and the verdict is suppressed. "
                     "Nothing here is a finding.")
    elif rest.get("status") != "OK":
        verdict = "NOT_ESTIMABLE"
        statement = str(rest.get("reason") or "the rest permutation could not be "
                                              "computed")
    elif (rest.get("inference") or {}).get("reject_null"):
        verdict = "REST_PERMUTATION_REJECTS"
        statement = ("The observed load x rest interaction lies outside the "
                     "within-(rusher, game) rest-shuffle null: shuffling rest "
                     "does move it to the null centre, so the effect depends on "
                     "the real rest alignment rather than on chance.")
    else:
        verdict = "CONSISTENT_WITH_REST_NULL"
        statement = ("The observed load x rest interaction sits inside the "
                     "within-(rusher, game) rest-shuffle null: shuffling rest "
                     "leaves it unchanged, so the data do not show the effect "
                     "the spec predicts.")

    result = {
        "stage": STAGE, "mode": mode,
        "spec": "TASK.md Phase 5 (IDR spec, 2026-11-04)",
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
        "superseded": ["ceiling_permutation"],
        "rest_permutation": rest_public,
        "context_controls": ctx,
        "ceiling_permutation": {k: v for k, v in ceil.items() if k != "rows"},
        "gap": {
            "check": "context_controls.exclude_obvious_passing",
            "status": "DEFINITION_UNDEFINED",
            "reason": ("TASK.md Phase 5 asks for an obvious-passing exclusion, "
                       "but no rule exists in config.yaml, notes/prereg.md or any "
                       "module. No threshold was chosen; see notes/blockers.md."),
        },
        "design": {
            "reps_requested": reps,
            "reps_configured_full": int(restr_cfg["reps"]),
            "seed": seed,
            "seed_key": str(restr_cfg["seed_key"]),
            "window": (rest.get("design") or {}).get("window"),
            "block_columns": (rest.get("design") or {}).get("block_columns"),
            "ceiling_permutation_enabled": bool(ceil_cfg.get("enabled", False)),
        },
        "counts": {
            "n_snaps": int(len(frame)),
            "n_players": int(frame["nfl_id"].nunique()),
        },
    }

    context_rows = _context_rows(ctx)
    csv_tables: dict[str, tuple[list[dict], list[str]]] = {
        "rest_permutation_null.csv": (
            [{"rep": rep + 1, "interaction_coef_std": value,
              "status": "OK"} for rep, value in enumerate(null_values)],
            ["rep", "interaction_coef_std", "status"]),
        "context_controls.csv": (context_rows, _CONTEXT_FIELDS),
        # legacy artefact name kept in step with the same rows (one source)
        "context_sensitivity.csv": (context_rows, _CONTEXT_FIELDS),
        "check_status.csv": (_check_rows(result),
                             ["check", "status", "statistic", "observed",
                              "null_mean", "p_two_sided", "reason"]),
    }
    if ceil.get("status") == "SUPERSEDED":
        csv_tables["permutation_null.csv"] = (
            [{"check": "ceiling_permutation", "rep": "", "statistic": "",
              "value": "", "status": "SUPERSEDED",
              "rung": "", "relative_load_column": ""}],
            ["check", "rep", "statistic", "value", "status", "rung",
             "relative_load_column"])
    else:
        csv_tables["permutation_null.csv"] = ([
            {k: row.get(k) for k in
             ("check", "rep", "statistic", "value", "status", "rung",
              "relative_load_column")} for row in (ceil.get("rows") or [])],
            ["check", "rep", "statistic", "value", "status", "rung",
             "relative_load_column"])
    for filename, names in _ARTEFACT_CHECKS.items():
        csv_tables[filename] = (
            [{"check": name, "status": checks[name]["status"],
              "reason": checks[name]["reason"]} for name in names],
            ["check", "status", "reason"])
    csv_tables["equivalence.csv"] = (
        [{"check": "equivalence_tost", "status": checks["equivalence_tost"]["status"],
          "reason": checks["equivalence_tost"]["reason"]}],
        ["check", "status", "reason"])

    payload = {
        "json": {
            "validation_result.json": result,
            "equivalence.json": {
                "status": checks["equivalence_tost"]["status"],
                "reason": checks["equivalence_tost"]["reason"],
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
        "rest_permutation": rest.get("status"),
        "context_controls": ctx.get("status"),
        "ceiling_permutation": ceil.get("status"),
        "not_implemented": len(result["not_implemented"]),
        "reportable": prov["reportable"]})
    return {"skipped": False, "path": out_dir, "result": result,
            "rest": rest, "ctx": ctx, "ceiling": ceil}


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
            rest = outcome["rest"]
            inf = rest.get("inference") or {}
            st.note(f"verdict={result['verdict']} rest={rest.get('status')} "
                    f"reps={result['design']['reps_requested']} "
                    f"not_implemented={len(result['not_implemented'])}")
            print(f"{STAGE}: wrote {outcome['path']}")
            print(f"  verdict: {result['verdict']}")
            print(f"  rest permutation: {rest.get('status')} · "
                  f"p_two={inf.get('p_two_sided')} · "
                  f"valid={((rest.get('design') or {}).get('reps_valid'))}")
            print(f"  context controls: {outcome['ctx'].get('status')} · "
                  f"{[r.get('status') for r in (outcome['ctx'].get('rows') or [])]}")
            print(f"  ceiling permutation: {outcome['ceiling'].get('status')}")
            print(f"  NOT IMPLEMENTED: {', '.join(result['not_implemented'])}")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
