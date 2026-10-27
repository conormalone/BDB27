#!/usr/bin/env python3
"""Behaviour tests for cross-platform portability of `src/common.py`.

Runnable **without pytest** (`python src/tests/test_common_portability.py`) and collected by
pytest (functions named `test_*`). Regression coverage for the Windows bug report
(2026-10-13): `import resource` was a MODULE-LEVEL import of a POSIX-only stdlib module, so
`import common` — and therefore the whole deliverable notebook — crashed on Windows.

Covers:
  * importing `common` (and running a whole stage) succeeds even when `resource` is
    **unimportable** (simulated non-POSIX host via an import blocker);
  * `peak_ram_mb()` never raises and degrades to the documented sentinel
    (`common.RAM_UNKNOWN_MB == -1.0`) when no probe is usable;
  * the `run_log.csv` schema is preserved EXACTLY (column names/order) and a stage logs a
    numeric `peak_ram_mb` even on the non-POSIX path;
  * platform-specific `ru_maxrss` unit handling (Linux = KiB, macOS/BSD = bytes).
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402

EXPECTED_RUN_LOG_FIELDS = ["timestamp", "stage", "mode", "wall_s", "peak_ram_mb", "error", "notes"]

# An import blocker that makes `import resource` raise, simulating a non-POSIX host.
_BLOCKER_SRC = """
import sys


class _BlockResource:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "resource":
            raise ModuleNotFoundError("No module named 'resource' (simulated non-POSIX host)")
        return None


sys.meta_path.insert(0, _BlockResource())
for _m in [m for m in list(sys.modules) if m == "resource" or m.startswith("resource.")]:
    del sys.modules[_m]
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _blocker_dir(tmp: Path) -> Path:
    """Write a `sitecustomize.py` that blocks `import resource`; return its dir."""
    d = tmp / "site"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sitecustomize.py").write_text(_BLOCKER_SRC, encoding="utf-8")
    return d


def _cfg(out: Path, run_log: Path, notes: Path) -> Path:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    cfg["paths"]["outputs"] = str(out)
    cfg["paths"]["notes"] = str(notes)
    # absolute path => common.REPO_ROOT / path == path (Path join resets on absolute)
    cfg["logging"]["run_log"] = str(run_log)
    p = out.parent / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _env_with_blocker(blocker: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{blocker}{os.pathsep}{REPO / 'src'}{os.pathsep}" + env.get("PYTHONPATH", "")
    return env


# --------------------------------------------------------------------------- #
# 1. import survives a missing POSIX `resource` module
# --------------------------------------------------------------------------- #
def test_import_common_survives_missing_resource():
    tmp = Path(tempfile.mkdtemp())
    blocker = _blocker_dir(tmp)
    code = (
        "import common, sys\n"
        "assert common._resource is None, 'resource should be blocked'\n"
        "assert 'resource' not in sys.modules\n"
        "v = common.peak_ram_mb()\n"
        "assert isinstance(v, float), type(v)\n"
        "assert v == common.RAM_UNKNOWN_MB, v\n"  # no fallback usable on this sim host
        "print('OK', v)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True, env=_env_with_blocker(blocker))
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "ModuleNotFoundError" not in r.stderr, r.stderr


# --------------------------------------------------------------------------- #
# 2. a real stage runs end-to-end and preserves the run_log schema
# --------------------------------------------------------------------------- #
def test_stage_runs_without_resource_and_logs_sentinel():
    import csv

    tmp = Path(tempfile.mkdtemp())
    blocker = _blocker_dir(tmp)
    out, notes = tmp / "out", tmp / "notes"
    run_log = out / "run_log.csv"
    notes.mkdir(parents=True, exist_ok=True)
    cfg = _cfg(out, run_log, notes)
    stage = REPO / "src" / "00_thresholds.py"
    r = subprocess.run(
        [sys.executable, str(stage), "--synthetic", "--synthetic-regime", "binding",
         "--config", str(cfg)],
        cwd=str(REPO), capture_output=True, text=True, env=_env_with_blocker(blocker),
    )
    assert r.returncode == 0, f"stage failed without resource:\n{r.stdout}\n{r.stderr}"
    assert run_log.exists(), "stage did not write run_log.csv"
    with open(run_log, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fh.seek(0)
        header = fh.readline().strip().split(",")
    assert header == EXPECTED_RUN_LOG_FIELDS, header
    assert rows, "no run-log rows written"
    last = rows[-1]
    assert set(last.keys()) == set(EXPECTED_RUN_LOG_FIELDS)
    float(last["peak_ram_mb"])  # must stay a numeric cell (sentinel, never a crash)
    assert last["error"] == "", last  # a stage must never fail on memory measurement


# --------------------------------------------------------------------------- #
# 3. peak_ram_mb never raises / degrades to the documented sentinel
# --------------------------------------------------------------------------- #
def test_peak_ram_sentinel_when_no_probe_usable():
    saved = (common._resource, common._peak_ram_mb_posix, common._peak_ram_mb_windows)
    try:
        common._resource = None
        common._peak_ram_mb_posix = lambda: None
        common._peak_ram_mb_windows = lambda: None
        assert common.peak_ram_mb() == common.RAM_UNKNOWN_MB
        assert common.RAM_UNKNOWN_MB < 0.0  # documented "unknown" sentinel, not a real 0
    finally:
        common._resource, common._peak_ram_mb_posix, common._peak_ram_mb_windows = saved
    # restored: real measurement works again on this (POSIX) host
    assert common.peak_ram_mb() > 0.0


# --------------------------------------------------------------------------- #
# 4. platform-specific ru_maxrss units (Linux KiB vs macOS bytes)
# --------------------------------------------------------------------------- #
def test_ru_maxrss_unit_handling_linux_vs_darwin():
    class _FakeUsage:
        ru_maxrss = 2_000_000  # Linux: KiB -> 1953.125 MiB ; macOS: bytes -> 1.907... MiB

    class _FakeResource:
        RUSAGE_SELF = 0

        @staticmethod
        def getrusage(_who):
            return _FakeUsage()

    saved_resource, saved_platform = common._resource, sys.platform
    try:
        common._resource = _FakeResource()
        sys.platform = "linux"
        assert abs(common._peak_ram_mb_posix() - 2_000_000 / 1024.0) < 1e-6
        sys.platform = "darwin"
        assert abs(common._peak_ram_mb_posix() - 2_000_000 / (1024.0 * 1024.0)) < 1e-9
    finally:
        common._resource, sys.platform = saved_resource, saved_platform


# --------------------------------------------------------------------------- #
# 5. run_log schema names/order are frozen
# --------------------------------------------------------------------------- #
def test_run_log_schema_is_frozen():
    # append_run_log hard-codes the header; assert it matches the documented schema exactly.
    tmp = Path(tempfile.mkdtemp())
    log = tmp / "run_log.csv"
    cfg = {"logging": {"run_log": str(log)}}
    common.append_run_log(cfg, "stage", "sample", 1.0, common.peak_ram_mb(), "", {"k": "v"})
    header = log.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == EXPECTED_RUN_LOG_FIELDS, header


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [test_import_common_survives_missing_resource,
             test_stage_runs_without_resource_and_logs_sentinel,
             test_peak_ram_sentinel_when_no_probe_usable,
             test_ru_maxrss_unit_handling_linux_vs_darwin,
             test_run_log_schema_is_frozen]
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
