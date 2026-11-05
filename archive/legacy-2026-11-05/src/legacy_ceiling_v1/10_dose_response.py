#!/usr/bin/env python3
"""10_dose_response.py — the **continuous** exposure panel for the security question (D50).

**Exploratory / post hoc. It cannot change the pre-registered result.** ``b_primary.csv`` (the
binary ``moved_down`` vs ``high_secure`` contrast at the declared ``edp_trend >= +3.0`` cut) and the
pre-registered test are untouched by this file. The declared ``panel_threshold_T5`` sensitivity
showed that the binary cut relabels only a few players while moving *p* a lot, i.e. the threshold
carries no real signal. This stage therefore models the exposure **continuously** and reports a
linear, a restricted-cubic-spline, an explicit-knot-at-5, a binned and a segmented (kink)
specification on **exactly block B's outcome**.

**Reuse, never reimplement.** ``07_security_models`` is imported by path. This module calls, unchanged:

* ``load_exposures`` / ``load_envelope`` / ``load_player_summary`` / ``load_topk`` /
  ``load_snap_counts`` / ``scoped_snap_counts`` / ``load_covariates`` / ``load_reliability`` /
  ``_inputs`` / ``fixture_dir``;
* ``build_effort_rows(..., keep_unclassified=True)`` — the realisation/ceiling construction;
* ``design`` / ``ols`` / ``_num`` / ``_complete`` / ``fwl_attenuation``.

So the outcome is **identically** block B's: row-level ``y = log(peak_speed)`` on the top-k efforts,
``log_env_vmax`` as the ceiling regressor, ``statsmodels`` OLS with ``cov_type="cluster"`` and
``groups = nfl_id``. The controls are ``security_models.primary.controls`` **minus ``stratum``**:
``stratum`` is constant inside the trend stratum by construction (every row in this stage has a
finite ``edp_trend``, which *is* the trend stratum), so a stratum fixed effect would be an intercept
with no identifying variation. It is deliberately **not** included.

**Exposure.** ``d = edp_trend`` = 1-week-pre EDP − 2-weeks-pre EDP (negative = the player rose up
the board). **Positive = the EDP number rose = the stock fell** (worse / less secure). Rows without a
finite ``edp_trend`` (the level-fallback stratum) are excluded — a dose model needs a dose.

**Sign convention.** ``coef`` = change in log(game peak speed) per **+1 EDP spot fallen**, holding
the Combine ceiling fixed. **NEGATIVE = the slipping player realises LESS of the ceiling** — the PI's
stated expectation, and the like-for-like reading of the pre-registered H1 (which predicts the
*opposite*: H1's binary wording says ``moved_down`` realises a *higher* share, i.e. a positive
coefficient). The discrepancy is real, is documented in D50, and is carried verbatim in every row's
``sign_convention`` column. No pre-registered value is changed by this stage.

**Capital is mandatory.** Every specification is fitted under each ``dose.capital.treatments``
entry (``unadjusted``, ``pick_linear``, ``round_band``, ``pick_plus_level``, ``within_R1``,
``within_R2_R3``, ``within_R4plus``). ``pick_linear`` excludes undrafted rows (no pick) and says so;
``round_band`` keeps ``undrafted`` as its own level; the within-band treatments fit only that band.
If the slope dies once draft capital is controlled, that is the finding: the dose slope is draft
position in disguise.

Outputs (all under ``outputs/security_models/dose/``, never anywhere else):

* ``dose_fits.csv``       — one row per season_scope × scope × spec × feature × treatment × term
* ``dose_bins.csv``       — the binned means/contrasts (fixed + the quantile variant when triggered)
* ``dose_curve.csv``      — the plotting grid for linear/spline/knot5/kink
* ``SUMMARY.md``          — plain-words reading, sign convention first
* ``SUMMARY.json``        — the same, machine-readable (+ the figure metadata)
* ``fig_dose_response.png`` — the supplementary exploratory figure (300 dpi)

Every CSV's FIRST column is ``provenance``. Thin cells (``n_obs < dose.min_obs`` **or**
``n_players < dose.min_players``) are emitted with null coefficients and a reason — never a forced
number. Checkpoint + ``--force`` (fingerprinted), ``common.timed_stage``, ``gate.enforce``, and a
labelled ``PENDING FULL RUN`` placeholder in ``--mode sample`` (the house pattern of 03/04/05/07).

Usage::

    python src/10_dose_response.py --synthetic --synthetic-regime dose --force
    python src/10_dose_response.py --mode sample
    python src/10_dose_response.py --mode full            # needs the full game files
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import gate  # noqa: E402

STAGE = "10_dose_response"
PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PENDING = "PENDING FULL RUN"
EXPLORATORY = "EXPLORATORY — POST HOC (cannot change the pre-registered binary result)"

FIT_COLS = ["season_scope", "scope", "spec", "feature", "capital_treatment", "term", "coef", "se",
            "p", "ci_lo", "ci_hi", "test_stat", "df", "n_obs", "n_players", "thin", "note",
            "reliability_icc", "coef_corrected", "se_corrected", "p_corrected", "sign_convention"]
BIN_COLS = ["season_scope", "scope", "binning", "capital_treatment", "bin", "n_players", "n_obs",
            "mean_edp_trend", "mean_gap", "se_gap", "mean_realisation_pct", "realisation_ci_lo",
            "realisation_ci_hi", "ref_contrast", "ref_se", "ref_ci_lo", "ref_ci_hi", "thin", "note"]
CURVE_COLS = ["season_scope", "scope", "spec", "feature", "capital_treatment", "x", "fitted_log",
              "realisation_pct", "realisation_ci_lo", "realisation_ci_hi", "n_obs", "n_players"]


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
# emit (house convention: provenance FIRST, full float precision)
# --------------------------------------------------------------------------- #
def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return "" if not math.isfinite(f) else f
    return v


def emit(path: Path, rows: Sequence[dict], provenance: str, columns: Sequence[str]) -> None:
    """Write one table immediately, ``provenance`` first, stable column order, raw precision."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["provenance", *columns], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"provenance": provenance, **{k: _cell(r.get(k)) for k in columns}})


# --------------------------------------------------------------------------- #
# restricted cubic splines (Harrell parameterisation)
# --------------------------------------------------------------------------- #
def rcs_basis(x: np.ndarray, knots: Sequence[float]) -> np.ndarray:
    """Harrell restricted-cubic-spline basis: column 0 is ``x``, then ``k-2`` spline columns.

    With knots ``t1 < ... < tk`` the j-th spline column (j = 1..k-2) is

        [(x-tj)_+^3 - (x-t_{k-1})_+^3 (tk-tj)/(tk-t_{k-1})
                    + (x-tk)_+^3 (t_{k-1}-tj)/(tk-t_{k-1})] / (tk-t1)^2

    so the fit is **linear beyond the boundary knots** (the cubic terms cancel for x >= tk and are
    all zero for x <= t1) and the basis has ``k-1`` degrees of freedom. The ``(tk-t1)^2`` scaling is
    Royston/Harrell's; it changes no fitted value, Wald statistic or interval, only the coefficient
    magnitudes and the conditioning of the design.
    """
    x = np.asarray(x, float)
    t = np.asarray(sorted({float(k) for k in knots}), float)
    if t.size < 3:
        raise ValueError(f"restricted cubic spline needs >= 3 distinct knots, got {t.tolist()}")
    span = float((t[-1] - t[0]) ** 2)
    if span <= 0.0:
        raise ValueError("degenerate knot range")
    cols = [x.copy()]
    denom = float(t[-1] - t[-2])
    for j in range(t.size - 2):
        term = (np.maximum(x - t[j], 0.0) ** 3
                - np.maximum(x - t[-2], 0.0) ** 3 * float(t[-1] - t[j]) / denom
                + np.maximum(x - t[-1], 0.0) ** 3 * float(t[-2] - t[j]) / denom)
        cols.append(term / span)
    return np.column_stack(cols)


def spline_knots(d: np.ndarray, cfg: dict) -> tuple[list[float], str]:
    """Knots for the 3-df spline: quantiles of ``d``, de-duplicated (never silently re-used)."""
    qs = [float(q) for q in cfg["dose"]["knots_3df"]]
    raw = [float(np.quantile(d, q)) for q in qs]
    t = sorted({round(v, 10) for v in raw})
    note = ""
    if len(t) < len(raw):
        note = (f"knot de-duplication collapsed {len(raw)} quantile knots to {len(t)} "
                f"(d has few distinct values)")
    return t, note


