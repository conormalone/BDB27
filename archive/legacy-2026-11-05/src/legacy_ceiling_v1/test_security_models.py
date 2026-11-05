#!/usr/bin/env python3
"""Behaviour tests for Phase 5 B–G — the security / incentive-exposure models.

Runnable **without pytest** (``python src/tests/test_security_models.py``) and collected by pytest
(functions named ``test_*``). Covers ``src/07_security_models.py``, the shared pre-Combine sort key in
``src/security_features.py`` and the committed game-side fixture
(``src/synthetic_security_game_fixture.py``):

  * **schema / units** — every input reader fails loudly on a missing column; the emitted tables have
    the exact required column order; log speeds are finite; shares are in [0, 1];
  * **sort key** — D40 asymmetric threshold (``edp_trend >= 3.0`` ⇒ moved_down; stable *and* rising
    players are high_secure), within-position tertiles, the middle tertile excluded from the
    two-group contrast, cutoffs/counts reported, and the declared T=5 sensitivity;
  * **pre-Combine only** — mutating ``edp_post`` / ``edp_change`` leaves the assignment byte-identical,
    and the assignment source contains no post-Combine field;
  * **synthetic recovery** — the planted positive group effect is recovered with a CI excluding 0; the
    continuous dose-response is monotone;
  * **null** — no significant group effect, and unstable estimates are written as **null**, never forced;
  * **selection** — the unweighted estimate is biased and IPW moves back toward the planted target;
    the appearance model reports coefficients/ORs;
  * **edge cases** — no 2-week snapshot (level fallback), one effort, <100 route snaps, no game data,
    missing size covariates;
  * **determinism** — the same seed gives byte-identical fixtures and outputs;
  * **sample mode** — a labelled ``PENDING FULL RUN`` placeholder and checkpoint skip;
  * the CLI runs on **all three** synthetic regimes and exits 0.
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
import security_features as sf  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SM = _load("_sm07", REPO / "src" / "07_security_models.py")
GEN = _load("_secgamefix", REPO / "src" / "synthetic_security_game_fixture.py")

SCRIPT = REPO / "src" / "07_security_models.py"
PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PROV_SAMPLE = "UNVALIDATED SAMPLE OUTPUT"
PENDING = "PENDING FULL RUN"
GROUPS = ("moved_down", "high_secure")

TMP = Path(tempfile.mkdtemp(prefix="bdb27_sm_"))
FIXTURES = TMP / "fixtures"
OUT = TMP / "out"
CFG_PATH = TMP / "config.yaml"
REGIMES = ("planted", "null", "selection")
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
    # fast but identical code path: fewer bootstrap replicates only
    cfg["security_models"]["primary"]["attenuation_reps"] = 40
    cfg["security_models"]["g"]["bootstrap_reps"] = 15
    return cfg


def _write_cfg() -> Path:
    cfg = _cfg_dict()
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
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
    """Run the CLI once per regime (cached) with the test config; return the output dir."""
    if regime not in _RUNS:
        # self-contained: never rely on another test having built this regime's fixture first
        _fixture(regime)
        cfg = _write_cfg()
        r = subprocess.run([sys.executable, str(SCRIPT), "--synthetic", "--synthetic-regime", regime,
                            "--force", "--config", str(cfg)],
                           cwd=str(REPO), capture_output=True, text=True)
        assert r.returncode == 0, f"{regime} stage failed:\n{r.stdout}\n{r.stderr[-2500:]}"
        _RUNS[regime] = OUT / "security_models" / "synthetic" / regime
    return _RUNS[regime]


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _header(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _meta(regime: str) -> dict:
    return json.loads((_fixture(regime) / "meta.json").read_text(encoding="utf-8"))


def _num(v, default=float("nan")) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pool_row(nid, pos, log_edp, trend=None, has_trend=False, has_primary=True):
    return dict(nfl_id=nid, position_group=pos, edp_pre=math.exp(log_edp), log_edp_pre=log_edp,
                edp_trend=trend, has_trend=has_trend, has_primary=has_primary)


# --------------------------------------------------------------------------- #
# 1. schema / units
# --------------------------------------------------------------------------- #
def test_fixture_schema_and_units():
    cfg = _cfg_dict()
    required = ["efforts.csv", "top_k_efforts.csv", "player_summary.csv",
                "thresholds_applied.csv", "security_exposure.csv", "per_player_envelope.csv",
                "attempt_features.csv", "covariates.csv", "selection_pool.csv",
                "feature_reliability.csv", "meta.json"]
    for regime in REGIMES:
        d = _fixture(regime)
        for f in required:
            assert (d / f).exists(), f"{regime}: missing {f}"
        meta = _meta(regime)
        assert meta["provenance"] == PROV_SYNTH
        assert meta["regime"] == regime
        assert "delta" in meta["ground_truth"]
        # stated population floors (planted/null/selection share one population)
        assert meta["design"]["n_wr"] >= 120 and meta["design"]["n_te"] >= 40
        assert meta["design"]["k_top"] == 5 and meta["design"]["k_bottom"] == 5
        # shares in [0,1]; log speeds finite
        for r in _rows(d / "security_exposure.csv"):
            if r["consensus_share"] not in ("", None):
                assert 0.0 <= float(r["consensus_share"]) <= 1.0
            if r["log_edp_pre"] not in ("", None):
                assert abs(float(r["log_edp_pre"]) - math.log(float(r["edp_pre"]))) < 1e-5
            for r in _rows(d / "top_k_efforts.csv"):
                assert math.isfinite(float(r["peak_speed"])) and float(r["peak_speed"]) > 0
            # both effort strata present (top-k carries the planted effect; bottom-k is the placebo)
            eff = _rows(d / "efforts.csv")
            # D49: top-k is emitted once per scope; compare against the primary-scope block
            topk_primary = [r for r in _rows(d / "top_k_efforts.csv")
                            if r.get("season_scope") == "primary"]
            assert len(eff) > len(topk_primary)


def test_input_schema_guard_fails_loudly():
    cfg = _cfg_dict()
    con = common.connect(1)
    d = _fixture("planted")
    # the good file passes
    SM.assert_input_columns(con, d / "top_k_efforts.csv", SM.TOPK_COLS)
    # a file missing a required column fails loudly (not silently)
    bad = TMP / "bad_topk.csv"
    cols = [c for c in _header(d / "top_k_efforts.csv") if c != "peak_speed"]
    rows = [{c: r[c] for c in cols} for r in _rows(d / "top_k_efforts.csv")]
    common.write_csv(bad, rows, fieldnames=cols)
    try:
        SM.assert_input_columns(con, bad, SM.TOPK_COLS)
    except AssertionError as exc:
        assert "peak_speed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing column was accepted")
    # the assignment path refuses a post-Combine field outright
    try:
        sf.assert_pre_combine_only(["nfl_id", "edp_post", "edp_change"])
    except AssertionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("post-Combine fields accepted by the assignment guard")


def test_output_tables_schema():
    d = _run("planted")
    expect = {
        "group_assignment.csv": ["provenance", "season_scope", "panel", "nfl_id",
                                 "position_group",
                                 "edp_pre", "log_edp_pre",
                                 "edp_trend", "has_trend", "stratum", "group", "basis",
                                 "block_status"],
        "b_primary.csv": ["provenance", "season_scope", "panel", "spec", "scope", "n_obs",
                          "n_players",
                          "n_players_moved_down", "n_players_high_secure", "thin",
                          "group_coef", "group_se", "group_p", "group_ci_lo", "group_ci_hi", "speed_coef",
                          "speed_se", "speed_p", "speed_coef_corrected", "group_coef_corrected",
                          "group_se_corrected", "group_p_corrected", "reliability_icc",
                          "reliability_source", "r2", "method", "note", "dropped_controls",
                          "block_status"],
    }
    for name, cols in expect.items():
        assert _header(d / name) == cols, (name, _header(d / name))
    for f in d.glob("*.csv"):
        assert _header(f)[0] == "provenance", f"{f.name}: first column is not provenance"
        assert _header(f)[-1] == "block_status", f"{f.name}: last column is not block_status"
        rows_f = _rows(f)
        assert all(r["provenance"] == PROV_SYNTH for r in rows_f), f.name
        assert all(r["block_status"] == "OK" for r in rows_f), f.name
    for f in ("SUMMARY.md", "SUMMARY.json", "_DONE.json"):
        assert (d / f).exists(), f


# --------------------------------------------------------------------------- #
# 2. sort key
# --------------------------------------------------------------------------- #
def test_group_assignment_trend_sign_and_level_tertiles():
    cfg = _cfg_dict()
    thr = float(cfg["security_models"]["sort"]["fall_threshold"])
    assert thr == 3.0, "the D40 primary rule is a fall of 3+ EDP spots"
    pool = [
        _pool_row(1, "WR", 3.0, trend=+4.0, has_trend=True),     # fell 4 -> moved_down
        _pool_row(2, "WR", 3.0, trend=+2.9, has_trend=True),     # fell 2.9 -> NOT 3+, secure
        _pool_row(3, "WR", 3.0, trend=thr, has_trend=True),      # exactly 3.0 -> moved_down (>=)
        _pool_row(4, "WR", 3.0, trend=0.0, has_trend=True),      # stable -> high_secure
        _pool_row(5, "WR", 3.0, trend=-5.0, has_trend=True),     # rising -> high_secure
        # level-fallback WR block: log EDP 1..6 -> tertiles 2.667 / 4.333
        _pool_row(10, "WR", 1.0), _pool_row(11, "WR", 2.0), _pool_row(12, "WR", 3.0),
        _pool_row(13, "WR", 4.0), _pool_row(14, "WR", 5.0), _pool_row(15, "WR", 6.0),
        # a TE block with a different level so within-position tertiles matter
        _pool_row(20, "TE", 2.0), _pool_row(21, "TE", 3.0), _pool_row(22, "TE", 4.0),
    ]
    res = sf.assign_security_groups(pool, cfg)
    g = {a["nfl_id"]: a for a in res["assignments"]}
    assert g[1]["group"] == "moved_down" and g[1]["stratum"] == "trend"
    assert g[2]["group"] == "high_secure" and g[2]["stratum"] == "trend"
    assert g[3]["group"] == "moved_down", "edp_trend == 3.0 IS moved_down (D40 uses >=)"
    assert g[4]["group"] == "high_secure", "a stable player is high_secure (not falling 3+)"
    assert g[5]["group"] == "high_secure", "a riser is high_secure (not falling 3+)"
    # within-position tertiles
    assert g[10]["group"] == "high_secure" and g[11]["group"] == "high_secure"
    assert g[12]["group"] == "unclassified" and g[13]["group"] == "unclassified"
    assert g[14]["group"] == "moved_down" and g[15]["group"] == "moved_down"
    assert g[12]["stratum"] == "level_fallback" and g[12]["basis"] == "level_tertile_mid"
    # TE tertiles are computed inside TE, so the middle TE value is unclassified
    assert g[21]["group"] == "unclassified", g[21]
    assert res["cutoffs"]["WR"]["q_lo"] < res["cutoffs"]["WR"]["q_hi"]
    assert res["counts"]["trend"]["moved_down"] == 2
    assert res["counts"]["trend"]["high_secure"] == 3
    assert res["counts"]["level_fallback"]["unclassified"] == 3
    # the middle tertile is excluded from the two-group contrast but the player is retained
    assert g[12]["group"] not in GROUPS


def test_assignment_reports_cutoffs_and_threshold_grid():
    cfg = _cfg_dict()
    meta = _meta("planted")
    gt = meta["ground_truth"]
    assert gt["cutoffs"]["WR"]["n"] > 0 and gt["cutoffs"]["TE"]["n"] > 0
    assert "trend" in gt["counts"] and "level_fallback" in gt["counts"]
    assert gt["fall_threshold"] == 3.0 and gt["sensitivity_threshold"] == 5.0
    grid = sf.group_counts_by_threshold(
        [_pool_row(int(k), "WR", 3.0, trend=float(v), has_trend=True)
         for k, v in [("1", 0.2), ("2", 4.0), ("3", 6.0)]], cfg)
    assert [r["threshold"] for r in grid] == [3.0, 5.0]
    assert sum(1 for r in grid if r["is_primary_threshold"]) == 1
    assert grid[0]["trend:moved_down"] == 2      # 4.0 and 6.0 are both >= 3
    assert grid[1]["trend:moved_down"] == 1      # only 6.0 is >= 5


def test_threshold_counts_match_board_rule():
    """A trend-only WR fixture built to the D40 rule must give exactly 31 moved_down / 52 secure."""
    cfg = _cfg_dict()
    thr = float(cfg["security_models"]["sort"]["fall_threshold"])
    pool: list[dict] = []
    nid = 1000
    for i in range(31):                       # 31 fallen: first is exactly at the threshold
        pool.append(_pool_row(nid, "WR", 3.5, trend=thr + float(i % 4), has_trend=True))
        nid += 1
    for i in range(52):                       # 52 stable/rising, all strictly below 3.0
        pool.append(_pool_row(nid, "WR", 3.5, trend=0.5 + 0.05 * float(i % 48), has_trend=True))
        nid += 1
    res = sf.assign_security_groups(pool, cfg)
    assert res["by_position"]["WR"]["moved_down"] == 31
    assert res["by_position"]["WR"]["high_secure"] == 52
    assert res["counts"]["trend"] == {"moved_down": 31, "high_secure": 52}


def test_threshold_T5_sensitivity_panel_uses_5():
    """The declared sensitivity re-runs the rule at +5; the emitted panel carries that spec."""
    cfg = _cfg_dict()
    sort = cfg["security_models"]["sort"]
    assert float(sort["fall_threshold"]) == 3.0
    assert float(sort["sensitivity_threshold"]) == 5.0
    pool = [_pool_row(1, "WR", 3.0, trend=4.0, has_trend=True),
            _pool_row(2, "WR", 3.0, trend=6.0, has_trend=True),
            _pool_row(3, "WR", 3.0, trend=2.0, has_trend=True)]
    a3 = {a["nfl_id"]: a for a in sf.assign_security_groups(pool, cfg)["assignments"]}
    a5 = {a["nfl_id"]: a for a in sf.assign_security_groups(
        pool, cfg, threshold=sort["sensitivity_threshold"])["assignments"]}
    assert a3[1]["group"] == "moved_down" and a5[1]["group"] == "high_secure"
    assert a3[2]["group"] == "moved_down" and a5[2]["group"] == "moved_down"
    assert a3[3]["group"] == "high_secure" and a5[3]["group"] == "high_secure"
    # the emitted stage panel exists for every scope and encodes the T=5 rule
    rows = [r for r in _rows(_run("planted") / "b_primary.csv")
            if r["spec"] == "panel_threshold_T5"]
    assert {r["scope"] for r in rows} == {"WR", "TE", "pooled"}, rows
    assert all("5" in r["note"] for r in rows), rows
    primary = {r["scope"]: r for r in _rows(_run("planted") / "b_primary.csv")
               if r["spec"] == "primary_env_vmax"}
    for r in rows:
        assert int(r["n_players_moved_down"]) <= int(primary[r["scope"]]["n_players_moved_down"])
    assert int(next(r for r in rows if r["scope"] == "WR")["n_players_moved_down"]) < \
        int(primary["WR"]["n_players_moved_down"])


# --------------------------------------------------------------------------- #
# 3. pre-Combinate-only guard (behavioural + static)
# --------------------------------------------------------------------------- #
def test_post_combine_mutation_invariance():
    """The loader never selects post-Combine columns, so mutating them cannot move a group.

    Two independent guarantees are exercised: (1) the DuckDB reader selects an explicit pre-Combine
    column list, so a table carrying ``edp_post``/``edp_change`` (mutated to arbitrary values) yields a
    **byte-identical** assignment; (2) if such a column is injected *directly* into the assignment
    input, the runtime assertion refuses it rather than silently ignoring it.
    """
    cfg = _cfg_dict()
    src = _fixture("planted") / "security_exposure.csv"
    posts = [10.0, -5.0, 999.0, 1234.5, -0.5]

    def _assign_from(path: Path) -> str:
        con = common.connect(1)
        try:
            exposures = SM.load_exposures(con, path)
        finally:
            con.close()
        pool = [dict(nfl_id=nid, position_group=e["position_group"], edp_pre=e["edp_pre"],
                     log_edp_pre=e["log_edp_pre"], edp_trend=e["edp_trend"],
                     has_trend=e["has_trend"], has_primary=e["has_primary"])
                for nid, e in sorted(exposures.items())]
        return json.dumps(sf.assign_security_groups(pool, cfg)["assignments"], sort_keys=True)

    clean = _assign_from(src)
    base_rows = _rows(src)
    base_cols = _header(src)
    for j, val in enumerate(posts):
        mutated = []
        for i, r in enumerate(base_rows):
            d = dict(r)
            d["edp_post"] = val + i
            d["edp_change"] = -val - i
            mutated.append(d)
        p = TMP / f"mut_{j}.csv"
        common.write_csv(p, mutated, fieldnames=base_cols + ["edp_post", "edp_change"])
        assert _assign_from(p) == clean, f"assignment changed for mutation {j}"
    # direct injection is refused outright
    pool = _rows(_fixture("planted") / "selection_pool.csv")
    pool[0]["edp_post"] = 12.0
    try:
        sf.assign_security_groups(pool, cfg)
    except AssertionError as exc:
        assert "post-Combine" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("post-Combine field was accepted by the assignment")
    # the stage's exposure column list contains no post-Combine field
    sf.assert_pre_combine_only(SM.EXPOSURE_COLS)


def test_assignment_source_has_no_post_combine_reads():
    import inspect
    src = inspect.getsource(sf.assign_security_groups) + inspect.getsource(sf._pre_only)
    for token in ("edp_post", "edp_change", "post_edp"):
        assert token not in src, f"assignment source mentions {token}"


# --------------------------------------------------------------------------- #
# 4. synthetic recovery + dose-response
# --------------------------------------------------------------------------- #
def test_planted_effect_recovered():
    d = _run("planted")
    rows = [r for r in _rows(d / "b_primary.csv")
            if r["spec"] == "primary_env_vmax" and r["scope"] in ("WR", "TE", "pooled")]
    assert len(rows) == 3, rows
    for r in rows:
        coef, p = float(r["group_coef"]), float(r["group_p"])
        lo, hi = float(r["group_ci_lo"]), float(r["group_ci_hi"])
        assert coef > 0, f"{r['scope']}: expected a POSITIVE group coefficient, got {coef}"
        assert p < 0.05, r
        assert lo > 0, f"{r['scope']}: CI includes 0 ({lo}, {hi})"
    # corrected and naive agree in sign and magnitude order
    for r in rows:
        if r["group_coef_corrected"] not in ("", None):
            assert float(r["group_coef_corrected"]) > 0


def test_dose_response_monotone():
    d = _run("planted")
    c = [r for r in _rows(d / "c_end.csv") if r["scope"] == "WR" and r["basis"] == "mean_ceiling"]
    gaps = {float(r["percentile"]): float(r["fitted_gap"]) for r in c}
    assert set(gaps) >= {0.1, 0.5, 0.9}, gaps
    assert gaps[0.1] < gaps[0.5] < gaps[0.9], gaps   # at-risk (high EDP) => smaller gap
    # exceedance is reported overall FIRST, then by group
    exc = [r for r in _rows(d / "c_exceedance.csv") if r["scope"] == "WR"]
    assert exc and exc[0]["group"] == "overall", exc[:2]
    assert all(0.0 <= float(r["exceedance_rate"]) <= 1.0 for r in exc)
    assert all(float(r["wilson_lo"]) <= float(r["exceedance_rate"]) <= float(r["wilson_hi"])
               for r in exc)


# --------------------------------------------------------------------------- #
# 5. null regime
# --------------------------------------------------------------------------- #
def test_null_not_recovered_and_nulls_written_as_null():
    d = _run("null")
    wr = next(r for r in _rows(d / "b_primary.csv")
              if r["spec"] == "primary_env_vmax" and r["scope"] == "WR")
    assert abs(float(wr["group_coef"])) < 0.02, wr
    assert float(wr["group_p"]) > 0.05, wr
    lo, hi = float(wr["group_ci_lo"]), float(wr["group_ci_hi"])
    assert lo < 0 < hi, (lo, hi)
    # unstable / failed estimates must be empty cells, never forced numbers
    for r in _rows(d / "g_tail.csv"):
        if r["action"] == "report_null":
            assert r["reported"] in ("", None), r
        if r["unstable"] == "True":
            assert r["reported"] in ("", None), r
    # The bottom-k placebo is also null in the null regime. A fixed fixture can throw a single
    # type-I error at the 5% level, so require a small coefficient and not-significant at 1%.
    bottom = next(r for r in _rows(d / "falsification.csv") if r["stratum"] == "bottom_k")
    assert abs(float(bottom["coef"])) < 0.02, bottom
    assert float(bottom["p"]) > 0.01, bottom


# --------------------------------------------------------------------------- #
# 6. selection + IPW
# --------------------------------------------------------------------------- #
def test_selection_ipw_moves_toward_planted():
    d = _run("selection")
    target = float(_meta("selection")["ground_truth"]["target_group_coef"])
    rows = _rows(d / "e_ipw.csv")
    unw = next(r for r in rows if r["weighting"] == "unweighted")
    cfg = _cfg_dict()
    trim = cfg["security_models"]["e"]["weight_trim"]
    ipw = next(r for r in rows if r["weighting"] == "ipw"
               and abs(float(r["trim_lo"]) - float(trim[0])) < 1e-12
               and abs(float(r["trim_hi"]) - float(trim[1])) < 1e-12)
    du = abs(float(unw["group_coef"]) - target)
    di = abs(float(ipw["group_coef"]) - target)
    assert du > 0.01, f"unweighted estimate is not meaningfully biased (|d|={du})"
    assert di < du, f"IPW did not move toward the planted target ({di} vs {du})"
    # the selection model reports coefficients + odds ratios
    sel = _rows(d / "e_selection.csv")
    terms = {r["term"] for r in sel}
    assert "z_log_edp_pre" in terms and "z_log_combine_speed" in terms, terms
    edp = next(r for r in sel if r["term"] == "z_log_edp_pre")
    assert float(edp["coef"]) < 0, "at-risk players should be LESS likely to appear"
    assert float(edp["p"]) < 0.05
    assert float(edp["odds_ratio"]) > 0
    # truncation grid present and ordered
    grid = [r for r in rows if r["weighting"] == "ipw"]
    assert len(grid) >= len(cfg["security_models"]["e"]["weight_grid"])


# --------------------------------------------------------------------------- #
# 6b. separation guard (D43) — a separating predictor must never crash the stage
# --------------------------------------------------------------------------- #
def _separating_pool(n: int = 80) -> list[dict]:
    """`appeared` is perfectly predicted by log_edp_pre (no overlap) -> logit separation."""
    pool = []
    for i in range(n):
        appeared = (i % 2 == 0)
        log_edp = (3.0 + i * 1e-3) if appeared else (1.0 + i * 1e-3)
        pool.append(dict(nfl_id=i, position_group="WR", position="WR", edp_pre=math.exp(log_edp),
                         log_edp_pre=log_edp, edp_trend=None, has_trend=False, has_primary=True,
                         participation_pattern="full", height=72.0, weight=200.0 + i,
                         log_combine_speed=4.4, draft_year=2024, appeared=appeared))
    return pool


def test_separation_reports_null_e_not_crash():
    cfg = _cfg_dict()
    sel, ipw, meta = SM.run_e(_separating_pool(), [], cfg)   # must not raise OverflowError
    assert sel and sel[0]["term"] == "unavailable", sel
    assert sel[0]["odds_ratio"] in (None, ""), sel[0]
    assert "reported null" in sel[0]["note"], sel[0]
    assert any(tok in sel[0]["note"].lower() for tok in ("separation", "did not converge",
                                                          "logit failed")), sel[0]
    assert ipw and ipw[0]["weighting"] == "unavailable", ipw
    assert meta["n_pool"] == 80 and meta["n_appeared"] == 40, meta


def test_safe_odds_ratio_clips_instead_of_overflowing():
    clip = float(_cfg_dict()["security_models"]["e"]["or_exponent_clip"])
    assert SM._safe_exp(1.0, clip) == math.e
    assert SM._safe_exp(clip + 50.0, clip) == float("inf")      # would be OverflowError
    assert SM._safe_exp(-(clip + 50.0), clip) == 0.0
    val, note = SM._odds_ratio(clip + 50.0, clip)
    assert val == float("inf") and "1e300" in note and "clipped" in note, (val, note)
    lo_val, _ = SM._odds_ratio(-(clip + 50.0), clip)
    assert lo_val == 0.0


def test_e_failure_is_non_fatal_and_b_primary_still_written():
    """An unexpected E blow-up becomes a reported null; B/C/D/F/G still write (D43)."""
    cfg = _cfg_dict()
    cfg["security_models"]["primary"]["attenuation_reps"] = 10
    cfg["security_models"]["g"]["bootstrap_reps"] = 5
    _fixture("planted")
    out = TMP / "out_fatal_e"
    log = common.get_logger()

    def _boom(*_a, **_k):
        raise OverflowError("math range error (simulated)")

    orig = SM.run_e
    SM.run_e = _boom
    try:
        SM.run(cfg, "sample", True, "planted", out, PROV_SYNTH, log)
    finally:
        SM.run_e = orig
    assert (out / "b_primary.csv").exists(), "b_primary.csv was not emitted"
    for name in ("c_end.csv", "d_skippers.csv", "falsification.csv", "g_tail.csv"):
        assert (out / name).exists(), f"{name} was not emitted"
    sel = _rows(out / "e_selection.csv")
    assert sel[0]["term"] == "unavailable", sel
    assert "OverflowError" in sel[0]["reason"] and "simulated" in sel[0]["reason"], sel
    assert sel[0]["block_status"] == "FAILED", sel
    # the failure is loud: summary JSON + SUMMARY.md both list the block and the reason
    s = json.loads((out / "SUMMARY.json").read_text(encoding="utf-8"))
    assert "E" in s["failed_blocks"] and "OverflowError" in s["failed_blocks"]["E"], s["failed_blocks"]
    md = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "FAILED BLOCKS" in md and "**E: FAILED**" in md, md[-1200:]


# --------------------------------------------------------------------------- #
# 7. edge cases
# --------------------------------------------------------------------------- #
def test_edge_cases_present_and_handled():
    d = _run("planted")
    meta = _meta("planted")
    edges = meta["ground_truth"]["edge_players"]
    flags = {frozenset(v) for v in edges.values()}
    assert any("no_trend" in f for f in flags)
    assert any("one_effort" in f for f in flags)
    assert any("low_snap" in f for f in flags)
    assert any("no_game" in f for f in flags)
    assert any("missing_cov" in f for f in flags)
    assert any("no_primary" in f for f in flags)
    # no 2-week snapshot -> level fallback (or none when there is no 1-week snapshot either)
    assign = {int(r["nfl_id"]): r for r in _rows(d / "group_assignment.csv")}
    no_trend = [int(k) for k, v in edges.items() if "no_trend" in v]
    for nid in no_trend:
        assert assign[nid]["stratum"] == "level_fallback", assign[nid]
    no_primary = [int(k) for k, v in edges.items() if "no_primary" in v]
    for nid in no_primary:
        assert assign[nid]["group"] == "unclassified" and assign[nid]["basis"] == "no_pre_combine"
    # one effort -> exactly one top-k row
    one = [int(k) for k, v in edges.items() if "one_effort" in v]
    topk = [r for r in _rows(_fixture("planted") / "top_k_efforts.csv")
            if r.get("season_scope") == "primary"]
    for nid in one:
        assert sum(1 for r in topk if int(r["nfl_id"]) == nid) == 1
    # <100 route snaps reported (not dropped)
    ps = {int(r["nfl_id"]): r for r in _rows(_fixture("planted") / "player_summary.csv")}
    low = [int(k) for k, v in edges.items() if "low_snap" in v]
    assert any(int(ps[n]["n_route_snaps"]) < 100 for n in low if n in ps)
    # no game data -> absent from player_summary but present in the assignment
    nog = [int(k) for k, v in edges.items() if "no_game" in v]
    assert all(n not in ps for n in nog)
    assert all(n in assign for n in nog)
    # missing covariates counted, not invented
    cov = {int(r["nfl_id"]): r for r in _rows(_fixture("planted") / "covariates.csv")}
    miss = [int(k) for k, v in edges.items() if "missing_cov" in v]
    assert all(cov[n]["height"] in ("", None) for n in miss)
    summ = json.loads((d / "SUMMARY.json").read_text())
    assert summ["n_missing_size_covariate_players"] >= len(miss)
    # the stage still produced estimates
    assert any(r["group_coef"] not in ("", None) for r in _rows(d / "b_primary.csv"))


# --------------------------------------------------------------------------- #
# 8. determinism
# --------------------------------------------------------------------------- #
def test_determinism_same_seed_identical():
    cfg = _cfg_dict()
    seed = int(cfg["seed"]) + int(cfg["security_models"]["synthetic"]["seed_offset"])
    a = TMP / "det_a"
    b = TMP / "det_b"
    GEN.build_fixture(a, cfg, "planted", seed)
    GEN.build_fixture(b, cfg, "planted", seed)
    for f in sorted(p.name for p in a.iterdir()):
        assert (a / f).read_bytes() == (b / f).read_bytes(), f"non-deterministic fixture file: {f}"
    # the stage output is deterministic too (same config, --force twice)
    d = _run("planted")
    first = {f.name: f.read_bytes() for f in d.glob("*.csv")}
    r = subprocess.run([sys.executable, str(SCRIPT), "--synthetic", "--synthetic-regime", "planted",
                        "--force", "--config", str(CFG_PATH)],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    for name, blob in first.items():
        assert (d / name).read_bytes() == blob, f"non-deterministic stage output: {name}"


# --------------------------------------------------------------------------- #
# 9. sample mode + checkpoint
# --------------------------------------------------------------------------- #
def test_sample_mode_placeholder_and_checkpoint():
    cfg = _write_cfg()
    r = subprocess.run([sys.executable, str(SCRIPT), "--mode", "sample", "--force",
                        "--config", str(cfg)], cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    out = OUT / "security_models"
    rows = _rows(out / "security_models_summary.csv")
    assert rows[0]["provenance"] == PROV_SAMPLE
    assert rows[0]["status"] == PENDING
    assert PENDING in (out / "SUMMARY.md").read_text()
    assert "UNVALIDATED SAMPLE OUTPUT" in (out / "SUMMARY.md").read_text()
    # checkpoint: a second sample run is skipped
    r2 = subprocess.run([sys.executable, str(SCRIPT), "--mode", "sample", "--config", str(cfg)],
                        cwd=str(REPO), capture_output=True, text=True)
    assert r2.returncode == 0 and "checkpoint hit" in r2.stderr, r2.stderr[-500:]


# --------------------------------------------------------------------------- #
# 10. CLI on all three regimes
# --------------------------------------------------------------------------- #
def test_cli_all_regimes_exit_zero():
    for regime in REGIMES:
        d = _run(regime)
        done = json.loads((d / "_DONE.json").read_text())
        assert done["stage"] == "07_security_models"
        assert done["synthetic"] is True and done["provenance"] == PROV_SYNTH
        assert done["regime"] == regime
        for f in ("SUMMARY.md", "SUMMARY.json", "group_assignment.csv", "b_primary.csv",
                  "b_group_means.csv", "c_end.csv", "c_exceedance.csv", "d_skippers.csv",
                  "e_selection.csv", "e_ipw.csv", "falsification.csv", "g_tail.csv",
                  "g_tail_summary.csv"):
            assert (d / f).exists(), f"{regime}: missing {f}"
        # fixture ground truth is recorded for the synthetic run (never a finding)
        s = json.loads((d / "SUMMARY.json").read_text())
        assert s["ground_truth"] and s["ground_truth"]["delta"] == _meta(regime)["ground_truth"]["delta"]
        assert "not a finding" in s["ground_truth"]["note"]


# --------------------------------------------------------------------------- #
# 11. reliability fallback
# --------------------------------------------------------------------------- #
def test_attenuation_null_without_icc():
    """No ICC -> the corrected coefficient is null with a reason (never a silent guess)."""
    y = np.random.default_rng(0).normal(size=50)
    x = np.random.default_rng(1).normal(size=50)
    g = np.random.default_rng(2).normal(size=50)
    C = np.ones((50, 1))
    clusters = np.arange(50)
    out = SM.fwl_attenuation(y, x, g, C, float("nan"), clusters, 10, 1)
    assert out["ok"] is False and out["beta_corr"] is None
    out2 = SM.fwl_attenuation(y, x, g, C, 0.0, clusters, 10, 1)
    assert out2["ok"] is False and "outside" in out2["reason"]
    # a valid ICC returns a finite corrected group coefficient
    out3 = SM.fwl_attenuation(y, x, g, C, 0.8, clusters, 20, 1)
    assert out3["ok"] is True and math.isfinite(float(out3["beta_corr"][1]))


# --------------------------------------------------------------------------- #
# 12. regressions — cycle-2 review defects
# --------------------------------------------------------------------------- #
def _model_rows(regime: str):
    """Rebuild the effort-level model rows straight from the fixture (no CSV round-trip)."""
    _fixture(regime)
    cfg = yaml.safe_load(_write_cfg().read_text(encoding="utf-8"))
    con = common.connect(cfg["runtime"]["duckdb_threads"])
    try:
        inp = SM._inputs(cfg, True, regime)
        exposures = SM.load_exposures(con, inp["exposure"])
        envelope = SM.load_envelope(con, inp["envelope"])
        psum = SM.load_player_summary(con, inp["players"])
        topk = SM.load_topk(con, inp["topk"])
        scope_key, active_types = common.scope_types(cfg)
        snaps = SM.scoped_snap_counts(SM.load_snap_counts(con, inp["snap_counts"]),
                                      active_types)
        game_ids = {int(r["game_id"]) for r in topk}
        gi = common.load_game_index(con, inp["games"], game_ids=game_ids)
        cov = SM.load_covariates(con, cfg, True, regime)
    finally:
        con.close()
    pool = [dict(nfl_id=nid, position_group=ex["position_group"], edp_pre=ex["edp_pre"],
                 log_edp_pre=ex["log_edp_pre"], edp_trend=ex["edp_trend"],
                 has_trend=ex["has_trend"], has_primary=ex["has_primary"])
            for nid, ex in sorted(exposures.items())]
    assign = sf.assign_security_groups(pool, cfg)
    try:
        rows, _ = SM.build_effort_rows(
            topk, assign, envelope, cov, psum, cfg, keep_unclassified=True,
            snap_counts=snaps, game_index=gi, season_types=active_types,
            season_scope=scope_key, min_snaps=int(cfg["game"]["route_snap_count"]))
    except TypeError:   # pre-fix signature: contrast groups only
        rows, _ = SM.build_effort_rows(topk, assign, envelope, cov, psum, cfg)
    return cfg, rows


def test_panel_rows_use_primary_feature_not_leftover_xkey():
    """Sensitivity panels must fit the primary feature (env_vmax), not the last robustness spec."""
    d = _run("planted")
    cfg, rows = _model_rows("planted")
    p = cfg["security_models"]["primary"]
    num_controls = [k for k in p["controls"] if k not in ("route_family", "position")]
    cat_controls = [k for k in p["controls"] if k in ("route_family", "position")]
    if p.get("stratum_fe", True):
        cat_controls = cat_controls + ["stratum"]
    req = ["y", "log_ceiling", "moved_down"] + num_controls
    keys = [("log_env_vmax" if k == "log_ceiling" else k) for k in req]

    for panel, stratum in (("trend_only", "trend"), ("level_fallback", "level_fallback")):
        fin = SM._complete([r for r in rows
                            if str(r.get("position")) == "WR" and r.get("stratum") == stratum
                            and r["group"] in GROUPS],
                           keys)
        X, names, _ = SM.design(fin, ["log_env_vmax", "moved_down"] + num_controls, cat_controls,
                                focal=["log_env_vmax", "moved_down"])
        y = np.array([r["y"] for r in fin], float)
        direct = SM.ols(y, X, clusters=np.array([r["nfl_id"] for r in fin]))
        beta = float(direct.params[names.index("log_env_vmax")])
        # the same rows fitted with the old leftover loop variable (20-yard speed) must differ
        Xw, nw, _ = SM.design(fin, ["log_speed_at_20yd", "moved_down"] + num_controls,
                              cat_controls, focal=["log_speed_at_20yd", "moved_down"])
        wrong = float(SM.ols(y, Xw, clusters=np.array([r["nfl_id"] for r in fin]))
                      .params[nw.index("log_speed_at_20yd")])
        # The two features are deliberately near-collinear in the fixture (~0.94 ratio), so the
        # discriminator is *exact* agreement with the intended regressor, not a large separation.
        assert abs(beta - wrong) > 1e-3, (panel, beta, wrong)
        rec = next(r for r in _rows(d / "b_primary.csv")
                   if r["spec"] == f"panel_{panel}" and r["scope"] == "WR")
        assert abs(float(rec["speed_coef"]) - beta) < 1e-4, (panel, rec["speed_coef"], beta)
        assert abs(float(rec["speed_coef"]) - wrong) > 1e-3, (panel, rec["speed_coef"], wrong)
        assert abs(float(rec["reliability_icc"]) - 0.64) < 1e-9, rec
        # the panel rows must carry BOTH the naive and ICC-attenuation-corrected estimates
        assert rec["reliability_icc"] not in ("", None), rec
        assert rec["speed_coef_corrected"] not in ("", None), rec
        assert rec["group_coef_corrected"] not in ("", None), rec
        assert rec["group_se_corrected"] not in ("", None), rec
        assert rec["group_p_corrected"] not in ("", None), rec


def test_c_end_retains_unclassified_middle_tertile():
    """The middle tertile is out of the contrast but stays in the continuous dose-response."""
    d = _run("planted")
    _, rows = _model_rows("planted")
    wr_unclass = [r for r in rows
                  if str(r.get("position")) == "WR" and r["group"] == "unclassified"]
    assert wr_unclass, "the continuous-model rows dropped the unclassified middle tertile"

    b = next(r for r in _rows(d / "b_primary.csv")
             if r["spec"] == "primary_env_vmax" and r["scope"] == "WR")
    c = [r for r in _rows(d / "c_end.csv")
         if r["scope"] == "WR" and r["basis"] == "mean_ceiling"]
    assert c, "no WR mean-ceiling rows in c_end.csv"
    for rec in c:
        assert int(rec["n_obs"]) > int(b["n_obs"]), (rec["n_obs"], b["n_obs"])
        assert int(rec["n_players"]) > int(b["n_players"]), (rec["n_players"], b["n_players"])


def test_min_group_n_enforced_per_group():
    """One thin contrast group must block the fit, even when total players clear min_group_n."""
    _run("planted")
    cfg, rows = _model_rows("planted")
    cfg["security_models"]["primary"]["scopes"] = ["WR"]
    wr = [r for r in rows if str(r.get("position")) == "WR" and r["group"] in GROUPS]
    moved_ids = sorted({r["nfl_id"] for r in wr if r["group"] == "moved_down"})
    assert len(moved_ids) > 5, "fixture has too few moved_down WR players for this test"
    keep = set(moved_ids[:5])
    small = [r for r in wr if r["group"] != "moved_down" or r["nfl_id"] in keep]
    out, _, _ = SM.run_b(small, {}, {}, cfg, {})
    rec = next(r for r in out if r["spec"] == "primary_env_vmax" and r["scope"] == "WR")
    assert rec["group_coef"] is None, rec
    assert rec["thin"] is True, rec
    assert rec["n_players_moved_down"] == 5, rec
    assert rec["n_players_high_secure"] > 15, rec
    assert "thin group" in rec["note"], rec["note"]
    assert "moved_down=5" in rec["note"], rec["note"]
    # and the summary renderer marks it explicitly, not just in a CSV note
    s = json.loads((_run("planted") / "SUMMARY.json").read_text(encoding="utf-8"))
    s["sort"]["thin_groups"] = ["moved_down"]
    s["sort"]["min_group_n"] = 15
    s["b_primary"][0]["thin"] = True
    txt = SM.render_summary(s)
    assert "THIN GROUP(S)" in txt and "moved_down" in txt and "| yes |" in txt


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_fixture_schema_and_units, test_input_schema_guard_fails_loudly,
             test_output_tables_schema, test_group_assignment_trend_sign_and_level_tertiles,
             test_assignment_reports_cutoffs_and_threshold_grid,
             test_threshold_counts_match_board_rule,
             test_threshold_T5_sensitivity_panel_uses_5,
             test_post_combine_mutation_invariance,
             test_assignment_source_has_no_post_combine_reads,
             test_planted_effect_recovered, test_dose_response_monotone,
             test_null_not_recovered_and_nulls_written_as_null,
             test_selection_ipw_moves_toward_planted, test_edge_cases_present_and_handled,
             test_separation_reports_null_e_not_crash,
             test_safe_odds_ratio_clips_instead_of_overflowing,
             test_e_failure_is_non_fatal_and_b_primary_still_written,
             test_determinism_same_seed_identical, test_sample_mode_placeholder_and_checkpoint,
             test_cli_all_regimes_exit_zero, test_attenuation_null_without_icc,
             test_panel_rows_use_primary_feature_not_leftover_xkey,
             test_c_end_retains_unclassified_middle_tertile,
             test_min_group_n_enforced_per_group]
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
