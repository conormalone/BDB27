#!/usr/bin/env python3
"""Hostile-input tests for Phase 5 B–G (`src/07_security_models.py`) — D43.

The committed fixtures are well-behaved, which is exactly how the `math.exp` overflow in E reached a
four-hour real run. These tests feed deliberately nasty inputs through the stage and assert the
structural guarantees added by D43 rather than any particular number:

  * **no uncaught exception** — a separating predictor, a zero-variance covariate, extreme outliers and
    NaN/blank optional cells must not take the process down;
  * **incremental emission** — every block's file is on disk right after that block, so a later failure
    cannot destroy earlier work;
  * **labelled failure** — a failed block leaves a single `block_status=FAILED` row with the reason, and
    `SUMMARY.json` / `SUMMARY.md` name the block and the reason;
  * **the primary test is never faked** — if B fails the stage raises with an explicit message (and the
    B file is labelled FAILED); it never writes a fabricated null as the primary result.

Runnable without pytest (`python src/tests/test_security_models_hostile.py`).
"""
from __future__ import annotations

import copy
import csv
import ast
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402

SCRIPT = REPO / "src" / "07_security_models.py"
PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
EXPECTED = ["group_assignment.csv", "b_primary.csv", "b_group_means.csv", "c_end.csv",
            "c_exceedance.csv", "d_skippers.csv", "e_selection.csv", "e_ipw.csv",
            "falsification.csv", "g_tail.csv", "g_tail_summary.csv"]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEN = _load("_hostile_gen", REPO / "src" / "synthetic_security_game_fixture.py")
SM = _load("_hostile_sm07", SCRIPT)
MF = _load("_hostile_mf", REPO / "src" / "model_features.py")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(tmp: Path) -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(tmp / "out")
    cfg["paths"]["fixtures"] = str(tmp / "fixtures")
    cfg["paths"]["notes"] = str(tmp / "notes")          # never write into the repo's notes/
    # fast but identical code path (fewer bootstrap replicates only)
    cfg["security_models"]["primary"]["attenuation_reps"] = 10
    cfg["security_models"]["g"]["bootstrap_reps"] = 5
    return cfg


def _build(tmp: Path, regime: str = "planted") -> tuple[dict, Path]:
    cfg = _cfg(tmp)
    d = Path(cfg["paths"]["fixtures"]) / cfg["security_models"]["synthetic"]["root"] / regime
    GEN.build_fixture(d, cfg, regime,
                      int(cfg["seed"]) + int(cfg["security_models"]["synthetic"]["seed_offset"]))
    return cfg, d


def _write_cfg(tmp: Path, cfg: dict) -> Path:
    p = tmp / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _rd(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _wr(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _run_cli(cfg_path: Path, regime: str = "planted") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--synthetic", "--synthetic-regime", regime,
                           "--force", "--config", str(cfg_path)],
                          cwd=str(REPO), capture_output=True, text=True)


def _out_dir(cfg: dict) -> Path:
    return Path(cfg["paths"]["outputs"]) / "security_models" / "synthetic" / "planted"


def _assert_files_and_labels(out: Path) -> None:
    for name in EXPECTED:
        assert (out / name).exists(), f"missing output file: {name}"
        rows = _rd(out / name)
        assert rows, f"{name} is empty"
        header = (out / name).read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header[0] == "provenance", f"{name}: first column must be provenance"
        assert header[-1] == "block_status", f"{name}: last column must be block_status"
        for r in rows:
            assert r["block_status"] in ("OK", "FAILED"), (name, r["block_status"])
            if r["block_status"] == "FAILED":
                assert r.get("reason"), f"{name}: FAILED row without a reason"


