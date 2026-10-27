#!/usr/bin/env python3
"""09_draft_and_target.py — two EXPLORATORY probes around the group contrast.

**Analysis-only.** Nothing here rebuilds a pipeline stage, and nothing here enters the
pre-registered §8 panel or can change the verdict. It re-uses ``07_security_models`` row
construction (``build_effort_rows``), design matrix (``design``), estimator (``ols``), thin-group
rule (``_mark_thin``) and ICC attenuation (``_apply_attenuation``). The statistics are therefore
identical to block B; only the *sample* and the *extra covariate* change.

PART A — what draft capital did the two sort populations actually get?
    The sort key (``edp_trend``) is pre-Combine. Actual draft slot is POST-Combine. Joining
    ``players.csv`` to the 07 group assignment lets us ask whether ``moved_down`` players really
    were drafted later, and whether the group coefficient survives controlling for — or within —
    realised draft capital. This is a mechanism/diagnostic probe, NOT a clean adjustment:
    conditioning on a post-Combine outcome can open a collider (Combine performance -> draft slot,
    and Combine performance -> game speed). A significant group difference in draft slot is BOTH
    evidence that the pre-Combine sort key tracks a real market outcome AND a live confound
    (worse draft capital -> worse QB/team/opportunity).

PART B — targeted routes only?
    ``efforts.csv`` carries a ``target`` flag. The analysis derives it by ONE documented key join on
    (``nfl_id``, ``game_id``, ``play_id``) and asserts it agrees with any in-row value. It then
    refits block B on ``target == True``, ``target == False`` and all rows. Conditioning on being
    targeted is selection on a post-treatment, role-dependent variable (target share depends on QB,
    scheme and depth chart), so target-only is NOT a clean estimate. The split is a decomposition:
    the non-target (decoy) subset is where the mechanical "peak speed is an opportunity-limited
    maximum" suppression should live.

Discipline: every CSV starts with ``provenance``; a thin group is reported and never fitted; a
subset below ``primary.min_obs`` is reported, not forced. Outputs go to
``outputs/security_models/draft_and_target/`` only. No existing result file is modified.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"
NOT_A_TEST = "exploratory — not a pre-declared test"

DRAFT_COLS = ["nfl_id", "draft_year", "draft_round", "draft_pick_within_round",
              "draft_overall_pick"]
TARGET_COLS = ["nfl_id", "game_id", "play_id", "target"]


# --------------------------------------------------------------------------- #
# reuse stage 07 / 08 machinery (never fork the statistics)
# --------------------------------------------------------------------------- #
def _load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_stage07():
    return _load_path("_bdb27_07_security_models_dt",
                      Path(__file__).with_name("07_security_models.py"))


def load_stage08():
    return _load_path("_bdb27_08_route_robustness_dt",
                      Path(__file__).with_name("08_route_robustness.py"))


def _f(v: Any) -> Any:
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return "" if v is None or not np.isfinite(float(v)) else float(v)
    return v


def emit(path: Path, rows: Sequence[dict], provenance: str,
         columns: Sequence[str] | None = None) -> None:
    """Write a CSV with ``provenance`` first and a stable column order."""
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(columns or [])
    if not cols:
        seen: list[str] = []
        for r in rows:
            for k in r:
                if k != "provenance" and k not in seen:
                    seen.append(k)
        cols = seen
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["provenance", *cols], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"provenance": provenance, **{k: _f(r.get(k)) for k in cols}})


# --------------------------------------------------------------------------- #
# config / inputs
# --------------------------------------------------------------------------- #
def _dt(cfg: dict) -> dict:
    return cfg.get("draft_target", {}) or {}


def _min_obs(cfg: dict) -> int:
    v = _dt(cfg).get("min_obs")
    return int(v) if v is not None else int(cfg["security_models"]["primary"]["min_obs"])


def _min_group_n(cfg: dict) -> int:
    v = _dt(cfg).get("min_group_n")
    return int(v) if v is not None else int(cfg["security_models"]["sort"]["min_group_n"])


def _round_bands(cfg: dict) -> list[tuple[int, int]]:
    bands = _dt(cfg).get("round_bands") or [[1, 1], [2, 2], [3, 4], [5, 7]]
    return [(int(a), int(b)) for a, b in bands]


def _strata_bands(cfg: dict) -> list[tuple[int, int]]:
    bands = _dt(cfg).get("strata_bands") or [[1, 2], [3, 4], [5, 7]]
    return [(int(a), int(b)) for a, b in bands]


def band_label(rnd: Any, bands: Sequence[tuple[int, int]]) -> str | None:
    try:
        r = int(rnd)
    except (TypeError, ValueError):
        return None
    for lo, hi in bands:
        if lo <= r <= hi:
            return f"R{lo}" if lo == hi else f"R{lo}-{hi}"
    return None


def draft_path(cfg: dict) -> Path:
    spec = _dt(cfg).get("draft_file")
    if spec:
        p = Path(str(spec)).expanduser()
        return p if p.is_absolute() else common.resolve(cfg, "raw", str(spec))
    return common.csv_path(cfg, "players")


def required_inputs(cfg: dict, synthetic: bool, regime: str) -> list[tuple[str, Path]]:
    """Every file this analysis needs, labelled. Used by the notebook §10 to say what is missing."""
    sm = load_stage07()
    inp = sm._inputs(cfg, synthetic, regime)
    checks = [
        ("top-k efforts (model rows)", inp["topk"]),
        ("player summary", inp["players"]),
        ("route snap counts (season scope, D49)", inp["snap_counts"]),
        ("games.csv (true season, D49)", inp["games"]),
        ("efforts (target key join)", inp["efforts"]),
        ("security exposure", inp["exposure"]),
        ("Combine envelope", inp["envelope"]),
        ("draft outcomes (players.csv)", draft_path(cfg)),
    ]
    return [(label, Path(p)) for label, p in checks if not Path(p).exists()]


def load_draft(con, path: Path, sm) -> tuple[dict[int, dict], dict]:
    """Draft outcomes keyed by ``nfl_id``. Duplicates are counted, never silently averaged."""
    sm.assert_input_columns(con, path, DRAFT_COLS)
    q = con.execute(
        f"select nfl_id, draft_year, draft_round, draft_pick_within_round, draft_overall_pick "
        f"from read_csv_auto('{path}')")
    out: dict[int, dict] = {}
    duplicates: list[int] = []
    n_rows = 0
    for nid, year, rnd, pick_in_round, overall in q.fetchall():
        n_rows += 1
        try:
            key = int(nid)
        except (TypeError, ValueError):
            continue
        pick = sm._num(overall)
        rec = dict(draft_year=(int(year) if sm._num(year) is not None else None),
                   draft_round=(int(rnd) if sm._num(rnd) is not None else None),
                   draft_pick_within_round=(int(pick_in_round)
                                            if sm._num(pick_in_round) is not None else None),
                   draft_overall_pick=(int(round(pick)) if pick is not None else None))
        if key in out:
            duplicates.append(key)
            if out[key].get("draft_overall_pick") is None:
                out[key] = rec
            continue
        out[key] = rec
    meta = dict(n_rows=n_rows, n_unique=len(out), n_duplicate_ids=len(set(duplicates)),
                n_missing_overall_pick=sum(1 for v in out.values()
                                           if v.get("draft_overall_pick") is None))
    return out, meta


def load_target_map(con, path: Path, sm) -> dict[tuple[int, int, int], bool | None]:
    """The ONE documented target source: ``efforts.csv`` keyed by (nfl_id, game_id, play_id)."""
    sm.assert_input_columns(con, path, TARGET_COLS)
    q = con.execute(
        f"select nfl_id, game_id, play_id, target from read_csv_auto('{path}')")
    out: dict[tuple[int, int, int], bool | None] = {}
    conflicts: list[tuple[int, int, int]] = []
    for nid, gid, pid, tgt in q.fetchall():
        key = (int(nid), int(gid), int(pid))
        val = sm._bool(tgt)
        if key not in out or out[key] is None:
            out[key] = val
        elif val is not None and out[key] is not None and bool(val) != bool(out[key]):
            conflicts.append(key)
    if conflicts:
        raise ValueError(f"target key join is not unique/consistent for {len(conflicts)} key(s); "
                         f"first: {conflicts[0]}")
    return out


def enrich_targets(rows: Sequence[dict], tmap: dict, sm) -> dict:
    """Attach the key-joined target flag and assert agreement with any in-row value.

    Returns ``dict(n_missing=..., n_mismatch=0)``. A disagreement is a hard error: two divergent
    code paths are exactly what this single-source rule exists to prevent.
    """
    n_missing = 0
    for r in rows:
        key = (int(r["nfl_id"]), int(r["game_id"]), int(r["play_id"]))
        if key not in tmap:
            r["target"] = None
            r["target_join"] = "missing"
            n_missing += 1
            continue
        joined = tmap[key]
        inrow = r.get("target")
        if joined is not None and inrow is not None and bool(joined) != bool(inrow):
            raise ValueError(f"target disagrees for key {key}: join={joined} row={inrow}")
        r["target"] = joined
        r["target_join"] = "matched"
    return dict(n_missing=n_missing, n_mismatch=0)


def enrich_draft(rows: Sequence[dict], draft: dict[int, dict], cfg: dict) -> None:
    bands = _round_bands(cfg)
    for r in rows:
        d = draft.get(int(r["nfl_id"])) or {}
        pick = d.get("draft_overall_pick")
        rnd = d.get("draft_round")
        r["draft_overall_pick"] = pick
        r["draft_round"] = rnd
        r["draft_year"] = d.get("draft_year")
        r["log_draft_pick"] = (math.log(float(pick)) if pick is not None and float(pick) > 0
                               else None)
        r["draft_band"] = band_label(rnd, bands)


# --------------------------------------------------------------------------- #
# A1/A2 — draft outcomes by group
# --------------------------------------------------------------------------- #
def _q(vals: Sequence[float], p: float) -> float | None:
    a = np.asarray([v for v in vals if v is not None and np.isfinite(float(v))], float)
    return float(np.percentile(a, p)) if a.size else None


def _share(k: int, n: int) -> float | None:
    return (k / n) if n else None


def _player_draft(a: dict, draft: dict[int, dict], cfg: dict) -> dict:
    d = draft.get(int(a["nfl_id"])) or {}
    return dict(pick=d.get("draft_overall_pick"), rnd=d.get("draft_round"),
                band=band_label(d.get("draft_round"), _round_bands(cfg)))


def draft_group_rows(assign: dict, draft: dict, cfg: dict, groups: Sequence[str],
                     min_group_n: int) -> list[dict]:
    bands = _round_bands(cfg)
    labels = [f"R{lo}" if lo == hi else f"R{lo}-{hi}" for lo, hi in bands]
    out: list[dict] = []
    for panel, key in (("T3_primary", "T3"), ("T5_sensitivity", "T5")):
        amap = {int(a["nfl_id"]): a for a in assign[key]["assignments"]}
        for scope in ("pooled", "WR"):
            for g in groups:
                players = [a for a in amap.values() if a.get("group") == g
                           and (scope == "pooled" or str(a.get("position_group")) == "WR")]
                n_assigned = len(players)
                unmatched = [int(a["nfl_id"]) for a in players
                             if (draft.get(int(a["nfl_id"])) or {}).get("draft_overall_pick")
                             is None]
                matched = [a for a in players if int(a["nfl_id"]) not in set(unmatched)]
                picks = [float((draft[int(a["nfl_id"])])["draft_overall_pick"])
                         for a in matched]
                rounds = [(draft[int(a["nfl_id"])] or {}).get("draft_round") for a in matched]
                counts = {lab: 0 for lab in labels}
                n_round = 0
                for rnd in rounds:
                    lab = band_label(rnd, bands)
                    if lab is not None:
                        counts[lab] += 1
                        n_round += 1
                out.append(dict(
                    panel=panel, scope=scope, group=g,
                    n_players_assigned=n_assigned, n_matched=len(matched),
                    n_unmatched=len(unmatched),
                    join_rate=_share(len(matched), n_assigned),
                    unmatched_nfl_ids=";".join(str(x) for x in unmatched[:50]) +
                                      (";..." if len(unmatched) > 50 else ""),
                    thin=bool(n_assigned < min_group_n),
                    min_group_n=min_group_n,
                    n_with_round=n_round, n_round_other=len(matched) - n_round,
                    median_overall_pick=_q(picks, 50), pick_q1=_q(picks, 25),
                    pick_q3=_q(picks, 75),
                    **{f"n_{lab}": counts[lab] for lab in labels},
                    **{f"share_{lab}": _share(counts[lab], n_round) for lab in labels},
                    share_R1_2=_share(counts.get("R1", 0) + counts.get("R2", 0), n_round),
                    share_R1_2_only=(_share(counts.get("R1", 0) + counts.get("R2", 0), n_round)),
                ))
    return out


def _mann_whitney(x: Sequence[float], y: Sequence[float]) -> dict:
    from scipy import stats as st
    a = np.asarray([v for v in x if v is not None], float)
    b = np.asarray([v for v in y if v is not None], float)
    if a.size < 3 or b.size < 3:
        return dict(n_moved_down=int(a.size), n_high_secure=int(b.size), u=None, p=None,
                    rank_biserial=None, note="fewer than 3 players in a group")
    res = st.mannwhitneyu(a, b, alternative="two-sided", method="auto")
    u = float(res.statistic)
    delta = 2.0 * u / (a.size * b.size) - 1.0
    return dict(n_moved_down=int(a.size), n_high_secure=int(b.size), u=u,
                p=float(res.pvalue), rank_biserial=float(delta),
                note=("positive delta = moved_down drafted later (worse capital)"
                      if delta > 0 else "negative delta = moved_down drafted earlier"))


def _round_tests(md_rounds: Sequence[int], hs_rounds: Sequence[int],
                 cfg: dict) -> tuple[dict, dict]:
    from scipy import stats as st
    bands = _round_bands(cfg)
    labels = [f"R{lo}" if lo == hi else f"R{lo}-{hi}" for lo, hi in bands]
    def _counts(rs):
        c = {lab: 0 for lab in labels}
        for rnd in rs:
            lab = band_label(rnd, bands)
            if lab is not None:
                c[lab] += 1
        return [c[lab] for lab in labels]
    tab = np.array([_counts(md_rounds), _counts(hs_rounds)], float)
    keep = tab.sum(axis=0) > 0
    chi = dict(n_moved_down=int(tab[0].sum()), n_high_secure=int(tab[1].sum()),
               chi2=None, dof=None, p=None, min_expected=None,
               note="no round variation")
    if keep.sum() >= 2 and tab[:, keep].sum(axis=1).min() > 0:
        chi2, p, dof, exp = st.chi2_contingency(tab[:, keep], correction=False)
        chi.update(chi2=float(chi2), dof=int(dof), p=float(p),
                   min_expected=float(np.min(exp)),
                   note=("expected count < 5: chi-square is approximate"
                         if float(np.min(exp)) < 5 else "expected counts >= 5"))
    # 2x2 Fisher: R1-2 vs R3-7 (the pre-declared band collapse in words, not a search)
    early = [sum(1 for r in md_rounds if band_label(r, bands) in ("R1", "R2")),
             sum(1 for r in hs_rounds if band_label(r, bands) in ("R1", "R2"))]
    late = [sum(1 for r in md_rounds if band_label(r, bands) in ("R3-4", "R5-7")),
            sum(1 for r in hs_rounds if band_label(r, bands) in ("R3-4", "R5-7"))]
    fisher = dict(n_moved_down=int(sum(early[:1]) + late[0]),
                  n_high_secure=int(sum(early[1:]) + late[1]),
                  odds_ratio=None, p=None, note="empty 2x2 table")
    if (early[0] + late[0]) and (early[1] + late[1]):
        odr, p = st.fisher_exact([[early[0], late[0]], [early[1], late[1]]])
        fisher.update(odds_ratio=float(odr), p=float(p),
                      note="Fisher exact, R1-2 vs R3-7")
    return chi, fisher


def draft_test_rows(assign: dict, draft: dict, cfg: dict, groups: Sequence[str]) -> list[dict]:
    out: list[dict] = []
    for panel, key in (("T3_primary", "T3"), ("T5_sensitivity", "T5")):
        amap = {int(a["nfl_id"]): a for a in assign[key]["assignments"]}
        for scope in ("pooled", "WR"):
            def _vals(g):
                ps = [a for a in amap.values() if a.get("group") == g
                      and (scope == "pooled" or str(a.get("position_group")) == "WR")]
                d = [draft.get(int(a["nfl_id"])) or {} for a in ps]
                picks = [x.get("draft_overall_pick") for x in d]
                rounds = [x.get("draft_round") for x in d]
                return ([float(v) for v in picks if v is not None],
                        [v for v in rounds if v is not None])
            md_pick, md_rnd = _vals(groups[0])
            hs_pick, hs_rnd = _vals(groups[1])
            mw = _mann_whitney(md_pick, hs_pick)
            out.append(dict(panel=panel, scope=scope, analysis="overall_pick_rank",
                            test="Mann-Whitney U (two-sided)", **mw))
            chi, fisher = _round_tests(md_rnd, hs_rnd, cfg)
            out.append(dict(panel=panel, scope=scope, analysis="round_bands",
                            test="chi-square (4 bands)", **chi))
            out.append(dict(panel=panel, scope=scope, analysis="round_bands_2x2",
                            test="Fisher exact (R1-2 vs R3-7)", **fisher))
    return out


def draft_spearman_rows(assign: dict, draft: dict) -> list[dict]:
    from scipy import stats as st
    out: list[dict] = []
    amap = {int(a["nfl_id"]): a for a in assign["T3"]["assignments"]}
    for scope in ("pooled", "WR"):
        rows = [a for a in amap.values()
                if scope == "pooled" or str(a.get("position_group")) == "WR"]
        for col in ("edp_pre", "log_edp_pre"):
            pairs = [(a.get(col), (draft.get(int(a["nfl_id"])) or {}).get("draft_overall_pick"))
                     for a in rows]
            pairs = [(float(x), float(y)) for x, y in pairs
                     if x is not None and y is not None and np.isfinite(float(x))
                     and np.isfinite(float(y))]
            rec = dict(scope=scope, exposure=col, n_players=len(pairs),
                       rho=None, p=None,
                       note="fewer than 3 paired players" if len(pairs) < 3 else "")
            if len(pairs) >= 3:
                rho, p = st.spearmanr([x for x, _ in pairs], [y for _, y in pairs])
                rec.update(rho=float(rho), p=float(p))
            out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# fits — block B's exact specification on a subset / with an extra covariate
# --------------------------------------------------------------------------- #
def fit_cell(rows: Sequence[dict], cfg: dict, sm, rel: dict, groups: Sequence[str],
             xkey: str = "log_env_vmax", feature: str = "env_vmax",
             extra_numeric: Sequence[str] = (), require: Sequence[str] = ()) -> dict:
    """One fit of block B's specification on an arbitrary row subset."""
    p = cfg["security_models"]["primary"]
    min_obs, min_group_n = _min_obs(cfg), _min_group_n(cfg)
    num_controls = [c for c in p["controls"] if c not in ("route_family", "position")]
    for c in extra_numeric:
        if c not in num_controls:
            num_controls.append(c)
    cat_controls = [c for c in p["controls"] if c in ("route_family", "position")]
    if p.get("stratum_fe", True):
        cat_controls = cat_controls + ["stratum"]
    req = list(dict.fromkeys(["y", "log_ceiling", "moved_down"] + num_controls + list(require)))
    keys = [xkey if k == "log_ceiling" else k for k in req]
    fin = sm._complete(rows, keys)
    rec = dict(n_obs=len(fin), n_players=len({r["nfl_id"] for r in fin}),
               n_players_moved_down=0, n_players_high_secure=0, thin=False,
               group_coef=None, group_se=None, group_p=None, group_ci_lo=None, group_ci_hi=None,
               speed_coef=None, speed_se=None, speed_p=None, speed_coef_corrected=None,
               group_coef_corrected=None, group_se_corrected=None, group_p_corrected=None,
               r2=None, method="ols_cluster" if p["cluster"] == "player" else "ols_hc1", note="")
    thin = sm._mark_thin(rec, fin, groups, min_group_n)
    if thin:
        rec["note"] = thin
        return rec
    if rec["n_obs"] < min_obs:
        rec["note"] = f"underpowered: n_obs={rec['n_obs']} < min_obs={min_obs}"
        return rec
    try:
        X, names, dropped = sm.design(fin, [xkey, "moved_down"] + num_controls, cat_controls,
                                      focal=[xkey, "moved_down"])
    except ValueError as exc:
        rec["note"] = f"design failed: {exc}"
        return rec
    y = np.array([r["y"] for r in fin], float)
    clusters = np.array([r["nfl_id"] for r in fin]) if p["cluster"] == "player" else None
    res = sm.ols(y, X, clusters=clusters)
    gc, sc = sm._coef(res, names, "moved_down"), sm._coef(res, names, xkey)
    rec.update(n_obs=int(res.nobs), group_coef=gc["coef"], group_se=gc["se"],
               group_p=gc["p"], group_ci_lo=gc["ci_lo"], group_ci_hi=gc["ci_hi"],
               speed_coef=sc["coef"], speed_se=sc["se"], speed_p=sc["p"],
               r2=float(res.rsquared))
    if dropped:
        rec["note"] = "dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dropped)
    icc = rel.get(feature) or {}
    rec["reliability_icc"] = icc.get("icc")
    rec["reliability_source"] = icc.get("source")
    sm._apply_attenuation(rec, fin, xkey, y, X, names, icc, num_controls, cat_controls, p, cfg,
                          feature=feature)
    return rec


