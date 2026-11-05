#!/usr/bin/env python3
"""stage_00_thresholds — Phase 3 (first step): freeze the load thresholds.

Run order: after ``02_combine_ceiling``, before ``03_game_snaps``.

    cd src && python -m dl.run 00_thresholds --mode sample
    python src/dl/stage_00_thresholds.py --mode sample --force

What it writes (``TASK.md`` Phase 3, ``notes/dl-design.md`` §5.3)
----------------------------------------------------------------
``outputs/dl/thresholds.json`` — the search grids, the recency weighting, the
minimum-snaps and garbage-time rules, the primary/fallback Combine feature, and
the *chosen* threshold slots.  Every value is copied from ``bdb27_dl``; this
module holds no numbers of its own.

Honesty rules
-------------
* A sample run never tunes anything: ``chosen`` stays null and ``frozen`` stays
  false.  Thresholds are never set from the sample.
* A sample run refuses to overwrite a file whose provenance is ``FULL``.
* Full mode must assert FULL provenance.  It still writes nulls the first time:
  the values are chosen on training folds in ``04_models``, which rewrites
  ``chosen``/``frozen``.  A re-run in full mode over an already-FULL file
  carries the existing ``chosen`` block forward so a tuning result cannot be
  silently erased.
* :func:`assert_thresholds_frozen` is the gate ``03``/``04``/``05`` import:
  it raises unless ``chosen`` holds both values and provenance is ``FULL``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402  (needs the sys.path fix above)

STAGE = "00_thresholds"

#: The two tuned parameters and the record of what they were tuned on.
CHOSEN_KEYS = ("absolute_speed_yds", "relative_fraction", "tuned_on")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _read_existing(path: Path) -> dict[str, Any] | None:
    """Parsed existing thresholds file, or ``None`` when absent/unreadable."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _provenance_level(payload: Mapping | None) -> str:
    """Provenance level of an artefact (text before the first colon)."""
    if not payload:
        return ""
    return str(payload.get("provenance", "")).split(":", 1)[0].strip()


def is_full_provenance(payload: Mapping | None) -> bool:
    """True when the artefact's provenance starts with ``FULL``."""
    return _provenance_level(payload).startswith("FULL")


