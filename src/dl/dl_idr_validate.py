#!/usr/bin/env python3
"""dl_idr_validate — Phase 5 under the binding IDR spec (TASK.md, 2026-11-04).

The spec names **two** validation items, and this module implements exactly
those two:

1. **Within-Player Permutation** — *"Shuffle inter-snap rest times within a
   player-game; the deficit effect should vanish."*
2. **Context Controls** — *"Exclude garbage time, 2-minute drills
   (systematically different rest mechanics), and obvious passing
   situations."*

Nothing else.  The retired 2026-10-30 spec's placebos, rotation/survivorship
and mechanical-coupling panels are **not** in this spec and are not built.

Within-player rest permutation
------------------------------
Per replicate:

* shuffle ``inter_snap_rest_s`` **within each ``(nfl_id, game_id)`` block** —
  the rest multiset of every rusher-game is preserved exactly and no value
  ever crosses a player or game boundary (both asserted, see
  :func:`permute_rest_within_blocks`);
* leave the load, the outcome and every other column in place;
* refit the Part-1 mixed model through the **identical** production path
  (:func:`dl.dl_idr_models.build_idr_frame` -> :func:`fit_idr_ladder`), same
  rung ladder, same player-clustered inference.

The observed interaction is recomputed by that same path, so the observed
statistic and the null distribution are commensurable.  Reported: point
estimate, null mean / SD / 2.5 % / 97.5 % quantiles, two-sided and one-sided
permutation p, reps requested, reps valid, seed, and the snaps that could not
be permuted (singleton blocks) — counted, never dropped silently.

Context controls
----------------
The interaction is re-estimated on three restricted samples:

* **(a) excluding garbage time** — the rule and its margins are read from
  config (``bdb27_dl.validate.garbage_time`` +
  ``bdb27_dl.game.garbage_time``); the Phase-3 ``garbage_time`` column is used
  when present and cross-checked against the config rule;
* **(b) excluding the final two minutes of each half** — quarters ``[2, 4]``
  with ``game_clock_s <= 120``; the clock threshold and the quarters are read
  from config;
* **(c) excluding obvious passing situations** — **the repository defines no
  rule for this** (neither ``config.yaml`` nor ``notes/prereg.md`` says what
  makes a passing situation "obvious").  Conor skipped this control by
  decision on 2026-11-04 (``enabled: false``, blocker B-DL-IDR-01), so the row
  reports ``SKIPPED_BY_DECISION``.  If it were re-enabled with a ``rule`` of
  ``null`` it would still report ``DEFINITION_UNDEFINED``; a threshold is never
  chosen here.

The load and rest of a filtered row keep the values Phase 3 measured on the
**full** sequence: restricting the sample must not silently redefine the
window-3 load as "the three preceding *remaining* snaps".  Only the
estimation sample changes; standardisation is recomputed on it, as the model
declares.

Honesty
-------
Sample mode stamps everything ``UNVALIDATED SAMPLE OUTPUT`` and returns
``NOT_INTERPRETED_SAMPLE``.  A check that cannot be computed is
``NOT_ESTIMABLE`` with its reason — never a pass, never a zero.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from dl import dl_idr_models as IDR

__all__ = [
    "REST_PERMUTATION_DEFAULTS",
    "CONTEXT_DEFAULTS",
    "block_indices",
    "permute_rest_within_blocks",
    "interaction_statistic",
    "rest_permutation",
    "context_controls",
    "run_idr_validation",
]

REST_PERMUTATION_DEFAULTS: dict[str, Any] = {
    "scheme": "within_player_game",
    "block_columns": ["nfl_id", "game_id"],
    "reps": 199,
    "sample_reps": 9,
    "seed_key": "permutation",
    "window": None,
    "min_permutable_fraction": 0.5,
}

CONTEXT_DEFAULTS: dict[str, Any] = {
    "garbage_time": {"enabled": True, "rule": "quarter_and_margin"},
    "final_two_minutes": {"enabled": True, "rule": "quarter_and_clock",
                          "quarters": [2, 4]},
    "obvious_passing": {"enabled": True, "rule": None},
}


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #
def _idr_cfg(block: Mapping) -> Mapping:
    return dict(block.get("idr_part1") or {})


def _vcfg(block: Mapping) -> Mapping:
    return dict(block.get("validate") or {})


def _restr_cfg(block: Mapping) -> dict:
    merged = dict(REST_PERMUTATION_DEFAULTS)
    merged.update({k: v for k, v in
                   dict(_vcfg(block).get("rest_permutation") or {}).items()
                   if v is not None})
    return merged


def _context_cfg(block: Mapping) -> dict:
    merged = {key: dict(value) for key, value in CONTEXT_DEFAULTS.items()}
    given = dict(_vcfg(block).get("context_controls") or {})
    for key, value in given.items():
        if isinstance(value, Mapping):
            merged.setdefault(key, {}).update(
                {k: v for k, v in dict(value).items() if v is not None})
        else:
            merged[key] = value
    return merged


def _window(block: Mapping, cfg: Mapping) -> int:
    configured = cfg.get("window")
    if configured is not None:
        return int(configured)
    windows = dict(_idr_cfg(block).get("windows") or {})
    return int(windows.get("primary", IDR.IDR_DEFAULTS["windows"]["primary"]))


# --------------------------------------------------------------------------- #
# Permutation machinery
# --------------------------------------------------------------------------- #
def block_indices(frame: pd.DataFrame, block_columns: list[str]
                  ) -> dict[tuple, np.ndarray]:
    """Row positions of every ``(rusher, game)`` block, deterministically."""
    keys = frame[block_columns].to_numpy()
    buckets: dict[tuple, list[int]] = {}
    for pos, key in enumerate(keys):
        buckets.setdefault(tuple(key), []).append(int(pos))
    return {key: np.asarray(buckets[key], dtype=int) for key in sorted(buckets)}


def _verify(values_before: np.ndarray, values_after: np.ndarray,
            blocks: Mapping[tuple, np.ndarray]) -> dict[str, Any]:
    """Integrity assertions for one permutation.

    Two properties are required by the spec and are *asserted*, not merely
    reported:

    * each block's finite rest **multiset** is preserved (so no value is
      created, destroyed or moved out of its block — i.e. never across a
      player or game);
    * the missingness pattern is unchanged, so the complete-case sample is the
      same set of snaps in every replicate.
    """
    multiset_ok = True
    missing_ok = True
    for idx in blocks.values():
        before = values_before[idx]
        after = values_after[idx]
        if not np.array_equal(np.isfinite(before), np.isfinite(after)):
            missing_ok = False
        finite = np.isfinite(before)
        if not np.array_equal(np.sort(before[finite]), np.sort(after[finite])):
            multiset_ok = False
    report = {
        "block_multiset_preserved": bool(multiset_ok),
        "missingness_preserved": bool(missing_ok),
    }
    if not multiset_ok:
        raise AssertionError(
            "rest permutation violated the block multiset: a rest value was "
            "created, destroyed or moved across a (rusher, game) boundary")
    if not missing_ok:
        raise AssertionError(
            "rest permutation moved missingness: the complete-case sample "
            "would change between replicates")
    return report


def permute_rest_within_blocks(frame: pd.DataFrame, column: str, rng,
                               blocks: Mapping[tuple, np.ndarray] | None = None,
                               block_columns: list[str] | None = None
                               ) -> tuple[np.ndarray, dict[str, Any]]:
    """One permutation of ``column`` within ``(rusher, game)`` blocks.

    Returns the permuted values (aligned to ``frame.index``) and a report that
    counts the blocks that could not be permuted: a block with fewer than two
    finite rest values has nothing to swap and keeps its own values — those
    snaps are **reported**, never dropped.
    """
    columns = list(block_columns or REST_PERMUTATION_DEFAULTS["block_columns"])
    blocks = blocks if blocks is not None else block_indices(frame, columns)
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    out = values.copy()

    n_block_total = len(blocks)
    n_block_permuted = 0
    n_block_singleton = 0
    n_snaps_singleton = 0
    n_values_permuted = 0
    identity_blocks = 0

    for idx in blocks.values():
        finite = idx[np.isfinite(values[idx])]
        if finite.size < 2:
            n_block_singleton += 1
            n_snaps_singleton += int(idx.size)
            continue
        order = rng.permutation(finite.size)
        out[finite] = values[finite][order]
        n_block_permuted += 1
        n_values_permuted += int(finite.size)
        identity_blocks += int(np.array_equal(out[finite], values[finite]))

    integrity = _verify(values, out, blocks)
    n_permutable_blocks = n_block_total - n_block_singleton
    report = {
        "block_columns": columns,
        "n_blocks": int(n_block_total),
        "n_blocks_permuted": int(n_block_permuted),
        "n_blocks_singleton": int(n_block_singleton),
        "n_snaps_in_singleton_blocks": int(n_snaps_singleton),
        "n_values_permuted": int(n_values_permuted),
        "n_identity_blocks": int(identity_blocks),
        "permutable_fraction": (float(n_permutable_blocks) / n_block_total
                                if n_block_total else 0.0),
        **integrity,
    }
    return out, report


# --------------------------------------------------------------------------- #
# The Part-1 statistic, through the production code path
# --------------------------------------------------------------------------- #
def _standardise(work: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Re-standardise load and rest on the estimation sample (the model rule)."""
    load = pd.to_numeric(work["_idr_load"], errors="coerce")
    rest = pd.to_numeric(work["_idr_rest"], errors="coerce")
    sd_load = float(load.std(ddof=0))
    sd_rest = float(rest.std(ddof=0))
    if not (np.isfinite(sd_load) and sd_load > 0
            and np.isfinite(sd_rest) and sd_rest > 0):
        return work, {"status": "NOT_ESTIMABLE",
                      "reason": "load or rest has zero variance on the "
                                "estimation sample"}
    out = work.copy()
    out["_z_load"] = (load - load.mean()) / sd_load
    out["_z_rest"] = (rest - rest.mean()) / sd_rest
    out["_z_int"] = out["_z_load"] * out["_z_rest"]
    return out, {
        "status": "OK",
        "load_mean": float(load.mean()), "load_sd": sd_load,
        "rest_mean": float(rest.mean()), "rest_sd": sd_rest,
    }


