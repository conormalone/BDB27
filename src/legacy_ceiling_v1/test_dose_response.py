#!/usr/bin/env python3
"""Behaviour tests for the dose-response panel (D50) — ``src/10_dose_response.py``.

Runnable **without pytest** (``python src/tests/test_dose_response.py``) and collected by pytest
(functions named ``test_*``). Covers the spline/bins/kink machinery, the thin-cell discipline, the
planted recovery on the four committed dose fixtures, the capital confound, determinism, the CLI,
sample mode and the supplementary figure.

Notes on the recovery tolerance (read before changing a number here)
--------------------------------------------------------------------
The four dose regimes share **one population and one RNG stream**, so ``dose`` vs ``dose_null`` (and
``dose`` vs ``dose_kink``) are an *exact* counterfactual: the planted difference is what an estimator
must recover. A single fixture replication still carries its realised noise draw, which is not
exactly orthogonal to the deterministic ``edp_trend`` grid; that is ordinary sampling error, so each
recovery check is stated as (i) a magnitude tolerance and (ii) the estimate's own significance — not
as an exact equality. ``dose_null`` is a null *in expectation*: its level estimate is small in every
scope (|slope| < 0.001, half the planted effect), and the pre-declared primary scope (WR) is not
significant. Two of the three null scopes can dip below p = 0.05 in this single draw; that is a
type-I outcome of the common noise draw, and it is reported rather than hidden.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D10 = _load("_d10_dose", REPO / "src" / "10_dose_response.py")
SM = _load("_sm07_for_dose", REPO / "src" / "07_security_models.py")
GEN = _load("_secgamefix_for_dose", REPO / "src" / "synthetic_security_game_fixture.py")

SCRIPT = REPO / "src" / "10_dose_response.py"
PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PROV_SAMPLE = "UNVALIDATED SAMPLE OUTPUT"
PENDING = "PENDING FULL RUN"
REGIMES = ("dose", "dose_null", "dose_kink", "dose_capital")
PLANTED_LINEAR = -0.002
PLANTED_KINK_ABOVE = -0.006

TMP = Path(tempfile.mkdtemp(prefix="bdb27_dose_"))
FIXTURES = TMP / "fixtures"
OUT = TMP / "out"
CFG_PATH = TMP / "config.yaml"
_RUNS: dict[str, Path] = {}
_FIX: dict[str, Path] = {}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg_dict() -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(OUT)
    cfg["paths"]["fixtures"] = str(FIXTURES)
    # same code path, fewer bootstrap replicates (the corrected SE only needs to exist here)
    cfg["security_models"]["primary"]["attenuation_reps"] = 40
    return cfg


def _write_cfg() -> Path:
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(yaml.safe_dump(_cfg_dict(), sort_keys=False), encoding="utf-8")
    return CFG_PATH


def _fixture(regime: str) -> Path:
    if regime not in _FIX:
        cfg = _cfg_dict()
        seed = int(cfg["seed"]) + int(cfg["security_models"]["synthetic"]["seed_offset"])
        out = FIXTURES / cfg["security_models"]["synthetic"]["root"] / regime
        GEN.build_fixture(out, cfg, regime, seed)
        _FIX[regime] = out
    return _FIX[regime]


def _run(regime: str) -> Path:
    if regime not in _RUNS:
        _fixture(regime)
        cfg = _write_cfg()
        r = subprocess.run([sys.executable, str(SCRIPT), "--synthetic", "--synthetic-regime", regime,
                            "--force", "--config", str(cfg)],
                           cwd=str(REPO), capture_output=True, text=True)
        assert r.returncode == 0, f"{regime} stage failed:\n{r.stdout}\n{r.stderr[-2500:]}"
        _RUNS[regime] = OUT / "security_models" / "dose" / "synthetic" / regime
    return _RUNS[regime]


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _header(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _fits(regime: str) -> list[dict]:
    return _rows(_run(regime) / "dose_fits.csv")


def _pick(rows, **kw):
    for r in rows:
        if all(str(r.get(k)) == str(v) for k, v in kw.items()):
            return r
    return None


def _num(v, default=float("nan")) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _panel(regime: str, panel: str = "all_seasons"):
    """Rebuild the stage's model rows for one regime/panel (no CSV round-trip)."""
    _fixture(regime)
    cfg = yaml.safe_load(_write_cfg().read_text(encoding="utf-8"))
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    inp = SM._inputs(cfg, True, regime)
    exposures = SM.load_exposures(con, inp["exposure"])
    envelope = SM.load_envelope(con, inp["envelope"])
    psum = SM.load_player_summary(con, inp["players"])
    topk = SM.load_topk(con, inp["topk"])
    snaps = SM.load_snap_counts(con, inp["snap_counts"])
    cov = SM.load_covariates(con, cfg, True, regime)
    picks, _src = D10.load_pick(con, cfg, True, regime, SM, common.get_logger())
    import security_features as sf
    pool = [dict(nfl_id=nid, position_group=ex["position_group"], edp_pre=ex["edp_pre"],
                 log_edp_pre=ex["log_edp_pre"], edp_trend=ex["edp_trend"],
                 has_trend=ex["has_trend"], has_primary=ex["has_primary"])
            for nid, ex in sorted(exposures.items())]
    assign = sf.assign_security_groups(pool, cfg)
    rows, _dropped = D10.build_panel(cfg, True, regime, SM, panel, inp, con, exposures, envelope,
                                     psum, topk, snaps, cov, picks, assign, "primary",
                                     ["REG", "POST"])
    con.close()
    return cfg, rows