def _display_path(path: Path) -> str:
    """Repo-relative POSIX path when possible (stable across checkouts)."""
    try:
        return path.resolve().relative_to(D.REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _prereg_frozen(path: Path) -> bool:
    """A prereg is frozen when the file exists and contains ``FROZEN``."""
    if not path.exists():
        return False
    try:
        return "FROZEN" in path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - unreadable file is simply not frozen
        return False


def _chosen_block(payload: Mapping | None, mode: str) -> dict[str, Any]:
    """``chosen`` for this run: nulls, or the existing full-run values.

    Only a full-mode re-run over an already-FULL file may inherit values: those
    were tuned by ``04_models`` and must not be erased.  Sample runs are always
    null, whatever is on disk.
    """
    empty = {key: None for key in CHOSEN_KEYS}
    if mode != "full" or not is_full_provenance(payload):
        return empty
    existing = payload.get("chosen") if isinstance(payload, dict) else None
    if not isinstance(existing, Mapping):
        return empty
    inherited = {key: existing.get(key) for key in CHOSEN_KEYS}
    return inherited if is_frozen(inherited) else empty


def is_frozen(chosen: Mapping | None) -> bool:
    """True only when *both* tuned thresholds are set (non-null)."""
    if not isinstance(chosen, Mapping):
        return False
    return all(chosen.get(key) is not None
               for key in ("absolute_speed_yds", "relative_fraction"))


def build_thresholds(cfg: Mapping, mode: str,
                     existing: Mapping | None = None) -> dict[str, Any]:
    """Assemble the thresholds payload (without the provenance stamp)."""
    block = D.dl(cfg)
    load = block["load"]
    game = block["game"]
    ceiling = block["ceiling"]
    paths = D.resolve_paths(cfg, mode)
    chosen = _chosen_block(existing, mode)
    return {
        "mode": mode,
        "frozen": is_frozen(chosen),
        "absolute_speed_grid_yds": list(load["absolute_speed_grid_yds"]),
        "relative_fraction_grid": list(load["relative_fraction_grid"]),
        "recency_half_life_s": load["recency_half_life_s"],
        "min_snaps_per_player": game["min_snaps_per_player"],
        "garbage_time": dict(game["garbage_time"]),
        "primary_feature": ceiling["primary_feature"],
        "fallback_feature": ceiling["fallback_feature"],
        "prereg_path": _display_path(paths["prereg"]),
        "prereg_frozen": _prereg_frozen(paths["prereg"]),
        "chosen": chosen,
    }


# --------------------------------------------------------------------------- #
# Stage
# --------------------------------------------------------------------------- #
def run(cfg: Mapping, mode: str, force: bool = False) -> dict[str, Any]:
    """Write ``thresholds.json``; returns a small outcome mapping."""
    paths = D.resolve_paths(cfg, mode)
    out = paths["thresholds"]
    existing = _read_existing(out)

    # A sample run must never overwrite full provenance (design §5.3).
    if mode == "sample" and is_full_provenance(existing):
        raise RuntimeError(
            f"STAGE {STAGE!r}: refusing to run — {out} already carries FULL "
            f"provenance. A sample run must never overwrite a full-run "
            f"threshold file; rebuild it with --mode full or remove it "
            f"deliberately.")

    # Full mode must assert FULL provenance; sample mode is stamped and allowed.
    D.require_full_provenance(cfg, mode, STAGE)

    if D.is_done(cfg, STAGE, mode, force=force):
        return {"skipped": True, "stage": STAGE, "path": out}

    D.assert_disk_space(cfg, mode)
    payload = build_thresholds(cfg, mode, existing=existing)
    D.write_json(out, payload, mode=mode, cfg=cfg)
    D.mark_done(cfg, STAGE, mode, extra={
        "frozen": payload["frozen"],
        "chosen": payload["chosen"],
    })
    return {"skipped": False, "stage": STAGE, "path": out,
            "payload": payload}


def assert_thresholds_frozen(cfg_path: str | None = None) -> dict[str, Any]:
    """Return the thresholds payload, or raise unless it is frozen and FULL.

    Importable gate for stages 03/04/05: thresholds tuned on the sample, or
    written under sample provenance, can never feed a full-data model.
    """
    cfg = D.load_config(cfg_path)
    mode = D.dl(cfg)["mode"]["default"]
    path = D.resolve_paths(cfg, mode)["thresholds"]
    payload = _read_existing(path)
    if payload is None:
        raise RuntimeError(
            f"THRESHOLDS NOT FROZEN: {path} is missing or unreadable. Run "
            f"stage {STAGE!r} in full mode first.")
    level = _provenance_level(payload)
    chosen = payload.get("chosen")
    problems = []
    if not level.startswith("FULL"):
        problems.append(f"provenance is {level or 'absent'!r}, not 'FULL'")
    if not is_frozen(chosen):
        missing = [key for key in ("absolute_speed_yds", "relative_fraction")
                   if not isinstance(chosen, Mapping) or chosen.get(key) is None]
        problems.append(f"chosen thresholds are unset: {', '.join(missing)}")
    if problems:
        raise RuntimeError(
            f"THRESHOLDS NOT FROZEN: {path} — " + "; ".join(problems) +
            ". Thresholds must be tuned on training folds in full mode "
            "(stage 04_models) before 03/04/05 may use them.")
    return payload


def main(argv=None) -> int:
    args = D.parse_args(argv)
    cfg = D.load_config(args.config)
    try:
        with D.timed_stage(cfg, STAGE, args.mode) as st:
            outcome = run(cfg, args.mode, force=args.force)
            if outcome.get("skipped"):
                st.note("skipped: checkpoint current")
                print(f"{STAGE}: up to date (use --force to rebuild)")
                return 0
            payload = outcome["payload"]
            st.note(f"frozen={payload['frozen']} prereg_frozen="
                    f"{payload['prereg_frozen']}")
            print(f"{STAGE}: wrote {outcome['path']}")
            print(f"  frozen: {payload['frozen']}  "
                  f"prereg_frozen: {payload['prereg_frozen']}")
            print(f"  absolute grid: {payload['absolute_speed_grid_yds']}")
            print(f"  relative grid: {payload['relative_fraction_grid']}")
    except (RuntimeError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