def _fit_work(work: pd.DataFrame, report: Mapping, idr_cfg: Mapping) -> dict:
    """Fit the declared ladder on a prepared work frame (no re-windowing)."""
    work, scale = _standardise(work)
    if scale.get("status") != "OK":
        return {"status": "NOT_ESTIMABLE", "reason": scale.get("reason")}
    report = {**dict(report), "scale": scale}
    fit = IDR.fit_idr_ladder(work, report, idr_cfg)
    if fit.get("status") != "OK":
        return {"status": "NOT_ESTIMABLE",
                "reason": fit.get("reason") or "Part-1 ladder did not estimate "
                                               "the interaction",
                "ladder": fit.get("ladder")}
    est = fit["interaction"]
    return {
        "status": "OK",
        "coef_std": float(est["coef_std"]),
        "se_std": float(est["se_std"]),
        "p": float(est["p"]) if est.get("p") is not None else None,
        "coef_raw": float(est["coef_raw"]),
        "raw_unit": est.get("raw_unit"),
        "rung_used": fit.get("rung_used"),
        "n_snaps": int(fit.get("n_snaps") or 0),
        "n_players": int(fit.get("n_players") or 0),
        "design_effect": fit.get("design_effect"),
    }


def prepare_idr(block: Mapping, frame: pd.DataFrame,
                window: int | None = None) -> tuple[pd.DataFrame, dict, int]:
    """Complete-case Part-1 frame built once on the **full** snap sequence."""
    idr_cfg = _idr_cfg(block)
    win = int(window if window is not None else _window(block, _restr_cfg(block)))
    work, report = IDR.build_idr_frame(frame, idr_cfg, win)
    return work, report, win