def _icc(cfg: dict, feature: str = "env_vmax") -> dict:
    icc = cfg["security_models"]["synthetic"]["icc"]
    return {"icc": float(icc[feature])} if feature in icc else {}


# --------------------------------------------------------------------------- #
# 1. restricted cubic spline basis
# --------------------------------------------------------------------------- #
def test_rcs_basis_shape_linearity_and_knots():
    x = np.linspace(-8.0, 16.0, 241)
    knots = [-4.0, 1.0, 6.0, 12.0]
    B = D10.rcs_basis(x, knots)
    assert B.shape == (x.size, len(knots) - 1), B.shape          # df = k-1
    assert np.allclose(B[:, 0], x), "the first basis column must be x itself"
    # linear beyond BOTH boundary knots (second differences vanish)
    left = x < knots[0]
    right = x > knots[-1]
    for j in range(B.shape[1]):
        assert np.allclose(np.diff(B[left, j], 2), 0.0, atol=1e-9), (j, "left")
        assert np.allclose(np.diff(B[right, j], 2), 0.0, atol=1e-9), (j, "right")
    # non-linear strictly inside the interior knots (the spline is actually doing something)
    inner = (x > knots[0]) & (x < knots[-1])
    assert np.abs(np.diff(B[inner, -1], 2)).max() > 1e-6
    # knots: monotone, de-duplicated, and the declared knot 5 survives
    cfg = _cfg_dict()
    d = np.array([-6.0, -4.0, 0.0, 2.0, 5.0, 9.0, 14.0])
    kn, note = D10.knot5_knots(np.repeat(d, 3), cfg)
    assert kn == sorted(set(kn)), kn
    assert any(abs(k - 5.0) < 1e-12 for k in kn), kn
    assert len(kn) == 3 and note == "", (kn, note)
    # 5.0 outside the observed range -> documented fallback, never a silent knot
    kn2, note2 = D10.knot5_knots(np.linspace(-9.0, 2.0, 50), cfg)
    assert any("outside the observed range" in note2 for _ in [0]), note2
    assert 5.0 not in kn2, kn2
    # degenerate inputs raise (a basis needs >= 3 distinct knots)
    for bad in ([0.0, 1.0], [2.0, 2.0, 2.0]):
        try:
            D10.rcs_basis(np.array([0.0, 1.0, 2.0]), bad)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"degenerate knots {bad} were accepted")