def knot5_knots(d: np.ndarray, cfg: dict) -> tuple[list[float], str]:
    """Knots with an **explicit** knot at ``dose.knot5`` (default 5.0 EDP spots).

    ``knots = unique(sort([q_lo, knot5, q_hi]))``. When the declared knot lies outside the observed
    range the fallback ``[q10, q50, q90]`` is used and the note says so — the shape question ("is the
    curve steeper past 5?") cannot be answered by a knot that is not in the data.
    """
    k5 = float(cfg["dose"]["knot5"])
    qs = [float(q) for q in cfg["dose"]["knot5_percentiles"]]
    fallback = [float(q) for q in cfg["dose"]["knot5_fallback_percentiles"]]
    lo, hi = float(np.min(d)), float(np.max(d))
    if not (lo < k5 < hi):
        raw = [float(np.quantile(d, q)) for q in fallback]
        t = sorted({round(v, 10) for v in raw})
        return t, (f"knot5={k5:g} lies outside the observed range [{lo:.4f}, {hi:.4f}]; fell back to "
                   f"percentiles {fallback} (shape question unanswerable)")
    raw = [float(np.quantile(d, q)) for q in qs] + [k5]
    t = sorted({round(v, 10) for v in raw})
    note = ""
    if len(t) < len(raw):
        note = f"knot de-duplication collapsed {len(raw)} knots to {len(t)}"
    return t, note


def _rank_reduce(B: np.ndarray, names: list[str]) -> tuple[np.ndarray, list[str], list[int], str]:
    """Drop basis columns from the END until ``[1, B]`` is full rank (§3.2 fallback).

    Returns ``(B_reduced, names, keep_index, note)``; ``keep_index`` lets the curve re-apply the same
    reduction to a fresh basis on the plotting grid.
    """
    keep = list(range(B.shape[1]))
    notes: list[str] = []
    while len(keep) > 1:
        A = np.column_stack([np.ones(B.shape[0]), B[:, keep]])
        if np.linalg.matrix_rank(A) >= A.shape[1]:
            break
        notes.append(f"rank-deficient basis: dropped the last column ({names[keep[-1]]})")
        keep.pop()
    return B[:, keep], [names[j] for j in keep], keep, "; ".join(notes)

# --------------------------------------------------------------------------- #
# bins
# --------------------------------------------------------------------------- #
def bin_index(d: np.ndarray, bounds: Sequence[Sequence[Any]]) -> np.ndarray:
    """Assign each ``d`` to a configured bin, closed on the **upper** edge.

    ``bounds`` is ``[[null, 0], [1, 2], [3, 4], [5, 9], [10, null]]``. For the integer EDP trends
    this is exactly ``<=0 / 1-2 / 3-4 / 5-9 / 10+``; it is also a **total** partition (every real
    falls in exactly one bin), so a fractional trend (``edp`` is an expected position, so the
    difference can be fractional) goes to the bin whose upper bound it does not exceed instead of
    falling through a gap. Returns bin indices into ``bounds``.
    """
    d = np.asarray(d, float)
    out = np.full(d.shape, -1, dtype=int)
    for j, (_lo, hi) in enumerate(bounds):
        if hi is None:
            continue
        out[(out < 0) & (d <= float(hi))] = j
    out[out < 0] = len(bounds) - 1          # the open-ended top bin
    return out


def quantile_edges(d: np.ndarray, n_bins: int) -> list[float]:
    qs = [j / float(n_bins) for j in range(1, int(n_bins))]
    edges = [float(np.quantile(d, q)) for q in qs]
    return sorted({round(e, 12) for e in edges})


# --------------------------------------------------------------------------- #
# inputs: exactly the stage-07 readers (+ the capital columns)
# --------------------------------------------------------------------------- #
def load_pick(con, cfg: dict, synthetic: bool, regime: str, sm, log) -> tuple[dict[int, Any], str]:
    """``overall_pick`` by ``nfl_id``. Never invents a pick for an undrafted player (absent = None).

    Real: the ``players`` table (``draft_overall_pick``; the Parquet cache when it exists, else the
    CSV), read column-selectively. Synthetic: the fixture ``covariates.csv`` column ``overall_pick``
    (present only in the dose regimes — the legacy fixtures are kept byte-identical, so those
    regimes cannot be passed to this stage).
    """
    cap = cfg["dose"]["capital"]
    col = str(cap["fixture_pick_column"] if synthetic else cap["pick_column"])
    if synthetic:
        path = sm.fixture_dir(cfg, regime) / cfg["security_models"]["inputs"]["covariates"]
        if not path.exists():
            raise FileNotFoundError(f"fixture covariates missing: {path}")
        src = f"read_csv_auto('{path}', header=true)"
        names = [r[0] for r in con.execute(f"describe select * from {src}").fetchall()]
        if col not in names:
            raise AssertionError(
                f"{path.name} has no '{col}' column (this regime predates the D50 dose panel); "
                f"columns: {names}")
        source = f"fixture:{path.name}"
    else:
        parq = common.resolve(cfg, "parquet") / (Path(cfg["files"]["players"]).stem + ".parquet")
        path = parq if parq.exists() else common.csv_path(cfg, "players")
        if not path.exists():
            raise FileNotFoundError(f"required input missing: {path}")
        src = (common.pq(path) if str(path).endswith(".parquet")
               else f"read_csv_auto('{path}', header=true)")
        names = [r[0] for r in con.execute(f"describe select * from {src}").fetchall()]
        if col not in names:
            raise AssertionError(f"{path.name} has no '{col}' column")
        source = f"real:{path.name}"
    out: dict[int, Any] = {}
    for nid, pick in con.execute(f'SELECT nfl_id, "{col}" FROM {src}').fetchall():
        if nid is None:
            continue
        try:
            key = int(nid)
        except (TypeError, ValueError):
            continue
        v = sm._num(pick)
        out[key] = (int(round(v)) if v is not None else None)
    log.info("overall_pick: %d rows, %d with a pick (%s)", len(out),
             sum(1 for v in out.values() if v is not None), source)
    return out, source


def build_panel(cfg: dict, synthetic: bool, regime: str, sm, panel: str, inp: dict, con,
                exposures: dict, envelope: dict, psum: dict, topk: Sequence[dict],
                snap_rows: Sequence[dict], cov: dict, picks: dict, assign: dict,
                scope_key: str, active_types: Sequence[str]) -> tuple[list[dict], dict]:
    """Model rows for ONE season_scope (panel), restricted to the trend stratum."""
    draft_years = {int(nid): c.get("draft_year") for nid, c in cov.items()}
    snap_counts = sm.scoped_snap_counts(snap_rows, active_types, panel=panel,
                                       draft_year=draft_years)
    game_ids = {int(r["game_id"]) for r in topk}
    game_index = common.load_game_index(con, inp["games"], game_ids=game_ids)
    rows, dropped = sm.build_effort_rows(
        topk, assign, envelope, cov, psum, cfg, keep_unclassified=True,
        snap_counts=snap_counts, game_index=game_index, season_types=active_types,
        season_scope=scope_key, min_snaps=int(cfg["game"]["route_snap_count"]), panel=panel)
    amap = {int(a["nfl_id"]): a for a in assign["assignments"]}
    kept: list[dict] = []
    for r in rows:
        nid = int(r["nfl_id"])
        a = amap.get(nid) or {}
        trend = sm._num(a.get("edp_trend"))
        if trend is None:
            continue                      # level-fallback players have no dose by construction
        r["edp_trend"] = float(trend)
        r["draft_position"] = (cov.get(nid) or {}).get("draft_position")
        r["overall_pick"] = picks.get(nid)
        kept.append(r)
    return kept, dropped


# --------------------------------------------------------------------------- #
# capital treatments
# --------------------------------------------------------------------------- #
def capital_sample(rows: Sequence[dict], cfg: dict,
                   treatment: str) -> tuple[list[dict], list[str], list[str], str, str]:
    """Apply one capital treatment: the sample, the extra numeric/categorical controls, notes.

    Returns ``(rows, cap_numeric, cap_categorical, note, band_label)``. No treatment ever drops a
    player silently: the exclusion is named in ``note`` and the surviving ``n_obs``/``n_players``
    are reported on every emitted row.
    """
    cap = cfg["dose"]["capital"]
    if treatment not in [str(t) for t in cap["treatments"]]:
        raise ValueError(f"unknown capital treatment {treatment!r}")
    if treatment == "unadjusted":
        return list(rows), [], [], "", ""
    if treatment == "pick_linear":
        sub = [r for r in rows if r.get("overall_pick") is not None]
        return (sub, ["overall_pick"], [],
                str(cap["pick_linear_note"]) +
                f" ({len(rows) - len(sub)} of {len(rows)} rows excluded)", "")
    if treatment == "pick_plus_level":
        sub = [r for r in rows if r.get("overall_pick") is not None
               and r.get("log_edp_pre") is not None]
        return (sub, ["overall_pick", "log_edp_pre"], [],
                str(cap["pick_linear_note"]) + " + the pre-Combine LEVEL (log_edp_pre)" +
                f" ({len(rows) - len(sub)} of {len(rows)} rows excluded)", "")
    if treatment == "round_band":
        sub = [r for r in rows if r.get("draft_position") not in (None, "")]
        return (sub, [], [str(cap["round_column"])],
                str(cap["round_band_note"]), "")
    band_label = str((cap.get("band_labels") or {}).get(treatment, treatment))
    allowed = [str(x) for x in (cap.get("bands") or {}).get(treatment, [])]
    sub = [r for r in rows if str(r.get("draft_position")) in set(allowed)]
    return (sub, [], [], f"fitted only inside {band_label} ({', '.join(allowed)})", band_label)


