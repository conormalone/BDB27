#!/usr/bin/env python3
"""Behaviour tests for the pre-registration freeze / go-no-go gate (`src/gate.py`).

Runnable **without pytest** (`python src/tests/test_gate.py`) and collected by pytest (functions
named `test_*`). Proves the gate **passes** with the frozen pre-registration and **blocks** an
unfrozen / missing pre-registration, a null required config key, and (game-side) a non-passing
audit decision — and that sample mode is never gated.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402
import gate  # noqa: E402

GATE = REPO / "src" / "gate.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(notes: Path | None = None, outputs: Path | None = None, **overrides) -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    if notes is not None:
        cfg["paths"]["notes"] = str(notes)
    if outputs is not None:
        cfg["paths"]["outputs"] = str(outputs)
    for dotted, val in overrides.items():
        node = cfg
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = val
    return cfg


def _write_prereg(d: Path, frozen: bool) -> None:
    d.mkdir(parents=True, exist_ok=True)
    if frozen:
        (d / "prereg.md").write_text(
            "# Pre-registration — BDB27 ceiling-binding task (**FROZEN**)\n\n"
            "> **Status: FROZEN** (2026-10-12). Values fixed before the full runs.\n\n"
            "All values are frozen; no residual placeholders.\n", encoding="utf-8")
    else:
        (d / "prereg.md").write_text(
            "# Pre-registration — BDB27 (skeleton)\n\n> Status: skeleton\n\n"
            "- quantile ladder: TBD\n- \u2610 feature set\n", encoding="utf-8")


def _write_decision(outputs: Path, decision: str) -> None:
    d = outputs / "audit"
    d.mkdir(parents=True, exist_ok=True)
    (d / "audit_decision.json").write_text(json.dumps({"decision": decision}), encoding="utf-8")


def _run_cli(args: list[str], config: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE)] + args + ["--config", str(config)],
                          cwd=str(REPO), capture_output=True, text=True)


def _dump_cfg(cfg: dict, path: Path) -> Path:
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# freeze gate
# --------------------------------------------------------------------------- #
def test_frozen_prereg_passes():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    _write_prereg(notes, frozen=True)
    frozen, reasons = gate.prereg_frozen(_cfg(notes=notes))
    assert frozen and not reasons, reasons
    ok, missing = gate.config_keys_ok(_cfg(notes=notes))
    assert ok and not missing, missing
    rep = gate.evaluate(_cfg(notes=notes, outputs=tmp / "out"), "full", "02_combine_envelope")
    assert rep["passed"], rep


def test_real_repo_prereg_is_frozen_and_keys_set():
    """The committed prereg + config must actually clear the gate (guards against a silent thaw)."""
    rep = gate.evaluate(common.load_config(), "full", "02_combine_envelope")
    assert rep["prereg_frozen"], rep["prereg_reasons"]
    assert rep["config_keys_ok"], rep["missing_keys"]
    assert rep["passed"], rep["reasons"]


def test_unfrozen_prereg_blocks():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    _write_prereg(notes, frozen=False)
    frozen, reasons = gate.prereg_frozen(_cfg(notes=notes))
    assert not frozen and reasons, "unfrozen prereg was not rejected"
    rep = gate.evaluate(_cfg(notes=notes, outputs=tmp / "out"), "full")
    assert not rep["passed"]
    try:
        gate.enforce(_cfg(notes=notes, outputs=tmp / "out"), "full", "02_combine_envelope")
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover
        raise AssertionError("enforce did not block an unfrozen prereg")


def test_missing_prereg_blocks():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "empty_notes"
    notes.mkdir(parents=True, exist_ok=True)
    frozen, reasons = gate.prereg_frozen(_cfg(notes=notes))
    assert not frozen and any("missing" in r for r in reasons), reasons


def test_null_required_key_blocks():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    _write_prereg(notes, frozen=True)
    cfg = _cfg(notes=notes, outputs=tmp / "out", **{"models.quantiles": None})
    ok, missing = gate.config_keys_ok(cfg)
    assert not ok and "models.quantiles" in missing, missing
    rep = gate.evaluate(cfg, "full")
    assert not rep["passed"] and any("models.quantiles" in r for r in rep["reasons"])


# --------------------------------------------------------------------------- #
# game-side audit gate
# --------------------------------------------------------------------------- #
def test_game_side_requires_passing_audit():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    _write_prereg(notes, frozen=True)
    out = tmp / "out"
    cfg = _cfg(notes=notes, outputs=out)

    # no audit decision -> blocked
    rep = gate.evaluate(cfg, "full", requires_game_side=True)
    assert not rep["passed"] and rep["audit_gate_ok"] is False

    # NO-GO / CONDITIONAL -> blocked
    for bad in ("NO-GO — missing field", "CONDITIONAL — thresholds not frozen"):
        _write_decision(out, bad)
        rep = gate.evaluate(cfg, "full", requires_game_side=True)
        assert not rep["passed"], bad

    # an explicit pass -> allowed
    _write_decision(out, "GO — matched WR 106, route snaps OK, fields present, ICC OK")
    rep = gate.evaluate(cfg, "full", requires_game_side=True)
    assert rep["passed"], rep


def test_sample_mode_never_gated():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    _write_prereg(notes, frozen=False)  # even an unfrozen prereg
    cfg = _cfg(notes=notes, outputs=tmp / "out", **{"models.quantiles": None})
    rep = gate.evaluate(cfg, "sample")
    assert rep["passed"], rep
    gate.enforce(cfg, "sample", "02_combine_envelope")  # no-op, must not raise


# --------------------------------------------------------------------------- #
# CLI exit codes
# --------------------------------------------------------------------------- #
def test_cli_exit_codes():
    tmp = Path(tempfile.mkdtemp())
    notes = tmp / "notes"
    _write_prereg(notes, frozen=True)
    ok_cfg = _dump_cfg(_cfg(notes=notes, outputs=tmp / "out"), tmp / "ok.yaml")
    r = _run_cli(["--mode", "full"], ok_cfg)
    assert r.returncode == 0 and "GATE PASS" in r.stdout, r.stdout + r.stderr

    _write_prereg(notes, frozen=False)
    bad_cfg = _dump_cfg(_cfg(notes=notes, outputs=tmp / "out"), tmp / "bad.yaml")
    r = _run_cli(["--mode", "full"], bad_cfg)
    assert r.returncode == 2 and "BLOCKED" in r.stderr, r.stdout + r.stderr


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_frozen_prereg_passes, test_real_repo_prereg_is_frozen_and_keys_set,
             test_unfrozen_prereg_blocks, test_missing_prereg_blocks, test_null_required_key_blocks,
             test_game_side_requires_passing_audit, test_sample_mode_never_gated, test_cli_exit_codes]
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