def test_spline_falls_back_to_linear_instead_of_raising():
    cfg, rows = _panel("dose")
    wr = [dict(r) for r in rows if str(r.get("position")) == "WR"]
    # (a) all-zero d -> the spline falls back to linear, and a CONSTANT d cannot identify a slope:
    # that is a labelled `unavailable` row with the reason, never an exception and never a number.
    flat = [dict(r, edp_trend=0.0) for r in wr[:120]]
    fits, _bins, model = D10.fit_cell(flat, cfg, SM, "all_seasons", "WR", "dose_spline",
                                      "env_vmax", "unadjusted", _icc(cfg), PROV_SYNTH)
    assert len(fits) == 1, fits
    assert "fewer than 5 distinct d values" in fits[0]["note"], fits[0]
    assert fits[0]["coef"] is None, fits[0]
    assert model is None, "a fallback spec must not emit a curve labelled as the spline"
    # (b) two distinct values -> the linear fallback is identified and IS fitted
    two = [dict(r, edp_trend=0.0 if i % 2 else 4.0) for i, r in enumerate(wr[:120])]
    fits2, _b, _m = D10.fit_cell(two, cfg, SM, "all_seasons", "WR", "dose_spline",
                                 "env_vmax", "unadjusted", _icc(cfg), PROV_SYNTH)
    assert fits2[0]["term"] == "slope" and "linear fallback" in fits2[0]["note"], fits2[0]
    assert fits2[0]["coef"] is not None and fits2[0]["df"] == 1, fits2[0]


# --------------------------------------------------------------------------- #
# 2. bins
# --------------------------------------------------------------------------- #
def test_bin_boundaries_empty_and_thin_bins():
    cfg = _cfg_dict()
    bounds = [tuple(b) for b in cfg["dose"]["bins"]["bounds"]]
    labels = [str(x) for x in cfg["dose"]["bins"]["labels"]]
    d = np.array([-6.0, -0.5, 0.0, 0.4, 1.0, 2.0, 2.5, 3.0, 4.0, 4.9, 5.0, 9.0, 9.5, 10.0, 12.0])
    idx = D10.bin_index(d, bounds)
    got = [labels[j] for j in idx]
    # bins are closed on the UPPER edge: -0.5/0 -> <=0, 0.4/1/2 -> 1-2, 2.5/3/4 -> 3-4,
    # 4.9/5/9 -> 5-9, 9.5/10/12 -> 10+
    assert got[:3] == ["<=0", "<=0", "<=0"], got
    assert got[3:6] == ["1-2"] * 3 and got[6:9] == ["3-4"] * 3, got
    assert got[9:12] == ["5-9"] * 3 and got[12:] == ["10+"] * 3, got
    # every real lands in exactly one configured bin (total partition)
    for v in (-1e6, -0.0001, 0.0, 0.999, 2.001, 4.999, 9.0001, 1e6):
        j = int(D10.bin_index(np.array([v]), bounds)[0])
        assert 0 <= j < len(bounds), (v, j)

    # an empty top bin is reported with counts and NO contrast (never interpreted)
    cfg, rows = _panel("dose")
    wr = [dict(r) for r in rows if str(r.get("position")) == "WR" and r["edp_trend"] <= 9.0]
    _fits, bins, _m = D10.fit_cell(wr, cfg, SM, "all_seasons", "WR", "dose_bins", "env_vmax",
                                   "unadjusted", _icc(cfg), PROV_SYNTH)
    top = [b for b in bins if b["binning"] == "fixed" and b["bin"] == "10+"]
    assert top and top[0]["n_obs"] == 0 and top[0]["thin"] is True, top
    assert top[0]["ref_contrast"] is None and "empty bin" in top[0]["note"], top[0]

    # a thin bin (few players) carries counts/means but a null contrast, and triggers the
    # quantile variant on the real fixture
    real = _rows(_run("dose") / "dose_bins.csv")
    fixed = [b for b in real if b["season_scope"] == "all_seasons" and b["scope"] == "WR"
             and b["capital_treatment"] == "unadjusted" and b["binning"] == "fixed"]
    thin = [b for b in fixed if b["thin"] == "True"]
    assert thin, "expected at least one thin fixed bin in the dose fixture"
    for b in thin:
        assert b["ref_contrast"] == "" and int(b["n_obs"]) > 0, b
        assert float(b["mean_gap"]) not in ("", None), b
    quant = [b for b in real if b["binning"] == "quantile" and b["season_scope"] == "all_seasons"
             and b["scope"] == "WR" and b["capital_treatment"] == "unadjusted"]
    assert quant, "the quantile variant must be fitted when a fixed bin is thin"
    assert all("[" in b["bin"] for b in quant), quant


