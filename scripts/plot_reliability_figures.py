"""Render the band-reliability figures (D207).

Two figures this round, both from committed FULL artefacts:

  fig_third_of_one_percent   outputs/band_reliability/reliability.json   (spec §5.1)
  fig_three_routes           outputs/fallback_validation/reliability.json (spec §5.2)

Each render writes ``outputs/figures/<key>.png``, ``.csv``,
``.provenance.json`` and ``.drawn_text.json``, and merges exactly one entry into
``outputs/figures/figures_manifest.json`` (``in_report: false``: TASK.md's
four-figure clause is not amended by this cycle).

Usage:
    .venv/bin/python scripts/plot_reliability_figures.py --figure all
    .venv/bin/python scripts/plot_reliability_figures.py --figure fig_three_routes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bdb27 import common  # noqa: E402
from bdb27 import reliability_figures as rf  # noqa: E402

BAND_REL = ROOT / "outputs" / "band_reliability" / "reliability.json"
FALLBACK_REL = ROOT / "outputs" / "fallback_validation" / "reliability.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--figure", default="all",
                        choices=[*rf.DRAWERS, "all"],
                        help="one figure key, or 'all'")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out-dir", default=None,
                        help="override outputs/figures (tests use a temp dir)")
    return parser.parse_args(argv)


def build_third() -> dict:
    """§5.1 — the variance decomposition, from the D206 artefact only."""
    doc = rf.load_band_reliability(BAND_REL)
    built = rf.build_third_of_one_percent(doc)
    built["source_artefacts"] = [rf.source_record(
        BAND_REL,
        "D206 band reliability: four ICC(1) scenarios, two adjusted ICCs, "
        "derived reliability/attenuation/sample-size block",
        ROOT)]
    return built


BUILDERS = {
    rf.FIG_THIRD: build_third,
}


def render(key: str, cfg, out_dir: Path) -> dict:
    built = BUILDERS[key]()
    result = rf.render_figure(key, built, out_dir, cfg, root=ROOT)
    rf.merge_manifest(result["entry"], out_dir / "figures_manifest.json", cfg)
    return result


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = common.load_config(args.config)
    out_dir = Path(args.out_dir) if args.out_dir else cfg.repo_path("figures")

    keys = list(rf.DRAWERS) if args.figure == "all" else [args.figure]
    for key in keys:
        result = render(key, cfg, out_dir)
        entry = result["entry"]
        print(f"{key}: png={entry['png']} csv={entry['csv']} "
              f"marks={entry['drawn_marks']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.StageRefused as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