# --------------------------------------------------------------------------- #
# one cell: design + fit
# --------------------------------------------------------------------------- #
def _thin_note(n_obs: int, n_players: int, cfg: dict) -> str:
    mo, mp = int(cfg["dose"]["min_obs"]), int(cfg["dose"]["min_players"])
    bits = []
    if n_obs < mo:
        bits.append(f"n_obs={n_obs} < min_obs={mo}")
    if n_players < mp:
        bits.append(f"n_players={n_players} < min_players={mp}")
    return "; ".join(bits)


def _wald(res, names: list[str], dose_names: Sequence[str]) -> tuple[float, int, float]:
    """Wald chi-square over the dose basis columns with the CLUSTERED covariance."""
    idx = [names.index(n) for n in dose_names]
    b = np.asarray(res.params, float)[idx]
    V = np.asarray(res.cov_params(), float)[np.ix_(idx, idx)]
    try:
        stat = float(b @ np.linalg.solve(V, b))
    except np.linalg.LinAlgError:
        stat = float(b @ np.linalg.pinv(V) @ b)
    from scipy import stats as st
    stat = max(stat, 0.0)
    return stat, len(idx), float(st.chi2.sf(stat, len(idx)))


def _fit_row(**kw) -> dict:
    rec = dict(season_scope=None, scope=None, spec=None, feature=None, capital_treatment=None,
               term=None, coef=None, se=None, p=None, ci_lo=None, ci_hi=None, test_stat=None,
               df=None, n_obs=0, n_players=0, thin=False, note="", reliability_icc=None,
               coef_corrected=None, se_corrected=None, p_corrected=None, sign_convention="")
    rec.update(kw)
    return rec


def fit_cell(rows: Sequence[dict], cfg: dict, sm, season_scope: str, scope: str, spec: str,
             feature: str, treatment: str, icc: dict,
             prov: str) -> tuple[list[dict], list[dict], dict | None]:
    """Fit one (season_scope × scope × spec × feature × treatment) cell.

    Returns ``(dose_fits rows, dose_bins rows, curve model or None)``. A thin or unestimable cell
    returns the same shape with null coefficients and a reason — never a forced number.
    """
    dcfg = cfg["dose"]
    p = cfg["security_models"]["primary"]
    sign_conv = str(dcfg["sign_convention"])
    xkey = f"log_{feature}"
    min_obs, min_players = int(dcfg["min_obs"]), int(dcfg["min_players"])
    num_controls = [c for c in p["controls"] if c not in ("route_family", "position")]
    cat_controls = [c for c in p["controls"] if c in ("route_family", "position")]
    base = dict(season_scope=season_scope, scope=scope, spec=spec, feature=feature,
                capital_treatment=treatment, sign_convention=sign_conv,
                reliability_icc=(icc or {}).get("icc"))
    band_label = ""

    sub, cap_num, cap_cat, cap_note, band_label = capital_sample(rows, cfg, treatment)
    req = ["y", xkey, "edp_trend"] + num_controls + cap_num
    fin = sm._complete(sub, req)
    if cap_cat:
        fin = [r for r in fin if r.get(str(cfg["dose"]["capital"]["round_column"]))
               not in (None, "")]
    n_obs, n_players = len(fin), len({r["nfl_id"] for r in fin})
    thin_reason = _thin_note(n_obs, n_players, cfg)
    notes = [cap_note] if cap_note else []
    if band_label and treatment.startswith("within_"):
        base["band"] = band_label

    d = np.array([float(r["edp_trend"]) for r in fin], float) if fin else np.array([], float)

    # ------------------------------------------------------------------ bins
    if spec == "dose_bins":
        return _fit_bins(fin, d, cfg, sm, base, icc, thin_reason, notes, prov, xkey,
                         num_controls, cat_controls, cap_num, cap_cat, n_obs, n_players)

    # ------------------------------------------------------- dose basis by spec
    # Every spec starts from the linear basis and is REPLACED only when the spline/kink basis is
    # estimable; the fallbacks below therefore always leave a usable `d` column behind.
    dose_names: list[str] = ["d"]
    basis_fn: Callable[[np.ndarray], np.ndarray] = lambda g: np.asarray(g, float).reshape(-1, 1)
    df = 1
    spec_used = spec
    if spec in ("dose_spline", "dose_spline_knot5"):
        n_distinct = len(set(np.round(d, 10).tolist())) if d.size else 0
        if n_distinct < 5:
            notes.append(f"fewer than 5 distinct d values ({n_distinct}) -> linear fallback (df 1)")
            spec_used = "dose_linear"
        else:
            knots, k_note = (spline_knots(d, cfg) if spec == "dose_spline"
                             else knot5_knots(d, cfg))
            if k_note:
                notes.append(k_note)
            if len(knots) < 3:
                notes.append(f"only {len(knots)} usable knot(s) -> linear fallback (df 1)")
                spec_used = "dose_linear"
            else:
                B = rcs_basis(d, knots)
                B, names_b, keep, r_note = _rank_reduce(
                    B, ["d"] + [f"d_s{j}" for j in range(1, B.shape[1])])
                if r_note:
                    notes.append(r_note)
                dose_names = list(names_b)
                df = len(dose_names)                 # k-1 basis columns (§3.2)
                spec_used = spec

                def basis_fn(g, _kn=tuple(knots), _keep=tuple(keep)):   # noqa: E731
                    return rcs_basis(np.asarray(g, float), _kn)[:, list(_keep)]

                for j, nm in enumerate(dose_names):
                    for i, r in enumerate(fin):
                        r[nm] = float(B[i, j])
                notes.append("knots=" + ",".join(f"{k:.4f}" for k in knots) +
                             f"; df={df}")
    if spec == "dose_kink":
        k_at = float(dcfg["kink_at"])
        dose_names = ["d", "d_above_kink"]
        df = 2
        spec_used = "dose_kink"

        def basis_fn(g, _k=k_at):                    # noqa: E731
            gg = np.asarray(g, float)
            return np.column_stack([gg, gg * (gg >= _k)])

        for i, r in enumerate(fin):
            r["d"] = float(d[i])
            r["d_above_kink"] = float(d[i] * (d[i] >= k_at))
        notes.append(f"segmented at d={k_at:g}: basis b1*d + b2*d*I(d>={k_at:g}), so the fit carries a "
                     f"jump of +{k_at:g}*b2 at the knot (downward when b2 < 0) and slope_above_5 = "
                     f"b1+b2 is the above-knot slope (not a pure slope change); naive only: the ICC "
                     f"attenuation correction is applied to the LINEAR spec (reliability_icc here)")
    elif spec in ("dose_linear", "dose_bins") or spec_used == "dose_linear":
        for i, r in enumerate(fin):
            r["d"] = float(d[i])

    # ------------------------------------------------------------ thin guard
    if thin_reason:
        out = []
        terms = (["slope"] if df == 1 else
                 (["slope_below_5", "slope_above_5", "slope_change_at_5"]
                  if spec_used == "dose_kink"
                  else ["overall"]))
        for term in terms:
            out.append(_fit_row(**base, term=term, n_obs=n_obs, n_players=n_players, thin=True,
                                df=(1 if term != "overall" else df),
                                note="; ".join(notes + [f"thin: {thin_reason}"])))
        return out, [], None

    # --------------------------------------------------------------- fit
    try:
        X, names, dropped = sm.design(fin, [xkey] + dose_names + num_controls + cap_num,
                                      cat_controls + cap_cat,
                                      focal=[xkey] + dose_names)
    except ValueError as exc:
        return ([_fit_row(**base, term="unavailable", n_obs=n_obs, n_players=n_players,
                          note="; ".join(notes + [f"design failed: {exc}"]))], [], None)
    y = np.array([r["y"] for r in fin], float)
    clusters = np.array([r["nfl_id"] for r in fin])
    res = sm.ols(y, X, clusters=clusters)
    if dropped:
        notes.append("dropped controls: " + "; ".join(f"{a}:{b}" for a, b in dropped))

    out: list[dict] = []
    corr = dict(coef_corrected=None, se_corrected=None, p_corrected=None)
    if spec == "dose_linear" and bool(p.get("attenuation", True)) and df == 1:
        corr = _attenuate(fin, cfg, sm, X, names, y, d if d.size else
                          np.array([r["d"] for r in fin], float), clusters, icc, feature,
                          num_controls + cap_num, cat_controls + cap_cat, notes)

    if spec_used == "dose_kink":
        k_at = float(dcfg["kink_at"])
        b1 = sm._coef(res, names, "d")
        b2 = sm._coef(res, names, "d_above_kink")
        i1, i2 = names.index("d"), names.index("d_above_kink")
        V = np.asarray(res.cov_params(), float)
        est = float(res.params[i1] + res.params[i2])
        var = float(V[i1, i1] + V[i2, i2] + 2.0 * V[i1, i2])
        se = math.sqrt(max(var, 0.0)) if var > 0 else float("nan")
        z = float(dcfg["z"])
        from scipy import stats as st
        p_above = float(2.0 * st.norm.sf(abs(est / se))) if se and np.isfinite(se) and se > 0 \
            else float("nan")
        out.append(_fit_row(**base, term="slope_below_5", n_obs=n_obs, n_players=n_players,
                            df=1, note="; ".join(notes + [f"d < {k_at:g} (no break)"]), **b1))
        out.append(_fit_row(**base, term="slope_above_5", coef=est, se=se, p=p_above,
                            ci_lo=(est - z * se if np.isfinite(se) else None),
                            ci_hi=(est + z * se if np.isfinite(se) else None),
                            n_obs=n_obs, n_players=n_players, df=1,
                            note="; ".join(notes + [
                                f"b1 + b2 with se = sqrt(V11 + V22 + 2*V12) from the FULL "
                                f"clustered covariance (not a separate fit)"])))
        out.append(_fit_row(**base, term="slope_change_at_5", n_obs=n_obs, n_players=n_players,
                            df=1, note="; ".join(notes + [f"break at d={k_at:g}"]), **b2))
    elif df > 1:
        stat, wdf, p_w = _wald(res, names, dose_names)
        out.append(_fit_row(**base, term="overall", test_stat=stat, df=wdf, p=p_w,
                            n_obs=n_obs, n_players=n_players,
                            note="; ".join(notes + [
                                f"Wald over {wdf} basis column(s) with the player-clustered "
                                f"covariance; naive only: the ICC attenuation correction is applied "
                                f"to the LINEAR spec (reliability_icc here)"])))
    else:
        c = sm._coef(res, names, "d")
        out.append(_fit_row(**base, term="slope", n_obs=n_obs, n_players=n_players, df=1,
                            note="; ".join(notes) if notes else "", **c, **corr))

    # A spec that fell back to the linear form is NOT re-labelled as the spline/kink curve.
    model = None
    if spec_used == spec:
        model = dict(names=names, params=np.asarray(res.params, float),
                     cov=np.asarray(res.cov_params(), float), dose_names=list(dose_names),
                     basis_fn=basis_fn, n_obs=n_obs, n_players=n_players,
                     mean_fitted=float(np.mean(X @ np.asarray(res.params, float))),
                     mean_log_ceiling=float(np.mean([r[xkey] for r in fin])),
                     d=d.copy(), spec=spec, feature=feature, treatment=treatment, scope=scope,
                     season_scope=season_scope)
    return out, [], model