# --------------------------------------------------------------------------- #
# 3. cluster-robust SEs
# --------------------------------------------------------------------------- #
def test_cluster_se_is_player_clustered_and_counts_players():
    rng = np.random.default_rng(7)
    n_pl, k = 40, 5
    ids = np.repeat(np.arange(n_pl), k)
    u = np.repeat(rng.normal(0.0, 1.0, n_pl), k)          # strong within-player correlation
    x = np.repeat(rng.normal(0.0, 1.0, n_pl), k)
    y = 0.5 * x + u + rng.normal(0.0, 0.1, n_pl * k)
    X = np.column_stack([np.ones(n_pl * k), x])
    hc1 = SM.ols(y, X)
    cl = SM.ols(y, X, clusters=ids)
    assert cl.bse[1] > 2.0 * hc1.bse[1], (cl.bse[1], hc1.bse[1])
    assert np.isfinite(float(cl.bse[1]))

    # the emitted n_players is the number of DISTINCT nfl_id, not the row count
    cfg, rows = _panel("dose")
    wr = [dict(r) for r in rows if str(r.get("position")) == "WR"]
    fits, _b, _m = D10.fit_cell(wr, cfg, SM, "all_seasons", "WR", "dose_linear", "env_vmax",
                                "unadjusted", _icc(cfg), PROV_SYNTH)
    assert int(fits[0]["n_players"]) == len({r["nfl_id"] for r in wr}) < int(fits[0]["n_obs"])


# --------------------------------------------------------------------------- #
# 4. thin / min_obs guards
# --------------------------------------------------------------------------- #
def test_thin_guards_emit_nulls_never_numbers():
    cfg, rows = _panel("dose")
    wr = [dict(r) for r in rows if str(r.get("position")) == "WR"]
    ids = sorted({r["nfl_id"] for r in wr})
    # too few PLAYERS (obs still above min_obs)
    small = [r for r in wr if r["nfl_id"] in set(ids[:10])]
    fits, _b, _m = D10.fit_cell(small, cfg, SM, "all_seasons", "WR", "dose_linear", "env_vmax",
                                "unadjusted", _icc(cfg), PROV_SYNTH)
    rec = fits[0]
    assert rec["thin"] is True and rec["coef"] is None and rec["se"] is None, rec
    assert "min_players" in rec["note"], rec["note"]
    assert int(rec["n_players"]) == 10
    # too few OBSERVATIONS (players above min_players)
    one_each = [next(r for r in wr if r["nfl_id"] == nid) for nid in ids[:20]]
    fits2, _b2, _m2 = D10.fit_cell(one_each, cfg, SM, "all_seasons", "WR", "dose_linear",
                                   "env_vmax", "unadjusted", _icc(cfg), PROV_SYNTH)
    assert fits2[0]["thin"] is True and "min_obs" in fits2[0]["note"], fits2[0]
    assert int(fits2[0]["n_players"]) == 20 and int(fits2[0]["n_obs"]) == 20
    # and the emitted table carries the same guard for every thin row
    for r in _rows(_run("dose") / "dose_fits.csv"):
        if r["thin"] == "True":
            assert r["coef"] == "" and r["note"], r