def _panel_rows(panels: dict, panel: str) -> list[dict]:
    rows = panels["rows"][panel]
    return rows


PANEL_SPECS = (
    ("T3_primary", "T3", "primary_env_vmax", None),
    ("T3_primary", "T3", "panel_trend_only", "trend"),
    ("T3_primary", "T3", "panel_level_fallback", "level_fallback"),
    ("T5_sensitivity", "T5", "panel_threshold_T5", None),
)


def draft_conditional_fits(panels: dict, cfg: dict, sm, rel: dict,
                           groups: Sequence[str]) -> list[dict]:
    """(a) add log(actual pick) as a covariate; (b) refit within declared draft bands."""
    out: list[dict] = []
    for label, key, spec, stratum in PANEL_SPECS:
        rows0 = _panel_rows(panels, key)
        if stratum is not None:
            rows0 = [r for r in rows0 if r.get("stratum") == stratum]
        for scope in ("pooled", "WR"):
            base = rows0 if scope == "pooled" else [
                r for r in rows0 if str(r.get("position")) == "WR"]
            recs = [("base", base, (), ()),
                    ("plus_log_draft_pick", base, ("log_draft_pick",), ("log_draft_pick",))]
            for band in _strata_bands(cfg):
                lab = f"R{band[0]}" if band[0] == band[1] else f"R{band[0]}-{band[1]}"
                sub = [r for r in base if r.get("draft_band") == lab]
                recs.append((f"base_within_{lab}", sub, (), ()))
            for model, sub, extra, req in recs:
                rec = fit_cell(sub, cfg, sm, rel, groups, extra_numeric=extra, require=req)
                out.append(dict(panel=label, spec=spec, scope=scope, model=model,
                                draft_band=("" if model in ("base", "plus_log_draft_pick")
                                            else model.replace("base_within_", "")),
                                **rec))
    return out