def interaction_statistic(frame: pd.DataFrame, block: Mapping,
                          window: int | None = None) -> dict:
    """The Part-1 load x rest interaction, fitted by the production ladder."""
    idr_cfg = _idr_cfg(block)
    work, report, win = prepare_idr(block, frame, window)
    if report.get("status") != "OK":
        return {"status": "NOT_ESTIMABLE", "window": win,
                "reason": report.get("reason") or "the Part-1 frame is not usable",
                "frame": report}
    fit = _fit_work(work, report, idr_cfg)
    fit["window"] = win
    fit["frame"] = {k: v for k, v in report.items() if k != "scale"}
    return fit


# --------------------------------------------------------------------------- #
# Permutation inference
# --------------------------------------------------------------------------- #
def _null_summary(observed: float, null: list[float], alpha: float) -> dict:
    arr = np.asarray([v for v in null if v is not None and np.isfinite(v)], float)
    n = int(arr.size)
    if n == 0:
        return {"status": "NOT_ESTIMABLE",
                "reason": "no finite permutation statistic"}
    observed = float(observed)
    extreme = int(np.sum(np.abs(arr) >= abs(observed) - 1e-15))
    greater = int(np.sum(arr >= observed - 1e-15))
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
        "p_two_sided": float((1.0 + extreme) / (1.0 + n)),
        "p_one_sided": float((1.0 + greater) / (1.0 + n)),
        "alpha": float(alpha),
        "reject_null": bool((1.0 + extreme) / (1.0 + n) <= alpha),
    }