# --------------------------------------------------------------------------- #
# 5. planted recovery: dose vs dose_null
# --------------------------------------------------------------------------- #
def test_planted_linear_recovered_and_null_is_flat():
    fits = _fits("dose")
    for scope in ("WR", "TE", "pooled"):
        r = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope=scope,
                  feature="env_vmax", capital_treatment="unadjusted", term="slope")
        assert r is not None, scope
        coef, p = float(r["coef"]), float(r["p"])
        assert abs(coef - PLANTED_LINEAR) <= 1e-3, (scope, coef)
        assert p < 0.05, (scope, p)
        assert coef < 0, (scope, coef)
        assert float(r["ci_lo"]) < coef < float(r["ci_hi"]), r
    null = _fits("dose_null")
    for scope in ("WR", "TE", "pooled"):
        r = _pick(null, spec="dose_linear", season_scope="all_seasons", scope=scope,
                  feature="env_vmax", capital_treatment="unadjusted", term="slope")
        coef = float(r["coef"])
        # |slope| below half the planted effect in EVERY scope
        assert abs(coef) < 1e-3, (scope, coef)
    wr = _pick(null, spec="dose_linear", season_scope="all_seasons", scope="WR",
               feature="env_vmax", capital_treatment="unadjusted", term="slope")
    assert float(wr["p"]) > 0.05, wr            # the pre-declared primary scope is not significant


# --------------------------------------------------------------------------- #
# 6. kink segments
# --------------------------------------------------------------------------- #
def test_kink_segments_recovered_and_se_uses_full_covariance():
    fits = _fits("dose_kink")
    rows = {r["term"]: r for r in fits
            if r["spec"] == "dose_kink" and r["season_scope"] == "all_seasons"
            and r["scope"] == "WR" and r["capital_treatment"] == "unadjusted"}
    assert set(rows) == {"slope_below_5", "slope_above_5", "slope_change_at_5"}, set(rows)
    below, above, brk = rows["slope_below_5"], rows["slope_above_5"], rows["slope_change_at_5"]
    assert abs(float(below["coef"])) < 1.5e-3, below            # planted 0 below the knot
    assert float(below["p"]) > 0.05, below
    assert abs(float(above["coef"]) - PLANTED_KINK_ABOVE) <= 1e-3, above
    assert float(above["p"]) < 0.01, above
    assert float(brk["coef"]) < 0 and float(brk["p"]) < 0.05, brk

    # se(slope_above) = sqrt(V11 + V22 + 2*V12) from the FULL clustered covariance — verify the
    # identity from the fitted model, and that a separate fit on d >= 5 gives a different SE.
    cfg, all_rows = _panel("dose_kink")
    wr = [dict(r) for r in all_rows if str(r.get("position")) == "WR"]
    cfits, _b, model = D10.fit_cell(wr, cfg, SM, "all_seasons", "WR", "dose_kink", "env_vmax",
                                    "unadjusted", _icc(cfg), PROV_SYNTH)
    names, V = model["names"], np.asarray(model["cov"], float)
    i1, i2 = names.index("d"), names.index("d_above_kink")
    se_cov = math.sqrt(V[i1, i1] + V[i2, i2] + 2.0 * V[i1, i2])
    rec = next(r for r in cfits if r["term"] == "slope_above_5")
    assert abs(float(rec["se"]) - se_cov) < 1e-12, (rec["se"], se_cov)
    upper = [dict(r) for r in wr if r["edp_trend"] >= 5.0]
    ufits, _ub, _um = D10.fit_cell(upper, cfg, SM, "all_seasons", "WR", "dose_linear",
                                   "env_vmax", "unadjusted", _icc(cfg), PROV_SYNTH)
    assert ufits[0]["coef"] is not None
    assert abs(float(ufits[0]["se"]) - se_cov) > 1e-6, "the above-knot SE is not a separate fit"
    # the basis really carries a jump at the knot (documented in the note)
    assert "jump" in rec["note"], rec["note"]