def target_subset_fits(panels: dict, cfg: dict, sm, rel: dict,
                       groups: Sequence[str]) -> list[dict]:
    subsets = list(_dt(cfg).get("target_subsets") or ["all", "target", "non_target"])
    out: list[dict] = []
    for label, key, spec, stratum in PANEL_SPECS:
        rows0 = _panel_rows(panels, key)
        if stratum is not None:
            rows0 = [r for r in rows0 if r.get("stratum") == stratum]
        for scope in ("pooled", "WR"):
            base = rows0 if scope == "pooled" else [
                r for r in rows0 if str(r.get("position")) == "WR"]
            for subset in subsets:
                if subset == "all":
                    sub = base
                elif subset == "target":
                    sub = [r for r in base if r.get("target") is True]
                elif subset == "non_target":
                    sub = [r for r in base if r.get("target") is False]
                else:
                    raise ValueError(f"unknown target subset {subset!r}")
                rec = fit_cell(sub, cfg, sm, rel, groups)
                tgt = [r.get("target") for r in sub if r.get("target") is not None]
                out.append(dict(panel=label, spec=spec, scope=scope, subset=subset,
                                n_target_missing=sum(1 for r in sub
                                                     if r.get("target") is None),
                                target_rate=(sum(1 for t in tgt if t) / len(tgt)
                                             if tgt else None), **rec))
    return out