def rest_permutation(frame: pd.DataFrame, block: Mapping, *,
                     reps: int | None = None, seed: int | None = None,
                     mode: str = "sample") -> dict:
    """Shuffle rest within (rusher, game); refit; compare with the null."""
    cfg = _restr_cfg(block)
    idr_cfg = _idr_cfg(block)
    alpha = float(idr_cfg.get("alpha", IDR.IDR_DEFAULTS["alpha"]))
    if reps is None:
        reps = int(cfg["sample_reps"] if mode == "sample" else cfg["reps"])
    if seed is None:
        seed = int((block.get("seeds") or {}).get(str(cfg["seed_key"]), 0))
    window = _window(block, cfg)
    columns = [str(c) for c in cfg["block_columns"]]

    design = {
        "scheme": str(cfg["scheme"]),
        "block_columns": columns,
        "reps_requested": int(reps),
        "seed": int(seed),
        "seed_key": str(cfg["seed_key"]),
        "window": int(window),
        "statistic": "z_load_x_z_rest coefficient on log(deficit_ratio)",
        "p_value_rule": "(1 + #{|null| >= |observed|}) / (reps + 1), two-sided",
        "mode": mode,
    }

    if not len(frame):
        return {"status": "NOT_ESTIMABLE", "reason": "empty snap table",
                "design": design}

    blocks = block_indices(frame, columns)
    n_blocks = len(blocks)
    n_singleton_blocks = 0
    n_snaps_singleton = 0
    rest_values = pd.to_numeric(frame[str(IDR._cfg(idr_cfg)["rest_column"])],
                                errors="coerce").to_numpy(dtype=float)
    for idx in blocks.values():
        if int(np.isfinite(rest_values[idx]).sum()) < 2:
            n_singleton_blocks += 1
            n_snaps_singleton += int(idx.size)
    census = {
        "n_blocks": int(n_blocks),
        "n_blocks_singleton": int(n_singleton_blocks),
        "n_snaps_in_singleton_blocks": int(n_snaps_singleton),
        "permutable_fraction": (float(n_blocks - n_singleton_blocks) / n_blocks
                                if n_blocks else 0.0),
    }

    observed = interaction_statistic(frame, block, window)
    if observed.get("status") != "OK":
        return {"status": "NOT_ESTIMABLE",
                "reason": observed.get("reason"),
                "frame": observed.get("frame"),
                "design": {**design, **census, "reps_valid": 0,
                           "reps_failed": 0, "n_blocks_permuted": 0,
                           "n_values_permuted_per_rep": 0},
                "observed": {k: v for k, v in observed.items() if k != "frame"}}

    rng = np.random.default_rng(int(seed))
    column = str(IDR._cfg(idr_cfg)["rest_column"])

    null: list[float] = []
    failed: list[dict] = []
    rung_counts: dict[str, int] = {}
    singleton_snaps = 0
    permuted_values = 0
    perm_report: dict[str, Any] = {}
    for rep in range(int(reps)):
        values, perm_report = permute_rest_within_blocks(
            frame, column, rng, blocks=blocks, block_columns=columns)
        singleton_snaps = int(perm_report["n_snaps_in_singleton_blocks"])
        permuted_values = int(perm_report["n_values_permuted"])
        permuted = frame.copy()
        permuted[column] = values
        stat = interaction_statistic(permuted, block, window)
        if stat.get("status") == "OK":
            null.append(float(stat["coef_std"]))
            rung_counts[str(stat.get("rung_used"))] = rung_counts.get(
                str(stat.get("rung_used")), 0) + 1
        else:
            failed.append({"rep": rep + 1, "reason": stat.get("reason")})

    inference = _null_summary(observed["coef_std"], null, alpha)
    min_valid = max(1, int(math.ceil(
        float(cfg["min_permutable_fraction"]) * max(1, int(reps)))))
    status = "OK" if inference.get("status") == "OK" and len(null) >= min_valid \
        else "NOT_ESTIMABLE"
    out = {
        "status": status,
        "design": {
            **design,
            "reps_valid": int(len(null)),
            "reps_failed": int(len(failed)),
            "min_valid_reps": int(min_valid),
            "n_blocks": int(perm_report.get("n_blocks", len(blocks))),
            "n_blocks_permuted": int(perm_report.get("n_blocks_permuted", 0)),
            "n_blocks_singleton": int(perm_report.get("n_blocks_singleton", 0)),
            "n_snaps_in_singleton_blocks": singleton_snaps,
            "n_values_permuted_per_rep": permuted_values,
            "block_multiset_preserved": perm_report.get("block_multiset_preserved"),
            "missingness_preserved": perm_report.get("missingness_preserved"),
            "permutable_fraction": perm_report.get("permutable_fraction"),
            "rung_counts": rung_counts,
        },
        "observed": observed,
        "inference": inference,
        "null_values": null,
        "failures": failed[:20],
    }
    if status != "OK" and inference.get("status") == "OK":
        out["reason"] = (f"only {len(null)} valid refits, below the declared "
                         f"minimum of {min_valid}")
    return out


