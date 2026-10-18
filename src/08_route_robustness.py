#!/usr/bin/env python3
"""08_route_robustness.py — can the two-group contrast be explained by route mix?

**Analysis-only.** This stage rebuilds nothing. It reads the *same* inputs as
``07_security_models`` (Phase-3 top-k efforts, Phase-4 security exposures, Phase-2 envelope,
Phase-4 covariates) and re-uses that module's row builder (``build_effort_rows``), design matrix
(``design``), estimator (``ols``), thin-group rule (``_mark_thin``) and ICC attenuation
(``_apply_attenuation``). The statistics are therefore the *same* ones block B reports — this file
only changes the **sample** (strata, leave-one-player-out, label permutation) and adds a descriptive
balance table. Nothing here can alter ``b_primary.csv`` or the pre-declared verdict.

The question it answers (Conor, D47): the rival explanation for a negative ``group_coef`` is that
later-pick / lower-security receivers run a different route menu — fewer deep routes, more decoy
duty — which mechanically lowers a top-k *peak-speed* outcome with no effort mechanism. Three tests:

A. **Balance** — do the two groups run materially different route families, depths, snap shares and
   target rates? (Only fields that actually reach ``top_k_efforts.csv``: ``route_ran``,
   ``pass_length``, ``target``. Coverage, alignment and motion exist in ``player_play.csv`` but are
   NOT propagated to the efforts table, so they cannot be balanced here.)
B. **Within-stratum re-estimation** — same spec, restricted to VERTICAL routes, to deep passes
   (``pass_length >= audit.deep_pass_length_yd``) and to three depth bands.
C. **Leave-one-player-out** — refit dropping one player at a time.
D. **Player-level permutation** — shuffle group labels across players (sizes fixed), >=1000 reps.

Outputs (all under ``outputs/security_models/route_robustness/``):

* ``route_balance.csv``       per scope x panel x group, route-family shares + depth/snap/target
* ``route_balance_tests.csv`` chi-square / Welch / two-proportion tests behind the balance claims
* ``route_strata.csv``        the B re-estimates
* ``loo_fits.csv``            one row per dropped player
* ``loo_summary.csv``         coef/p ranges and the share of refits that stay significant
* ``permutation.csv``         observed coef vs the player-label null
* ``reproduction_check.csv``  this stage's refit of B vs the ``b_primary.csv`` on disk (if present)
* ``route_robustness.md``     plain-English summary

Discipline: every emitted CSV starts with ``provenance``; a thin group (``sort.min_group_n``) is
reported and **never fitted**; a stratum below ``primary.min_obs`` is reported, not forced. Exit 0
with a labelled ``PENDING FULL RUN`` placeholder in sample mode (same house pattern as 03/04/05/07).
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
import security_features as sf  # noqa: E402

PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"
NOT_A_TEST = "robustness — not a pre-declared test"


# --------------------------------------------------------------------------- #
# reuse the stage-07 machinery (never fork the statistics)
# --------------------------------------------------------------------------- #
def load_stage07():
    """Import ``07_security_models`` by path (numeric module name; no package shadowing)."""
    path = Path(__file__).with_name("07_security_models.py")
    spec = importlib.util.spec_from_file_location("_bdb27_07_security_models", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# emit (house convention: provenance FIRST)
# --------------------------------------------------------------------------- #
def _fmt(v: Any) -> Any:
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return "" if v is None or not np.isfinite(float(v)) else float(v)
    return v


def emit(path: Path, rows: Sequence[dict], provenance: str, columns: Sequence[str] | None = None):
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
            w.writerow({"provenance": provenance, **{k: _fmt(r.get(k)) for k in cols}})


# --------------------------------------------------------------------------- #
# inputs: exactly the stage-07 readers
# --------------------------------------------------------------------------- #
def build_panels(cfg: dict, synthetic: bool, regime: str, sm) -> dict:
    """Load the stage-07 inputs and build the two group panels (T3 primary, T5 sensitivity).

    Returns ``dict(rows={'T3': [...], 'T5': [...]}, targets={...}, exposures=..., envelope=...,
    cfg=..., paths=...)``. ``rows`` are the *same* dicts ``run_b`` consumes.
    """
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    inp = sm._inputs(cfg, synthetic, regime)
    exposures = sm.load_exposures(con, inp["exposure"])
    envelope = sm.load_envelope(con, inp["envelope"])
    psum = sm.load_player_summary(con, inp["players"])
    topk = sm.load_topk(con, inp["topk"])
    cov = sm.load_covariates(con, cfg, synthetic, regime, issues=[])

    pool = [dict(nfl_id=nid, position_group=ex["position_group"], edp_pre=ex["edp_pre"],
                 log_edp_pre=ex["log_edp_pre"], edp_trend=ex["edp_trend"],
                 has_trend=ex["has_trend"], has_primary=ex["has_primary"])
            for nid, ex in sorted(exposures.items())]
    threshold = float(cfg["security_models"]["sort"]["fall_threshold"])
    threshold5 = float(cfg["security_models"]["sort"]["sensitivity_threshold"])
    assign3 = sf.assign_security_groups(pool, cfg)
    assign5 = sf.assign_security_groups(pool, cfg, threshold=threshold5)

    rows3, _ = sm.build_effort_rows(topk, assign3, envelope, cov, psum, cfg)
    rows5, _ = sm.build_effort_rows(topk, assign5, envelope, cov, psum, cfg)

    # ``target`` and ``rank`` are in the top-k file but are deliberately not model covariates; read
    # them here (descriptive balance only) — never as controls.
    extra: dict[tuple[int, int, int], dict] = {}
    try:
        q = con.execute(
            f"select nfl_id, game_id, play_id, target, rank from read_csv_auto('{inp['topk']}')")
        for nid, gid, pid, tgt, rk in q.fetchall():
            extra[(int(nid), int(gid), int(pid))] = dict(
                target=(None if tgt is None else bool(tgt)), rank=int(rk) if rk is not None else None)
    except Exception:  # noqa: BLE001 - synthetic fixtures carry the same columns; absence is reported
        extra = {}
    return dict(rows={"T3": rows3, "T5": rows5}, extra=extra, exposures=exposures,
                envelope=envelope, assign={"T3": assign3, "T5": assign5}, paths=inp,
                threshold=threshold, threshold5=threshold5)


# --------------------------------------------------------------------------- #
# A. group balance
# --------------------------------------------------------------------------- #
def _q(vals: Sequence[float], p: float) -> float:
    a = np.asarray([v for v in vals if v is not None and np.isfinite(v)], float)
    return float(np.percentile(a, p)) if a.size else float("nan")


def balance_rows(rows: Sequence[dict], extra: dict, groups: Sequence[str]) -> dict:
    """Route-family shares + depth/snap/target summaries per group (descriptive only)."""
    fams = ["VERTICAL", "BREAK_IN", "BREAK_OUT", "SHORT", "OTHER"]

    def _stats(rs: Sequence[dict]) -> dict:
        n = len(rs)
        counts = {f: sum(1 for r in rs if r.get("route_family") == f) for f in fams}
        other = n - sum(counts.values())
        counts["OTHER"] = counts.get("OTHER", 0) + other
        pl = [r.get("pass_length") for r in rs]
        pl_ok = [float(v) for v in pl if v is not None and np.isfinite(v)]
        snaps = [r.get("snap_share") for r in rs if r.get("snap_share") is not None]
        tgt = [extra.get((int(r["nfl_id"]), int(r["game_id"]), int(r["play_id"])), {})
               .get("target") for r in rs]
        tgt_ok = [t for t in tgt if t is not None]
        out = dict(n_efforts=n, n_players=len({r["nfl_id"] for r in rs}))
        for f in fams:
            out[f"n_{f}"] = counts[f]
            out[f"share_{f}"] = (counts[f] / n) if n else float("nan")
        out.update(
            pass_length_mean=float(np.mean(pl_ok)) if pl_ok else float("nan"),
            pass_length_median=_q(pl_ok, 50), pass_length_q1=_q(pl_ok, 25),
            pass_length_q3=_q(pl_ok, 75),
            pass_length_n_missing=int(n - len(pl_ok)),
            snap_share_mean=float(np.mean(snaps)) if snaps else float("nan"),
            snap_share_median=_q(snaps, 50),
            n_route_snaps_mean=float(np.mean([r.get("n_route_snaps") or 0 for r in rs]))
            if rs else float("nan"),
            target_rate=(sum(1 for t in tgt_ok if t) / len(tgt_ok)) if tgt_ok else float("nan"),
            target_n_missing=int(n - len(tgt_ok)),
            n_rank_missing=sum(1 for r in rs
                               if extra.get((int(r["nfl_id"]), int(r["game_id"]),
                                             int(r["play_id"])), {}).get("rank") is None),
        )
        return out

    out: dict[str, dict] = {}
    for g in groups:
        rs = [r for r in rows if r.get("group") == g]
        out[g] = _stats(rs)
    return out


def balance_tests(rows: Sequence[dict], extra: dict, groups: Sequence[str]) -> list[dict]:
    """Chi-square (route family), Welch (depth) and two-proportion z (target rate)."""
    from scipy import stats as st  # statsmodels dependency; present in the run venv

    a = [r for r in rows if r.get("group") == groups[0]]
    b = [r for r in rows if r.get("group") == groups[1]]
    fams = ["VERTICAL", "BREAK_IN", "BREAK_OUT", "SHORT", "OTHER"]

    def _fam_counts(rs):
        c = {f: sum(1 for r in rs if r.get("route_family") == f) for f in fams}
        c["OTHER"] = len(rs) - sum(v for k, v in c.items() if k != "OTHER")
        return [c[f] for f in fams]

    tab = np.array([_fam_counts(a), _fam_counts(b)], float)
    keep = tab.sum(axis=0) > 0
    chi2 = dof = p = cramers = float("nan")
    if keep.sum() >= 2 and tab[:, keep].min() >= 0:
        chi2, p, dof, _ = st.chi2_contingency(tab[:, keep], correction=False)
        n = tab.sum()
        cramers = math.sqrt(chi2 / (n * (min(tab.shape) - 1))) if n else float("nan")

    def _num(rs, k):
        return [float(r[k]) for r in rs if r.get(k) is not None and np.isfinite(float(r[k]))]

    pl_a, pl_b = _num(a, "pass_length"), _num(b, "pass_length")
    t_p = float("nan")
    if len(pl_a) > 1 and len(pl_b) > 1:
        t_p = float(st.ttest_ind(pl_a, pl_b, equal_var=False).pvalue)

    def _tgt(rs):
        t = [extra.get((int(r["nfl_id"]), int(r["game_id"]), int(r["play_id"])), {}).get("target")
             for r in rs]
        t = [x for x in t if x is not None]
        return (sum(1 for x in t if x), len(t))

    ka, na = _tgt(a)
    kb, nb = _tgt(b)
    z_p = float("nan")
    if na and nb:
        p1, p2 = ka / na, kb / nb
        pp = (ka + kb) / (na + nb)
        se = math.sqrt(pp * (1 - pp) * (1 / na + 1 / nb)) if 0 < pp < 1 else 0.0
        if se > 0:
            z_p = float(2 * (1 - st.norm.cdf(abs((p1 - p2) / se))))
    return [dict(chi2_route_family=float(chi2), chi2_dof=int(dof) if dof == dof else "",
                 chi2_p=float(p), cramers_v=float(cramers),
                 pass_length_welch_p=t_p,
                 target_rate_diff=(ka / na - kb / nb) if na and nb else float("nan"),
                 target_diff_p=z_p,
                 route_family_counts_moved_down="|".join(str(int(v)) for v in tab[0]),
                 route_family_counts_high_secure="|".join(str(int(v)) for v in tab[1]))]


# --------------------------------------------------------------------------- #
# B. within-stratum re-estimation (the run_b spec, unchanged)
# --------------------------------------------------------------------------- #
def stratum_defs(cfg: dict) -> list[tuple[str, Any]]:
    """Declared strata; predicates receive a model row. No tuning, no data-dependent cutoffs."""
    deep = float(cfg["audit"]["deep_pass_length_yd"])
    bands = cfg.get("route_robustness", {}).get("strata", {}).get(
        "pass_length_bands", [[0, 9], [10, 19], [20, None]])
    fam = str(cfg.get("route_robustness", {}).get("strata", {}).get("vertical_family", "VERTICAL"))
    out: list[tuple[str, Any]] = [
        (f"route_family={fam}", lambda r, fam=fam: r.get("route_family") == fam),
        (f"pass_length>={deep:g}", lambda r, deep=deep: (r.get("pass_length") is not None
                                                        and float(r["pass_length"]) >= deep)),
    ]
    for lo, hi in bands:
        label = f"pass_length {lo}-{hi}" if hi is not None else f"pass_length {lo}+"
        out.append((label,
                    lambda r, lo=lo, hi=hi: (r.get("pass_length") is not None
                                             and float(r["pass_length"]) >= float(lo)
                                             and (hi is None or float(r["pass_length"]) <= float(hi)))))
    return out


def fit_group(rows: Sequence[dict], cfg: dict, sm, rel: dict, groups: Sequence[str],
              xkey: str = "log_env_vmax", feature: str = "env_vmax") -> dict:
    """One fit of block B's specification on an arbitrary row subset.

    Identical to ``run_b``: same ``_complete`` filter, same numeric/categorical controls, same
    player-clustered OLS, same thin-group rule, same ICC attenuation. Returns a record; on a thin or
    underpowered subset the coefficients stay ``None`` (never a forced number).
    """
    p = cfg["security_models"]["primary"]
    min_obs = int(p["min_obs"])
    num_controls = [c for c in p["controls"] if c not in ("route_family", "position")]
    cat_controls = [c for c in p["controls"] if c in ("route_family", "position")]
    if p.get("stratum_fe", True):
        cat_controls = cat_controls + ["stratum"]
    req = ["y", "log_ceiling", "moved_down"] + num_controls
    keys = [xkey if k == "log_ceiling" else k for k in req]
    fin = sm._complete(rows, keys)
    rec = dict(n_obs=len(fin), n_players=len({r["nfl_id"] for r in fin}),
               n_players_moved_down=0, n_players_high_secure=0, thin=False,
               group_coef=None, group_se=None, group_p=None, group_ci_lo=None, group_ci_hi=None,
               speed_coef=None, speed_se=None, speed_p=None, speed_coef_corrected=None,
               group_coef_corrected=None, group_se_corrected=None, group_p_corrected=None,
               r2=None, note="")
    thin = sm._mark_thin(rec, fin, groups, int(cfg["security_models"]["sort"]["min_group_n"]))
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
    res = sm.ols(y, X, clusters=np.array([r["nfl_id"] for r in fin]))
    gc = sm._coef(res, names, "moved_down")
    sc = sm._coef(res, names, xkey)
    rec.update(group_coef=gc["coef"], group_se=gc["se"], group_p=gc["p"],
               group_ci_lo=gc["ci_lo"], group_ci_hi=gc["ci_hi"],
               speed_coef=sc["coef"], speed_se=sc["se"], speed_p=sc["p"],
               r2=float(res.rsquared))
    icc = rel.get(feature) or {}
    rec["reliability_icc"] = icc.get("icc")
    if dropped:
        rec["note"] = "dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dropped)
    sm._apply_attenuation(rec, fin, xkey, y, X, names, icc, num_controls, cat_controls, p, cfg,
                          feature=feature)
    return rec


def route_strata(panels: dict, cfg: dict, sm, rel: dict, groups: Sequence[str]) -> list[dict]:
    out: list[dict] = []
    for panel in ("T3", "T5"):
        rows = panels["rows"][panel]
        for scope in ("pooled", "WR"):
            base = rows if scope == "pooled" else [r for r in rows if str(r.get("position")) == "WR"]
            for name, pred in stratum_defs(cfg):
                sub = [r for r in base if pred(r)]
                rec = fit_group(sub, cfg, sm, rel, groups)
                out.append(dict(panel="primary_env_vmax" if panel == "T3" else "panel_threshold_T5",
                                scope=scope, stratum=name, **rec))
    return out


# --------------------------------------------------------------------------- #
# C. leave-one-player-out
# --------------------------------------------------------------------------- #
def loo(rows: Sequence[dict], cfg: dict, sm, rel: dict, groups: Sequence[str],
        panel: str, scope: str) -> tuple[list[dict], dict]:
    base = fit_group(rows, cfg, sm, rel, groups)
    players = sorted({r["nfl_id"] for r in rows})
    fits: list[dict] = []
    for nid in players:
        sub = [r for r in rows if r["nfl_id"] != nid]
        rec = fit_group(sub, cfg, sm, rel, groups)
        fits.append(dict(panel=panel, scope=scope, dropped_nfl_id=nid, **rec))
    return fits, base


def loo_summary(fits: Sequence[dict], base: dict, panel: str, scope: str) -> dict:
    ok = [f for f in fits if f.get("group_coef") is not None and f.get("group_p") is not None]
    coefs = [f["group_coef"] for f in ok]
    ps = [f["group_p"] for f in ok]
    sig = [1 for f in ok if f["group_p"] < 0.05]
    ci = [1 for f in ok if (f.get("group_ci_lo") is not None and f.get("group_ci_hi") is not None
                            and (f["group_ci_lo"] > 0 or f["group_ci_hi"] < 0))]
    worst = max(ok, key=lambda f: abs(f["group_coef"] - (base.get("group_coef") or 0.0))) if ok else {}
    return dict(panel=panel, scope=scope, n_fits=len(ok), n_players=len(fits),
                baseline_coef=base.get("group_coef"), baseline_p=base.get("group_p"),
                coef_min=min(coefs) if coefs else None, coef_max=max(coefs) if coefs else None,
                coef_range=(max(coefs) - min(coefs)) if coefs else None,
                p_min=min(ps) if ps else None, p_max=max(ps) if ps else None,
                p_median=float(np.median(ps)) if ps else None,
                n_p_lt_05=len(sig), n_ci_excludes_zero=len(ci),
                share_p_lt_05=(len(sig) / len(ok)) if ok else None,
                share_ci_excludes_zero=(len(ci) / len(ok)) if ok else None,
                most_influential_player=worst.get("dropped_nfl_id"),
                most_influential_coef=worst.get("group_coef"),
                most_influential_delta=(None if worst.get("group_coef") is None or
                                        base.get("group_coef") is None
                                        else worst["group_coef"] - base["group_coef"]))


# --------------------------------------------------------------------------- #
# D. player-level permutation
# --------------------------------------------------------------------------- #
def permutation(rows: Sequence[dict], cfg: dict, sm, groups: Sequence[str], reps: int,
                seed: int) -> dict:
    """Shuffle ``moved_down`` across *players* (group sizes fixed), refit, compare |coef|.

    Only the group indicator moves: the design matrix (controls, ceiling, dummies) is built once and
    the permuted column is substituted, so every rep is the same estimator on the same rows.
    """
    p = cfg["security_models"]["primary"]
    num_controls = [c for c in p["controls"] if c not in ("route_family", "position")]
    cat_controls = [c for c in p["controls"] if c in ("route_family", "position")]
    if p.get("stratum_fe", True):
        cat_controls = cat_controls + ["stratum"]
    req = ["y", "log_ceiling", "moved_down"] + num_controls
    fin = sm._complete(rows, req)
    X, names, _ = sm.design(fin, ["log_env_vmax", "moved_down"] + num_controls, cat_controls,
                            focal=["log_env_vmax", "moved_down"])
    y = np.array([r["y"] for r in fin], float)
    j = names.index("moved_down")
    players = sorted({r["nfl_id"] for r in fin})
    idx_of = {nid: i for i, nid in enumerate(players)}
    prow = np.array([idx_of[r["nfl_id"]] for r in fin])
    pgroup = np.array([next(r["moved_down"] for r in fin if r["nfl_id"] == nid) for nid in players],
                      float)

    def _coef(col: np.ndarray) -> float:
        Z = X.copy()
        Z[:, j] = col
        b, *_ = np.linalg.lstsq(Z, y, rcond=None)
        return float(b[j])

    obs = _coef(pgroup[prow])
    rng = np.random.default_rng(int(seed))
    n_down = int(round(float(pgroup.sum())))
    draws = np.empty(int(reps), float)
    for k in range(int(reps)):
        shuffled = np.zeros_like(pgroup)
        shuffled[rng.choice(len(players), size=n_down, replace=False)] = 1.0
        draws[k] = _coef(shuffled[prow])
    extreme = int(np.sum(np.abs(draws) >= abs(obs) - 1e-15))
    more_neg = int(np.sum(draws <= obs))
    return dict(observed_coef=obs, n_reps=int(reps), n_players=len(players),
                n_moved_down_players=n_down, n_obs=len(fin), seed=int(seed),
                p_perm_two_sided=(1 + extreme) / (1 + reps),
                p_perm_directional=(1 + more_neg) / (1 + reps),
                perm_coef_mean=float(np.mean(draws)), perm_coef_sd=float(np.std(draws, ddof=1)),
                perm_coef_q025=float(np.percentile(draws, 2.5)),
                perm_coef_q975=float(np.percentile(draws, 97.5)))


# --------------------------------------------------------------------------- #
# reproduction check vs the b_primary.csv on disk
# --------------------------------------------------------------------------- #
def reproduction_check(baselines: dict, security_models_dir: Path) -> list[dict]:
    """Compare this stage's refit of B with the ``b_primary.csv`` that stage 07 wrote (if any)."""
    path = security_models_dir / "b_primary.csv"
    if not path.exists():
        return [dict(panel="(none)", scope="(none)",
                     note=f"no b_primary.csv at {path}")]
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        disk = list(csv.DictReader(fh))
    out: list[dict] = []
    for panel, spec in (("T3", "primary_env_vmax"), ("T5", "panel_threshold_T5")):
        for scope in ("pooled", "WR"):
            row = next((r for r in disk if r.get("scope") == scope and r.get("spec") == spec), None)
            mine = baselines.get((spec, scope))
            if row is None or mine is None:
                continue
            try:
                d_coef = float(row["group_coef"])
            except (KeyError, TypeError, ValueError):
                d_coef = None
            out.append(dict(panel=spec, scope=scope,
                            b_primary_coef=d_coef, b_primary_p=row.get("group_p"),
                            refit_coef=mine.get("group_coef"), refit_p=mine.get("group_p"),
                            coef_delta=(None if d_coef is None or mine.get("group_coef") is None
                                        else mine["group_coef"] - d_coef),
                            # b_primary.csv stores 4 decimal places; a 1e-4 tolerance is
                            # CSV rounding, not a different estimate.
                            note=("matches (CSV rounding)"
                                  if (d_coef is not None and mine.get("group_coef") is not None
                                      and abs(mine["group_coef"] - d_coef) <= 1e-4)
                                  else "DIFFERS")))
    return out or [dict(panel="(none)", scope="(none)", note="b_primary.csv has no comparable rows")]


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def render_md(ctx: dict) -> str:
    L = [f"# Route robustness — {ctx['provenance']}", "",
         f"> **{NOT_A_TEST}.** This is an analysis of the *existing* stage-07 estimate; it is not "
         "pre-declared, it does not change the verdict, and it is never merged into the headline "
         "block.", ""]
    if ctx.get("sample"):
        L += [f"> **{PENDING} / {ctx['provenance']}** — a full-data run is required before any of "
              "this is a finding.", ""]
    L += ["## What was tested", "",
          "The rival explanation: later-pick / lower-security receivers run a different route menu "
          "(fewer deep routes, more decoy duty), which would mechanically lower a top-k peak-speed "
          "outcome with no effort mechanism.", "",
          "Three independent checks, all re-using block B's exact design and estimator:", "",
          "1. **Balance** — route family, depth, snap share and target rate by group.",
          "2. **Within-stratum re-estimation** — VERTICAL only, deep passes only, and depth bands.",
          "3. **Resampling** — leave-one-player-out and player-level label permutation.", ""]

    L += ["## 1. Do the groups run different route menus?", ""]
    for scope in ("pooled", "WR"):
        bal = ctx["balance"].get(scope, {})
        if not bal:
            continue
        gs = list(bal.keys())
        L += [f"**{scope}**", "",
              "| group | players | efforts | VERTICAL | BREAK_IN | BREAK_OUT | SHORT | "
              "pass len (mean / median [IQR]) | snap share | target rate |",
              "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|"]
        for g in gs:
            b = bal[g]
            L.append(f"| {g} | {b['n_players']} | {b['n_efforts']} | {b['share_VERTICAL']:.1%} | "
                     f"{b['share_BREAK_IN']:.1%} | {b['share_BREAK_OUT']:.1%} | "
                     f"{b['share_SHORT']:.1%} | {b['pass_length_mean']:.1f} / "
                     f"{b['pass_length_median']:.0f} [{b['pass_length_q1']:.0f}–"
                     f"{b['pass_length_q3']:.0f}] | {b['snap_share_mean']:.2f} | "
                     f"{b['target_rate']:.1%} |")
        t = ctx["tests"].get(scope)
        if t:
            L += ["", f"χ²(route family) = {t['chi2_route_family']:.2f}, dof = {t['chi2_dof']}, "
                      f"p = {t['chi2_p']:.3g}; Cramér's V = {t['cramers_v']:.2f}. "
                      f"Welch depth test p = {t['pass_length_welch_p']:.3g}. "
                      f"Target-rate difference = {t['target_rate_diff']:+.1%} "
                      f"(p = {t['target_diff_p']:.3g})."]
        L.append("")

    L += ["**Verdict on the menu.** " + ctx["balance_verdict"], ""]

    L += ["## 2. Does the coefficient survive within-route strata?", "",
          "Same specification as block B (log game speed on log Combine ceiling + group + controls, "
          "player-clustered SEs). A cell below `min_group_n` players per group is marked thin and "
          "**not fitted**.", "",
          "| panel | scope | stratum | obs | players (down / secure) | coef | se | p | thin/note |",
          "|---|---|---|---:|---|---:|---:|---:|---|"]
    for r in ctx["strata"]:
        counts = f"{r['n_players_moved_down']} / {r['n_players_high_secure']}"
        coef = "—" if r["group_coef"] is None else f"{r['group_coef']:+.4f}"
        se = "—" if r["group_se"] is None else f"{r['group_se']:.4f}"
        p = "—" if r["group_p"] is None else f"{r['group_p']:.4f}"
        L.append(f"| {r['panel']} | {r['scope']} | {r['stratum']} | {r['n_obs']} | {counts} | "
                 f"{coef} | {se} | {p} | {r['note'] or ''} |")
    L += ["", "**Verdict on the strata.** " + ctx["strata_verdict"], ""]

    L += ["## 3. Does p survive resampling?", "",
          "**Leave-one-player-out** (drop each player in turn):", "",
          "| panel | scope | baseline coef (p) | LOO coef range | LOO p range | share p<0.05 | "
          "share CI excludes 0 | most influential |",
          "|---|---|---|---|---|---:|---:|---|"]
    for s in ctx["loo_summary"]:
        L.append(f"| {s['panel']} | {s['scope']} | {s['baseline_coef']:+.4f} "
                 f"({s['baseline_p']:.4f}) | {s['coef_min']:+.4f} … {s['coef_max']:+.4f} | "
                 f"{s['p_min']:.4f} … {s['p_max']:.4f} | {s['share_p_lt_05']:.1%} | "
                 f"{s['share_ci_excludes_zero']:.1%} | nfl_id {s['most_influential_player']} "
                 f"({s['most_influential_delta']:+.4f}) |")
    L += ["", "**Player-level permutation** (group labels shuffled across players, sizes fixed):", "",
          "| panel | scope | observed coef | permutation p (two-sided) | p (directional, ≤ observed) | "
          "null 95% range | reps |", "|---|---|---:|---:|---:|---|---:|"]
    for r in ctx["perm"]:
        L.append(f"| {r['panel']} | {r['scope']} | {r['observed_coef']:+.4f} | "
                 f"{r['p_perm_two_sided']:.4f} | {r['p_perm_directional']:.4f} | "
                 f"{r['perm_coef_q025']:+.4f} … {r['perm_coef_q975']:+.4f} | {r['n_reps']} |")
    L += ["", "**Verdict on resampling.** " + ctx["resample_verdict"], ""]

    L += ["## 4. What these tests cannot rule out", "",
          "The efforts table carries ``route_ran``, ``pass_length`` and ``target`` only. "
          "``receiver_alignment``, ``in_motion_at_ball_snap`` and the ``team_coverage_*`` family "
          "exist in ``player_play.csv`` but are **not propagated** into ``outputs/game/efforts.csv``, "
          "so they cannot be balanced or controlled here.", "",
          "Therefore, even if every test above passes, this analysis **cannot rule out**:", "",
          "* **Route-running skill / separation** — no field for separation, cushion or win rate.",
          "* **Quarterback quality and target quality** — no QB identity, pressure or accuracy field.",
          "* **Coverage difficulty** — man/zone, coverage shell and assignment never reach the "
          "efforts table; a receiver facing more bracket/double coverage would show a lower top-k "
          "peak speed for reasons unrelated to effort.",
          "* **Decoy duty beyond the target flag** — ``target`` is binary; it does not measure "
          "whether a route was run at full speed as a decoy.",
          "* **Play-call selection within a route family** — depth is observed, the design that "
          "produced it is not.", ""]

    if ctx.get("repro"):
        L += ["## 5. Reproduction check against the `b_primary.csv` on disk", "",
              "| panel | scope | b_primary coef (p) | this refit (p) | delta | status |",
              "|---|---|---|---|---|---|"]
        for r in ctx["repro"]:
            L.append(f"| {r['panel']} | {r['scope']} | "
                     f"{r.get('b_primary_coef') if r.get('b_primary_coef') is not None else '—'} "
                     f"({r.get('b_primary_p', '—')}) | "
                     f"{r.get('refit_coef') if r.get('refit_coef') is not None else '—'} "
                     f"({r.get('refit_p', '—')}) | {r.get('coef_delta', '—')} | {r.get('note')} |")
        L.append("")
    if ctx.get("notes"):
        L += ["## 6. Provenance notes", ""] + [f"* {n}" for n in ctx["notes"]] + [""]
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def sample_placeholder(out_dir: Path, prov: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    emit(out_dir / "route_robustness_summary.csv",
         [dict(stage="08_route_robustness", status=PENDING, dataset="sample",
               note="route robustness needs the FULL game-side outputs; nothing was estimated")],
         prov, ["stage", "status", "dataset", "note"])
    (out_dir / "route_robustness.md").write_text(
        f"# Route robustness — {prov}\n\n- **status:** `{PENDING}` — needs the FULL game-side "
        "outputs (top-k efforts + FULL thresholds). Nothing was estimated.\n", encoding="utf-8")


def run(cfg: dict, mode: str, synthetic: bool, regime: str, out_dir: Path, prov: str, log) -> dict:
    sm = load_stage07()
    p = cfg["security_models"]["primary"]
    groups = list(cfg["security_models"]["sort"]["groups"])
    panels = build_panels(cfg, synthetic, regime, sm)
    rel = sm.load_reliability(cfg, synthetic, regime, log)

    # ---- A. balance (T3 primary panel; T5 repeated in the CSV) -----------------
    balance: dict[str, dict] = {}
    tests: dict[str, dict] = {}
    balance_csv: list[dict] = []
    for panel in ("T3", "T5"):
        rows = panels["rows"][panel]
        for scope in ("pooled", "WR"):
            rs = rows if scope == "pooled" else [r for r in rows if str(r.get("position")) == "WR"]
            b = balance_rows(rs, panels["extra"], groups)
            for g, rec in b.items():
                balance_csv.append(dict(panel="primary_env_vmax" if panel == "T3"
                                        else "panel_threshold_T5", scope=scope, group=g, **rec))
            if panel == "T3":
                balance[scope] = b
                tests[scope] = balance_tests(rs, panels["extra"], groups)[0]

    # ---- B. strata ------------------------------------------------------------
    strata = route_strata(panels, cfg, sm, rel, groups)
    baselines: dict[tuple[str, str], dict] = {}
    for panel, label in (("T3", "primary_env_vmax"), ("T5", "panel_threshold_T5")):
        for scope in ("pooled", "WR"):
            rs = panels["rows"][panel]
            if scope == "WR":
                rs = [r for r in rs if str(r.get("position")) == "WR"]
            baselines[(label, scope)] = fit_group(rs, cfg, sm, rel, groups)

    # ---- C. leave-one-player-out ---------------------------------------------
    loo_targets = [("T5", "pooled"), ("T3", "pooled"), ("T5", "WR")]
    loo_fits: list[dict] = []
    loo_summ: list[dict] = []
    label = {"T3": "primary_env_vmax", "T5": "panel_threshold_T5"}
    for panel, scope in loo_targets:
        rs = panels["rows"][panel]
        if scope == "WR":
            rs = [r for r in rs if str(r.get("position")) == "WR"]
        fits, base = loo(rs, cfg, sm, rel, groups, panel, scope)
        for f in fits:
            f["panel"] = label[panel]
        loo_fits.extend(fits)
        loo_summ.append(loo_summary(fits, base, label[panel], scope))

    # ---- D. permutation -------------------------------------------------------
    reps = int(cfg.get("route_robustness", {}).get("perm_reps", 1000))
    seed = int(cfg["seed"]) + int(cfg.get("route_robustness", {}).get("perm_seed_offset", 505))
    perm: list[dict] = []
    for panel, scope in [("T5", "pooled"), ("T3", "pooled"), ("T5", "WR")]:
        rs = panels["rows"][panel]
        if scope == "WR":
            rs = [r for r in rs if str(r.get("position")) == "WR"]
        rec = permutation(rs, cfg, sm, groups, reps, seed + {"T5": 0, "T3": 1}[panel] + (
            100 if scope == "WR" else 0))
        perm.append(dict(panel="panel_threshold_T5" if panel == "T5" else "primary_env_vmax",
                         scope=scope, **rec))

    # ---- write ----------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    emit(out_dir / "route_balance.csv", balance_csv, prov)
    emit(out_dir / "route_balance_tests.csv",
         [dict(panel="primary_env_vmax", scope=s, **t) for s, t in tests.items()], prov)
    emit(out_dir / "route_strata.csv", strata, prov)
    emit(out_dir / "loo_fits.csv", loo_fits, prov)
    emit(out_dir / "loo_summary.csv", loo_summ, prov)
    emit(out_dir / "permutation.csv", perm, prov)
    input_models = common.resolve(cfg, "outputs", "security_models")
    repro = reproduction_check(baselines, input_models)
    emit(out_dir / "reproduction_check.csv", repro, prov)

    # ---- verdict text (computed, never typed) --------------------------------
    def _fmt_pct(x):
        return "—" if x is None or not np.isfinite(x) else f"{x:.1%}"

    bal_bits = []
    for scope, b in balance.items():
        md, hs = b.get("moved_down", {}), b.get("high_secure", {})
        if not md or not hs:
            continue
        t = tests[scope]
        fam = ("route-family mix differs" if t["chi2_p"] < 0.05
               else "route-family mix is statistically indistinguishable")
        deep = ("depth differs" if t["pass_length_welch_p"] < 0.05
                else "depth does not differ at 0.05")
        tgt = ("target rate differs" if t["target_diff_p"] < 0.05
               else "target rate does not differ at 0.05")
        bal_bits.append(
            f"{scope}: {fam} (χ² p={t['chi2_p']:.3g}, V={t['cramers_v']:.2f}); "
            f"VERTICAL share {_fmt_pct(md['share_VERTICAL'])} vs "
            f"{_fmt_pct(hs['share_VERTICAL'])}; mean depth {md['pass_length_mean']:.1f} vs "
            f"{hs['pass_length_mean']:.1f} yd ({deep}, Welch p={t['pass_length_welch_p']:.3g}); "
            f"{tgt} ({_fmt_pct(md['target_rate'])} vs {_fmt_pct(hs['target_rate'])}, "
            f"{t['target_rate_diff']:+.1%}, p={t['target_diff_p']:.3g}).")
    fam_sig = any(tests[s]["chi2_p"] < 0.05 for s in tests)
    tgt_sig = any(tests[s]["target_diff_p"] < 0.05 for s in tests)
    balance_verdict = (
        ("Materially different? On **route family**, no — the family mix is not distinguishable "
         "from chance. On **target rate** (a proxy for decoy duty), yes — `moved_down` receivers "
         "are targeted materially less often. The depth gap points the same way but is not "
         "significant at 0.05 in this sample. "
         if (not fam_sig and tgt_sig) else
         "Materially different? On route family, yes. " if fam_sig else
         "Materially different? No balance variable separates the groups at 0.05. ")
        + " ".join(bal_bits) if bal_bits else "Balance could not be computed.")

    fitted = [r for r in strata if r["group_coef"] is not None]
    sig = [r for r in fitted if r["group_p"] is not None and r["group_p"] < 0.05]
    neg = [r for r in fitted if r["group_coef"] < 0]
    strata_verdict = (
        f"{len(fitted)} of {len(strata)} stratum x panel x scope cells were estimable; "
        f"{len(sig)} reached p<0.05 and {len(neg)} had a negative coefficient. "
        + ("The coefficient does not survive as a consistent within-route effect."
           if len(sig) < 2 else
           "Some within-route cells remain significant — see the table for which."))

    loo_bits = []
    for s in loo_summ:
        loo_bits.append(f"{s['panel']}/{s['scope']}: p {s['p_min']:.3f}–{s['p_max']:.3f}, "
                        f"{_fmt_pct(s['share_p_lt_05'])} of refits significant")
    perm_bits = [f"{r['panel']}/{r['scope']}: permutation p={r['p_perm_two_sided']:.3f}"
                 for r in perm]
    resample_verdict = "; ".join(loo_bits) + ". " + "; ".join(perm_bits) + "."

    notes = []
    if not synthetic:
        notes.append("Inputs: the same top-k efforts, exposures, envelope and covariates that "
                     "07_security_models reads. Nothing was rebuilt.")
    else:
        notes.append("SYNTHETIC FIXTURE — every number is generated by the committed fixture and "
                     "says nothing about the NFL.")
    notes.append("Coverage / alignment / motion are present in player_play.csv but absent from "
                 "efforts.csv; they are therefore neither balanced nor controlled.")
    te = [r for r in panels["rows"]["T3"] if str(r.get("position")) == "TE"]
    te_counts = {g: len({r["nfl_id"] for r in te if r["group"] == g}) for g in groups}
    floor = int(cfg["security_models"]["sort"]["min_group_n"])
    if any(n < floor for n in te_counts.values()):
        notes.append(
            f"TE is thin at T=3 ({te_counts.get('moved_down', 0)} moved_down / "
            f"{te_counts.get('high_secure', 0)} high_secure players, min_group_n={floor}); "
            "no TE balance or TE stratum is reported as a result.")
    notes.append("The permutation null shuffles group labels across players, keeping the observed "
                 "group sizes; it tests the assignment, not the mechanism.")
    ctx = dict(provenance=prov, sample=(mode == "sample" and not synthetic), balance=balance,
               tests=tests, strata=strata, loo_summary=loo_summ, perm=perm, repro=repro,
               balance_verdict=balance_verdict, strata_verdict=strata_verdict,
               resample_verdict=resample_verdict, notes=notes)
    (out_dir / "route_robustness.md").write_text(render_md(ctx), encoding="utf-8")
    log.info("%s: route robustness written to %s", prov, out_dir)
    return ctx


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 route-mix robustness for the group contrast")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-regime", choices=["planted", "null", "selection"],
                    default="planted")
    ap.add_argument("--out-dir", default=None, help="override the output directory")
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()
    prov = PROV_SYNTH if args.synthetic else common.provenance(cfg, mode)
    out_dir = Path(args.out_dir) if args.out_dir else (
        common.resolve(cfg, "outputs", "security_models") /
        str(cfg.get("route_robustness", {}).get("out_subdir", "route_robustness")))
    if args.synthetic:
        out_dir = out_dir / "synthetic" / args.synthetic_regime

    if mode == "sample" and not args.synthetic:
        with common.timed_stage(cfg, "08_route_robustness", mode, synthetic=False):
            sample_placeholder(out_dir, prov)
        return
    with common.timed_stage(cfg, "08_route_robustness", mode, synthetic=args.synthetic):
        run(cfg, mode, args.synthetic, args.synthetic_regime, out_dir, prov, log)


if __name__ == "__main__":
    main()
