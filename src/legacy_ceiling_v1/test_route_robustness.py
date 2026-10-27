#!/usr/bin/env python3
"""Tests for ``src/08_route_robustness.py`` — balance, strata plumbing, LOO and permutation.

The statistics themselves live in ``07_security_models`` (``design`` / ``ols`` / ``_mark_thin``);
these tests cover only the *new* plumbing: the balance table cannot lose or double-count efforts,
shares sum to one, a thin group is never fitted, LOO drops exactly one player per fit, and the
player-level permutation is deterministic under a fixed seed and centred near zero under the null.

Runs without pytest (``python src/tests/test_route_robustness.py``) and under pytest.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
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


RR = _load("_rr08", REPO / "src" / "08_route_robustness.py")
SM = _load("_sm07_for_rr", REPO / "src" / "07_security_models.py")
GEN = _load("_secgamefix_for_rr", REPO / "src" / "synthetic_security_game_fixture.py")
SCRIPT = REPO / "src" / "08_route_robustness.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg_dict(out: Path, fixtures: Path | None = None) -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(out)
    if fixtures is not None:
        cfg["paths"]["fixtures"] = str(fixtures)
    # small reps only; the code path is identical
    cfg["route_robustness"]["perm_reps"] = 60
    cfg["security_models"]["primary"]["attenuation_reps"] = 20
    return cfg


def _row(nid, group, fam, pl, snap=0.9, pos="WR", stratum="trend", y=2.5, ceiling=2.4,
         height=73.0, weight=200.0, years=2.0, play=0):
    return dict(nfl_id=nid, group=group, moved_down=1.0 if group == "moved_down" else 0.0,
                route_family=fam, pass_length=pl, snap_share=snap, position=pos,
                stratum=stratum, y=y, log_ceiling=ceiling, log_env_vmax=ceiling,
                height=height, weight=weight, years_since_combine=years,
                game_id=2024010100 + nid, play_id=nid * 10 + play, n_route_snaps=int(snap * 100))


def _rows(n_down=20, n_secure=20, fams=("VERTICAL", "BREAK_IN", "BREAK_OUT", "SHORT")):
    """One player per id; ``len(fams)`` efforts per player."""
    out = []
    nid = 1000
    for g, n in (("moved_down", n_down), ("high_secure", n_secure)):
        for _ in range(n):
            for j, fam in enumerate(fams):
                out.append(_row(nid, g, fam, 5 + 5 * j, snap=0.5 + 0.1 * j, play=j))
            nid += 1
    return out


def _write_cfg(cfg: dict, path: Path) -> Path:
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# A. balance
# --------------------------------------------------------------------------- #
def test_balance_counts_shares_and_depth():
    rows = _rows()
    extra = {(int(r["nfl_id"]), int(r["game_id"]), int(r["play_id"])): dict(target=(r["nfl_id"] % 2 == 0),
                                                                             rank=1)
             for r in rows}
    bal = RR.balance_rows(rows, extra, ("moved_down", "high_secure"))
    for g, rec in bal.items():
        assert rec["n_efforts"] == 80
        assert rec["n_players"] == 20
        shares = [rec[f"share_{f}"] for f in ("VERTICAL", "BREAK_IN", "BREAK_OUT", "SHORT", "OTHER")]
        assert abs(sum(shares) - 1.0) < 1e-12, shares
        assert sum(rec[f"n_{f}"] for f in ("VERTICAL", "BREAK_IN", "BREAK_OUT", "SHORT",
                                           "OTHER")) == rec["n_efforts"]
        assert rec["pass_length_n_missing"] == 0
        assert 0.0 <= rec["target_rate"] <= 1.0
    # identical menus by construction -> identical shares
    assert bal["moved_down"]["share_VERTICAL"] == bal["high_secure"]["share_VERTICAL"]


def test_balance_handles_missing_pass_length_and_no_targets():
    rows = [_row(1, "moved_down", "VERTICAL", None), _row(2, "high_secure", "SHORT", 10)]
    bal = RR.balance_rows(rows, {}, ("moved_down", "high_secure"))
    assert bal["moved_down"]["pass_length_n_missing"] == 1
    assert math.isnan(bal["moved_down"]["pass_length_mean"])
    # no target rows -> NaN, never a fabricated 0
    assert math.isnan(bal["moved_down"]["target_rate"])
    assert bal["moved_down"]["target_n_missing"] == 1


def test_balance_tests_are_bounded():
    rows = _rows(fams=("VERTICAL", "SHORT"))
    extra = {(int(r["nfl_id"]), int(r["game_id"]), int(r["play_id"])): dict(target=False, rank=1)
             for r in rows}
    t = RR.balance_tests(rows, extra, ("moved_down", "high_secure"))[0]
    assert 0.0 <= t["chi2_p"] <= 1.0
    assert 0.0 <= t["pass_length_welch_p"] <= 1.0
    assert t["chi2_dof"] == 1
    assert abs(t["target_rate_diff"]) < 1e-12


# --------------------------------------------------------------------------- #
# B. strata / thin rule
# --------------------------------------------------------------------------- #
def test_stratum_defs_use_config_thresholds():
    cfg = _cfg_dict(Path(tempfile.mkdtemp()))
    names = [n for n, _ in RR.stratum_defs(cfg)]
    assert "route_family=VERTICAL" in names
    assert f"pass_length>={float(cfg['audit']['deep_pass_length_yd']):g}" in names
    assert "pass_length 0-9" in names and "pass_length 10-19" in names
    assert "pass_length 20+" in names


def test_fit_group_marks_thin_and_never_fits():
    cfg = _cfg_dict(Path(tempfile.mkdtemp()))
    # 3 vs 30 players: below sort.min_group_n=15 -> thin, coefficients stay None
    rows = _rows(n_down=3, n_secure=30)
    rec = RR.fit_group(rows, cfg, SM, {}, ("moved_down", "high_secure"))
    assert rec["thin"] is True
    assert rec["group_coef"] is None and rec["group_p"] is None
    assert rec["n_players_moved_down"] == 3


def test_fit_group_matches_run_b_on_the_full_sample():
    """The stratum fit on the *whole* sample must equal block B's own row (same machinery)."""
    cfg = _cfg_dict(Path(tempfile.mkdtemp()))
    d = RR.build_panels(cfg, True, "planted", SM)
    rel = SM.load_reliability(cfg, True, "planted", common.get_logger())
    rows = d["rows"]["T3"]
    got = RR.fit_group(rows, cfg, SM, rel, ("moved_down", "high_secure"))
    b = SM.run_b(rows, {}, rel, cfg, {})[0]
    ref = next(r for r in b if r["scope"] == "pooled" and r["spec"] == "primary_env_vmax")
    assert got["n_obs"] == ref["n_obs"]
    assert abs(got["group_coef"] - ref["group_coef"]) < 1e-12
    assert abs(got["group_se"] - ref["group_se"]) < 1e-12