# --------------------------------------------------------------------------- #
# Context controls
# --------------------------------------------------------------------------- #
def _garbage_mask(frame: pd.DataFrame, block: Mapping, cfg: Mapping
                  ) -> tuple[pd.Series, dict]:
    """Garbage time, from the config rule (never a threshold invented here)."""
    rule = str(cfg.get("rule") or "")
    gcfg = dict((block.get("game") or {}).get("garbage_time") or {})
    q4 = int(cfg.get("q4", gcfg.get("q4", 4)))
    q4_margin = gcfg.get("q4_margin")
    any_margin = gcfg.get("any_quarter_margin")
    base = {"rule": rule, "q4": q4, "q4_margin": q4_margin,
            "any_quarter_margin": any_margin,
            "config_source": "bdb27_dl.game.garbage_time"}

    if rule != "quarter_and_margin":
        return pd.Series(False, index=frame.index), {
            **base, "status": "DEFINITION_UNDEFINED",
            "reason": f"garbage-time rule {rule!r} is not implemented"}

    if "garbage_time" in frame.columns:
        column = frame["garbage_time"]
        determined = column.notna()
        mask = column.fillna(False).astype(bool)
        source = "phase3_garbage_time_column"
        mismatch, checked = None, 0
        if {"quarter", "score_margin"} <= set(frame.columns):
            recomputed = _garbage_from_rule(frame, q4, q4_margin, any_margin)
            both = determined & recomputed.notna()
            checked = int(both.sum())
            mismatch = int((mask[both] != recomputed[both].astype(bool)).sum())
        return mask, {**base, "status": "OK", "source": source,
                      "n_undetermined": int((~determined).sum()),
                      "crosscheck_rows": checked,
                      "crosscheck_mismatches": mismatch}

    if not {"quarter", "score_margin"} <= set(frame.columns):
        return pd.Series(False, index=frame.index), {
            **base, "status": "NOT_ESTIMABLE",
            "reason": ("no garbage_time column and no quarter/score_margin "
                       "columns: the config rule cannot be applied")}
    recomputed = _garbage_from_rule(frame, q4, q4_margin, any_margin)
    determined = recomputed.notna()
    return recomputed.fillna(False).astype(bool), {
        **base, "status": "OK", "source": "config_rule_recomputed",
        "n_undetermined": int((~determined).sum()),
        "crosscheck_rows": 0, "crosscheck_mismatches": None}


