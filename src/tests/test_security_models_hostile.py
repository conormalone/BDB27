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
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

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
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_hostile_covariates_outliers_and_blanks,
             test_separating_selection_logit_through_stage,
             test_later_block_failure_keeps_earlier_files,
             test_primary_block_failure_is_hard_and_labelled]
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
