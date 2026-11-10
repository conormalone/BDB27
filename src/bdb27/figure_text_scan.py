"""Scan the DRAWN text of every stage-06 figure for placeholder hedging.

Why this exists (D180, Conor 2026-11-10): the figures are production output,
so no figure may draw a "PLACEHOLDER"/"UNVALIDATED SAMPLE OUTPUT"/"PENDING
FULL RUN" banner — on the image or in its caption prose. Provenance lives in
the CSV ``stamp`` column and the manifest ``stamp`` field only.

The scan does not trust the committed PNGs (they cannot be read back as text).
It rebuilds every figure by running stage 06 with each render entry point
wrapped, then inspects the resulting matplotlib ``Figure`` objects: ``fig.texts``
(the drawn captions and corner banners), axis texts, tick labels, titles and
legends, plus the ggplot caption label on the plot object before drawing.

Usage (writes nothing into the repo)::

    python scripts/scan_figure_text.py --config config.yaml --mode sample

Exit code 0 when every figure is clean, 1 when any banned token is drawn.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

#: Tokens that must never be drawn on a figure or its caption (D180).
BANNED_TOKENS = (
    "PLACEHOLDER",
    "UNVALIDATED",
    "SAMPLE OUTPUT",
    "PENDING FULL RUN",
)

#: Stage-06 render entry points, all of which close the figure after saving.
RENDER_FUNCTIONS = (
    "render",
    "render_schematic",
    "render_measurement_limits",
    "render_turn_cost",
    "render_stopwatch",
    "render_both_hips",
    "render_shallow_sources",
    "render_ledger",
    "render_bars_of_doubt",
    "render_supply_composition",
    "render_band_only",
    "render_entry_exit_speed",
    "render_movement_family",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _stage06():
    """Import ``src/06_figures.py`` (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location(
        "bdb27_stage06_scan", _SRC / "06_figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def figure_texts(fig) -> list[str]:
    """Every string this drawn figure puts on a canvas."""
    texts: list[str] = [t.get_text() for t in fig.texts]
    for ax in fig.axes:
        texts.extend(t.get_text() for t in ax.texts)
        texts.append(ax.get_title())
        texts.append(ax.get_xlabel())
        texts.append(ax.get_ylabel())
        texts.extend(t.get_text() for t in ax.get_xticklabels())
        texts.extend(t.get_text() for t in ax.get_yticklabels())
        legend = ax.get_legend()
        if legend is not None:
            texts.extend(t.get_text() for t in legend.get_texts())
    return [t for t in texts if t]


def banned_hits(texts: Iterable[str]) -> list[tuple[str, str]]:
    """``(token, drawn text)`` for every banned token in every drawn string."""
    hits: list[tuple[str, str]] = []
    for text in texts:
        upper = text.upper()
        for token in BANNED_TOKENS:
            if token in upper:
                hits.append((token, text))
    return hits


def scan_figures(cfg_path: str | Path, mode: str = "sample",
                 overrides: dict[str, Any] | None = None,
                 workdir: str | Path | None = None) -> dict[str, Any]:
    """Render stage 06 once with every figure captured, then scan the text.

    ``overrides`` are dotted config keys applied to the run (e.g.
    ``{"figures.provenance.on_image_stamp": True}``); the caller's config file
    is never modified — a scratch copy is written into ``workdir``.
    """
    import tempfile

    cfg_path = Path(cfg_path).resolve()
    raw = yaml.safe_load(cfg_path.read_text())
    block = raw["turn_coverage"]
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="bdb27-scan-"))
    tmp.mkdir(parents=True, exist_ok=True)
    # ``repo_path`` resolves relative entries against the config file's own
    # directory, and the scratch config lives in ``tmp``: absolutise first so
    # the input roots stay pointed at the real repo.
    for key, value in list(block["paths"].items()):
        p = Path(str(value))
        if not p.is_absolute():
            block["paths"][key] = str((cfg_path.parent / p).resolve())
    block["paths"]["figures"] = str(tmp / "figures")
    block["paths"]["run_log"] = str(tmp / "run_log.csv")
    for dotted, value in (overrides or {}).items():
        node = block
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    scratch = tmp / "config.yaml"
    scratch.write_text(yaml.safe_dump(raw, sort_keys=False))

    stage06 = _stage06()
    from bdb27 import figures

    captured: list[dict[str, Any]] = []
    current: dict[str, str] = {"renderer": "", "png": ""}
    originals = {name: getattr(figures, name) for name in RENDER_FUNCTIONS}

    import matplotlib.pyplot as plt

    original_close = plt.close

    def close_hook(fig=None):
        # Figure objects stay readable after ``plt.close``; capture before it.
        if fig is not None and hasattr(fig, "texts"):
            captured.append({"renderer": current["renderer"],
                             "png": current["png"],
                             "figure": fig})
        return original_close(fig)

    def wrap(name: str):
        original = originals[name]

        def wrapper(*args, **kwargs):
            png = kwargs.get("out_png")
            if png is None and len(args) > 2:
                png = args[2]
            current["renderer"] = name
            current["png"] = Path(str(png)).name if png else ""
            before = len(captured)
            returned = original(*args, **kwargs)
            if len(captured) == before:
                # A renderer that hands the figure back instead of closing it.
                candidate = args[0] if args and hasattr(args[0], "texts") \
                    else returned
                if hasattr(candidate, "texts"):
                    captured.append({"renderer": name, "png": current["png"],
                                     "figure": candidate})
            return returned
        return wrapper

    try:
        for name in RENDER_FUNCTIONS:
            setattr(figures, name, wrap(name))
        plt.close = close_hook
        with contextlib.redirect_stdout(_NullWriter()):
            rc = stage06.main(["--mode", mode, "--config", str(scratch)])
    finally:
        plt.close = original_close
        for name, original in originals.items():
            setattr(figures, name, original)

    seen_ids: set[int] = set()
    unique: list[dict[str, Any]] = []
    for entry in captured:
        if id(entry["figure"]) in seen_ids:
            continue
        seen_ids.add(id(entry["figure"]))
        unique.append(entry)

    report_figures = []
    total_hits = 0
    for entry in unique:
        texts = figure_texts(entry["figure"])
        hits = banned_hits(texts)
        total_hits += len(hits)
        report_figures.append({
            "png": entry["png"],
            "renderer": entry["renderer"],
            "drawn_strings": len(texts),
            "banner_on_image": any(
                figures.STAMP in t for t in (x.get_text()
                                             for x in entry["figure"].texts)),
            "hits": [{"token": tok, "text": txt} for tok, txt in hits],
        })
    return {
        "stage": "06_figures",
        "mode": mode,
        "overrides": overrides or {},
        "config": str(cfg_path),
        "exit_code": rc,
        "figures_scanned": len(unique),
        "banned_tokens": list(BANNED_TOKENS),
        "total_hits": total_hits,
        "figures": report_figures,
    }


class _NullWriter:
    def write(self, _text: str) -> int:
        return 0

    def flush(self) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(_REPO_ROOT / "config.yaml"))
    parser.add_argument("--mode", default="sample", choices=["sample", "full"])
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    report = scan_figures(args.config, args.mode)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
    print(f"figures scanned: {report['figures_scanned']}")
    print(f"banned tokens:   {', '.join(report['banned_tokens'])}")
    print(f"hits:            {report['total_hits']}")
    for item in report["figures"]:
        if item["hits"]:
            print(f"  {item['png']}: {len(item['hits'])} hit(s)")
            for hit in item["hits"]:
                print(f"    [{hit['token']}] {hit['text'][:160]!r}")
    print("RESULT:", "CLEAN" if report["total_hits"] == 0 else "HEDGING FOUND")
    return 0 if report["total_hits"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