def target_balance(panels: dict, cfg: dict, groups: Sequence[str]) -> list[dict]:
    subsets = list(_dt(cfg).get("target_subsets") or ["all", "target", "non_target"])
    out: list[dict] = []
    for label, key, spec, stratum in PANEL_SPECS:
        rows0 = _panel_rows(panels, key)
        if stratum is not None:
            rows0 = [r for r in rows0 if r.get("stratum") == stratum]
        for scope in ("pooled", "WR"):
            base = rows0 if scope == "pooled" else [
                r for r in rows0 if str(r.get("position")) == "WR"]
            for subset in subsets:
                if subset == "all":
                    sub = base
                elif subset == "target":
                    sub = [r for r in base if r.get("target") is True]
                else:
                    sub = [r for r in base if r.get("target") is False]
                for g in groups:
                    rs = [r for r in sub if r.get("group") == g]
                    tgt = [r.get("target") for r in rs if r.get("target") is not None]
                    out.append(dict(panel=label, spec=spec, scope=scope, subset=subset,
                                    group=g, n_obs=len(rs),
                                    n_players=len({r["nfl_id"] for r in rs}),
                                    n_target_missing=sum(1 for r in rs
                                                         if r.get("target") is None),
                                    target_rate=(sum(1 for t in tgt if t) / len(tgt)
                                                 if tgt else None)))
    return out


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def _fmt(v: Any, nd: int = 4, signed: bool = False) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    try:
        return f"{float(v):+.{nd}f}" if signed else f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Any) -> str:
    return "—" if v is None or not np.isfinite(float(v)) else f"{float(v):.1%}"


