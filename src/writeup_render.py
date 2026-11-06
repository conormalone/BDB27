"""writeup_render — fill the writeup's named fields from FULL artefacts.

Presentation only. This module reads ``notes/writeup-draft.md`` plus the two
result artefacts and substitutes each ``{{PENDING_FULL_RUN: <path>}}`` token with
the value at that exact path. It never estimates, rounds a result to a nicer
number, substitutes a sample value, or fills a field it cannot find.

Three rules are enforced in code, not in prose:

1. **No sample numbers, ever.** If an artefact is present and its provenance is
   not ``FULL``, the renderer refuses: status ``REFUSED``, no text, no file
   written, and the reason names the artefact and the stamp it carried. The
   whole point of the refusal is that a sample run cannot leak into a document
   that looks like a result.
2. **Missing fields are named.** A field that is absent, ``null``, non-finite or
   of a type that cannot be printed is replaced by ``[[UNRESOLVED: <path>]]``
   and listed in the result. No ``{{...}}`` token and no blank space ever stands
   in for a value.
3. **Deterministic, offline.** No clocks, no network, no environment reads; the
   same draft and artefacts always produce the same bytes.

Draft apparatus after ``<!-- END OF WRITEUP BODY -->`` (the word-count line and
the "what still has to be filled" checklist) is not part of the writeup and is
neither rendered nor counted.

Token grammar::

    {{PENDING_FULL_RUN: decomposition.pooled.share_phys}}
    {{PENDING_FULL_RUN: validation.checks.permutation.n_requested}}

The first component is the artefact (``decomposition`` or ``validation``); the
rest is a dotted path inside it. Tokens inside HTML comments are ignored, so the
draft can document the syntax without documenting a pending field.

Floats print with three decimals (one decimal from 100 upward); a two-element
list prints as ``[low, high]``. Nothing else is reformatted.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping

try:  # same import pattern as the rest of src/
    import notebook_report as R
except ImportError:  # pragma: no cover
    from . import notebook_report as R  # type: ignore

#: Artefact name -> path under the outputs directory.
ARTEFACTS: dict[str, tuple[str, ...]] = {
    "decomposition": ("phase4", "decomposition.json"),
    "validation": ("validation", "validation.json"),
}

#: Literal token, only outside comments.
TOKEN_RE = re.compile(r"\{\{\s*PENDING_FULL_RUN\s*:\s*([^{}]+?)\s*\}\}")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

UNRESOLVED_FMT = "[[UNRESOLVED: {path}]]"

#: Everything after this marker is drafting apparatus (word-count line, "what
#: still has to be filled" checklist). It is not part of the writeup: it is not
#: rendered and it is not counted against the word cap.
APPARATUS_MARKER = "<!-- END OF WRITEUP BODY -->"

OK = "OK"
REFUSED = "REFUSED"
UNRESOLVED = "UNRESOLVED"

# --------------------------------------------------------------------------- #
# values
# --------------------------------------------------------------------------- #
def format_value(value: Any) -> str | None:
    """Render a value for prose; ``None`` means "cannot be printed".

    Booleans, integers, finite floats, strings and two-element numeric lists are
    printable. Anything else (a dict, a null, a non-finite float, a bare
    object) returns ``None`` so the caller reports the path instead of
    publishing a repr.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if abs(value) >= 100:
            return f"{value:.1f}"
        text = f"{value:.3f}"
        return "0.000" if text == "-0.000" else text
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        parts = [format_value(item) for item in value]
        if any(part is None for part in parts):
            return None
        return "[" + ", ".join(parts) + "]"
    return None


def lookup(payload: Any, path: str) -> tuple[bool, Any]:
    """Walk a dotted path. ``(False, None)`` for anything absent or null."""
    cursor: Any = payload
    for key in path.split("."):
        if not isinstance(cursor, Mapping) or key not in cursor:
            return False, None
        cursor = cursor[key]
    if cursor is None:
        return False, None
    return True, cursor


# --------------------------------------------------------------------------- #
# artefacts
# --------------------------------------------------------------------------- #
def load_artefacts(outputs_dir: str | Path) -> tuple[dict[str, dict | None], dict[str, str]]:
    """Read the two artefacts. Returns payloads and per-artefact provenance kind."""
    outputs = Path(outputs_dir)
    payloads: dict[str, dict | None] = {}
    kinds: dict[str, str] = {}
    for name, parts in ARTEFACTS.items():
        path = outputs.joinpath(*parts)
        payload = R.read_json(path) if path.exists() else None
        payloads[name] = payload
        kinds[name] = R.provenance_kind(payload) if payload is not None else "ABSENT"
    return payloads, kinds


def fixture_names(payloads: Mapping[str, dict | None]) -> list[str]:
    """Artefacts that declare themselves a synthetic fixture.

    The smoke-test fixture is stamped ``FULL`` so the render paths can run, and
    carries ``fixture: true`` so a filled document can never be mistaken for a
    result without the reader being told.
    """
    return [name for name, payload in payloads.items()
            if isinstance(payload, Mapping) and payload.get("fixture") is True]


def refusal_reason(kinds: Mapping[str, str]) -> str | None:
    """Refuse when any artefact that exists is not FULL.

    An absent artefact is not a provenance failure — its fields are simply
    unresolved, and the renderer says so by name. A *present* artefact stamped
    ``PLACEHOLDER_SAMPLE`` is a refusal, because filling from it would put
    sample numbers into the deliverable.
    """
    bad = {name: kind for name, kind in kinds.items() if kind not in ("FULL", "ABSENT")}
    if not bad:
        return None
    detail = ", ".join(f"{name}={kind}" for name, kind in sorted(bad.items()))
    return (f"refusing to fill the writeup: {detail}. Only FULL-provenance "
            f"artefacts may supply a result; a sample or unknown stamp would put "
            f"an unvalidated number into the document.")