def _attenuate(fin: Sequence[dict], cfg: dict, sm, X: np.ndarray, names: list[str],
               y: np.ndarray, d: np.ndarray, clusters: np.ndarray, icc: dict, feature: str,
               ctrl_num: Sequence[str], ctrl_cat: Sequence[str], notes: list[str]) -> dict:
    """ICC-attenuation-corrected LINEAR slope (mirrors ``_apply_attenuation``).

    Two-regressor moment matrix on ``(x = ceiling, d = dose)`` with ``Var(x)`` scaled by the
    reliability ``lambda``; the corrected SE/p come from the **player-clustered bootstrap** inside
    ``07_security_models.fwl_attenuation`` (same seed/reps config as block B).
    """
    p = cfg["security_models"]["primary"]
    lam = (icc or {}).get("icc")
    if lam is None:
        notes.append(f"attenuation unavailable: no reliability ICC for {feature}")
        return dict(coef_corrected=None, se_corrected=None, p_corrected=None)
    lam = float(lam)
    if not (0.0 < lam <= 1.0):
        notes.append(f"attenuation unavailable: reliability_icc={lam} outside (0, 1]")
        return dict(coef_corrected=None, se_corrected=None, p_corrected=None)
    xkey = f"log_{feature}"
    C = np.column_stack(
        [np.ones(len(fin))]
        + [np.array([float(r[k]) for r in fin], float) for k in ctrl_num if k in names]
        + [X[:, j] for j, nm in enumerate(names)
           if "=" in nm and nm.split("=", 1)[0] in ctrl_cat])
    corr = sm.fwl_attenuation(
        y, np.array([float(r[xkey]) for r in fin], float), d, C, lam, clusters,
        int(p["attenuation_reps"]), int(cfg["seed"]) + int(p["attenuation_seed_offset"]))
    if not corr["ok"]:
        notes.append(f"attenuation unavailable: {corr['reason']}")
        return dict(coef_corrected=None, se_corrected=None, p_corrected=None)
    notes.append(f"corrected: Frisch-Waugh + ICC moment matrix (lambda={lam:g}), player-clustered "
                 f"bootstrap ({corr['n_boot']} reps)")
    return dict(coef_corrected=float(corr["beta_corr"][1]),
                se_corrected=(float(corr["se_group"]) if corr["se_group"] is not None else None),
                p_corrected=(float(corr["p_group"]) if corr["p_group"] is not None else None))