def render_md(ctx: dict) -> str:
    L = [f"# Draft outcomes and targeted routes — {ctx['provenance']}", "",
         f"> **{NOT_A_TEST}.** Neither analysis enters the pre-registered §8 panel, neither can "
         "change `b_primary.csv` or the verdict, and both are reported separately for that reason.",
         ""]
    if ctx.get("sample"):
        L += [f"> **{PENDING} / {ctx['provenance']}** — a full-data run is required before any of "
              "this is a finding.", ""]
    L += ["## Part A — what draft capital did the sort populations actually get?", "",
          "The sort key (`edp_trend`) is **pre-Combine**. Actual draft slot is **POST-Combine**. "
          "This is a mechanism/diagnostic probe, **not a clean adjustment**: conditioning on realised "
          "draft capital can open a collider (Combine performance → draft slot, and Combine "
          "performance → game speed). Read the conditional coefficients as descriptive, not causal.",
          "",
          "A significant group difference in draft slot is **both** evidence that the pre-Combine "
          "sort key tracks a real market outcome **and** a live confound: worse draft capital means "
          "worse QB, team and opportunity, any of which can lower a top-k peak-speed outcome with no "
          "effort mechanism.", ""]

    L += ["### A1. Draft outcomes by group", "",
          "| panel | scope | group | assigned | matched | join rate | median pick [IQR] | "
          "R1 | R2 | R3-4 | R5-7 | R1-2 | thin |",
          "|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---|"]
    for r in ctx["draft_by_group"]:
        L.append(
            f"| {r['panel']} | {r['scope']} | {r['group']} | {r['n_players_assigned']} | "
            f"{r['n_matched']} | {_pct(r['join_rate'])} | "
            f"{_fmt(r['median_overall_pick'], 1)} [{_fmt(r['pick_q1'], 1)}–"
            f"{_fmt(r['pick_q3'], 1)}] | {_pct(r.get('share_R1'))} | {_pct(r.get('share_R2'))} | "
            f"{_pct(r.get('share_R3-4'))} | {_pct(r.get('share_R5-7'))} | "
            f"{_pct(r.get('share_R1_2'))} | {'yes' if r['thin'] else 'no'} |")
    L += ["", "Unmatched ids are written per group in `draft_by_group.csv` "
              "(`unmatched_nfl_ids`, capped at 50 shown). The join rate is on the assigned player, "
              "not on effort rows.", ""]

    L += ["### A2. Statistical tests on realised draft slot", "",
          "Mann-Whitney U on overall pick; Cliff's delta > 0 means `moved_down` was drafted **later** "
          "(worse capital). Chi-square on the four round bands, plus a Fisher exact on the declared "
          "R1-2 vs R3-7 collapse.", "",
          "| panel | scope | analysis | n (down / secure) | statistic | p | note |",
          "|---|---|---|---:|---|---:|---|"]
    for r in ctx["draft_tests"]:
        stat = r.get("u")
        if stat is None:
            stat = r.get("chi2")
        if stat is None:
            stat = r.get("odds_ratio")
        L.append(f"| {r['panel']} | {r['scope']} | {r['analysis']} | "
                 f"{r.get('n_moved_down', '—')} / {r.get('n_high_secure', '—')} | "
                 f"{_fmt(stat, 3)} | {_fmt(r.get('p'), 4)} | {r.get('note') or ''} |")
    L += ["", "Cliff's delta for the rank test is in `draft_tests.csv` "
              "(`rank_biserial`). A chi-square with expected counts below 5 is labelled approximate; "
              "the Fisher row is the small-cell check.", ""]

    L += ["### A3. Validity check — does the pre-Combine sort key track the realised outcome?", "",
          "Spearman correlation of the pre-Combine exposure with actual overall pick "
          "(positive = higher EDP predicts a later pick; that is the expected direction).", "",
          "| scope | exposure | players | Spearman rho | p |",
          "|---|---|---:|---:|---:|"]
    for r in ctx["draft_spearman"]:
        L.append(f"| {r['scope']} | {r['exposure']} | {r['n_players']} | "
                 f"{_fmt(r['rho'], 3)} | {_fmt(r['p'], 4)} |")
    L += ["", "`edp_pre` and `log_edp_pre` are monotone transforms, so their Spearman correlations "
              "should be identical up to ties; reporting both makes that check visible.", ""]

    L += ["### A4. Mechanism probe — does `moved_down` survive controlling for realised capital?", "",
          "`base` is block B's specification. `plus_log_draft_pick` adds `log(draft_overall_pick)` "
          "as a numeric covariate **on the same rows** (missing picks are excluded, so `n_obs` must "
          "fall or stay equal). `base_within_R…` refits the same specification inside a declared "
          "draft band. Every cell carries its own thin/underpowered note; a thin group is never "
          "fitted.", "",
          "| panel | spec | scope | model | obs | players (down / secure) | coef | se | 95% CI | p | thin/note |",
          "|---|---|---|---|---:|---|---:|---:|---|---:|---|"]
    for r in ctx["conditional"]:
        ci = (f"{_fmt(r['group_ci_lo'], 3)}…{_fmt(r['group_ci_hi'], 3)}"
              if r.get("group_ci_lo") is not None else "—")
        L.append(f"| {r['panel']} | {r['spec']} | {r['scope']} | {r['model']} | {r['n_obs']} | "
                 f"{r['n_players_moved_down']} / {r['n_players_high_secure']} | "
                 f"{_fmt(r['group_coef'], 4, signed=True)} | {_fmt(r['group_se'])} | {ci} | "
                 f"{_fmt(r['group_p'])} | {r['note'] or ''} |")
    L += ["", "**What a surviving coefficient would mean.** If the group coefficient barely moves "
              "when `log(pick)` is added, realised draft slot does not explain it in this linear "
              "probe. If it shrinks toward zero, the contrast is partly a draft-capital contrast. "
              "Neither reading is causal: draft slot is post-Combine and may itself be a collider. "
              "The within-band fits are the weaker, more local version of the same question and are "
              "especially exposed to thin cells.", ""]

    L += ["## Part B — does the contrast hold on targeted routes, and what do decoys look like?", "",
          "The target flag is derived by **one** key join from `efforts.csv` on "
          "(`nfl_id`, `game_id`, `play_id`); any in-row value is asserted to agree. "
          "`target == True` is **not** a clean estimate: being targeted is a post-treatment, "
          "role-dependent variable (QB, scheme, depth chart). The split is a decomposition, not an "
          "identification strategy.", "",
          "The mechanical story — peak speed is an opportunity-limited maximum — should be strongest "
          "on the **non-target (decoy)** subset, where the route is run without a ball coming. If the "
          "group contrast is similar in both subsets, that is evidence against a pure decoy-duty "
          "explanation; if it is concentrated in decoys, the mechanical explanation remains live.",
          "",
          "| panel | spec | scope | subset | target rate | obs | players (down / secure) | coef | "
          "se | 95% CI | p | thin/note |",
          "|---|---|---|---|---:|---:|---|---:|---:|---|---:|---|"]
    for r in ctx["target_fits"]:
        ci = (f"{_fmt(r['group_ci_lo'], 3)}…{_fmt(r['group_ci_hi'], 3)}"
              if r.get("group_ci_lo") is not None else "—")
        L.append(f"| {r['panel']} | {r['spec']} | {r['scope']} | {r['subset']} | "
                 f"{_pct(r['target_rate'])} | "
                 f"{r['n_obs']} | {r['n_players_moved_down']} / {r['n_players_high_secure']} | "
                 f"{_fmt(r['group_coef'], 4, signed=True)} | {_fmt(r['group_se'])} | {ci} | "
                 f"{_fmt(r['group_p'])} | {r['note'] or ''} |")
    L += ["", "Target rates by group and subset (descriptive; a missing target is not a zero):", "",
          "| panel | spec | scope | subset | group | obs | players | target rate | missing |",
          "|---|---|---|---|---|---:|---:|---:|---:|"]
    for r in ctx["target_balance"]:
        L.append(f"| {r['panel']} | {r['spec']} | {r['scope']} | {r['subset']} | {r['group']} | "
                 f"{r['n_obs']} | "
                 f"{r['n_players']} | {_pct(r['target_rate'])} | {r['n_target_missing']} |")
    L += ["", "**What each subset can and cannot license.**", "",
          "* `target == True`: can describe the contrast among plays where the ball was thrown; "
          "cannot be read as the effect of the group net of opportunity, because target share is "
          "selected by QB/scheme/role and is downstream of draft capital.",
          "* `target == False`: closest to the decoy/mechanical channel, but it is still selected "
          "(decoy routes are called for specific players) and peak speed there is the most "
          "opportunity-limited of all. A null here is not evidence of no effort effect.",
          "* `all`: the block-B population; the only subset with the original sample size, and the "
          "only one whose coefficient is directly comparable to `b_primary.csv`.",
          "", f"**Power.** The pooled target rate in D47 was 0.556 vs 0.664 (moved_down vs "
               "high_secure); a target-only split keeps roughly 55–66% of rows, so thin flags are "
               "expected in the WR cells. No fit below `min_group_n` is reported as a number.",
          ""]

    L += ["## What this does and does not support", "",
          "* **Draft slot is post-Combine.** A group difference in realised pick is evidence the "
          "sort key is real, but it also induces a confound (worse team/QB/opportunity). It is not a "
          "control variable in the causal sense.",
          "* **The conditional probe is exploratory.** It answers \"does the coefficient survive "
          "this linear adjustment / this band?\", not \"what is the causal effect of the group?\".",
          "* **The target split is selection, not randomisation.** Target-only cannot be written as "
          "a clean estimate. Its value is decomposition: the non-target subset is where the "
          "opportunity-limited-maximum mechanism should live.",
          "* **Nothing here enters §8.** The pre-registered verdict remains the block-B result.",
          ""]

    if ctx.get("missing_note"):
        L += ["## Input caveat", "", ctx["missing_note"], ""]
    if ctx.get("notes"):
        L += ["## Provenance notes", ""] + [f"* {n}" for n in ctx["notes"]] + [""]
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(cfg: dict, mode: str, synthetic: bool, regime: str, out_dir: Path, prov: str,
        log) -> dict:
    sm = load_stage07()
    rr = load_stage08()
    groups = list(cfg["security_models"]["sort"]["groups"])
    panels = rr.build_panels(cfg, synthetic, regime, sm)
    rel = sm.load_reliability(cfg, synthetic, regime, log)

    # D49: 09 inherits 07's season-scope filter through build_effort_rows. Assert the inherited
    # rows never leak a season type outside the active scope (a bypass must fail loudly).
    _allowed = set(panels["season_types"])
    _leaked = sorted({str(r.get("season_type")) for rows in panels["rows"].values()
                      for r in rows if r.get("season_type") not in (None, *_allowed)})
    if _leaked:
        raise AssertionError(
            f"09_draft_and_target bypassed the D49 season scope: rows with season_type "
            f"{_leaked} outside active scope {sorted(_allowed)}")
    _leaked_panel = sorted({str(r.get("panel")) for rows in panels["rows"].values()
                            for r in rows if r.get("panel") not in (None, panels["panel"])})
    if _leaked_panel:
        raise AssertionError(
            f"09_draft_and_target bypassed the D49 panel axis: rows with panel "
            f"{_leaked_panel} outside active panel {panels['panel']!r}")

    con = common.connect(cfg["runtime"]["duckdb_threads"])
    try:
        draft, draft_meta = load_draft(con, draft_path(cfg), sm)
        tmap = load_target_map(con, panels["paths"]["efforts"], sm)
    finally:
        con.close()

    for key in ("T3", "T5"):
        enrich_targets(panels["rows"][key], tmap, sm)
        enrich_draft(panels["rows"][key], draft, cfg)

    assign = panels["assign"]
    draft_by_group = draft_group_rows(assign, draft, cfg, groups, _min_group_n(cfg))
    draft_tests = draft_test_rows(assign, draft, cfg, groups)
    draft_spearman = draft_spearman_rows(assign, draft)
    conditional = draft_conditional_fits(panels, cfg, sm, rel, groups)
    target_fits = target_subset_fits(panels, cfg, sm, rel, groups)
    target_bal = target_balance(panels, cfg, groups)

    out_dir.mkdir(parents=True, exist_ok=True)
    emit(out_dir / "draft_by_group.csv", draft_by_group, prov)
    emit(out_dir / "draft_tests.csv", draft_tests, prov)
    emit(out_dir / "draft_spearman.csv", draft_spearman, prov)
    emit(out_dir / "draft_conditional_fits.csv", conditional, prov)
    emit(out_dir / "target_subset_fits.csv", target_fits, prov)
    emit(out_dir / "target_balance.csv", target_bal, prov)

    # ---- honest, computed headline notes -------------------------------------
    md_base = [r for r in draft_by_group
               if r["panel"] == "T3_primary" and r["scope"] == "pooled"]
    join_note = ""
    if md_base:
        assigned = sum(r["n_players_assigned"] for r in md_base)
        matched = sum(r["n_matched"] for r in md_base)
        join_note = (f"Draft join (T3 primary, pooled): {matched}/{assigned} assigned players "
                     f"matched to `players.csv` ({_pct(_share(matched, assigned))}). "
                     f"{draft_meta['n_duplicate_ids']} duplicate nfl_id(s); "
                     f"{draft_meta['n_missing_overall_pick']} row(s) with no overall pick.")
    tgt_missing = sum(r["n_target_missing"] for r in target_bal
                      if r["panel"] == "T3_primary" and r["spec"] == "primary_env_vmax"
                      and r["scope"] == "pooled" and r["subset"] == "all")
    notes = [join_note] if join_note else []
    notes.append(f"Target key join: {tgt_missing} pooled T3 rows without a matching efforts.csv key "
                 "(reported, never imputed).")
    if synthetic:
        notes.append("SYNTHETIC FIXTURE — every number is generated by the committed fixture and "
                     "says nothing about the NFL.")
    else:
        notes.append("Inputs are the same top-k efforts, exposures, envelope and covariates that "
                     "07_security_models reads; nothing was rebuilt.")
    notes.append("Draft outcomes are POST-Combine. The conditional fits are a mechanism/diagnostic "
                 "probe, not a clean adjustment, and are kept out of §8.")
    notes.append("The target split conditions on a post-treatment, role-dependent variable; "
                 "target-only is not a clean estimate.")

    ctx = dict(provenance=prov, sample=(mode == "sample" and not synthetic),
               draft_by_group=draft_by_group, draft_tests=draft_tests,
               draft_spearman=draft_spearman, conditional=conditional,
               target_fits=target_fits, target_balance=target_bal,
               notes=notes, missing_note=ctx_missing_note(synthetic, cfg, prov))
    (out_dir / "draft_target.md").write_text(render_md(ctx), encoding="utf-8")
    log.info("%s: draft/target analysis written to %s", prov, out_dir)
    return ctx