# --------------------------------------------------------------------------- #
# C. LOO plumbing
# --------------------------------------------------------------------------- #
def test_loo_drops_exactly_one_player_per_fit():
    cfg = _cfg_dict(Path(tempfile.mkdtemp()))
    rows = _rows(n_down=16, n_secure=16)
    fits, base = RR.loo(rows, cfg, SM, {}, ("moved_down", "high_secure"), "T3", "pooled")
    assert len(fits) == 32
    assert len({f["dropped_nfl_id"] for f in fits}) == 32
    for f in fits:
        assert f["n_obs"] == base["n_obs"] - 4          # 4 efforts per player in the fixture
        assert f["n_players"] == base["n_players"] - 1


def test_loo_summary_ranges_and_shares():
    base = dict(group_coef=-0.05, group_p=0.04)
    fits = [dict(dropped_nfl_id=i, group_coef=c, group_p=p, group_ci_lo=c - 0.01, group_ci_hi=c + 0.01)
            for i, (c, p) in enumerate([(-0.06, 0.03), (-0.04, 0.06), (-0.02, 0.20)])]
    s = RR.loo_summary(fits, base, "T5", "pooled")
    assert s["coef_min"] == -0.06 and s["coef_max"] == -0.02
    assert abs(s["coef_range"] - 0.04) < 1e-12
    assert s["p_min"] == 0.03 and s["p_max"] == 0.20
    assert s["n_p_lt_05"] == 1 and abs(s["share_p_lt_05"] - 1 / 3) < 1e-12
    assert s["n_ci_excludes_zero"] == 3
    assert s["most_influential_player"] == 2            # |−0.02 − (−0.05)| largest


# --------------------------------------------------------------------------- #
# D. permutation
# --------------------------------------------------------------------------- #
def _vary(rows, seed=3):
    rng = np.random.default_rng(seed)
    for r in rows:
        r["log_env_vmax"] = r["log_ceiling"] = 2.4 + float(rng.normal(0, 0.05))
        r["y"] = r["log_ceiling"] + float(rng.normal(0, 0.08))
    return rows