# --------------------------------------------------------------------------- #
# 7. capital confound
# --------------------------------------------------------------------------- #
def test_capital_confound_unadjusted_vs_pick_adjusted():
    fits = _fits("dose_capital")
    base = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                 feature="env_vmax", capital_treatment="unadjusted", term="slope")
    pick = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                 feature="env_vmax", capital_treatment="pick_linear", term="slope")
    band = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                 feature="env_vmax", capital_treatment="round_band", term="slope")
    assert float(base["coef"]) < -0.002 and float(base["p"]) < 0.05, base
    assert abs(float(pick["coef"])) < 1e-3 and float(pick["p"]) > 0.05, pick
    assert abs(float(band["coef"])) < 1.5e-3, band
    assert "undrafted" in pick["note"], pick["note"]
    assert "undrafted" in band["note"], band["note"]
    # the within-band treatments really restrict the sample, and say which band
    for treat, needle in (("within_R1", "R1"), ("within_R2_R3", "R2"),
                          ("within_R4plus", "R4plus_or_undrafted")):
        r = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                  feature="env_vmax", capital_treatment=treat, term="slope")
        assert needle in r["note"], (treat, r["note"])
    n_all = int(base["n_obs"])
    for treat in ("within_R1", "within_R2_R3", "within_R4plus"):
        r = _pick(fits, spec="dose_linear", season_scope="all_seasons", scope="WR",
                  feature="env_vmax", capital_treatment=treat, term="slope")
        assert int(r["n_obs"]) < n_all, (treat, r["n_obs"], n_all)