def ctx_missing_note(synthetic: bool, cfg: dict | None = None, prov: str = "") -> str:
    if synthetic:
        return ("This run uses the committed synthetic fixture, not Conor's full-data artefacts. "
                "It validates the code path only.")
    missing = required_inputs(cfg, False, "treated") if cfg is not None else []
    if not missing:
        return ""
    base = ("This analysis needs the FULL game-side artefacts (`outputs/game/efforts.csv`, "
            "`top_k_efforts.csv`, `player_summary.csv`), the security exposure and the Combine "
            "envelope. On the Pi those full-data inputs are absent (`outputs/` is gitignored), so "
            "the real numbers must come from the laptop run. Any number produced here without them "
            "is not a finding.")
    if missing:
        base += " Missing now: " + "; ".join(f"{label} ({p})" for label, p in missing) + "."
    return base


def sample_placeholder(out_dir: Path, prov: str, cfg: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = required_inputs(cfg, False, "treated")
    lines = [f"# Draft outcomes and targeted routes — {prov}", "",
             f"- **status:** `{PENDING}` — needs the FULL game-side artefacts.",
             "- **nothing was estimated.**", ""]
    if missing:
        lines += ["Missing inputs:", ""] + [f"* {label}: `{p}`" for label, p in missing] + [""]
    (out_dir / "draft_target.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 draft-outcome and targeted-route probes")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-regime", choices=["planted", "null", "selection"],
                    default="planted")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()
    prov = PROV_SYNTH if args.synthetic else common.provenance(cfg, mode)
    out_dir = Path(args.out_dir) if args.out_dir else (
        common.resolve(cfg, "outputs", "security_models") /
        str(_dt(cfg).get("out_subdir", "draft_and_target")))
    if args.synthetic:
        out_dir = out_dir / "synthetic" / args.synthetic_regime

    missing = required_inputs(cfg, args.synthetic, args.synthetic_regime)
    if missing:
        if mode == "sample" and not args.synthetic:
            with common.timed_stage(cfg, "09_draft_and_target", mode, synthetic=False):
                sample_placeholder(out_dir, prov, cfg)
            return
        print(f"{prov}: cannot run 09_draft_and_target — missing input(s):")
        for label, p in missing:
            print(f"  - {label}: {p}")
        raise SystemExit(2)

    with common.timed_stage(cfg, "09_draft_and_target", mode, synthetic=args.synthetic):
        run(cfg, mode, args.synthetic, args.synthetic_regime, out_dir, prov, log)


if __name__ == "__main__":
    main()
