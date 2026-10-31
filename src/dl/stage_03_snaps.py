#!/usr/bin/env python3
"""stage_03_snaps — Phase 3: the DL pass-rush snap table.

Run order: after ``00_thresholds``, before ``04_models``.

    cd src && python -m dl.run 03_game_snaps --mode sample
    python src/dl/stage_03_snaps.py --mode sample --force

What it writes (``TASK.md`` Phase 3, ``notes/dl-design.md`` §5.4)
----------------------------------------------------------------
``outputs/dl/game/snaps/snaps.csv`` — one row per DL pass-rush snap, in the
exact column order declared by :data:`dl.dl_snaps.SNAP_COLUMNS` (with the
mandatory leading ``provenance`` column added by the writer).

``outputs/dl/game/snaps/snaps_summary.json`` — every count that explains a row
that is **not** in the table: coverage rows, null censor times, ``ttp >
censor`` rows demoted to ``event = 0`` (kept, not dropped), unmapped
alignments, snaps without a drive key, and the per-column ``NA`` rates that
show which fields the data cannot support.  Counts, never silent drops.

Honesty rules
-------------
* A sample run is stamped ``PLACEHOLDER_SAMPLE`` and sets ``reportable: false``.
  It never overwrites an artefact that already carries ``FULL`` provenance.
* A full run refuses to start unless the config provenance is ``FULL`` **and**
  ``outputs/dl/thresholds.json`` carries ``FULL`` provenance (design §5.3,
  D70).  Thresholds tuned on the sample can never feed a reportable table.
* Nothing is zero-filled.  A field the data cannot support stays ``NA`` with a
  reason in ``not_derivable`` / ``omitted_columns``.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402  (needs the sys.path fix above)
from dl import dl_snaps as snaps  # noqa: E402
from dl import stage_00_thresholds as stage00  # noqa: E402

STAGE = "03_game_snaps"


# --------------------------------------------------------------------------- #
# Paths / provenance
# --------------------------------------------------------------------------- #
def _out_dir(cfg: Mapping, mode: str) -> Path:
    """``outputs/dl/game/snaps`` (subdir overridable from the config block)."""
    paths = D.resolve_paths(cfg, mode)
    subdir = D.dl(cfg)["game"].get("snap_out_subdir") or "game/snaps"
    return paths["outputs"] / str(subdir)


def _display(path: Path) -> str:
    """Repo-relative POSIX path when possible (stable across checkouts)."""
    try:
        return path.resolve().relative_to(D.REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _thresholds_state(cfg: Mapping, mode: str) -> dict[str, Any]:
    """Provenance + frozen flag of ``thresholds.json`` (never a value guess)."""
    path = D.resolve_paths(cfg, mode)["thresholds"]
    payload = stage00._read_existing(path)
    return {
        "path": _display(path),
        "present": payload is not None,
        "provenance": stage00._provenance_level(payload) or "missing",
        "full": stage00.is_full_provenance(payload),
        "frozen": bool(payload.get("frozen")) if payload else False,
    }


def check_provenance(cfg: Mapping, mode: str) -> dict[str, Any]:
    """Gate the stage and return the provenance block for the summary.

    Full mode raises unless the config provenance **and** the thresholds file
    are ``FULL``; a reportable table cannot be built from sample-tuned
    thresholds (design §5.3, D70).  Sample mode is allowed and marked
    non-reportable.
    """
    level = D.require_full_provenance(cfg, mode, STAGE)
    thresholds = _thresholds_state(cfg, mode)
    if mode == "sample" and level != "PLACEHOLDER_SAMPLE":
        raise RuntimeError(
            f"STAGE {STAGE!r}: refusing to run — mode=sample would be stamped "
            f"{level!r}. A sample run must carry PLACEHOLDER_SAMPLE provenance.")
    if mode == "full":
        D.require_prereg(cfg, mode, STAGE)
        if not thresholds["full"]:
            raise RuntimeError(
                f"STAGE {STAGE!r}: refusing to run — {thresholds['path']} "
                f"carries {thresholds['provenance']!r} provenance, not 'FULL'. "
                f"Run 00_thresholds in full mode first; sample thresholds can "
                f"never feed a reportable snap table.")
    reportable = bool(mode == "full" and level == "FULL" and thresholds["full"])
    return {
        "config_level": level,
        "reportable": reportable,
        "thresholds": thresholds,
    }


def _existing_full(path: Path) -> bool:
    """True when an existing summary file already carries FULL provenance."""
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    return str(payload.get("provenance", "")).split(":", 1)[0].strip() == "FULL"


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _csv_rows(frame: pd.DataFrame, fields: list[str]) -> list[dict]:
    """Dataframe rows as CSV-safe dicts: every missing value becomes ``''``."""
    rows: list[dict] = []
    for record in frame[fields].to_dict("records"):
        row: dict[str, Any] = {}
        for key, value in record.items():
            if value is None:
                row[key] = ""
            elif isinstance(value, float):
                row[key] = value if math.isfinite(value) else ""
            elif pd.isna(value):
                row[key] = ""
            else:
                row[key] = value
        rows.append(row)
    return rows


def _exclusions(counts: Mapping[str, Any],
                frame: pd.DataFrame) -> dict[str, Any]:
    """The exclusions the design names, each with its count and disposition."""
    sequence_na = (int(frame["snaps_in_drive"].isna().sum())
                   if "snaps_in_drive" in frame.columns else 0)
    return {
        "coverage_rows_excluded": {
            "count": int(counts.get("n_excluded_coverage", 0)),
            "disposition": "excluded (blitzing == FALSE: coverage, not a rush)",
        },
        "null_censor_time_excluded": {
            "count": int(counts.get("n_excluded_null_censor", 0)),
            "disposition": "excluded (no dropback_duration => not censorable)",
        },
        "ttp_after_censor_demoted": {
            "count": int(counts.get("n_ttp_gt_censor_event_set_to_0", 0)),
            "disposition": "kept, event set to 0 at censor_time (inconsistency)",
        },
        "unmapped_alignment": {
            "count": int(counts.get("n_alignment_unmapped", 0)),
            "disposition": "kept, alignment NA (never coerced to edge/interior)",
        },
        "missing_drive_key": {
            "count": int(counts.get("n_snaps_drive_na", 0)),
            "disposition": "kept, snaps_in_drive NA (possession unknown for the play)",
        },
        "snaps_in_drive_unavailable": {
            "count": sequence_na,
            "disposition": ("kept, snaps_in_drive NA (no drive key, or the "
                            "rusher's game has no usable clock to sequence)"),
        },
    }


def _markdown(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    drive = summary["drive"]
    lines = [
        "# Phase 3 — DL pass-rush snap table",
        "",
        f"Mode: `{summary['mode']}`. Reportable: "
        f"**{'yes' if summary['reportable'] else 'no'}**. Thresholds: "
        f"`{summary['thresholds']['provenance']}` "
        f"(frozen: {summary['thresholds']['frozen']}).",
        "",
        "## Rows",
        "",
        f"- DL rows read: {counts.get('n_dl_rows', 0)}",
        f"- rush rows (`blitzing == TRUE`): {counts.get('n_rush_rows', 0)}",
        f"- emitted snaps: {summary['n_snaps']}",
        f"- event rate (pressure): {summary['event_rate']:.4f}"
        if isinstance(summary["event_rate"], float)
        and math.isfinite(summary["event_rate"])
        else "- event rate (pressure): NA",
        "",
        "## Exclusions (counted, never silent)",
        "",
        "| reason | count | disposition |",
        "| --- | --- | --- |",
    ]
    for name, block in summary["exclusions"].items():
        lines.append(f"| {name} | {block['count']} | {block['disposition']} |")
    lines += [
        "",
        "## Drive key",
        "",
        f"Rule: {drive['rule']}. Clock order: {drive['clock_order']}.",
        "",
        f"- plays in the play list: {drive['n_plays_in_list']} "
        f"across {drive['n_games_in_list']} games",
        f"- drives derived: {drive['n_drives']} (per game: min "
        f"{drive['drives_per_game']['min']}, median "
        f"{drive['drives_per_game']['median']}, max "
        f"{drive['drives_per_game']['max']})",
        f"- plays with missing possession: "
        f"{drive['n_plays_missing_possession']}; conflicting: "
        f"{drive['n_plays_conflicting_possession']}",
        f"- clock/play_id order inversions: {drive['n_clock_order_inversions']}",
        "",
        "## Columns left NA (and why)",
        "",
    ]
    for column, reason in sorted(summary["not_derivable"].items()):
        lines.append(f"- `{column}`: {reason}")
    for column, reason in sorted(summary["omitted_columns"].items()):
        lines.append(f"- `{column}`: {reason}")
    if not summary["not_derivable"] and not summary["omitted_columns"]:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage
# --------------------------------------------------------------------------- #
def build_summary(cfg: Mapping, mode: str, frame: pd.DataFrame,
                  snap_summary: Mapping[str, Any],
                  prov: Mapping[str, Any]) -> dict[str, Any]:
    """The ``snaps_summary.json`` payload (without the provenance stamp)."""
    counts = dict(snap_summary["counts"])

    def finite_or_none(value: Any) -> Any:
        """Keep the artefact valid JSON: ``NaN`` becomes ``null``."""
        return None if isinstance(value, float) and not math.isfinite(value) else value

    return {
        "stage": STAGE,
        "mode": mode,
        "reportable": bool(prov["reportable"]),
        "config_provenance": prov["config_level"],
        "thresholds": dict(prov["thresholds"]),
        "n_snaps": int(len(frame)),
        "n_plays": int(counts.get("n_plays_with_dropback", 0)),
        "n_players": int(frame["nfl_id"].nunique()) if len(frame) else 0,
        "event_rate": finite_or_none(snap_summary["event_rate"]),
        "columns": list(frame.columns),
        "counts": counts,
        "exclusions": _exclusions(counts, frame),
        "na_rate": {key: finite_or_none(value)
                    for key, value in snap_summary["na_rate"].items()},
        "not_derivable": snap_summary["not_derivable"],
        "omitted_columns": snap_summary["omitted_columns"],
        "drive": snap_summary["drive"],
        "derived_constants": snap_summary["derived_constants"],
        "checks": snap_summary["checks"],
    }


def run(cfg: Mapping, mode: str, force: bool = False) -> dict[str, Any]:
    """Build and write the snap table; returns a small outcome mapping."""
    out_dir = _out_dir(cfg, mode)
    summary_path = out_dir / "snaps_summary.json"

    # A sample run must never overwrite full provenance (design §5.4).
    if mode == "sample" and _existing_full(summary_path):
        raise RuntimeError(
            f"STAGE {STAGE!r}: refusing to run — {summary_path} already carries "
            f"FULL provenance. A sample run must never overwrite a full-run "
            f"snap table; use --mode full or remove it deliberately.")

    prov = check_provenance(cfg, mode)

    if D.is_done(cfg, STAGE, mode, force=force):
        return {"skipped": True, "stage": STAGE, "path": out_dir}

    D.assert_disk_space(cfg, mode)
    frame, snap_summary = snaps.build_snaps(cfg, mode, with_summary=True)

    fields = [c for c in snaps.SNAP_COLUMNS if c in frame.columns]
    missing = [c for c in snaps.SNAP_COLUMNS if c not in frame.columns]
    if missing:
        raise RuntimeError(
            f"STAGE {STAGE!r}: the snap table is missing declared columns "
            f"{missing}. A required field is unavailable; refusing to write a "
            f"partial schema.")

    D.write_csv(out_dir / "snaps.csv", _csv_rows(frame, fields), fields,
                mode=mode, cfg=cfg)
    summary = build_summary(cfg, mode, frame, snap_summary, prov)
    D.write_json(summary_path, summary, mode=mode, cfg=cfg)
    D.write_markdown(out_dir / "SUMMARY.md", _markdown(summary),
                     mode=mode, cfg=cfg)
    D.mark_done(cfg, STAGE, mode, extra={
        "n_snaps": summary["n_snaps"],
        "event_rate": summary["event_rate"],
        "n_drives": summary["drive"]["n_drives"],
        "n_snaps_drive_na": summary["counts"].get("n_snaps_drive_na"),
        "reportable": summary["reportable"],
    })
    return {"skipped": False, "stage": STAGE, "path": out_dir,
            "summary": summary}


def main(argv=None) -> int:
    args = D.parse_args(argv)
    cfg = D.load_config(args.config)
    D.seed_everything(int(D.dl(cfg)["seeds"]["base"]))
    try:
        with D.timed_stage(cfg, STAGE, args.mode) as st:
            outcome = run(cfg, args.mode, force=args.force)
            if outcome.get("skipped"):
                st.note("skipped: checkpoint current")
                print(f"{STAGE}: up to date (use --force to rebuild)")
                return 0
            summary = outcome["summary"]
            excl = summary["exclusions"]
            rate = summary["event_rate"]
            rate_text = f"{rate:.4f}" if isinstance(rate, float) else "NA"
            st.note(f"snaps={summary['n_snaps']} "
                    f"event_rate={rate_text} "
                    f"drives={summary['drive']['n_drives']} "
                    f"reportable={summary['reportable']}")
            print(f"{STAGE}: wrote {outcome['path']}")
            print(f"  snaps: {summary['n_snaps']}  "
                  f"players: {summary['n_players']}  "
                  f"event rate: {rate_text}")
            print(f"  drives: {summary['drive']['n_drives']}  "
                  f"snaps without drive key: "
                  f"{excl['missing_drive_key']['count']}")
            for name in ("coverage_rows_excluded", "null_censor_time_excluded",
                         "ttp_after_censor_demoted", "unmapped_alignment"):
                print(f"  {name}: {excl[name]['count']}")
            print(f"  reportable: {summary['reportable']} "
                  f"(thresholds {summary['thresholds']['provenance']})")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