def test_permutation_is_deterministic_and_bounded():
    cfg = _cfg_dict(Path(tempfile.mkdtemp()))
    rows = _vary(_rows(n_down=18, n_secure=18))
    a = RR.permutation(rows, cfg, SM, ("moved_down", "high_secure"), 50, 12345)
    b = RR.permutation(rows, cfg, SM, ("moved_down", "high_secure"), 50, 12345)
    assert a["observed_coef"] == b["observed_coef"]
    assert a["p_perm_two_sided"] == b["p_perm_two_sided"]
    assert 0.0 < a["p_perm_two_sided"] <= 1.0
    assert a["n_reps"] == 50 and a["n_players"] == 36


def test_permutation_null_is_centred_on_zero():
    """With group labels unrelated to the outcome, the null mean must sit near zero."""
    cfg = _cfg_dict(Path(tempfile.mkdtemp()))
    rng = np.random.default_rng(7)
    rows = _vary(_rows(n_down=25, n_secure=25), seed=11)
    for r in rows:
        r["y"] = 2.5 + float(rng.normal(0, 0.1))       # outcome independent of group
    rec = RR.permutation(rows, cfg, SM, ("moved_down", "high_secure"), 200, 999)
    assert abs(rec["perm_coef_mean"]) < 0.01
    assert abs(rec["observed_coef"]) < 0.12
    assert 0.2 <= rec["p_perm_two_sided"] <= 1.0


# --------------------------------------------------------------------------- #
# E. end-to-end on the committed fixture
# --------------------------------------------------------------------------- #
def _run_fixture(tmp: Path) -> Path:
    cfg = _cfg_dict(tmp / "out", REPO / "fixtures")
    cfg["paths"]["fixtures"] = str(REPO / "fixtures")
    cfg_path = _write_cfg(cfg, tmp / "config.yaml")
    out = tmp / "out" / "security_models" / "route_robustness"
    r = subprocess.run([sys.executable, str(SCRIPT), "--synthetic", "--synthetic-regime", "planted",
                        "--config", str(cfg_path), "--out-dir", str(out)],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-3000:]}"
    return out / "synthetic" / "planted"          # --synthetic nests under the regime


def test_cli_writes_the_documented_artefacts():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_rr_"))
    out = _run_fixture(tmp)
    for f in ("route_balance.csv", "route_balance_tests.csv", "route_strata.csv",
              "composition.csv", "loo_fits.csv", "loo_summary.csv", "permutation.csv",
              "route_robustness.md"):
        assert (out / f).exists(), f"missing {f}"
    for f in ("route_balance.csv", "route_strata.csv", "loo_fits.csv", "loo_summary.csv",
              "permutation.csv"):
        header = (out / f).read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header[0] == "provenance", f"{f}: first column must be provenance"
    md = (out / "route_robustness.md").read_text(encoding="utf-8")
    assert md.startswith("# Route robustness")
    assert "not a pre-declared test" in md
    assert "cannot rule out" in md
    strata = list(csv.DictReader((out / "route_strata.csv").open(newline="", encoding="utf-8")))
    assert {r["stratum"] for r in strata} >= {"route_family=VERTICAL", "pass_length 0-9"}
    # an unfitted row must say why; the fixture must produce at least one fitted cell
    unfitted = [r for r in strata if r["group_coef"] == ""]
    assert all(r["note"] for r in unfitted)
    assert any(r["group_coef"] != "" for r in strata)
    perm = list(csv.DictReader((out / "permutation.csv").open(newline="", encoding="utf-8")))
    assert len(perm) == 3
    assert all(0.0 <= float(r["p_perm_two_sided"]) <= 1.0 for r in perm)


def test_composition_has_every_scope_and_never_fits_a_thin_one():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_rr_comp_"))
    out = _run_fixture(tmp)
    rows = list(csv.DictReader((out / "composition.csv").open(newline="", encoding="utf-8")))
    scopes = {r["scope"] for r in rows}
    assert scopes == {"pooled", "WR", "pooled excluding TE", "pooled excluding WR"}
    assert len(rows) == 8                                    # 4 scopes x 2 panels
    assert all(r["group_coef"] == "" for r in rows if r["thin"] == "True")
    pooled = [r for r in rows if r["scope"] == "pooled"]
    assert len(pooled) == 2 and all(r["group_coef"] != "" for r in pooled)


def test_sample_mode_writes_a_labelled_placeholder():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_rr_sample_"))
    cfg = _cfg_dict(tmp / "out")
    cfg_path = _write_cfg(cfg, tmp / "config.yaml")
    out = tmp / "out" / "security_models" / "route_robustness"
    r = subprocess.run([sys.executable, str(SCRIPT), "--mode", "sample", "--config", str(cfg_path),
                        "--out-dir", str(out)], cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    md = (out / "route_robustness.md").read_text(encoding="utf-8")
    assert "PENDING FULL RUN" in md


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