def _garbage_from_rule(frame: pd.DataFrame, q4: int,
                       q4_margin: Any, any_margin: Any) -> pd.Series:
    quarter = pd.to_numeric(frame["quarter"], errors="coerce")
    margin = pd.to_numeric(frame["score_margin"], errors="coerce").abs()
    if q4_margin is None or any_margin is None:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    out = ((quarter >= q4) & (margin >= float(q4_margin))) \
        | (margin >= float(any_margin))
    out = out.astype(float)
    out[quarter.isna() | margin.isna()] = np.nan
    return out


def _final_two_mask(frame: pd.DataFrame, block: Mapping, cfg: Mapping
                    ) -> tuple[pd.Series, dict]:
    vcfg = _vcfg(block)
    rule = str(cfg.get("rule") or "")
    quarters = [int(q) for q in (cfg.get("quarters") or [2, 4])]
    seconds = float(cfg.get("seconds", vcfg.get("final_two_minutes_s", 120)))
    base = {"rule": rule, "quarters": quarters, "seconds": seconds,
            "config_source": "bdb27_dl.validate.final_two_minutes_s"}
    if rule != "quarter_and_clock":
        return pd.Series(False, index=frame.index), {
            **base, "status": "DEFINITION_UNDEFINED",
            "reason": f"final-two-minute rule {rule!r} is not implemented"}
    if not {"quarter", "game_clock_s"} <= set(frame.columns):
        return pd.Series(False, index=frame.index), {
            **base, "status": "NOT_ESTIMABLE",
            "reason": ("no quarter/game_clock_s columns: the final two "
                       "minutes of each half cannot be identified")}
    quarter = pd.to_numeric(frame["quarter"], errors="coerce")
    clock = pd.to_numeric(frame["game_clock_s"], errors="coerce")
    mask = quarter.isin(quarters) & (clock <= seconds)
    determined = quarter.notna() & clock.notna()
    return mask.fillna(False).astype(bool), {
        **base, "status": "OK", "source": "config_rule_recomputed",
        "n_undetermined": int((~determined).sum())}


def _obvious_passing(cfg: Mapping) -> dict:
    """Report the configured state; invent no rule."""
    rule = cfg.get("rule")
    if not bool(cfg.get("enabled", True)):
        return {
            "status": "SKIPPED_BY_DECISION",
            "reason": ("skipped by Conor's decision on 2026-11-04 "
                       "(notes/blockers.md B-DL-IDR-01, notes/decisions.md "
                       "D83); this context control was not run."),
            "rule": None,
        }
    if rule is None:
        return {
            "status": "DEFINITION_UNDEFINED",
            "reason": ("TASK.md Phase 5 asks for an 'obvious passing "
                       "situations' exclusion, but neither config.yaml nor "
                       "notes/prereg.md defines which situations are obvious "
                       "(no down/distance/score/formation rule exists). No "
                       "threshold is chosen here; the gap is recorded in "
                       "notes/blockers.md."),
            "rule": None,
        }
    return {"status": "DEFINITION_UNDEFINED",
            "reason": (f"configured obvious-passing rule {rule!r} has no "
                       f"implementation and no definition in notes/prereg.md"),
            "rule": rule}


