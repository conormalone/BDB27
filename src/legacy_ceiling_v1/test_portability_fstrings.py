#!/usr/bin/env python3
"""Version-portability guard: f-string expression hazards + declared-minimum-Python consistency.

Runnable **without pytest** (``python src/tests/test_portability_fstrings.py``) and collected by
pytest (functions named ``test_*``). Regression coverage for the crash introduced in ``eca046e``:
``src/model_features.py`` line 105 put an escaped quote (``\\"``) **inside an f-string expression** —

    f"{', '.join('\\"%s\\"' % f for f in cfg['validate']['optout_fields'])}"

That is legal only on CPython >= 3.12 (PEP 701). On <= 3.11 it is a hard ``SyntaxError``
("f-string expression part cannot include a backslash"), raised the moment ``04_models.py`` /
``05_validate.py`` import ``model_features``. Symmetrically, *same-quote reuse* inside an f-string
expression (``f"{d["k"]}"``) is also 3.12+-only.

Method (primary = ``tokenize``). On 3.12+ the tokenizer emits ``FSTRING_START`` / ``FSTRING_MIDDLE`` /
``FSTRING_END`` and tokenizes the expression parts (including nested ``STRING`` tokens). We track
brace depth inside each f-string region and inspect the tokens that fall in the *expression* part:

  (a) backslash inside an f-string expression part;
  (b) same-quote reuse inside an f-string expression (nested string uses the f-string's quote char);
  (c) the source does not parse on the declared minimum (``ast.parse(feature_version=(3,12))``).

Documented limitation: ``ast.parse(..., feature_version=(3, 11))`` does **not** catch a
backslash-in-f-string when run on a 3.13 host (the running tokenizer implements PEP 701), so the
``ast`` gate is only a coarse secondary check; ``tokenize`` is the primary detector and is what
actually flags (a)/(b).
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
NOTEBOOKS = REPO / "notebooks"

# The exact PRE-FIX (eca046e) source text for model_features.py line 105 (raw: backslashes literal).
_PREFIX_LINE_105 = r'''
cr = con.execute(
    f"SELECT nfl_id, combine_position, combine_height, combine_weight, "
    f"{', '.join('\"%s\"' % f for f in cfg['validate']['optout_fields'])} "
    f"FROM {common.pq(cr_path)}").fetchall()
'''

MIN_PYTHON = (3, 12)
_MIN_TOKEN = "Python 3.12"
_MIN_PYTHON_FILES = ["README_RUN.md", "requirements.txt", "notebooks/README.md"]


# --------------------------------------------------------------------------- #
# scanner
# --------------------------------------------------------------------------- #
def _quote_style(prefixed: str) -> str:
    """Return the quote run of a string / f-string token after its prefix letters."""
    i = 0
    while i < len(prefixed) and prefixed[i] in "rRfFbBuU":
        i += 1
    return prefixed[i:]


def _strip_magics(src: str) -> str:
    """Blank IPython magics / shell escapes, preserving line count (so scans don't false-positive)."""
    out = []
    for line in src.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped[:1] in ("%", "!") or stripped[:1] == "?":
            out.append("\n" if line.endswith("\n") else "")
        else:
            out.append(line)
    return "".join(out)


def scan_source(src: str, filename: str = "<source>") -> list[str]:
    """Return human-readable portability violations for (a)/(b)/(c); empty list == clean."""
    violations: list[str] = []

    # (c) coarse secondary: does it even parse on the declared minimum?
    try:
        ast.parse(src, filename=filename, feature_version=MIN_PYTHON)
    except SyntaxError as exc:
        violations.append(
            f"{filename}:{exc.lineno}: [c] does not parse on Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} "
            f"(feature_version): {exc.msg}"
        )

    # (a)/(b) primary: tokenize-based, tracking brace depth inside f-string regions.
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        violations.append(f"{filename}: [tokenize] source could not be tokenized: {exc}")
        return violations

    fstrings: list[dict] = []  # stack of {"quote": str, "depth": int}
    for tok in tokens:
        ttype = tok.type
        if ttype == tokenize.FSTRING_START:
            fstrings.append({"quote": _quote_style(tok.string), "depth": 0})
            continue
        if not fstrings:
            continue
        top = fstrings[-1]
        if ttype == tokenize.FSTRING_END:
            fstrings.pop()
            continue
        if ttype == tokenize.OP and tok.string in "([{":
            top["depth"] += 1
            continue
        if ttype == tokenize.OP and tok.string in ")]}":
            top["depth"] -= 1
            continue
        if top["depth"] >= 1:  # inside an f-string *expression* part
            if "\\" in tok.string:
                violations.append(
                    f"{filename}:{tok.start[0]}: [a] backslash in f-string expression: {tok.string!r}"
                )
            if ttype == tokenize.STRING:
                nested = _quote_style(tok.string)
                if nested and nested[0] == top["quote"][0]:
                    violations.append(
                        f"{filename}:{tok.start[0]}: [b] same-quote reuse in f-string expression: "
                        f"{tok.string!r} inside f-string {top['quote']!r}"
                    )
    return violations


def scan_file(path: Path) -> list[str]:
    return scan_source(path.read_text(encoding="utf-8"), filename=str(path.relative_to(REPO)))


def scan_notebook(path: Path) -> list[str]:
    """Scan every code cell; magics blanked first. Cell index is included in the filename tag."""
    nb = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for idx, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        src = _strip_magics("".join(cell.get("source", [])))
        out += scan_source(src, filename=f"{path.relative_to(REPO)}:cell{idx}")
    return out


def _iter_py_under_src() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def scan_repo() -> list[str]:
    out: list[str] = []
    for p in _iter_py_under_src():
        out += scan_file(p)
    for nb in sorted(NOTEBOOKS.glob("*.ipynb")):
        out += scan_notebook(nb)
    return out


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_flags_prefix_line105_source():
    """RED case: the pre-fix line-105 snippet MUST be flagged (a)."""
    v = scan_source(_PREFIX_LINE_105, filename="prefix")
    assert any("[a]" in x for x in v), f"guard failed to flag pre-fix backslash f-string: {v}"
    assert any(x.startswith("prefix:") for x in v), v


def test_flags_same_quote_reuse():
    """(b): a nested string reusing the f-string's own quote character MUST be flagged."""
    v = scan_source('d = f"{m["k"]}"', filename="sq")
    assert any("[b]" in x for x in v), f"guard failed to flag same-quote reuse: {v}"


def test_clean_snippets_not_flagged():
    """No false positives on legitimate f-strings (nested different-quote, format spec, conversion)."""
    for src in (
        "z = f\"hi {a+b} {x!r:>8}\"\n",
        "w = f\"{d['k']}\"\n",
        "v = f\"{', '.join(str(i) for i in xs)}\"\n",
    ):
        assert scan_source(src, "clean") == [], src


def test_repo_is_clean():
    """GREEN case: every .py under src/ and every notebook code cell must be violation-free."""
    violations = scan_repo()
    assert violations == [], "portability violations in repo:\n" + "\n".join(violations)


def test_declared_min_python_consistent():
    """README_RUN.md / requirements.txt / notebooks/README.md must all declare `Python 3.12`."""
    missing, found = [], {}
    for rel in _MIN_PYTHON_FILES:
        text = (REPO / rel).read_text(encoding="utf-8")
        if _MIN_TOKEN in text:
            found[rel] = True
        else:
            missing.append(rel)
    assert not missing, f"missing {_MIN_TOKEN!r} in: {missing}"
    # and the running interpreter must be >= the declared minimum
    assert sys.version_info[:2] >= MIN_PYTHON, (
        f"interpreter {sys.version_info[:3]} < declared {MIN_PYTHON}"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--scan":
        print("usage: python src/tests/test_portability_fstrings.py --scan <path|->")
        return 2
    target = argv[1]
    if target == "-":
        violations = scan_source(sys.stdin.read(), filename="<stdin>")
    else:
        p = Path(target)
        violations = scan_notebook(p) if p.suffix == ".ipynb" else scan_source(
            p.read_text(encoding="utf-8"), filename=str(p)
        )
    if violations:
        print(f"{len(violations)} violation(s):")
        for v in violations:
            print("  " + v)
        return 1
    print("0 violations")
    return 0


# --------------------------------------------------------------------------- #
# dependency-free runner
# --------------------------------------------------------------------------- #
def main() -> int:
    tests = [
        test_flags_prefix_line105_source,
        test_flags_same_quote_reuse,
        test_clean_snippets_not_flagged,
        test_repo_is_clean,
        test_declared_min_python_consistent,
    ]
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
    if len(sys.argv) > 1 and sys.argv[1] == "--scan":
        raise SystemExit(_cli(sys.argv[1:]))
    raise SystemExit(main())
