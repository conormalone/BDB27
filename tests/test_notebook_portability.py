"""The notebook must run on Windows, not only on Linux (D184).

Windows is a supported host: Conor runs `notebooks/bdb27_full_game_turns.ipynb` on his
laptop. `resource` is a Unix-only stdlib module, so a bare `import resource` is a crash
there — after the expensive detection run. These tests are static and need no data: the
memory probe may *use* `resource`, but only behind a guard, and it must degrade to
``None`` (an honest null), never to a fabricated number and never to an exception.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_full_game_turns.ipynb"
COMMON = ROOT / "src" / "bdb27" / "common.py"


def code_cells() -> list[str]:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def unguarded_resource_imports(source: str, label: str) -> list[str]:
    """Every `import resource` / `from resource import ...` that is not inside a try."""
    tree = ast.parse(source, filename=label)
    offenders: list[str] = []

    def visit(node: ast.AST, in_try: bool) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _is_resource(node):
            if not in_try:
                offenders.append(f"{label}:{node.lineno}")
            return
        nested = in_try or isinstance(node, ast.Try)
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(tree, False)
    return offenders


def _is_resource(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "resource" for alias in node.names)
    return isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "resource"


def test_no_bare_resource_import_in_any_notebook_cell():
    offenders = []
    for i, source in enumerate(code_cells()):
        offenders += unguarded_resource_imports(source, f"cell {i}")
    assert not offenders, f"bare `import resource` outside a try — crashes on Windows: {offenders}"


def test_no_bare_resource_import_in_common_module():
    offenders = unguarded_resource_imports(COMMON.read_text(encoding="utf-8"), "common.py")
    assert not offenders, f"bare `import resource` outside a try: {offenders}"


def test_psutil_is_never_installed_by_the_notebook():
    """psutil is an optional fallback, never a new requirement."""
    for i, source in enumerate(code_cells()):
        for line in source.splitlines():
            if "psutil" in line and ("pip" in line or "install" in line):
                pytest.fail(f"cell {i} tries to install psutil: {line.strip()}")


def _notebook_probe():
    """The notebook's own `peak_rss_mb` function, lifted out of its cell."""
    for source in code_cells():
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "peak_rss_mb":
                namespace: dict = {"resource": None}
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             "<notebook probe>", "exec"), namespace)  # noqa: S102
                return namespace["peak_rss_mb"]
    pytest.fail("the notebook no longer defines peak_rss_mb()")


@pytest.fixture()
def no_psutil(monkeypatch):
    """`import psutil` fails, as on a Windows machine that never installed it."""
    monkeypatch.setitem(sys.modules, "psutil", None)


def test_notebook_probe_returns_null_when_nothing_is_measurable(no_psutil):
    value, method = _notebook_probe()()
    assert value is None
    assert "unavailable" in method


def test_notebook_probe_uses_psutil_when_it_is_present():
    if importlib.util.find_spec("psutil") is None:
        pytest.skip("psutil not installed on this host")
    value, method = _notebook_probe()()
    assert isinstance(value, float) and value > 0
    assert "psutil" in method


def test_common_peak_rss_is_null_not_an_exception_without_resource(no_psutil, monkeypatch):
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from bdb27 import common
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(common, "resource", None)
    assert common.peak_rss_mb() is None


def test_common_peak_rss_measures_on_this_host():
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from bdb27 import common
    finally:
        sys.path.pop(0)
    if common.resource is None:
        pytest.skip("no `resource` module on this host (Windows)")
    assert common.peak_rss_mb() > 0.0