def context_controls(frame: pd.DataFrame, block: Mapping,
                     baseline: Mapping | None = None) -> dict:
    """The three TASK.md context exclusions, each re-estimating the interaction."""
    cfg = _context_cfg(block)
    window = _window(block, _restr_cfg(block))
    base = baseline if baseline is not None else interaction_statistic(
        frame, block, window)
    work, report, win = prepare_idr(block, frame, window)
    rows: list[dict] = []

    def _row(name: str, mask: pd.Series | None, info: Mapping,
             applied: bool) -> dict:
        if not applied:
            return {"check": name, "status": info.get("status"),
                    "reason": info.get("reason"), "n_rows": None,
                    "n_excluded": None, "estimate": None,
                    "rule": info}
        if report.get("status") != "OK" or not len(work):
            n_rows = int(len(work)) if len(work) else 0
            n_excl = 0
            if n_rows and mask is not None:
                n_excl = int(mask.reindex(work.index).fillna(False).astype(bool).sum())
            return {"check": name, "status": "NOT_ESTIMABLE",
                    "reason": (report.get("reason")
                               or "the Part-1 frame is not usable, so the "
                                  "restricted sample cannot be estimated"),
                    "n_rows": n_rows, "n_excluded": n_excl,
                    "estimate": None, "rule": info}
        keep = ~mask.reindex(work.index).fillna(False).astype(bool)
        subset = work.loc[keep]
        n_excluded = int(len(work) - len(subset))
        if not len(subset):
            return {"check": name, "status": "NOT_ESTIMABLE",
                    "reason": "the exclusion removes every estimable snap",
                    "n_rows": 0, "n_excluded": n_excluded,
                    "estimate": None, "rule": info}
        fit = _fit_work(subset, report, _idr_cfg(block))
        return {"check": name, "status": fit.get("status"),
                "reason": fit.get("reason"), "n_rows": int(len(subset)),
                "n_excluded": n_excluded, "estimate": fit, "rule": info}

    # reference row: every estimable snap (no exclusion)
    rows.append({
        "check": "baseline_all_snaps", "status": base.get("status"),
        "reason": base.get("reason"),
        "n_rows": base.get("n_snaps"), "n_excluded": 0,
        "estimate": base if base.get("status") == "OK" else None,
        "rule": {"rule": None},
    })

    g_cfg = dict(cfg["garbage_time"])
    g_mask, g_info = _garbage_mask(frame, block, g_cfg)
    rows.append(_row("exclude_garbage_time", g_mask, g_info,
                     bool(g_cfg.get("enabled", True))))

    f_cfg = dict(cfg["final_two_minutes"])
    f_mask, f_info = _final_two_mask(frame, block, f_cfg)
    rows.append(_row("exclude_final_two_minutes", f_mask, f_info,
                     bool(f_cfg.get("enabled", True))))

    rows.append(_row("exclude_obvious_passing", None, _obvious_passing(
        dict(cfg["obvious_passing"])), False))

    return {"status": "OK" if base.get("status") == "OK" else "NOT_ESTIMABLE",
            "window": int(win), "baseline": base, "rows": rows,
            "note": ("each row re-fits the same Part-1 ladder on the same "
                     "controls; the windowed load and the rest keep the "
                     "values Phase 3 measured on the full sequence, so an "
                     "exclusion changes the estimation sample, not the "
                     "measurement")}


# --------------------------------------------------------------------------- #
# Entry point used by stage 05
# --------------------------------------------------------------------------- #
def run_idr_validation(frame: pd.DataFrame, block: Mapping, mode: str,
                       reps: int | None = None, seed: int | None = None
                       ) -> dict:
    """Both TASK.md Phase-5 items, plus the honest gap record."""
    restr = rest_permutation(frame, block, reps=reps, seed=seed, mode=mode)
    ctx = context_controls(frame, block, baseline=restr.get("observed"))
    return {
        "spec": "TASK.md Phase 5 (IDR spec, 2026-11-04)",
        "estimand": ("rolling kinematic load x inter-snap rest interaction on "
                     "log(deficit_ratio)"),
        "rest_permutation": {k: v for k, v in restr.items()
                             if k != "null_values"},
        "rest_permutation_null_values": restr.get("null_values") or [],
        "context_controls": ctx,
        "mode": mode,
        "sample_output": mode != "full",
    }
