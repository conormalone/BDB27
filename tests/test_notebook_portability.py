"""The notebook must run on Windows, not only on Linux (D184).

Windows is a *supported* host: Conor runs `notebooks/bdb27_full_game_turns.ipynb` on his
laptop, so the notebook carries no POSIX-only code and no platform branches at all. These
tests are static (no data needed) except the installer, which is exercised in memory:

* no memory probe and no `resource`/`psutil` anywhere in the notebook;
* every text read/write passes ``encoding="utf-8"`` — the band labels contain `°` and `–`,
  and Windows' default code page is not UTF-8;
* every filesystem path interpolated into DuckDB SQL is ``.as_posix()``, never a native
  (backslash) path;
* nothing is pip-installed unless §1's ``INSTALL_MISSING`` is True.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_full_game_turns.ipynb"
COMMON = ROOT / "src" / "bdb27" / "common.py"

ENCODED_CALLS = {"to_csv", "read_csv", "read_text", "write_text"}
#: an f-string that puts a filesystem path inside a SQL string literal
SQL_PATH = re.compile(r"(?:read_csv_auto|read_parquet|read_json_auto)\('\{|TO '\{")


def code_cells() -> list[str]:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_binary_open(node: ast.Call) -> bool:
    mode = None
    if len(node.args) > 1:
        mode = node.args[1]
    for kw in node.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value


def _utf8(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "encoding":
            return isinstance(kw.value, ast.Constant) and kw.value.value == "utf-8"
    return False


def unencoded_text_io(source: str, label: str) -> list[str]:
    """Every text read/write that does not pass encoding='utf-8'."""
    bad: list[str] = []
    for node in ast.walk(ast.parse(source, filename=label)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in ENCODED_CALLS and not _utf8(node):
            bad.append(f"{label}:{node.lineno} {name}(...) without encoding='utf-8'")
        elif name == "open" and not _is_binary_open(node) and not _utf8(node):
            bad.append(f"{label}:{node.lineno} open(...) text mode without encoding='utf-8'")
    return bad


def unguarded_resource_imports(source: str, label: str) -> list[str]:
    """Every `import resource` / `from resource import ...` that is not inside a try."""
    offenders: list[str] = []

    def visit(node: ast.AST, in_try: bool) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _is_resource(node):
            if not in_try:
                offenders.append(f"{label}:{node.lineno}")
            return
        nested = in_try or isinstance(node, ast.Try)
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(ast.parse(source, filename=label), False)
    return offenders


def _is_resource(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "resource" for alias in node.names)
    return isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "resource"


# ---------------------------------------------------------------- memory probe is gone


def test_the_notebook_has_no_memory_probe_at_all():
    forbidden = re.compile(r"\bimport resource\b|\bresource\.|\bpsutil\b|peak_rss|peak_ram|getrusage")
    for i, source in enumerate(code_cells()):
        hit = forbidden.search(source)
        assert hit is None, (
            f"cell {i} contains {hit.group(0)!r} — the memory probe was removed on purpose "
            "(D184: peak memory is deliberately not reported)")


def test_common_module_the_notebook_imports_has_no_bare_resource_import():
    """`common` is imported by §2, so a bare import there would crash before any stage."""
    offenders = unguarded_resource_imports(COMMON.read_text(encoding="utf-8"), "common.py")
    assert not offenders, f"bare `import resource` outside a try: {offenders}"


# ---------------------------------------------------------------- UTF-8 everywhere


def test_every_text_read_and_write_is_utf8():
    bad = []
    for i, source in enumerate(code_cells()):
        bad += unencoded_text_io(source, f"cell {i}")
    assert not bad, "text I/O without encoding='utf-8' breaks on Windows:\n  " + "\n  ".join(bad)


def test_the_reference_csv_is_read_as_utf8_explicitly():
    reads = [line for source in code_cells() for line in source.splitlines()
             if "pd.read_csv(FIG_HOLE_CSV" in line]
    assert reads == ['    ref = pd.read_csv(FIG_HOLE_CSV, encoding="utf-8")'], reads


# ---------------------------------------------------------------- Windows paths in SQL


def test_every_sql_path_is_posix():
    """A native Windows path would put backslashes inside a SQL string literal."""
    bad = []
    for i, source in enumerate(code_cells()):
        for line in source.splitlines():
            if SQL_PATH.search(line) and ".as_posix()" not in line:
                bad.append(f"cell {i}: {line.strip()}")
    assert not bad, "SQL path interpolation without .as_posix():\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------- no silent pip install


def _ensure_packages(subprocess_module=None):
    """The notebook's own `ensure_packages`, lifted out of its cell with its globals."""
    for source in code_cells():
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_packages":
                namespace: dict = {"sys": sys,
                                   "subprocess": subprocess_module or subprocess}
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             "<ensure_packages>", "exec"), namespace)  # noqa: S102
                return namespace["ensure_packages"]
    pytest.fail("the notebook no longer defines ensure_packages()")


class FakeSubprocess:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.CalledProcessError = subprocess.CalledProcessError

    def check_call(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail:
            raise subprocess.CalledProcessError(1, args)


def _run_installer(missing, install, fake):
    return _ensure_packages(fake)(missing, install)


def test_nothing_installed_when_the_packages_are_present():
    fake = FakeSubprocess()
    _run_installer([], True, fake)
    assert fake.calls == []


def test_missing_packages_are_reported_not_installed_by_default():
    fake = FakeSubprocess()
    with pytest.raises(RuntimeError) as exc:
        _run_installer(["duckdb"], False, fake)
    assert fake.calls == [], "a package was installed with INSTALL_MISSING=False"
    assert "pip install duckdb" in str(exc.value)


def test_installing_is_opt_in_and_shell_free():
    fake = FakeSubprocess()
    _run_installer(["duckdb", "pyyaml"], True, fake)
    assert len(fake.calls) == 1
    args, kwargs = fake.calls[0]
    assert args[:3] == [sys.executable, "-m", "pip"]
    assert kwargs == {}


def test_a_failed_install_explains_itself():
    fake = FakeSubprocess(fail=True)
    with pytest.raises(RuntimeError) as exc:
        _run_installer(["duckdb"], True, fake)
    assert "pip install failed" in str(exc.value)
    assert "pip install duckdb" in str(exc.value)


def test_the_config_cell_ships_the_install_flag_off():
    config = code_cells()[0]
    assert "INSTALL_MISSING = False" in config