def _fit_bins(fin: Sequence[dict], d: np.ndarray, cfg: dict, sm, base: dict, icc: dict,
              thin_reason: str, notes: list[str], prov: str, xkey: str,
              num_controls: Sequence[str], cat_controls: Sequence[str], cap_num: Sequence[str],
              cap_cat: Sequence[str], n_obs: int, n_players: int) -> tuple[list[dict],
                                                                           list[dict], None]:
    """Fixed bins (+ the quantile variant when a fixed bin is thin) and their descriptions."""
    dcfg = cfg["dose"]
    z = float(dcfg["z"])
    bounds = [tuple(b) for b in dcfg["bins"]["bounds"]]
    labels = [str(x) for x in dcfg["bins"]["labels"]]
    reference = str(dcfg["bins"]["reference"])
    min_players = int(dcfg["min_players"])
    fit_rows: list[dict] = []
    bin_rows: list[dict] = []
    idx = bin_index(d, bounds) if d.size else np.array([], int)

    def _describe(mask_by_label: list[tuple[str, np.ndarray, str]], binning: str,
                  contrast: dict[str, dict], model_note: str,
                  ref_label: str | None = None) -> None:
        for label, mask, note in mask_by_label:
            sub = [r for i, r in enumerate(fin) if mask[i]]
            n_pl = len({r["nfl_id"] for r in sub})
            thin = n_pl < min_players or not sub
            gap = np.array([r["y"] - r[xkey] for r in sub], float) if sub else np.array([])
            if gap.size:
                mean_gap = float(np.mean(gap))
                if n_pl >= 2:
                    se_gap = float(sm.ols(gap, np.ones((gap.size, 1)),
                                          clusters=np.array([r["nfl_id"] for r in sub])).bse[0])
                else:
                    se_gap = None
            else:
                mean_gap, se_gap = None, None
            note_bits = [n for n in (note, model_note) if n]
            if thin:
                note_bits.append(f"thin: {n_pl} player(s) < min_players={min_players}"
                                 if sub else "empty bin")
            rec = dict(base, binning=binning, bin=label, n_players=n_pl, n_obs=len(sub),
                       mean_edp_trend=(float(np.mean([r["edp_trend"] for r in sub]))
                                       if sub else None),
                       mean_gap=mean_gap, se_gap=se_gap,
                       mean_realisation_pct=(100.0 * math.exp(mean_gap)
                                             if mean_gap is not None else None),
                       realisation_ci_lo=(100.0 * math.exp(mean_gap - z * se_gap)
                                          if mean_gap is not None and se_gap else None),
                       realisation_ci_hi=(100.0 * math.exp(mean_gap + z * se_gap)
                                          if mean_gap is not None and se_gap else None),
                       ref_contrast=None, ref_se=None, ref_ci_lo=None, ref_ci_hi=None,
                       thin=bool(thin), note="; ".join(note_bits))
            c = contrast.get(label)
            if c and not thin:
                rec.update(ref_contrast=c["coef"], ref_se=c["se"], ref_ci_lo=c["ci_lo"],
                           ref_ci_hi=c["ci_hi"])
            elif label == (ref_label or reference):
                rec.update(ref_contrast=0.0,
                           note="; ".join(note_bits +
                                          [f"reference level ({ref_label or reference}): "
                                           f"contrast = 0 by construction"]))
            bin_rows.append(rec)
            # one dose_fits row per bin (§3.4)
            fr = _fit_row(**base, term=f"bin:{label}", n_obs=len(sub), n_players=n_pl,
                          df=1, thin=bool(thin), note=rec["note"])
            if c and not thin:
                fr.update(coef=c["coef"], se=c["se"], p=c["p"], ci_lo=c["ci_lo"],
                          ci_hi=c["ci_hi"],
                          test_stat=((c["coef"] / c["se"]) ** 2 if c["se"] else None))
            fit_rows.append(fr)

    # ------------------------------------------------ fixed bins (always emitted)
    present = [lab for j, lab in enumerate(labels) if np.any(idx == j)] if d.size else []
    if thin_reason or reference not in present:
        why = thin_reason or f"reference bin {reference!r} is empty in this sample"
        for j, lab in enumerate(labels):
            n_pl = len({fin[i]["nfl_id"] for i in range(len(fin)) if idx.size and idx[i] == j})
            n_o = int(np.sum(idx == j)) if idx.size else 0
            bin_rows.append(dict(base, binning="fixed", bin=lab, n_players=n_pl, n_obs=n_o,
                                 mean_edp_trend=None, mean_gap=None, se_gap=None,
                                 mean_realisation_pct=None, realisation_ci_lo=None,
                                 realisation_ci_hi=None, ref_contrast=None, ref_se=None,
                                 ref_ci_lo=None, ref_ci_hi=None, thin=True,
                                 note=f"bins not fitted: {why}"))
            fit_rows.append(_fit_row(**base, term=f"bin:{lab}", n_obs=n_o, n_players=n_pl,
                                    thin=True, note=f"bins not fitted: {why}"))
        return fit_rows, bin_rows, None

    contrast_bins = [b for b in present if b != reference]
    dummy = {b: f"bin__{b}" for b in contrast_bins}
    for b in contrast_bins:
        j = labels.index(b)
        for i, r in enumerate(fin):
            r[dummy[b]] = (1.0 if idx[i] == j else 0.0)
    bin_note = (f"C(bin) with reference {reference}; bins are closed on the upper edge "
                f"({', '.join(labels)}); naive only: the ICC attenuation correction is applied to "
                f"the LINEAR spec (reliability_icc here)")
    try:
        X, names, dropped = sm.design(fin, [xkey] + [dummy[b] for b in contrast_bins]
                                      + list(num_controls) + list(cap_num),
                                      list(cat_controls) + list(cap_cat),
                                      focal=[xkey] + [dummy[b] for b in contrast_bins])
    except ValueError as exc:
        for b in labels:
            n_pl = len({fin[i]["nfl_id"] for i in range(len(fin)) if idx[i] == labels.index(b)})
            bin_rows.append(dict(base, binning="fixed", bin=b, n_players=n_pl,
                                 n_obs=int(np.sum(idx == labels.index(b))), mean_edp_trend=None,
                                 mean_gap=None, se_gap=None, mean_realisation_pct=None,
                                 realisation_ci_lo=None, realisation_ci_hi=None, ref_contrast=None,
                                 ref_se=None, ref_ci_lo=None, ref_ci_hi=None, thin=True,
                                 note=f"bin model not estimable: {exc}"))
            fit_rows.append(_fit_row(**base, term=f"bin:{b}", n_obs=0, n_players=n_pl,
                                     thin=True, note=f"bin model not estimable: {exc}"))
        return fit_rows, bin_rows, None
    y = np.array([r["y"] for r in fin], float)
    res = sm.ols(y, X, clusters=np.array([r["nfl_id"] for r in fin]))
    contrast: dict[str, dict] = {}
    for b in contrast_bins:
        contrast[b] = sm._coef(res, names, dummy[b])
    if dropped:
        bin_note += "; dropped: " + "; ".join(f"{a}:{b}" for a, b in dropped)
    masks = [(lab, (idx == j), "") for j, lab in enumerate(labels)]
    _describe(masks, "fixed", contrast, bin_note, reference)

    # ---------------------------- quantile variant, ONLY when a fixed bin is thin
    thin_fixed = [r for r in bin_rows if r["binning"] == "fixed"
                  and (r["thin"] or r["n_players"] < min_players)]
    if thin_fixed:
        nq = int(dcfg["quantile_bins"])
        edges = quantile_edges(d, nq)
        qidx = np.searchsorted(np.array(edges, float), d, side="left")
        qlabels: list[str] = []
        qmasks: list[np.ndarray] = []
        for j in range(len(edges) + 1):
            m = (qidx == j)
            if not m.any():
                continue
            lo, hi = float(np.min(d[m])), float(np.max(d[m]))
            qlabels.append(f"[{lo:g}, {hi:g}]")
            qmasks.append(m)
        qref = qlabels[0]
        qcontrast_bins = qlabels[1:]
        qdummy = {b: f"qbin__{i}" for i, b in enumerate(qcontrast_bins)}
        for b in qcontrast_bins:
            j = qlabels.index(b)
            for i, r in enumerate(fin):
                r[qdummy[b]] = (1.0 if qmasks[j][i] else 0.0)
        qnote = (f"quantile bins (q={nq}) fitted because a fixed bin is thin; reference = lowest "
                 f"bin {qref}; naive only: the ICC attenuation correction is applied to the LINEAR "
                 f"spec (reliability_icc here)")
        try:
            Xq, nq_names, qdropped = sm.design(
                fin, [xkey] + [qdummy[b] for b in qcontrast_bins] + list(num_controls)
                + list(cap_num), list(cat_controls) + list(cap_cat),
                focal=[xkey] + [qdummy[b] for b in qcontrast_bins])
            resq = sm.ols(y, Xq, clusters=np.array([r["nfl_id"] for r in fin]))
            qcontrast = {b: sm._coef(resq, nq_names, qdummy[b]) for b in qcontrast_bins}
            if qdropped:
                qnote += "; dropped: " + "; ".join(f"{a}:{b}" for a, b in qdropped)
        except ValueError as exc:
            qcontrast = {}
            qnote += f"; not estimable: {exc}"
        _describe([(lab, m, "") for lab, m in zip(qlabels, qmasks)], "quantile", qcontrast, qnote,
                  qref)
    return fit_rows, bin_rows, None


# --------------------------------------------------------------------------- #
# curves
# --------------------------------------------------------------------------- #
def curve_rows(model: dict, cfg: dict) -> list[dict]:
    """``fitted_log(x) = mean_i(X_i b) + g(x) - mean_i g(x_i)`` on the configured grid (§5)."""
    d = np.asarray(model["d"], float)
    if d.size == 0:
        return []
    z = float(cfg["dose"]["z"])
    n = int(cfg["dose"]["grid_n"])
    grid = np.linspace(float(d.min()), float(d.max()), n)
    B_obs = np.asarray(model["basis_fn"](d), float)
    B_grid = np.asarray(model["basis_fn"](grid), float)
    idx = [model["names"].index(nm) for nm in model["dose_names"]]
    beta = model["params"][idx]
    V = model["cov"][np.ix_(idx, idx)]
    centred = B_grid - B_obs.mean(axis=0)
    g = centred @ beta
    var = np.einsum("ij,jk,ik->i", centred, V, centred)
    se = np.sqrt(np.maximum(var, 0.0))
    base_log = model["mean_fitted"] - model["mean_log_ceiling"]
    out = []
    for k in range(n):
        out.append(dict(season_scope=model["season_scope"], scope=model["scope"],
                        spec=model["spec"], feature=model["feature"],
                        capital_treatment=model["treatment"], x=float(grid[k]),
                        fitted_log=float(model["mean_fitted"] + g[k]),
                        realisation_pct=float(100.0 * math.exp(base_log + g[k])),
                        realisation_ci_lo=float(100.0 * math.exp(base_log + g[k] - z * se[k])),
                        realisation_ci_hi=float(100.0 * math.exp(base_log + g[k] + z * se[k])),
                        n_obs=model["n_obs"], n_players=model["n_players"]))
    return out


