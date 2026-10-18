#!/usr/bin/env python3
"""Regression tests for ``src/stage_runner.py`` — D46, "the kernel ran old code".

The defect: after a ``git pull``, re-running the notebook's §3 stage cell re-executed the module
object cached in ``sys.modules``, so the pre-pull code ran while the checkout guard (which reads
files) passed. Three promises are locked in here:

(a) **stale module** — a module imported, then rewritten on disk, is re-executed from the new
    source by ``run_stage`` (including the same-size / same-second rewrite that a ``.pyc`` header
    would wrongly accept);
(b) **crash file** — a stage exception writes ``outputs/<stage_subdir>/crash_<stamp>.txt`` with the
    full traceback, and ``failure_lines`` prints that path (also when the captured tail is empty);
(c) **preload warning** — ``code_state_report`` flags pipeline modules already in ``sys.modules``
    and reports each one's file, mtime and last commit.

Runnable without pytest: ``python src/tests/test_stage_runner.py``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import run_diagnostics as rd      # noqa: E402
import stage_runner as sr         # noqa: E402

MOD_MAIN_V1 = "VERSION = 'v1'\n\n\ndef main():\n    print('probe reports', VERSION)\n"
MOD_MAIN_V2 = "VERSION = 'v2'\n\n\ndef main():\n    print('probe reports', VERSION)\n"
MOD_BOOM = "def main():\n    raise RuntimeError('probe boom')\n"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _cleanup(name: str, tmp: Path) -> None:
    sys.modules.pop(name, None)
    if str(tmp) in sys.path:
        sys.path.remove(str(tmp))


# --------------------------------------------------------------------------- #
# (a) the stale-module reproduction
# --------------------------------------------------------------------------- #
def test_run_stage_reloads_a_module_whose_source_changed_on_disk(tmp_path):
    """Import -> rewrite the file -> run again: the NEW code must execute (D46's core claim)."""
    name = "d46_stale_probe"
    src = tmp_path / f"{name}.py"
    _write(src, MOD_MAIN_V1)
    sys.path.insert(0, str(tmp_path))
    try:
        first = sr.run_stage(name, [])
        assert first["ok"], first
        assert first["module_action"] == "imported", first
        assert any("probe reports v1" in ln for ln in first["tail"]), first["tail"]

        # Same byte length, written in the same second: a plain import/reload can consider the
        # cached .pyc still valid here, which is exactly how a pull can look "applied" but not be.
        _write(src, MOD_MAIN_V2)
        second = sr.run_stage(name, [])
        assert second["ok"], second
        assert second["module_action"] == "reloaded", second
        assert any("probe reports v2" in ln for ln in second["tail"]), second["tail"]
        assert not any("probe reports v1" in ln for ln in second["tail"]), second["tail"]
        assert sys.modules[name].VERSION == "v2"
    finally:
        _cleanup(name, tmp_path)


def test_stage_sees_the_new_helper_when_the_helper_changed_on_disk(tmp_path):
    """Reload order matters: a stage's `from helper import x` must re-bind the NEW helper."""
    helper = tmp_path / "d46_helper_probe.py"
    stage = tmp_path / "d46_helper_stage_probe.py"
    helper.write_text("LABEL = 'old-helper'\n", encoding="utf-8")
    stage.write_text("from d46_helper_probe import LABEL\n\n\n"
                     "def main():\n    print('helper says', LABEL)\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    helpers = ("d46_helper_probe",)
    try:
        first = sr.run_stage("d46_helper_stage_probe", [], helpers=helpers)
        assert any("helper says old-helper" in ln for ln in first["tail"]), first["tail"]

        helper.write_text("LABEL = 'new-helper'\n", encoding="utf-8")
        second = sr.run_stage("d46_helper_stage_probe", [], helpers=helpers)
        assert any("helper says new-helper" in ln for ln in second["tail"]), second["tail"]
        assert "d46_helper_probe" in second["reloaded"], second["reloaded"]
    finally:
        for name in ("d46_helper_stage_probe", "d46_helper_probe"):
            sys.modules.pop(name, None)
        sys.path.remove(str(tmp_path))


def test_reload_pipeline_modules_returns_only_what_was_loaded(tmp_path):
    """A helper that is not loaded must not be imported as a side effect of the reload pass."""
    name = "d46_reload_probe"
    src = tmp_path / f"{name}.py"
    _write(src, "VALUE = 1\n")
    sys.path.insert(0, str(tmp_path))
    try:
        import importlib
        mod = importlib.import_module(name)
        mods = {name: mod}
        done = sr.reload_pipeline_modules(mods, (name, "never_imported_helper"))
        assert done == [name], done
        assert "never_imported_helper" not in sys.modules
    finally:
        _cleanup(name, tmp_path)


# --------------------------------------------------------------------------- #
# (b) the crash file
# --------------------------------------------------------------------------- #
def test_crash_writes_full_traceback_into_the_stage_subdir_and_prints_the_path(tmp_path):
    name = "d46_crash_probe"
    src = tmp_path / f"{name}.py"
    _write(src, MOD_BOOM)
    outputs = tmp_path / "outputs"
    sys.path.insert(0, str(tmp_path))
    try:
        res = sr.run_stage(name, [], outputs_root=outputs)
    finally:
        _cleanup(name, tmp_path)

    assert res["ok"] is False, res
    assert res["error"] == "RuntimeError: probe boom", res["error"]
    crash = Path(res["crash_file"] or "")
    assert crash.exists(), f"no crash file written: {res}"
    assert crash.name.startswith("crash_") and crash.suffix == ".txt", crash.name
    text = crash.read_text(encoding="utf-8")
    assert "RuntimeError: probe boom" in text
    assert "--- full traceback ---" in text and "Traceback (most recent call last)" in text

    lines = rd.failure_lines(res)
    assert any(str(crash) in ln for ln in lines), lines


def test_syntax_error_in_the_pulled_source_is_a_crash_not_a_dead_notebook(tmp_path):
    """A bad pull must surface as a stage crash with a crash file, not kill the §3 loop silently."""
    name = "d46_syntax_probe"
    src = tmp_path / f"{name}.py"
    _write(src, MOD_MAIN_V1)
    sys.path.insert(0, str(tmp_path))
    try:
        assert sr.run_stage(name, [])["ok"] is True
        _write(src, "def main(:\n    pass\n")
        res = sr.run_stage(name, [], outputs_root=tmp_path / "outputs")
    finally:
        _cleanup(name, tmp_path)
    assert res["ok"] is False, res
    assert res["error"].startswith("SyntaxError"), res["error"]
    crash = Path(res["crash_file"] or "")
    assert crash.exists() and "SyntaxError" in crash.read_text(encoding="utf-8")


def test_crash_file_lands_in_the_stage_subdir_for_a_real_stage(tmp_path):
    """Stage-name mapping is honoured: a 07 crash belongs in outputs/security_models/."""
    sub, _ = rd.STAGE_OUTPUTS["07_security_models"]
    path = sr.crash_report_path("07_security_models", tmp_path / "outputs")
    assert path == tmp_path / "outputs" / sub / path.name, path
    assert path.parent.name == "security_models", path

    # And a real crash report written for that stage name lands there too.
    written = sr.write_crash_report("07_security_models", "Traceback: fake", tmp_path / "outputs")
    assert written.parent.name == "security_models"
    assert "module loaded" in written.read_text(encoding="utf-8")


def test_failure_lines_never_ends_on_a_bare_traceback_tail(tmp_path):
    crash = tmp_path / "outputs" / "security_models" / "crash_20261018-211600.txt"
    empty_tail = {"stage": "07_security_models", "ok": False, "rc": 1,
                  "error": "OverflowError: math range error", "tail": [],
                  "crash_file": str(crash)}
    lines = rd.failure_lines(empty_tail)
    assert lines[0] == "crashed: OverflowError: math range error", lines
    assert f"traceback tail: (empty — see {crash})" in lines, lines
    assert "" not in lines, lines

    with_tail = dict(empty_tail, tail=["line one", "line two"])
    lines = rd.failure_lines(with_tail)
    assert "traceback tail:" in lines and "line one" in lines and "line two" in lines, lines
    assert any(ln.startswith("full traceback: ") for ln in lines), lines


# --------------------------------------------------------------------------- #
# (c) the bootstrap warning
# --------------------------------------------------------------------------- #
def test_code_state_report_flags_preloaded_modules_with_file_mtime_and_commit():
    real = REPO / "src" / "07_security_models.py"
    fake = types.ModuleType("07_security_models")
    fake.__file__ = str(real)
    mods = {"07_security_models": fake}
    rep = sr.code_state_report(REPO, modules=("07_security_models", "common"), sys_modules=mods)
    assert rep["preloaded"] == ["07_security_models"], rep["preloaded"]
    assert len(rep["warnings"]) == 1
    line = rep["warnings"][0]
    assert str(real) in line and "mtime" in line and "commit" in line, line
    assert rep["table"][0]["loaded"] is True
    assert rep["table"][0]["mtime_str"], rep["table"][0]


def test_code_state_report_is_silent_when_nothing_is_preloaded():
    rep = sr.code_state_report(REPO, modules=("common", "05_validate"), sys_modules={})
    assert rep["preloaded"] == [] and rep["warnings"] == [] and rep["table"] == []


def test_pipeline_module_list_covers_every_documented_module():
    expected = {"01_audit", "02_combine_envelope", "06_security", "00_thresholds",
                "03_game_demand", "07_security_models", "00b_external_proxies",
                "04_models", "05_validate", "common", "gate", "model_features",
                "security_features"}
    assert expected <= set(sr.PIPELINE_MODULES), sorted(expected - set(sr.PIPELINE_MODULES))
    # helpers must precede stages so a stage's `from common import x` re-binds fresh objects
    assert list(sr.PIPELINE_MODULES) == list(sr.HELPER_MODULES) + list(sr.STAGE_MODULES)


if __name__ == "__main__":
    import tempfile

    failures = []
    for fn in (
        test_code_state_report_is_silent_when_nothing_is_preloaded,
        test_pipeline_module_list_covers_every_documented_module,
        test_code_state_report_flags_preloaded_modules_with_file_mtime_and_commit,
    ):
        try:
            fn()
        except AssertionError as exc:  # pragma: no cover - manual runner
            failures.append((fn.__name__, exc))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for fn in (
            test_run_stage_reloads_a_module_whose_source_changed_on_disk,
            test_stage_sees_the_new_helper_when_the_helper_changed_on_disk,
            test_reload_pipeline_modules_returns_only_what_was_loaded,
            test_crash_writes_full_traceback_into_the_stage_subdir_and_prints_the_path,
            test_syntax_error_in_the_pulled_source_is_a_crash_not_a_dead_notebook,
            test_crash_file_lands_in_the_stage_subdir_for_a_real_stage,
            test_failure_lines_never_ends_on_a_bare_traceback_tail,
        ):
            try:
                fn(tmp)
            except AssertionError as exc:  # pragma: no cover
                failures.append((fn.__name__, exc))
    for name, exc in failures:
        print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