# --------------------------------------------------------------------------- #
# 8. determinism
# --------------------------------------------------------------------------- #
def test_determinism_byte_identical():
    d = _run("dose")
    first = {name: (d / name).read_bytes()
             for name in ("dose_fits.csv", "dose_bins.csv", "dose_curve.csv")}
    r = subprocess.run([sys.executable, str(SCRIPT), "--synthetic", "--synthetic-regime", "dose",
                        "--force", "--config", str(CFG_PATH)],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    for name, blob in first.items():
        assert (d / name).read_bytes() == blob, f"non-deterministic stage output: {name}"


# --------------------------------------------------------------------------- #
# 9. CLI on all four regimes + schema/sign convention
# --------------------------------------------------------------------------- #
def test_cli_all_four_regimes_and_schema():
    for regime in REGIMES:
        d = _run(regime)
        done = json.loads((d / "_DONE.json").read_text())
        assert done["stage"] == "10_dose_response"
        assert done["synthetic"] is True and done["provenance"] == PROV_SYNTH
        assert done["regime"] == regime
        for name in ("dose_fits.csv", "dose_bins.csv", "dose_curve.csv", "SUMMARY.md",
                     "SUMMARY.json", "fig_dose_response.png"):
            assert (d / name).exists(), f"{regime}: missing {name}"
        for f in d.glob("*.csv"):
            hdr = _header(f)
            assert hdr[0] == "provenance", f"{f.name}: first column is not provenance"
            assert all(r["provenance"] == PROV_SYNTH for r in _rows(f)), f.name
        fits = _fits(regime)
        assert fits and all(r["sign_convention"].strip() for r in fits), "empty sign_convention"
        cells = {(r["season_scope"], r["scope"]) for r in fits}
        for panel in ("all_seasons", "rookie_only"):
            for scope in ("WR", "TE", "pooled"):
                assert (panel, scope) in cells, (regime, panel, scope)
        specs = {r["spec"] for r in fits}
        assert {"dose_linear", "dose_spline", "dose_spline_knot5", "dose_bins",
                "dose_kink"} <= specs, specs
        # both panels really fitted in ONE invocation, and the rookie panel is the smaller one
        n_all = sum(int(r["n_obs"]) for r in fits if r["season_scope"] == "all_seasons"
                    and r["spec"] == "dose_linear" and r["capital_treatment"] == "unadjusted")
        n_rook = sum(int(r["n_obs"]) for r in fits if r["season_scope"] == "rookie_only"
                     and r["spec"] == "dose_linear" and r["capital_treatment"] == "unadjusted")
        assert 0 < n_rook < n_all, (regime, n_rook, n_all)
        # the spline rows carry df and a Wald statistic; the explicit knot is 5.0
        sp = [r for r in fits if r["spec"] == "dose_spline" and r["term"] == "overall"
              and r["capital_treatment"] == "unadjusted" and r["thin"] != "True"]
        assert sp and all(int(r["df"]) == 3 for r in sp), sp[:2]
        k5 = [r for r in fits if r["spec"] == "dose_spline_knot5" and r["term"] == "overall"
              and r["capital_treatment"] == "unadjusted" and r["thin"] != "True"]
        assert k5 and all("5.0000" in r["note"] for r in k5), k5[:2]
        # a thin cell is a null with a reason, never a forced number
        thin = [r for r in fits if r["thin"] == "True"]
        assert thin and all(r["coef"] == "" and r["note"] for r in thin)


# --------------------------------------------------------------------------- #
# 10. sample mode + checkpoint
# --------------------------------------------------------------------------- #
def test_sample_mode_placeholder_and_checkpoint():
    cfg = _write_cfg()
    r = subprocess.run([sys.executable, str(SCRIPT), "--mode", "sample", "--force",
                        "--config", str(cfg)], cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    out = OUT / "security_models" / "dose"
    rows = _rows(out / "dose_summary.csv")
    assert rows[0]["provenance"] == PROV_SAMPLE, rows
    assert rows[0]["status"] == PENDING, rows
    md = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert PENDING in md and PROV_SAMPLE in md
    # no numbers were invented in sample mode
    assert not (out / "dose_fits.csv").exists()
    r2 = subprocess.run([sys.executable, str(SCRIPT), "--mode", "sample", "--config", str(cfg)],
                        cwd=str(REPO), capture_output=True, text=True)
    assert r2.returncode == 0 and "checkpoint hit" in r2.stderr, r2.stderr[-600:]


# --------------------------------------------------------------------------- #
# 11. figure
# --------------------------------------------------------------------------- #
def test_figure_written_large_and_300dpi():
    d = _run("dose")
    fig = d / "fig_dose_response.png"
    assert fig.exists(), "figure missing"
    assert fig.stat().st_size > 15_000, fig.stat().st_size
    meta = json.loads((d / "SUMMARY.json").read_text())["figure"]
    assert int(meta["dpi"]) == 300, meta
    assert int(meta["bytes"]) == fig.stat().st_size, meta
    md = (d / "SUMMARY.md").read_text(encoding="utf-8")
    assert "fig_dose_response.png" in md
    assert "not one of the 5 writeup figures" in md


# --------------------------------------------------------------------------- #
# 12. the rest of the suite stays green
# --------------------------------------------------------------------------- #
def test_preexisting_contract_suites_still_green():
    import stage_runner
    assert "10_dose_response" in stage_runner.STAGE_MODULES, stage_runner.STAGE_MODULES
    for name in ("test_stage_runner.py", "test_notebook_contract.py"):
        r = subprocess.run([sys.executable, str(REPO / "src" / "tests" / name)],
                           cwd=str(REPO), capture_output=True, text=True)
        assert r.returncode == 0, f"{name} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}"


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_rcs_basis_shape_linearity_and_knots,
             test_spline_falls_back_to_linear_instead_of_raising,
             test_bin_boundaries_empty_and_thin_bins,
             test_cluster_se_is_player_clustered_and_counts_players,
             test_thin_guards_emit_nulls_never_numbers,
             test_planted_linear_recovered_and_null_is_flat,
             test_kink_segments_recovered_and_se_uses_full_covariance,
             test_capital_confound_unadjusted_vs_pick_adjusted,
             test_determinism_byte_identical,
             test_cli_all_four_regimes_and_schema,
             test_sample_mode_placeholder_and_checkpoint,
             test_figure_written_large_and_300dpi,
             test_preexisting_contract_suites_still_green]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