# --------------------------------------------------------------------------- #
# (b) constant covariate + (c) extreme outliers + (d) NaN/blank optional cells
# --------------------------------------------------------------------------- #
def test_hostile_covariates_outliers_and_blanks():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_hostile_cov_"))
    cfg, d = _build(tmp)

    cov = _rd(d / "covariates.csv")
    for i, r in enumerate(cov):
        r["weight"] = "200.0"                       # (b) zero-variance covariate
        if i % 17 == 0:
            r["age"] = ""                            # (d) blank optional numeric
            r["draft_position"] = ""
        if i % 23 == 0:
            r["height"] = ""                         # (d) blank size covariate (row must drop)
        if i % 29 == 0:
            r["optout"] = "NaN"                      # (d) NaN-ish token in an optional flag
    _wr(d / "covariates.csv", cov)

    exp = _rd(d / "security_exposure.csv")
    for i, r in enumerate(exp):
        if i in (0, 1):                              # (c) extreme log-EDP outliers
            r["log_edp_pre"] = "40.0"
            r["edp_pre"] = repr(math.exp(40.0))
        if i == 2:
            r["edp_trend"] = "1e6"
    _wr(d / "security_exposure.csv", exp)

    env = _rd(d / "per_player_envelope.csv")
    env[0]["env_vmax"] = "100000.0"                  # (c) extreme Combine speed outlier
    _wr(d / "per_player_envelope.csv", env)

    r = _run_cli(_write_cfg(tmp, cfg))
    out = _out_dir(cfg)
    if r.returncode != 0:
        # allowed ONLY for the headline block, and only with an explicit message
        assert "primary block B failed" in r.stderr, r.stderr[-1500:]
        assert (out / "b_primary.csv").exists()
        assert _rd(out / "b_primary.csv")[0]["block_status"] == "FAILED"
        return
    _assert_files_and_labels(out)
    b = _rd(out / "b_primary.csv")
    primary = [x for x in b if x["block_status"] == "OK" and x["spec"] == "primary_env_vmax"]
    assert primary, b[:2]                            # the primary result is present, not a fake null
    assert all(x["group_coef"] != "" for x in primary), primary[:1]


# --------------------------------------------------------------------------- #
# (a) perfectly separating predictor for the selection logit, through the stage
# --------------------------------------------------------------------------- #
def test_separating_selection_logit_through_stage():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_hostile_sep_"))
    cfg, d = _build(tmp)
    appeared = {r["nfl_id"] for r in _rd(d / "player_summary.csv")}

    # make log_combine_speed perfectly predict appearance: two disjoint value clusters, with
    # within-cluster variance so the other blocks (whose ceiling control needs variance) still fit.
    env = _rd(d / "per_player_envelope.csv")
    for i, r in enumerate(env):
        base = 8.5 if r["nfl_id"] in appeared else 6.5
        r["env_vmax"] = f"{base + (i % 7) * 0.05:.3f}"
    _wr(d / "per_player_envelope.csv", env)

    r = _run_cli(_write_cfg(tmp, cfg))
    assert r.returncode == 0, f"a separating predictor must not kill the stage:\n{r.stderr[-2000:]}"
    out = _out_dir(cfg)
    _assert_files_and_labels(out)

    sel = _rd(out / "e_selection.csv")
    assert sel[0]["term"] == "unavailable", sel[0]
    assert "reported null" in sel[0]["note"], sel[0]
    assert any(tok in sel[0]["note"].lower()
               for tok in ("separation", "did not converge", "logit failed")), sel[0]
    ipw = _rd(out / "e_ipw.csv")
    assert ipw[0]["weighting"] == "unavailable", ipw[0]

    # B is unaffected: the primary result is present and was written before E ran
    b = _rd(out / "b_primary.csv")
    assert any(x["spec"] == "primary_env_vmax" and x["block_status"] == "OK" for x in b), b[:2]
    s = json.loads((out / "SUMMARY.json").read_text(encoding="utf-8"))
    assert s["failed_blocks"] == {}, s["failed_blocks"]


# --------------------------------------------------------------------------- #
# 2) incremental emission + labelled exploratory failure
# --------------------------------------------------------------------------- #
def test_later_block_failure_keeps_earlier_files():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_hostile_g_"))
    cfg, _ = _build(tmp)
    out = _out_dir(cfg)
    log = common.get_logger()
    orig = SM.run_g

    def _boom(*_a, **_k):
        raise ValueError("simulated G explosion")

    SM.run_g = _boom
    try:
        summary = SM.run(cfg, "sample", True, "planted", out, PROV_SYNTH, log)
    finally:
        SM.run_g = orig

    for name in ("group_assignment.csv", "b_primary.csv", "b_group_means.csv", "c_end.csv",
                 "c_exceedance.csv", "d_skippers.csv", "e_selection.csv", "e_ipw.csv",
                 "falsification.csv"):
        assert (out / name).exists(), f"{name} was not emitted before G failed"
        rows = _rd(out / name)
        assert rows and rows[0]["block_status"] == "OK", (name, rows[:1])

    for name in ("g_tail.csv", "g_tail_summary.csv"):
        rows = _rd(out / name)
        assert len(rows) == 1 and rows[0]["block_status"] == "FAILED", rows
        assert "ValueError: simulated G explosion" in rows[0]["reason"], rows

    assert "G" in summary["failed_blocks"], summary["failed_blocks"]
    assert "simulated G explosion" in summary["failed_blocks"]["G"]
    md = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "FAILED BLOCKS" in md and "**G: FAILED**" in md, md[-1000:]


