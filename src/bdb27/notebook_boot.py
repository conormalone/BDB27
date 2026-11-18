"""Fresh imports for a notebook re-run in a kernel that already imported bdb27.

A `git pull` while a Jupyter kernel is alive changes the files on disk but not the
module objects Python already holds in ``sys.modules``. The notebook's ``sys.path``
guard cannot help: it only chooses where a *first* import looks. This module drops
the cached package and imports the files the resolved checkout holds, then checks
that the symbols the notebook needs are actually there.

Deliberately stdlib-only: the notebook loads this file by path *before* it imports
``bdb27``, so the helper cannot itself be the stale article it exists to fix.
"""

from __future__ import annotations

import datetime
import importlib
import sys
from pathlib import Path

PACKAGE = "bdb27"


# --------------------------------------------------------------------- checkout
def _git_dir(root: Path) -> Path | None:
    """``root/.git`` as a directory, following a worktree ``gitdir:`` file."""
    dot = root / ".git"
    if dot.is_dir():
        return dot
    if dot.is_file():
        try:
            text = dot.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if text.startswith("gitdir:"):
            target = Path(text[len("gitdir:"):].strip())
            return target if target.is_absolute() else (root / target).resolve()
    return None


def _resolve_ref(git_dir: Path, ref: str) -> str | None:
    """The SHA for ``ref`` from the loose ref file or ``packed-refs``."""
    try:
        return (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:
        packed = (git_dir / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in packed.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "^")):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return parts[0]
    return None


def checkout_label(root: str | Path) -> str:
    """``branch @ short-sha`` read from ``.git``, never from a subprocess."""
    root = Path(root).resolve()
    git_dir = _git_dir(root)
    if git_dir is None:
        return "unknown (no readable .git at %s)" % (root / ".git")
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown (no readable .git/HEAD)"
    if head.startswith("ref:"):
        ref = head[len("ref:"):].strip()
        sha = _resolve_ref(git_dir, ref)
        branch = ref.rsplit("/", 1)[-1]
        return f"{branch} @ {sha[:12]}" if sha else branch
    return f"detached @ {head[:12]}"


def _mtime(path: Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(
            path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return "unknown"


# ----------------------------------------------------------------------- import
def fresh_import(src_dir: str | Path, names, root: str | Path | None = None,
                 package: str = PACKAGE, verbose: bool = True) -> dict:
    """Drop every cached ``package`` module, import ``names`` from ``src_dir``.

    ``src_dir`` is placed at the front of ``sys.path``. Returns
    ``{name: module}`` in the order requested. Prints what it loaded: the
    resolved file and mtime of each module, the checkout label, and how many
    cached modules it dropped.
    """
    src_dir = Path(src_dir).resolve()
    src = str(src_dir)
    while src in sys.path:
        sys.path.remove(src)
    sys.path.insert(0, src)
    importlib.invalidate_caches()

    dropped = sorted(m for m in list(sys.modules)
                     if m == package or m.startswith(package + "."))
    for name in dropped:
        del sys.modules[name]

    modules = {}
    for name in names:
        modules[name] = importlib.import_module(f"{package}.{name}")

    if verbose:
        print("loaded       (from the files on disk, every Run All)")
        for name in names:
            path = Path(modules[name].__file__).resolve()
            print(f"  {package}.{name}  ->  {path}  (modified {_mtime(path)})")
        label = checkout_label(root) if root is not None else "not given"
        print(f"checkout     {label}"
              + (f"  ({Path(root).resolve()})" if root is not None else ""))
        if dropped:
            print(f"reloaded     dropped {len(dropped)} cached module(s); "
                  f"imported {len(names)} from {src}")
        else:
            print(f"reloaded     nothing was cached in this kernel; imported "
                  f"{len(names)} from {src}")
    return modules


# ---------------------------------------------------------------------- symbols
def missing_symbols(modules: dict, required: dict, package: str = PACKAGE) -> list:
    """``['bdb27.mod.attr', ...]`` for every required attribute not present."""
    missing = []
    for name, attrs in required.items():
        module = modules[name]
        for attr in attrs:
            if not hasattr(module, attr):
                missing.append(f"{package}.{name}.{attr}")
    return missing


def require_symbols(modules: dict, required: dict, src_dir: str | Path | None = None,
                    root: str | Path | None = None, package: str = PACKAGE) -> None:
    """Stop in plain English if the loaded files are older than the notebook.

    The message names the file that was loaded, the symbol(s) it lacks, and the
    checkout, so the failure says "pull the repo" rather than
    ``AttributeError: module ... has no attribute ...``.
    """
    missing = missing_symbols(modules, required, package=package)
    outside = []
    if src_dir is not None:
        src_dir = Path(src_dir).resolve()
        for name in required:
            path = Path(getattr(modules[name], "__file__", "")).resolve()
            if src_dir not in path.parents:
                outside.append(f"{package}.{name} <- {path}")
    if not missing and not outside:
        print("import check  every symbol the notebook uses is present")
        return

    lines = ["IMPORT CHECK FAILED — the notebook cannot run against these files."]
    lines.append("loaded:")
    for name in required:
        lines.append(f"  {package}.{name} <- "
                     f"{Path(getattr(modules[name], '__file__', '?')).resolve()}")
    if root is not None:
        lines.append(f"checkout: {checkout_label(root)}  "
                     f"({Path(root).resolve()})")
    if missing:
        lines.append("missing: " + ", ".join(missing))
        lines.append("This checkout is OLDER THAN THE NOTEBOOK: the notebook asks "
                     "for code the files above do not contain.")
        lines.append("Pull the latest main in this folder, then press Run All again.")
    if outside:
        lines.append("these modules did not come from this checkout's src/: "
                     + "; ".join(outside))
        lines.append("The notebook is running against another copy of bdb27. "
                     "Close Jupyter, open the notebook in this checkout, and "
                     "press Run All.")
    message = "\n".join(lines)
    print(message)
    raise RuntimeError(message)