# --------------------------------------------------------------------------- #
# filling
# --------------------------------------------------------------------------- #
def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def find_tokens(text: str) -> list[str]:
    """Token paths in document order, ignoring HTML comments."""
    return [m.group(1).strip() for m in TOKEN_RE.finditer(strip_comments(text))]


def fill_tokens(text: str, payloads: Mapping[str, dict | None]
                ) -> tuple[str, list[str], list[str], int]:
    """Substitute every token outside comments.

    Returns ``(text, unresolved, malformed, n_filled)``. Tokens are matched on
    the raw document so comment bodies never reach the output path, but only
    tokens outside comments are replaced.
    """
    unresolved: list[str] = []
    malformed: list[str] = []
    filled = 0

    # Replace the draft's own documentation examples (inside comments) with a
    # placeholder that cannot match the real token pattern, then restore them.
    comments: list[str] = []

    def _park(match: re.Match) -> str:
        comments.append(match.group(0))
        return f"\x00COMMENT{len(comments) - 1}\x00"

    parked = COMMENT_RE.sub(_park, text)

    def _sub(match: re.Match) -> str:
        nonlocal filled
        path = match.group(1).strip()
        head, _, rest = path.partition(".")
        if not rest or head not in ARTEFACTS:
            malformed.append(path)
            return UNRESOLVED_FMT.format(path=path)
        payload = payloads.get(head)
        found, value = lookup(payload, rest)
        rendered = format_value(value) if found else None
        if rendered is None:
            unresolved.append(path)
            return UNRESOLVED_FMT.format(path=path)
        filled += 1
        return rendered

    rendered = TOKEN_RE.sub(_sub, parked)

    for index, body in enumerate(comments):
        rendered = rendered.replace(f"\x00COMMENT{index}\x00", body)

    # A stray token (e.g. inside a code fence that the draft wants to show)
    # must still be visible as a problem, never silent.
    for leftover in TOKEN_RE.findall(strip_comments(rendered)):
        if leftover.strip() not in malformed:
            malformed.append(leftover.strip())

    return rendered, unresolved, malformed, filled


def word_count(text: str) -> int:
    """Visible words: HTML comments stripped, whitespace split."""
    return len(strip_comments(text).split())


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def render(draft_path: str | Path, outputs_dir: str | Path) -> dict[str, Any]:
    """Fill the draft. Pure: reads files, returns a result dict, writes nothing."""
    draft = Path(draft_path)
    if not draft.exists():
        return {"status": REFUSED, "reason": f"draft absent: {draft}", "text": None,
                "unresolved": [], "malformed": [], "filled": 0, "word_count": 0,
                "provenance": {}, "draft": str(draft)}

    text = draft.read_text(encoding="utf-8")
    body, marker, _appendix = text.partition(APPARATUS_MARKER)
    if marker:
        text = body
    payloads, kinds = load_artefacts(outputs_dir)

    reason = refusal_reason(kinds)
    if reason is not None:
        return {"status": REFUSED, "reason": reason, "text": None,
                "unresolved": find_tokens(text), "malformed": [], "filled": 0,
                "word_count": 0, "provenance": kinds, "draft": str(draft),
                "fixture": fixture_names(payloads)}

    rendered, unresolved, malformed, filled = fill_tokens(text, payloads)
    status = UNRESOLVED if (unresolved or malformed) else OK
    return {"status": status, "reason": None, "text": rendered,
            "unresolved": unresolved, "malformed": malformed, "filled": filled,
            "word_count": word_count(rendered), "provenance": kinds,
            "draft": str(draft), "fixture": fixture_names(payloads)}


def report(result: Mapping[str, Any]) -> str:
    """One-paragraph human summary of a render result."""
    status = result["status"]
    prov = ", ".join(f"{k}={v}" for k, v in sorted((result.get("provenance") or {}).items()))
    lines = [f"status: {status}",
             f"provenance: {prov or 'none'}",
             f"draft: {result.get('draft')}"]
    if status == REFUSED:
        lines.append(f"reason: {result['reason']}")
        lines.append("wrote: nothing")
        return "\n".join(lines)
    if result.get("fixture"):
        lines.append("WARNING: synthetic fixture artefact(s): "
                     + ", ".join(result["fixture"]) + " — smoke test, not a result")
    lines.append(f"filled: {result['filled']} field(s)")
    lines.append(f"word count: {result['word_count']}")
    if result["unresolved"]:
        lines.append("unresolved (named, not filled): " + ", ".join(result["unresolved"]))
    if result["malformed"]:
        lines.append("malformed token (artefact not known): " + ", ".join(result["malformed"]))
    return "\n".join(lines)


def write(result: Mapping[str, Any], out_path: str | Path, *, strict: bool = False) -> Path | None:
    """Write the rendered text. Refuses on REFUSED, and on UNRESOLVED if strict."""
    if result["status"] == REFUSED or result.get("text") is None:
        return None
    if strict and result["status"] != OK:
        return None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(str(result["text"]), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--draft", default="notes/writeup-draft.md")
    parser.add_argument("--outputs", default="outputs")
    parser.add_argument("--out", default=None, help="write the rendered markdown here")
    parser.add_argument("--strict", action="store_true",
                        help="write only when every token resolved")
    args = parser.parse_args(argv)

    result = render(args.draft, args.outputs)
    print(report(result))
    if args.out:
        path = write(result, args.out, strict=args.strict)
        print(f"wrote: {path}" if path else "wrote: nothing")
    return 0 if result["status"] == OK else (2 if result["status"] == REFUSED else 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