# --------------------------------------------------------------------------- #
# 3) the headline block fails loudly and is never fabricated
# --------------------------------------------------------------------------- #
def test_primary_block_failure_is_hard_and_labelled():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_hostile_b_"))
    cfg, _ = _build(tmp)
    out = _out_dir(cfg)
    log = common.get_logger()
    orig = SM.run_b

    def _boom(*_a, **_k):
        raise RuntimeError("simulated B explosion")

    SM.run_b = _boom
    try:
        try:
            SM.run(cfg, "sample", True, "planted", out, PROV_SYNTH, log)
        except RuntimeError as exc:
            msg = str(exc)
        else:  # pragma: no cover
            raise AssertionError("a failing primary block B must raise")
    finally:
        SM.run_b = orig

    assert "primary block B failed" in msg and "simulated B explosion" in msg, msg
    rows = _rd(out / "b_primary.csv")
    assert len(rows) == 1 and rows[0]["block_status"] == "FAILED", rows
    assert "RuntimeError: simulated B explosion" in rows[0]["reason"], rows
    assert rows[0].get("group_coef", "") == "" and rows[0].get("group_p", "") == "", rows


# --------------------------------------------------------------------------- #
# 4) static numeric audit + real-shaped separating rows (D43 hardening)
# --------------------------------------------------------------------------- #
_NUMERIC_FILES = ("07_security_models.py", "common.py", "gate.py", "model_features.py",
                  "security_features.py")
_ALLOWED_NUMERIC_FUNCS = {"_safe_exp"}
_UNSAFE_MATH = {"exp", "cosh", "sinh", "factorial"}


def test_static_numeric_audit():
    """math.exp/cosh/sinh/factorial and pow() may only appear inside the one safe helper."""
    offenders: list[str] = []
    for name in _NUMERIC_FILES:
        path = REPO / "src" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
                fn = node.func
                fname = None
                if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                        and fn.value.id == "math" and fn.attr in _UNSAFE_MATH):
                    fname = f"math.{fn.attr}"
                elif (isinstance(fn, ast.Name)
                      and fn.id in ("pow", "factorial")):
                    fname = fn.id
                if fname and not (set(self.stack) & _ALLOWED_NUMERIC_FUNCS):
                    offenders.append(f"{name}:{node.lineno} {fname} outside _safe_exp")
                self.generic_visit(node)

            def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
                # A bare ``float(x) ** n`` is the shape that overflowed at runtime.
                if isinstance(node.op, ast.Pow) and "float(" in ast.unparse(node):
                    if not (set(self.stack) & _ALLOWED_NUMERIC_FUNCS):
                        offenders.append(f"{name}:{node.lineno} bare float ** power")
                self.generic_visit(node)

        Visitor().visit(tree)
    assert not offenders, "\n".join(offenders)


def test_separating_real_shaped_rows_without_fixture():
    """Perfect separation in a hand-built pool -> labelled unavailable E, never OverflowError."""
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    pool = []
    for i in range(80):
        appeared = i < 40
        pool.append(dict(
            nfl_id=i, position_group="WR", position="WR",
            log_edp_pre=3.0 + 0.01 * (i % 7),
            log_combine_speed=9.0 if appeared else 7.0,   # perfectly separates appearance
            height=72.0 + 0.1 * (i % 5), weight=200.0 + 0.5 * (i % 4),
            appeared=appeared))

    sel, ipw, meta = SM.run_e(pool, [], cfg)            # must not raise OverflowError
    assert sel[0]["term"] == "unavailable", sel[0]
    assert "reported null" in sel[0]["note"], sel[0]
    assert ipw[0]["weighting"] == "unavailable", ipw[0]
    assert meta.get("n_pool") == 80 and meta.get("n_appeared") == 40, meta

    # bmi must never raise on absurd but numeric inputs (and must not return a bogus 0.0 either:
    # ``h * h`` overflows to inf, so a naive guard would report a BMI of zero for height=1e200).
    for val in (MF.bmi(1e200, 200, 703), MF.bmi(1e-200, 200, 703)):
        assert np.isnan(val), val