# --------------------------------------------------------------------------- #
# figure
# --------------------------------------------------------------------------- #
def make_figure(cfg: dict, prov: str, bins: Sequence[dict], curves: Sequence[dict],
                linear: dict | None, out_dir: Path) -> dict:
    """The supplementary exploratory figure: binned means + spline curve, zero-based y-axis."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figcfg = cfg["dose"]["figure"]
    refs = [float(x) for x in figcfg["reference_spots"]]
    fig, ax = plt.subplots(figsize=tuple(float(x) for x in figcfg["figsize"]))

    b = [r for r in bins if r["season_scope"] == "all_seasons" and r["scope"] == "WR"
         and r["binning"] == "fixed" and r["capital_treatment"] == "unadjusted"
         and r.get("mean_realisation_pct") is not None]
    if b:
        xs = [float(r["mean_edp_trend"]) for r in b]
        ys = [float(r["mean_realisation_pct"]) for r in b]
        lo = [float(r["realisation_ci_lo"]) for r in b if r.get("realisation_ci_lo") is not None]
        hi = [float(r["realisation_ci_hi"]) for r in b if r.get("realisation_ci_hi") is not None]
        if len(lo) == len(ys) and len(hi) == len(ys):
            yerr = [[max(0.0, ys[i] - lo[i]) for i in range(len(ys))],
                    [max(0.0, hi[i] - ys[i]) for i in range(len(ys))]]
        else:
            yerr = None
        ax.errorbar(xs, ys, yerr=yerr, fmt="o", color="C0", ecolor="C0", elinewidth=1.0,
                    capsize=3, label="binned mean ± 95% CI (fixed bins)")
        for r in b:
            ax.annotate(str(int(r["n_players"])),
                        (float(r["mean_edp_trend"]), float(r["mean_realisation_pct"])),
                        textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8,
                        color="C0")

    sp = [r for r in curves if r["season_scope"] == "all_seasons" and r["scope"] == "WR"
          and r["spec"] == "dose_spline" and r["capital_treatment"] == "unadjusted"]
    if sp:
        x = [float(r["x"]) for r in sp]
        ax.plot(x, [float(r["realisation_pct"]) for r in sp], "-", color="C1", lw=1.6,
                label="restricted cubic spline (3 df) ± 95% CI")
        ax.fill_between(x, [float(r["realisation_ci_lo"]) for r in sp],
                        [float(r["realisation_ci_hi"]) for r in sp], color="C1", alpha=0.18)

    for x0 in refs:
        ax.axvline(x0, ls="--", lw=1.0, color="0.35")
        ax.annotate(f"{x0:g} spots", (x0, 0.02), xycoords=("data", "axes fraction"),
                    rotation=90, va="bottom", ha="right", fontsize=8, color="0.35")

    if linear and linear.get("coef") is not None:
        ci = (f"[{float(linear['ci_lo']):+.4f}, {float(linear['ci_hi']):+.4f}]"
              if linear.get("ci_lo") is not None and linear.get("ci_hi") is not None else "—")
        txt = (f"linear slope {float(linear['coef']):+.4f} log-units per spot\n"
               f"95% CI {ci} · p = {float(linear['p']):.3g}\n"
               f"N = {int(linear['n_obs'])} efforts / {int(linear['n_players'])} players")
        ax.annotate(txt, (0.02, 0.98), xycoords="axes fraction", va="top", ha="left",
                    fontsize=8.5, family="monospace",
                    bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="0.6", alpha=0.9))
    else:
        ax.annotate("linear slope unavailable (thin cell)", (0.02, 0.98),
                    xycoords="axes fraction", va="top", fontsize=8.5)

    ax.set_ylim(bottom=0.0)
    ax.set_xlabel("EDP spots fallen between the 2-week and 1-week pre-Combine snapshots\n"
                  "(positive = the EDP number rose = the stock fell)")
    ax.set_ylabel("realised % of the Combine ceiling  (100·exp(gap))")
    ax.set_title(f"{prov} · exploratory — post hoc\n"
                 f"continuous exposure vs realisation of the Combine ceiling (WR, all seasons)",
                 fontsize=9.5)
    ax.legend(loc="lower left", fontsize=8, frameon=False)
    ax.grid(alpha=0.25, lw=0.6)
    path = out_dir / str(figcfg["file"])
    fig.savefig(path, dpi=int(figcfg["dpi"]), bbox_inches="tight")
    plt.close(fig)
    return dict(file=str(path.name), path=str(path), dpi=int(figcfg["dpi"]),
                bytes=int(path.stat().st_size) if path.exists() else 0)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def stage_dir(cfg: dict, synthetic: bool, regime: str) -> Path:
    base = common.resolve(cfg, "outputs", "security_models") / str(cfg["dose"]["out_subdir"])
    return base / "synthetic" / regime if synthetic else base


def run(cfg: dict, mode: str, synthetic: bool, regime: str, out_dir: Path, prov: str, log) -> dict:
    sm = load_stage07()
    dcfg = cfg["dose"]
    p = cfg["security_models"]["primary"]
    scope_key, active_types = common.scope_types(cfg)
    inp = sm._inputs(cfg, synthetic, regime)
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    exposures = sm.load_exposures(con, inp["exposure"])
    envelope = sm.load_envelope(con, inp["envelope"])
    psum = sm.load_player_summary(con, inp["players"])
    topk = sm.load_topk(con, inp["topk"])
    snap_rows = sm.load_snap_counts(con, inp["snap_counts"])
    cov = sm.load_covariates(con, cfg, synthetic, regime, issues=[])
    picks, pick_source = load_pick(con, cfg, synthetic, regime, sm, log)
    rel = sm.load_reliability(cfg, synthetic, regime, log)
    pool_rows = [dict(nfl_id=nid, position_group=ex["position_group"], edp_pre=ex["edp_pre"],
                      log_edp_pre=ex["log_edp_pre"], edp_trend=ex["edp_trend"],
                      has_trend=ex["has_trend"], has_primary=ex["has_primary"])
                 for nid, ex in sorted(exposures.items())]
    import security_features as sf
    assign = sf.assign_security_groups(pool_rows, cfg)

    feature_specs: dict[str, list[str]] = {}
    for feat in [str(f) for f in dcfg["features"]]:
        specs = []
        if feat in [str(f) for f in dcfg["linear_spline_features"]]:
            specs += ["dose_linear", "dose_spline"]
        if feat in [str(f) for f in dcfg["other_spec_features"]]:
            specs += ["dose_spline_knot5", "dose_bins", "dose_kink"]
        feature_specs[feat] = specs

    fits: list[dict] = []
    bins: list[dict] = []
    curves: list[dict] = []
    panel_dropped: dict[str, dict] = {}
    for season_scope in [str(x) for x in dcfg["season_scopes"]]:
        rows, dropped = build_panel(cfg, synthetic, regime, sm, season_scope, inp, con, exposures,
                                    envelope, psum, topk, snap_rows, cov, picks, assign,
                                    scope_key, active_types)
        panel_dropped[season_scope] = dropped
        log.info("%s: panel %s -> %d trend-stratum effort rows, %d players", prov, season_scope,
                 len(rows), len({r["nfl_id"] for r in rows}))
        for scope in [str(s) for s in dcfg["scopes"]]:
            sub = rows if scope == "pooled" else [r for r in rows
                                                  if str(r.get("position")) == scope]
            for feat, specs in feature_specs.items():
                icc = rel.get(feat) or {}
                for spec in specs:
                    for treatment in [str(t) for t in dcfg["capital"]["treatments"]]:
                        fr, br, model = fit_cell(sub, cfg, sm, season_scope, scope, spec, feat,
                                                 treatment, icc, prov)
                        fits.extend(fr)
                        bins.extend(br)
                        if model is not None:
                            eligible = (spec in [str(s) for s in dcfg["curve"]["specs"]]
                                        and feat == str(dcfg["curve"]["feature"])
                                        and treatment in [str(t) for t in
                                                          dcfg["curve"]["capital_treatments"]])
                            if eligible:
                                curves.extend(curve_rows(model, cfg))
    con.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    linear = next((r for r in fits if r["spec"] == "dose_linear"
                   and r["season_scope"] == "all_seasons" and r["scope"] == "WR"
                   and r["feature"] == "env_vmax" and r["capital_treatment"] == "unadjusted"), None)
    fig = make_figure(cfg, prov, bins, curves, linear, out_dir)
    log.info("%s: figure -> %s (%d bytes, %d dpi)", prov, fig["path"], fig["bytes"], fig["dpi"])

    emit(out_dir / "dose_fits.csv", fits, prov, FIT_COLS)
    emit(out_dir / "dose_bins.csv", bins, prov, BIN_COLS)
    emit(out_dir / "dose_curve.csv", curves, prov, CURVE_COLS)

    ground = None
    meta_path = sm.fixture_dir(cfg, regime) / cfg["security_models"]["inputs"]["meta"]
    if synthetic and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ground = dict(regime=meta.get("regime"), dose=meta.get("design", {}).get("dose"),
                          note="SYNTHETIC FIXTURE ground truth — not a finding")
        except (ValueError, OSError):
            ground = None

    summary = dict(
        provenance=prov, mode=mode, synthetic=bool(synthetic), regime=regime, stage=STAGE,
        label=EXPLORATORY,
        sign_convention=str(dcfg["sign_convention"]),
        season_scope=dict(active=scope_key, types=list(active_types),
                          panels=[str(x) for x in dcfg["season_scopes"]]),
        stratum_note=("every row here has a finite edp_trend, i.e. this IS the trend stratum; "
                      "`stratum` is constant and is therefore NOT a control (no stratum FE)."),
        exclusions=dict(no_trend_rows="level-fallback players carry no dose and are excluded",
                        dropped_by_panel=panel_dropped),
        pick_source=pick_source,
        n_pick_rows=len(picks),
        n_pick_missing=sum(1 for v in picks.values() if v is None),
        thresholds=dict(min_obs=int(dcfg["min_obs"]), min_players=int(dcfg["min_players"]),
                        knot5=float(dcfg["knot5"]), kink_at=float(dcfg["kink_at"]),
                        knots_3df=[float(x) for x in dcfg["knots_3df"]],
                        bins=[str(x) for x in dcfg["bins"]["labels"]],
                        features=[str(x) for x in dcfg["features"]],
                        capital=[str(x) for x in dcfg["capital"]["treatments"]]),
        reliability=rel, figure=fig,
        dose_fits=fits, dose_bins=bins, dose_curve_n=len(curves),
        ground_truth=ground,
        figure_note=("supplementary EXPLORATORY figure — not one of the 5 writeup figures"),
        n_thin=sum(1 for r in fits if r.get("thin")),
    )
    summary["statements"] = statements(summary)
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True,
                                                     default=str), encoding="utf-8")
    (out_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")
    log.info("%s: dose panel written to %s (%d fit rows, %d bin rows, %d curve points, %d thin)",
             prov, out_dir, len(fits), len(bins), len(curves), summary["n_thin"])
    return summary


# --------------------------------------------------------------------------- #
# plain-words statements (computed from the fitted numbers, never typed)
# --------------------------------------------------------------------------- #
def _pick(fits: Sequence[dict], **kw) -> dict | None:
    for r in fits:
        if all(r.get(k) == v for k, v in kw.items()):
            return r
    return None


def _sig(r: dict | None) -> bool:
    return bool(r and r.get("p") is not None and r.get("coef") is not None
                and float(r["p"]) < 0.05)


def statements(s: dict) -> dict:
    fits = s["dose_fits"]
    lin = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                feature="env_vmax", capital_treatment="unadjusted")
    pick = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                 feature="env_vmax", capital_treatment="pick_linear")
    band = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                 feature="env_vmax", capital_treatment="round_band")
    out: dict[str, str] = {}

    def _f(v, nd=4):
        return "—" if v is None else f"{float(v):+.{nd}f}"

    if lin and lin.get("coef") is not None:
        out["sign"] = (
            f"The WR linear slope is {_f(lin['coef'])} log-units per +1 EDP spot fallen "
            f"(95% CI {_f(lin['ci_lo'])} to {_f(lin['ci_hi'])}, p = {float(lin['p']):.3g}, "
            f"{int(lin['n_obs'])} efforts / {int(lin['n_players'])} players). "
            + ("A NEGATIVE slope means a slipping player realises LESS of his Combine ceiling — the "
               "PI's stated expectation." if float(lin["coef"]) < 0 else
               "A POSITIVE slope means a slipping player realises MORE of his Combine ceiling — the "
               "direction the pre-registered H1 wording predicts."))
    else:
        out["sign"] = "The WR linear slope is unavailable (thin cell) — no sign is reported."

    if lin and pick and lin.get("coef") is not None and pick.get("coef") is not None:
        dies = (abs(float(pick["coef"])) < 0.5 * abs(float(lin["coef"])) or not _sig(pick))
        detail = (f"unadjusted {_f(lin['coef'])} (p = {float(lin['p']):.3g}) vs pick-adjusted "
                  f"{_f(pick['coef'])} (p = {float(pick['p']):.3g}); "
                  + (f"round-band-adjusted {_f(band['coef'])} (p = {float(band['p']):.3g})."
                     if band and band.get("coef") is not None else
                     "the round-band adjustment is unavailable."))
        if not _sig(lin):
            out["capital"] = ("**There is no unadjusted dose slope to attribute:** " + detail +
                              " A null slope cannot be 'draft position in disguise'.")
        elif dies:
            out["capital"] = ("**The dose slope is draft position in disguise:** " + detail)
        else:
            out["capital"] = ("**The dose slope is not draft position in disguise:** " + detail)
    else:
        out["capital"] = "The capital comparison is unavailable (thin cell)."

    # feature fragility (D38/§8): does the slope appear with all three features?
    names = [str(f) for f in (s["thresholds"].get("features") or [])]
    hits, bits = [], []
    for feat in names:
        r = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                  feature=feat, capital_treatment="unadjusted")
        icc = (s.get("reliability") or {}).get(feat, {}).get("icc")
        if r is None or r.get("coef") is None:
            bits.append(f"{feat}: not estimable")
            continue
        ok = _sig(r)
        hits.append(ok)
        bits.append(f"{feat}: {_f(r['coef'])} (p = {float(r['p']):.3g}; ICC "
                    f"{'—' if icc is None else f'{float(icc):g}'})")
    if hits and all(hits):
        out["features"] = ("The dose slope appears with ALL THREE ceiling features — it does not "
                           "inherit the env_vmax fragility. " + "; ".join(bits) + ".")
    elif any(hits):
        out["features"] = ("The dose slope appears with SOME ceiling features only — it inherits part "
                           "of the existing fragility. " + "; ".join(bits) + ".")
    else:
        out["features"] = ("The dose slope appears with NO ceiling feature at p<0.05. " +
                           "; ".join(bits) + ".")

    k = [r for r in fits if r["spec"] == "dose_kink" and r["season_scope"] == "all_seasons"
         and r["scope"] == "WR" and r["capital_treatment"] == "unadjusted"]
    if k:
        seg = {r["term"]: r for r in k}
        out["kink"] = (
            f"Segmented (knot at {float(s['thresholds']['kink_at']):g}): below the knot "
            f"{_f(seg.get('slope_below_5', {}).get('coef'))}, above it "
            f"{_f(seg.get('slope_above_5', {}).get('coef'))}, break "
            f"{_f(seg.get('slope_change_at_5', {}).get('coef'))} "
            f"(break p = {_fmt_p(seg.get('slope_change_at_5', {}).get('p'))}).")
    else:
        out["kink"] = "The segmented model is unavailable."
    out["shape"] = ("The explicit-knot-at-5 spline is a SHAPE question (is the curve steeper past 5 "
                    "spots?), not a rival model and not a new threshold search.")
    return out


def render_summary(s: dict) -> str:
    L: list[str] = [f"# Dose-response panel — continuous pre-Combine stock movement ({s['provenance']})",
                    ""]
    L.append(f"> **{EXPLORATORY}.** This panel models the exposure continuously instead of at the "
             "declared binary cut. `b_primary.csv` and the pre-registered test are untouched; "
             "nothing here can change the verdict.")
    L.append("")
    if s.get("synthetic"):
        L.append("> **SYNTHETIC FIXTURE (not a finding).** Every number is generated by "
                 "`src/synthetic_security_game_fixture.py`; it proves the plumbing, nothing about "
                 "the NFL.")
        L.append("")
    L.append("## How to read the sign (plain words)")
    L.append("")
    L.append(f"**`coef` = the change in log(game peak speed) per +1 EDP spot fallen, holding the "
             f"Combine ceiling fixed.**")
    L.append("")
    L.append("* **NEGATIVE** = a slipping player realises **LESS** of his Combine ceiling — the PI's "
             "stated expectation.")
    L.append("* **POSITIVE** = a slipping player realises **MORE** — the direction the "
             "pre-registered `TASK.md` H1 wording predicts.")
    L.append("* `edp_trend` positive = the EDP number rose = the stock fell. The exposure is "
             "**movement**, not level.")
    L.append("")
    L.append(f"*Stamped on every row:* “{s['sign_convention']}”")
    L.append("")
    L.append("## What was read")
    L.append("")
    L.append(f"- **mode:** `{s['mode']}` · **regime:** `{s['regime']}` · **synthetic:** "
             f"{s['synthetic']} · provenance `{s['provenance']}`")
    _sc = s["season_scope"]
    L.append(f"- **season scope (D49):** `{_sc['active']}` = `{'+'.join(_sc['types'])}`; panels "
             f"{', '.join(_sc['panels'])} fitted in ONE invocation.")
    L.append(f"- **{s['stratum_note']}**")
    L.append(f"- **exposure:** `d = edp_trend`; {s['exclusions']['no_trend_rows']}.")
    L.append(f"- **capital source:** `{s['pick_source']}` — {s['n_pick_rows']} rows, "
             f"{s['n_pick_missing']} without a pick (undrafted; **never invented**).")
    t = s["thresholds"]
    L.append(f"- **thin discipline:** a cell is fitted only when `n_obs >= {t['min_obs']}` **and** "
             f"`n_players >= {t['min_players']}`; thin cells carry null coefficients + a reason.")
    L.append(f"- **geometry:** 3-df spline knots at percentiles {t['knots_3df']}; explicit knot at "
             f"{t['knot5']:g}; segmented break at {t['kink_at']:g}; fixed bins "
             f"{', '.join(t['bins'])}.")
    L.append("")
    L.append("## The findings, in words")
    L.append("")
    for key, head in (("sign", "Linear slope"), ("capital", "Capital confound"),
                      ("features", "Feature robustness (does the slope inherit the fragility?)"),
                      ("kink", "Segmented"), ("shape", "Explicit-knot spline")):
        L.append(f"**{head}.** {s['statements'][key]}")
        L.append("")
    L.append("### Linear slope — all season panels, all capital treatments")
    L.append("| season panel | scope | feature | capital | coef | se | p | ci_lo | ci_hi | "
             "n_obs | n_players | coef_corrected | p_corrected |")
    L.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    order = {"unadjusted": 0, "pick_linear": 1, "round_band": 2, "pick_plus_level": 3,
             "within_R1": 4, "within_R2_R3": 5, "within_R4plus": 6}
    scope_order = {"WR": 0, "TE": 1, "pooled": 2}
    rows = [r for r in s["dose_fits"] if r["spec"] == "dose_linear"]
    rows.sort(key=lambda r: (r["season_scope"], scope_order.get(r["scope"], 9), r["feature"],
                             order.get(r["capital_treatment"], 9)))
    for r in rows:
        L.append(f"| {r['season_scope']} | {r['scope']} | {r['feature']} | "
                 f"{r['capital_treatment']} | {_fmt(r['coef'])} | {_fmt(r['se'])} | "
                 f"{_fmt_p(r['p'])} | {_fmt(r['ci_lo'])} | {_fmt(r['ci_hi'])} | {r['n_obs']} | "
                 f"{r['n_players']} | {_fmt(r['coef_corrected'])} | {_fmt_p(r['p_corrected'])} |")
    L.append("")
    L.append("`coef_corrected` is the ICC-attenuation correction (Frisch–Waugh + a 2-regressor moment "
             "matrix with Var(ceiling) scaled by the reliability λ, player-clustered bootstrap SE). "
             "It is reported **beside** the naive estimate, never instead of it — and only for the "
             "linear spec, which is the like-for-like replacement for the binary contrast. "
             "Non-linear specs are naive only (the ICC is reported on every row).")
    L.append("")
    L.append("### Spline / explicit-knot overall tests (Wald over the dose basis, clustered)")
    L.append("| season panel | scope | spec | feature | capital | test_stat | df | p | n_obs | "
             "n_players | note |")
    L.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---|")
    for r in s["dose_fits"]:
        if r["spec"] in ("dose_spline", "dose_spline_knot5") and r["term"] == "overall":
            L.append(f"| {r['season_scope']} | {r['scope']} | {r['spec']} | {r['feature']} | "
                     f"{r['capital_treatment']} | {_fmt(r['test_stat'], 2)} | {r['df']} | "
                     f"{_fmt_p(r['p'])} | {r['n_obs']} | {r['n_players']} | {r['note']} |")
    L.append("")
    L.append("### Binned contrasts (WR, all seasons)")
    L.append("| binning | capital | bin | n_players | n_obs | mean d | mean gap | realisation % "
             "| 95% CI | contrast vs reference | thin |")
    L.append("|---|---|---|---:|---:|---:|---:|---:|---|---:|---|")
    for r in s["dose_bins"]:
        if r["scope"] != "WR" or r["season_scope"] != "all_seasons":
            continue
        ci = ("—" if r.get("realisation_ci_lo") is None
              else f"{float(r['realisation_ci_lo']):.1f}–{float(r['realisation_ci_hi']):.1f}")
        con = ("—" if r.get("ref_contrast") is None
               else f"{float(r['ref_contrast']):+.4f} ({float(r['ref_se']):.4f})"
               if r.get("ref_se") is not None else "0 (reference)")
        L.append(f"| {r['binning']} | {r['capital_treatment']} | {r['bin']} | {r['n_players']} | "
                 f"{r['n_obs']} | {_fmt(r['mean_edp_trend'], 2)} | {_fmt(r['mean_gap'])} | "
                 f"{_fmt(r['mean_realisation_pct'], 1)} | {ci} | {con} | "
                 f"{'yes' if r['thin'] else 'no'} |")
    L.append("")
    L.append("### Figure")
    f = s["figure"]
    L.append(f"`{f['file']}` — {f['bytes']} bytes, {f['dpi']} dpi. "
             f"{s['figure_note']} (the writeup cap of 5 figures is unchanged).")
    L.append("")
    L.append("## Caveats")
    L.append("- This panel is exploratory and post hoc. It cannot change `b_primary.csv`, the "
             "pre-registered test, or the frozen values in `notes/prereg.md`.")
    L.append("- The outcome is block B's exactly: `log(peak_speed)` on the top-k efforts, "
             "`log_env_vmax` as the ceiling, the same controls minus `stratum` (constant in this "
             "stratum), `statsmodels` OLS with `cov_type=\"cluster\"`, `groups=nfl_id`.")
    L.append("- `dose_bins` bins are closed on the upper edge, so they form a total partition; for "
             "the integer EDP trends they are exactly `<=0 / 1-2 / 3-4 / 5-9 / 10+`.")
    L.append("- `pick_linear` and `pick_plus_level` exclude undrafted rows (no pick exists). "
             "`round_band` keeps `undrafted` as its own level. Within-band treatments fit only that "
             "band, so their slopes are not comparable across bands.")
    L.append("- `peak_speed` and `speed_at_20yd` are near-collinear with `env_vmax` in the Combine "
             "envelope; the feature panel is a fragility check, not three independent outcomes.")
    L.append("")
    return "\n".join(L)


def _fmt_p(v: Any) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{float(v):.3g}"


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    return f"{float(v):.{nd}f}"


# --------------------------------------------------------------------------- #
# sample placeholder / checkpoint / main
# --------------------------------------------------------------------------- #
def sample_placeholder(cfg: dict, prov: str, out_dir: Path, log) -> None:
    note = ("the dose panel needs the full game-tracking files and FULL-provenance thresholds; the "
            "2023 sample is a non-representative prefix")
    out_dir.mkdir(parents=True, exist_ok=True)
    emit(out_dir / "dose_summary.csv",
         [dict(stage=STAGE, status=PENDING, dataset="sample", note=note)],
         prov, ["stage", "status", "dataset", "note"])
    (out_dir / "SUMMARY.md").write_text(
        f"# Dose-response panel — {prov}\n\n"
        f"- **status:** `{PENDING}` — {note}\n"
        f"- **label:** {EXPLORATORY}\n"
        f"- **sign convention:** {cfg['dose']['sign_convention']}\n", encoding="utf-8")


def _done_ok(done: Path, mode: str, prov: str, synthetic: bool, regime: str,
             fingerprint: str | None = None) -> bool:
    return common.checkpoint_match(common.read_done_marker(done), mode=mode, provenance=prov,
                                   synthetic=synthetic, regime=regime, path=done,
                                   fingerprint=fingerprint)


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 D50 — continuous dose-response panel "
                                             "(exploratory)")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild")
    ap.add_argument("--synthetic", action="store_true",
                    help="run the identical path on the committed dose fixture (not a finding)")
    ap.add_argument("--synthetic-regime", choices=["dose", "dose_null", "dose_kink", "dose_capital"],
                    default="dose")
    ap.add_argument("--scope", choices=["primary", "sensitivity"], default=None,
                    help="season-type scope for this run (default: config game.scope.active)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()
    gate.enforce(cfg, mode, STAGE, requires_game_side=True, logger=log)

    regime = args.synthetic_regime
    prov = PROV_SYNTH if args.synthetic else common.provenance(cfg, mode)
    scope_key, _types = common.scope_types(cfg, args.scope)
    out_dir = stage_dir(cfg, args.synthetic, regime)
    configured_active = str(cfg["game"]["scope"].get("active", "primary"))
    if scope_key != configured_active:
        out_dir = out_dir / scope_key
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = f"{STAGE}[{mode}{'-synthetic-' + regime if args.synthetic else ''}]"
    fingerprint = common.config_fingerprint(cfg, STAGE)

    done = out_dir / "_DONE.json"
    if _done_ok(done, mode, prov, args.synthetic, regime, fingerprint) and not args.force:
        log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
        common.append_run_log(cfg, stage, mode, 0.0, common.peak_ram_mb(), "",
                              {"skipped": "checkpoint"})
        return
    if done.exists() and not args.force:
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding",
                 done.name, common.checkpoint_mismatch_note(
                     common.read_done_marker(done), mode=mode, provenance=prov,
                     synthetic=args.synthetic, regime=regime, fingerprint=fingerprint))

    if mode == "sample" and not args.synthetic:
        with common.timed_stage(cfg, stage, mode, synthetic=False):
            sample_placeholder(cfg, prov, out_dir, log)
    else:
        # House convention (03/04/05/07): a synthetic run is LABELLED "synthetic" in SUMMARY, never
        # with the config's default mode. The checkpoint marker keeps the real mode (as stage 07 does).
        mode_label = "synthetic" if args.synthetic else mode
        with common.timed_stage(cfg, stage, mode, synthetic=args.synthetic):
            run(cfg, mode_label, args.synthetic, regime, out_dir, prov, log)

    done.write_text(json.dumps(common.done_marker(
        cfg, STAGE, mode=mode, provenance=prov, synthetic=bool(args.synthetic),
        regime=regime, season_scope=scope_key), indent=2), encoding="utf-8")
    log.info("dose-response panel complete -> %s", out_dir)


if __name__ == "__main__":
    main()
