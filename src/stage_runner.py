#!/usr/bin/env python3
"""stage_runner.py — run one pipeline stage in the notebook's kernel, always from the code on disk.

Why this module exists (D46, 2026-10-18). The notebook's ``run_stage()`` used to do
``importlib.import_module(module_name)``. In a long-lived Jupyter kernel that returns the module
object **already cached in ``sys.modules``**, so after a ``git pull`` the cell re-executed the
*pre-pull* code objects. The checkout guard did not catch it because the guard reads the files on
disk, which were new. The result was a loop that looked impossible: the same ``OverflowError``
recurred verbatim after every pull, and ``b_primary.csv`` was never written.

The diagnostic signature (worth remembering — it is the general tell for "this kernel is running
old code"): **a traceback whose frames cannot be reconciled with the current source.** Line numbers
come from the fresh file, while the executed code objects are older, so a frame reports e.g.
``main()`` at a line that is not a call at all, or ``run_e(...)`` at a line that is a ``return``
inside a different function. If the printed frames do not form a legal call chain in the file you
are reading, the kernel is stale — not the source.

Contract: every call to :func:`run_stage` invalidates the import caches and reloads every pipeline
module already in ``sys.modules`` (helpers first, then the stage), so re-running any stage cell
always executes the code on disk. Nothing here caches state between calls.

Stdlib only, like ``run_diagnostics``.
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import os
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:            # so the numeric stage names import via importlib
    sys.path.insert(0, _SRC_DIR)

import run_diagnostics  # noqa: E402  (same directory; stdlib-only, no cycle)

# Helper modules first: a stage that did `from common import load_config` re-binds that name when
# the *stage* is re-executed, so the helpers must be reloaded before it.
HELPER_MODULES: tuple[str, ...] = (
    "common",
    "gate",
    "model_features",
    "security_features",
    "combine_features",
    "game_features",
)

STAGE_MODULES: tuple[str, ...] = (
    "00_thresholds",
    "01_audit",
    "02_combine_envelope",
    "00b_external_proxies",
    "03_game_demand",
    "06_security",
    "07_security_models",
    "08_route_robustness",
    "09_draft_and_target",
    "04_models",
    "05_validate",
)

#: Every module the notebook's pipeline touches, helpers before stages (reload order).
PIPELINE_MODULES: tuple[str, ...] = HELPER_MODULES + STAGE_MODULES

_CRASH_PREFIX = "crash_"


# --------------------------------------------------------------------------- #
# module provenance (what is loaded vs. what is on disk)
# --------------------------------------------------------------------------- #
def module_provenance(name: str, repo_root: str | Path | None = None,
                      sys_modules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe the *loaded* module object: its file, its mtime, and that file's last commit.

    ``loaded=False`` means the name is not in ``sys.modules`` yet, which is the healthy state at
    bootstrap in a fresh kernel.
    """
    mods = sys.modules if sys_modules is None else sys_modules
    info: dict[str, Any] = dict(name=name, loaded=False, path=None, mtime=None, mtime_str="",
                                commit="", error="")
    mod = mods.get(name)
    if mod is None:
        return info
    info["loaded"] = True
    path = getattr(mod, "__file__", None)
    if not path:
        info["error"] = "module has no __file__ (namespace/builtin)"
        return info
    p = Path(path)
    info["path"] = str(p)
    try:
        mtime = p.stat().st_mtime
        info["mtime"] = mtime
        info["mtime_str"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError as exc:
        info["error"] = f"stat failed: {type(exc).__name__}: {exc}"
    root = Path(repo_root).resolve() if repo_root is not None else p.parent.parent
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = None
    if rel is not None:
        info["commit"] = run_diagnostics.file_commit(root, str(rel), cache=True)
    return info


def preloaded_modules(modules: tuple[str, ...] = PIPELINE_MODULES,
                      sys_modules: dict[str, Any] | None = None) -> list[str]:
    """Which pipeline modules are already in ``sys.modules`` (i.e. cached in this kernel)."""
    mods = sys.modules if sys_modules is None else sys_modules
    return [m for m in modules if m in mods]


def code_state_report(repo_root: str | Path | None = None,
                      modules: tuple[str, ...] = PIPELINE_MODULES,
                      sys_modules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bootstrap-time verdict: are any pipeline modules preloaded, and where do they come from?

    Returns ``{"preloaded": [...], "table": [...], "warnings": [...]}``. ``table`` carries one line
    per preloaded module with its file, mtime and last commit, so "which code is this kernel
    actually holding?" is answerable from the notebook output alone.
    """
    pre = preloaded_modules(modules, sys_modules)
    table: list[dict[str, Any]] = []
    warnings: list[str] = []
    for name in pre:
        prov = module_provenance(name, repo_root, sys_modules)
        table.append(prov)
        where = prov["path"] or prov["error"] or "unknown file"
        warnings.append(f"{name:<20} {where}  mtime {prov['mtime_str'] or '?'}  "
                        f"commit {prov['commit'] or 'unknown'}")
    return dict(preloaded=pre, table=table, warnings=warnings)


# --------------------------------------------------------------------------- #
# loading: always from disk
# --------------------------------------------------------------------------- #
def _purge_bytecode(name: str, sys_modules: dict[str, Any] | None = None) -> None:
    """Delete the cached ``.pyc`` for ``name`` so the reload must recompile from source.

    ``reload`` alone trusts the ``.pyc`` header (source mtime + size), and both can survive a
    pull or a same-second rewrite — the exact trap this module exists to close. Purging is cheap
    and makes "the code on disk" literally true.
    """
    mods = sys.modules if sys_modules is None else sys_modules
    mod = mods.get(name)
    path = getattr(mod, "__file__", None) if mod is not None else None
    if not path or not str(path).endswith(".py"):
        return
    try:
        cached = importlib.util.cache_from_source(str(path))
    except (NotImplementedError, ValueError):
        return
    for candidate in (cached,):
        try:
            os.unlink(candidate)
        except OSError:
            pass


def reload_pipeline_modules(sys_modules: dict[str, Any] | None = None,
                            modules: tuple[str, ...] = PIPELINE_MODULES) -> list[str]:
    """Reload every module in ``modules`` that is already loaded. Returns the names reloaded.

    A module that fails to reload is dropped from ``sys.modules`` rather than left half-initialised
    (a syntax error in the new file must surface as that error, not as stale code).
    """
    mods = sys.modules if sys_modules is None else sys_modules
    importlib.invalidate_caches()
    done: list[str] = []
    for name in modules:
        if name not in mods:
            continue
        try:
            _purge_bytecode(name, mods)
            importlib.reload(mods[name])
        except BaseException:
            mods.pop(name, None)
            raise
        done.append(name)
    return done


def load_stage_module(module_name: str,
                      sys_modules: dict[str, Any] | None = None,
                      helpers: tuple[str, ...] = HELPER_MODULES) -> tuple[Any, str, list[str]]:
    """Import ``module_name`` from disk, reloading it and its already-loaded helpers first.

    Returns ``(module, action, reloaded)`` where ``action`` is ``"reloaded"`` when the stage module
    was already cached (the D46 case) or ``"imported"`` otherwise.
    """
    mods = sys.modules if sys_modules is None else sys_modules
    reloaded = reload_pipeline_modules(mods, helpers)
    importlib.invalidate_caches()
    if module_name in mods:
        try:
            _purge_bytecode(module_name, mods)
            mod = importlib.reload(mods[module_name])
            return mod, "reloaded", reloaded
        except BaseException:
            mods.pop(module_name, None)
            raise
    return importlib.import_module(module_name), "imported", reloaded


# --------------------------------------------------------------------------- #
# crash reports (a traceback must never be lost again)
# --------------------------------------------------------------------------- #
def crash_report_path(stage: str, outputs_root: str | Path, when: datetime | None = None) -> Path:
    """``outputs/<stage_subdir>/crash_<YYYYmmdd-HHMMSS>.txt`` (never overwrites; adds ``-2`` …)."""
    sub = run_diagnostics.STAGE_OUTPUTS.get(stage, ("", ()))[0]
    out = Path(outputs_root) / sub if sub else Path(outputs_root)
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = out / f"{_CRASH_PREFIX}{stamp}.txt"
    n = 1
    while path.exists():
        n += 1
        path = out / f"{_CRASH_PREFIX}{stamp}-{n}.txt"
    return path


def write_crash_report(stage: str, tb_text: str, outputs_root: str | Path,
                       exc: BaseException | None = None,
                       when: datetime | None = None,
                       sys_modules: dict[str, Any] | None = None) -> Path:
    """Write the FULL traceback for a crashed stage and return the path.

    The file lives next to the stage's own outputs but is named ``crash_*`` so it is never mistaken
    for a result artefact (and it is excluded from every results listing).
    """
    path = crash_report_path(stage, outputs_root, when)
    path.parent.mkdir(parents=True, exist_ok=True)
    prov = module_provenance(stage, None, sys_modules)
    header = [
        f"# BDB27 stage crash — {stage}",
        f"written          : {datetime.now().isoformat(timespec='seconds')}",
        f"stage module     : {prov['path'] or 'unknown'}",
        f"module loaded    : {'reloaded from disk' if prov['loaded'] else 'not in sys.modules'}",
        f"module mtime     : {prov['mtime_str'] or 'unknown'}",
        f"module commit    : {prov['commit'] or 'unknown'}",
        f"exception        : {type(exc).__name__ + ': ' + str(exc) if exc is not None else 'unknown'}",
        f"python           : {sys.version.split()[0]}",
        f"cwd              : {Path.cwd()}",
        "",
        "--- full traceback ---",
        tb_text.rstrip() or "(no traceback text was produced)",
        "",
    ]
    path.write_text("\n".join(header), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #
def run_stage(module_name: str, extra_args: list[Any] | tuple[Any, ...],
              outputs_root: str | Path | None = None,
              sys_modules: dict[str, Any] | None = None,
              clock: Any = None,
              helpers: tuple[str, ...] = HELPER_MODULES) -> dict[str, Any]:
    """Run one ``src/`` stage in-process via its own ``main()``. Returns a status dict.

    The module is reloaded from disk on every call (see the module docstring). A stage exception
    writes ``outputs/<stage_subdir>/crash_<stamp>.txt`` with the full traceback; the returned dict
    carries ``crash_file`` so the caller can print the path even when the captured tail is empty.
    """
    t0 = (clock or time.time)()
    old = sys.argv
    buf = io.StringIO()
    rc, err, tb_text, crash_file = 0, "", "", None
    action, reloaded = "unknown", []
    try:
        # loading is inside the guard too: a SyntaxError in the freshly-pulled file is a stage
        # crash (with a crash file), not a dead notebook with no traceback.
        mod, action, reloaded = load_stage_module(module_name, sys_modules, helpers)
        sys.argv = [f"{module_name}.py", *[str(a) for a in extra_args]]
        with redirect_stdout(buf), redirect_stderr(buf):
            mod.main()
    except SystemExit as e:
        rc = 0 if e.code in (None, 0) else int(e.code)
    except BaseException as e:                      # noqa: BLE001 - the stage owns the failure
        rc, err = 1, f"{type(e).__name__}: {e}"
        tb_text = traceback.format_exc()
        traceback.print_exc(file=buf)               # keep it in the captured tail too
        if outputs_root is not None:
            try:
                crash_file = str(write_crash_report(module_name, tb_text, outputs_root, e,
                                                    sys_modules=sys_modules))
            except OSError as werr:                 # a read-only outputs dir must not mask the crash
                err += f" (crash report not written: {type(werr).__name__}: {werr})"
    finally:
        sys.argv = old
    tail = [ln for ln in buf.getvalue().strip().splitlines() if ln.strip()][-10:]
    return {"stage": module_name, "ok": rc == 0, "rc": rc,
            "seconds": round((clock or time.time)() - t0, 2), "error": err, "tail": tail,
            "module_action": action, "reloaded": reloaded,
            "crash_file": crash_file, "traceback": tb_text}