# --------------------------------------------------------------------------- #
# 5) D44: an absurd height through the REAL stage entry point (the Windows crash)
# --------------------------------------------------------------------------- #
def test_absurd_height_through_stage_writes_primary_and_reports_quality():
    """A corrupt height (1e200) must not kill stage 07 — and must never vanish silently.

    Reproduces Conor's Windows symptom on Linux: ``OverflowError: math range error`` came from
    ``model_features.bmi`` while building covariates, i.e. BEFORE block B, so there was no
    ``b_primary.csv`` at all. After D44 the stage completes, B is written, and the corrupt row is
    named by ``nfl_id`` in a WARNING + ``covariate_quality.csv`` + ``SUMMARY.*``.
    """
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_hostile_bmi_"))
    cfg, d = _build(tmp)

    effort_ids = sorted({int(r["nfl_id"]) for r in _rd(d / "efforts.csv")})
    bad_height, bad_weight = effort_ids[0], effort_ids[1]
    cov = _rd(d / "covariates.csv")
    touched = 0
    for r in cov:
        if int(r["nfl_id"]) == bad_height:
            r["height"] = "1e200"                    # the exact shape that raised OverflowError
            touched += 1
        elif int(r["nfl_id"]) == bad_weight:
            r["weight"] = "1e200"                    # finite, but an impossible weight
            touched += 1
    assert touched == 2, (bad_height, bad_weight, touched)
    _wr(d / "covariates.csv", cov)

    r = _run_cli(_write_cfg(tmp, cfg))
    assert r.returncode == 0, f"a corrupt height must not kill the stage:\n{r.stderr[-2000:]}"
    out = _out_dir(cfg)
    _assert_files_and_labels(out)

    # (a) no exception, and the primary result IS written
    b = _rd(out / "b_primary.csv")
    primary = [x for x in b if x["spec"] == "primary_env_vmax" and x["block_status"] == "OK"]
    assert primary, b[:2]
    assert primary[0]["group_coef"] != "", primary[0]
    assert "covariate quality" in primary[0]["note"], primary[0]["note"]

    # (b) the corrupt rows are named, counted and reported — not silently nan'd
    q = _rd(out / "covariate_quality.csv")
    assert q, "covariate_quality.csv must record the excluded rows"
    assert (out / "covariate_quality.csv").read_text(encoding="utf-8").splitlines()[0].split(",")[0] \
        == "provenance"
    named = {int(x["nfl_id"]) for x in q if x["nfl_id"]}
    assert {bad_height, bad_weight} <= named, (bad_height, bad_weight, named)
    raw = {x["field"]: x["value"] for x in q if int(x["nfl_id"]) == bad_height}
    assert float(raw.get("height")) == 1e200, raw          # the raw value, not a masked one

    s = json.loads((out / "SUMMARY.json").read_text(encoding="utf-8"))
    cq = s["covariate_quality"]
    assert cq["n_players_flagged"] >= 2, cq
    assert cq["n_values_flagged"] >= 2, cq
    assert cq["n_effort_rows_excluded"] > 0, cq
    assert {bad_height, bad_weight} <= {int(i["nfl_id"]) for i in cq["issues"]}, cq["issues"]
    assert cq["bounds"]["height_in"] == [48.0, 90.0], cq["bounds"]
    md = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "Covariate quality (D44)" in md and "covariate_quality.csv" in md, md[:2000]

    # (c) the operator sees the player, not just a count
    assert "IMPLAUSIBLE COVARIATE" in r.stderr, r.stderr[-2000:]
    assert f"nfl_id={bad_height}" in r.stderr, r.stderr[-2000:]
    assert "1e+200" in r.stderr or "1e200" in r.stderr, r.stderr[-2000:]


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_hostile_covariates_outliers_and_blanks,
             test_separating_selection_logit_through_stage,
             test_later_block_failure_keeps_earlier_files,
             test_primary_block_failure_is_hard_and_labelled,
             test_static_numeric_audit,
             test_separating_real_shaped_rows_without_fixture,
             test_absurd_height_through_stage_writes_primary_and_reports_quality]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
